# SMRT Fleet Check — Draft Web App

A phone-first, no-build (plain HTML/CSS/JS) prototype for vehicle inspections.
No Node.js, no framework, no server code — just open it with a static file
server like VS Code's **Live Server**.

## Files
- `index.html` — all screens/modals
- `style.css` — all styling (mobile "phone frame" layout)
- `app.js` — all logic
- `employees.csv` — sample staff IDs used for the ID-only sign-in
- `vehicles.csv` — the inspection records "database"

## Running it
1. Open the `smrt-inspect` folder in VS Code.
2. Right-click `index.html` → **Open with Live Server**.
3. Sign in with a sample ID: `E1001`, `E1002`, `E1003`, `E1004`, or `E1005`.
4. If you want it on your phone too: keep Live Server running and either
   open your PC's local IP on your phone (same Wi-Fi), or run `ngrok http 5500`
   (or whatever port Live Server shows) to get a temporary public HTTPS URL —
   the camera capture requires HTTPS (or `localhost`) on real devices.

## ⚠️ Important: how the CSV storage actually works
Browsers are not allowed to silently read/write files on your computer — a
webpage can't just "save to vehicles.csv" on its own for security reasons.
So this draft handles it two ways, and you get both:

1. **"Connect vehicles.csv" (Chrome / Edge, desktop & Android only)** — on
   the sign-in screen, tap this and pick `vehicles.csv` from the project
   folder. The browser will ask for permission, and from then on every
   add/edit/delete writes straight back into that real CSV file. This is
   the closest thing to a "real database" you can get without a backend.
   *(Not supported in Safari/iOS — Apple hasn't implemented this API.)*
2. **Export / Import CSV buttons (header of the main screen)** — works
   everywhere. Export downloads the current data as `vehicles.csv`; Import
   loads a CSV back in (replacing what's currently loaded). Use this as your
   fallback on iPhones or if you skip the Connect step.

Either way, while you're using the app your data also lives in the browser's
`localStorage`, so refreshing the page won't lose anything mid-demo.

**For a real production version**, you'd want a small backend (even a
lightweight one) or a proper database — writing to a shared CSV file from
multiple phones at once will eventually cause conflicts (last save wins).
This draft is intentionally backend-free just to show the concept fast.

## What's implemented (matches your spec)
- Sign in by staff ID only, checked against `employees.csv`
- Single-page card list, no side/menu nav, floating **+** add button
- Add flow: choose Car / Single Bus / Double Decker (icon picker) →
  one-page form (no multi-step wizard)
- Plate photo capture with a **wide** alignment guide (matches a front
  plate's wide aspect ratio); odometer photo capture with a **tall**
  alignment guide — both guides are on-screen only and are never part of
  the saved photo, since they're a separate overlay from the camera frame
- Tyre tread depth fields: 4 for cars, 6 for single-bus, 8 for double-decker
- "Confirm to submit?" modal with Cancel, before anything is saved
- Card list shows: plate photo thumbnail, a colored type icon
  (car = pink, single bus = green, double decker = white with outline),
  and submitted/last-modified date & time — readings/odometer are hidden
  until you tap into a card
- Tap a card for full details, including both photos and all tyre readings
- Editing is allowed only within 24 hours of the original submission;
  after that the Edit button is hidden and a lock notice is shown instead
- Delete with its own confirmation
