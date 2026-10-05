/* ==========================================================================
   SMRT Technician App Logic
   ========================================================================== */

let currentStep = 1;
let selectedType = null;
let plateImageData = null;
let odometerImageData = null;
let editingId = null;
let tyreValues = {};
let activeCameraMode = null; // 'plate' | 'odometer'
let mediaStream = null;

const CFG = window.VEHICLE_CONFIG;

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
  document.getElementById('wizard-title').textContent = 'New Inspection';
  document.querySelectorAll('.type-card').forEach(c => c.classList.remove('selected'));
  document.getElementById('plate-preview').style.display = 'none';
  document.getElementById('odometer-preview').style.display = 'none';
  document.getElementById('plate-preview').src = '';
  document.getElementById('odometer-preview').src = '';
  showStep(1);
}

function openAddWizard() {
  resetWizardState();
  document.getElementById('wizard-overlay').classList.add('open');
}

function closeWizard() {
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
    showStep(2);
  }, 150);
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

  input.addEventListener('focus', () => highlightTyre(tyre.code));
  input.addEventListener('input', () => {
    tyreValues[tyre.code] = input.value;
  });

  const unit = document.createElement('span');
  unit.className = 'unit';
  unit.textContent = 'mm';

  wrap.appendChild(input);
  wrap.appendChild(unit);
  field.appendChild(label);
  field.appendChild(wrap);
  return field;
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
    tyres: cleanedTyres
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
