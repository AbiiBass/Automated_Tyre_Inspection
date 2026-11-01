/* ==========================================================================
   SMRT Technician App Logic
   ========================================================================== */

let currentStep = 1;
let selectedType = null;
let plateImageData = null;
let odometerImageData = null;
let editingId = null;
let tyreValues = {};
let tyreReadings = {};   // ESP32 only: { FL: [r1..r5, avg] }  (manual tyres have no entry)
let espPendingCode = null; // a field that has asked the server to listen but hasn't heard back yet
let espListen = null;    // { token, code, timer } while a field is waiting for the ESP32
let routeType = 'feeder';
let activeCameraMode = null; // 'plate' | 'odometer'
let mediaStream = null;

const CFG = window.VEHICLE_CONFIG;

/* --------------------- Technician homepage device selector --------------------- */
const deviceSelect = document.getElementById('tech-device-select');
if (deviceSelect) {
  let savedDevice = deviceSelect.value;
  const status = document.getElementById('tech-device-status');
  deviceSelect.addEventListener('change', async () => {
    const chosen = deviceSelect.value;
    deviceSelect.disabled = true;
    status.textContent = 'Saving device selection…';
    try {
      const res = await fetch('/technician/esp/device', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_id: chosen })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Unable to select device');
      savedDevice = chosen;
      status.textContent = chosen ? '✓ Device selected for this session' : 'Select a device to receive measurements.';
      status.classList.remove('error');
    } catch (error) {
      deviceSelect.value = savedDevice;
      status.textContent = error.message;
      status.classList.add('error');
    } finally {
      deviceSelect.disabled = false;
    }
  });
}

/* ---------------------------- Toast helper ---------------------------- */
function showToast(msg, isError) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.toggle('error', !!isError);
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2600);
}

/* ---------------------------- Card menu ---------------------------- */
let openMenuId = null;
function toggleMenu(evt, id) {
  evt.stopPropagation();
  document.querySelectorAll('.card-menu-popover.open').forEach(el => {
    if (el.id !== `menu-${id}`) el.classList.remove('open');
  });
  const el = document.getElementById(`menu-${id}`);
  el.classList.toggle('open');
  openMenuId = el.classList.contains('open') ? id : null;
}
document.addEventListener('click', () => {
  document.querySelectorAll('.card-menu-popover.open').forEach(el => el.classList.remove('open'));
});

/* ---------------------------- Wizard open/close ---------------------------- */
function resetWizardState() {
  currentStep = 1;
  selectedType = null;
  plateImageData = null;
  odometerImageData = null;
  editingId = null;
  tyreValues = {};
  tyreReadings = {};
  stopEspListening(true);
  routeType = 'feeder';
  document.getElementById('wizard-title').textContent = 'New Inspection';
  document.querySelectorAll('.type-card').forEach(c => c.classList.remove('selected'));
  document.getElementById('plate-preview').style.display = 'none';
  document.getElementById('odometer-preview').style.display = 'none';
  document.getElementById('plate-preview').src = '';
  document.getElementById('odometer-preview').src = '';
  updateRouteTypeUI();
  showStep(1);
}

function openAddWizard() {
  resetWizardState();
  document.getElementById('wizard-overlay').classList.add('open');
}

function closeWizard() {
  stopEspListening(true);
  document.getElementById('wizard-overlay').classList.remove('open');
}

function showStep(step) {
  currentStep = step;
  document.getElementById('step-1').style.display = step === 1 ? 'block' : 'none';
  document.getElementById('step-2').style.display = step === 2 ? 'block' : 'none';
  document.querySelectorAll('.step-progress .seg').forEach(seg => {
    const s = parseInt(seg.dataset.step, 10);
    seg.classList.toggle('active', s === step);
    seg.classList.toggle('done', s < step);
  });
  document.getElementById('back-btn').style.display = step === 2 ? 'inline-flex' : 'none';
  document.getElementById('complete-btn').style.display = step === 2 ? 'inline-flex' : 'none';
  document.getElementById('wizard-body').scrollTop = 0;
}

function goBackStep() {
  showStep(1);
}

function selectType(type) {
  selectedType = type;
  document.querySelectorAll('.type-card').forEach(c => {
    c.classList.toggle('selected', c.dataset.type === type);
  });
  // small delay so the user sees the selection highlight before advancing
  setTimeout(() => {
    buildTyreUI(type);
    updateRouteTypeUI();
    showStep(2);
  }, 150);
}

/* ---------------------------- Bus route type (Trunk / Feeder) ---------------------------- */
function selectRouteType(type) {
  routeType = type;
  const input = document.getElementById('route-slider-input');
  if (input) input.checked = (type === 'trunk');
  document.getElementById('route-label-feeder').classList.toggle('active', type === 'feeder');
  document.getElementById('route-label-trunk').classList.toggle('active', type === 'trunk');
}

function onRouteSliderChange(checkboxEl) {
  selectRouteType(checkboxEl.checked ? 'trunk' : 'feeder');
}

function updateRouteTypeUI() {
  const cfg = selectedType ? CFG[selectedType] : null;
  const showRoute = !!(cfg && cfg.has_route_type);
  const section = document.getElementById('route-type-section');
  section.style.display = showRoute ? 'block' : 'none';

  selectRouteType(routeType);

  // Renumber the steps that follow, since the route-type question only
  // appears for buses/double-decker buses.
  document.getElementById('tyre-step-num').textContent = showRoute ? '4' : '3';
  document.getElementById('last-edited-step-num').textContent = showRoute ? '5' : '4';
}

/* ---------------------------- Tyre diagram + inputs ---------------------------- */
function buildTyreUI(type) {
  const cfg = CFG[type];
  const diagram = document.getElementById('tyre-diagram');
  const inputs = document.getElementById('tyre-inputs');
  diagram.innerHTML = '';
  inputs.innerHTML = '';

  cfg.rows.forEach(rowCfg => {
    // --- diagram row ---
    const rowLabelMini = document.createElement('div');
    rowLabelMini.className = 'row-label-mini';
    rowLabelMini.textContent = rowCfg.row;
    diagram.appendChild(rowLabelMini);

    const chassisRow = document.createElement('div');
    chassisRow.className = 'chassis-row';

    const leftGroup = document.createElement('div');
    leftGroup.className = 'side-group';
    rowCfg.left.forEach(t => {
      const dot = document.createElement('div');
      dot.className = 'tyre-dot';
      dot.id = `dot-${t.code}`;
      leftGroup.appendChild(dot);
    });

    const chassisBody = document.createElement('div');
    chassisBody.className = 'chassis-body';

    const rightGroup = document.createElement('div');
    rightGroup.className = 'side-group';
    rowCfg.right.forEach(t => {
      const dot = document.createElement('div');
      dot.className = 'tyre-dot';
      dot.id = `dot-${t.code}`;
      rightGroup.appendChild(dot);
    });

    chassisRow.appendChild(leftGroup);
    chassisRow.appendChild(chassisBody);
    chassisRow.appendChild(rightGroup);
    diagram.appendChild(chassisRow);

    // --- input row ---
    const rowGroup = document.createElement('div');
    rowGroup.className = 'tyre-row-group';

    const rowLabel = document.createElement('div');
    rowLabel.className = 'row-label';
    rowLabel.textContent = rowCfg.row + ' Axle';
    rowGroup.appendChild(rowLabel);

    const cols = document.createElement('div');
    cols.className = 'tyre-row-cols';

    [rowCfg.left, rowCfg.right].forEach(sideList => {
      const sideWrap = document.createElement('div');
      sideWrap.className = 'side-inputs';
      sideList.forEach(t => {
        sideWrap.appendChild(buildTyreField(t));
      });
      cols.appendChild(sideWrap);
    });

    rowGroup.appendChild(cols);
    inputs.appendChild(rowGroup);
  });
}

function buildTyreField(tyre) {
  const field = document.createElement('div');
  field.className = 'tyre-field';

  const label = document.createElement('label');
  label.htmlFor = `input-${tyre.code}`;
  label.textContent = tyre.label;

  const wrap = document.createElement('div');
  wrap.className = 'input-wrap';

  const input = document.createElement('input');
  input.type = 'number';
  input.step = '0.01';
  input.min = '0';
  input.max = '30';
  input.id = `input-${tyre.code}`;
  input.placeholder = '0.00';
  input.inputMode = 'decimal';
  if (tyreValues[tyre.code] !== undefined) input.value = tyreValues[tyre.code];

  // Tapping a field = "I'm about to measure this tyre". The page starts
  // listening for the ESP32; typing a number by hand is still allowed.
  input.addEventListener('focus', () => {
    highlightTyre(tyre.code);
    startEspListening(tyre.code);
  });
  // A box that already holds an ESP32 value keeps listening for a newer one
  // only while it stays selected; tapping away ends it. (A box that has NOT
  // received anything yet keeps waiting, so dismissing the phone keyboard
  // doesn't cancel a measurement you're about to take.)
  input.addEventListener('blur', () => {
    if (espListen && espListen.code === tyre.code && tyreReadings[tyre.code]) {
      stopEspListening(true);
    }
  });
  input.addEventListener('input', () => {
    tyreValues[tyre.code] = input.value;
    // Typing by hand turns this tyre back into a plain manual entry.
    delete tyreReadings[tyre.code];
    if (espListen && espListen.code === tyre.code) stopEspListening(true);
    setEspStatus(tyre.code, 'none');
  });

  const unit = document.createElement('span');
  unit.className = 'unit';
  unit.textContent = 'mm';

  const status = document.createElement('div');
  status.className = 'esp-status';
  status.id = `esp-status-${tyre.code}`;

  wrap.appendChild(input);
  wrap.appendChild(unit);
  field.appendChild(label);
  field.appendChild(wrap);
  field.appendChild(status);

  if (tyreReadings[tyre.code]) setEspStatus(tyre.code, 'received');
  return field;
}

/* ---------------------------- ESP32 listening ---------------------------- */
function setEspStatus(code, state) {
  const el = document.getElementById(`esp-status-${code}`);
  if (!el) return;
  el.className = 'esp-status' + (state === 'none' ? '' : ` show ${state}`);
  if (state === 'listening') {
    el.innerHTML = '<span class="esp-pulse"></span> Listening for ESP32… measure now';
  } else if (state === 'received') {
    const live = espListen && espListen.code === code;
    el.innerHTML = '<i class="fa fa-microchip" aria-hidden="true"></i> ESP32 average of 5 readings' +
      (live ? ' <span class="esp-live"><span class="esp-pulse"></span> listening for a new one</span>' : '');
  } else {
    el.innerHTML = '';
  }
}

async function startEspListening(code) {
  if (espListen && espListen.code === code) return;        // already listening here
  if (espPendingCode === code) return;                     // arm request already in flight
  stopEspListening(true);                                  // only one field listens at a time
  espPendingCode = code;
  if (!tyreReadings[code]) setEspStatus(code, 'listening');
  try {
    const res = await fetch('/technician/esp/arm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tyre: code })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not start listening');
    if (espPendingCode !== code) {
      // The technician moved on (typed, tapped another tyre, closed the wizard)
      // while the request was in flight - release this slot straight away.
      fetch('/technician/esp/disarm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: data.token })
      }).catch(() => {});
      return;
    }
    espPendingCode = null;
    espListen = { token: data.token, code, timer: null, seq: 0 };
    espListen.timer = setInterval(pollEsp, 1000);
    setEspStatus(code, tyreReadings[code] ? 'received' : 'listening');
  } catch (err) {
    if (espPendingCode === code) {
      espPendingCode = null;
      setEspStatus(code, tyreReadings[code] ? 'received' : 'none');   // manual typing still works
    }
  }
}

async function pollEsp() {
  if (!espListen) return;
  const { token, code } = espListen;
  try {
    const res = await fetch(`/technician/esp/poll?token=${encodeURIComponent(token)}&after=${espListen.seq}`);
    const data = await res.json();
    if (!espListen || espListen.token !== token) return;   // superseded meanwhile
    if (data.status === 'received') {
      // Keep listening: a later message replaces this one.
      espListen.seq = data.seq;
      applyEspResult(code, data.values);
    } else if (data.status === 'expired') {
      stopEspListening(false);
      setEspStatus(code, tyreReadings[code] ? 'received' : 'none');
    }
  } catch (err) { /* transient network error - keep listening */ }
}

function stopEspListening(tellServer) {
  if (espPendingCode) {
    const pc = espPendingCode;
    espPendingCode = null;
    if (tellServer) setEspStatus(pc, tyreReadings[pc] ? 'received' : 'none');
  }
  if (!espListen) return;
  clearInterval(espListen.timer);
  const { token, code } = espListen;
  espListen = null;
  if (tellServer) {
    setEspStatus(code, tyreReadings[code] ? 'received' : 'none');
    fetch('/technician/esp/disarm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token })
    }).catch(() => {});
  }
}

function applyEspResult(code, values) {
  if (!Array.isArray(values) || values.length !== 6) return;
  const avg = parseFloat(parseFloat(values[5]).toFixed(2));
  const replaced = !!tyreReadings[code];
  tyreReadings[code] = values.map(Number);   // all six are kept and saved (replaces any older set)
  tyreValues[code] = avg;                    // only the average is shown
  const input = document.getElementById(`input-${code}`);
  if (input) input.value = avg.toFixed(2);
  setEspStatus(code, 'received');
  markFilledDots();
  showToast(`${replaced ? 'ESP32 reading updated' : 'ESP32 reading received'} ✔  (${avg.toFixed(2)} mm)`);
}

function highlightTyre(code) {
  document.querySelectorAll('.tyre-dot').forEach(d => d.classList.remove('active'));
  const dot = document.getElementById(`dot-${code}`);
  if (dot) dot.classList.add('active');
}

function markFilledDots() {
  Object.keys(tyreValues).forEach(code => {
    const dot = document.getElementById(`dot-${code}`);
    if (dot && tyreValues[code] !== '' && tyreValues[code] !== undefined) {
      dot.classList.add('filled');
    }
  });
}

/* ---------------------------- Camera capture ---------------------------- */
async function openCamera(mode) {
  activeCameraMode = mode;
  const overlay = document.getElementById('camera-overlay-rect');
  const caption = document.getElementById('camera-caption');
  overlay.className = 'camera-overlay-rect ' + (mode === 'plate' ? 'plate-shape' : 'odo-shape');
  caption.textContent = mode === 'plate'
    ? 'Align the license plate inside the box'
    : 'Align the odometer inside the box';

  document.getElementById('camera-modal').classList.add('open');

  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      video: {
        facingMode: { ideal: 'environment' },
        width: { ideal: 1920 },
        height: { ideal: 1440 }
      },
      audio: false
    });
    const video = document.getElementById('camera-video');
    video.srcObject = mediaStream;
  } catch (err) {
    showToast('Could not access camera. Please check permissions.', true);
    closeCamera();
  }
}

function closeCamera() {
  document.getElementById('camera-modal').classList.remove('open');
  if (mediaStream) {
    mediaStream.getTracks().forEach(t => t.stop());
    mediaStream = null;
  }
}

function captureShot() {
  const video = document.getElementById('camera-video');
  const stage = document.querySelector('.camera-stage');
  const overlay = document.getElementById('camera-overlay-rect');

  const stageRect = stage.getBoundingClientRect();
  const overlayRect = overlay.getBoundingClientRect();

  const vw = video.videoWidth;
  const vh = video.videoHeight;
  if (!vw || !vh) {
    showToast('Camera not ready yet, please try again.', true);
    return;
  }

  // "object-fit: cover" scale factor between native video and rendered stage
  const scale = Math.max(stageRect.width / vw, stageRect.height / vh);
  const renderedW = vw * scale;
  const renderedH = vh * scale;
  const offsetX = (renderedW - stageRect.width) / 2;
  const offsetY = (renderedH - stageRect.height) / 2;

  const overlayLeft = overlayRect.left - stageRect.left;
  const overlayTop = overlayRect.top - stageRect.top;

  const srcX = (overlayLeft + offsetX) / scale;
  const srcY = (overlayTop + offsetY) / scale;
  const srcW = overlayRect.width / scale;
  const srcH = overlayRect.height / scale;

  const canvas = document.getElementById('capture-canvas');
  canvas.width = Math.round(srcW);
  canvas.height = Math.round(srcH);
  const ctx = canvas.getContext('2d');
  ctx.drawImage(video, srcX, srcY, srcW, srcH, 0, 0, canvas.width, canvas.height);

  const dataUrl = canvas.toDataURL('image/jpeg', 0.88);

  if (activeCameraMode === 'plate') {
    plateImageData = dataUrl;
    const preview = document.getElementById('plate-preview');
    preview.src = dataUrl;
    preview.style.display = 'block';
  } else {
    odometerImageData = dataUrl;
    const preview = document.getElementById('odometer-preview');
    preview.src = dataUrl;
    preview.style.display = 'block';
  }

  closeCamera();
}

/* ---------------------------- Submit / Edit / Delete ---------------------------- */
function validateBeforeSubmit() {
  if (!selectedType) {
    showToast('Please choose a vehicle type.', true);
    return false;
  }
  if (!plateImageData) {
    showToast('Please take a photo of the license plate.', true);
    return false;
  }
  if (!odometerImageData) {
    showToast('Please take a photo of the odometer.', true);
    return false;
  }
  const cfg = CFG[selectedType];
  for (const rowCfg of cfg.rows) {
    for (const t of [...rowCfg.left, ...rowCfg.right]) {
      const v = tyreValues[t.code];
      if (v === undefined || v === '' || isNaN(parseFloat(v))) {
        showToast(`Please enter the tread depth for ${t.label}.`, true);
        highlightTyre(t.code);
        document.getElementById(`input-${t.code}`).focus();
        return false;
      }
    }
  }
  return true;
}

async function submitInspection() {
  stopEspListening(true);
  if (!validateBeforeSubmit()) return;

  const cleanedTyres = {};
  Object.keys(tyreValues).forEach(code => {
    cleanedTyres[code] = parseFloat(parseFloat(tyreValues[code]).toFixed(2));
  });

  const payload = {
    id: editingId,
    vehicle_type: selectedType,
    plate_image: plateImageData,
    odometer_image: odometerImageData,
    route_type: routeType,
    tyres: cleanedTyres,
    tyre_readings: tyreReadings   // ESP32 tyres only: [r1..r5, avg]; server re-checks
  };

  const btn = document.getElementById('complete-btn');
  btn.disabled = true;
  btn.textContent = 'Saving...';

  try {
    const res = await fetch('/technician/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Save failed');
    showToast(editingId ? 'Inspection updated ✔' : 'Inspection added ✔');
    closeWizard();
    setTimeout(() => window.location.reload(), 500);
  } catch (err) {
    showToast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = '✓ Complete';
  }
}

async function editVehicle(id) {
  document.querySelectorAll('.card-menu-popover.open').forEach(el => el.classList.remove('open'));
  try {
    const res = await fetch(`/technician/vehicle/${id}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not load record');

    resetWizardState();
    editingId = id;
    selectedType = data.vehicle_type;
    plateImageData = data.plate_image || null;
    odometerImageData = data.odometer_image || null;
    tyreValues = data.tyres || {};
    tyreReadings = data.tyre_readings || {};
    routeType = data.route_type === 'trunk' ? 'trunk' : 'feeder';

    document.getElementById('wizard-title').textContent = 'Edit Inspection';
    document.querySelectorAll('.type-card').forEach(c => {
      c.classList.toggle('selected', c.dataset.type === selectedType);
    });

    if (plateImageData) {
      const pp = document.getElementById('plate-preview');
      pp.src = plateImageData; pp.style.display = 'block';
    }
    if (odometerImageData) {
      const op = document.getElementById('odometer-preview');
      op.src = odometerImageData; op.style.display = 'block';
    }

    buildTyreUI(selectedType);
    updateRouteTypeUI();
    markFilledDots();
    showStep(2);
    document.getElementById('wizard-overlay').classList.add('open');
  } catch (err) {
    showToast(err.message, true);
  }
}

async function deleteVehicle(id) {
  document.querySelectorAll('.card-menu-popover.open').forEach(el => el.classList.remove('open'));
  if (!confirm('Delete this vehicle inspection? This cannot be undone.')) return;
  try {
    const res = await fetch(`/technician/delete/${id}`, { method: 'POST' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Delete failed');
    document.querySelector(`.vehicle-card[data-id="${id}"]`).remove();
    showToast('Inspection deleted');
    if (document.querySelectorAll('.vehicle-card').length === 0) {
      window.location.reload();
    }
  } catch (err) {
    showToast(err.message, true);
  }
}
