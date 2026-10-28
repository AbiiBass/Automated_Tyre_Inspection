"""
One-time import of the old CSV data (data/*.csv) into Neon.

  * users.csv          -> users table
  * vehicles.csv       -> vehicles table; the base64 plate/odometer photos
                          are decoded and uploaded to the Object Storage
                          bucket, and only their keys are stored in Postgres
  * fleet_history.csv  -> fleet_history table

Safe to re-run: rows that already exist are left as they are, and photos
are uploaded to fixed keys (vehicles/<id>/<kind>-csv.<ext>), so a second
run overwrites the same objects instead of creating duplicates.

    python migrate_csv_to_neon.py
"""
import os
import csv
import sys

import db

DATA_DIR = os.path.join(db.BASE_DIR, "data")

# The photos are base64 strings inside CSV cells, far over csv's default
# per-field limit.
csv.field_size_limit(2**31 - 1)


def read_csv(name):
    path = os.path.join(DATA_DIR, name)
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def migrate_users():
    added = 0
    for u in read_csv("users.csv"):
        if db.get_user(u["username"]):
            continue
        db.add_user(u["username"], u["password"], u["role"], u["name"])
        added += 1
    return added


def migrate_vehicles():
    added = skipped = photos = 0
    for r in read_csv("vehicles.csv"):
        if db.get_vehicle(r["id"]):
            skipped += 1
            continue
        row = {f: r.get(f) or "" for f in db.VEHICLE_FIELDS}
        for kind in ("plate", "odometer"):
            data_url = r.get(f"{kind}_image") or ""
            if db.is_data_url(data_url):
                ext = "png" if data_url.startswith("data:image/png") else "jpg"
                row[f"{kind}_image_key"] = db.put_image(
                    r["id"], kind, data_url, key=f"vehicles/{r['id']}/{kind}-csv.{ext}"
                )
                photos += 1
        db.upsert_vehicle(row)
        added += 1
    return added, skipped, photos


def migrate_fleet_history():
    return db.add_fleet_history(read_csv("fleet_history.csv"))


if __name__ == "__main__":
    db.init()
    print(f"users:         {migrate_users()} added")
    v_added, v_skipped, photos = migrate_vehicles()
    print(f"vehicles:      {v_added} added, {v_skipped} already present, {photos} photos uploaded")
    fh_added, fh_skipped = migrate_fleet_history()
    print(f"fleet history: {fh_added} added, {fh_skipped} already present")
    sys.exit(0)
