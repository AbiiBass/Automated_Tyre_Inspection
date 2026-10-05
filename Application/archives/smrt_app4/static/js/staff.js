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
    if (value === 'plate') {
      const pa = a.dataset.plate || '';
      const pb = b.dataset.plate || '';
      if (pa && !pb) return -1;
      if (!pa && pb) return 1;
      return pa.localeCompare(pb);
    }
    // last_edited: newest first
    return (b.dataset.lastEdited || '').localeCompare(a.dataset.lastEdited || '');
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
        if (currentSort === 'plate') applySort('plate');
      }
    }
  } catch (err) {
    showToast('Could not save text. Please try again.', true);
  }
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
});
