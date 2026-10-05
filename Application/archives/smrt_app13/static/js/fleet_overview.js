/* ==========================================================================
   SMRT Fleet Overview Logic
   ========================================================================== */

function showToast(msg, isError) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.toggle('error', !!isError);
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2800);
}

/* ---------------------------- Upload ---------------------------- */
let fleetSelectedFiles = [];

function onFleetFilesChosen(inputEl) {
  fleetSelectedFiles = Array.from(inputEl.files || []);
  const label = document.getElementById('fleet-upload-filenames');
  const btn = document.getElementById('fleet-upload-btn');
  if (fleetSelectedFiles.length === 0) {
    label.textContent = 'Choose .xlsx or .zip file(s)…';
    btn.disabled = true;
  } else {
    label.textContent = fleetSelectedFiles.length === 1
      ? fleetSelectedFiles[0].name
      : `${fleetSelectedFiles.length} files selected`;
    btn.disabled = false;
  }
}

async function uploadFleetFiles() {
  if (fleetSelectedFiles.length === 0) return;
  const btn = document.getElementById('fleet-upload-btn');
  const originalHtml = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<i class="fa fa-spinner fa-spin" aria-hidden="true"></i> Uploading...';

  const formData = new FormData();
  fleetSelectedFiles.forEach(f => formData.append('files', f));

  const resultBox = document.getElementById('fleet-upload-result');
  resultBox.innerHTML = '';

  try {
    const res = await fetch('/staff/fleet_overview/upload', { method: 'POST', body: formData });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Upload failed.');

    const parts = [];
    if (data.added > 0) {
      parts.push(`<span class="ok-count">${data.added} new record${data.added === 1 ? '' : 's'} added</span>`);
    }
    if (data.skipped_duplicates > 0) {
      parts.push(`<span class="skip-count">${data.skipped_duplicates} duplicate${data.skipped_duplicates === 1 ? '' : 's'} skipped</span>`);
    }
    resultBox.innerHTML = parts.length ? parts.join(' · ') : 'Nothing new to add — looks like this was already uploaded.';
    showToast(`Upload complete — ${data.added} new record${data.added === 1 ? '' : 's'} added.`);

    document.getElementById('fleet-upload-input').value = '';
    fleetSelectedFiles = [];
    document.getElementById('fleet-upload-filenames').textContent = 'Choose .xlsx or .zip file(s)…';

    setTimeout(() => window.location.reload(), 900);
  } catch (err) {
    resultBox.innerHTML = `<span class="fail-count">${err.message}</span>`;
    showToast(err.message, true);
  } finally {
    btn.disabled = fleetSelectedFiles.length === 0;
    btn.innerHTML = originalHtml;
  }
}

/* ---------------------------- Vehicle detail + charts ---------------------------- */
let fleetOdometerChartInstance = null;
let fleetTyreChartInstance = null;

const FLEET_CHART_PALETTE = [
  '#C8102E', '#A9772C', '#2E7D4F', '#1f6feb', '#8e44ad',
  '#e67e22', '#16a085', '#d35400', '#2c3e50', '#7f8c8d',
];

async function openFleetDetail(vehicleType, plate) {
  document.getElementById('fleet-detail-title').innerHTML =
    `<i class="fa fa-car" aria-hidden="true"></i> ${plate}`;
  document.getElementById('fleet-detail-loading').style.display = 'block';
  document.getElementById('fleet-detail-content').style.display = 'none';
  document.getElementById('fleet-detail-overlay').classList.add('open');

  try {
    const res = await fetch(`/staff/fleet_overview/vehicle?type=${encodeURIComponent(vehicleType)}&plate=${encodeURIComponent(plate)}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Could not load this vehicle's history.");

    document.getElementById('fleet-detail-title').innerHTML =
      `<i class="fa fa-car" aria-hidden="true"></i> ${data.plate} ` +
      `<span class="fleet-detail-subtitle">${data.vehicle_label}</span>`;

    renderFleetLatestGrid(data);
    renderFleetCharts(data);

    document.getElementById('fleet-detail-loading').style.display = 'none';
    document.getElementById('fleet-detail-content').style.display = 'block';
  } catch (err) {
    closeFleetDetail();
    showToast(err.message, true);
  }
}

function closeFleetDetail() {
  document.getElementById('fleet-detail-overlay').classList.remove('open');
}

function renderFleetLatestGrid(data) {
  const grid = document.getElementById('fleet-latest-grid');
  const items = [
    { label: 'Last Updated', value: data.latest.date || '—' },
    { label: 'Odometer', value: data.latest.odometer_text || '—' },
    { label: 'Entered By', value: data.latest.entered_by || '—' },
  ];
  if (data.route_type) {
    items.push({ label: 'Route', value: data.route_type === 'trunk' ? 'Trunk' : 'Feeder' });
  }

  let html = items.map(it =>
    `<div class="fleet-latest-item"><div class="col-label">${it.label}</div><div class="col-value">${it.value}</div></div>`
  ).join('');
  grid.innerHTML = html;

  const tyreEntries = Object.entries(data.latest.tyres || {});
  const tyreWrap = document.createElement('div');
  tyreWrap.className = 'fleet-tyre-chip-row';
  if (tyreEntries.length === 0) {
    tyreWrap.innerHTML = '<span class="route-na">No tyre readings in the latest snapshot.</span>';
  } else {
    tyreEntries.forEach(([code, val]) => {
      const flagged = Number(val) > 3.2;
      const chip = document.createElement('span');
      chip.className = 'tyre-chip' + (flagged ? ' flag' : '');
      chip.textContent = `${code} ${val} mm`;
      tyreWrap.appendChild(chip);
    });
  }
  grid.appendChild(tyreWrap);
}

function renderFleetCharts(data) {
  const unavailableBanner = document.getElementById('fleet-chart-unavailable');
  const chartBlocks = document.getElementById('fleet-chart-blocks');

  if (typeof Chart === 'undefined') {
    // The Chart.js CDN script didn't load (offline, blocked, CDN hiccup,
    // etc). Fail visibly with a helpful message instead of throwing
    // "Chart is not defined" and leaving the modal looking broken. The
    // canvases themselves are left untouched (just hidden) so charts can
    // still render normally the next time this modal opens, if Chart.js
    // becomes available again (e.g. after a page reload).
    if (unavailableBanner) unavailableBanner.style.display = 'block';
    if (chartBlocks) chartBlocks.style.display = 'none';
    return;
  }
  if (unavailableBanner) unavailableBanner.style.display = 'none';
  if (chartBlocks) chartBlocks.style.display = 'block';

  const labels = data.history.map(h => h.date);

  if (fleetOdometerChartInstance) fleetOdometerChartInstance.destroy();
  const odoCtx = document.getElementById('fleet-odometer-chart').getContext('2d');
  fleetOdometerChartInstance = new Chart(odoCtx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Odometer (km)',
        data: data.history.map(h => h.odometer_value),
        borderColor: '#C8102E',
        backgroundColor: 'rgba(200,16,46,0.12)',
        tension: 0.25,
        spanGaps: true,
        fill: true,
      }],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: { y: { beginAtZero: false } },
    },
  });

  const tyreCodes = [];
  data.history.forEach(h => {
    Object.keys(h.tyres || {}).forEach(code => {
      if (!tyreCodes.includes(code)) tyreCodes.push(code);
    });
  });

  const datasets = tyreCodes.map((code, i) => ({
    label: code,
    data: data.history.map(h => (h.tyres && h.tyres[code] !== undefined) ? h.tyres[code] : null),
    borderColor: FLEET_CHART_PALETTE[i % FLEET_CHART_PALETTE.length],
    tension: 0.25,
    spanGaps: true,
  }));

  if (fleetTyreChartInstance) fleetTyreChartInstance.destroy();
  const tyreCtx = document.getElementById('fleet-tyre-chart').getContext('2d');
  fleetTyreChartInstance = new Chart(tyreCtx, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true,
      plugins: { legend: { display: true, position: 'bottom' } },
      scales: { y: { beginAtZero: true } },
    },
  });
}

/* ---------------------------- Clear Fleet History ---------------------------- */
function openClearFleetWarning() {
  document.getElementById('clear-fleet-password-input').value = '';
  document.getElementById('clear-fleet-password-input').type = 'password';
  document.getElementById('clear-fleet-password-toggle-icon').className = 'fa fa-eye';
  document.getElementById('clear-fleet-error').style.display = 'none';
  document.getElementById('clear-fleet-warning-overlay').classList.add('open');
}

function closeClearFleetWarning() {
  document.getElementById('clear-fleet-warning-overlay').classList.remove('open');
}

function toggleClearFleetPasswordVisibility() {
  const input = document.getElementById('clear-fleet-password-input');
  const icon = document.getElementById('clear-fleet-password-toggle-icon');
  const isHidden = input.type === 'password';
  input.type = isHidden ? 'text' : 'password';
  icon.classList.toggle('fa-eye', !isHidden);
  icon.classList.toggle('fa-eye-slash', isHidden);
}

async function confirmClearFleet() {
  const password = document.getElementById('clear-fleet-password-input').value;
  const errorBox = document.getElementById('clear-fleet-error');
  const errorText = document.getElementById('clear-fleet-error-text');

  if (!password) {
    errorText.textContent = 'Please enter your password.';
    errorBox.style.display = 'flex';
    return;
  }

  const btn = document.getElementById('clear-fleet-confirm-btn');
  const originalHtml = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<i class="fa fa-spinner fa-spin" aria-hidden="true"></i> Deleting...';

  try {
    const res = await fetch('/staff/fleet_overview/clear', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    });
    const data = await res.json();
    if (!res.ok) {
      errorText.textContent = data.error || 'Incorrect password. Please try again.';
      errorBox.style.display = 'flex';
      btn.disabled = false;
      btn.innerHTML = originalHtml;
      return;
    }
    showToast('Fleet history cleared.');
    closeClearFleetWarning();
    setTimeout(() => window.location.reload(), 500);
  } catch (err) {
    errorText.textContent = 'Something went wrong. Please try again.';
    errorBox.style.display = 'flex';
    btn.disabled = false;
    btn.innerHTML = originalHtml;
  }
}
