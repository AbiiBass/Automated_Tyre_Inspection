import os
import csv
import sys
import json
import uuid
import base64
import threading
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, jsonify, send_file, flash
)
from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from io import BytesIO

# ---------------------------------------------------------------------------
# The plate/odometer photos are stored as base64 strings inside CSV cells,
# which can comfortably exceed Python's default per-field read limit
# (131072 bytes). Raise it generously so large images don't blow up with
# "_csv.Error: field larger than field limit" when the file is re-read.
# ---------------------------------------------------------------------------
_max_int = sys.maxsize
while True:
    try:
        csv.field_size_limit(_max_int)
        break
    except OverflowError:
        _max_int = int(_max_int / 10)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE_DIR, "data", "vehicles.csv")
USERS_CSV_PATH = os.path.join(BASE_DIR, "data", "users.csv")
CSV_FIELDS = [
    "id", "vehicle_type", "plate_image", "plate_text",
    "odometer_image", "odometer_text", "tyres_json",
    "route_type", "added_by", "added_by_username", "last_edited"
]
USERS_CSV_FIELDS = ["username", "password", "role", "name"]

app = Flask(__name__)
app.secret_key = "smrt-fleet-inspection-secret-key-change-in-production"

# ---------------------------------------------------------------------------
# User accounts now live in data/users.csv (see ensure_users_csv for the
# starter demo accounts it creates on first run). In production, replace
# plain-text passwords with hashes (e.g. werkzeug.security).
# ---------------------------------------------------------------------------
# def ensure_users_csv():
#     if not os.path.exists(USERS_CSV_PATH):
#         with open(USERS_CSV_PATH, "w", newline="", encoding="utf-8") as f:
#             writer = csv.DictWriter(f, fieldnames=USERS_CSV_FIELDS)
#             writer.writeheader()
#             writer.writerows([
#                 {"username": "tech1",  "password": "tech123",  "role": "technician", "name": "Ah Kow"},
#                 {"username": "tech2",  "password": "tech123",  "role": "technician", "name": "Ravi"},
#                 {"username": "staff1", "password": "staff123", "role": "staff",      "name": "Mei Ling"},
#                 {"username": "staff2", "password": "staff123", "role": "staff",      "name": "Farah"},
#             ])


def load_users():
    """Read data/users.csv fresh on every call so edits to the file take
    effect without restarting the server."""
    # ensure_users_csv()
    users = {}
    with open(USERS_CSV_PATH, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            users[row["username"]] = {
                "password": row["password"],
                "role": row["role"],
                "name": row["name"],
            }
    return users



def _p(code, label):
    return {"code": code, "label": label}


VEHICLE_CONFIG = {
    "car": {
        "label": "Car",
        "icon": "car.png",
        "tyre_count": 4,
        "has_route_type": False,
        "rows": [
            {"row": "Front", "left": [_p("FL", "Front Left")],  "right": [_p("FR", "Front Right")]},
            {"row": "Back",  "left": [_p("BL", "Back Left")],   "right": [_p("BR", "Back Right")]},
        ],
    },
    "bus": {
        "label": "Bus",
        "icon": "bus.png",
        "tyre_count": 6,
        "has_route_type": True,
        "rows": [
            {"row": "Front", "left": [_p("FL", "Front Left")], "right": [_p("FR", "Front Right")]},
            {"row": "Back",  "left": [_p("BL1", "Back Left 1"), _p("BL2", "Back Left 2")],
                              "right": [_p("BR1", "Back Right 1"), _p("BR2", "Back Right 2")]},
        ],
    },
    "doubledecker": {
        "label": "Double Decker Bus",
        "icon": "double-decker-bus.png",
        "tyre_count": 10,
        "has_route_type": True,
        "rows": [
            {"row": "Front",  "left": [_p("FL", "Front Left")], "right": [_p("FR", "Front Right")]},
            {"row": "Middle", "left": [_p("ML1", "Middle Left 1"), _p("ML2", "Middle Left 2")],
                               "right": [_p("MR1", "Middle Right 1"), _p("MR2", "Middle Right 2")]},
            {"row": "Back",   "left": [_p("BL1", "Back Left 1"), _p("BL2", "Back Left 2")],
                               "right": [_p("BR1", "Back Right 1"), _p("BR2", "Back Right 2")]},
        ],
    },
}


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------
def ensure_csv():
    if not os.path.exists(CSV_PATH):
        with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            writer.writeheader()


def read_all():
    ensure_csv()
    with open(CSV_PATH, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def write_all(rows):
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def find_row(rows, vid):
    for r in rows:
        if r["id"] == vid:
            return r
    return None


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------
# csv_lock guards every read-modify-write cycle against the CSV file, since
# the "Convert All Plates" batch job (below) runs on a background thread
# that reads/writes rows independently of whatever request thread a staff
# member's own edit might be on at the same moment.
#
# ocr_inference_lock serializes every call into license_plate.py's shared,
# already-loaded OCR model — one prediction at a time, whether it was
# triggered by the single "Ask AI" button or the batch job. PaddleOCR's
# pipeline object isn't documented as safe for concurrent predict() calls,
# so this is a deliberate correctness choice, not just a performance knob:
# see the "Convert All Plates" section in README.md for the full reasoning.
csv_lock = threading.Lock()
ocr_inference_lock = threading.Lock()

# In-memory batch-job tracking for "Convert All Plates". This only works
# within a single running process — see the README note on deployment.
ocr_jobs = {}
ocr_jobs_lock = threading.Lock()


def _parse_last_edited(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def filter_by_date(rows, date_filter):
    """date_filter: 'all' | 'today' | 'week' (current calendar week, Mon-Sun to date)."""
    if date_filter not in ("today", "week"):
        return rows

    now = datetime.now()
    if date_filter == "today":
        today_str = now.strftime("%Y-%m-%d")
        return [r for r in rows if r.get("last_edited", "").startswith(today_str)]

    # date_filter == "week": from this week's Monday 00:00 up to now
    week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    result = []
    for r in rows:
        dt = _parse_last_edited(r.get("last_edited", ""))
        if dt and dt >= week_start:
            result.append(r)
    return result


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def login_required(role=None):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if "username" not in session:
                return redirect(url_for("login"))
            if role and session.get("role") != role:
                return redirect(url_for("login"))
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Routes: Auth
# ---------------------------------------------------------------------------
@app.route("/", methods=["GET"])
def index():
    if "username" not in session:
        return redirect(url_for("login"))
    if session["role"] == "technician":
        return redirect(url_for("technician_home"))
    return redirect(url_for("staff_home"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        users = load_users()
        user = users.get(username)
        if user and user["password"] == password:
            session["username"] = username
            session["role"] = user["role"]
            session["display_name"] = user["name"]
            if user["role"] == "technician":
                return redirect(url_for("technician_home"))
            return redirect(url_for("staff_home"))
        error = "Incorrect User ID or password. Please try again."
        return render_template("login.html", error=error)
    return render_template("login.html", error=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Routes: Technician
# ---------------------------------------------------------------------------
@app.route("/technician")
@login_required(role="technician")
def technician_home():
    rows = read_all()
    rows = filter_by_date(rows, "today")
    my_username = session.get("username")
    rows = [r for r in rows if r.get("added_by_username") == my_username]
    rows.sort(key=lambda r: r.get("last_edited", ""), reverse=True)
    return render_template(
        "technician.html",
        vehicles=rows,
        vehicle_config=VEHICLE_CONFIG,
        display_name=session.get("display_name"),
    )


@app.route("/technician/config")
@login_required(role="technician")
def technician_config():
    return jsonify(VEHICLE_CONFIG)


@app.route("/technician/save", methods=["POST"])
@login_required(role="technician")
def technician_save():
    data = request.get_json(force=True)
    vehicle_type = data.get("vehicle_type")
    if vehicle_type not in VEHICLE_CONFIG:
        return jsonify({"error": "Invalid vehicle type"}), 400

    tyres = data.get("tyres", {})
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    my_username = session.get("username")

    has_route_type = VEHICLE_CONFIG[vehicle_type].get("has_route_type", False)
    route_type = data.get("route_type", "feeder")
    if not has_route_type or route_type not in ("feeder", "trunk"):
        route_type = "" if not has_route_type else "feeder"

    rows = read_all()
    vid = data.get("id")

    row = {
        "id": vid if vid else str(uuid.uuid4()),
        "vehicle_type": vehicle_type,
        "plate_image": data.get("plate_image", ""),
        "plate_text": data.get("plate_text", ""),
        "odometer_image": data.get("odometer_image", ""),
        "odometer_text": data.get("odometer_text", ""),
        "tyres_json": json.dumps(tyres),
        "route_type": route_type,
        "added_by": session.get("display_name", my_username),
        "added_by_username": my_username,
        "last_edited": now,
    }

    with csv_lock:
        rows = read_all()
        if vid:
            existing = find_row(rows, vid)
            if existing is None:
                return jsonify({"error": "Record not found"}), 404
            # A technician may only edit inspections they logged themselves.
            if existing.get("added_by_username") != my_username:
                return jsonify({"error": "You can only edit inspections you added yourself."}), 403
            # preserve staff-entered text if technician edit didn't touch images
            if not row["plate_image"]:
                row["plate_image"] = existing["plate_image"]
            if not row["odometer_image"]:
                row["odometer_image"] = existing["odometer_image"]
            if not data.get("plate_text_touched"):
                row["plate_text"] = existing.get("plate_text", "")
            if not data.get("odometer_text_touched"):
                row["odometer_text"] = existing.get("odometer_text", "")
            # keep the name of the technician who originally logged this vehicle
            row["added_by"] = existing.get("added_by") or row["added_by"]
            row["added_by_username"] = existing.get("added_by_username") or row["added_by_username"]
            rows = [r if r["id"] != vid else row for r in rows]
        else:
            rows.append(row)

        write_all(rows)
    return jsonify({"success": True, "id": row["id"]})


@app.route("/technician/vehicle/<vid>")
@login_required(role="technician")
def technician_get_vehicle(vid):
    rows = read_all()
    row = find_row(rows, vid)
    if not row:
        return jsonify({"error": "Not found"}), 404
    if row.get("added_by_username") != session.get("username"):
        return jsonify({"error": "You can only view inspections you added yourself."}), 403
    row = dict(row)
    row["tyres"] = json.loads(row.get("tyres_json") or "{}")
    return jsonify(row)


@app.route("/technician/delete/<vid>", methods=["POST"])
@login_required(role="technician")
def technician_delete(vid):
    with csv_lock:
        rows = read_all()
        existing = find_row(rows, vid)
        if not existing:
            return jsonify({"error": "Not found"}), 404
        if existing.get("added_by_username") != session.get("username"):
            return jsonify({"error": "You can only delete inspections you added yourself."}), 403
        new_rows = [r for r in rows if r["id"] != vid]
        if len(new_rows) == len(rows):
            return jsonify({"error": "Not found"}), 404
        write_all(new_rows)
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# Routes: SMRT Staff
# ---------------------------------------------------------------------------
@app.route("/staff")
@login_required(role="staff")
def staff_home():
    date_filter = request.args.get("date_filter", "all")
    all_rows = read_all()

    date_filtered_rows = filter_by_date(all_rows, date_filter)

    counts = {"all": len(date_filtered_rows)}
    for key in VEHICLE_CONFIG:
        counts[key] = len([r for r in date_filtered_rows if r["vehicle_type"] == key])
    counts["feeder"] = len([r for r in date_filtered_rows if r.get("route_type") == "feeder"])
    counts["trunk"] = len([r for r in date_filtered_rows if r.get("route_type") == "trunk"])

    # Type/route filtering and sorting now happen client-side (JS) so the
    # person can combine multiple filters instantly without a page reload.
    # The server hands over everything in the current date range, pre-sorted
    # newest-first as a sensible default.
    rows = date_filtered_rows
    rows.sort(key=lambda r: r.get("last_edited", ""), reverse=True)

    for r in rows:
        r["tyres"] = json.loads(r.get("tyres_json") or "{}")

    return render_template(
        "staff.html",
        vehicles=rows,
        vehicle_config=VEHICLE_CONFIG,
        date_filter=date_filter,
        counts=counts,
        total_all_time=len(all_rows),
        display_name=session.get("display_name"),
    )


@app.route("/staff/update_text/<vid>", methods=["POST"])
@login_required(role="staff")
def staff_update_text(vid):
    data = request.get_json(force=True)
    with csv_lock:
        rows = read_all()
        row = find_row(rows, vid)
        if not row:
            return jsonify({"error": "Not found"}), 404
        if "plate_text" in data:
            row["plate_text"] = data["plate_text"]
        if "odometer_text" in data:
            row["odometer_text"] = data["odometer_text"]
        if "route_type" in data:
            has_route_type = VEHICLE_CONFIG.get(row["vehicle_type"], {}).get("has_route_type", False)
            if not has_route_type:
                return jsonify({"error": "This vehicle type doesn't have a route type."}), 400
            if data["route_type"] not in ("feeder", "trunk"):
                return jsonify({"error": "Route type must be 'feeder' or 'trunk'."}), 400
            row["route_type"] = data["route_type"]
        write_all(rows)
    return jsonify({"success": True})


def _load_ocr_module():
    """
    Import license_plate.py on demand (never at server startup) so its
    heavy, optional OCR dependencies don't affect the rest of the app.
    Returns (module, None) on success, or (None, error_message) if the
    optional packages aren't installed.
    """
    try:
        import license_plate
        return license_plate, None
    except ImportError:
        return None, (
            "The AI plate-reading feature isn't installed on this server. "
            "Run: pip install -r requirements-ocr.txt"
        )


def _predict_plate(ocr_module, plate_image):
    """
    Every OCR prediction — whether from the single "Ask AI" button or the
    batch job — goes through this one choke point, serialized by
    ocr_inference_lock. PaddleOCR's pipeline object isn't documented as
    safe for concurrent predict() calls, so only one prediction ever runs
    at a time across the whole server process.
    """
    with ocr_inference_lock:
        return ocr_module.predict_license_plate(plate_image)


@app.route("/staff/ocr_plate/<vid>", methods=["POST"])
@login_required(role="staff")
def staff_ocr_plate(vid):
    """
    Ask the standalone license_plate.py module to read the plate text out
    of the photo already saved for this vehicle. Kept as a separate module
    (not merged into this file) so its heavy OCR dependencies are entirely
    optional — this route only imports it, and only at request time, so the
    rest of the app runs fine even if those packages aren't installed.
    """
    rows = read_all()
    row = find_row(rows, vid)
    if not row:
        return jsonify({"error": "Vehicle not found"}), 404

    plate_image = row.get("plate_image", "")
    if not plate_image:
        return jsonify({"error": "There's no plate photo saved for this vehicle."}), 400

    ocr_module, err = _load_ocr_module()
    if err:
        return jsonify({"error": err}), 503

    try:
        plate_text = _predict_plate(ocr_module, plate_image)
    except Exception as exc:
        return jsonify({"error": f"AI couldn't read this plate photo: {exc}"}), 500

    if not plate_text:
        return jsonify({"error": "AI couldn't make out any text on this plate photo."}), 422

    return jsonify({"plate": plate_text})


def _run_ocr_batch_job(job_id, vehicle_ids):
    """
    Runs on a single background thread, one vehicle at a time — see the
    README section on "Convert All Plates" for why this is deliberately
    sequential rather than parallelized with threads or processes.
    """
    ocr_module, err = _load_ocr_module()
    if err:
        with ocr_jobs_lock:
            job = ocr_jobs.get(job_id)
            if job is not None:
                job["fatal_error"] = err
                job["done"] = True
        return

    for vid in vehicle_ids:
        entry = {"id": vid}
        plate_image = None

        with csv_lock:
            rows = read_all()
            row = find_row(rows, vid)
            if not row:
                entry["error"] = "This vehicle no longer exists."
            elif not row.get("plate_image"):
                entry["error"] = "No plate photo saved."
            else:
                plate_image = row["plate_image"]

        # The slow part (actual OCR) happens OUTSIDE csv_lock so normal
        # staff edits elsewhere in the app aren't blocked while it runs —
        # it's only ever serialized against other OCR calls, via
        # ocr_inference_lock inside _predict_plate().
        if plate_image is not None:
            try:
                plate_text = _predict_plate(ocr_module, plate_image)
            except Exception as exc:
                entry["error"] = str(exc)
            else:
                if not plate_text:
                    entry["error"] = "AI couldn't make out any text on this photo."
                else:
                    with csv_lock:
                        rows = read_all()
                        row = find_row(rows, vid)
                        if row is None:
                            entry["error"] = "This vehicle was deleted before the result could be saved."
                        else:
                            row["plate_text"] = plate_text
                            write_all(rows)
                            entry["plate"] = plate_text

        with ocr_jobs_lock:
            job = ocr_jobs.get(job_id)
            if job is None:
                return  # job entry was pruned/cancelled; stop quietly
            job["results"].append(entry)
            job["processed"] += 1

    with ocr_jobs_lock:
        job = ocr_jobs.get(job_id)
        if job is not None:
            job["done"] = True


def compute_ocr_eligible_ids(rows, vehicle_ids):
    """
    Vehicles worth sending to OCR: they exist, have a saved plate photo,
    and don't already have a plate number typed in (so a bulk run never
    overwrites a staff member's manual correction).
    """
    eligible = []
    for vid in vehicle_ids:
        row = find_row(rows, vid)
        if not row or not row.get("plate_image"):
            continue
        if row.get("plate_text", "").strip():
            continue
        eligible.append(vid)
    return eligible


@app.route("/staff/ocr_all/start", methods=["POST"])
@login_required(role="staff")
def staff_ocr_all_start():
    """
    Kicks off "Convert All Plates" as a background job and returns
    immediately with a job_id the browser polls for progress. Vehicles
    that already have a plate number typed in, or have no plate photo at
    all, are skipped automatically so a bulk run never overwrites a staff
    member's manual correction.
    """
    data = request.get_json(force=True)
    vehicle_ids = data.get("vehicle_ids", [])
    if not isinstance(vehicle_ids, list) or not vehicle_ids:
        return jsonify({"error": "No vehicles to process."}), 400

    rows = read_all()
    eligible = compute_ocr_eligible_ids(rows, vehicle_ids)
    if not eligible:
        return jsonify({
            "error": "Nothing to convert — every selected vehicle already has a "
                     "plate number entered, or has no plate photo saved."
        }), 400

    ocr_module, err = _load_ocr_module()
    if err:
        return jsonify({"error": err}), 503

    job_id = str(uuid.uuid4())
    with ocr_jobs_lock:
        # Opportunistic cleanup: keep at most the 5 most recent finished
        # jobs around so this dict doesn't grow forever on a long-lived
        # server process.
        finished = [jid for jid, j in ocr_jobs.items() if j["done"]]
        for jid in finished[:-5]:
            ocr_jobs.pop(jid, None)

        ocr_jobs[job_id] = {
            "total": len(eligible),
            "processed": 0,
            "done": False,
            "fatal_error": None,
            "results": [],
        }

    thread = threading.Thread(target=_run_ocr_batch_job, args=(job_id, eligible), daemon=True)
    thread.start()

    return jsonify({"job_id": job_id, "total": len(eligible)})


@app.route("/staff/ocr_all/status/<job_id>")
@login_required(role="staff")
def staff_ocr_all_status(job_id):
    with ocr_jobs_lock:
        job = ocr_jobs.get(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
        return jsonify({
            "total": job["total"],
            "processed": job["processed"],
            "done": job["done"],
            "fatal_error": job["fatal_error"],
            "results": list(job["results"]),
        })


@app.route("/staff/download")
@login_required(role="staff")
def staff_download():
    date_filter = request.args.get("date_filter", "all")
    types_param = request.args.get("types", "").strip()
    routes_param = request.args.get("routes", "").strip()
    sort_param = request.args.get("sort", "last_edited")

    selected_types = set(t for t in types_param.split(",") if t)
    selected_routes = set(r for r in routes_param.split(",") if r)

    rows = read_all()
    rows = filter_by_date(rows, date_filter)
    if selected_types:
        rows = [r for r in rows if r["vehicle_type"] in selected_types]
    if selected_routes:
        rows = [r for r in rows if r.get("route_type") in selected_routes]

    if sort_param == "plate":
        rows.sort(key=lambda r: (r.get("plate_text", "") or "").lower())
    else:
        rows.sort(key=lambda r: r.get("last_edited", ""), reverse=True)

    # Validate: every row must have both plate_text and odometer_text filled in
    missing = [r["id"] for r in rows if not r.get("plate_text", "").strip()
               or not r.get("odometer_text", "").strip()]
    if missing:
        return jsonify({
            "error": "Some vehicles are missing plate number or odometer reading text. "
                     "Please fill in every text field before downloading."
        }), 400

    wb = Workbook()
    ws = wb.active
    ws.title = "Vehicle Inspections"

    header_fill = PatternFill(start_color="C8102E", end_color="C8102E", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)

    max_tyres = max((len(json.loads(r.get("tyres_json") or "{}")) for r in rows), default=0)
    tyre_labels = []
    seen = set()
    for r in rows:
        tyres = json.loads(r.get("tyres_json") or "{}")
        for k in tyres:
            if k not in seen:
                seen.add(k)
                tyre_labels.append(k)

    headers = ["Vehicle Type", "Route Type", "Plate Number", "Odometer Reading (km)"] + \
        [f"Tyre {t} (mm)" for t in tyre_labels] + ["Entered By", "Last Edited"]

    ws.append(headers)
    for col in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")

    for r in rows:
        tyres = json.loads(r.get("tyres_json") or "{}")
        route_label = {"feeder": "Feeder", "trunk": "Trunk"}.get(r.get("route_type"), "—")
        row_data = [
            VEHICLE_CONFIG.get(r["vehicle_type"], {}).get("label", r["vehicle_type"]),
            route_label,
            r.get("plate_text", ""),
            r.get("odometer_text", ""),
        ]
        for t in tyre_labels:
            row_data.append(tyres.get(t, ""))
        row_data.append(r.get("added_by", ""))
        row_data.append(r.get("last_edited", ""))
        ws.append(row_data)

    for col_idx, header in enumerate(headers, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = max(18, len(header) + 4)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)

    type_tag = "-".join(sorted(selected_types)) if selected_types else "all"
    route_tag = "-".join(sorted(selected_routes)) if selected_routes else "all"
    filename = f"vehicle_inspections_{type_tag}_{route_tag}_{date_filter}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return send_file(
        buf,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/staff/clear_all", methods=["POST"])
@login_required(role="staff")
def staff_clear_all():
    data = request.get_json(force=True)
    password = data.get("password", "")

    users = load_users()
    current_user = users.get(session.get("username"))
    if not current_user or current_user["password"] != password:
        return jsonify({"error": "Incorrect password. Please try again."}), 401

    with csv_lock:
        write_all([])
    return jsonify({"success": True})


if __name__ == "__main__":
    ensure_csv()
    # ensure_users_csv()
    app.run(debug=True, host="0.0.0.0", port=5000, threaded=True)
