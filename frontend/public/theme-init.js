// Apply the saved/system theme before first paint (no light flash in dark mode). A separate
// file, not an inline <script>, so the Content-Security-Policy can forbid inline scripts.
try {
  const t = localStorage.getItem('theme');
  const dark =
    t === 'dark' || (t !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.classList.toggle('dark', dark);
} catch {
  /* storage blocked: system theme via CSS default */
}
