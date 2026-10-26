/* A vertical splitter for Chainlit's compact stacked results layout. */
(() => {
  if (window.__oceanResultsLayout) return;
  window.__oceanResultsLayout = true;
  const compact = matchMedia('(min-width:768px) and (max-width:1250px)');
  let binding = null;
  let ratio = .42;
  try { const saved = Number(localStorage.getItem('ocean-results-split')); if (saved >= .2 && saved <= .8) ratio = saved; } catch {}
  function bind() {
    const results = document.querySelector('#side-view-content')?.closest('[data-panel]');
    const group = results?.parentElement;
    if (binding && binding.results === results && binding.handle.isConnected) return;
    if (binding) {
      binding.observer.disconnect(); binding.handle.remove();
      binding.group.classList.remove('oi-split-group');
      binding.chat.classList.remove('oi-split-chat');
      binding.results.classList.remove('oi-split-results');
      binding = null;
      document.body.classList.remove('oi-split-dragging');
    }
    if (!group?.matches('[data-panel-group]')) return;
    const chat = [...group.children].find(el => el.matches('[data-panel]') && el !== results);
    if (!chat) return;
    const handle = document.createElement('div');
    handle.className = 'oi-results-splitter';
    handle.tabIndex = 0;
    handle.setAttribute('role', 'separator');
    handle.setAttribute('aria-orientation', 'horizontal');
    handle.setAttribute('aria-label', 'Resize results height');
    handle.title = 'Drag to resize results · Arrow keys to adjust · Double-click to reset';
    handle.innerHTML = '<span aria-hidden="true"></span>';
    group.classList.add('oi-split-group'); chat.classList.add('oi-split-chat'); results.classList.add('oi-split-results');
    group.insertBefore(handle, results);
    function layout() {
      if (!compact.matches) return;
      const available = group.clientHeight - 14;
      if (available <= 0) return;
      const min = Math.min(160, available * .3);
      const max = Math.max(min, available - Math.min(180, available * .35));
      const height = Math.round(Math.max(min, Math.min(max, available * ratio)));
      group.style.setProperty('--oi-chat-height', `${height}px`);
      handle.setAttribute('aria-valuemin', Math.round(available - max));
      handle.setAttribute('aria-valuemax', Math.round(available - min));
      handle.setAttribute('aria-valuenow', Math.round(available - height));
      handle.setAttribute('aria-valuetext', `${Math.round(available - height)} pixels of results`);
    }
    function update(next) {
      ratio = Math.max(.2, Math.min(.8, next)); layout();
      try { localStorage.setItem('ocean-results-split', String(ratio)); } catch {}
    }
    handle.addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      event.preventDefault(); handle.focus(); handle.setPointerCapture(event.pointerId);
      document.body.classList.add('oi-split-dragging');
    });
    handle.addEventListener('pointermove', event => {
      if (!handle.hasPointerCapture(event.pointerId)) return;
      const box = group.getBoundingClientRect();
      update((event.clientY - box.top - 7) / (group.clientHeight - 14));
    });
    const stop = () => document.body.classList.remove('oi-split-dragging');
    ['pointerup', 'pointercancel', 'lostpointercapture'].forEach(type => handle.addEventListener(type, stop));
    handle.addEventListener('dblclick', () => update(.42));
    handle.addEventListener('keydown', event => {
      if (!['ArrowUp','ArrowDown','Home','End'].includes(event.key)) return;
      event.preventDefault();
      update(event.key === 'Home' ? .2 : event.key === 'End' ? .8 : ratio + (event.key === 'ArrowUp' ? -.04 : .04));
    });
    const observer = new ResizeObserver(layout);
    binding = {results, group, chat, handle, observer, layout};
    observer.observe(group); layout();
  }
  let pending = false;
  new MutationObserver(() => {
    if (pending) return;
    pending = true; requestAnimationFrame(() => { pending = false; bind(); });
  }).observe(document.documentElement, {childList:true, subtree:true});
  compact.addEventListener('change', () => binding?.layout());
  bind();
})();
