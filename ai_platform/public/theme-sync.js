/* The dashboard is the single theme control, including after React remounts. */
(() => {
  function start() {
    const root = document.documentElement;
    if (!root) return;
    function sync() {
      let theme;
      try { theme = window.parent !== window ? window.parent.document.documentElement?.dataset.theme : null; } catch { /* standalone */ }
      try { theme ||= localStorage.getItem('theme'); } catch { /* storage unavailable */ }
      if (!['dark', 'light'].includes(theme)) theme = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
      if (!root.classList.contains(theme) || root.classList.contains(theme === 'dark' ? 'light' : 'dark')) {
        root.classList.remove('dark', 'light'); root.classList.add(theme);
      }
      if (root.dataset.oceanTheme !== theme) root.dataset.oceanTheme = theme;
      if (root.style.colorScheme !== theme) root.style.colorScheme = theme;
      const variables = window.theme?.[theme];
      if (variables) Object.entries(variables).forEach(([key, value]) => {
        if (root.style.getPropertyValue(key) !== value) root.style.setProperty(key, value);
      });
      try { if (localStorage.getItem('vite-ui-theme') !== theme) localStorage.setItem('vite-ui-theme', theme); } catch { /* storage unavailable */ }
    }
    sync();
    // Chainlit can replace inline variables without changing the theme class.
    new MutationObserver(sync).observe(root, {attributes:true, attributeFilter:['class', 'style']});
    try {
      const parentRoot = window.parent !== window && window.parent.document.documentElement;
      if (parentRoot) new MutationObserver(sync).observe(parentRoot, {attributes:true, attributeFilter:['data-theme']});
    } catch { /* standalone or different origin */ }
    window.addEventListener('storage', sync);
    window.addEventListener('load', sync);
    matchMedia('(prefers-color-scheme: dark)').addEventListener('change', sync);
  }
  // Custom scripts can execute before Chainlit's document and theme are ready.
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once:true});
  else start();
})();
