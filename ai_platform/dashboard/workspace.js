/* Keep the existing Chainlit session mounted when the assistant is collapsed or floated. */
(() => {
  const panel = document.getElementById('tab-chat');
  const toggle = document.getElementById('chatToggle');
  const handle = document.getElementById('chatResize');
  const floatButton = document.getElementById('chatFloat');
  const pill = document.getElementById('chatPill');
  const read = (key) => { try { return localStorage.getItem(key); } catch { return null; } };
  const save = (key, value) => { try { localStorage.setItem(key, value); } catch {} };
  const savedWidth = () => Number(read('ocean-chat-width')) || Math.min(880, innerWidth * 0.62);
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  function resize(width, persist = true) {
    const max = Math.max(360, Math.min(innerWidth - 320, innerWidth * 0.72));
    width = Math.round(clamp(width, 360, max));
    document.documentElement.style.setProperty('--chat-width', `${width}px`);
    handle.setAttribute('aria-valuenow', width);
    handle.setAttribute('aria-valuemax', max);
    if (persist) save('ocean-chat-width', width);
  }
  const isOpen = () => !panel.classList.contains('hidden');
  const isFloating = () => panel.classList.contains('floating');
  function minimise(minimised) {
    panel.classList.toggle('minimised', minimised);
    pill.hidden = !minimised;
    (minimised ? pill : document.getElementById('chatMin')).focus();
  }
  function openPanel(open) {
    if (!open && !pill.hidden) minimise(false);
    if (open && !isFloating()) resize(savedWidth(), false);
    panel.classList.toggle('hidden', !open);
    document.body.classList.toggle('chat-open', open && !isFloating());
    toggle.setAttribute('aria-expanded', String(open));
    if (open) document.getElementById('chatClose').focus();
    else toggle.focus();
  }
  const MIN_WIDTH = 320, MIN_HEIGHT = 280, RAIL = 45, GAP = 8;
  const box = { left: 0, top: 0, width: 460, height: 640 };
  let hasFloated = false;
  function place(left, top, width, height) {
    box.width = clamp(width, MIN_WIDTH, Math.max(MIN_WIDTH, innerWidth - RAIL - GAP));
    box.height = clamp(height, MIN_HEIGHT, Math.max(MIN_HEIGHT, innerHeight - 2 * GAP));
    box.left = clamp(left, RAIL, innerWidth - box.width - GAP);
    box.top = clamp(top, GAP, innerHeight - box.height - GAP);
    Object.assign(panel.style, Object.fromEntries(Object.entries(box).map(([key, value]) => [key, `${Math.round(value)}px`])));
  }
  function setFloating(floating) {
    panel.classList.toggle('floating', floating);
    floatButton.setAttribute('aria-pressed', String(floating));
    floatButton.textContent = floating ? '⇥' : '⧉';
    floatButton.title = floating ? 'Dock to the side' : 'Float over the dashboard';
    document.body.classList.toggle('chat-open', isOpen() && !floating);
    if (!floating) { if (!pill.hidden) minimise(false); panel.removeAttribute('style'); return; }
    if (hasFloated) { place(box.left, box.top, box.width, box.height); return; }
    hasFloated = true;
    const dockedWidth = panel.getBoundingClientRect().width;
    const width = Math.min(Math.max(dockedWidth, innerWidth * 0.55, 460), 960, innerWidth - RAIL - GAP);
    const height = innerHeight - 104;
    place(innerWidth - width - 24, 80, width, height);
  }
  function resizeFrom(edge, dx, dy, start) {
    let { left, top, width, height } = start;
    if (edge.includes('e')) width = Math.min(start.width + dx, innerWidth - GAP - start.left);
    if (edge.includes('s')) height = Math.min(start.height + dy, innerHeight - GAP - start.top);
    if (edge.includes('w')) { width = clamp(start.width - dx, MIN_WIDTH, start.left + start.width - RAIL); left = start.left + start.width - width; }
    if (edge.includes('n')) { height = clamp(start.height - dy, MIN_HEIGHT, start.top + start.height - GAP); top = start.top + start.height - height; }
    place(left, top, width, height);
  }
  function drag(grip, onMove, onEnd) {
    let start = null;
    grip.addEventListener('pointerdown', (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      grip.setPointerCapture(event.pointerId);
      document.body.classList.add('chat-moving');
      start = { x: event.clientX, y: event.clientY, ...box };
    });
    grip.addEventListener('pointermove', (event) => {
      if (start && grip.hasPointerCapture(event.pointerId)) onMove(event.clientX - start.x, event.clientY - start.y, start, event);
    });
    grip.addEventListener('lostpointercapture', () => { if (start && onEnd) onEnd(); start = null; document.body.classList.remove('chat-moving'); });
  }
  const SNAP = 40;
  drag(document.getElementById('chatMove'), (dx, dy, start, event) => {
    place(start.left + dx, start.top + dy, start.width, start.height);
    document.body.classList.toggle('chat-snap', start.left + dx + start.width >= innerWidth - GAP || event.clientX >= innerWidth - SNAP);
  }, () => {
    if (document.body.classList.contains('chat-snap')) setFloating(false);
    document.body.classList.remove('chat-snap');
  });
  ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'].forEach((edge) => {
    const grip = document.createElement('div');
    grip.className = `chat-edge chat-edge-${edge}`;
    grip.setAttribute('aria-hidden', 'true');
    panel.append(grip);
    drag(grip, (dx, dy, start) => resizeFrom(edge, dx, dy, start));
  });
  toggle.addEventListener('click', () => (pill.hidden ? openPanel(!isOpen()) : minimise(false)));
  pill.addEventListener('click', () => minimise(false));
  document.getElementById('chatMin').addEventListener('click', () => minimise(true));
  floatButton.addEventListener('click', () => setFloating(!isFloating()));
  document.getElementById('chatClose').addEventListener('click', () => openPanel(false));
  panel.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') openPanel(false);
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
  window.addEventListener('resize', () => {
    resize(savedWidth(), false);
    if (!isFloating()) return;
    if (innerWidth <= 900) { setFloating(false); return; }
    place(box.left, box.top, box.width, box.height);
  });
  resize(savedWidth(), false);
})();
