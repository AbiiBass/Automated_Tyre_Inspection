/* ==========================================================================
   Shared "last edited" time display — lets the person switch between a
   friendly relative format ("5 min ago") and the exact timestamp, on both
   the technician and staff pages. Preference is remembered per-browser.
   ========================================================================== */

const TIME_FORMAT_STORAGE_KEY = 'smrt_time_format'; // 'relative' | 'exact'

function getTimeFormatPref() {
  try {
    return localStorage.getItem(TIME_FORMAT_STORAGE_KEY) || 'relative';
  } catch (e) {
    return 'relative';
  }
}

function setTimeFormatPref(mode) {
  try {
    localStorage.setItem(TIME_FORMAT_STORAGE_KEY, mode);
  } catch (e) { /* ignore (e.g. storage disabled) */ }
}

function formatRelativeTime(dateStr) {
  // dateStr is "YYYY-MM-DD HH:MM:SS" as stored by the server.
  const then = new Date(dateStr.replace(' ', 'T'));
  if (isNaN(then.getTime())) return dateStr;

  const now = new Date();
  let diffSec = Math.round((now - then) / 1000);
  if (diffSec < 0) diffSec = 0;

  if (diffSec < 45) return 'Just now';
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin} min${diffMin === 1 ? '' : 's'} ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr} hour${diffHr === 1 ? '' : 's'} ago`;
  const diffDay = Math.round(diffHr / 24);
  if (diffDay === 1) return 'Yesterday';
  if (diffDay < 7) return `${diffDay} days ago`;
  const diffWeek = Math.round(diffDay / 7);
  if (diffWeek < 5) return `${diffWeek} week${diffWeek === 1 ? '' : 's'} ago`;
  const diffMonth = Math.round(diffDay / 30);
  if (diffMonth < 12) return `${diffMonth} month${diffMonth === 1 ? '' : 's'} ago`;
  const diffYear = Math.round(diffDay / 365);
  return `${diffYear} year${diffYear === 1 ? '' : 's'} ago`;
}

function applyTimeFormat() {
  const mode = getTimeFormatPref();
  document.querySelectorAll('[data-timestamp]').forEach(el => {
    const raw = el.getAttribute('data-timestamp');
    if (!raw) return;
    el.textContent = mode === 'exact' ? raw : formatRelativeTime(raw);
    el.title = mode === 'exact' ? formatRelativeTime(raw) : raw;
  });
  document.querySelectorAll('.time-format-toggle').forEach(btn => {
    btn.classList.toggle('is-exact', mode === 'exact');
    const label = btn.querySelector('.tf-label');
    if (label) label.textContent = mode === 'exact' ? 'Exact Time' : 'Relative Time';
  });
}

function toggleTimeFormat() {
  const current = getTimeFormatPref();
  setTimeFormatPref(current === 'exact' ? 'relative' : 'exact');
  applyTimeFormat();
}

document.addEventListener('DOMContentLoaded', () => {
  applyTimeFormat();
  // Keep relative labels ("5 min ago") fresh without needing a reload.
  setInterval(() => {
    if (getTimeFormatPref() === 'relative') applyTimeFormat();
  }, 30000);
});
