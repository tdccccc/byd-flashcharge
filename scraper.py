"""Scraper for BYD Flash Charge stations - scans all of China.

Uses district centroids + highway corridor points (see scan_points.py)
instead of a fixed grid, giving better urban coverage with fewer requests.
"""

import json
import os
import time
import ssl
import requests
import logging
from datetime import date
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import API_URL, REQUEST_HEADERS, REQUEST_TEMPLATE, CONCURRENT_WORKERS
from database import init_db, get_db, upsert_station, insert_daily_snapshot, update_daily_summary
from geocoder import geocode_pending_stations
from scan_points import load_scan_points

os.makedirs("data", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("data/scraper.log"),
    ],
)
log = logging.getLogger(__name__)


def fetch_stations(lat: float, lng: float, max_retries: int = 5) -> list:
    """Fetch stations near a given coordinate with retry on transient errors."""
    req_data = REQUEST_TEMPLATE.copy()
    req_data["lat"] = lat
    req_data["lng"] = lng
    req_data["reqTimestamp"] = int(time.time() * 1000)

    payload = {"request": json.dumps(req_data)}

    for attempt in range(max_retries):
        try:
            resp = requests.post(API_URL, json=payload, headers=REQUEST_HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            inner = json.loads(data.get("response", "{}"))
            if inner.get("code") != "0":
                msg = inner.get("message", "")
                if "繁忙" in msg or "稍后" in msg:
                    if attempt < max_retries - 1:
                        wait = 2 ** attempt + 1
                        log.warning(f"Rate limited at ({lat}, {lng}), retry {attempt+1}/{max_retries} in {wait}s")
                        time.sleep(wait)
                        continue
                log.warning(f"API error at ({lat}, {lng}): {msg}")
                return []

            respond_data = json.loads(inner.get("respondData", "{}"))
            return respond_data.get("rows", [])

        except (ssl.SSLError, requests.exceptions.SSLError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                log.warning(f"Retry {attempt+1}/{max_retries} at ({lat}, {lng}): {e}")
                time.sleep(wait)
            else:
                log.error(f"All {max_retries} retries failed at ({lat}, {lng}): {e}")
                return []

        except Exception as e:
            log.error(f"Request failed at ({lat}, {lng}): {e}")
            return []

    return []


def batch_fetch(coords, label=""):
    """Fetch stations for a list of coordinates concurrently. Returns {id: station}."""
    results = {}
    total = len(coords)

    with ThreadPoolExecutor(max_workers=CONCURRENT_WORKERS) as executor:
        future_to_coord = {
            executor.submit(fetch_stations, lat, lng): (lat, lng)
            for lat, lng in coords
        }
        done = 0
        for future in as_completed(future_to_coord):
            done += 1
            stations = future.result()
            for s in stations:
                results[s["id"]] = s
            if done % 200 == 0 and label:
                log.info(f"  {label}: {done}/{total} done, {len(results)} unique so far")

    return results


def run_full_scan():
    """Scan all district centroids + highway points for full coverage."""
    init_db()
    today = date.today().isoformat()
    t_start = time.time()

    coords = load_scan_points()
    log.info(f"Scanning {len(coords)} points ({CONCURRENT_WORKERS} workers)")

    all_stations = batch_fetch(coords, "Scan")

    # Save to database
    elapsed = time.time() - t_start
    log.info(f"Scan complete: {len(all_stations)} stations in {elapsed:.0f}s")
    log.info("Saving to database...")

    conn = get_db()
    try:
        for station in all_stations.values():
            upsert_station(conn, station, today)
            insert_daily_snapshot(conn, station, today)
        conn.commit()
        log.info("Database updated. Running geocoder...")

        geocode_pending_stations(conn)
        update_daily_summary(conn, today)
        conn.commit()
        log.info("Geocoding and summary complete.")
    finally:
        conn.close()

    # Also save raw JSON
    raw_path = f"data/raw_{today}.json"
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(list(all_stations.values()), f, ensure_ascii=False, indent=2)
    log.info(f"Raw data saved to {raw_path}")

    return all_stations


if __name__ == "__main__":
    stations = run_full_scan()
    print(f"\nDone! Found {len(stations)} unique stations across China.")
