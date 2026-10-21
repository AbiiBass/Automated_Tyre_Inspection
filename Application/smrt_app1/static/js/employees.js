/* ==========================================================================
   SMRT Manage Employees Logic
   ========================================================================== */

function showToast(msg, isError) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.toggle('error', !!isError);
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2800);
}

let editingUsername = null; // null = Add mode, otherwise the username being edited
let pendingDeleteUsername = null;

/* ---------------------------- Add / Edit modal ---------------------------- */
function openAddEmployeeModal() {
  editingUsername = null;
  document.getElementById('employee-modal-title').innerHTML =
    '<i class="fa fa-user-plus" aria-hidden="true"></i> Add Employee';
  document.getElementById('employee-name-input').value = '';
  document.getElementById('employee-username-input').value = '';
  document.getElementById('employee-role-select').value = 'technician';
  document.getElementById('employee-password-hint').style.display = 'block';
  hideEmployeeFormError();
  document.getElementById('employee-modal-overlay').classList.add('open');
}

function openEditEmployeeModal(username, name, role) {
  editingUsername = username;
  document.getElementById('employee-modal-title').innerHTML =
    '<i class="fa fa-pencil" aria-hidden="true"></i> Edit Employee';
  document.getElementById('employee-name-input').value = name;
  document.getElementById('employee-username-input').value = username;
  document.getElementById('employee-role-select').value = role;
  document.getElementById('employee-password-hint').style.display = 'none';
  hideEmployeeFormError();
  document.getElementById('employee-modal-overlay').classList.add('open');
}

function closeEmployeeModal() {
  document.getElementById('employee-modal-overlay').classList.remove('open');
}

function showEmployeeFormError(msg) {
  document.getElementById('employee-form-error-text').textContent = msg;
  document.getElementById('employee-form-error').style.display = 'flex';
}

function hideEmployeeFormError() {
  document.getElementById('employee-form-error').style.display = 'none';
}

async function submitEmployeeForm() {
  const name = document.getElementById('employee-name-input').value.trim();
  const username = document.getElementById('employee-username-input').value.trim();
  const role = document.getElementById('employee-role-select').value;

  if (!name) { showEmployeeFormError('Please enter a full name.'); return; }
  if (!username) { showEmployeeFormError('Please enter a username.'); return; }

  const submitBtn = document.getElementById('employee-submit-btn');
  const originalHtml = submitBtn.innerHTML;
  submitBtn.disabled = true;
  submitBtn.innerHTML = '<i class="fa fa-spinner fa-spin" aria-hidden="true"></i> Saving...';

  const url = editingUsername
    ? `/staff/employees/edit/${encodeURIComponent(editingUsername)}`
    : '/staff/employees/add';

  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, username, role })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not save this employee.');

    showToast(editingUsername ? 'Employee updated ✔' : 'Employee added ✔');
    closeEmployeeModal();
    setTimeout(() => window.location.reload(), 400);
  } catch (err) {
    showEmployeeFormError(err.message);
  } finally {
    submitBtn.disabled = false;
    submitBtn.innerHTML = originalHtml;
  }
}

/* ---------------------------- Delete ---------------------------- */
function deleteEmployee(username, name) {
  pendingDeleteUsername = username;
  document.getElementById('delete-employee-text').innerHTML =
    `Are you sure you want to delete <strong>${name}</strong> (${username})? This cannot be undone.`;
  document.getElementById('delete-employee-overlay').classList.add('open');
}

function closeDeleteEmployeeModal() {
  document.getElementById('delete-employee-overlay').classList.remove('open');
  pendingDeleteUsername = null;
}

async function confirmDeleteEmployee() {
  if (!pendingDeleteUsername) return;
  const btn = document.getElementById('delete-employee-confirm-btn');
  const originalHtml = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<i class="fa fa-spinner fa-spin" aria-hidden="true"></i> Deleting...';

  try {
    const res = await fetch(`/staff/employees/delete/${encodeURIComponent(pendingDeleteUsername)}`, {
      method: 'POST'
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not delete this employee.');

    showToast('Employee deleted');
    closeDeleteEmployeeModal();
    setTimeout(() => window.location.reload(), 400);
  } catch (err) {
    showToast(err.message, true);
    btn.disabled = false;
    btn.innerHTML = originalHtml;
  }
}
