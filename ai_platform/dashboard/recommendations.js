(() => {
  const make = (tag, className, value) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (value != null) element.textContent = value;
    return element;
  };
  const panel = make('aside'); panel.id = 'recommendation-panel'; panel.hidden = true;
  panel.setAttribute('aria-label', 'Recommendation workspace');
  panel.innerHTML = `
    <header class="rec-header"><div class="rec-header-top"><span class="rec-eyebrow">Decision support</span><button class="rec-icon-btn" id="rec-close" aria-label="Close recommendation workspace">×</button></div><h2>Find your next match.</h2><p>From cargo enquiry to a considered shortlist.</p></header>
    <div class="rec-scroll">
      <div class="rec-tabs" role="tablist" aria-label="Recommendation views"><button id="rec-new-tab" role="tab" aria-selected="true" aria-controls="rec-new">New review</button><button id="rec-recent-tab" role="tab" aria-selected="false" aria-controls="rec-recent">Recent reviews</button></div>
      <div id="rec-status" class="rec-notice" role="status" hidden></div>
      <div id="rec-new" role="tabpanel" aria-labelledby="rec-new-tab">
        <form id="rec-form">
          <div class="rec-field"><label class="rec-label" for="rec-order">Cargo enquiry</label><input id="rec-order" class="rec-input" name="order_id" placeholder="Search by order ID or loading port" autocomplete="off" required maxlength="80" aria-controls="rec-order-options"><div id="rec-order-options" class="rec-order-options" hidden></div><p id="rec-selected" class="rec-order-selected"></p><p class="rec-hint">Choose an open enquiry or enter its exact order ID.</p></div>
          <div class="rec-field"><label class="rec-label" for="rec-mode">Review approach</label><select class="rec-input" id="rec-mode" name="mode"><option value="ai">AI specialists + screening rules</option><option value="rules">Screening rules only</option></select><p class="rec-hint" id="rec-mode-hint">Logistics and commercial risk review the evidence independently.</p></div>
          <button class="rec-primary" id="rec-generate" disabled>Build shortlist <span aria-hidden="true">↗</span></button>
        </form>
        <div id="rec-results"><div class="rec-empty"><div class="rec-empty-symbol" aria-hidden="true">≋</div><h3>A clearer next move.</h3><p>Select an enquiry to explore vessel fit,<br>review the evidence and record your decision.</p></div></div>
      </div>
      <div id="rec-recent" role="tabpanel" aria-labelledby="rec-recent-tab" hidden><div id="rec-operations" class="rec-hint"></div><a class="rec-export" href="/api/recommendations/evaluation/cases" download="trader-evaluation-cases.json">Export labelled evaluation cases ↗</a><div id="rec-recent-list"></div><details class="rec-details"><summary>Open a review by ID</summary><form id="rec-load"><label for="rec-id" class="rec-label">Review ID</label><input id="rec-id" class="rec-input" name="identifier" required><button class="rec-secondary" style="margin-top:10px">Open review</button></form></details></div>
    </div>
    <footer class="rec-session"><span class="desk-dot"></span><span id="rec-identity">Checking your session…</span></footer>`;
  const backdrop = make('button', 'rec-backdrop'); backdrop.hidden = true; backdrop.setAttribute('aria-label', 'Close recommendation workspace');
  document.body.append(backdrop, panel);
  const toggle = make('button', 'desk-review-toggle'); toggle.type = 'button'; toggle.innerHTML = '<span class="desk-dot"></span>Recommendations <span aria-hidden="true">↗</span>';
  toggle.setAttribute('aria-expanded', 'false'); toggle.setAttribute('aria-controls', panel.id);
  document.querySelector('nav.tabs').append(toggle);
  const $ = id => panel.querySelector('#' + id);
  const status = $('rec-status'); const results = $('rec-results');
  let identity = null, busy = false, selectedOrder = null, searchVersion = 0, pendingDecision = null;
  function notice(message, kind = 'info') { status.textContent = message; status.dataset.kind = kind; status.hidden = !message; }
  async function api(url, body) {
    const response = await fetch(url, body ? {method:'POST', headers:{'Content-Type':'application/json','X-OI-Request':'1'}, body:JSON.stringify(body)} : {});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { const error = new Error(typeof data.detail === 'string' ? data.detail : response.status === 422 ? 'Please check the entered values.' : 'The desk could not reach its data service. Try again.'); error.status = response.status; throw error; }
    return data;
  }
  async function session() {
    try {
      identity = await api('/api/recommendations/session');
      $('rec-identity').textContent = `${identity.identifier} · Working date ${identity.working_date}`;
      $('rec-generate').disabled = false;
    } catch (error) {
      identity = null; $('rec-generate').disabled = true;
      const link = make('a', '', 'Sign in through Assistant'); link.href = '/chat/login';
      link.onclick = event => { event.preventDefault(); close(); document.querySelector('[data-tab="chat"]').click(); };
      $('rec-identity').replaceChildren(link);
      notice(error.message, 'error');
    }
  }
  async function open() { panel.hidden = false; backdrop.hidden = false; document.body.classList.add('rec-open'); toggle.setAttribute('aria-expanded','true'); $('rec-order').focus(); await session(); }
  function close() { panel.hidden = true; backdrop.hidden = true; document.body.classList.remove('rec-open'); toggle.setAttribute('aria-expanded','false'); toggle.focus(); }
  toggle.onclick = () => panel.hidden ? open() : close(); $('rec-close').onclick = close; backdrop.onclick = close;
  panel.onkeydown = event => {
    if (event.key === 'Escape') close();
    if (event.key === 'Tab' && matchMedia('(max-width:1199px)').matches) {
      const focusable = [...panel.querySelectorAll('button,input,select,textarea,a[href],summary')].filter(element => !element.disabled && element.getClientRects().length);
      if (event.shiftKey && document.activeElement === focusable[0]) { event.preventDefault(); focusable.at(-1).focus(); }
      else if (!event.shiftKey && document.activeElement === focusable.at(-1)) { event.preventDefault(); focusable[0].focus(); }
    }
  };
  async function showTab(name) {
    for (const value of ['new','recent']) { $('rec-'+value).hidden = value !== name; $('rec-'+value+'-tab').setAttribute('aria-selected',String(value === name)); $('rec-'+value+'-tab').tabIndex = value === name ? 0 : -1; }
    if (name === 'recent') {
      const list = $('rec-recent-list'); list.replaceChildren(make('p','rec-hint','Loading recent reviews…'));
      try {
        const [runs, metrics] = await Promise.all([api('/api/recommendations'), api('/api/recommendations/metrics')]); list.replaceChildren();
        $('rec-operations').textContent = `${metrics.requests} tracked requests in 24h · ${metrics.degraded + metrics.failed} needing attention · ${metrics.labelled_reviews} labelled for evaluation. ${Number(metrics.prompt_tokens + metrics.completion_tokens).toLocaleString()} reported tokens${metrics.usage_unknown ? ' (some usage unavailable)' : ''}.`;
        if (!runs.length) list.append(make('p','rec-hint','Your desk’s saved reviews will appear here.'));
        for (const run of runs) {
          const button = make('button','rec-recent'); button.append(make('strong','',`Order ${run.order_id}`),make('span','',`${new Date(run.created_at).toLocaleString()} · ${run.decision || 'Awaiting decision'}`));
          button.onclick = () => load(run.id); list.append(button);
        }
      } catch(error) { list.replaceChildren(make('p','rec-hint',error.message)); }
    }
  }
  $('rec-recent-tab').tabIndex = -1;
  for (const name of ['new','recent']) {
    const tab = $('rec-'+name+'-tab'); tab.onclick = () => showTab(name);
    tab.onkeydown = event => { if (['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) { event.preventDefault(); const target = event.key === 'Home' ? 'new' : event.key === 'End' ? 'recent' : name === 'new' ? 'recent' : 'new'; showTab(target); $('rec-'+target+'-tab').focus(); } };
  }
  async function search() {
    const version = ++searchVersion; const query = $('rec-order').value.trim();
    try {
      const orders = await api('/api/recommendations/orders?q='+encodeURIComponent(query));
      if (version !== searchVersion || panel.hidden) return;
      const list = $('rec-order-options'); list.replaceChildren(); list.hidden = !orders.length;
      for (const order of orders) {
        const button = make('button'); button.type = 'button';
        button.append(make('span','',`${order.load_port || order.load_zone || 'Loading port unknown'} · ${Number(order.cargo_weight_min || 0).toLocaleString()} t`),make('small','',`${order.order_id} · ${String(order.laycan_start || '').slice(0,10)}`));
        button.onclick = () => { selectedOrder = order; $('rec-order').value = order.order_id; $('rec-selected').textContent = `${order.load_port || order.load_zone || 'Enquiry selected'} · ${Number(order.cargo_weight_min || 0).toLocaleString()} t minimum`; list.hidden = true; ++searchVersion; };
        list.append(button);
      }
    } catch { $('rec-order-options').hidden = true; }
  }
  let searchTimer;
  $('rec-order').oninput = () => { selectedOrder = null; $('rec-selected').textContent = ''; clearTimeout(searchTimer); ++searchVersion; searchTimer = setTimeout(search,250); };
  $('rec-mode').onchange = () => { $('rec-mode-hint').textContent = $('rec-mode').value === 'ai' ? 'Logistics and commercial risk review the evidence independently.' : 'Screen capacity, availability and freshness without calling AI models.'; };
  $('rec-order').onfocus = () => { if (identity && !$('rec-order').value) search(); };
  const descriptions = {completed:'Specialists reviewed',degraded:'Manual review required',rules_only:'Rules screened',not_needed:'No eligible vessels'};
  function render(run) {
    results.replaceChildren(); pendingDecision = null;
    selectedOrder = run.order; $('rec-order').value = run.order.order_id; $('rec-mode').value = run.mode || 'rules'; $('rec-mode').onchange();
    $('rec-selected').textContent = `${run.order.load_port || run.order.load_zone || 'Enquiry selected'} · ${Number(run.order.cargo_weight_min || 0).toLocaleString()} t minimum`;
    $('rec-order-options').hidden = true;
    const state = make('div','rec-state'); state.append(make('span','rec-pill',descriptions[run.review_status] || 'Saved review'),make('span','',`${(run.latency_ms/1000 || 0).toFixed(1)}s · ${run.candidates.length} shortlisted`)); results.append(state);
    const meta = make('details','rec-details'); meta.append(make('summary','',`Order ${run.order.order_id} · review details`),make('p','rec-hint',`Review ${run.id}\nCreated by ${run.created_by || 'legacy'} · ${run.created_at ? new Date(run.created_at).toLocaleString() : run.as_of}\n${run.ranking_policy}`)); results.append(meta);
    if (run.review_status === 'degraded') results.append(make('p','rec-notice','A specialist could not complete its review. Candidates are held for your manual assessment.'));
    const source = run.evidence_catalogue || {};
    function card(item, index, held) {
      const node = make('article','rec-card');
      const heading = make('div','rec-card-heading'); heading.append(make('h3','',item.vessel_id),make('span','rec-pill',held ? 'On hold' : `Match ${index+1}`)); node.append(heading);
      const metrics = make('div','rec-metrics');
      for (const [label,value] of [['Deadweight',item.evidence.dwt ? Number(item.evidence.dwt).toLocaleString()+' t' : 'Unknown'],['Region',item.evidence.parent_zone || 'Unknown']]) { const field = make('div'); field.append(make('span','',label),make('strong','',value)); metrics.append(field); } node.append(metrics);
      if (held) for (const reason of item.hold_reasons) node.append(make('p','',reason));
      const findings = make('details','rec-findings'); findings.append(make('summary','', 'Explore agent findings & evidence'));
      for (const finding of item.model_findings || []) {
        findings.append(make('h4','',finding.agent.replaceAll('_',' ')+' · '+finding.verdict),make('p','',finding.rationale));
        for (const key of finding.evidence_keys) findings.append(make('div','rec-evidence',`${key.split('.').at(-1).replaceAll('_',' ')}: ${source[key]}`));
      }
      for (const agent of item.agents) for (const note of [...(agent.reasons||[]),...(agent.warnings||[])]) findings.append(make('p','',note));
      node.append(findings); return node;
    }
    run.candidates.forEach((item,index) => results.append(card(item,index,false)));
    if (!run.candidates.length) results.append(make('p','rec-hint','No candidates are cleared for the shortlist. Review the holds and screening exclusions below.'));
    if (run.held?.length) { results.append(make('h3','rec-label',`${run.held.length} held for review`)); run.held.forEach((item,index) => results.append(card(item,index,true))); }
    const exclusions = make('details','rec-details'); exclusions.append(make('summary','',`${run.rejected.length} screening exclusions · ${run.not_shortlisted?.length || 0} additional vessels`));
    for (const item of run.rejected) exclusions.append(make('p','rec-hint',`${item.vessel_id} — ${item.agents.flatMap(agent=>agent.blockers).join('; ')}`)); results.append(exclusions);
    const form = make('form','rec-decision');
    form.innerHTML = `<h3>Make the call.</h3><div class="rec-field"><label class="rec-label" for="rec-action">Your decision</label><select id="rec-action" class="rec-input" name="action"><option value="accept">Accept a shortlisted vessel</option><option value="override">Override the recommendation</option><option value="reject">Reject this recommendation</option></select></div><div class="rec-field"><label class="rec-label" for="rec-vessel">Vessel</label><select id="rec-vessel" class="rec-input" name="vessel_id"><option value="">Select a vessel</option></select></div><div class="rec-field"><label class="rec-label" for="rec-reason">Decision rationale</label><textarea id="rec-reason" class="rec-input" name="reason" rows="3" required maxlength="2000" placeholder="What informed your decision?"></textarea></div><p class="rec-hint">Recorded under your signed-in identity. Source data is checked again before acceptance or override.</p><button class="rec-primary">Record decision</button>`;
    const eligible = new Set(run.candidates.map(item => item.vessel_id));
    for (const item of [...run.candidates,...(run.held||[]),...run.rejected,...(run.not_shortlisted||[])]) { const option = make('option','',item.vessel_id); option.value = item.vessel_id; form.elements.vessel_id.append(option); }
    function sync() { const reject = form.elements.action.value === 'reject'; form.elements.vessel_id.disabled = reject; form.elements.vessel_id.required = !reject; for (const option of form.elements.vessel_id.options) option.disabled = Boolean(option.value && form.elements.action.value === 'accept' && !eligible.has(option.value)); if (form.elements.vessel_id.selectedOptions[0]?.disabled) form.elements.vessel_id.value = ''; }
    if (!eligible.size) { form.elements.action.querySelector('[value="accept"]').disabled = true; form.elements.action.value = 'reject'; }
    form.elements.action.onchange = sync; sync();
    form.onsubmit = async event => {
      event.preventDefault(); if (busy) return; busy = true; const button = form.querySelector('button'); button.disabled = true;
      const body = Object.fromEntries(new FormData(form)); if (body.action === 'reject') body.vessel_id = null; body.expected_version = run.version;
      const fingerprint = JSON.stringify(body); if (!pendingDecision || pendingDecision.fingerprint !== fingerprint) pendingDecision = {fingerprint,id:crypto.randomUUID()}; body.request_id = pendingDecision.id;
      try { const updated = await api(`/api/recommendations/${run.id}/decisions`,body); render(updated); notice('Decision recorded in the shared audit trail.'); }
      catch(error) { notice(error.message,'error'); if (error.status === 409) { const reload = make('button','rec-secondary','Reload saved review'); reload.type = 'button'; reload.onclick = () => load(run.id); status.append(document.createElement('br'),reload); } }
      finally { busy = false; button.disabled = false; }
    };
    results.append(form);
    const assessment = make('details','rec-details rec-evaluation');
    assessment.append(make('summary','','Label this review for evaluation'));
    const labelForm = make('form');
    labelForm.innerHTML = `<p class="rec-hint">Provide your independent judgement. These labels are used for evaluation and do not change a trading decision.</p><div class="rec-field"><label class="rec-label" for="rec-relevant">Suitable vessels</label><textarea class="rec-input" id="rec-relevant" name="relevant" rows="2" placeholder="Vessel IDs, separated by commas"></textarea></div><div class="rec-field"><label class="rec-label" for="rec-forbidden">Vessels that must not be recommended</label><textarea class="rec-input" id="rec-forbidden" name="forbidden" rows="2" placeholder="Vessel IDs, separated by commas"></textarea></div><div class="rec-field"><label class="rec-label" for="rec-label-reason">Assessment rationale</label><textarea class="rec-input" id="rec-label-reason" name="rationale" required maxlength="2000" rows="2"></textarea></div><label class="rec-hint rec-check"><input type="checkbox" required> I reviewed the fleet in this saved run; the suitable list is complete (empty means no suitable vessels).</label><button class="rec-secondary">Save evaluation labels</button>`;
    labelForm.onsubmit = async event => {
      event.preventDefault(); if (busy) return; busy = true; const button = labelForm.querySelector('button'); button.disabled = true;
      const ids = value => value.split(',').map(id=>id.trim()).filter(Boolean);
      try { await api(`/api/recommendations/${run.id}/labels`,{relevant:ids(labelForm.elements.relevant.value),forbidden:ids(labelForm.elements.forbidden.value),rationale:labelForm.elements.rationale.value,complete_review:true}); notice('Evaluation labels saved under your signed-in identity.'); }
      catch(error) { notice(error.message,'error'); }
      finally { busy = false; button.disabled = false; }
    };
    assessment.append(labelForm); results.append(assessment);

    if (run.decisions?.length) {
      const history = make('div','rec-history'); history.append(make('h3','rec-label','Decision history'));
      for (const decision of run.decisions) { history.append(make('p','',`${decision.entered_by} · ${decision.action}${decision.vessel_id ? ' '+decision.vessel_id : ''}`),make('small','',new Date(decision.created_at).toLocaleString()),make('p','rec-hint',decision.reason)); } results.append(history);
    }
  }
  async function load(identifier) { if (busy) return; busy = true; notice('Opening review…'); try { render(await api('/api/recommendations/'+encodeURIComponent(identifier))); await showTab('new'); notice(''); } catch(error) { notice(error.message,'error'); } finally { busy = false; } }
  $('rec-load').onsubmit = event => { event.preventDefault(); load($('rec-id').value.trim()); };
  $('rec-form').onsubmit = async event => {
    event.preventDefault(); if (busy) return;
    const orderId = selectedOrder?.order_id || $('rec-order').value.trim();
    if (!/^\d+$/.test(orderId)) { notice('Choose an enquiry from the search results or enter its numeric order ID.','error'); return; }
    busy = true; const button = $('rec-generate'); button.disabled = true; button.textContent = 'Reviewing evidence…'; $('rec-order-options').hidden = true; ++searchVersion;
    notice($('rec-mode').value === 'ai' ? 'Checking vessel fit and availability. AI reviews can take up to 30 seconds.' : 'Screening vessel capacity, availability and freshness…');
    try { render(await api('/api/recommendations',{order_id:orderId,mode:$('rec-mode').value})); notice(''); }
    catch(error) { notice(error.message,'error'); }
    finally { busy = false; button.disabled = !identity; button.textContent = 'Build shortlist ↗'; }
  };
})();
