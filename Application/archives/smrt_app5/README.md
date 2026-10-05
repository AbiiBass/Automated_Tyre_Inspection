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
- **Technicians only see their own inspections**: each vehicle record now
  tracks which technician account created it (`added_by_username`). A
  technician's home page — and the edit/view/delete endpoints — are scoped
  to that field, so logging in as `tech1` never shows, or allows editing/
  deleting, anything `tech2` added, and vice versa (enforced server-side,
  not just hidden in the UI). SMRT Staff still see every inspection from
  every technician, with the technician's name shown on each card.
- **Staff date filters**: below the vehicle-type pills, staff can filter by
  **All Dates**, **Today**, or **This Week** (Monday through now). This
  combines with the vehicle-type filter, and the spreadsheet download
  respects both.
- **Clear All Data**: staff can wipe the entire database from the dashboard.
  It's guarded by two steps: (1) a warning dialog explaining the deletion is
  permanent and recommending a spreadsheet backup first, then (2) a password
  re-entry prompt that must match the logged-in staff account before
  anything is actually deleted (`POST /staff/clear_all`).
- **One login page, no role picker**: technicians and staff sign in with
  the same User ID + password form. The role is looked up from
  `data/users.csv` automatically — there's nothing to select.
- **Tutorial mode is now a real guided tour**: instead of a generic
  slideshow, each step spotlights the actual button, filter, or panel it's
  describing — with a dimmed backdrop, a glowing cutout around the real
  element, and a pointer callout next to it. Where possible it also *runs
  the real feature live* while explaining it: it opens the real Add Vehicle
  wizard and walks through it, cycles the tyre diagram's highlight the same
  way tapping a field does, pops open a real card's 3-dot menu, and
  expands/collapses real cards on the staff dashboard — then cleans
  everything back up when the tour ends or is closed early. The two camera
  steps (which can't be safely auto-triggered) show a small looping
  animation instead, illustrating the plate/odometer sliding into the guide
  rectangle and the shutter flashing.
- **Real vehicle icons**: car/bus/double-decker icons throughout the app are
  the illustrated PNGs in `static/img/` instead of emoji.
- **Bus/Double Decker route type**: after the odometer photo, technicians
  are asked whether the trip is a **Feeder** or **Trunk** route via a real
  toggle switch (default: Feeder). Cars skip this question. Staff can also
  correct it later from the expanded card view.
- **Technicians only see their own inspections**: each vehicle record now
  tracks which technician account created it (`added_by_username`). A
  technician's home page — and the edit/view/delete endpoints — are scoped
  to that field, so logging in as `tech1` never shows, or allows editing/
  deleting, anything `tech2` added, and vice versa (enforced server-side).
  SMRT Staff still see every inspection from every technician.
- **Staff cards are one wide row each**, sized to fill a laptop screen, and
  show plate number, odometer reading, route badge, and every tyre reading
  right on the collapsed header — no need to expand just to see the basics.
  Typing a new plate/odometer value updates the header instantly.
- **Relative or exact time**: a toggle in the top bar (both pages) switches
  every "last edited" timestamp between "3 hours ago" style relative time
  and the exact date/time. The choice is remembered in the browser.
- **Multi-select filters + Feeder/Trunk filter pills**: staff can combine
  multiple vehicle-type pills (e.g. Bus + Double Decker) and combine those
  with the new Feeder/Trunk route pills — filtering is AND between the two
  groups, OR within each group. Selecting Car together with a route filter
  pops up a heads-up explaining cars don't have a route type. A sort
  dropdown reorders the list by newest-first or by license plate, and the
  download respects whatever combination of filters/sort is active.
- **"Ask AI" plate reading**: next to the license plate text box on the
  staff dashboard, an **Ask AI** button sends that vehicle's saved plate
  photo to `license_plate.py` (a separate, standalone OCR module — see
  below) and fills the text box with its best guess. Staff can still edit
  the result before it's saved; it also auto-saves via the normal
  plate-text flow. If the OCR photo can't be read, or the optional OCR
  packages aren't installed on the server, it shows a clear error instead
  of guessing.

## AI plate reading ("Ask AI" button)

`license_plate.py` is a standalone module — `app.py` never imports it at
startup, only inside the `/staff/ocr_plate/<id>` route the first time
someone clicks **Ask AI**, and only if it's actually needed. That keeps the
rest of the app fully usable even if these (fairly heavy) OCR packages
aren't installed; the button will just show a friendly "not installed"
error instead of crashing anything.

To enable it:

```bash
pip install -r requirements-ocr.txt
```

This pulls in `paddleocr`, `paddlepaddle`, `opencv-python`, and `numpy`.
The first click after installing will be slow (PaddleOCR downloads and
loads its models); every click after that reuses the already-loaded model
in memory, so it's fast.

The button only appears when a vehicle has a saved plate photo. It reads
that saved photo — not a fresh camera capture — so it works for any
existing inspection, not just newly-added ones.
