"""Scraper for BYD Flash Charge stations - scans all of China.

Uses district centroids + highway corridor points (see scan_points.py)
instead of a fixed grid, giving better urban coverage with fewer requests.
"""

import json
import os
import time
import ssl
import threading
import requests
import logging
from datetime import date
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import API_URL, REQUEST_HEADERS, REQUEST_TEMPLATE, CONCURRENT_WORKERS, generate_imei_md5
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


class GlobalBackoff:
    """Shared backoff state across all worker threads.

    When any thread hits a rate limit, it sets a global cooldown that
    all threads respect before sending their next request.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._resume_at = 0.0  # timestamp when requests can resume
        self.rate_limit_hits = 0
        self.network_errors = 0
        self.api_errors = 0

    def trigger(self, wait_seconds: float):
        """Signal that rate limiting was detected; all threads should pause."""
        with self._lock:
            self.rate_limit_hits += 1
            new_resume = time.time() + wait_seconds
            if new_resume > self._resume_at:
                self._resume_at = new_resume

    def record_network_error(self):
        with self._lock:
            self.network_errors += 1

    def record_api_error(self):
        with self._lock:
            self.api_errors += 1

    def wait_if_needed(self):
        """Block the calling thread until the global cooldown expires."""
        with self._lock:
            remaining = self._resume_at - time.time()
        if remaining > 0:
            time.sleep(remaining)

    def summary(self) -> str:
        parts = []
        if self.rate_limit_hits:
            parts.append(f"rate_limited={self.rate_limit_hits}")
        if self.network_errors:
            parts.append(f"network_errors={self.network_errors}")
        if self.api_errors:
            parts.append(f"api_errors={self.api_errors}")
        return ", ".join(parts) if parts else "no errors"


# Module-level instance shared by all workers
_backoff = GlobalBackoff()


def fetch_stations(lat: float, lng: float, max_retries: int = 5) -> list:
    """Fetch stations near a given coordinate with retry on transient errors."""
    for attempt in range(max_retries):
        _backoff.wait_if_needed()

        req_data = REQUEST_TEMPLATE.copy()
        req_data["imeiMD5"] = generate_imei_md5()
        req_data["lat"] = lat
        req_data["lng"] = lng
        req_data["reqTimestamp"] = int(time.time() * 1000)
        payload = {"request": json.dumps(req_data)}

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
                        _backoff.trigger(wait)
                        time.sleep(wait)
                        continue
                    return []
                _backoff.record_api_error()
                return []

            respond_data = json.loads(inner.get("respondData", "{}"))
            return respond_data.get("rows", [])

        except (ssl.SSLError, requests.exceptions.SSLError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout):
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                _backoff.trigger(wait)
                time.sleep(wait)
            else:
                _backoff.record_network_error()
                return []

        except Exception as e:
            log.error(f"Unexpected error at ({lat}, {lng}): {e}")
            return []

    return []


def batch_fetch(coords, label=""):
    """Fetch stations for a list of coordinates concurrently. Returns {id: station}."""
    global _backoff
    _backoff = GlobalBackoff()

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

    log.info(f"  {label} errors: {_backoff.summary()}")
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
