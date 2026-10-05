function showToast(msg, isError) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.toggle('error', !!isError);
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2800);
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
  } catch (err) {
    showToast('Could not save text. Please try again.', true);
  }
}

async function downloadSheet() {
  const type = window.CURRENT_FILTER || 'all';
  const dateFilter = window.CURRENT_DATE_FILTER || 'all';
  try {
    const res = await fetch(`/staff/download?type=${encodeURIComponent(type)}&date_filter=${encodeURIComponent(dateFilter)}`);
    if (!res.ok) {
      const data = await res.json();
      // Highlight every input still missing text, and expand its card so
      // the person can see exactly what needs filling in.
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
