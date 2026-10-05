/* =======================================================
   SMRT Fleet Check — app.js
   Plain HTML/CSS/JS, no build step, no backend.

   DATA STORAGE NOTES (read this first):
   - Browsers cannot silently read/write arbitrary files on disk.
   - This demo keeps a live working copy of the data in
     localStorage (so add/edit/delete feel instant and survive
     a page refresh), and mirrors it to vehicles.csv in one of
     two ways:
       1) BEST: "Connect vehicles.csv" uses the File System
          Access API (Chrome / Edge on desktop & Android) to
          open the real CSV file next to index.html and write
          straight back to it after every change.
       2) FALLBACK (Safari / iOS / older browsers): use the
          Export CSV / Import CSV buttons in the header to move
          data in and out of vehicles.csv by hand.
   - On first run (empty localStorage) the app seeds itself by
     fetching vehicles.csv from the same folder.
======================================================= */

const VEHICLE_COLUMNS = ["id","submittedBy","type","plateImage","odometerImage","tyreDepths","submittedAt","lastModifiedAt"];
const TYRE_COUNTS = { "car": 4, "single-bus": 6, "double-decker": 8 };
const TYPE_META = {
  "car":            { label: "Car",            icon: "🚗", color: "#FF6FA4" },
  "single-bus":     { label: "Single Bus",      icon: "🚌", color: "#3CB878" },
  "double-decker":  { label: "Double Decker",   icon: "🚍", color: "#FFFFFF", border: true },
};
const EDIT_WINDOW_MS = 24 * 60 * 60 * 1000; // 24 hours

// ---------------- Global state ----------------
let employees = [];
let vehicles = [];
let currentUser = null;
let csvFileHandle = null;

let pendingInspection = null;   // record currently being built/edited in the form
let editingId = null;           // set when editing an existing record
let cameraStream = null;
let cameraTarget = null;        // 'plate' | 'odometer'
let lastCapturedDataUrl = null;

// ---------------- Element shortcuts ----------------
const $ = (id) => document.getElementById(id);

// =========================================================
// CSV helpers (small hand-rolled parser/writer — data here
// never contains newlines inside fields, and base64 image
// strings never contain commas or quotes, so this stays simple)
// =========================================================
function parseCSV(text) {
  const lines = text.replace(/\r\n/g, "\n").split("\n").filter(l => l.length > 0);
  if (lines.length === 0) return [];
  const headers = splitCSVLine(lines[0]);
  return lines.slice(1).map(line => {
    const values = splitCSVLine(line);
    const row = {};
    headers.forEach((h, i) => row[h] = values[i] !== undefined ? values[i] : "");
    return row;
  });
}

function splitCSVLine(line) {
  const result = [];
  let cur = "";
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (inQuotes) {
      if (c === '"' && line[i + 1] === '"') { cur += '"'; i++; }
      else if (c === '"') { inQuotes = false; }
      else { cur += c; }
    } else {
      if (c === '"') inQuotes = true;
      else if (c === ',') { result.push(cur); cur = ""; }
      else cur += c;
    }
  }
  result.push(cur);
  return result;
}

function csvEscape(value) {
  const s = (value === undefined || value === null) ? "" : String(value);
  if (s.includes(",") || s.includes('"') || s.includes("\n")) {
    return '"' + s.replace(/"/g, '""') + '"';
  }
  return s;
}

function vehiclesToCSV(rows) {
  const lines = [VEHICLE_COLUMNS.join(",")];
  rows.forEach(r => {
    lines.push(VEHICLE_COLUMNS.map(col => csvEscape(r[col])).join(","));
  });
  return lines.join("\n") + "\n";
}

function csvRowToVehicle(row) {
  return {
    id: row.id,
    submittedBy: row.submittedBy,
    type: row.type,
    plateImage: row.plateImage || "",
    odometerImage: row.odometerImage || "",
    tyreDepths: row.tyreDepths ? row.tyreDepths.split("|") : [],
    submittedAt: row.submittedAt,
    lastModifiedAt: row.lastModifiedAt,
  };
}

// =========================================================
// Data loading / persistence
// =========================================================
async function loadEmployees() {
  try {
    const res = await fetch("employees.csv");
    const text = await res.text();
    employees = parseCSV(text);
  } catch (e) {
    console.error("Could not load employees.csv", e);
    employees = [];
  }
}

async function loadVehicles() {
  const cached = localStorage.getItem("smrt_vehicles");
  if (cached) {
    vehicles = JSON.parse(cached);
    return;
  }
  try {
    const res = await fetch("vehicles.csv");
    const text = await res.text();
    vehicles = parseCSV(text).map(csvRowToVehicle);
  } catch (e) {
    console.error("Could not load vehicles.csv", e);
    vehicles = [];
  }
  persistVehicles();
}

function persistVehicles() {
  localStorage.setItem("smrt_vehicles", JSON.stringify(vehicles));
  writeToConnectedFile();
}

async function writeToConnectedFile() {
  if (!csvFileHandle) return;
  try {
    const writable = await csvFileHandle.createWritable();
    await writable.write(vehiclesToCSV(vehicles));
    await writable.close();
  } catch (e) {
    console.error("Could not write to connected CSV file", e);
    showToast("⚠️ Could not save to vehicles.csv — using browser storage only");
  }
}

async function connectCsvFile() {
  if (!("showOpenFilePicker" in window)) {
    showToast("This browser doesn't support direct file saving — use Export/Import instead");
    return;
  }
  try {
    const [handle] = await window.showOpenFilePicker({
      types: [{ description: "CSV file", accept: { "text/csv": [".csv"] } }],
    });
    const perm = await handle.requestPermission({ mode: "readwrite" });
    if (perm !== "granted") { showToast("Permission denied"); return; }
    csvFileHandle = handle;
    const file = await handle.getFile();
    const text = await file.text();
    if (text.trim().length > 0) {
      vehicles = parseCSV(text).map(csvRowToVehicle);
      persistVehicles();
    } else {
      await writeToConnectedFile();
    }
    $("connect-status").textContent = "✅ Connected to " + handle.name + " — changes save automatically.";
    showToast("Connected to " + handle.name);
  } catch (e) {
    if (e.name !== "AbortError") console.error(e);
  }
}

// =========================================================
// Login
// =========================================================
function handleLogin() {
  const val = $("staff-id").value.trim();
  if (!val) return;
  const match = employees.find(e => e.id.toLowerCase() === val.toLowerCase());
  if (!match) {
    $("login-error").classList.remove("hidden");
    return;
  }
  $("login-error").classList.add("hidden");
  currentUser = match;
  $("staff-id").value = "";
  $("welcome-text").textContent = "Hi, " + currentUser.name;
  showScreen("app-screen");
  renderCardList();
}

function handleLogout() {
  currentUser = null;
  showScreen("login-screen");
}

// =========================================================
// Screen / modal helpers
// =========================================================
function showScreen(id) {
  document.querySelectorAll(".screen").forEach(s => s.classList.add("hidden"));
  $(id).classList.remove("hidden");
}
function openModal(id) { $(id).classList.remove("hidden"); }
function closeModal(id) { $(id).classList.add("hidden"); }

let toastTimer = null;
function showToast(msg, ms = 2200) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), ms);
}

function formatDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString([], { day: "2-digit", month: "short", year: "numeric" }) +
         " · " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function isEditable(record) {
  return (Date.now() - new Date(record.submittedAt).getTime()) < EDIT_WINDOW_MS;
}

// =========================================================
// Card list rendering
// =========================================================
function renderCardList() {
  const list = $("card-list");
  const sorted = [...vehicles].sort((a, b) => new Date(b.lastModifiedAt) - new Date(a.lastModifiedAt));
  list.innerHTML = "";
  $("empty-state").classList.toggle("hidden", sorted.length > 0);

  sorted.forEach(record => {
    const meta = TYPE_META[record.type] || TYPE_META["car"];
    const card = document.createElement("div");
    card.className = "vcard";
    card.innerHTML = `
      <div class="vcard-thumb">
        ${record.plateImage
          ? `<img src="${record.plateImage}" alt="Plate photo">`
          : `<span class="placeholder">${meta.icon}</span>`}
      </div>
      <div class="vcard-main">
        <div class="vcard-top">
          <span class="type-chip" style="background:${meta.color};${meta.border ? "border:2px solid var(--navy);" : ""}">${meta.icon}</span>
          <span class="vcard-id">${meta.label}</span>
        </div>
        <div class="vcard-time">🕒 ${formatDateTime(record.lastModifiedAt)}</div>
        ${!isEditable(record) ? `<div class="vcard-lock">🔒 Locked for editing</div>` : ``}
      </div>
      <div class="vcard-chevron">›</div>
    `;
    card.addEventListener("click", () => openDetail(record.id));
    list.appendChild(card);
  });
}

// =========================================================
// Add / Edit flow
// =========================================================
function startNewInspection(type) {
  editingId = null;
  pendingInspection = {
    id: null,
    type,
    plateImage: "",
    odometerImage: "",
    tyreDepths: new Array(TYRE_COUNTS[type]).fill(""),
  };
  renderFormScreen("New Inspection");
  showScreen("form-screen");
}

function startEditInspection(record) {
  editingId = record.id;
  pendingInspection = {
    id: record.id,
    type: record.type,
    plateImage: record.plateImage,
    odometerImage: record.odometerImage,
    tyreDepths: [...record.tyreDepths],
  };
  renderFormScreen("Edit Inspection");
  showScreen("form-screen");
}

function renderFormScreen(titleText) {
  const meta = TYPE_META[pendingInspection.type];
  $("form-title-text").textContent = titleText;
  $("form-type-icon").textContent = meta.icon;

  const tyreCount = TYRE_COUNTS[pendingInspection.type];
  let tyreFieldsHtml = "";
  for (let i = 0; i < tyreCount; i++) {
    tyreFieldsHtml += `
      <div class="tyre-field">
        <label for="tyre-${i}">Tyre ${i + 1}</label>
        <input type="number" inputmode="decimal" min="0" step="0.1" id="tyre-${i}"
               placeholder="mm" value="${pendingInspection.tyreDepths[i] || ""}">
      </div>`;
  }

  $("form-body").innerHTML = `
    <div class="form-section">
      <div class="form-section-title"><span class="form-section-num">1</span> License plate photo</div>
      <div class="photo-capture-box ${pendingInspection.plateImage ? "filled" : ""}" id="plate-capture-box">
        ${pendingInspection.plateImage
          ? `<img src="${pendingInspection.plateImage}" alt="Plate photo"><div class="retake-label">Tap to retake</div>`
          : `<span class="cap-icon">🚘</span><span>Tap to take a photo of the license plate</span>`}
      </div>
    </div>

    <div class="form-section">
      <div class="form-section-title"><span class="form-section-num">2</span> Odometer photo</div>
      <div class="photo-capture-box ${pendingInspection.odometerImage ? "filled" : ""}" id="odometer-capture-box">
        ${pendingInspection.odometerImage
          ? `<img src="${pendingInspection.odometerImage}" alt="Odometer photo"><div class="retake-label">Tap to retake</div>`
          : `<span class="cap-icon">🔢</span><span>Tap to take a photo of the odometer</span>`}
      </div>
    </div>

    <div class="form-section">
      <div class="form-section-title"><span class="form-section-num">3</span> Tyre tread depth (mm) — ${tyreCount} tyres</div>
      <div class="tyre-grid">${tyreFieldsHtml}</div>
    </div>
  `;

  $("plate-capture-box").addEventListener("click", () => openCamera("plate"));
  $("odometer-capture-box").addEventListener("click", () => openCamera("odometer"));
  for (let i = 0; i < tyreCount; i++) {
    $("tyre-" + i).addEventListener("input", (e) => {
      pendingInspection.tyreDepths[i] = e.target.value;
    });
  }
}

function handleFormBack() {
  const hasData = pendingInspection.plateImage || pendingInspection.odometerImage ||
                  pendingInspection.tyreDepths.some(v => v);
  if (hasData && !confirm("Discard this inspection?")) return;
  pendingInspection = null;
  editingId = null;
  showScreen("app-screen");
}

function handleSubmitInspection() {
  const tyreCount = TYRE_COUNTS[pendingInspection.type];
  if (!pendingInspection.plateImage) { showToast("Please take a photo of the license plate"); return; }
  if (!pendingInspection.odometerImage) { showToast("Please take a photo of the odometer"); return; }
  for (let i = 0; i < tyreCount; i++) {
    const v = pendingInspection.tyreDepths[i];
    if (v === "" || v === undefined || isNaN(Number(v)) || Number(v) < 0) {
      showToast(`Please enter a valid reading for Tyre ${i + 1}`);
      return;
    }
  }
  openModal("confirm-modal");
}

function finalizeSubmit() {
  const now = new Date().toISOString();
  if (editingId) {
    const record = vehicles.find(v => v.id === editingId);
    record.plateImage = pendingInspection.plateImage;
    record.odometerImage = pendingInspection.odometerImage;
    record.tyreDepths = [...pendingInspection.tyreDepths];
    record.lastModifiedAt = now;
    showToast("Inspection updated ✔");
  } else {
    const newRecord = {
      id: "V-" + Date.now().toString(36).toUpperCase(),
      submittedBy: currentUser.id,
      type: pendingInspection.type,
      plateImage: pendingInspection.plateImage,
      odometerImage: pendingInspection.odometerImage,
      tyreDepths: [...pendingInspection.tyreDepths],
      submittedAt: now,
      lastModifiedAt: now,
    };
    vehicles.push(newRecord);
    showToast("Inspection submitted ✔");
  }
  persistVehicles();
  closeModal("confirm-modal");
  pendingInspection = null;
  editingId = null;
  showScreen("app-screen");
  renderCardList();
}

// =========================================================
// Camera capture with alignment guide overlays
// =========================================================
const GUIDE_CONFIG = {
  plate:    { widthPct: 0.86, aspect: 4.7,  hint: "Line up the license plate within the box" }, // wide rectangle
  odometer: { widthPct: 0.62, aspect: 1/1.3, hint: "Line up the odometer within the box" },      // taller rectangle
};

async function openCamera(target) {
  cameraTarget = target;
  const cfg = GUIDE_CONFIG[target];
  $("camera-hint").textContent = cfg.hint;

  const guide = $("camera-guide");
  guide.style.width = (cfg.widthPct * 100) + "vw";
  guide.style.height = (cfg.widthPct * 100 / cfg.aspect) + "vw";

  showScreen("camera-screen");

  try {
    cameraStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "environment" }, audio: false,
    });
    $("camera-video").srcObject = cameraStream;
  } catch (e) {
    console.error(e);
    showToast("Camera not available — check permissions");
    closeCameraScreen();
  }
}

function closeCameraScreen() {
  if (cameraStream) {
    cameraStream.getTracks().forEach(t => t.stop());
    cameraStream = null;
  }
  showScreen("form-screen");
}

function capturePhoto() {
  const video = $("camera-video");
  const canvas = $("camera-canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  const ctx = canvas.getContext("2d");
  // Only the raw video frame is drawn — the amber guide box is a
  // separate HTML overlay, so it is never part of the captured image.
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
  lastCapturedDataUrl = canvas.toDataURL("image/jpeg", 0.85);

  if (cameraStream) { cameraStream.getTracks().forEach(t => t.stop()); cameraStream = null; }
  showScreen("form-screen"); // keep form underneath; review modal sits on top
  $("photo-review-title").textContent = cameraTarget === "plate" ? "Use this plate photo?" : "Use this odometer photo?";
  $("photo-review-img").src = lastCapturedDataUrl;
  openModal("photo-review-modal");
}

function confirmPhoto() {
  if (cameraTarget === "plate") pendingInspection.plateImage = lastCapturedDataUrl;
  else pendingInspection.odometerImage = lastCapturedDataUrl;
  closeModal("photo-review-modal");
  renderFormScreen($("form-title-text").textContent);
}

function retakePhoto() {
  closeModal("photo-review-modal");
  openCamera(cameraTarget);
}

// =========================================================
// Detail view / delete
// =========================================================
let detailRecordId = null;

function openDetail(id) {
  detailRecordId = id;
  const record = vehicles.find(v => v.id === id);
  const meta = TYPE_META[record.type];
  $("detail-title").textContent = meta.label + " Inspection";

  const tyresHtml = record.tyreDepths.map((v, i) => `
    <div class="tyre-pill ${Number(v) < 3 ? "low" : ""}">
      <div class="val">${v}</div>
      <div class="lbl">Tyre ${i + 1}</div>
    </div>`).join("");

  $("detail-body").innerHTML = `
    <div class="detail-row"><span>Vehicle type</span><span>${meta.icon} ${meta.label}</span></div>
    <div class="detail-row"><span>Submitted by</span><span>${record.submittedBy}</span></div>
    <div class="detail-row"><span>Submitted at</span><span>${formatDateTime(record.submittedAt)}</span></div>
    <div class="detail-row"><span>Last modified</span><span>${formatDateTime(record.lastModifiedAt)}</span></div>

    <div class="detail-photos">
      <figure>
        <img src="${record.plateImage || ""}" alt="License plate">
        <figcaption>License plate</figcaption>
      </figure>
      <figure>
        <img src="${record.odometerImage || ""}" alt="Odometer">
        <figcaption>Odometer</figcaption>
      </figure>
    </div>

    <div class="form-section-title" style="margin-top:6px;">Tyre tread depth (mm)</div>
    <div class="detail-tyres">${tyresHtml}</div>
  `;

  const editable = isEditable(record);
  $("detail-edit-btn").classList.toggle("hidden", !editable);
  $("detail-lock-note").classList.toggle("hidden", editable);

  openModal("detail-modal");
}

function handleDetailEdit() {
  const record = vehicles.find(v => v.id === detailRecordId);
  closeModal("detail-modal");
  startEditInspection(record);
}

function handleDeleteConfirmed() {
  vehicles = vehicles.filter(v => v.id !== detailRecordId);
  persistVehicles();
  closeModal("delete-modal");
  closeModal("detail-modal");
  renderCardList();
  showToast("Record deleted");
}

// =========================================================
// Export / Import CSV (manual fallback)
// =========================================================
function exportCsv() {
  const blob = new Blob([vehiclesToCSV(vehicles)], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "vehicles.csv";
  a.click();
  URL.revokeObjectURL(url);
}

function importCsv(file) {
  const reader = new FileReader();
  reader.onload = () => {
    if (!confirm("This will replace all current records with the contents of this CSV. Continue?")) return;
    vehicles = parseCSV(reader.result).map(csvRowToVehicle);
    persistVehicles();
    renderCardList();
    showToast("Imported " + vehicles.length + " records");
  };
  reader.readAsText(file);
}

// =========================================================
// Wire up events + init
// =========================================================
function init() {
  $("login-btn").addEventListener("click", handleLogin);
  $("staff-id").addEventListener("keydown", (e) => { if (e.key === "Enter") handleLogin(); });
  $("connect-csv-btn").addEventListener("click", connectCsvFile);
  $("logout-btn").addEventListener("click", handleLogout);

  $("add-fab").addEventListener("click", () => openModal("type-modal"));
  $("type-cancel-btn").addEventListener("click", () => closeModal("type-modal"));
  document.querySelectorAll(".type-option").forEach(btn => {
    btn.addEventListener("click", () => {
      closeModal("type-modal");
      startNewInspection(btn.dataset.type);
    });
  });

  $("form-back-btn").addEventListener("click", handleFormBack);
  $("submit-inspection-btn").addEventListener("click", handleSubmitInspection);
  $("confirm-cancel-btn").addEventListener("click", () => closeModal("confirm-modal"));
  $("confirm-ok-btn").addEventListener("click", finalizeSubmit);

  $("camera-cancel-btn").addEventListener("click", closeCameraScreen);
  $("camera-shutter-btn").addEventListener("click", capturePhoto);
  $("photo-retake-btn").addEventListener("click", retakePhoto);
  $("photo-confirm-btn").addEventListener("click", confirmPhoto);

  $("detail-close-btn").addEventListener("click", () => closeModal("detail-modal"));
  $("detail-edit-btn").addEventListener("click", handleDetailEdit);
  $("detail-delete-btn").addEventListener("click", () => openModal("delete-modal"));
  $("delete-cancel-btn").addEventListener("click", () => closeModal("delete-modal"));
  $("delete-ok-btn").addEventListener("click", handleDeleteConfirmed);

  $("export-btn").addEventListener("click", exportCsv);
  $("import-btn").addEventListener("click", () => $("import-file-input").click());
  $("import-file-input").addEventListener("change", (e) => {
    if (e.target.files[0]) importCsv(e.target.files[0]);
    e.target.value = "";
  });

  loadEmployees();
  loadVehicles();
  showScreen("login-screen");
}

document.addEventListener("DOMContentLoaded", init);