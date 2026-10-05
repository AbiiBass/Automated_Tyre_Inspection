# SMRT Fleet Inspection System

A Flask web app for recording and reviewing vehicle tyre-tread, odometer, and
plate-number inspections. Two separate login groups share one CSV "database":

- **Technicians** (phone-first, large-tap UI) — add / view / edit / delete
  inspections. They take photos of the plate and odometer instead of typing.
- **SMRT Staff** (desktop dashboard) — review inspections by vehicle type,
  transcribe the plate/odometer photos into text, and export everything to
  an Excel spreadsheet.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate         # Windows: venv\Scripts\activate
pip install -r requirements.txt
python3 app.py
```

Then open **http://localhost:5000** in a browser (or on a phone on the same
network, using your computer's local IP, e.g. `http://192.168.1.20:5000`,
so the camera can be used).

> Camera capture requires HTTPS or `localhost` in most mobile browsers. For
> real-device testing over your LAN, either use a tool like `ngrok`, or set
> up a local HTTPS certificate.

## Demo accounts

Accounts live in **`data/users.csv`** (auto-created on first run if missing),
not in the Python code. Columns: `username,password,role,name`. Edit that
file directly to add/remove/change accounts — changes take effect on the
next login attempt, no restart needed.

| Role       | Username | Password  |
|------------|----------|-----------|
| Technician | tech1    | tech123   |
| Technician | tech2    | tech123   |
| SMRT Staff | staff1   | staff123  |
| SMRT Staff | staff2   | staff123  |

Passwords are stored in plain text in the CSV for this demo — replace with
hashed passwords (e.g. `werkzeug.security.generate_password_hash`) and a
real database before deploying anywhere reachable by the public.

## How it works

- **Storage**: `data/vehicles.csv` is the shared database. Each row holds the
  vehicle type, the plate photo and odometer photo (as base64 data URLs),
  the staff-transcribed plate/odometer text, a JSON blob of tyre readings,
  the name of the technician who logged the inspection, and a `last_edited`
  timestamp. `data/users.csv` holds login accounts (see above).
- **Technician flow** (`/technician`): a single page lists all inspections as
  cards (icon, last-edited time, plate photo thumbnail) with a 3-dot menu for
  edit/delete, and a red "Add Vehicle" button opens a step-by-step wizard:
  1. Pick vehicle type (car / bus / double-decker bus — each with its own
     tyre count and layout).
  2. Take a plate photo (guided by an on-screen gold rectangle; the photo is
     cropped to that rectangle before saving).
  3. Take a 4:3 odometer photo.
  4. Enter tyre tread depths (mm, 2dp) next to a live diagram that highlights
     the tyre matching the focused input field.
  5. Tap **Complete** — `last_edited` is stamped automatically server-side.
- **Staff flow** (`/staff`): filter by Car / Bus / Double Decker Bus / All,
  see each inspection's photos with a text box underneath to type what the
  photo shows, the name of the technician who entered it, and tyre readings
  in a small table. **Download Spreadsheet** exports the currently filtered
  view to `.xlsx` — the server blocks the download (and the page highlights
  the offending fields) if any plate or odometer text box is still empty.

## Notes / things to harden for production

- Replace the plain-text `data/users.csv` passwords with hashes (e.g.
  `werkzeug.security.generate_password_hash`) and a real user table.
- Move from CSV to a real database (SQLite/Postgres) once concurrent writes
  become a concern — CSV has no locking.
- Add HTTPS termination so the camera API works on real phones off-network.
- Consider compressing/resizing photos client-side further if CSV size
  becomes a concern (currently JPEG quality 0.88, cropped to the guide box).

## Fixed in this update

- **`_csv.Error: field larger than field limit (131072)`** — Python's CSV
  reader defaults to a ~128KB per-field limit, which the base64-encoded
  photos can exceed. `app.py` now raises this limit at startup
  (`csv.field_size_limit(...)`), so large images no longer crash the app on
  the next read after being saved.
- If you were seeing an `ngrok` "endpoint offline" error (`ERR_NGROK_3004`),
  that happens when the local Flask server behind the tunnel isn't running —
  which is exactly what the CSV crash above caused. Restart `python3 app.py`
  and your `ngrok http 5000` tunnel after pulling this update and it should
  stay up through large-image saves.

## What's new in this update

- **Tutorial mode**: both the technician page and the staff dashboard have a
  gold "Tutorial" button in the top bar. It opens a step-by-step walkthrough
  (Back / Next, a progress dial, and a "Close Tutorial" link available on
  every step) explaining each feature in order.
- **Collapsible staff cards**: inspection cards on the staff dashboard now
  load collapsed by default so long lists stay easy to scan. Click a card's
  header to expand or collapse just that one, or use the "Expand All" /
  "Collapse All" button in the toolbar to toggle every card at once. If a
  spreadsheet download is blocked because a text field is empty, the
  relevant card(s) auto-expand so it's obvious what still needs filling in.
- **Formal icons**: the login page (and the app's other icon-only controls —
  logout, the 3-dot menu, download, expand/collapse) now use Font Awesome
  icons loaded from `cdnjs.cloudflare.com` instead of emoji, including a
  proper eye / eye-slash toggle for the password field. The larger vehicle
  pictograms (🚗 🚌 🚎) on the technician and staff pages are left as emoji
  on purpose — they're deliberately large, colourful, and easy to recognise
  for technicians who may not read text quickly.
- **Technician view is "today only"**: the technician home page now only
  lists inspections whose `last_edited` timestamp falls on the current
  calendar date — older entries simply don't show up there. They're not
  deleted; SMRT Staff can still see and export them at any time.
- **Staff date filters**: below the vehicle-type pills, staff can filter by
  **All Dates**, **Today**, or **This Week** (Monday through now). This
  combines with the vehicle-type filter, and the spreadsheet download
  respects both.
- **Clear All Data**: staff can wipe the entire database from the dashboard.
  It's guarded by two steps: (1) a warning dialog explaining the deletion is
  permanent and recommending a spreadsheet backup first, then (2) a password
  re-entry prompt that must match the logged-in staff account before
  anything is actually deleted (`POST /staff/clear_all`).
