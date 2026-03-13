"""Export SQLite data to static JSON files for Cloudflare Pages deployment."""

import json
import os
from datetime import date, datetime
from database import get_db, get_city_stats, get_summary_history
from config import DB_PATH

OUTPUT_DIR = os.path.join("public", "api")


def export_summary(conn, today):
    latest = conn.execute(
        "SELECT * FROM daily_summary ORDER BY snapshot_date DESC LIMIT 1"
    ).fetchone()

    history = get_summary_history(conn, 90)

    snapshot_date = latest["snapshot_date"] if latest else today
    type_stats = conn.execute("""
        SELECT
            CASE
                WHEN attribute_tags LIKE '%高速%' THEN '高速站'
                WHEN station_name LIKE '%比亚迪%'
                    OR station_name LIKE '%方程豹%'
                    OR station_name LIKE '%腾势%'
                    OR station_name LIKE '%仰望%'
                    OR station_name LIKE '%领汇%'
                    OR station_name LIKE '%4S%' THEN '4S站'
                ELSE '公共站'
            END as station_type,
            COUNT(*) as count
        FROM stations
        GROUP BY station_type
    """).fetchall()

    return {
        "latest": dict(latest) if latest else None,
        "last_updated": datetime.fromtimestamp(os.path.getmtime(DB_PATH)).strftime("%Y-%m-%d %H:%M"),
        "history": [dict(h) for h in history],
        "type_breakdown": [dict(t) for t in type_stats],
    }


def export_cities(conn, today):
    latest = conn.execute(
        "SELECT snapshot_date FROM daily_summary ORDER BY snapshot_date DESC LIMIT 1"
    ).fetchone()
    snapshot_date = latest["snapshot_date"] if latest else today
    cities = get_city_stats(conn, snapshot_date)
    return [dict(c) for c in cities]


def export_stations(conn, today):
    stations = conn.execute("""
        SELECT id, station_name, address, province, city, lat, lng,
               flash_charge_num, fast_charge_num, slow_charge_num, super_charge_num,
               service_tags, attribute_tags, first_seen, last_seen
        FROM stations
        ORDER BY station_name
    """).fetchall()

    return [dict(s) for s in stations]


def export_growth(conn):
    data = conn.execute("""
        SELECT snapshot_date, total_stations, new_stations_today,
               total_flash_connectors, total_fast_connectors,
               total_slow_connectors, total_super_connectors,
               city_count
        FROM daily_summary
        ORDER BY snapshot_date
    """).fetchall()
    return [dict(d) for d in data]


def write_json(filename, data):
    path = os.path.join(OUTPUT_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    size = os.path.getsize(path)
    print(f"  {filename}: {size:,} bytes")


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    today = date.today().isoformat()

    conn = get_db()
    try:
        print("Exporting JSON to public/api/ ...")
        write_json("summary.json", export_summary(conn, today))
        write_json("cities.json", export_cities(conn, today))
        write_json("stations.json", export_stations(conn, today))
        write_json("growth.json", export_growth(conn))
        print("Done!")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
