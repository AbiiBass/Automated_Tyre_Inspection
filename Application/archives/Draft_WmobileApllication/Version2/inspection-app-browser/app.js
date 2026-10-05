(() => {
  "use strict";

  // ---------------------------------------------------------------
  // This version runs entirely in the browser - no server, no install.
  // Records are kept in this browser's localStorage (under STORAGE_KEY).
  // Use the export button (top right, after signing in) to download a
  // vehicles.csv snapshot whenever you want a file on disk.
  //
  // Trade-off vs. the Node.js version: data lives only in this browser
  // until you export it - it won't survive clearing browser data, and
  // won't be visible from a different browser/device.
  // ---------------------------------------------------------------

  const STORAGE_KEY = "smrt_vehicles_v1";
  const USERS_CSV_PATH = "users.csv";

  const state = {
    user: null,
    vehicles: [],
    filter: "all",
    activeType: null,
    editingVehicle: null,
    captured: { plate: null, odometer: null },
    cameraTarget: null,
    cameraStream: null,
  };

  const TYPE_LABELS = { car: "Car", single_bus: "Single Bus", double_decker: "Double Decker" };
  const TYPE_TYRES = { car: 4, single_bus: 6, double_decker: 8 };
  const TYRE_LOW_THRESHOLD = 1.6; // mm - typical legal minimum tread depth

  // ---------------------------------------------------------------
  // Small helpers
  // ---------------------------------------------------------------
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  function showScreen(id) {
    $$(".screen").forEach((s) => s.classList.remove("active"));
    $(`#${id}`).classList.add("active");
  }
  function openSheet(id) { $(`#${id}`).classList.add("active"); }
  function closeSheet(id) { $(`#${id}`).classList.remove("active"); }

  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => (t.hidden = true), 2400);
  }

  function formatDateTime(iso) {
    if (!iso) return "-";
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      day: "2-digit", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  }

  function hoursSince(iso) {
    return (Date.now() - new Date(iso).getTime()) / 36e5;
  }

  function uid() {
    return Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  }

  // ---------------------------------------------------------------
  // Tiny CSV helpers (parse users.csv, and build the export file)
  // ---------------------------------------------------------------
  function parseCsv(text) {
    const lines = text.replace(/\r\n/g, "\n").trim().split("\n");
    const headers = lines.shift().split(",").map((h) => h.trim());
    return lines.filter(Boolean).map((line) => {
      const cols = line.split(",");
      const obj = {};
      headers.forEach((h, i) => (obj[h] = (cols[i] || "").trim()));
      return obj;
    });
  }

  function csvEscape(value) {
    const s = String(value ?? "");
    if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
    return s;
  }

  // ---------------------------------------------------------------
  // localStorage persistence
  // ---------------------------------------------------------------
  function loadVehicles() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      state.vehicles = raw ? JSON.parse(raw) : [];
    } catch (e) {
      state.vehicles = [];
    }
  }

  function saveVehicles() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state.vehicles));
    } catch (e) {
      toast("Storage is full - export and clear old records");
    }
  }

  // ---------------------------------------------------------------
  // Image compression (keeps localStorage + exported CSV small)
  // ---------------------------------------------------------------
  function compressImage(dataUrl, maxDim = 640, quality = 0.6) {
    return new Promise((resolve) => {
      const img = new Image();
      img.onload = () => {
        let { width, height } = img;
        if (width > height && width > maxDim) { height *= maxDim / width; width = maxDim; }
        else if (height > maxDim) { width *= maxDim / height; height = maxDim; }
        const canvas = document.createElement("canvas");
        canvas.width = Math.round(width);
        canvas.height = Math.round(height);
        canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
        resolve(canvas.toDataURL("image/jpeg", quality));
      };
      img.onerror = () => resolve(dataUrl);
      img.src = dataUrl;
    });
  }

  // ---------------------------------------------------------------
  // Login
  // ---------------------------------------------------------------
  $("#btn-login").addEventListener("click", handleLogin);
  $("#login-id").addEventListener("keydown", (e) => { if (e.key === "Enter") handleLogin(); });

  async function handleLogin() {
    const id = $("#login-id").value.trim();
    $("#login-error").hidden = true;
    if (!id) return;
    try {
      const res = await fetch(USERS_CSV_PATH, { cache: "no-store" });
      if (!res.ok) throw new Error("Could not load users.csv");
      const users = parseCsv(await res.text());
      const user = users.find((u) => u.id.toLowerCase() === id.toLowerCase());
      if (!user) { $("#login-error").hidden = false; return; }
      state.user = { id: user.id, name: user.name };
      $("#welcome-text").textContent = `Hi, ${user.name}`;
      loadVehicles();
      renderList();
      showScreen("screen-list");
    } catch (err) {
      toast("Could not read users.csv - is it in the same folder?");
    }
  }

  $("#btn-logout").addEventListener("click", () => {
    state.user = null;
    $("#login-id").value = "";
    showScreen("screen-login");
  });

  // ---------------------------------------------------------------
  // Export to CSV
  // ---------------------------------------------------------------
  $("#btn-export").addEventListener("click", () => {
    if (state.vehicles.length === 0) { toast("No inspections to export yet"); return; }
    const headers = ["id", "type", "submittedAt", "lastModifiedAt", "userId", "userName", "tyreDepths", "plateImage", "odometerImage"];
    const lines = [headers.join(",")];
    for (const v of state.vehicles) {
      const row = [
        v.id, v.type, v.submittedAt, v.lastModifiedAt, v.userId, v.userName,
        v.tyreDepths.join(";"), v.plateImage, v.odometerImage,
      ];
      lines.push(row.map(csvEscape).join(","));
    }
    const blob = new Blob([lines.join("\n")], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `vehicles_export_${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    toast("CSV downloaded");
  });

  // ---------------------------------------------------------------
  // List screen
  // ---------------------------------------------------------------
  $$(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      $$(".chip").forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      state.filter = chip.dataset.filter;
      renderList();
    });
  });

  function typeBadgeSvg(type) {
    if (type === "car") {
      return `<svg viewBox="0 0 64 40" width="16" height="16"><path d="M6 26l4-12a5 5 0 0 1 5-3h20a5 5 0 0 1 5 3l4 12" fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/><rect x="4" y="26" width="56" height="9" rx="3" fill="#fff"/></svg>`;
    }
    if (type === "single_bus") {
      return `<svg viewBox="0 0 64 40" width="15" height="15"><rect x="4" y="8" width="56" height="24" rx="4" fill="none" stroke="#fff" stroke-width="4"/></svg>`;
    }
    return `<svg viewBox="0 0 64 40" width="15" height="15"><rect x="4" y="2" width="56" height="30" rx="4" fill="none" stroke="#fff" stroke-width="4"/><line x1="4" y1="17" x2="60" y2="17" stroke="#fff" stroke-width="3"/></svg>`;
  }

  function renderList() {
    const list = $("#card-list");
    const items = state.vehicles
      .filter((v) => state.filter === "all" || v.type === state.filter)
      .sort((a, b) => new Date(b.submittedAt) - new Date(a.submittedAt));
    list.innerHTML = "";
    $("#empty-state").hidden = state.vehicles.length > 0;

    if (items.length === 0) {
      if (state.vehicles.length > 0) {
        list.innerHTML = `<p style="text-align:center;color:var(--text-muted);margin-top:40px;">No ${TYPE_LABELS[state.filter] || ""} inspections in this filter.</p>`;
      }
      return;
    }

    for (const v of items) {
      const card = document.createElement("div");
      card.className = "vehicle-card";
      card.innerHTML = `
        <img class="card-thumb" src="${v.plateImage}" alt="License plate" onerror="this.style.visibility='hidden'"/>
        <div class="card-body">
          <div class="card-top-row">
            <div class="type-badge ${v.type}">${typeBadgeSvg(v.type)}</div>
            <div class="card-type-label">${TYPE_LABELS[v.type] || v.type}</div>
          </div>
          <div class="card-date">Submitted ${formatDateTime(v.submittedAt)}</div>
        </div>
        <div class="card-chevron">
          <svg viewBox="0 0 24 24" width="18" height="18"><path d="M9 6l6 6-6 6" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </div>`;
      card.addEventListener("click", () => openDetail(v));
      list.appendChild(card);
    }
  }

  // ---------------------------------------------------------------
  // Add flow: type picker
  // ---------------------------------------------------------------
  $("#btn-add").addEventListener("click", () => {
    state.editingVehicle = null;
    openSheet("screen-type");
  });
  $("#btn-type-cancel").addEventListener("click", () => closeSheet("screen-type"));

  $$(".type-card").forEach((card) => {
    card.addEventListener("click", () => {
      state.activeType = card.dataset.type;
      closeSheet("screen-type");
      openNewForm(state.activeType);
    });
  });

  // ---------------------------------------------------------------
  // Form (add / edit)
  // ---------------------------------------------------------------
  function openNewForm(type) {
    state.captured = { plate: null, odometer: null };
    $("#form-title").textContent = `New Inspection — ${TYPE_LABELS[type]}`;
    resetCaptureBox("plate");
    resetCaptureBox("odometer");
    buildTyreFields(type, []);
    openSheet("screen-form");
  }

  function openEditForm(vehicle) {
    state.editingVehicle = vehicle;
    state.activeType = vehicle.type;
    state.captured = { plate: null, odometer: null };
    $("#form-title").textContent = `Edit Inspection — ${TYPE_LABELS[vehicle.type]}`;

    setCaptureBoxImage("plate", vehicle.plateImage);
    setCaptureBoxImage("odometer", vehicle.odometerImage);
    buildTyreFields(vehicle.type, vehicle.tyreDepths);

    closeSheet("screen-detail");
    openSheet("screen-form");
  }

  function resetCaptureBox(target) {
    const box = $(`#capture-${target}`);
    box.classList.remove("filled");
    box.querySelector(".capture-placeholder").hidden = false;
    const img = box.querySelector(".capture-preview");
    img.hidden = true;
    img.removeAttribute("src");
    const tag = box.querySelector(".retake-tag");
    if (tag) tag.remove();
  }

  function setCaptureBoxImage(target, src) {
    const box = $(`#capture-${target}`);
    box.classList.add("filled");
    box.querySelector(".capture-placeholder").hidden = true;
    const img = box.querySelector(".capture-preview");
    img.src = src;
    img.hidden = false;
    if (!box.querySelector(".retake-tag")) {
      const tag = document.createElement("div");
      tag.className = "retake-tag";
      tag.textContent = "Retake";
      box.appendChild(tag);
    }
  }

  function buildTyreFields(type, existingDepths) {
    const count = TYPE_TYRES[type];
    const grid = $("#tyre-grid");
    grid.innerHTML = "";
    for (let i = 0; i < count; i++) {
      const wrap = document.createElement("div");
      wrap.className = "tyre-field";
      const value = existingDepths && existingDepths[i] !== undefined ? existingDepths[i] : "";
      wrap.innerHTML = `
        <label for="tyre-${i}">Tyre ${i + 1}</label>
        <input type="number" id="tyre-${i}" inputmode="decimal" step="0.1" min="0" max="20" placeholder="mm" value="${value}" />`;
      grid.appendChild(wrap);
    }
  }

  $("#capture-plate").addEventListener("click", () => openCamera("plate"));
  $("#capture-odometer").addEventListener("click", () => openCamera("odometer"));

  $("#btn-form-cancel").addEventListener("click", () => {
    closeSheet("screen-form");
  });

  // ---------------------------------------------------------------
  // Camera capture (live preview + alignment guide)
  // ---------------------------------------------------------------
  async function openCamera(target) {
    state.cameraTarget = target;
    const guide = $("#camera-guide");
    guide.className = "camera-guide " + (target === "plate" ? "guide-plate" : "guide-odometer");
    $("#camera-label").textContent = target === "plate" ? "Align license plate" : "Align odometer display";

    $("#camera-shoot-bar").hidden = false;
    $("#camera-confirm-bar").hidden = true;
    $("#camera-preview-img").hidden = true;
    $("#camera-video").hidden = false;
    guide.hidden = false;

    showScreen("screen-camera");

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "environment", width: { ideal: 1280 }, height: { ideal: 960 } },
        audio: false,
      });
      state.cameraStream = stream;
      $("#camera-video").srcObject = stream;
    } catch (err) {
      openCameraFallback(target);
    }
  }

  function openCameraFallback(target) {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    input.capture = "environment";
    input.style.display = "none";
    document.body.appendChild(input);
    input.addEventListener("change", () => {
      const file = input.files && input.files[0];
      document.body.removeChild(input);
      if (!file) { closeCamera(); return; }
      const reader = new FileReader();
      reader.onload = () => showCapturedPreview(reader.result);
      reader.readAsDataURL(file);
    });
    input.click();
  }

  function stopCameraStream() {
    if (state.cameraStream) {
      state.cameraStream.getTracks().forEach((t) => t.stop());
      state.cameraStream = null;
    }
  }

  function closeCamera() {
    stopCameraStream();
    showScreen("screen-form");
  }
  $("#btn-camera-close").addEventListener("click", closeCamera);

  $("#btn-camera-shoot").addEventListener("click", () => {
    const video = $("#camera-video");
    const canvas = $("#camera-canvas");
    canvas.width = video.videoWidth || 1280;
    canvas.height = video.videoHeight || 960;
    const ctx = canvas.getContext("2d");
    // Draw only the raw video frame — the guide box is a CSS overlay only,
    // so it is never part of the captured image.
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    const dataUrl = canvas.toDataURL("image/jpeg", 0.85);
    stopCameraStream();
    showCapturedPreview(dataUrl);
  });

  function showCapturedPreview(dataUrl) {
    $("#camera-video").hidden = true;
    $("#camera-guide").hidden = true;
    const img = $("#camera-preview-img");
    img.src = dataUrl;
    img.hidden = false;
    $("#camera-shoot-bar").hidden = true;
    $("#camera-confirm-bar").hidden = false;
    img.dataset.pending = dataUrl;
  }

  $("#btn-camera-retake").addEventListener("click", () => {
    openCamera(state.cameraTarget);
  });

  $("#btn-camera-use").addEventListener("click", async () => {
    const dataUrl = $("#camera-preview-img").dataset.pending;
    const compressed = await compressImage(dataUrl, 640, 0.6);
    state.captured[state.cameraTarget] = compressed;
    setCaptureBoxImage(state.cameraTarget, compressed);
    showScreen("screen-form");
  });

  // ---------------------------------------------------------------
  // Submit (create or update)
  // ---------------------------------------------------------------
  $("#btn-submit").addEventListener("click", () => {
    if (!validateForm()) return;
    $("#modal-confirm").hidden = false;
  });

  $("#btn-confirm-cancel").addEventListener("click", () => { $("#modal-confirm").hidden = true; });

  $("#btn-confirm-ok").addEventListener("click", () => {
    $("#modal-confirm").hidden = true;
    doSubmit();
  });

  function validateForm() {
    const editing = state.editingVehicle;
    if (!editing && !state.captured.plate) { toast("Please take a photo of the license plate"); return false; }
    if (!editing && !state.captured.odometer) { toast("Please take a photo of the odometer"); return false; }
    const count = TYPE_TYRES[state.activeType];
    for (let i = 0; i < count; i++) {
      const val = $(`#tyre-${i}`).value;
      if (val === "" || Number(val) < 0) { toast(`Please enter a valid reading for Tyre ${i + 1}`); return false; }
    }
    return true;
  }

  function doSubmit() {
    const count = TYPE_TYRES[state.activeType];
    const tyreDepths = [];
    for (let i = 0; i < count; i++) tyreDepths.push(Number($(`#tyre-${i}`).value));
    const now = new Date().toISOString();

    if (state.editingVehicle) {
      const v = state.editingVehicle;
      if (hoursSince(v.submittedAt) > 24) { toast("Editing window (24h) has passed"); return; }
      v.type = state.activeType;
      v.tyreDepths = tyreDepths;
      if (state.captured.plate) v.plateImage = state.captured.plate;
      if (state.captured.odometer) v.odometerImage = state.captured.odometer;
      v.lastModifiedAt = now;
      toast("Inspection updated");
    } else {
      state.vehicles.push({
        id: uid(),
        type: state.activeType,
        plateImage: state.captured.plate,
        odometerImage: state.captured.odometer,
        tyreDepths,
        userId: state.user.id,
        userName: state.user.name,
        submittedAt: now,
        lastModifiedAt: now,
      });
      toast("Inspection submitted");
    }

    saveVehicles();
    closeSheet("screen-form");
    state.editingVehicle = null;
    renderList();
  }

  // ---------------------------------------------------------------
  // Detail screen
  // ---------------------------------------------------------------
  function openDetail(vehicle) {
    const hrsSince = hoursSince(vehicle.submittedAt);
    const canEdit = hrsSince < 24;

    const tyreHtml = vehicle.tyreDepths.map((d, i) => {
      const low = Number(d) < TYRE_LOW_THRESHOLD;
      return `<div class="tyre-readout ${low ? "low" : ""}">
        <span class="label">Tyre ${i + 1}</span>
        <span class="value">${d} mm</span>
      </div>`;
    }).join("");

    $("#detail-content").innerHTML = `
      <div class="detail-hero"><img src="${vehicle.plateImage}" alt="License plate" /></div>
      <div class="detail-type-row">
        <div class="type-badge ${vehicle.type}" style="width:34px;height:34px;">${typeBadgeSvg(vehicle.type)}</div>
        <span>${TYPE_LABELS[vehicle.type]}</span>
      </div>

      <div class="detail-block">
        <div class="detail-block-title">Odometer photo</div>
        <div class="detail-hero"><img src="${vehicle.odometerImage}" alt="Odometer" /></div>
      </div>

      <div class="detail-block">
        <div class="detail-block-title">Tyre tread depth</div>
        <div class="tyre-readout-grid">${tyreHtml}</div>
      </div>

      <div class="detail-block">
        <div class="detail-block-title">Submission info</div>
        <div class="detail-meta-row"><span>Submitted by</span><span>${vehicle.userName}</span></div>
        <div class="detail-meta-row"><span>Submitted at</span><span>${formatDateTime(vehicle.submittedAt)}</span></div>
        <div class="detail-meta-row"><span>Last modified</span><span>${formatDateTime(vehicle.lastModifiedAt)}</span></div>
        ${canEdit
          ? `<div class="edit-window-banner">Editable for ${Math.max(0, (24 - hrsSince)).toFixed(1)} more hour(s)</div>`
          : `<div class="lock-banner">Editing window (24h) has passed — this record is locked</div>`}
      </div>

      <button id="btn-edit-vehicle" class="btn btn-primary btn-block" ${canEdit ? "" : "disabled"}>
        ${canEdit ? "Edit Inspection" : "Locked"}
      </button>
    `;

    if (canEdit) {
      $("#btn-edit-vehicle").addEventListener("click", () => openEditForm(vehicle));
    }

    openSheet("screen-detail");
  }

  $("#btn-detail-close").addEventListener("click", () => closeSheet("screen-detail"));

})();
