/* ==========================================================================
   Shared user-menu dropdown — click the name in the top bar to reveal a
   small "Log Out" popover, instead of showing Log Out as its own button.
   Included on every logged-in page (technician + all staff pages).
   ========================================================================== */
function toggleUserMenu(evt) {
  evt.stopPropagation();
  document.querySelectorAll('.user-menu-popover.open').forEach(el => {
    if (el.id !== 'user-menu-popover') el.classList.remove('open');
  });
  const popover = document.getElementById('user-menu-popover');
  if (popover) popover.classList.toggle('open');
}

document.addEventListener('click', () => {
  const popover = document.getElementById('user-menu-popover');
  if (popover) popover.classList.remove('open');
});
