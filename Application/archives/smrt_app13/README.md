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

## Accounts

Accounts live in **`data/users.csv`**, not in the Python code. Columns:
`username,password,role,name`. This file is **not auto-created** — bring
your own `data/users.csv` (or copy the example row format below into one)
before starting the server. Edit the file directly to add/remove/change
accounts, or use the in-app **Manage Employees** page — changes take
effect on the next login attempt, no restart needed.

Example row format (for reference — not shipped as a starter file):

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
- **"Convert All Plates"**: a toolbar button that runs AI plate-reading for
  every currently-visible vehicle that doesn't already have a plate number
  typed in, in one go. It runs as a background job on the server (with a
  progress bar you can dismiss and it keeps going, or check back on later)
  and never overwrites a plate number a staff member already entered. See
  the dedicated section below for how it's built and why.

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

## "Convert All Plates" — how it's built, and why

**The design question:** batch-converting many plate photos means either
running OCR predictions one after another, or trying to parallelize them.
Here's the reasoning behind the choice made:

- **Not multiprocessing.** Loading the PaddleOCR models (what
  `license_plate.py` does once at import time) is the expensive part —
  several seconds. Every separate *process* would need to reload that
  model independently, so spinning up worker processes would likely make a
  batch *slower* overall for the batch sizes this app deals with (tens of
  vehicles), not faster — you'd be paying the multi-second model-load cost
  repeatedly instead of once.
- **Not multiple threads calling the model concurrently, either.**
  PaddleOCR's pipeline object isn't documented as safe for concurrent
  `predict()` calls from multiple threads at once. Rather than gamble on
  that, `app.py` has a single `ocr_inference_lock` that every OCR call
  goes through — the individual "Ask AI" button and the batch job both
  funnel through it, so **only one OCR prediction ever runs at a time**,
  process-wide. This was verified under real concurrent load: firing the
  individual button from several threads while a batch job was mid-flight
  never showed more than one prediction in flight at once.
- **What actually happens:** "Convert All Plates" starts a single
  background thread (`threading.Thread`) that works through the eligible
  vehicles one at a time, reusing the same already-loaded model instance
  and persisting each result to the CSV as soon as it's read — not all at
  the end, so progress survives even if something goes wrong partway
  through. The browser polls `/staff/ocr_all/status/<job_id>` for progress
  and reflects each result live without re-saving it (the background
  thread already persisted it).
- **A separate `csv_lock`** now guards every read-modify-write cycle
  against `data/vehicles.csv`, since this background thread is the first
  thing in the app that genuinely runs concurrently with normal request
  handling — without it, a staff member editing a field at the exact
  moment the batch job saves a result could silently lose one of the two
  changes.

**Limitation to know about:** the job list and the loaded OCR model both
live in memory in the single running Python process. This works great for
the dev server (even with `threaded=True`, which is now set). If this is
ever deployed behind a production server with multiple *worker processes*
(e.g. `gunicorn -w 4`), a status-poll request could land on a different
worker than the one running the job and report "not found" — that would
need a shared store (Redis, a database row, etc.) instead of the in-memory
dict used here. For a single-process deployment, or the dev server, it
works as-is.

## Manage Employees & My Profile (new staff pages)

A nav bar now sits under the top bar on every staff page — **Dashboard**,
**Manage Employees**, and **My Profile** — built to have more tabs added
later without restructuring anything.

**Manage Employees** (`/staff/employees`) lists every account (full name,
username, position) — passwords are never sent to this page or shown
anywhere. Staff can:
- **Add** an employee (full name, username, Technician/SMRT Staff) — new
  accounts always start with the password `1234`.
- **Edit** another employee's name, username, or position.
- **Delete** another employee (blocked if it would remove the very last
  SMRT Staff account, so nobody can lock everyone out by accident).

You can't edit or delete your *own* account from this page — that row shows
"Edit in My Profile" instead. That's a deliberate boundary: it keeps this
page purely about managing *other* people's accounts, and avoids a subtle
bug where renaming yourself here would silently break your own logged-in
session mid-request.

**My Profile** (`/staff/profile`) is where you change your *own* username
and/or password. Leave the password fields blank to keep your current
password. Submitting shows a warning that this will log you out
immediately — confirming actually does log you out server-side
(`session.clear()`) right after saving, so you sign back in with whichever
of the two you changed.

Both pages reuse the existing `data/users.csv` file and the same
`users_lock` pattern as the rest of the app — every add/edit/delete/profile
change is a locked read-modify-write cycle so it can't collide with another
request touching the same file.

## Odometer AI reading (new: mirrors the plate feature exactly)

`odometer.py` — the file you provided, kept completely separate from
`app.py` just like `license_plate.py` — is now wired up the same way:

- An **Ask AI** button sits next to the odometer text box on every card,
  right beside the existing plate one.
- A **Convert All Odometers** button sits next to **Convert All Plates**
  in the toolbar, with its own confirmation dialog and progress bar. It
  only touches vehicles that have an odometer photo and don't already
  have a reading typed in — exactly the same skip-what's-already-filled
  rule as the plate version.
- Under the hood, `app.py`'s OCR routes were generalized to take a `task`
  ("plate" or "odometer") instead of duplicating the whole pipeline —
  same single background thread, same `csv_lock`, same
  `ocr_inference_lock`. That lock now serializes plate *and* odometer
  predictions together, not just within each type: firing an individual
  "Ask AI" click and a bulk odometer job at the same instant still never
  runs two OCR predictions at once, verified under real concurrent load
  during testing.
- `/staff/ocr_plate/<id>` and the old plate-only behavior of
  `/staff/ocr_all/start` (when no `task` is sent) are unchanged, so
  nothing that already worked had to change to make room for this.

## Your storage/efficiency question, answered directly

**What actually gets installed, and does it double up?**

`pip install -r requirements-ocr.txt` installs the Python packages
(`paddleocr`, `paddlepaddle`, `opencv-python`, `numpy`) once — that part
definitely doesn't duplicate, `pip` just has one copy of each package
regardless of how many scripts import it.

The bigger question is the **model weight files** PaddleOCR downloads the
first time it's actually used. Both `license_plate.py` and `odometer.py`
ask for the exact same two model names (`PP-OCRv5_mobile_det` and
`en_PP-OCRv5_mobile_rec` — the small "mobile" variants, not the larger
"server" ones). PaddleX (which PaddleOCR sits on top of) caches downloaded
models in one shared local directory, keyed by model name. So:

- Whichever of the two scripts runs **first** triggers the one-time
  download onto disk.
- The **second** script to run — even though it's a separate Python
  `import`, in the same process or not — asks for the *same* model names
  and finds them already sitting in that shared cache, so it loads from
  disk instead of downloading again. **Disk usage is shared, not
  doubled.**

**Where it doesn't share: memory.** Each file's top-level
`ocr = PaddleOCR(...)` line creates its *own* Python object the moment
that file is imported. Since our Flask app can end up importing *both*
`license_plate` and `odometer` in the same long-running process (once
each feature has been used once), you end up with **two separate loaded
model instances in RAM** at the same time, even though they're built from
identical weight files on disk. For these small "mobile" models this is a
modest, not dramatic, cost — but it is real, and it's paid once per
process lifetime (each instance loads once, then just sits in memory
answering predict() calls).

**Why I didn't force these two into sharing one instance:** I checked, and
their `PaddleOCR(...)` configs aren't quite identical —
`odometer.py` passes `cpu_threads=CPU_THREADS` explicitly, while
`license_plate.py` doesn't set that parameter at all (it only relies on
the `OMP_NUM_THREADS`-style environment variables). If I silently merged
them into one shared instance, whichever script's config "wins" (created
first) would quietly override the other's setting — and I don't have a
reliable way to prove that's behaviorally identical for models you've
clearly tuned carefully. I didn't want to risk changing either script's
detection behavior without you explicitly asking for that trade-off.

**If you do want the memory deduplicated**, the fix is small and doesn't
require touching either file's detection logic: a tiny third module (e.g.
`ocr_shared.py`) that both scripts check on startup — "has anyone already
built a PaddleOCR instance? If so, hand me that one; if not, I'll build it
myself" — with a clear decision up front about which config wins (or
unifying both scripts to request the same `cpu_threads` value explicitly).
That would take both scripts down to one shared model instance in memory,
at the cost of both being forced onto identical thread-count settings.
Happy to build that if you'd like it — just wanted to flag the trade-off
rather than make that call for you.

## Branding & account menu (latest update)

- The real SMRT logo (`static/img/smrt-logo.png`) now appears on the login
  page and in the top bar of every page (technician, staff dashboard,
  Manage Employees, My Profile), replacing the earlier placeholder mark.
- **Log Out** is no longer its own button — click your name in the top
  right to open a small dropdown with the Log Out option, on every page.
- `data/users.csv` is no longer auto-created on startup — bring your own
  file (see "Accounts" above).

## Staff dashboard: four more additions

- **Scroll-to-top button**: a floating arrow button appears bottom-right
  once you've scrolled down 300px, and smooth-scrolls back to the top of
  the list when clicked.
- **Split downloads by vehicle type**: when no vehicle-type pill is
  selected (i.e. "All" types), **Download Spreadsheet** now returns a
  `.zip` containing three separate workbooks — `car.xlsx`, `bus.xlsx`,
  `doubledecker.xlsx` — instead of one mixed file. Pick a specific type
  pill and it goes back to a single `.xlsx` as before. The existing
  "every row needs plate + odometer text" rule still blocks the whole
  export if anything in range is incomplete, and date/route filters and
  sort order are still respected inside each split file.
- **Delete a specific selection of rows**: each card now has a checkbox in
  its header. Checking any card reveals a **Delete Selected (N)** button
  next to **Clear All Data** — a lighter-weight option for removing just a
  handful of rows without wiping everything.
- **Select Range date filter**: alongside All Dates / Today / This Week,
  a new **Select Range** pill opens a small popover with two date pickers
  (From / To). Applying it reloads the dashboard showing only inspections
  whose last-edited date falls within that range (inclusive), and the
  spreadsheet download respects the same range.

## Also in this update

- **SMRT logo**: the placeholder red-circle/gold-border mark on the login
  page and in every top bar is now the real SMRT logo image
  (`static/img/smrt-logo.png`).
- **Logout moved into a name dropdown**: clicking your name in the top bar
  (any logged-in page) now opens a small menu with **Log Out** in it,
  instead of a separate always-visible Log Out button.
- **`ensure_users_csv()` removed**: `data/users.csv` is no longer
  auto-created/seeded by the app at startup — it's now a real file you
  manage directly (the Manage Employees page still reads/writes it the
  same way). Because of this, **`data/users.csv` must exist** before the
  app will start handling logins; the demo accounts are shipped in this
  package's copy of that file. If you ever delete it, recreate it with at
  least the `username,password,role,name` header row and one account.

## Two refinements to the dashboard's danger zone

- **Clear All Data now respects your filters.** It used to wipe the whole
  database regardless of what was on screen. Now it only deletes whatever
  currently matches your active date/route/vehicle-type filters — the
  confirmation dialog shows exactly how many vehicles that is before you
  type your password. Leave every filter at its default ("All") and it
  behaves like before (clears everything); narrow it down first and only
  that subset is removed.
- **Row-selection checkboxes are now opt-in, not always-on.** Right-click
  (or long-press-equivalent on touch) a card's header and choose **To
  Delete** from the small menu that appears — that reveals a checkbox on
  every card (and pre-checks the one you right-clicked), after which you
  can tick others and use **Delete Selected (N)** as before. A **Cancel
  Selection** link next to it exits selection mode and unchecks everything
  without deleting anything.

## New page: Fleet Overview (historical trends, not just current state)

The live Dashboard only ever holds *current* state — one row per vehicle,
overwritten every time it's edited, with no record of what it used to be.
**Fleet Overview** (a new tab) fills that gap by building a history out of
the spreadsheets staff already produce with **Download Spreadsheet**.

**How it works:**
- Upload one or more `.xlsx` files (or the `.zip` the split-download option
  produces) via the upload card at the top. Each file is parsed using the
  exact column layout this app's own exports use — "Vehicle Type",
  "Route Type", "Plate Number", "Odometer Reading (km)", any
  "Tyre _code_ (mm)" columns, "Entered By", "Last Edited" — so anything
  downloaded from this app can be re-uploaded here.
- Every row becomes one historical **snapshot** keyed by plate + vehicle
  type + date, stored in `data/fleet_history.csv` (completely separate
  from the live `data/vehicles.csv`). Uploading the same file twice is
  safe — duplicate snapshots (same plate + type + date) are silently
  skipped, so re-uploading an export you already added just does nothing.
- Three tables (Car / Bus / Double Decker Bus) show one row per plate —
  whichever is that plate's *most recent* uploaded snapshot — with just
  the plate number, route (Bus/Double Decker only), and latest-updated
  date, per your spec.
- **Click any row** to open a detail view: the latest odometer reading and
  tyre tread depths (tyres over 3.2mm flagged red, matching the Dashboard's
  convention), plus two trend charts — odometer over time, and tyre tread
  depth over time, one line per tyre position — built from every snapshot
  ever uploaded for that plate, via [Chart.js](https://www.chartjs.org/)
  (loaded from its CDN, the same pattern as Font Awesome elsewhere in this
  app).
- A **Clear Fleet History** button (password-protected, same pattern as
  Clear All Data) wipes the uploaded history without touching the live
  Dashboard data at all.

This was tested end-to-end: uploading a real export from this app, the
dedup logic on re-upload, editing a vehicle and uploading a second
snapshot to confirm the chart actually shows two distinct points in the
right order, the split `.zip` path, and the detail view's math (correctly
distinguishing "latest" from "history").

## Fixed: "Chart is not defined" on Fleet Overview

Fleet Overview's detail modal was pinned to Chart.js version `4.4.4` via
cdnjs — but that exact version was never published there (cdnjs lagged
behind npm/jsDelivr for that release; see
[chartjs/Chart.js#11892](https://github.com/chartjs/Chart.js/issues/11892)).
That made the CDN `<script>` tag 404 silently, so `Chart` was never
defined, and clicking any vehicle row threw `Chart is not defined` the
moment the detail modal tried to draw its charts.

Fixed by pinning to `4.4.1` instead — a version confirmed to actually
exist on cdnjs. As a safety net for the future, `fleet_overview.js` now
also checks `typeof Chart === 'undefined'` before using it: if the CDN
ever fails to load for any reason (offline, a blocked domain, a future
CDN hiccup), the detail modal still opens and still shows the latest
odometer/tyre readings — it just shows a plain-language message in place
of the charts instead of crashing.
