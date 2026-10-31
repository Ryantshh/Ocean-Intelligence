/* Decorative orthographic globe using the same local coastline as the data map.
   No network, WebGL, or fabricated vessel tracks. Stop work when offscreen. */
(() => {
  const host = document.querySelector('#workspace .hero-chart');
  if (!host || typeof WORLD_LAND === 'undefined') return;
  const canvas = document.createElement('canvas');
  canvas.setAttribute('aria-hidden', 'true');
  host.prepend(canvas);
  host.removeAttribute('aria-hidden');
  const caption = host.querySelector('span');
  if (caption) caption.textContent = 'A wider horizon.';
  const button = document.createElement('button');
  button.type = 'button'; button.className = 'globe-motion';
  host.append(button);
  const ctx = canvas.getContext('2d');
  if (!ctx) { canvas.remove(); button.remove(); return; }
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  let paused = reduced.matches, visible = false, frame = 0, last = 0, angle = -1.2;
  const rings = WORLD_LAND.flat();
  const rad = Math.PI / 180;
  function point(lon, lat) {
    const a = lon * rad + angle, b = lat * rad, tilt = .26;
    const x = Math.cos(b) * Math.sin(a), y = Math.sin(b), z = Math.cos(b) * Math.cos(a);
    return [x, y * Math.cos(tilt) - z * Math.sin(tilt), y * Math.sin(tilt) + z * Math.cos(tilt)];
  }
  let light = false;
  function draw() {
    const width = host.clientWidth;
    if (!width) return;
    const dpr = Math.min(devicePixelRatio || 1, 2), height = 180;
    if (canvas.width !== Math.round(width*dpr) || canvas.height !== height*dpr) { canvas.width = Math.round(width*dpr); canvas.height = height*dpr; }
    ctx.setTransform(dpr,0,0,dpr,0,0); ctx.clearRect(0,0,width,height);
    const cx=width/2, cy=83, r=70;

    const glow=ctx.createRadialGradient(cx-24,cy-25,3,cx,cy,r);
    glow.addColorStop(0,light?'#efffff':'#327889'); glow.addColorStop(.7,light?'#99c9ce':'#123c4d'); glow.addColorStop(1,light?'#4c889a':'#071f2f');
    ctx.beginPath(); ctx.arc(cx,cy,r,0,Math.PI*2); ctx.fillStyle=glow; ctx.fill();
    function line(points, color, weight) {
      ctx.beginPath(); let pen=false;
      for (const [lon,lat] of points) { const [x,y,z]=point(lon,lat); if(z<0) {pen=false;continue;} if(pen) ctx.lineTo(cx+x*r,cy-y*r); else ctx.moveTo(cx+x*r,cy-y*r); pen=true; }
      ctx.strokeStyle=color; ctx.lineWidth=weight; ctx.stroke();
    }
    for(let lon=-180;lon<180;lon+=30) line(Array.from({length:91},(_,i)=>[lon,-90+i*2]),'#8fd3d126',.6);
    for(let lat=-60;lat<=60;lat+=30) line(Array.from({length:181},(_,i)=>[-180+i*2,lat]),'#8fd3d126',.6);
    rings.forEach(ring=>line(ring,light?'#285c68':'#a8e0d3',.7));
    ctx.beginPath(); ctx.ellipse(cx,cy,95,28,-.35,0,Math.PI*2); ctx.strokeStyle='#8cddd54d'; ctx.lineWidth=.7; ctx.stroke();
    ctx.beginPath(); ctx.arc(cx,cy,r+4,0,Math.PI*2); ctx.strokeStyle='#9ad9d52b'; ctx.stroke();
    ctx.beginPath(); ctx.arc(cx-89,cy+9,2.4,0,Math.PI*2); ctx.fillStyle='#c2eddb'; ctx.fill();
  }
  function tick(time) { frame=0; if (!visible || document.hidden || paused) return; if(time-last>=80) { angle+=Math.min(time-last,120)*.00006; last=time; draw(); } frame=requestAnimationFrame(tick); }
  function sync() { light = getComputedStyle(host).getPropertyValue('--hero-ink').trim() === '#143f4c'; cancelAnimationFrame(frame); frame=0; last=performance.now(); button.textContent=paused?'Play':'Pause'; button.setAttribute('aria-label',paused?'Play decorative globe animation':'Pause decorative globe animation'); draw(); if(visible&&!document.hidden&&!paused) frame=requestAnimationFrame(tick); }
  button.addEventListener('click',()=>{paused=!paused;sync();});
  reduced.addEventListener('change',()=>{paused=reduced.matches;sync();});
  document.addEventListener('visibilitychange',sync);
  new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;sync();}).observe(host);
  new ResizeObserver(sync).observe(host);
  new MutationObserver(sync).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change',sync);
})();

/* Map navigation is independent of region selection and survives data refreshes. */
(() => {
  const container = document.getElementById('regionsMap');
  if (!container) return;
  let scale = 1, cx = 480, cy = 208;
  function bind() {
    const svg = container.querySelector(':scope > svg');
    if (!svg || svg.dataset.navigationBound) return;
    svg.dataset.navigationBound = 'true';
    const output = container.querySelector('.atlas-tools output');
    let drag = null;
    function paint() {
      const w=960/scale, h=416/scale;
      cx=Math.max(w/2,Math.min(960-w/2,cx)); cy=Math.max(h/2,Math.min(416-h/2,cy));
      svg.setAttribute('viewBox',`${cx-w/2} ${cy-h/2} ${w} ${h}`);
      svg.classList.toggle('is-zoomed',scale>1);
      output.textContent=`${Math.round(scale*100)}%`;
      container.querySelector('[data-map-action="out"]').disabled=scale<=1;
      container.querySelector('[data-map-action="in"]').disabled=scale>=4;
    }
    function zoom(action) {
      if(action==='reset') {scale=1;cx=480;cy=208;}
      else scale=Math.max(1,Math.min(4,scale*(action==='in'?1.5:1/1.5)));
      paint();
    }
    container.querySelectorAll('[data-map-action]').forEach(button=>button.addEventListener('click',()=>zoom(button.dataset.mapAction)));
    svg.addEventListener('keydown',event=>{
      if(event.target!==svg) return;
      if(['+','=','-','0','ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)) {
        event.preventDefault();
        if(event.key==='+'||event.key==='=') zoom('in');
        else if(event.key==='-') zoom('out');
        else if(event.key==='0') zoom('reset');
        else {cx+=({'ArrowLeft':-40,'ArrowRight':40}[event.key]||0)/scale;cy+=({'ArrowUp':-40,'ArrowDown':40}[event.key]||0)/scale;paint();}
      }
    });
    svg.addEventListener('pointerdown',event=>{
      if(event.button!==0 || scale===1 || event.target.closest('[data-region]')) return;
      const rect=svg.getBoundingClientRect();
      drag={x:event.clientX,y:event.clientY,cx,cy,ratio:960/scale/rect.width};
      svg.setPointerCapture(event.pointerId); svg.classList.add('is-dragging');
    });
    svg.addEventListener('pointermove',event=>{if(!drag)return;cx=drag.cx-(event.clientX-drag.x)*drag.ratio;cy=drag.cy-(event.clientY-drag.y)*drag.ratio;paint();});
    svg.addEventListener('lostpointercapture',()=>{drag=null;svg.classList.remove('is-dragging');});
    svg.addEventListener('pointerup',event=>{if(svg.hasPointerCapture(event.pointerId))svg.releasePointerCapture(event.pointerId);});
    svg.addEventListener('focusin',event=>{
      if(scale===1 || !event.target.classList.contains('map-bubble-hit'))return;
      cx=Number(event.target.getAttribute('cx'));cy=Number(event.target.getAttribute('cy'));paint();
    });
    paint();
  }
  new MutationObserver(bind).observe(container,{childList:true});
  bind();
})();

(() => {
  const host=document.querySelector('.voyage-chart');
  if(!host)return;
  host.removeAttribute('aria-hidden');
  const button=document.createElement('button');button.type='button';button.className='globe-motion';
  let paused=matchMedia('(prefers-reduced-motion: reduce)').matches;
  function update(){host.classList.toggle('motion-paused',paused);button.textContent=paused?'Play':'Pause';button.setAttribute('aria-label',`${paused?'Play':'Pause'} decorative vessel animation`);}
  button.addEventListener('click',()=>{paused=!paused;update();});host.append(button);update();
})();

/* Presentational navigation only: existing handlers still own tab state/data.
   Animate opacity on containers so fixed tooltips retain their coordinate space. */
(() => {
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  const animations=new Set();
  function animate(element,frames,options) {
    if(!element || reduced.matches || !element.animate)return;
    const animation=element.animate(frames,options);
    animations.add(animation);
    animation.finished.catch(()=>{}).finally(()=>animations.delete(animation));
  }
  reduced.addEventListener('change',()=>{if(reduced.matches)animations.forEach(animation=>animation.cancel());});
  document.querySelectorAll('.primary-tabs,.sub-tabs').forEach(nav=>{
    const buttons=[...nav.querySelectorAll('.tab-btn')];
    const glider=document.createElement('span');glider.className='nav-glider';glider.setAttribute('aria-hidden','true');nav.append(glider);nav.classList.add('has-glider');
    let active=buttons.find(button=>button.classList.contains('active'));
    function position() {
      const selected=buttons.find(button=>button.classList.contains('active'));
      if(!selected)return;
      glider.style.width=`${selected.offsetWidth}px`;
      glider.style.height=`${selected.offsetHeight}px`;
      glider.style.transform=`translate(${selected.offsetLeft}px,${nav.classList.contains('sub-tabs')?0:selected.offsetTop}px)`;
    }
    nav.addEventListener('click',event=>{
      const button=event.target.closest('.tab-btn');
      if(!button || !buttons.includes(button))return;
      position();
      if(active===button)return;
      const direction=buttons.indexOf(button)>buttons.indexOf(active)?1:-1;active=button;
      animations.forEach(animation=>animation.cancel());
      const target=nav.classList.contains('sub-tabs')?document.getElementById(`tab-${button.dataset.tab}`):document.getElementById(button.dataset.tab==='voyage-defaults'?'tab-voyage-defaults':'workspace');
      animate(target,[{opacity:.35},{opacity:1}],{duration:280,easing:'ease-out'});
      const heading=target?.querySelector('.workspace-heading');
      animate(heading,[{transform:`translateX(${direction*12}px)`,opacity:.65},{transform:'translateX(0)',opacity:1}],{duration:400,easing:'cubic-bezier(.22,1,.36,1)'});
      // Re-measure hidden sub-navigation after returning to Overview.
      requestAnimationFrame(()=>window.dispatchEvent(new Event('ocean-navigation-layout')));
    });
    new ResizeObserver(position).observe(nav);
    window.addEventListener('ocean-navigation-layout',position);
    position();
  });
})();

/* Static contours retain the visual identity without pointer-driven repainting. */
document.querySelectorAll('.workspace-heading').forEach(card=>{
  const waves=document.createElement('div');waves.className='hero-seascape';waves.setAttribute('aria-hidden','true');card.append(waves);
});
