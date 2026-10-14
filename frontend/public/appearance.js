// Runs before styles load; keep the key and choices aligned with appearance.ts.
(() => {
  let preference = 'system';
  try { preference = localStorage.getItem('notes-appearance') || 'system'; } catch {}
  const dark = preference === 'dark' || (preference !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
})();
