/* Keep the existing Chainlit session mounted when the assistant is collapsed. */
(() => {
  const panel = document.getElementById('tab-chat');
  const toggle = document.getElementById('chatToggle');
  const handle = document.getElementById('chatResize');
  const read = (key) => { try { return localStorage.getItem(key); } catch { return null; } };
  const save = (key, value) => { try { localStorage.setItem(key, value); } catch {} };
  function resize(width) {
    const max = Math.max(360, innerWidth - 320);
    width = Math.round(Math.max(360, Math.min(max, width)));
    document.documentElement.style.setProperty('--chat-width', `${width}px`);
    handle.setAttribute('aria-valuenow', width);
    handle.setAttribute('aria-valuemax', max);
    save('ocean-chat-width', width);
  }
  function openPanel(open) {
    if (open) resize(innerWidth * 0.70);
    panel.classList.toggle('hidden', !open);
    document.body.classList.toggle('chat-open', open);
    toggle.setAttribute('aria-expanded', String(open));
    if (open) document.getElementById('chatClose').focus();
    else toggle.focus();
  }
  toggle.addEventListener('click', () => openPanel(panel.classList.contains('hidden')));
  document.getElementById('chatClose').addEventListener('click', () => openPanel(false));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !panel.classList.contains('hidden')) openPanel(false);
  });
  handle.addEventListener('pointerdown', (event) => {
    handle.setPointerCapture(event.pointerId);
    document.body.classList.add('chat-resizing');
  });
  handle.addEventListener('pointermove', (event) => {
    if (handle.hasPointerCapture(event.pointerId)) resize(innerWidth - event.clientX);
  });
  const stop = () => document.body.classList.remove('chat-resizing');
  handle.addEventListener('pointerup', stop);
  handle.addEventListener('pointercancel', stop);
  handle.addEventListener('lostpointercapture', stop);
  handle.addEventListener('keydown', (event) => {
    if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    event.preventDefault();
    const width = Number(handle.getAttribute('aria-valuenow'));
    resize(event.key === 'Home' ? 360 : event.key === 'End' ? Number(handle.getAttribute('aria-valuemax')) : width + (event.key === 'ArrowLeft' ? 24 : -24));
  });
  window.addEventListener('resize', () => resize(Number(read('ocean-chat-width')) || innerWidth * 0.70));
  resize(Number(read('ocean-chat-width')) || innerWidth * 0.70);
})();
