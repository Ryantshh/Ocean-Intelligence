(() => {
  const $ = id => document.getElementById(id);
  const form = $('reportForm'), content = $('reportContent'), status = $('reportStatus');
  const esc = value => String(value ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num = value => value == null ? '—' : Number(value).toLocaleString('en-GB');
  const date = value => value ? String(value).slice(0,10) : 'Not supplied';
  const stamp = value => String(value).replace('T',' ').slice(0,16) + ' UTC';
  const section = (title, body) => `<section class="report-section"><h3>${title}</h3>${body}</section>`;
  let report = null, version = 0, evidenceOffset = 0, evidenceIssue = 'all';
  const issueNames = {all:'All received orders', missing_weight:'Incomplete weight ranges', missing_laycan:'Missing laycan dates', missing_cargo:'Missing cargo descriptions', invalid_ranges:'Invalid date or weight ranges', due_soon:'Laycans ending within 7 days', elapsed_laycans:'Elapsed laycans', repeated_ids:'Repeated order IDs'};
  const table = rows => `<div class="table-wrap"><table><thead><tr><th>Order · occurrence</th><th>Cargo</th><th>Load region</th><th>Laycan</th><th>Weight (mt)</th></tr></thead><tbody>${rows.map(o=>`<tr><td><button type="button" class="report-order" data-order="${esc(o.order_id)}" data-seq="${esc(o.order_seq)}">${esc(o.order_id)} · ${esc(o.order_seq)}</button></td><td>${esc(o.cargo_type)}</td><td>${esc(o.load_zone)}</td><td>${esc(date(o.laycan_start))} – ${esc(date(o.laycan_end))}</td><td>${num(o.cargo_weight_min)} – ${num(o.cargo_weight_max)}</td></tr>`).join('') || '<tr><td colspan="5">No matching records.</td></tr>'}</tbody></table></div>`;
  const kpi = (label,value,note) => `<div class="report-kpi"><span>${label}</span><strong>${value}</strong><small>${note}</small></div>`;
  function render(r) {
    const m=r.metrics, delta=Number(m.orders)-Number(m.previous_orders);
    const change=Number(m.previous_orders) ? `${delta>=0?'+':''}${(delta/m.previous_orders*100).toFixed(1)}%` : 'No prior-period baseline';
    const open=Number(r.fleet.find(row=>row.status==='OPEN')?.count ?? 0);
    const coverage=Number(m.orders) ? `${Math.round(Number(m.complete_weight)/m.orders*100)}%` : '—';
    const max=Math.max(1,...r.regions.flatMap(row=>[Number(row.orders),Number(row.previous_orders)]));
    const actions=[
      ['invalid_ranges','Check inconsistent ranges','Correct reversed dates, negative weights or a minimum exceeding the maximum before matching.'],
      ['due_soon','Confirm near-term execution','Laycans end within seven days of the current simulation date. Confirm whether these orders remain actionable.'],
      ['elapsed_laycans','Confirm elapsed windows','The recorded laycan has ended. Confirm fulfilment, cancellation or revised dates; elapsed does not mean overdue shipment.'],
      ['missing_weight','Complete capacity information','Confirm both cargo-weight endpoints before assessing vessel suitability.'],
      ['missing_laycan','Confirm shipment timing','Obtain the missing laycan endpoints before scheduling.'],
      ['missing_cargo','Confirm cargo requirements','Obtain the cargo description before checking suitability.'],
      ['repeated_ids','Resolve repeated identifiers','Ask traders whether occurrences are separate shipments or duplicates. Nothing is automatically removed.']
    ].filter(([key])=>Number(m[key])>0);
    const leader=r.regions.find(row=>Number(row.orders)>0);
    const daily=new Map(r.daily.map(row=>[date(row.day),Number(row.orders)]));
    const days=[];
    for(let d=new Date(`${r.start}T00:00:00Z`);d<=new Date(`${r.end}T00:00:00Z`);d.setUTCDate(d.getUTCDate()+1)) {
      const day=d.toISOString().slice(0,10); days.push({day,count:daily.get(day)||0});
    }
    const peak=Math.max(1,...days.map(d=>d.count));
    content.innerHTML=section('Commercial management brief',`<p><strong>${esc(r.start)} – ${esc(r.end)}</strong> · ${esc(r.region||'All regions')}</p><p class="muted">Compared with ${esc(r.previous_start)} – ${esc(r.previous_end)} · Generated ${esc(stamp(r.generated_at))}</p>`)
      + `<div class="report-kpis">${kpi('Orders received',num(m.orders),`${change} · previous ${num(m.previous_orders)}`)}${kpi('Open vessels now',num(open),'Current fleet snapshot')}${kpi('Laycans ending soon',num(m.due_soon),'Received in this period · next 7 simulated days')}${kpi('Weight coverage',coverage,`${num(m.complete_weight)} orders have valid, complete ranges`)}</div>`
      + section('Executive brief',`<ul><li>Received-order volume ${delta>0?'rose':delta<0?'fell':'was unchanged'} by <strong>${num(Math.abs(delta))}</strong> against the preceding equal-length period.</li>${leader?`<li><strong>${esc(leader.region)}</strong> leads the displayed load-region groups with ${num(leader.orders)} orders (${m.orders?Math.round(leader.orders/m.orders*100):0}% of this scope).</li>`:'<li>No received orders in this scope. Broaden the dates or region to review market activity.</li>'}<li>Recorded cargo-weight range: <strong>${num(m.weight_min)}–${num(m.weight_max)} mt</strong>, across ${num(m.complete_weight)} complete, valid order ranges. This is potential demand, not booked cargo.</li></ul>`)
      + `<div class="report-columns">${section('Regional demand comparison',`<p class="muted">Blue: selected period · Lavender: previous period</p>${r.regions.map(row=>`<div class="report-region"><strong>${esc(row.region)}</strong> · ${num(row.orders)} / ${num(row.previous_orders)}<div class="report-bar" style="width:${Number(row.orders)/max*100}%"></div><div class="report-bar previous" style="width:${Number(row.previous_orders)/max*100}%"></div></div>`).join('')||'<p>No orders in either period.</p>'}<p class="muted">Top 12 reported labels. Combined region labels remain grouped.</p>`)}${section('Order intake through the period',`<div class="report-trend" role="img" aria-label="Daily received-order counts; exact figures in the table below">${days.map(d=>`<div title="${esc(d.day)}: ${d.count} orders" style="height:${d.count/peak*100}%"></div>`).join('')}</div><p class="muted">${esc(r.start)} → ${esc(r.end)} · Missing days shown as zero.</p><details><summary>View daily figures</summary><table><thead><tr><th>Date</th><th>Orders</th></tr></thead><tbody>${days.map(d=>`<tr><td>${d.day}</td><td>${num(d.count)}</td></tr>`).join('')}</tbody></table></details><h3 style="margin-top:1.5rem">Current fleet</h3><p>${r.fleet.map(row=>`<strong>${esc(row.status)}: ${num(row.count)}</strong>`).join(' · ')||'No vessels match this region.'}</p><p class="muted">Snapshot at ${esc(stamp(r.reference.tonnage_now))}. Open-region filtering differs from order load-region filtering. These are not historical supply ratios.</p>`)}</div>`
      + section('Management actions',actions.map(([key,title,detail])=>`<div class="report-action"><strong>${title} · ${num(m[key])} ${key==='repeated_ids'?(Number(m[key])===1?'ID':'IDs'):(Number(m[key])===1?'order':'orders')}</strong><p>${detail}</p><button type="button" class="report-evidence" data-issue="${key}">Review supporting records</button></div>`).join('')||'<p>No issues found by the checks in this scope. This is not a complete commercial risk assessment.</p>')
      + section('Evidence and commercial review',`<p>Review the underlying records or prepare a chatbot follow-up. Data is read-only.</p><button type="button" class="report-evidence" data-issue="all">Browse all received orders</button><div id="reportEvidence"><p>Sample: first ${r.orders.length} orders by laycan start, of ${num(m.orders)} received.</p>${table(r.orders)}</div>`)
      + section('Manager notes', '<label for="reportNotes">Decisions, owners and next steps</label><textarea id="reportNotes" maxlength="4000" rows="4" placeholder="Add context for your team…"></textarea><p class="muted">Included in your PDF. Notes stay in this page only and clear when you generate a new report or reload.</p>')
      + section('Coverage and method',`<details><summary>Sources, clocks and limitations</summary><p>Gold order records and the current fleet status view. All metrics are database calculations; no AI-generated figures. Repeated occurrences count separately. Issue counts can overlap and must not be added.</p><p>Order simulation: ${esc(stamp(r.reference.orders_now))}. Fleet simulation: ${esc(stamp(r.reference.tonnage_now))}. Deadline actions use the order simulation date, not the selected report end. Missing receive dates cannot be included in a received-order cohort.</p><p>Historical figures reflect records currently stored and can change after edits. Evidence is fetched live and may differ from this snapshot. Profit ranking, confirmed vessel matches, historical fleet reconstruction, assignments and automatic distribution require additional business inputs or workflow.</p><p>Report ID: ${esc(r.report_id)}</p></details>`);
  }
  async function evidence(issue, offset=0) {
    if(!report) return;
    const token=++version;
    evidenceIssue=issue; evidenceOffset=offset;
    $('reportPrint').disabled=true;
    const target=$('reportEvidence'); target.innerHTML='<p role="status">Loading supporting records…</p>';
    target.scrollIntoView({behavior:'smooth',block:'start'});
    try {
      const params=new URLSearchParams({start:report.start,end:report.end,region:report.region,issue,offset});
      const response=await fetch(`/api/manager-report/evidence?${params}`);
      if(!response.ok) throw new Error('Supporting records are unavailable. Please retry.');
      const data=await response.json(); if(token!==version) return;
      target.innerHTML=`<h4>${esc(issueNames[issue])}</h4><p>${num(data.total)} matching occurrences · live evidence</p>${table(data.rows)}<div class="report-export"><button type="button" class="report-evidence" data-page="-1" ${offset===0?'disabled':''}>Previous</button><span>${data.total?offset+1:0}–${offset+data.rows.length} of ${num(data.total)}</span><button type="button" class="report-evidence" data-page="1" ${offset+data.rows.length>=data.total?'disabled':''}>Next</button></div>`;
    } catch(error) { if(token===version) target.textContent=error.message; }
    finally { if(token===version && report) { $('reportPrint').disabled=false; } }
  }
  $('reportPeriod').addEventListener('change',()=>{
    if($('reportPeriod').value!=='custom') { $('reportStart').value=''; $('reportEnd').value=''; }
    status.textContent='Period changed. Generate a report to apply it.';
  });
  for(const id of ['reportStart','reportEnd']) $(id).addEventListener('change',()=>{$('reportPeriod').value='custom';});
  form.addEventListener('input',()=>{if(report) status.textContent='Filters changed. The report below retains its labelled scope until you generate again.';});
  $('reportPrint').addEventListener('click',async()=>{
    if(!report) return;
    const snapshot=report;
    $('reportPrint').disabled=true;
    status.textContent='Preparing PDF…';
    try {
      const response=await fetch('/api/manager-report/pdf',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({report:snapshot,notes:$('reportNotes')?.value||''})});
      if(!response.ok) throw new Error('PDF export is unavailable. Your report and notes are still available.');
      const url=URL.createObjectURL(await response.blob());
      const a=document.createElement('a'); a.href=url; a.download=`ocean-manager-report-${snapshot.start}-${snapshot.end}.pdf`; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
      if(report===snapshot) status.textContent='PDF downloaded with the report summary, original order sample and manager notes.';
    } catch(error) { if(report===snapshot) status.textContent=error.message; }
    finally { if(report===snapshot) $('reportPrint').disabled=false; }
  });
  form.addEventListener('submit',async event=>{
    event.preventDefault(); ++version;
    $('reportGenerate').disabled=true; $('reportPrint').disabled=true;
    report=null; content.innerHTML=''; status.textContent='Preparing the commercial brief…';
    const params=new URLSearchParams();
    for(const [key,id] of [['start','reportStart'],['end','reportEnd'],['region','reportRegion']]) { const value=$(id).value.trim(); if(value) params.set(key,value); }
    if($('reportPeriod').value!=='custom') params.set('days',$('reportPeriod').value);
    try {
      const response=await fetch(`/api/manager-report?${params}`); const data=await response.json();
      if(!response.ok) throw new Error(response.status===422 && typeof data.detail==='string'?data.detail:'The report is unavailable. Please try again.');
      report=data; render(data);
      $('reportStart').value=data.start; $('reportEnd').value=data.end;
      status.textContent='Report ready. Deadline actions use the current simulation date. Download PDF saves this report and your notes.';
      $('reportPrint').disabled=false;
    } catch(error) { status.textContent=`Could not generate report: ${error.message}`; }
    finally { $('reportGenerate').disabled=false; }
  });
  content.addEventListener('click',event=>{
    const button=event.target.closest('button'); if(!button) return;
    if(button.dataset.order) queueChatPrompt(`Review order ${button.dataset.order}, occurrence ${button.dataset.seq}. Explain its laycan, cargo weight and information needed for a commercial decision.`);
    if(button.dataset.issue) evidence(button.dataset.issue);
    if(button.dataset.page) evidence(evidenceIssue,Math.max(0,evidenceOffset+Number(button.dataset.page)*25));
  });
})();
