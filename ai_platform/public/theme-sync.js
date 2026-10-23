/* Dashboard owns the theme, including when the assistant is opened separately. */
(() => {
  const root = document.documentElement;
  function sync() {
    let theme;
    try { theme = window.parent !== window ? window.parent.document.documentElement.dataset.theme : null; } catch { /* standalone */ }
    try { theme ||= localStorage.getItem('theme'); } catch { /* storage unavailable */ }
    if (!['dark', 'light'].includes(theme)) theme = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    if (!root.classList.contains(theme) || root.classList.contains(theme === 'dark' ? 'light' : 'dark')) {
      root.classList.remove('dark', 'light'); root.classList.add(theme);
    }
    root.style.colorScheme = theme;
    // Chainlit installs theme.json variables inline; update them as well as the class.
    const variables = window.theme?.[theme];
    if (variables) Object.entries(variables).forEach(([key, value]) => {
      if (root.style.getPropertyValue(key) !== value) root.style.setProperty(key, value);
    });
    try { if (localStorage.getItem('vite-ui-theme') !== theme) localStorage.setItem('vite-ui-theme', theme); } catch { /* storage unavailable */ }
  }
  sync();
  new MutationObserver(sync).observe(root, {attributes:true, attributeFilter:['class']});
  try {
    if (window.parent !== window) new MutationObserver(sync).observe(window.parent.document.documentElement, {attributes:true, attributeFilter:['data-theme']});
  } catch { /* standalone */ }
  window.addEventListener('storage', sync);
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', sync);
})();
