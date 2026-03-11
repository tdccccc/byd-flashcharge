"""Diagnose API response limits and coverage gaps."""

import json
import time
import requests
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import API_URL, REQUEST_HEADERS, REQUEST_TEMPLATE, CONCURRENT_WORKERS
from cities import MAJOR_CITIES

# ── 1. Core fetch (returns raw row count + station list) ──────────────

def fetch_raw(lat, lng, extra_params=None):
    """Fetch stations, return (count, rows, label)."""
    req_data = REQUEST_TEMPLATE.copy()
    req_data["lat"] = lat
    req_data["lng"] = lng
    req_data["reqTimestamp"] = int(time.time() * 1000)
    if extra_params:
        req_data.update(extra_params)

    payload = {"request": json.dumps(req_data)}
    try:
        resp = requests.post(API_URL, json=payload, headers=REQUEST_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        inner = json.loads(data.get("response", "{}"))
        if inner.get("code") != "0":
            return (0, [], inner.get("message", "unknown error"))
        respond_data = json.loads(inner.get("respondData", "{}"))
        rows = respond_data.get("rows", [])
        return (len(rows), rows, None)
    except Exception as e:
        return (0, [], str(e))


# ── 2. Scan all city centers and record per-query counts ──────────────

def scan_city_counts():
    print("=" * 60)
    print("Phase 1: Querying 116 city centers for per-query row counts")
    print("=" * 60)

    results = []  # (city_name, lat, lng, count)

    with ThreadPoolExecutor(max_workers=CONCURRENT_WORKERS) as pool:
        futures = {
            pool.submit(fetch_raw, lat, lng): (lat, lng, name)
            for lat, lng, name in MAJOR_CITIES
        }
        for fut in as_completed(futures):
            lat, lng, name = futures[fut]
            count, rows, err = fut.result()
            results.append((name, lat, lng, count))
            if err:
                print(f"  ERROR {name}: {err}")

    results.sort(key=lambda x: -x[3])

    # Distribution
    counts = [r[3] for r in results]
    print(f"\n{'City':12s} {'Count':>6s}")
    print("-" * 20)
    for name, lat, lng, count in results[:20]:
        bar = "#" * (count // 5)
        print(f"{name:12s} {count:>6d}  {bar}")
    print(f"  ... ({len(results)} total cities)")

    print(f"\n--- Distribution ---")
    print(f"  Max:    {max(counts)}")
    print(f"  Min:    {min(counts)}")
    print(f"  Mean:   {sum(counts)/len(counts):.1f}")
    print(f"  Median: {sorted(counts)[len(counts)//2]}")

    # Histogram of counts
    buckets = Counter()
    for c in counts:
        bucket = (c // 10) * 10
        buckets[bucket] += 1
    print(f"\n--- Histogram (bucket size = 10) ---")
    for bucket in sorted(buckets):
        bar = "#" * buckets[bucket]
        print(f"  {bucket:3d}-{bucket+9:3d}: {buckets[bucket]:3d}  {bar}")

    # Check for ceiling
    max_count = max(counts)
    at_max = [r for r in results if r[3] == max_count]
    near_max = [r for r in results if r[3] >= max_count - 2]
    if len(near_max) >= 3:
        print(f"\n⚠️  WARNING: {len(near_max)} cities returned {max_count-2}~{max_count} rows")
        print(f"  This strongly suggests a server-side TRUNCATION at ~{max_count}!")
        for name, lat, lng, count in near_max:
            print(f"    {name}: {count}")
    elif len(at_max) >= 2:
        print(f"\n⚠️  {len(at_max)} cities returned exactly {max_count} rows — possible ceiling")
    else:
        print(f"\n✓  No obvious truncation ceiling detected (max={max_count})")

    return results


# ── 3. Probe pagination parameters ───────────────────────────────────

def probe_pagination():
    print("\n" + "=" * 60)
    print("Phase 2: Probing pagination parameters")
    print("=" * 60)

    # Use Beijing as test point (likely highest density)
    lat, lng = 39.90, 116.40

    # Test various pagination params
    tests = [
        ("baseline (no extra params)", {}),
        ("pageNum=1, pageSize=200", {"pageNum": 1, "pageSize": 200}),
        ("pageNum=2, pageSize=50", {"pageNum": 2, "pageSize": 50}),
        ("pageNo=1, pageSize=200", {"pageNo": 1, "pageSize": 200}),
        ("page=1, size=200", {"page": 1, "size": 200}),
        ("page=1, limit=200", {"page": 1, "limit": 200}),
        ("currentPage=1, pageSize=200", {"currentPage": 1, "pageSize": 200}),
        ("pageIndex=0, pageSize=200", {"pageIndex": 0, "pageSize": 200}),
        ("startIndex=0, count=200", {"startIndex": 0, "count": 200}),
    ]

    baseline_count = None
    for label, params in tests:
        count, rows, err = fetch_raw(lat, lng, params)
        marker = ""
        if baseline_count is None:
            baseline_count = count
        elif count != baseline_count:
            marker = " ← DIFFERENT!"
        elif err:
            marker = f" ← ERROR: {err}"
        print(f"  {label:42s} → {count:4d} rows{marker}")
        time.sleep(0.3)

    # Also try pageNum=2 with baseline pageSize
    print("\n  Testing page 2 to see if there are more results...")
    count2, rows2, err2 = fetch_raw(lat, lng, {"pageNum": 2, "pageSize": baseline_count})
    if count2 > 0 and not err2:
        print(f"  pageNum=2 returned {count2} additional rows! Pagination WORKS!")
    else:
        print(f"  pageNum=2 returned {count2} rows (pagination likely not supported)")


# ── 4. Test search radius by measuring max distance in results ────────

def probe_radius():
    print("\n" + "=" * 60)
    print("Phase 3: Estimating API search radius")
    print("=" * 60)

    import math

    def haversine(lat1, lng1, lat2, lng2):
        R = 6371
        dlat = math.radians(lat2 - lat1)
        dlng = math.radians(lng2 - lng1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
        return R * 2 * math.asin(math.sqrt(a))

    # Sample a few cities
    test_cities = [
        (39.90, 116.40, "北京"),
        (31.23, 121.47, "上海"),
        (23.13, 113.26, "广州"),
        (30.57, 104.07, "成都"),
        (45.75, 126.65, "哈尔滨"),  # low density
        (36.06, 103.83, "兰州"),    # low density
    ]

    for lat, lng, name in test_cities:
        count, rows, err = fetch_raw(lat, lng)
        if not rows:
            print(f"  {name}: no data")
            continue
        distances = []
        for s in rows:
            d = haversine(lat, lng, s["stationLat"], s["stationLng"])
            distances.append(d)
        distances.sort()
        print(f"  {name}: {count:3d} stations | "
              f"nearest={distances[0]:.1f}km, "
              f"median={distances[len(distances)//2]:.1f}km, "
              f"max={distances[-1]:.1f}km")
        time.sleep(0.3)


# ── 5. Overlap analysis: how many unique stations across offset scan ──

def probe_overlap():
    print("\n" + "=" * 60)
    print("Phase 4: Beijing deep scan — testing if offsets find new stations")
    print("=" * 60)

    lat, lng = 39.90, 116.40
    _8DIR = [(1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)]

    # Center
    _, center_rows, _ = fetch_raw(lat, lng)
    center_ids = {s["id"] for s in center_rows}
    print(f"  Center (0,0): {len(center_ids)} stations")

    all_ids = set(center_ids)
    for r in [0.15, 0.3, 0.5, 0.7]:
        ring_new = 0
        ring_total = 0
        coords = [(lat + d[0]*r, lng + d[1]*r) for d in _8DIR]
        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = {pool.submit(fetch_raw, c[0], c[1]): c for c in coords}
            for fut in as_completed(futs):
                count, rows, _ = fut.result()
                ring_total += count
                for s in rows:
                    if s["id"] not in all_ids:
                        ring_new += 1
                        all_ids.add(s["id"])
        print(f"  Radius {r}° (8 dirs): {ring_total:4d} returned, +{ring_new:3d} new | cumulative: {len(all_ids)}")

    print(f"\n  Total unique stations in Beijing area: {len(all_ids)}")
    print(f"  Center alone captured: {len(center_ids)}/{len(all_ids)} "
          f"({100*len(center_ids)/len(all_ids):.0f}%)")


# ── Main ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    city_results = scan_city_counts()
    probe_pagination()
    probe_radius()
    probe_overlap()

    print("\n" + "=" * 60)
    print("Diagnosis complete.")
    print("=" * 60)
