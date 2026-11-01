-- Schema for the SMRT fleet inspection app (Lakebase Postgres on Neon).
-- Applied on startup by db.init_schema() over the direct (unpooled)
-- connection. Every statement is idempotent, so re-running it is safe.

-- One row per vehicle inspection (current state; overwritten on edit).
-- Photos live in the Neon Object Storage bucket; only their keys are here.
CREATE TABLE IF NOT EXISTS vehicles (
    seq                 BIGSERIAL,
    id                  TEXT PRIMARY KEY,
    vehicle_type        TEXT NOT NULL,
    plate_image_key     TEXT NOT NULL DEFAULT '',
    plate_text          TEXT NOT NULL DEFAULT '',
    odometer_image_key  TEXT NOT NULL DEFAULT '',
    odometer_text       TEXT NOT NULL DEFAULT '',
    tyres_json          TEXT NOT NULL DEFAULT '{}',
    route_type          TEXT NOT NULL DEFAULT '',
    added_by            TEXT NOT NULL DEFAULT '',
    added_by_username   TEXT NOT NULL DEFAULT '',
    -- 'YYYY-MM-DD HH:MM:SS' local time, the same format the CSV used;
    -- the app compares and filters on this string.
    last_edited         TEXT NOT NULL DEFAULT '',
    -- {"FL": [r1, r2, r3, r4, r5, avg], ...} for ESP32-measured tyres only.
    tyre_readings_json  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS vehicles_last_edited_idx ON vehicles (last_edited);
CREATE INDEX IF NOT EXISTS vehicles_added_by_username_idx ON vehicles (added_by_username);

-- Login accounts. Passwords are still plain text, as in users.csv.
CREATE TABLE IF NOT EXISTS users (
    seq       BIGSERIAL,
    username  TEXT PRIMARY KEY,
    password  TEXT NOT NULL,
    role      TEXT NOT NULL CHECK (role IN ('technician', 'staff')),
    name      TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS users_username_lower_idx ON users (lower(username));

-- Fleet Overview history: one row per (type, plate, snapshot date),
-- accumulated from uploaded spreadsheets.
CREATE TABLE IF NOT EXISTS fleet_history (
    seq              BIGSERIAL PRIMARY KEY,
    vehicle_type     TEXT NOT NULL,
    route_type       TEXT NOT NULL DEFAULT '',
    plate_text       TEXT NOT NULL,
    odometer_text    TEXT NOT NULL DEFAULT '',
    tyres_json       TEXT NOT NULL DEFAULT '{}',
    entered_by       TEXT NOT NULL DEFAULT '',
    snapshot_date    TEXT NOT NULL,
    uploaded_by      TEXT NOT NULL DEFAULT '',
    uploaded_at      TEXT NOT NULL DEFAULT '',
    source_filename  TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS fleet_history_snapshot_key
    ON fleet_history (vehicle_type, (upper(btrim(plate_text))), snapshot_date);
