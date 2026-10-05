/* ==========================================================================
   SMRT Staff Dashboard Logic
   ========================================================================== */

function showToast(msg, isError) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.toggle('error', !!isError);
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2800);
}

/* ---------------------------- Filters (multi-select) ---------------------------- */
const selectedTypes = new Set();
const selectedRoutes = new Set();
let currentSort = 'last_edited';

function togglePill(kind, value) {
  if (kind === 'all') {
    selectedTypes.clear();
    selectedRoutes.clear();
  } else if (kind === 'type') {
    if (selectedTypes.has(value)) selectedTypes.delete(value);
    else selectedTypes.add(value);
  } else if (kind === 'route') {
    if (selectedRoutes.has(value)) selectedRoutes.delete(value);
    else selectedRoutes.add(value);
  }

  syncPillButtons();
  applyFilters();

  // Cars don't have a route type — warn (but still apply) if both are active.
  if (selectedTypes.has('car') && selectedRoutes.size > 0) {
    document.getElementById('filter-warning-overlay').classList.add('open');
  }
}

function closeFilterWarning() {
  document.getElementById('filter-warning-overlay').classList.remove('open');
}

function syncPillButtons() {
  const allBtn = document.querySelector('.filter-pill[data-pill="all"]');
  const noneSelected = selectedTypes.size === 0 && selectedRoutes.size === 0;
  if (allBtn) allBtn.classList.toggle('active', noneSelected);

  document.querySelectorAll('#type-pill-group .filter-pill[data-pill^="type:"]').forEach(btn => {
    const val = btn.dataset.pill.split(':')[1];
    btn.classList.toggle('active', selectedTypes.has(val));
  });
  document.querySelectorAll('#route-pill-group .filter-pill[data-pill^="route:"]').forEach(btn => {
    const val = btn.dataset.pill.split(':')[1];
    btn.classList.toggle('active', selectedRoutes.has(val));
  });
}

function applyFilters() {
  const cards = document.querySelectorAll('.inspection-card');
  let visibleCount = 0;

  cards.forEach(card => {
    const type = card.dataset.type;
    const route = card.dataset.route;
    const typeMatch = selectedTypes.size === 0 || selectedTypes.has(type);
    const routeMatch = selectedRoutes.size === 0 || selectedRoutes.has(route);
    const match = typeMatch && routeMatch;
    card.style.display = match ? '' : 'none';
    if (match) visibleCount++;
  });

  const noMatches = document.getElementById('no-filter-matches');
  if (noMatches) noMatches.style.display = (cards.length > 0 && visibleCount === 0) ? 'block' : 'none';
}

/* ---------------------------- Sorting ---------------------------- */
function applySort(value) {
  currentSort = value;
  const grid = document.getElementById('staff-grid');
  if (!grid) return;
  const cards = Array.from(grid.querySelectorAll('.inspection-card'));

  cards.sort((a, b) => {
    if (value === 'plate' || value === 'plate_desc') {
      const pa = a.dataset.plate || '';
      const pb = b.dataset.plate || '';
      // Vehicles with no plate text yet always sort to the end, regardless
      // of A-Z vs Z-A direction, so blanks don't jump around confusingly.
      if (pa && !pb) return -1;
      if (!pa && pb) return 1;
      const cmp = pa.localeCompare(pb);
      return value === 'plate' ? cmp : -cmp;
    }
    // last_edited / last_edited_oldest
    const cmp = (a.dataset.lastEdited || '').localeCompare(b.dataset.lastEdited || '');
    return value === 'last_edited_oldest' ? cmp : -cmp;
  });

  cards.forEach(card => grid.appendChild(card));
}

/* ---------------------------- Card expand/collapse ---------------------------- */
function toggleCard(id) {
  const card = document.querySelector(`.inspection-card[data-id="${id}"]`);
  if (!card) return;
  const expanded = card.classList.toggle('expanded');
  const header = card.querySelector('.ic-header');
  header.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  syncExpandAllButton();
}

function toggleExpandAll() {
  const cards = document.querySelectorAll('.inspection-card');
  if (cards.length === 0) return;
  const anyCollapsed = Array.from(cards).some(c => !c.classList.contains('expanded'));
  cards.forEach(card => {
    card.classList.toggle('expanded', anyCollapsed);
    card.querySelector('.ic-header').setAttribute('aria-expanded', anyCollapsed ? 'true' : 'false');
  });
  syncExpandAllButton();
}

function syncExpandAllButton() {
  const cards = document.querySelectorAll('.inspection-card');
  const icon = document.getElementById('expand-toggle-icon');
  const label = document.getElementById('expand-toggle-label');
  if (!icon || !label || cards.length === 0) return;
  const allExpanded = Array.from(cards).every(c => c.classList.contains('expanded'));
  icon.className = allExpanded ? 'fa fa-compress' : 'fa fa-expand';
  label.textContent = allExpanded ? 'Collapse All' : 'Expand All';
}

/* ---------------------------- Inline text + route type editing ---------------------------- */
/* ---------------------------- Ask AI to read the plate photo ---------------------------- */
async function askAIForSingle(task, id, btnEl) {
  if (window.ocrBatchRunning) {
    showToast('A bulk "Convert All" job is running — please wait for it to finish.', true);
    return;
  }

  const cfg = task === 'plate'
    ? { endpoint: 'ocr_plate', inputClass: '.plate-text-input', field: 'plate_text', resultKey: 'plate', label: 'plate' }
    : { endpoint: 'ocr_odometer', inputClass: '.odometer-text-input', field: 'odometer_text', resultKey: 'reading', label: 'odometer' };

  const card = btnEl.closest('.inspection-card');
  const input = card ? card.querySelector(cfg.inputClass) : null;
  if (!input) return;

  const originalHtml = btnEl.innerHTML;
  btnEl.disabled = true;
  btnEl.innerHTML = '<i class="fa fa-spinner fa-spin" aria-hidden="true"></i> Reading...';

  try {
    const res = await fetch(`/staff/${cfg.endpoint}/${id}`, { method: 'POST' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `Could not read the ${cfg.label} photo.`);
    const value = data[cfg.resultKey];
    if (!value) throw new Error(data.error || `AI couldn't read this ${cfg.label} photo.`);

    input.value = value;
    await saveText(id, cfg.field, input);
    showToast('AI reading applied — please double-check it against the photo.');
  } catch (err) {
    showToast(err.message, true);
  } finally {
    btnEl.disabled = false;
    btnEl.innerHTML = originalHtml;
  }
}

async function askAIForPlate(id, btnEl) {
  return askAIForSingle('plate', id, btnEl);
}

async function askAIForOdometer(id, btnEl) {
  return askAIForSingle('odometer', id, btnEl);
}

async function saveText(id, field, inputEl) {
  const value = inputEl.value.trim();
  try {
    const res = await fetch(`/staff/update_text/${id}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [field]: value })
    });
    if (!res.ok) throw new Error('Could not save');

    inputEl.classList.toggle('needs-fill', value === '');

    const hintId = field === 'plate_text' ? `hint-plate-${id}` : `hint-odo-${id}`;
    const hint = document.getElementById(hintId);
    if (hint) {
      hint.textContent = value ? 'Saved ✓' : '';
      if (value) setTimeout(() => { hint.textContent = ''; }, 1800);
    }

    // Reflect the change in the collapsed header immediately.
    const card = inputEl.closest('.inspection-card');
    if (card) {
      const liveSpan = card.querySelector(`.ic-header [data-live="${field}"]`);
      if (liveSpan) {
        if (field === 'odometer_text') {
          liveSpan.textContent = value ? `${value} km` : '—';
        } else {
          liveSpan.textContent = value || '—';
        }
      }
      if (field === 'plate_text') {
        card.dataset.plate = value.toLowerCase();
        if (currentSort === 'plate' || currentSort === 'plate_desc') applySort(currentSort);
      }
      updateCardCompletionState(card);
    }
  } catch (err) {
    showToast('Could not save text. Please try again.', true);
  }
}

function updateCardCompletionState(card) {
  const plateInput = card.querySelector('.plate-text-input');
  const odoInput = card.querySelector('.odometer-text-input');
  const plateVal = plateInput ? plateInput.value.trim() : '';
  const odoVal = odoInput ? odoInput.value.trim() : '';
  card.classList.toggle('complete', !!plateVal && !!odoVal);
}

async function onStaffRouteSliderChange(id, checkboxEl) {
  const routeType = checkboxEl.checked ? 'trunk' : 'feeder';
  await saveRouteType(id, routeType, checkboxEl);
}

async function saveRouteType(id, routeType, sourceEl) {
  try {
    const res = await fetch(`/staff/update_text/${id}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ route_type: routeType })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not save route type');

    const card = sourceEl.closest('.inspection-card');
    if (card) {
      const wrap = card.querySelector('.route-edit-control');
      if (wrap) {
        wrap.querySelector('.route-slider-label-feeder').classList.toggle('active', routeType === 'feeder');
        wrap.querySelector('.route-slider-label-trunk').classList.toggle('active', routeType === 'trunk');
        const checkbox = wrap.querySelector('input[type="checkbox"]');
        if (checkbox) checkbox.checked = (routeType === 'trunk');
      }
      card.dataset.route = routeType;

      const badge = card.querySelector('[data-route-badge]');
      if (badge) {
        badge.textContent = routeType === 'trunk' ? 'Trunk' : 'Feeder';
        badge.classList.remove('route-badge-feeder', 'route-badge-trunk');
        badge.classList.add(`route-badge-${routeType}`);
      }
      applyFilters();
    }

    const hint = document.getElementById(`hint-route-${id}`);
    if (hint) {
      hint.textContent = 'Saved ✓';
      setTimeout(() => { hint.textContent = ''; }, 1800);
    }
  } catch (err) {
    // Revert the switch to its previous position on failure.
    if (sourceEl && sourceEl.type === 'checkbox') sourceEl.checked = !sourceEl.checked;
    showToast(err.message || 'Could not save route type.', true);
  }
}

/* ---------------------------- Convert All Plates (bulk AI) ---------------------------- */
window.ocrBatchRunning = false;
let convertAllPollTimer = null;
let convertAllAppliedIds = new Set();
let convertAllTask = 'plate';

const CONVERT_ALL_CFG = {
  plate: {
    label: 'plate number', article: 'a', photoLabel: 'plate photo', imgClass: '.plate-img',
    inputClass: '.plate-text-input', title: 'Convert All Plates with AI',
    progressTitle: 'Converting Plates…', buttonId: 'convert-all-plate-btn',
    buttonLabel: 'Convert All Plates', toastNoun: 'plate',
  },
  odometer: {
    label: 'odometer reading', article: 'an', photoLabel: 'odometer photo', imgClass: '.odo-img',
    inputClass: '.odometer-text-input', title: 'Convert All Odometers with AI',
    progressTitle: 'Converting Odometers…', buttonId: 'convert-all-odometer-btn',
    buttonLabel: 'Convert All Odometers', toastNoun: 'odometer reading',
  },
};

function getVisibleCardIds() {
  return Array.from(document.querySelectorAll('.inspection-card'))
    .filter(c => c.style.display !== 'none')
    .map(c => c.dataset.id);
}

function getConvertAllCandidateIds(task) {
  // Mirrors the server's own eligibility rule (has the relevant photo, no
  // text yet) just so the confirmation dialog can show an honest count —
  // the server re-checks this itself regardless.
  const cfg = CONVERT_ALL_CFG[task];
  return Array.from(document.querySelectorAll('.inspection-card'))
    .filter(c => c.style.display !== 'none')
    .filter(c => {
      const img = c.querySelector(cfg.imgClass);
      const input = c.querySelector(cfg.inputClass);
      return img && input && !input.value.trim();
    })
    .map(c => c.dataset.id);
}

function openConvertAllWarning(task) {
  if (window.ocrBatchRunning) {
    showToast('A conversion is already running.', true);
    return;
  }
  convertAllTask = task;
  const cfg = CONVERT_ALL_CFG[task];
  const candidateCount = getConvertAllCandidateIds(task).length;

  document.getElementById('convert-all-warning-title').innerHTML =
    `<i class="fa fa-magic" aria-hidden="true"></i> ${cfg.title}`;

  const scopeText = document.getElementById('convert-all-scope-text');
  if (candidateCount === 0) {
    scopeText.innerHTML = `Every vehicle currently shown already has ${cfg.article} ${cfg.label} ` +
      `entered (or has no ${cfg.photoLabel}) — there's nothing to convert right now.`;
  } else {
    scopeText.innerHTML = `This will use AI to read the ${cfg.photoLabel} for <strong>${candidateCount} ` +
      `vehicle${candidateCount === 1 ? '' : 's'}</strong> currently shown that ${candidateCount === 1 ? "doesn't" : "don't"} ` +
      `already have ${cfg.article} ${cfg.label} typed in.`;
  }
  document.getElementById('convert-all-warning-overlay').classList.add('open');
}

function closeConvertAllWarning() {
  document.getElementById('convert-all-warning-overlay').classList.remove('open');
}

async function startConvertAll() {
  const task = convertAllTask;
  const ids = getVisibleCardIds();
  closeConvertAllWarning();

  if (ids.length === 0) {
    showToast('No vehicles to convert.', true);
    return;
  }

  window.ocrBatchRunning = true;
  convertAllAppliedIds = new Set();
  setConvertAllButtonBusy(task, true);
  showConvertAllProgress(task);
  updateConvertAllProgressUI({ processed: 0, total: ids.length, done: false });

  try {
    const res = await fetch('/staff/ocr_all/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ vehicle_ids: ids, task })
    });
    const data = await res.json();
    if (!res.ok) {
      window.ocrBatchRunning = false;
      setConvertAllButtonBusy(task, false);
      hideConvertAllProgress();
      showToast(data.error || 'Could not start conversion.', true);
      return;
    }
    pollConvertAllStatus(data.job_id, task);
  } catch (err) {
    window.ocrBatchRunning = false;
    setConvertAllButtonBusy(task, false);
    hideConvertAllProgress();
    showToast('Could not start conversion. Please try again.', true);
  }
}

function pollConvertAllStatus(jobId, task) {
  clearTimeout(convertAllPollTimer);

  const poll = async () => {
    try {
      const res = await fetch(`/staff/ocr_all/status/${jobId}`);
      const data = await res.json();
      if (!res.ok) {
        finishConvertAll(task, { error: data.error || 'Lost track of the conversion job.' });
        return;
      }

      updateConvertAllProgressUI(data);

      // Apply every result we haven't already applied to the DOM yet —
      // the batch job already persisted these server-side, so this is a
      // display-only update, not another save.
      data.results.forEach(r => {
        if (convertAllAppliedIds.has(r.id)) return;
        convertAllAppliedIds.add(r.id);
        if (r.value) applyOcrResultToCard(task, r.id, r.value);
      });

      if (data.done) {
        finishConvertAll(task, data);
      } else {
        convertAllPollTimer = setTimeout(poll, 900);
      }
    } catch (err) {
      convertAllPollTimer = setTimeout(poll, 1500);
    }
  };

  poll();
}

function finishConvertAll(task, data) {
  window.ocrBatchRunning = false;
  setConvertAllButtonBusy(task, false);
  const cfg = CONVERT_ALL_CFG[task];

  const summary = document.getElementById('convert-progress-summary');
  const hideBtn = document.getElementById('convert-progress-hide-btn');
  const doneBtn = document.getElementById('convert-progress-done-btn');

  if (data.error || data.fatal_error) {
    summary.innerHTML = `<span class="fail-count">${data.error || data.fatal_error}</span>`;
    showToast(data.error || data.fatal_error, true);
  } else {
    const results = data.results || [];
    const okCount = results.filter(r => r.value).length;
    const failCount = results.filter(r => r.error).length;
    summary.innerHTML =
      `<span class="ok-count">${okCount} converted</span>` +
      (failCount ? ` · <span class="fail-count">${failCount} couldn't be read</span>` : '');
    showToast(`AI ${cfg.toastNoun} conversion finished — ${okCount} converted. Please double-check the results.`);
  }

  hideBtn.style.display = 'none';
  doneBtn.style.display = 'inline-flex';
}

function setConvertAllButtonBusy(task, isBusy) {
  const cfg = CONVERT_ALL_CFG[task];
  const btn = document.getElementById(cfg.buttonId);
  if (!btn) return;
  btn.disabled = isBusy;
  btn.innerHTML = isBusy
    ? '<i class="fa fa-spinner fa-spin" aria-hidden="true"></i> Converting…'
    : `<i class="fa fa-magic" aria-hidden="true"></i> ${cfg.buttonLabel}`;
}

function showConvertAllProgress(task) {
  document.getElementById('convert-progress-title').innerHTML =
    `<i class="fa fa-magic" aria-hidden="true"></i> ${CONVERT_ALL_CFG[task].progressTitle}`;
  document.getElementById('convert-progress-hide-btn').style.display = 'inline-flex';
  document.getElementById('convert-progress-done-btn').style.display = 'none';
  document.getElementById('convert-progress-summary').innerHTML = '';
  document.getElementById('convert-all-progress-overlay').classList.add('open');
}

function hideConvertAllProgress() {
  // Only hides the modal - polling (if a job is still running) keeps going
  // in the background regardless, via the standing setTimeout chain.
  document.getElementById('convert-all-progress-overlay').classList.remove('open');
}

function updateConvertAllProgressUI(data) {
  const { processed, total } = data;
  const pct = total > 0 ? Math.round((processed / total) * 100) : 0;
  document.getElementById('convert-progress-bar-fill').style.width = pct + '%';
  document.getElementById('convert-progress-label').textContent =
    data.done ? `Finished — ${processed} of ${total}` : `Processing ${processed} of ${total}…`;
}

function applyOcrResultToCard(task, id, value) {
  const card = document.querySelector(`.inspection-card[data-id="${id}"]`);
  if (!card) return;
  const cfg = CONVERT_ALL_CFG[task];
  const input = card.querySelector(cfg.inputClass);
  if (input) {
    input.value = value;
    input.classList.remove('needs-fill');
  }

  if (task === 'plate') {
    card.dataset.plate = value.toLowerCase();
  }

  const liveSpan = card.querySelector(`.ic-header [data-live="${task === 'plate' ? 'plate_text' : 'odometer_text'}"]`);
  if (liveSpan) liveSpan.textContent = value ? (task === 'odometer' ? `${value} km` : value) : '—';

  const hint = document.getElementById(task === 'plate' ? `hint-plate-${id}` : `hint-odo-${id}`);
  if (hint) {
    hint.textContent = 'AI filled this in ✓';
    setTimeout(() => { hint.textContent = ''; }, 2500);
  }

  if (task === 'plate' && (currentSort === 'plate' || currentSort === 'plate_desc')) applySort(currentSort);
  updateCardCompletionState(card);
}

/* ---------------------------- Download spreadsheet ---------------------------- */
async function downloadSheet() {
  const dateFilter = window.CURRENT_DATE_FILTER || 'all';
  const params = new URLSearchParams();
  params.set('date_filter', dateFilter);
  params.set('sort', currentSort);
  if (selectedTypes.size > 0) params.set('types', Array.from(selectedTypes).join(','));
  if (selectedRoutes.size > 0) params.set('routes', Array.from(selectedRoutes).join(','));

  try {
    const res = await fetch(`/staff/download?${params.toString()}`);
    if (!res.ok) {
      const data = await res.json();
      document.querySelectorAll('.plate-text-input, .odometer-text-input').forEach(inp => {
        if (!inp.value.trim()) {
          inp.classList.add('needs-fill');
          const card = inp.closest('.inspection-card');
          if (card && !card.classList.contains('expanded')) {
            card.classList.add('expanded');
            card.querySelector('.ic-header').setAttribute('aria-expanded', 'true');
          }
        }
      });
      syncExpandAllButton();
      showToast(data.error || 'Please fill in all text fields before downloading.', true);
      return;
    }
    const blob = await res.blob();
    const disposition = res.headers.get('Content-Disposition') || '';
    let filename = 'vehicle_inspections.xlsx';
    const match = disposition.match(/filename="?([^"]+)"?/);
    if (match) filename = match[1];

    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
    showToast('Spreadsheet downloaded ✔');
  } catch (err) {
    showToast('Download failed. Please try again.', true);
  }
}

/* ---------------------------- Clear All Data (danger zone) ---------------------------- */
function openClearAllWarning() {
  document.getElementById('clear-warning-overlay').classList.add('open');
}

function closeClearAllWarning() {
  document.getElementById('clear-warning-overlay').classList.remove('open');
}

function proceedToPasswordConfirm() {
  closeClearAllWarning();
  const pwInput = document.getElementById('clear-password-input');
  pwInput.value = '';
  pwInput.type = 'password';
  document.getElementById('clear-password-toggle-icon').className = 'fa fa-eye';
  document.getElementById('clear-password-error').style.display = 'none';
  document.getElementById('clear-password-overlay').classList.add('open');
  setTimeout(() => pwInput.focus(), 100);
}

function closeClearPasswordModal() {
  document.getElementById('clear-password-overlay').classList.remove('open');
}

function toggleClearPasswordVisibility() {
  const pwInput = document.getElementById('clear-password-input');
  const icon = document.getElementById('clear-password-toggle-icon');
  const isHidden = pwInput.type === 'password';
  pwInput.type = isHidden ? 'text' : 'password';
  icon.classList.toggle('fa-eye', !isHidden);
  icon.classList.toggle('fa-eye-slash', isHidden);
}

async function confirmClearAll() {
  const pwInput = document.getElementById('clear-password-input');
  const password = pwInput.value;
  const errorBox = document.getElementById('clear-password-error');
  const errorText = document.getElementById('clear-password-error-text');
  const confirmBtn = document.getElementById('clear-confirm-btn');

  if (!password) {
    errorText.textContent = 'Please enter your password.';
    errorBox.style.display = 'flex';
    return;
  }

  confirmBtn.disabled = true;
  confirmBtn.innerHTML = '<i class="fa fa-spinner fa-spin" aria-hidden="true"></i> Deleting...';

  try {
    const res = await fetch('/staff/clear_all', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password })
    });
    const data = await res.json();
    if (!res.ok) {
      errorText.textContent = data.error || 'Incorrect password. Please try again.';
      errorBox.style.display = 'flex';
      confirmBtn.disabled = false;
      confirmBtn.innerHTML = '<i class="fa fa-trash" aria-hidden="true"></i> Permanently Delete';
      return;
    }
    showToast('All inspection data has been deleted.');
    closeClearPasswordModal();
    setTimeout(() => window.location.reload(), 500);
  } catch (err) {
    errorText.textContent = 'Something went wrong. Please try again.';
    errorBox.style.display = 'flex';
    confirmBtn.disabled = false;
    confirmBtn.innerHTML = '<i class="fa fa-trash" aria-hidden="true"></i> Permanently Delete';
  }
}

document.addEventListener('DOMContentLoaded', () => {
  syncPillButtons();
  syncExpandAllButton();
  document.querySelectorAll('.inspection-card').forEach(updateCardCompletionState);
});
