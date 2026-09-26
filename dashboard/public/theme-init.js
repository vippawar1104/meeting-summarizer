// Apply the saved theme before first paint so dark mode never flashes white.
// Kept as its own file (not inline) so the Content-Security-Policy can forbid inline scripts.
try {
  var t = localStorage.getItem("reviewly-theme");
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
} catch (e) {}
