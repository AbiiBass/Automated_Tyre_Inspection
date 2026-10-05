SMRT Vehicle Inspection — Browser-Only Draft (no install needed)
====================================================================

WHAT'S DIFFERENT FROM THE OTHER VERSION
------------------------------------------
This version needs nothing installed at all — open it with VS Code's Live
Server (or any static file server, or even just double-clicking
index.html) and it works.

The trade-off: a browser cannot write to a file on your disk by itself,
so this version keeps every inspection record in the browser's own
storage (localStorage) instead of continuously writing to vehicles.csv.
Use the export button (top-right, once signed in — the download-arrow
icon next to sign-out) any time you want an actual vehicles.csv file on
disk, e.g. to hand to whoever is currently doing this by hand in Excel.

Because of this, records only exist in the one browser/device you used to
create them, and will be lost if that browser's site data is cleared. For
a real pilot with multiple staff/devices, you'd want the Node.js version
(or a proper backend) instead — happy to help set that up when you're ready.

HOW TO RUN IT
--------------
Exactly how you've been running it:
1. Open this folder in VS Code.
2. Right-click index.html → "Open with Live Server" (or just open
   index.html directly in a browser).
3. Sign in with SM1001, SM1002, or SM1003 (from users.csv).

Signing in reads users.csv (a normal file fetch — no backend involved, so
this part works the same as before). Add a row to users.csv to let
someone new sign in.

HOW EXPORT WORKS
------------------
Tap the export icon in the header. It builds a CSV from everything
currently stored in this browser and downloads it as
vehicles_export_YYYY-MM-DD.csv to your Downloads folder. Re-running
export later downloads a fresh snapshot — it does not overwrite the
previous one automatically (browsers can't silently overwrite arbitrary
files on disk).

The exported CSV embeds each plate/odometer photo directly as a compressed
Base64 image string in its own column, so the file is fully self-contained
per row. Photos are automatically resized/compressed when captured (max
640px, ~60% quality) precisely so this stays practical — note that very
detailed photos may get visually truncated if you open the CSV in Excel,
since Excel caps how much text a single cell displays (the underlying data
in the file itself is not affected).

FILES
------
index.html   All screens (login, list, add flow, camera, detail view).
style.css    Styling — same look as the Node.js version.
app.js       All logic. Data lives in localStorage under the key
              "smrt_vehicles_v1". Everything else (camera capture with the
              alignment guide, 24-hour edit lock, confirm-to-submit dialog)
              works exactly the same as the other version.
users.csv    Valid staff IDs who can sign in. Read directly by the browser
              via fetch() — no backend needed for this part.

IF YOU LATER WANT DATA CONTINUOUSLY WRITTEN TO A REAL vehicles.csv
----------------------------------------------------------------------
That does require Node.js (or a similar small local server) running
alongside the page, since browsers can't write to disk by themselves.
I have a version already built that does exactly that — just ask and I'll
share it again.
