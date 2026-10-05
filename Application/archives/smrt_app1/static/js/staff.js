function showToast(msg, isError) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.toggle('error', !!isError);
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2800);
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
  try {
    const res = await fetch(`/staff/download?type=${encodeURIComponent(type)}`);
    if (!res.ok) {
      const data = await res.json();
      // Highlight every input still missing text
      document.querySelectorAll('.plate-text-input, .odometer-text-input').forEach(inp => {
        if (!inp.value.trim()) inp.classList.add('needs-fill');
      });
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
