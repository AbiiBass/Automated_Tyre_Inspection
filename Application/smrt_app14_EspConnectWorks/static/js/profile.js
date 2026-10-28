/* ==========================================================================
   SMRT My Profile Logic
   ========================================================================== */

function showToast(msg, isError) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.toggle('error', !!isError);
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2800);
}

function toggleProfilePasswordVisibility(inputId, btnEl) {
  const input = document.getElementById(inputId);
  const icon = btnEl.querySelector('i');
  const isHidden = input.type === 'password';
  input.type = isHidden ? 'text' : 'password';
  icon.classList.toggle('fa-eye', !isHidden);
  icon.classList.toggle('fa-eye-slash', isHidden);
}

function showProfileFormError(msg) {
  document.getElementById('profile-form-error-text').textContent = msg;
  document.getElementById('profile-form-error').style.display = 'flex';
}

function hideProfileFormError() {
  document.getElementById('profile-form-error').style.display = 'none';
}

let pendingProfileUpdate = null;

function submitProfileForm() {
  hideProfileFormError();

  const newUsername = document.getElementById('profile-username-input').value.trim();
  const newPassword = document.getElementById('profile-password-input').value;
  const confirmPassword = document.getElementById('profile-password-confirm-input').value;

  if (!newUsername) {
    showProfileFormError('Username can\'t be empty.');
    return;
  }
  if (newPassword || confirmPassword) {
    if (newPassword !== confirmPassword) {
      showProfileFormError('The two password fields don\'t match.');
      return;
    }
  }

  const usernameChanged = newUsername !== window.MY_USERNAME;
  const passwordChanged = !!newPassword;

  if (!usernameChanged && !passwordChanged) {
    showToast('No changes to save.');
    return;
  }

  let what;
  if (usernameChanged && passwordChanged) what = 'your username and password';
  else if (usernameChanged) what = 'your username';
  else what = 'your password';

  document.getElementById('profile-logout-warning-text').innerHTML =
    `Saving this will change ${what} and log you out immediately. ` +
    `You'll need to sign back in with your new details.`;

  pendingProfileUpdate = { username: newUsername, password: passwordChanged ? newPassword : '' };
  document.getElementById('profile-logout-warning-overlay').classList.add('open');
}

function closeProfileLogoutWarning() {
  document.getElementById('profile-logout-warning-overlay').classList.remove('open');
  pendingProfileUpdate = null;
}

async function confirmProfileUpdate() {
  if (!pendingProfileUpdate) return;
  const btn = document.getElementById('profile-confirm-btn');
  const originalHtml = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<i class="fa fa-spinner fa-spin" aria-hidden="true"></i> Saving...';

  try {
    const res = await fetch('/staff/profile/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(pendingProfileUpdate)
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not save your changes.');

    document.getElementById('profile-logout-warning-overlay').classList.remove('open');
    showToast('Saved — logging you out...');
    setTimeout(() => { window.location.href = '/login'; }, 900);
  } catch (err) {
    document.getElementById('profile-logout-warning-overlay').classList.remove('open');
    showProfileFormError(err.message);
    btn.disabled = false;
    btn.innerHTML = originalHtml;
  }
}
