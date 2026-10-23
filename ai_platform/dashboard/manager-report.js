/* Isolated, read-only report. No changes to dashboard filters or business rules. */
(() => {
  'use strict';
  const shiftDay = (day, offset) => {
    const date = new Date(`${day}T00:00:00Z`);
    date.setUTCDate(date.getUTCDate() + offset);
    return date.toISOString().slice(0, 10);
  };
  const count = (value) => {
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) throw new Error('Missing or invalid count in report data.');
    return value;
  };
  // Exclude both boundary dates: the API's first and last buckets can be partial.
  function compare(series, range, days, region = '') {
    if (![7, 14].includes(days) || !range?.since || !range?.until || !Array.isArray(series)) throw new Error('Report period is unavailable.');
    const end = range.until.slice(0, 10);
    const start = shiftDay(end, -days);
    const previous = shiftDay(start, -days);
    if (previous <= range.since.slice(0, 10)) throw new Error('Insufficient complete days for this comparison.');
    const byDay = new Map(series.map(row => [row.day, row]));
    const rows = [];
    for (let day = previous; day < end; day = shiftDay(day, 1)) {
      const source = byDay.get(day);
      if (!source) throw new Error(`Missing daily coverage for ${day}.`);
      if (region && !Array.isArray(source.regions)) throw new Error('Regional breakdown is unavailable.');
      const row = region ? (source.regions.find(r => r.region === region) || {count:0, up:0, down:0}) : source;
      rows.push({day, count:count(row.count), ...(source.up !== undefined ? {up:count(row.up), down:count(row.down)} : {})});
    }
    return {current:rows.slice(days), previous:rows.slice(0, days), start, end, previousStart:previous};
  }
  const total = (rows, key = 'count') => rows.reduce((sum, row) => sum + count(row[key]), 0);
  const delta = (current, previous) => `${current - previous > 0 ? '+' : ''}${current - previous}`;
  function build(snapshot, days, region) {
    const d = snapshot.daily;
    for (const key of ['OPEN','ON SUBS','FIXED','LIKELY FIXED']) count(snapshot.counts[key]);
    if (!Array.isArray(snapshot.regions)) throw new Error('Regional data unavailable.');
    snapshot.regions.forEach(r => { count(r.supply); count(r.demand); });
    return {
      vessels:compare(d.new_vessels, d.range, days, region),
      moves:compare(d.status_changes, d.range, days, region),
      orders:compare(d.new_orders, d.orders_range, days, region),
      regions:snapshot.regions.filter(r => !region || r.region === region),
    };
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {compare, total, build, shiftDay};
  if (typeof document === 'undefined') return;
  const $ = id => document.getElementById(id);
  const e = escapeHtml, n = formatNumber;
  const dayText = day => new Date(`${day.slice(0,10)}T00:00:00Z`).toLocaleDateString('en-GB', {day:'numeric', month:'short', year:'numeric', timeZone:'UTC'});
  const periodText = w => `${dayText(w.start)} – ${dayText(shiftDay(w.end,-1))}`;
  const previousText = w => `${dayText(w.previousStart)} – ${dayText(shiftDay(w.start,-1))}`;
  let snapshot = null, view = null, evidenceVersion = 0;
  const noteLimit = 4000;
  const definitions = [
    'Fleet position is a current, fleet-wide snapshot, independent of the activity period and regional detail selection. Historical fleet snapshots are unavailable; no fleet deltas are inferred.',
    'Activity uses completed UTC calendar days, excluding the current simulated day and the partially covered first API day. The previous period has the same number of completed days. Vessel and order clocks are reported separately.',
    'Regional pressure uses current open vessels and orders received in the existing recent 14-day window. The existing thresholds are unchanged: supply/demand below 0.7 is Tight; 0.7–1.5 is Balanced; above 1.5 is Oversupplied. Zero recent demand is neutral. Counts are not matched cargo capacity or a literal vessel shortfall.',
    'Returning supply means Fixed or On Subs to Open; committed supply means Open to Fixed or On Subs. Net movement is not the change in the Open fleet. Daily totals describe daily activity; summing days does not imply unique vessels over the entire period.',
    'Likely Fixed is inferred from the existing stale-report rule, not a confirmed fixture. Regional coverage can overlap: do not sum regions to obtain fleet totals or market shares. Regional movement attribution follows the existing API.',
    'Fetched-at timestamps are retrieval times, not source refresh times. Source freshness, missing source-record counts, historical fleet changes, freight economics and company performance are not available in these endpoints.',
    'Generation reads separate existing endpoints, not an atomic database snapshot. Downloaded snapshots retain the fetched inputs, selected scope, definitions, notes and any retrieved evidence. Later evidence requests may reflect updated records.'
  ];
  function chart(rows, move) {
    const width=560, height=170, pad=20, plot=130, mid=85, slot=(width-2*pad)/rows.length;
    const max=Math.max(1,...rows.map(r => move ? Math.max(r.up,r.down) : r.count));
    const bars=rows.map((r,i) => {
      const x=pad+i*slot+slot*.2, bw=slot*.6;
      const rect=(value,y,h,color,labelY) => `<rect x="${x}" y="${y}" width="${bw}" height="${h}" rx="2" fill="${color}"><title>${e(r.day)}: ${value}</title></rect>${labelY === undefined ? "" : `<text x="${x+bw/2}" y="${labelY}" text-anchor="middle" fill="var(--muted)" font-size="9">${value}</text>`}`;
      if (move) {
        const up=r.up/max*60, down=r.down/max*60;
        return rect(r.up,mid-up,up,'var(--status-good)',r.up?mid-up-4:undefined)+rect(-r.down,mid,down,'var(--status-critical)',r.down?mid+down+11:undefined);
      }
      const h=r.count/max*plot;
      return rect(r.count,height-pad-h,h,'var(--chart-blue)',height-pad-h-4);
    }).join('');
    return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${move?'Daily returning and committed supply':'Daily new orders'}. Exact values and evidence are in the daily table below."><line x1="${pad}" x2="${width-pad}" y1="${move?mid:height-pad}" y2="${move?mid:height-pad}" stroke="var(--muted)"/>${bars}</svg><div class="mr-axis"><span>${e(dayText(rows[0].day))}</span><span>${e(dayText(rows.at(-1).day))}</span></div>`;
  }
  function render() {
    const days=Number($('mrPeriod').value), region=$('mrRegion').value;
    const model=build(snapshot,days,region);
    view={days,region};
    const {vessels,moves,orders}=model;
    const up=total(moves.current,'up'), down=total(moves.current,'down'), newOrders=total(orders.current), previousOrders=total(orders.previous);
    const ranked=[...model.regions].sort((a,b) => {
      const rank={'tight':0,'balanced':1,'oversupplied':2,'no-data':3};
      return rank[regionStatus(a.supply,a.demand).key]-rank[regionStatus(b.supply,b.demand).key] || b.demand-a.demand || a.region.localeCompare(b.region);
    });
    const tight=ranked.filter(r => regionStatus(r.supply,r.demand).key==='tight');
    const neutral=ranked.filter(r => !r.demand);
    const unmapped=model.regions.filter(r => !REGION_COORDS[r.region]);
    const findings=[
      {title:'Supply movements',fact:`${n(up)} returning / ${n(down)} committed; net movement ${delta(up,down)}.`, implication:down>up?'Commitments exceeded returning supply in this period. This does not by itself prove a decline in open fleet size.':'Returning supply met or exceeded commitments in this period. Other stock changes are not reconciled here.',action:'Review the daily movement records before drawing conclusions about availability.',href:'#mrDaily'},
      {title:'Demand activity',fact:`${n(newOrders)} new orders; ${delta(newOrders,previousOrders)} versus ${n(previousOrders)} in the previous period.`,implication:newOrders>previousOrders?'More orders were recorded than in the comparison period; cargo size and vessel suitability still need review.':newOrders<previousOrders?'Fewer orders were recorded than in the comparison period; this alone does not establish weaker freight rates.':'Recorded order activity was unchanged between the two periods.',action:'Inspect order loading windows and cargo requirements.',href:'#mrOrdersDaily'},
      {title:'Regional attention',fact:`${tight.length} of ${ranked.length} displayed regions are Tight under the existing pressure definition.`,implication:tight.length?'These regions warrant availability checks; the indicator compares different units and time bases.':'No displayed region meets the existing Tight threshold. This does not establish vessel-cargo compatibility.',action:tight.length?`Review ${tight.slice(0,3).map(r=>r.region).join(', ')} and confirm vessel opening dates.`:'Review balanced regions and upcoming cargo requirements.',href:'#mrRegions'},
    ];
    const metric=(label,value,previous,unit='events') => `<div><span>${label}</span><strong>${n(value)}</strong><small>${delta(value,previous)} vs ${n(previous)} previous ${unit}</small></div>`;
    const regionRows=ranked.map(r => {
      const status=regionStatus(r.supply,r.demand);
      const persistence=r.days_with_supply == null || r.persistence_window_days == null ? 'Unavailable' : `${n(r.days_with_supply)} / ${n(r.persistence_window_days)} days`;
      return `<tr><th scope="row">${e(r.region)}</th><td>${n(r.supply)}</td><td>${n(r.demand)}</td><td><span class="mr-status" style="--signal:${status.color}">${e(status.label)}</span></td><td>${persistence}</td></tr>`;
    }).join('');
    $('mrReport').innerHTML=`
      <header class="mr-context"><div><span class="mr-eyebrow">GENERATED BRIEF · ${e(snapshot.id)}</span><h3>${days}-day commercial briefing</h3><p>Regional detail: <strong>${e(region||'All regions')}</strong> · Fleet KPIs: <strong>all regions</strong></p></div>
      <dl><dt>Vessel activity / comparison</dt><dd>${periodText(vessels)} / ${previousText(vessels)}</dd><dt>Order activity / comparison</dt><dd>${periodText(orders)} / ${previousText(orders)}</dd><dt>Simulation cutoffs (UTC)</dt><dd>Vessels: ${e(snapshot.daily.range.until)} · Orders: ${e(snapshot.daily.orders_range.until)}</dd><dt>Retrieval interval (UTC)</dt><dd>${e(snapshot.started)} – ${e(snapshot.generated)}</dd></dl></header>
      <section class="report-section"><div class="mr-section-head"><h3>Executive brief</h3><span class="muted">Rule-based observations · no forecasts</span></div><div class="mr-findings">${findings.map(f=>`<article><h4>${f.title}</h4><p><b>Observed:</b> ${e(f.fact)}</p><p><b>Interpretation:</b> ${e(f.implication)}</p><a href="${f.href}">View evidence ↓</a></article>`).join('')}</div></section>
      <section class="report-section"><h3>Fleet position <span class="muted">Current · fleet-wide</span></h3><div class="mr-kpis">${[['OPEN','Open','open'],['ON SUBS','On Subs','watchlist'],['FIXED','Fixed','fixed'],['LIKELY FIXED','Likely Fixed · inferred','likely-fixed']].map(([key,label,color])=>`<div style="--signal:var(--${color})"><span>${label}</span><strong>${n(snapshot.counts[key])}</strong></div>`).join('')}</div><p class="mr-note">Snapshot fetched ${e(snapshot.fetched.counts)}. Historical fleet deltas and source-refresh times are unavailable.</p></section>
      <section id="mrRegions" class="report-section"><h3>Regional pressure</h3><p class="mr-note">Current open vessels versus recent 14-day orders; independent of the activity selector. Ranked by existing status, then order count. Overlapping regions must not be added together.</p><div class="table-wrap"><table><thead><tr><th>Region</th><th>Open now</th><th>Recent orders</th><th>Pressure</th><th>Days with open supply</th></tr></thead><tbody>${regionRows||'<tr><td colspan="5">No regional observations available.</td></tr>'}</tbody></table></div><p class="mr-note">Fetched ${e(snapshot.fetched.regions)}. No historical regional snapshot is available for pressure deltas.</p></section>
      <section class="report-section"><h3>Activity comparison <span class="muted">${e(region||'All regions')}</span></h3><div class="mr-metrics">${metric('New vessels / newly open',total(vessels.current),total(vessels.previous))}${metric('Returning to Open',up,total(moves.previous,'up'))}${metric('Committed',down,total(moves.previous,'down'))}${metric('New orders',newOrders,previousOrders,'orders')}</div><div class="mr-charts"><article><h4>Supply movement</h4><p class="mr-note">Mint: returning (+) · Coral: committed (−)</p>${chart(moves.current,true)}</article><article><h4>New orders</h4><p class="mr-note">Orders received per completed day</p>${chart(orders.current,false)}</article></div><p class="mr-note">New-vessel and returning-supply events can overlap. These are not additive categories or a fleet reconciliation.</p>
      <details class="mr-daily" id="mrDaily"><summary>Daily vessel figures and supporting records</summary>${dailyTable(vessels.current,moves.current)}</details>
      <details class="mr-daily" id="mrOrdersDaily"><summary>Daily order figures and supporting records</summary>${orderTable(orders.current)}</details></section>
      <section class="report-section"><h3>Priority follow-ups</h3><ol class="mr-actions">${findings.map(f=>`<li><strong>${f.title}</strong><p>${e(f.action)} <a href="${f.href}">Evidence ↓</a></p><span class="muted">Suggested follow-up · no assignment or execution implied</span></li>`).join('')}</ol></section>
      <section class="report-section"><h3>Manager commentary</h3><p class="mr-note">Editable commentary is separate from generated facts. Included in exports; retained in this tab until reload.</p><textarea id="mrNotes" rows="3" maxlength="${noteLimit}" aria-label="Manager commentary" placeholder="Add decisions, named owners, and next review dates…"></textarea><p id="mrPrintedNotes"></p></section>
      <section class="report-section"><h3>Data quality &amp; definitions</h3><div class="mr-quality"><span><b>${neutral.length}</b> displayed regions without recent demand</span><span><b>${unmapped.length}</b> displayed regions without known coordinates</span><span><b>${n(snapshot.counts['LIKELY FIXED'])}</b> fleet-wide Likely Fixed, inferred</span></div><p class="mr-note">${unmapped.length?`Unmapped regions: ${e(unmapped.map(r=>r.region).join(', '))}. `:''}Zero-filled daily activity does not prove complete source coverage. Source freshness and missing source-record counts: unavailable.</p><details class="mr-methodology"><summary>Definitions, time windows &amp; limitations</summary><ol>${definitions.map(d=>`<li>${e(d)}</li>`).join('')}</ol></details></section>`;
    $('mrNotes').value=snapshot.notes||'';
    syncNotes();
    $('mrEvidence').hidden=true;
    evidenceVersion++;
    $('mrPrint').disabled=false; $('mrDownload').disabled=false;
  }
  function dailyTable(vessels,moves) {
    return `<div class="table-wrap"><table><thead><tr><th>UTC day</th><th>New vessels</th><th>Returning (+)</th><th>Committed (−)</th></tr></thead><tbody>${vessels.map((r,i)=>`<tr><th>${e(r.day)}</th><td>${evidenceButton(r.day,'new_vessels','',r.count)}</td><td>${evidenceButton(r.day,'status_changes','up',moves[i].up)}</td><td>${evidenceButton(r.day,'status_changes','down',moves[i].down)}</td></tr>`).join('')}</tbody></table></div>`;
  }
  function orderTable(rows) {
    return `<div class="table-wrap"><table><thead><tr><th>UTC day</th><th>New orders</th></tr></thead><tbody>${rows.map(r=>`<tr><th>${e(r.day)}</th><td>${evidenceButton(r.day,'new_orders','',r.count)}</td></tr>`).join('')}</tbody></table></div>`;
  }
  function evidenceButton(day,metric,direction,value) {
    return `<button type="button" class="mr-evidence-link" data-day="${e(day)}" data-metric="${metric}" data-direction="${direction}" aria-label="${e(day)}: ${n(value)} ${metric.replaceAll('_',' ')} ${direction}; view fleet-wide records">${n(value)} ↗</button>`;
  }
  function syncNotes() {
    if (!snapshot || !$('mrNotes')) return;
    snapshot.notes=$('mrNotes').value.slice(0,noteLimit);
    $('mrPrintedNotes').textContent=snapshot.notes||'No manager commentary added.';
  }
  async function generate(event) {
    event.preventDefault(); syncNotes();
    const button=$('mrGenerate'); button.disabled=true;
    $('mrPrint').disabled=true; $('mrDownload').disabled=true;
    $('mrPeriod').disabled=true; $('mrRegion').disabled=true;
    $('reportStatus').textContent='Capturing current position and completed-period activity…';
    evidenceVersion++;
    const started=new Date().toISOString(), fetched={};
    const read=async (key,path) => {const data=await getJson(path); fetched[key]=new Date().toISOString(); return data;};
    try {
      const [counts,regions,daily]=await Promise.all([
        read('counts','/api/dashboard/vessels/status-counts'),read('regions','/api/dashboard/regions'),read('daily','/api/dashboard/daily-counts?days=30')
      ]);
      const next={version:1,id:`MR-${Array.from(crypto.getRandomValues(new Uint8Array(6)), b=>b.toString(16).padStart(2,'0')).join('')}`,started,generated:new Date().toISOString(),fetched,counts,regions,daily,notes:snapshot?.notes||'',evidence:[]};
      const selected=$('mrRegion').value;
      build(next,Number($('mrPeriod').value),selected);
      snapshot=next;
      const regionNames=[...new Set([...regions.map(r=>r.region),...daily.new_orders.flatMap(r=>(r.regions||[]).map(z=>z.region)),...daily.new_vessels.flatMap(r=>(r.regions||[]).map(z=>z.region)),...daily.status_changes.flatMap(r=>(r.regions||[]).map(z=>z.region))])].filter(Boolean).sort();
      $('mrRegion').innerHTML='<option value="">All regions</option>'+regionNames.map(r=>`<option value="${e(r)}">${e(r)}</option>`).join('');
      $('mrRegion').value=regionNames.includes(selected)?selected:'';
      render();
      $('reportStatus').textContent='Report captured. Regional detail and period changes reuse this snapshot. Generate again to refresh.';
    } catch(error) {
      $('reportStatus').textContent=`Generation failed: ${error.message}. ${snapshot?'The previous report remains below; exports are disabled until a successful generation.':'No report has been published.'}`;
    } finally {button.disabled=false; $('mrPeriod').disabled=false; $('mrRegion').disabled=false;}
  }
  async function evidence(button) {
    const version=++evidenceVersion, captured=snapshot;
    const {day,metric,direction}=button.dataset;
    const orders=metric==='new_orders';
    const range=orders?captured.daily.orders_range:captured.daily.range;
    const params=new URLSearchParams({day,range_since:range.since,range_until:range.until});
    if (!orders) {params.set('metric',metric); if(direction) params.set('direction',direction);}
    const url=`/api/dashboard/${orders?'orders':'vessels'}/by-day?${params}`;
    $('mrEvidence').hidden=false;
    $('mrEvidenceTitle').textContent=`${day} · ${metric.replaceAll('_',' ')} ${direction} · all regions`;
    $('mrEvidenceBody').textContent='Loading supporting records…'; $('mrEvidence').focus();
    try {
      const rows=await getJson(url);
      if(version!==evidenceVersion || snapshot!==captured) return;
      if(!Array.isArray(rows)) throw new Error('Unexpected evidence response.');
      const retrieved=new Date().toISOString();
      captured.evidence=captured.evidence.filter(item=>item.url!==url);
      captured.evidence.push({url,retrieved,rows});
      const columns=orders?[['order_id','Order'],['order_seq','Sequence'],['load_zone','Load region'],['laycan_start','Laycan start'],['laycan_end','Laycan end'],['cargo_weight_min','Min cargo weight'],['cargo_weight_max','Max cargo weight']]:[['vessel_id','Vessel'],['dashboard_status','Current status'],['parent_zones','Current regions'],['open_date_start','Open from'],['open_date_end','Open until'],['update_date','Last reported']];
      $('mrEvidenceBody').innerHTML=`<p class="mr-note">${rows.length} fleet-wide records retrieved ${e(retrieved)}. Records are fetched on demand and may have changed since report generation. Regional selections do not filter this list; movement attribution can differ from current vessel regions. Full records are included in the downloaded snapshot.</p><div class="table-wrap"><table><thead><tr>${columns.map(([,label])=>`<th>${label}</th>`).join('')}</tr></thead><tbody>${rows.slice(0,200).map(r=>`<tr>${columns.map(([key])=>`<td>${e(Array.isArray(r[key])?r[key].join(', '):r[key]??'—')}</td>`).join('')}</tr>`).join('')||`<tr><td colspan="${columns.length}">No records returned.</td></tr>`}</tbody></table></div>${rows.length>200?'<p>First 200 rows shown; all returned rows are in the snapshot download.</p>':''}`;
    } catch(error) {if(version===evidenceVersion) $('mrEvidenceBody').textContent=`Could not load evidence: ${error.message}. Select the count again to retry.`;}
  }
  $('managerReportForm').addEventListener('submit',generate);
  for (const id of ['mrPeriod','mrRegion']) $(id).addEventListener('change',()=>{
    if(!snapshot) return; syncNotes();
    try {render(); $('reportStatus').textContent='Scope updated using the captured snapshot; no data refetched.';} catch(error){$('reportStatus').textContent=error.message; $('mrPrint').disabled=true; $('mrDownload').disabled=true;}
  });
  $('mrReport').addEventListener('input',event=>{if(event.target.id==='mrNotes') syncNotes();});
  $('mrReport').addEventListener('click',event=>{
    const button=event.target.closest('[data-metric]'); if(button) evidence(button);
    const link=event.target.closest('a[href^="#mr"]');
    if(link) {const target=document.querySelector(link.getAttribute('href')); if(target?.tagName==='DETAILS') target.open=true;}
  });
  $('mrPrint').addEventListener('click',()=>{syncNotes(); window.print();});
  $('mrDownload').addEventListener('click',()=>{
    syncNotes();
    const url=URL.createObjectURL(new Blob([JSON.stringify({...snapshot,scope:view,definitions},null,2)],{type:'application/json'}));
    const link=document.createElement('a'); link.href=url; link.download=`${snapshot.id}.json`; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  let openedForPrint=[];
  window.addEventListener('beforeprint',()=>{
    syncNotes(); $('tab-reports').classList.toggle('mr-print-evidence',$('mrAppendix').checked);
    openedForPrint=[...$('mrReport').querySelectorAll('.mr-methodology:not([open]), .mr-daily:not([open])')].filter(el=>el.classList.contains('mr-methodology')||$('mrAppendix').checked);
    openedForPrint.forEach(el=>el.open=true);
  });
  window.addEventListener('afterprint',()=>{openedForPrint.forEach(el=>el.open=false);});
})();
