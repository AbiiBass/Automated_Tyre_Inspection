import os
import csv
import sys
import json
import uuid
import base64
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
    "added_by", "added_by_username", "last_edited"
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
        "tyre_count": 4,
        "rows": [
            {"row": "Front", "left": [_p("FL", "Front Left")],  "right": [_p("FR", "Front Right")]},
            {"row": "Back",  "left": [_p("BL", "Back Left")],   "right": [_p("BR", "Back Right")]},
        ],
    },
    "bus": {
        "label": "Bus",
        "tyre_count": 6,
        "rows": [
            {"row": "Front", "left": [_p("FL", "Front Left")], "right": [_p("FR", "Front Right")]},
            {"row": "Back",  "left": [_p("BL1", "Back Left 1"), _p("BL2", "Back Left 2")],
                              "right": [_p("BR1", "Back Right 1"), _p("BR2", "Back Right 2")]},
        ],
    },
    "doubledecker": {
        "label": "Double Decker Bus",
        "tyre_count": 10,
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
        "added_by": session.get("display_name", my_username),
        "added_by_username": my_username,
        "last_edited": now,
    }

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
    filter_type = request.args.get("type", "all")
    date_filter = request.args.get("date_filter", "all")
    all_rows = read_all()

    date_filtered_rows = filter_by_date(all_rows, date_filter)

    counts = {"all": len(date_filtered_rows)}
    for key in VEHICLE_CONFIG:
        counts[key] = len([r for r in date_filtered_rows if r["vehicle_type"] == key])

    rows = date_filtered_rows if filter_type == "all" else \
        [r for r in date_filtered_rows if r["vehicle_type"] == filter_type]
    rows.sort(key=lambda r: r.get("last_edited", ""), reverse=True)

    for r in rows:
        r["tyres"] = json.loads(r.get("tyres_json") or "{}")

    return render_template(
        "staff.html",
        vehicles=rows,
        vehicle_config=VEHICLE_CONFIG,
        filter_type=filter_type,
        date_filter=date_filter,
        counts=counts,
        total_all_time=len(all_rows),
        display_name=session.get("display_name"),
    )


@app.route("/staff/update_text/<vid>", methods=["POST"])
@login_required(role="staff")
def staff_update_text(vid):
    data = request.get_json(force=True)
    rows = read_all()
    row = find_row(rows, vid)
    if not row:
        return jsonify({"error": "Not found"}), 404
    if "plate_text" in data:
        row["plate_text"] = data["plate_text"]
    if "odometer_text" in data:
        row["odometer_text"] = data["odometer_text"]
    write_all(rows)
    return jsonify({"success": True})


@app.route("/staff/download")
@login_required(role="staff")
def staff_download():
    filter_type = request.args.get("type", "all")
    date_filter = request.args.get("date_filter", "all")
    rows = read_all()
    rows = filter_by_date(rows, date_filter)
    if filter_type != "all":
        rows = [r for r in rows if r["vehicle_type"] == filter_type]

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

    headers = ["Vehicle Type", "Plate Number", "Odometer Reading (km)"] + \
        [f"Tyre {t} (mm)" for t in tyre_labels] + ["Entered By", "Last Edited"]

    ws.append(headers)
    for col in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")

    for r in rows:
        tyres = json.loads(r.get("tyres_json") or "{}")
        row_data = [
            VEHICLE_CONFIG.get(r["vehicle_type"], {}).get("label", r["vehicle_type"]),
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

    filename = f"vehicle_inspections_{filter_type}_{date_filter}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
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

    write_all([])
    return jsonify({"success": True})


if __name__ == "__main__":
    ensure_csv()
    # ensure_users_csv()
    app.run(debug=True, host="0.0.0.0", port=5000)
