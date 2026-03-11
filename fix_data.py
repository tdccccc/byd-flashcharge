"""One-time data fix: re-extract city for all stations and recalculate daily_summary."""

from database import get_db, extract_city_from_name, update_daily_summary


def main():
    conn = get_db()

    # 1. Re-extract city for all stations from station_name
    rows = conn.execute("SELECT id, station_name, city FROM stations").fetchall()
    updated = 0
    still_missing = []

    for row in rows:
        new_city = extract_city_from_name(row["station_name"])
        if new_city and new_city != (row["city"] or ""):
            conn.execute("UPDATE stations SET city = ? WHERE id = ?", (new_city, row["id"]))
            updated += 1
        elif not new_city and not row["city"]:
            still_missing.append((row["id"], row["station_name"]))

    conn.commit()
    print(f"Updated city for {updated} stations")

    if still_missing:
        print(f"\nStill missing city ({len(still_missing)} stations):")
        for sid, name in still_missing[:20]:
            print(f"  {sid}: {name}")
        if len(still_missing) > 20:
            print(f"  ... and {len(still_missing) - 20} more")

    # 2. Recalculate daily_summary for all dates
    dates = conn.execute(
        "SELECT DISTINCT snapshot_date FROM daily_summary ORDER BY snapshot_date"
    ).fetchall()

    for row in dates:
        d = row["snapshot_date"]
        update_daily_summary(conn, d)
    conn.commit()
    print(f"\nRecalculated daily_summary for {len(dates)} dates")

    # 3. Print stats
    total = conn.execute("SELECT COUNT(*) FROM stations").fetchone()[0]
    with_city = conn.execute(
        "SELECT COUNT(*) FROM stations WHERE city IS NOT NULL AND city != ''"
    ).fetchone()[0]
    distinct = conn.execute(
        "SELECT COUNT(DISTINCT city) FROM stations WHERE city IS NOT NULL AND city != ''"
    ).fetchone()[0]
    print(f"\nStats: {total} total stations, {with_city} with city ({distinct} distinct cities)")

    # Top 10 cities
    print("\nTop 10 cities:")
    for row in conn.execute(
        "SELECT city, COUNT(*) as cnt FROM stations WHERE city != '' GROUP BY city ORDER BY cnt DESC LIMIT 10"
    ):
        print(f"  {row['city']}: {row['cnt']}")

    conn.close()


if __name__ == "__main__":
    main()
