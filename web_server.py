"""Web server for BYD Flash Charge Station dashboard."""

from flask import Flask, render_template, jsonify
from database import get_db, get_city_stats, get_summary_history
from config import DB_PATH
from datetime import date, datetime
import os

app = Flask(__name__, template_folder="public", static_folder="public/static")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/summary.json")
def api_summary():
    """Current day summary + recent history."""
    conn = get_db()
    try:
        # Latest summary
        latest = conn.execute(
            "SELECT * FROM daily_summary ORDER BY snapshot_date DESC LIMIT 1"
        ).fetchone()

        # History for charts
        history = get_summary_history(conn, 90)

        # Station type breakdown
        today = latest["snapshot_date"] if latest else date.today().isoformat()

        # Count by attribute tags
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

        return jsonify({
            "latest": dict(latest) if latest else None,
            "last_updated": datetime.fromtimestamp(os.path.getmtime(DB_PATH)).strftime("%Y-%m-%d %H:%M"),
            "history": [dict(h) for h in history],
            "type_breakdown": [dict(t) for t in type_stats],
        })
    finally:
        conn.close()


@app.route("/api/cities.json")
def api_cities():
    """City-level statistics."""
    conn = get_db()
    try:
        latest = conn.execute(
            "SELECT snapshot_date FROM daily_summary ORDER BY snapshot_date DESC LIMIT 1"
        ).fetchone()
        today = latest["snapshot_date"] if latest else date.today().isoformat()
        cities = get_city_stats(conn, today)
        return jsonify([dict(c) for c in cities])
    finally:
        conn.close()


@app.route("/api/stations.json")
def api_stations():
    """All station details."""
    conn = get_db()
    try:
        stations = conn.execute("""
            SELECT id, station_name, address, province, city, lat, lng,
                   flash_charge_num, fast_charge_num, slow_charge_num, super_charge_num,
                   service_tags, attribute_tags, first_seen, last_seen
            FROM stations
            ORDER BY station_name
        """).fetchall()

        return jsonify([dict(s) for s in stations])
    finally:
        conn.close()


@app.route("/api/growth.json")
def api_growth():
    """Daily growth data for charts."""
    conn = get_db()
    try:
        data = conn.execute("""
            SELECT snapshot_date, total_stations, new_stations_today,
                   total_flash_connectors, total_fast_connectors,
                   total_slow_connectors, total_super_connectors,
                   city_count
            FROM daily_summary
            ORDER BY snapshot_date
        """).fetchall()
        return jsonify([dict(d) for d in data])
    finally:
        conn.close()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
