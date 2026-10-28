"""
Data layer for the SMRT fleet inspection app.

  * Lakebase Postgres (Neon) holds vehicles, users and fleet history.
    App traffic goes through a psycopg connection pool on the pooled
    DATABASE_URL; the schema is applied over DATABASE_URL_UNPOOLED
    (direct connection), as Neon recommends for DDL.
  * Neon Object Storage (S3-compatible) holds the plate/odometer photos.
    Postgres stores only each photo's object key.

Connection settings come from the env vars `neon deploy` / `neon env pull`
write into .env.local: DATABASE_URL, DATABASE_URL_UNPOOLED,
AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_ENDPOINT_URL_S3, AWS_REGION.
"""
import os
import re
import uuid
import atexit
import base64
import binascii

import boto3
import psycopg
from botocore.config import Config
from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Real environment variables win over the files; .env.local wins over .env.
load_dotenv(os.path.join(BASE_DIR, ".env.local"))
load_dotenv(os.path.join(BASE_DIR, ".env"))

DATABASE_URL = os.environ.get("DATABASE_URL")
DATABASE_URL_UNPOOLED = os.environ.get("DATABASE_URL_UNPOOLED") or DATABASE_URL
# Declared in neon.ts under `buckets`.
BUCKET = os.environ.get("NEON_BUCKET", "assets")

VEHICLE_FIELDS = [
    "id", "vehicle_type", "plate_image_key", "plate_text",
    "odometer_image_key", "odometer_text", "tyres_json",
    "route_type", "added_by", "added_by_username", "last_edited",
    "tyre_readings_json",
]
USER_FIELDS = ["username", "password", "role", "name"]
FLEET_HISTORY_FIELDS = [
    "vehicle_type", "route_type", "plate_text", "odometer_text",
    "tyres_json", "entered_by", "snapshot_date",
    "uploaded_by", "uploaded_at", "source_filename",
]

_pool = None
_s3 = None


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
def init():
    """Apply the schema and open the connection pool. Safe to call twice."""
    global _pool
    if _pool is not None:
        return
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. Run `neon deploy` (or `neon env pull`) "
            "in this folder to write it into .env.local."
        )
    init_schema()
    _pool = ConnectionPool(
        DATABASE_URL,
        min_size=1,
        max_size=10,
        # Neon's pooler is PgBouncer in transaction mode: don't rely on
        # server-side prepared statements surviving between transactions.
        kwargs={"prepare_threshold": None, "row_factory": dict_row},
        # Computes scale to zero, which drops idle connections; check each
        # one before handing it out and recycle idle ones.
        check=ConnectionPool.check_connection,
        max_idle=240,
        open=True,
    )
    _pool.wait(timeout=30)
    atexit.register(close)


def close():
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def init_schema():
    with open(os.path.join(BASE_DIR, "schema.sql"), encoding="utf-8") as f:
        ddl = f.read()
    with psycopg.connect(DATABASE_URL_UNPOOLED, autocommit=True) as conn:
        conn.execute(ddl)


def _conn():
    if _pool is None:
        init()
    return _pool.connection()


def s3():
    """boto3 reads AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION and
    AWS_ENDPOINT_URL_S3 from the environment. Neon requires path-style
    addressing and SigV4."""
    global _s3
    if _s3 is None:
        _s3 = boto3.client(
            "s3",
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
    return _s3


# ---------------------------------------------------------------------------
# Vehicles
# ---------------------------------------------------------------------------
_VEHICLE_COLS = ", ".join(VEHICLE_FIELDS)


def list_vehicles():
    with _conn() as conn:
        return conn.execute(f"SELECT {_VEHICLE_COLS} FROM vehicles ORDER BY seq").fetchall()


def get_vehicle(vid):
    with _conn() as conn:
        return conn.execute(
            f"SELECT {_VEHICLE_COLS} FROM vehicles WHERE id = %s", (vid,)
        ).fetchone()


def upsert_vehicle(row):
    values = [row.get(f) or "" for f in VEHICLE_FIELDS]
    updates = ", ".join(f"{f} = EXCLUDED.{f}" for f in VEHICLE_FIELDS if f != "id")
    with _conn() as conn:
        conn.execute(
            f"INSERT INTO vehicles ({_VEHICLE_COLS}) VALUES ({', '.join(['%s'] * len(values))}) "
            f"ON CONFLICT (id) DO UPDATE SET {updates}",
            values,
        )


def update_vehicle_fields(vid, fields):
    """fields: {column: value} for columns in VEHICLE_FIELDS. Returns True
    if the vehicle existed."""
    cols = [c for c in fields if c in VEHICLE_FIELDS and c != "id"]
    if not cols:
        return get_vehicle(vid) is not None
    sets = ", ".join(f"{c} = %s" for c in cols)
    with _conn() as conn:
        cur = conn.execute(
            f"UPDATE vehicles SET {sets} WHERE id = %s",
            [fields[c] for c in cols] + [vid],
        )
        return cur.rowcount > 0


def delete_vehicles(ids):
    """Deletes the given vehicles and their photos. Returns how many rows
    were removed."""
    ids = list(ids)
    if not ids:
        return 0
    with _conn() as conn:
        deleted = conn.execute(
            "DELETE FROM vehicles WHERE id = ANY(%s) "
            "RETURNING plate_image_key, odometer_image_key",
            (ids,),
        ).fetchall()
    delete_images([k for r in deleted for k in (r["plate_image_key"], r["odometer_image_key"])])
    return len(deleted)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
def list_users():
    with _conn() as conn:
        return conn.execute("SELECT username, password, role, name FROM users ORDER BY seq").fetchall()


def get_user(username):
    with _conn() as conn:
        return conn.execute(
            "SELECT username, password, role, name FROM users WHERE username = %s", (username,)
        ).fetchone()


def username_taken(username, except_username=None):
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM users WHERE lower(username) = lower(%s) "
            "AND username IS DISTINCT FROM %s",
            (username, except_username),
        ).fetchone()
        return row is not None


def add_user(username, password, role, name):
    with _conn() as conn:
        conn.execute(
            "INSERT INTO users (username, password, role, name) VALUES (%s, %s, %s, %s)",
            (username, password, role, name),
        )


def update_user(username, fields):
    """fields: any of username/password/role/name. Returns True if found."""
    cols = [c for c in fields if c in USER_FIELDS]
    sets = ", ".join(f"{c} = %s" for c in cols)
    with _conn() as conn:
        cur = conn.execute(
            f"UPDATE users SET {sets} WHERE username = %s",
            [fields[c] for c in cols] + [username],
        )
        return cur.rowcount > 0


def delete_user(username):
    with _conn() as conn:
        conn.execute("DELETE FROM users WHERE username = %s", (username,))


def count_staff_except(username):
    with _conn() as conn:
        return conn.execute(
            "SELECT count(*) AS n FROM users WHERE role = 'staff' AND username <> %s", (username,)
        ).fetchone()["n"]


# ---------------------------------------------------------------------------
# Fleet history
# ---------------------------------------------------------------------------
_FH_COLS = ", ".join(FLEET_HISTORY_FIELDS)


def list_fleet_history():
    with _conn() as conn:
        return conn.execute(f"SELECT {_FH_COLS} FROM fleet_history ORDER BY seq").fetchall()


def add_fleet_history(rows):
    """Inserts snapshot rows, skipping any (type, plate, snapshot date)
    already stored or repeated within `rows`. Returns (added, skipped)."""
    if not rows:
        return 0, 0
    columns = [[r.get(f) or "" for r in rows] for f in FLEET_HISTORY_FIELDS]
    with _conn() as conn:
        inserted = conn.execute(
            f"INSERT INTO fleet_history ({_FH_COLS}) "
            f"SELECT * FROM unnest({', '.join(['%s::text[]'] * len(FLEET_HISTORY_FIELDS))}) "
            "ON CONFLICT (vehicle_type, (upper(btrim(plate_text))), snapshot_date) DO NOTHING "
            "RETURNING 1",
            columns,
        ).fetchall()
    return len(inserted), len(rows) - len(inserted)


def clear_fleet_history():
    with _conn() as conn:
        conn.execute("DELETE FROM fleet_history")


# ---------------------------------------------------------------------------
# Photos (Neon Object Storage)
# ---------------------------------------------------------------------------
_DATA_URL_RE = re.compile(r"^data:(image/[\w.+-]+);base64,(.*)$", re.S)
_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}


def is_data_url(value):
    return isinstance(value, str) and value.startswith("data:image/")


def put_image(vid, kind, data_url, key=None):
    """Decodes a 'data:image/...;base64,...' string from the browser and
    uploads it. Each upload gets a fresh key unless one is given, so
    browsers never see a stale cached photo. Returns the object key."""
    m = _DATA_URL_RE.match(data_url.strip())
    if not m:
        raise ValueError("Photo must be a base64 image data URL.")
    content_type = m.group(1)
    try:
        body = base64.b64decode(re.sub(r"\s+", "", m.group(2)), validate=True)
    except binascii.Error as exc:
        raise ValueError(f"Photo isn't valid base64: {exc}")
    key = key or f"vehicles/{vid}/{kind}-{uuid.uuid4().hex}.{_EXT.get(content_type, 'bin')}"
    s3().put_object(Bucket=BUCKET, Key=key, Body=body, ContentType=content_type)
    return key


def get_image(key):
    """Returns (bytes, content_type)."""
    obj = s3().get_object(Bucket=BUCKET, Key=key)
    return obj["Body"].read(), obj.get("ContentType") or "application/octet-stream"


def get_image_data_url(key):
    """The OCR modules take a base64 data URL, the format the CSV stored."""
    body, content_type = get_image(key)
    return f"data:{content_type};base64,{base64.b64encode(body).decode('ascii')}"


def delete_images(keys):
    keys = [k for k in keys if k]
    for i in range(0, len(keys), 1000):
        s3().delete_objects(
            Bucket=BUCKET,
            Delete={"Objects": [{"Key": k} for k in keys[i:i + 1000]], "Quiet": True},
        )
