"""Generate scan coordinates: district centroids + urban infill + highway sampling.

Three layers:
  1. ~3,200 district/county centroids from Amap administrative API
     → baseline coverage for all urban areas
  2. ~300 urban midpoints between adjacent districts in dense cities
     → prevents API per-query truncation (~65 station limit) in metros
  3. ~26 major national expressways sampled every 80km
     → catches highway service-area stations between counties

Results are cached to data/scan_points.json so Amap APIs are only called
when regenerating (run this file directly or call generate_scan_points()).
"""

import json
import math
import os
import time
import logging
import requests

from config import AMAP_API_KEY, DATA_DIR

log = logging.getLogger(__name__)

CACHE_PATH = os.path.join(DATA_DIR, "scan_points.json")

AMAP_DISTRICT_URL = "https://restapi.amap.com/v3/config/district"
AMAP_DIRECTION_URL = "https://restapi.amap.com/v3/direction/driving"

# ── Major national expressways (origin, destination in "lng,lat" for Amap) ──

HIGHWAYS = [
    # 7 capital radials
    ("G1 京哈",   "116.40,39.90", "126.63,45.75"),
    ("G2 京沪",   "116.40,39.90", "121.47,31.23"),
    ("G3 京台",   "116.40,39.90", "119.30,26.08"),
    ("G4 京港澳", "116.40,39.90", "113.26,23.13"),
    ("G5 京昆",   "116.40,39.90", "102.71,25.04"),
    ("G6 京藏",   "116.40,39.90", "91.10,29.65"),
    ("G7 京新",   "116.40,39.90", "87.60,43.80"),
    # N-S verticals
    ("G11 鹤大", "130.97,46.80", "121.60,38.91"),
    ("G15 沈海", "123.43,41.80", "110.35,20.02"),
    ("G25 长深", "125.32,43.88", "114.06,22.55"),
    ("G35 济广", "117.00,36.65", "113.26,23.13"),
    ("G45 大广", "125.02,46.63", "113.26,23.13"),
    ("G55 二广", "112.55,37.87", "108.37,22.82"),
    ("G65 包茂", "109.84,40.66", "110.91,21.66"),
    ("G75 兰海", "103.83,36.06", "110.35,20.02"),
    ("G85 渝昆", "106.55,29.56", "102.71,25.04"),
    # E-W horizontals
    ("G20 青银", "120.38,36.07", "106.27,38.47"),
    ("G30 连霍", "119.22,34.60", "87.60,43.80"),
    ("G40 沪陕", "121.47,31.23", "108.94,34.26"),
    ("G50 沪渝", "121.47,31.23", "106.55,29.56"),
    ("G56 杭瑞", "120.15,30.27", "100.23,25.59"),
    ("G60 沪昆", "121.47,31.23", "102.71,25.04"),
    ("G70 福银", "119.30,26.08", "106.27,38.47"),
    ("G72 泉南", "118.68,24.87", "108.37,22.82"),
    ("G76 厦蓉", "118.09,24.48", "106.71,26.65"),
    ("G80 广昆", "113.26,23.13", "102.71,25.04"),
]

HIGHWAY_SAMPLE_INTERVAL_KM = 80
DEDUP_RADIUS_KM = 30
# Cities with >= this many districts get midpoint infill to avoid API truncation
DENSE_CITY_MIN_DISTRICTS = 8
MIDPOINT_MAX_DISTANCE_KM = 25
MIDPOINT_MIN_DISTANCE_KM = 10   # skip pairs too close (centroids already overlap)
MIDPOINT_MAX_PER_CITY = 10      # cap per city to avoid combinatorial explosion


# ── Helpers ─────────────────────────────────────────────────────────────

def _haversine(lat1, lng1, lat2, lng2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlng / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


# ── Layer 1: District centroids ─────────────────────────────────────────

def _fetch_district_centroids():
    """Fetch all district/county centroids + city centroids from Amap API.

    Returns (districts, city_groups) where city_groups maps city names to
    lists of their district centroids (used for midpoint infill).
    """
    log.info("Fetching district centroids from Amap...")
    params = {
        "key": AMAP_API_KEY,
        "keywords": "中国",
        "subdistrict": 3,
        "extensions": "base",
    }
    resp = requests.get(AMAP_DISTRICT_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if data.get("status") != "1":
        raise RuntimeError(f"Amap district API error: {data.get('info')}")

    centroids = []
    city_groups = {}  # city_name -> [(lat, lng), ...]

    for prov in data["districts"][0]["districts"]:
        prov_name = prov.get("name", "")
        for city in prov.get("districts", []):
            city_name = city.get("name", "")
            city_dists = []
            for dist in city.get("districts", []):
                center = dist.get("center", "")
                if not center:
                    continue
                lng, lat = map(float, center.split(","))
                entry = {
                    "lat": round(lat, 6),
                    "lng": round(lng, 6),
                    "name": dist.get("name", ""),
                    "province": prov_name,
                }
                centroids.append(entry)
                city_dists.append((entry["lat"], entry["lng"]))

            if city_dists:
                city_groups[f"{prov_name}/{city_name}"] = city_dists

    log.info(f"  Got {len(centroids)} district centroids in "
             f"{len(city_groups)} cities")
    return centroids, city_groups


# ── Layer 2: Urban midpoint infill ──────────────────────────────────────

def _generate_urban_midpoints(city_groups):
    """For dense cities, add midpoints between adjacent district centroids.

    The BYD API returns at most ~65 stations per query.  In metros like
    Guangzhou / Shanghai / Beijing, a single district centroid can have 70+
    stations within range, causing truncation.  Adding midpoints between
    nearby districts shrinks the effective query radius so every station
    appears as a 'nearest' result for at least one query point.
    """
    midpoints = []

    for city_key, dists in city_groups.items():
        if len(dists) < DENSE_CITY_MIN_DISTRICTS:
            continue

        candidates = []
        for i in range(len(dists)):
            for j in range(i + 1, len(dists)):
                d = _haversine(*dists[i], *dists[j])
                if MIDPOINT_MIN_DISTANCE_KM <= d <= MIDPOINT_MAX_DISTANCE_KM:
                    mlat = round((dists[i][0] + dists[j][0]) / 2, 6)
                    mlng = round((dists[i][1] + dists[j][1]) / 2, 6)
                    candidates.append({
                        "lat": mlat,
                        "lng": mlng,
                        "city": city_key,
                        "_dist": d,
                    })

        # Keep the most spread-out midpoints (largest inter-district distance first)
        candidates.sort(key=lambda x: -x["_dist"])
        added = candidates[:MIDPOINT_MAX_PER_CITY]
        for m in added:
            del m["_dist"]
        midpoints.extend(added)

        if added:
            log.info(f"  {city_key}: {len(dists)} districts → "
                     f"+{len(added)} midpoints")

    log.info(f"  Total urban midpoints: {len(midpoints)}")
    return midpoints


# ── Layer 2: Highway corridor sampling ──────────────────────────────────

def _fetch_highway_route(origin, destination):
    """Get driving route polyline from Amap direction API."""
    params = {
        "key": AMAP_API_KEY,
        "origin": origin,
        "destination": destination,
        "strategy": 2,  # prefer expressway
        "extensions": "base",
    }
    resp = requests.get(AMAP_DIRECTION_URL, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    if data.get("status") != "1":
        return None

    path = data["route"]["paths"][0]
    points = []
    for step in path["steps"]:
        for coord in step["polyline"].split(";"):
            lng, lat = map(float, coord.split(","))
            points.append((lat, lng))
    return points


def _sample_along_route(points, interval_km):
    """Sample points at fixed intervals along a polyline."""
    if not points:
        return []
    sampled = [points[0]]
    accumulated = 0
    for i in range(1, len(points)):
        d = _haversine(*points[i - 1], *points[i])
        accumulated += d
        if accumulated >= interval_km:
            sampled.append(points[i])
            accumulated = 0
    # always include endpoint
    if sampled[-1] != points[-1]:
        sampled.append(points[-1])
    return sampled


def _fetch_highway_samples():
    """Fetch all highway routes and sample points along them."""
    log.info(f"Fetching {len(HIGHWAYS)} highway routes from Amap...")
    all_samples = []

    for name, origin, dest in HIGHWAYS:
        for attempt in range(3):
            try:
                points = _fetch_highway_route(origin, dest)
                break
            except requests.exceptions.RequestException as e:
                if attempt < 2:
                    log.warning(f"  {name}: retry {attempt+1} ({e})")
                    time.sleep(2 ** attempt)
                else:
                    log.warning(f"  {name}: all retries failed, skipping")
                    points = None
        if not points:
            log.warning(f"  {name}: route fetch failed, skipping")
            continue
        samples = _sample_along_route(points, HIGHWAY_SAMPLE_INTERVAL_KM)
        dist_km = sum(
            _haversine(*points[i], *points[i + 1])
            for i in range(len(points) - 1)
        )
        for lat, lng in samples:
            all_samples.append({
                "lat": round(lat, 6),
                "lng": round(lng, 6),
                "highway": name,
            })
        log.info(f"  {name}: {dist_km:.0f}km → {len(samples)} sample points")
        time.sleep(0.2)

    log.info(f"  Total highway samples (before dedup): {len(all_samples)}")
    return all_samples


# ── Combine & deduplicate ───────────────────────────────────────────────

def generate_scan_points():
    """Generate and cache all scan coordinates."""
    os.makedirs(DATA_DIR, exist_ok=True)

    districts, city_groups = _fetch_district_centroids()
    midpoints = _generate_urban_midpoints(city_groups)
    highway_raw = _fetch_highway_samples()

    # Deduplicate highway points against district centroids + midpoints
    all_existing = (
        [(d["lat"], d["lng"]) for d in districts]
        + [(m["lat"], m["lng"]) for m in midpoints]
    )
    highway_new = []
    for h in highway_raw:
        min_dist = min(
            _haversine(h["lat"], h["lng"], elat, elng)
            for elat, elng in all_existing
        )
        if min_dist > DEDUP_RADIUS_KM:
            highway_new.append(h)

    log.info(f"Highway points after dedup: {len(highway_new)} "
             f"(removed {len(highway_raw) - len(highway_new)} within "
             f"{DEDUP_RADIUS_KM}km of a district/midpoint)")

    total = len(districts) + len(midpoints) + len(highway_new)
    result = {
        "districts": districts,
        "midpoints": midpoints,
        "highways": highway_new,
        "total": total,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    log.info(f"Saved {total} scan points to {CACHE_PATH}")
    return result


def load_scan_points():
    """Load cached scan points. Returns list of (lat, lng) tuples."""
    if not os.path.exists(CACHE_PATH):
        log.info("No cached scan points, generating...")
        generate_scan_points()

    with open(CACHE_PATH, encoding="utf-8") as f:
        data = json.load(f)

    coords = []
    for d in data["districts"]:
        coords.append((d["lat"], d["lng"]))
    for m in data.get("midpoints", []):
        coords.append((m["lat"], m["lng"]))
    for h in data["highways"]:
        coords.append((h["lat"], h["lng"]))

    n_dist = len(data["districts"])
    n_mid = len(data.get("midpoints", []))
    n_hwy = len(data["highways"])
    log.info(f"Loaded {len(coords)} scan points "
             f"({n_dist} districts + {n_mid} midpoints + {n_hwy} highway) "
             f"from {data.get('generated_at', 'unknown')}")
    return coords


# ── CLI ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    result = generate_scan_points()
    n_d = len(result["districts"])
    n_m = len(result.get("midpoints", []))
    n_h = len(result["highways"])
    print(f"\nDone: {result['total']} scan points "
          f"({n_d} districts + {n_m} midpoints + {n_h} highway)")
