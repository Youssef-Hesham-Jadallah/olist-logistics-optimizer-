'use strict';
let token = sessionStorage.getItem('token'), me = null, page = 'overview', META = {};
const RANK = {viewer: 1, operator: 2, admin: 3};
const PAGES = [['overview', 'home', 'viewer'], ['shipments', 'box', 'viewer'], ['compare', 'compare', 'viewer'], ['sourcing', 'pin', 'viewer'], ['batch', 'layers', 'operator'], ['whatif', 'sliders', 'viewer'],
  ['carriers', 'truck', 'viewer'], ['analytics', 'chart', 'viewer'], ['backtest', 'flask', 'viewer'], ['notifications', 'bell', 'operator'], ['import', 'upload', 'admin']];

async function api(path, opts = {}) {
  const h = {...(opts.headers || {})};
  if (token) h.Authorization = 'Bearer ' + token;
  if (opts.json !== undefined) { h['Content-Type'] = 'application/json'; opts.body = JSON.stringify(opts.json); opts.method = opts.method || 'POST'; }
  const r = await fetch(path, {...opts, headers: h});
  if (r.status === 401 && !path.includes('/auth/login')) { logout(); throw new Error('Session expired'); }
  let d; try { d = await r.json(); } catch { d = {detail: 'Unexpected server response'}; }
  if (!r.ok) throw new Error(typeof d.detail === 'string' ? d.detail : JSON.stringify(d.detail));
  return d;
}
const safe = fn => async (...a) => { try { return await fn(...a); } catch (e) { toast(e.message); } };

// ------------------------------------------------------------------ shell
function applyLang() {
  document.documentElement.lang = lang; document.documentElement.dir = lang === 'ar' ? 'rtl' : 'ltr';
  $$('[data-i18n]').forEach(e => e.textContent = t(e.dataset.i18n)); $('#langBtn').textContent = lang === 'ar' ? 'English' : 'العربية'; hydrateIcons(); if (me) buildNav();
}
function buildNav() {
  $('#nav').innerHTML = PAGES.filter(([, , r]) => RANK[me.role] >= RANK[r]).map(([p, ic]) => `<button data-p="${p}" class="${p === page ? 'active' : ''}">${icon(ic)}<span>${esc(t(p))}</span></button>`).join('');
  $$('#nav button').forEach(b => b.onclick = () => show(b.dataset.p));
}
function logout() { token = null; me = null; sessionStorage.removeItem('token'); $('#app').classList.add('hidden'); $('#login').classList.remove('hidden'); }
$('#langBtn').onclick = () => { lang = lang === 'ar' ? 'en' : 'ar'; localStorage.setItem('lang', lang); applyLang(); if (me) show(page); };
$('#themeBtn').onclick = () => { setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'); if (me) show(page); };
$('#logout').onclick = logout; $('#cmdBtn').onclick = () => openCmdk();
$('#loginForm').onsubmit = async e => {
  e.preventDefault(); $('#loginErr').textContent = '';
  try { const d = await api('/api/auth/login', {json: Object.fromEntries(new FormData(e.target))}); token = d.access_token; sessionStorage.setItem('token', token); await boot(); }
  catch (err) { $('#loginErr').textContent = err.message; }
};
async function boot() {
  try { me = await api('/api/auth/me'); META = await api('/api/meta'); } catch { logout(); return; }
  $('#login').classList.add('hidden'); $('#app').classList.remove('hidden'); $('#whoami').textContent = `${me.username} · ${me.role}`; applyLang(); show('overview');
}
async function show(p) {
  page = p; buildNav(); $('#pageTitle').textContent = t(p); $('#crumb').textContent = p.toUpperCase();
  const v = $('#view'); v.classList.remove('view-enter'); void v.offsetWidth; v.classList.add('view-enter');
  v.innerHTML = `<div class="grid g4"><div class="skel"></div><div class="skel"></div><div class="skel"></div><div class="skel"></div></div><div class="skel" style="height:260px"></div>`;
  try { await PAGE[p](); hydrateIcons(v); } catch (e) { v.innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; }
}

// ------------------------------------------------------------------ shared widgets
const ST = {Pending: ['', 'clock'], Booked: ['info', 'check'], 'In Transit': ['warn', 'truck'], Delivered: ['ok', 'check'], Canceled: ['bad', 'x']};
const stPill = s => `<span class="pill ${(ST[s] || [''])[0]}"><span class="dot"></span>${esc(s)}</span>`;
const carrierTag = c => c ? `<span class="cdot" style="background:${ccol(c)}"></span>${esc(c)}` : '<span class="muted">—</span>';
const objectiveSelect = (id = 'objective') => `<select id="${id}">${['balanced', 'cost', 'speed', 'reliability', 'expected_cost', 'custom'].map(o => `<option value="${o}">${esc(t('o_' + o))}</option>`).join('')}</select>`;
const objExtra = () => `<div class="row" id="objExtra" style="margin-top:10px"><div class="field hidden" id="penBox" style="max-width:220px"><label>${t('late_penalty')}</label><input id="penalty" type="number" min="0" step="5" value="${META.default_late_penalty_brl ?? 40}"></div>
  <div class="row hidden" id="wBox">${[['cost', .4], ['speed', .4], ['risk', .2]].map(([k, v]) => `<div class="field" style="max-width:110px"><label>w ${k}</label><input id="w_${k}" type="number" min="0" max="1" step="0.05" value="${v}"></div>`).join('')}</div></div>`;
function bindObjective() { const s = $('#objective'); if (!s) return; s.onchange = () => { $('#wBox').classList.toggle('hidden', s.value !== 'custom'); $('#penBox').classList.toggle('hidden', s.value !== 'expected_cost'); }; }
function readObjective() {
  const o = $('#objective').value, out = {objective: o};
  if (o === 'custom') out.weights = {cost: +$('#w_cost').value, speed: +$('#w_speed').value, risk: +$('#w_risk').value};
  if ($('#penalty') && (o === 'expected_cost' || $('#penalty').value !== '')) out.late_penalty = +$('#penalty').value;
  return out;
}
function acFields(root = document) { $$('input[data-ac]', root).forEach(i => attachAC(i, api)); }
function emptyBlock(msg) { return `<div class="empty">${icon('box')}<div>${esc(msg || t('empty'))}</div></div>`; }
function table(rows, cols, {onRow} = {}) {
  if (!rows.length) return emptyBlock();
  return `<div class="scroll"><table><thead><tr>${cols.map(c => `<th>${esc(c.label)}</th>`).join('')}</tr></thead><tbody>${rows.map((r, i) => `<tr data-i="${i}" class="${r._best ? 'best' : ''}" ${onRow ? 'style="cursor:pointer"' : ''}>${cols.map(c => `<td>${c.html ? c.html(r) : esc(r[c.key])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}
const kpi = (label, val, {sub = '', color = 'var(--brand)', ic = 'box', spark: sp = ''} = {}) => `<div class="card kpi" style="--k:${color}"><div class="lbl">${icon(ic)}${esc(label)}</div><div class="val" data-count="${val ?? ''}">—</div><div class="sub">${sub}</div>${sp ? `<div style="margin-top:6px">${sp}</div>` : ''}</div>`;
function runCounts(root) { $$('[data-count]', root).forEach(e => { const raw = e.dataset.count; if (raw === '') return; countUp(e, +raw, {dec: +(e.dataset.dec || 0), fmt: e.dataset.fmt === 'brl' ? v => brl(v) : e.dataset.fmt === 'pct' ? v => iso(nf(v, 1) + '%') : undefined}); }); }
async function mountMap(id, cfg) { const el = $(id); if (!el) return; await BrazilMap.load(); const m = new BrazilMap(el); m.set(cfg); return m; }

// ETA race: one lane per carrier, bar = P50..P90, dashed line = deadline
function race(res, {assign = false} = {}) {
  const qs = res.quotes; if (!qs.length) return emptyBlock(t('none_feasible'));
  const mx = Math.max(...qs.map(q => q.eta_p90_days), res.deadline_days || 0) * 1.1 || 10;
  return `<div class="race">${qs.map(q => {
    const best = res.recommendation && q.carrier_code === res.recommendation.carrier_code, l = q.eta_p50_days / mx * 100, w = Math.max((q.eta_p90_days - q.eta_p50_days) / mx * 100, 1.5);
    return `<div class="lane ${best ? 'best' : ''} ${q.eligible ? '' : 'dim'}"><div class="who"><b><span class="cdot" style="background:${ccol(q.carrier_code)}"></span>${esc(q.carrier)}${best ? `<span class="badge">${esc(t('recommended'))}</span>` : ''}${q.pareto ? '<span class="badge" style="background:rgba(108,140,255,.2);color:var(--brand)">Pareto</span>' : ''}</b><span class="muted sm">${esc(q.service)}</span></div>
      <div><div class="track"><div class="axis"></div>${res.deadline_days ? `<div class="deadline" style="inset-inline-start:${Math.min(res.deadline_days / mx * 100, 100)}%" data-l="${nf(res.deadline_days, 0)}d"></div>` : ''}<div class="rng" style="inset-inline-start:${l}%;width:${w}%;background:${ccol(q.carrier_code)}"></div><div class="p50" style="inset-inline-start:${l}%"></div></div>
        <div class="muted sm" style="margin-top:4px">P50 <b class="mono">${nf(q.eta_p50_days, 1)}d</b> · P90 <b class="mono">${nf(q.eta_p90_days, 1)}d</b> · ${esc(t('risk'))} <b class="mono">${pct(q.late_risk, 0)}</b> · ${esc(q.reason || '')}</div></div>
      <div class="price"><b>${brl(q.price)}</b><span class="muted sm">${esc(t('expected_cost'))} ${brl(q.expected_cost)}</span>${assign && q.eligible ? `<div style="margin-top:6px"><button class="primary" style="padding:6px 12px" data-assign="${esc(q.carrier_code)}">${esc(t('choose'))}</button></div>` : ''}</div></div>`;
  }).join('')}</div>`;
}
const excludedBlock = res => res.excluded && res.excluded.length ? `<div class="muted sm" style="margin-top:12px">${icon('alert')} <b>${esc(t('excluded'))}:</b> ${res.excluded.map(e => `<span class="pill" style="margin:2px">${esc(e.carrier_code)} · ${esc(e.reason)}</span>`).join('')}</div>` : '';
function paretoPlot(res) {
  const pts = res.quotes.filter(q => q.eligible).map(q => ({x: q.eta_p90_days, y: q.price, label: q.carrier_code, color: ccol(q.carrier_code), star: res.recommendation && q.carrier_code === res.recommendation.carrier_code, r: q.pareto ? 11 : 7}));
  return pts.length ? bubbles(pts.map(p => ({...p, color: rawColor(p.color)})), {xLabel: t('eta_p90') + ' (days)', yLabel: 'BRL', fx: v => nf(v, 0), fy: v => nf(v, 0)}) : emptyBlock();
}

// ------------------------------------------------------------------ pages
const PAGE = {
  async overview() {
    const [k, tl, fl] = await Promise.all([api('/api/kpis'), api('/api/analytics/timeline'), api('/api/analytics/flows')]);
    const months = tl.months.filter(m => m.shipments > 20), L = months.reduce((a, m) => a + m.late, 0), J = months.reduce((a, m) => a + m.judged, 0), by = k.by_status, palette = ['ALPHA', 'BETA', 'GAMMA', 'DELTA', 'EPSILON'];
    const top = fl.flows.filter(f => f.o !== f.d).slice(0, 18), mxn = Math.max(...top.map(f => f.n), 1);
    $('#view').innerHTML = `<div class="card hero"><div class="txt"><span class="pill info">${icon('zap')} ${esc(t('overview'))}</span><h2><span class="grad">${esc(t('hero_title'))}</span></h2><p class="muted" style="margin:0;max-width:46ch">${esc(t('hero_sub'))}</p>
        <div class="legend sm muted">${esc(t('flows_legend'))}</div></div><canvas id="heroMap"></canvas></div>
      <div class="notice info">${icon('shield')} ${esc(t('simulated_notice'))}</div>
      <div class="grid g4">${kpi(t('total'), k.total, {ic: 'box', sub: `${nf(k.pending, 0)} ${t('pending').toLowerCase()}`, spark: spark(months.map(m => m.shipments))})}${kpi(t('delivered'), k.delivered, {ic: 'check', color: 'var(--good)', sub: pct(k.delivered / (k.total || 1), 1)})}
        ${kpi(t('in_transit'), k.in_transit, {ic: 'truck', color: 'var(--amber)', sub: `${nf(k.booked, 0)} ${t('booked').toLowerCase()}`})}${kpi(t('canceled'), k.canceled, {ic: 'x', color: 'var(--bad)', sub: pct(k.canceled / (k.total || 1), 2)})}</div>
      <div class="grid g4"><div class="card kpi" style="--k:var(--brand2)"><div class="lbl">${icon('coin')}${esc(t('avg_hist'))}</div><div class="val" data-count="${k.avg_historical_freight ?? ''}" data-dec="2" data-fmt="brl">—</div><div style="margin-top:6px">${spark(months.map(m => m.avg_freight).filter(v => v != null), 'var(--brand2)')}</div></div>
        ${kpi(t('avg_transit'), k.avg_historical_transit_days, {ic: 'clock', color: 'var(--brand)', sub: '', spark: spark(months.map(m => m.avg_transit_days).filter(v => v != null))}).replace('data-count="' + k.avg_historical_transit_days + '"', 'data-count="' + k.avg_historical_transit_days + '" data-dec="1"')}
        <div class="card kpi" style="--k:var(--bad)"><div class="lbl">${icon('alert')}${esc(t('late_rate'))}</div><div class="val" data-count="${J ? L / J * 100 : ''}" data-fmt="pct">—</div><div style="margin-top:6px">${spark(months.map(m => m.late_rate).filter(v => v != null), 'var(--bad)')}</div></div>
        ${kpi(t('avg_booked'), k.avg_booked_price, {ic: 'coin', color: 'var(--good)', sub: `${nf(k.assigned, 0)} ${t('booked').toLowerCase()}`}).replace('data-count="' + k.avg_booked_price + '"', 'data-count="' + (k.avg_booked_price ?? '') + '" data-dec="2" data-fmt="brl"')}</div>
      <div class="grid g21"><div class="panel"><h2>${esc(t('timeline'))}</h2>${areaLine(tl.months.map(m => ({x: m.month.slice(2), y: m.shipments})))}</div>
        <div class="panel"><h2>${esc(t('status_mix'))}</h2><div class="row" style="justify-content:center">${donut(Object.entries(by).map(([s, n]) => ({label: s, value: n, color: rawColor({Pending: 'var(--muted)', Booked: 'var(--brand)', 'In Transit': 'var(--amber)', Delivered: 'var(--good)', Canceled: 'var(--bad)'}[s] || '#888')})), {center: nf(k.total, 0)})}<div style="display:grid;gap:6px">${Object.entries(by).map(([s, n]) => `<div class="sm">${stPill(s)} <b class="mono">${nf(n, 0)}</b></div>`).join('')}</div></div></div></div>`;
    runCounts($('#view'));
    mountMap('#heroMap', {arcs: top.map((f, i) => ({a: f.o_ll, b: f.d_ll, color: `var(--c-${palette[i % 5]})`, w: 1 + 2.6 * f.n / mxn, parcels: f.n / mxn > .4 ? 3 : 2, speed: .12 + .18 * (f.n / mxn)})), points: Object.entries(fl.states).slice(0, 0)});
  },

  async shipments(state = {q: '', status: '', offset: 0, open: false}) {
    const L = 14, d = await api(`/api/shipments?limit=${L}&offset=${state.offset}&q=${encodeURIComponent(state.q)}${state.status ? '&status=' + encodeURIComponent(state.status) : ''}`), canOp = RANK[me.role] >= 2;
    const reload = (p = {}) => PAGE.shipments({...state, ...p});
    $('#view').innerHTML = `${canOp ? `<div class="panel"><div class="row" style="justify-content:space-between;align-items:center"><h2 style="margin:0">${esc(t('new_shipment'))}</h2><button class="ghost" id="togNew">${icon('box')} ${esc(t('create'))}</button></div>
      <form id="newForm" class="row ${state.open ? '' : 'hidden'}" style="margin-top:14px"><div class="field"><label>${esc(t('origin'))}</label><input name="origin" data-ac value="Campinas, SP" required></div><div class="field"><label>${esc(t('destination'))}</label><input name="destination" data-ac value="Recife, PE" required></div>
        <div class="field" style="max-width:100px"><label>${esc(t('weight'))}</label><input name="weight_kg" type="number" step="0.1" min="0.1" value="2" required></div><div class="field" style="max-width:150px"><label>${esc(t('deadline_days').replace('(days)', '').trim())}</label><input name="deadline" type="date"></div>
        <div class="field"><label>${esc(t('notify_email'))}</label><input name="notify_email" type="email" placeholder="customer@example.com"></div><button class="primary">${icon('check')} ${esc(t('create'))}</button></form></div>` : ''}
      <div class="panel"><div class="row" style="margin-bottom:14px"><div class="field" style="min-width:220px"><input id="q" placeholder="${esc(t('search_ph'))}" value="${esc(state.q)}"></div>
        <div class="chips">${['', 'Pending', 'Booked', 'In Transit', 'Delivered', 'Canceled'].map(s => `<button class="chip ${s === state.status ? 'on' : ''}" data-st="${s}">${s ? esc(s) : 'All'}</button>`).join('')}</div></div>
      ${table(d.items, [{label: 'ID', html: r => `<code>${esc(r.order_id.length > 16 ? r.order_id.slice(0, 15) + '…' : r.order_id)}</code>`}, {label: t('origin'), key: 'origin'}, {label: t('destination'), key: 'destination'}, {label: 'kg', html: r => nf(r.weight_kg, 2)},
        {label: t('status'), html: r => stPill(r.status)}, {label: t('carrier'), html: r => carrierTag(r.carrier_code)}, {label: t('price'), html: r => brl(r.price)}], {onRow: true})}
      <div class="pager"><span class="muted sm">${d.total ? state.offset + 1 : 0}–${Math.min(state.offset + L, d.total)} / ${nf(d.total, 0)}</span><button class="ghost sm" id="prev" ${state.offset ? '' : 'disabled'}>${esc(t('prev'))}</button><button class="ghost sm" id="next" ${state.offset + L < d.total ? '' : 'disabled'}>${esc(t('next'))}</button></div></div>`;
    acFields();
    if (canOp) { $('#togNew').onclick = () => $('#newForm').classList.toggle('hidden'); $('#newForm').onsubmit = safe(async e => { e.preventDefault(); const f = Object.fromEntries(new FormData(e.target)); f.weight_kg = +f.weight_kg; ['deadline', 'notify_email'].forEach(k => { if (!f[k]) delete f[k]; }); const s = await api('/api/shipments', {json: f}); toast('✓ ' + s.order_id); await reload({offset: 0, open: false}); openShipment(s.id, () => reload()); }); }
    let tm; $('#q').oninput = e => { clearTimeout(tm); tm = setTimeout(() => reload({q: e.target.value, offset: 0}), 350); };
    $$('[data-st]').forEach(b => b.onclick = () => reload({status: b.dataset.st, offset: 0}));
    $('#prev').onclick = () => reload({offset: Math.max(0, state.offset - L)}); $('#next').onclick = () => reload({offset: state.offset + L});
    $$('tbody tr').forEach(tr => tr.onclick = safe(() => openShipment(d.items[+tr.dataset.i].id, () => reload())));
  },

  async compare() {
    $('#view').innerHTML = `<div class="panel"><form id="qf"><div class="row"><div class="field" style="min-width:180px"><label>${esc(t('origin'))}</label><input name="origin" data-ac value="Sao Paulo, SP" required></div><div class="field" style="min-width:180px"><label>${esc(t('destination'))}</label><input name="destination" data-ac value="Manaus, AM" required></div>
      <div class="field" style="max-width:100px"><label>${esc(t('weight'))}</label><input name="weight_kg" type="number" step="0.1" min="0.1" value="2.5"></div><div class="field" style="max-width:130px"><label>${esc(t('dims'))}</label><input name="dims" placeholder="30x20x15" value="30x20x15"></div>
      <div class="field" style="max-width:120px"><label>${esc(t('value'))}</label><input name="declared_value" type="number" min="0" value="150"></div><div class="field" style="max-width:130px"><label>${esc(t('deadline_days'))}</label><input name="deadline_days" type="number" min="1" placeholder="—" value="35"></div>
      <div class="field" style="max-width:210px"><label>${esc(t('objective'))}</label>${objectiveSelect()}</div><button class="primary">${icon('compare')} ${esc(t('compare_btn'))}</button></div>${objExtra()}</form></div><div id="qres"></div>`;
    acFields(); bindObjective();
    $('#qf').onsubmit = safe(async e => {
      e.preventDefault(); const f = Object.fromEntries(new FormData(e.target)), body = {origin: f.origin, destination: f.destination, weight_kg: +f.weight_kg, declared_value: +f.declared_value || 0, ...readObjective()};
      if (f.deadline_days) body.deadline_days = +f.deadline_days;
      const m = (f.dims || '').toLowerCase().match(/^(\d+(?:\.\d+)?)\s*[x×*]\s*(\d+(?:\.\d+)?)\s*[x×*]\s*(\d+(?:\.\d+)?)$/); if (m) { body.length_cm = +m[1]; body.height_cm = +m[2]; body.width_cm = +m[3]; }
      $('#qres').innerHTML = `<div class="skel" style="height:300px"></div>`; const r = await api('/api/quotes', {json: body}), q0 = r.quotes[0], rec = r.recommendation;
      $('#qres').innerHTML = `<div class="row" style="margin-bottom:12px"><span class="pill info">${icon('route')} ${esc(r.origin.label)} → ${esc(r.destination.label)}</span>${q0 ? `<span class="pill">${nf(q0.distance_km, 0)} km · zone ${q0.zone}</span>` : ''}${r.season_applied ? `<span class="pill warn">${icon('alert')} ${esc(t('season_note'))}</span>` : ''}<span class="pill">${esc(t('late_penalty').replace(' (R$)', ''))}: R$ ${nf(r.late_penalty, 0)}</span></div>
        ${r.infeasible ? `<div class="notice bad">${icon('alert')} ${esc(t('none_feasible'))}${r.infeasible.fastest_available ? ' — ' + esc(r.infeasible.fastest_available.carrier_code) + ' ' + nf(r.infeasible.fastest_available.eta_p90_days, 1) + 'd (P90)' : ''}</div>` : ''}
        <div class="panel"><h2>${esc(t('race_title'))}</h2>${race(r)}${excludedBlock(r)}</div>
        <div class="grid g2"><div class="panel"><h2>${esc(t('route'))}</h2><canvas id="cmpMap" style="width:100%;height:340px;display:block"></canvas></div><div class="panel"><h2>${esc(t('price'))} × ${esc(t('eta_p90'))}</h2>${paretoPlot(r)}</div></div>`;
      mountMap('#cmpMap', {arcs: [{a: [r.origin.lat, r.origin.lng], b: [r.destination.lat, r.destination.lng], color: rec ? ccol(rec.carrier_code) : 'var(--brand)', w: 2.6, parcels: 3, speed: .25}], points: [{lat: r.origin.lat, lng: r.origin.lng, label: r.origin.label, color: 'var(--good)', big: 1}, {lat: r.destination.lat, lng: r.destination.lng, label: r.destination.label, color: 'var(--amber)', big: 1, ph: .5}]});
    });
    $('#qf').requestSubmit();
  },

  async sourcing() {
    const prods = await api('/api/products/multi-seller?limit=12');
    $('#view').innerHTML = `<div class="panel"><div class="notice info">${icon('alert')} Olist has no inventory data: candidates are sellers that sold the product before.</div><form id="sf" class="row"><div class="field" style="flex:2"><label>${esc(t('product'))}</label><select name="product_id">${prods.map(p => `<option value="${esc(p.product_id)}">${esc(p.category || '?')} · ${p.sellers} sellers · ${nf(p.weight_kg, 2)}kg · ${esc(p.product_id.slice(0, 8))}</option>`).join('')}</select></div>
      <div class="field"><label>${esc(t('destination'))}</label><input name="destination" data-ac value="Salvador, BA" required></div><div class="field" style="max-width:90px"><label>${esc(t('qty'))}</label><input name="quantity" type="number" min="1" value="1"></div>
      <div class="field" style="max-width:210px"><label>${esc(t('objective'))}</label>${objectiveSelect()}</div><button class="primary">${icon('pin')} ${esc(t('run'))}</button></form></div><div id="sres"></div>`;
    acFields();
    $('#sf').onsubmit = safe(async e => {
      e.preventDefault(); const f = Object.fromEntries(new FormData(e.target)), o = $('#objective').value;
      const r = await api('/api/sourcing/rank', {json: {product_id: f.product_id, destination: f.destination, quantity: +f.quantity, objective: ['custom'].includes(o) ? 'balanced' : o}});
      const cs = r.candidates;
      $('#sres').innerHTML = `<div class="grid g21"><div class="panel"><h2>${esc(t('route'))}</h2><canvas id="srcMap" style="width:100%;height:380px;display:block"></canvas></div><div class="panel"><h2>${esc(t('sourcing'))}</h2>${table(cs.map(c => ({...c, _best: c.rank === 1})), [{label: '#', key: 'rank'}, {label: t('seller'), html: r => `<code>${esc(r.seller_id.slice(0, 8))}</code><div class="muted sm">${esc(r.origin.label)}</div>`}, {label: t('distance'), html: r => nf(r.distance_km, 0)},
        {label: t('carrier'), html: r => r.best_option ? carrierTag(r.best_option.carrier_code) : '—'}, {label: t('price'), html: r => r.best_option ? brl(r.best_option.price) : '—'}, {label: t('eta_p90'), html: r => r.best_option ? nf(r.best_option.eta_p90_days, 1) + ' d' : '—'}])}</div></div>`;
      mountMap('#srcMap', {arcs: cs.map(c => ({a: [c.origin.lat, c.origin.lng], b: [r.destination.lat, r.destination.lng], color: c.rank === 1 ? 'var(--good)' : 'var(--brand)', w: c.rank === 1 ? 3 : 1.2, dim: c.rank > 1 && c.rank > 4, parcels: c.rank === 1 ? 3 : 1, speed: .2})),
        points: [{lat: r.destination.lat, lng: r.destination.lng, label: r.destination.label, color: 'var(--amber)', big: 1}, ...cs.slice(0, 6).map(c => ({lat: c.origin.lat, lng: c.origin.lng, label: '#' + c.rank, color: c.rank === 1 ? 'var(--good)' : 'var(--brand)', ph: c.rank / 7}))]});
    });
    $('#sf').requestSubmit();
  },

  async batch() {
    $('#view').innerHTML = `<div class="panel"><div class="notice info">${icon('layers')} MILP (HiGHS): assigns Pending shipments to carriers minimising the chosen objective under each carrier's remaining daily capacity.</div><form id="bf" class="row"><div class="field" style="max-width:120px"><label>${esc(t('orders'))}</label><input name="limit" type="number" min="1" max="2000" value="300"></div>
      <div class="field" style="max-width:220px"><label>${esc(t('objective'))}</label>${objectiveSelect()}</div><label class="row" style="gap:6px;align-items:center"><input type="checkbox" id="relax"> ${esc(t('relax'))}</label><label class="row" style="gap:6px;align-items:center"><input type="checkbox" id="applyChk"> ${esc(t('apply_plan'))}</label><button class="primary">${icon('zap')} ${esc(t('run'))}</button></form>${objExtra()}</div><div id="bres"></div>`;
    bindObjective();
    $('#bf').onsubmit = safe(async e => {
      e.preventDefault(); $('#bres').innerHTML = `<div class="skel" style="height:260px"></div>`;
      const r = await api('/api/optimize/batch', {json: {limit: +new FormData(e.target).get('limit'), apply: $('#applyChk').checked, relax_deadline: $('#relax').checked, ...readObjective()}}), o = r.optimized, load = o.carrier_load || {}, totL = Object.values(load).reduce((a, b) => a + b, 0) || 1;
      const rows = Object.entries(r.baselines).map(([k, v]) => ({policy: k.replace(/_/g, ' '), ...v, vo: v.vs_optimized || {}}));
      $('#bres').innerHTML = `<div class="grid g4">${kpi(t('orders'), o.assigned, {ic: 'box', sub: `${nf(r.unassigned.length, 0)} unassigned`})}${kpi(t('price'), o.total_cost, {ic: 'coin', color: 'var(--brand2)', sub: 'BRL'}).replace('data-count="' + o.total_cost + '"', 'data-count="' + o.total_cost + '" data-fmt="brl" data-dec="2"')}
        ${kpi(t('expected_cost'), o.total_expected_cost, {ic: 'shield', color: 'var(--good)', sub: 'price + late penalty'}).replace('data-count="' + o.total_expected_cost + '"', 'data-count="' + o.total_expected_cost + '" data-fmt="brl" data-dec="2"')}${kpi(t('risk'), o.expected_late_orders, {ic: 'alert', color: 'var(--bad)', sub: 'expected late orders'}).replace('data-count="' + o.expected_late_orders + '"', 'data-count="' + o.expected_late_orders + '" data-dec="1"')}</div>
        <div class="grid g2"><div class="panel"><h2>${esc(t('carriers'))}</h2><div style="display:flex;height:16px;border-radius:9px;overflow:hidden;margin-bottom:12px">${Object.entries(load).map(([c, n]) => `<div title="${esc(c)} ${n}" style="width:${n / totL * 100}%;background:${ccol(c)}"></div>`).join('')}</div>${Object.entries(load).map(([c, n]) => `<div class="sm" style="margin:5px 0">${carrierTag(c)} <b class="mono">${nf(n, 0)}</b> <span class="muted">/ cap ${nf(r.capacity_used[c], 0)}</span></div>`).join('')}</div>
          <div class="panel"><h2>Unassigned</h2>${Object.keys(r.unassigned_summary || {}).length ? Object.entries(r.unassigned_summary).map(([k, n]) => `<div style="margin:6px 0"><span class="pill ${k === 'DEADLINE_INFEASIBLE' ? 'warn' : 'bad'}">${esc(k)}</span> <b class="mono">${n}</b></div>`).join('') + `<p class="muted sm">${esc((r.unassigned[0] || {}).reason || '')}</p>` : `<div class="pill ok">${icon('check')} all assigned</div>`}${r.relaxed_assignments ? `<p class="sm"><span class="pill warn">${r.relaxed_assignments}</span> assigned with deadline at risk</p>` : ''}${r.applied != null ? `<p class="sm"><span class="pill ok">${r.applied}</span> booked</p>` : ''}</div></div>
        <div class="panel"><h2>${esc(t('policies'))}</h2>${table(rows, [{label: 'Policy', key: 'policy'}, {label: t('orders'), key: 'assigned'}, {label: 'Cost vs optimized', html: r => r.vo.cost_change_pct == null ? '—' : `<span class="pill ${r.vo.cost_change_pct <= 0 ? 'ok' : 'warn'}">${r.vo.cost_change_pct <= 0 ? 'optimized ' + nf(r.vo.cost_change_pct, 1) + '%' : 'optimized +' + nf(r.vo.cost_change_pct, 1) + '%'}</span>`},
          {label: 'ETA', html: r => r.vo.avg_eta_change_days == null ? '—' : nf(r.vo.avg_eta_change_days, 1) + ' d'}, {label: 'Late orders', html: r => r.vo.expected_late_change == null ? '—' : nf(r.vo.expected_late_change, 1)}, {label: t('expected_cost'), html: r => r.vo.expected_cost_change_pct == null ? '—' : nf(r.vo.expected_cost_change_pct, 1) + '%'}])}<p class="muted sm">Like-for-like on the same orders. Negative = optimized is better.</p></div>`;
      runCounts($('#bres'));
    });
  },

  async whatif() {
    $('#view').innerHTML = `<div class="panel"><div class="row"><div class="field" style="flex:3"><label>${esc(t('o_cost'))} ←→ ${esc(t('o_speed'))}: <b id="wl">50</b>% cost</label><input id="slider" type="range" min="0" max="100" step="10" value="50"></div><button class="primary" id="wgo">${icon('sliders')} ${esc(t('run'))}</button></div></div><div id="wres"><div class="skel" style="height:260px"></div></div>`;
    let data = null;
    const draw = () => { if (!data) return; const w = +$('#slider').value / 100, i = data.sweep.reduce((a, b, ix) => Math.abs(b.w_cost - w) < Math.abs(data.sweep[a].w_cost - w) ? ix : a, 0), pt = data.sweep[i], s = data.sweep.map(x => ({x: Math.round(x.w_cost * 100) + '%', y: x.total_cost})), l = data.sweep.map(x => ({x: Math.round(x.w_cost * 100) + '%', y: x.expected_late_orders}));
      $('#wres').innerHTML = `<div class="grid g3">${kpi('Total cost', pt.total_cost, {ic: 'coin', color: 'var(--brand2)'}).replace('data-count="' + pt.total_cost + '"', 'data-count="' + pt.total_cost + '" data-fmt="brl"')}${kpi(t('eta_p50'), pt.avg_eta_p50_days, {ic: 'clock'}).replace('data-count="' + pt.avg_eta_p50_days + '"', 'data-count="' + pt.avg_eta_p50_days + '" data-dec="1"')}${kpi('Expected late orders', pt.expected_late_orders, {ic: 'alert', color: 'var(--bad)'}).replace('data-count="' + pt.expected_late_orders + '"', 'data-count="' + pt.expected_late_orders + '" data-dec="1"')}</div>
        <div class="grid g2"><div class="panel"><h2>Total cost (BRL) by cost weight</h2>${areaLine(s, {marker: i, color: 'var(--brand2)'})}</div><div class="panel"><h2>Expected late orders</h2>${areaLine(l, {marker: i, color: 'var(--bad)', fmtY: v => nf(v, 1)})}</div></div><p class="muted sm">${nf(data.orders_considered, 0)} Pending shipments · cost weight ${Math.round(pt.w_cost * 100)}% / speed ${Math.round(pt.w_speed * 100)}%</p>`; runCounts($('#wres')); };
    const load = safe(async () => { data = await api('/api/simulate/what-if', {json: {limit: 300}}); draw(); });
    $('#slider').oninput = e => { $('#wl').textContent = e.target.value; draw(); }; $('#wgo').onclick = load; await load();
  },

  async carriers() {
    const [cs, hs, sc] = await Promise.all([api('/api/carriers'), api('/api/carriers/health'), api('/api/analytics/scorecard')]), adm = me.role === 'admin', H = Object.fromEntries(hs.map(h => [h.carrier_code, h]));
    $('#view').innerHTML = `<div class="notice info">${icon('shield')} ${esc(t('simulated_notice'))}</div><div class="grid g3">${cs.map(c => { const up = H[c.code] && H[c.code].ok, u = Math.min(c.load_today / (c.daily_capacity || 1) * 100, 100);
      return `<div class="card" style="border-top:3px solid ${ccol(c.code)}"><div class="row" style="justify-content:space-between;align-items:flex-start"><div><b style="font-size:16px">${esc(c.name)}</b><div class="muted sm">${esc(c.service)}</div></div><span class="pill ${up ? 'ok' : 'bad'}"><span class="dot ${up ? 'pulse' : ''}"></span>${up ? 'OK' : 'DOWN'}</span></div>
        <div class="row" style="margin:14px 0;align-items:center"><div class="ring" style="--p:${u};--c:${ccol(c.code)}"><b>${nf(u, 0)}%</b></div><div class="kv"><span>${esc(t('capacity'))}</span><span>${nf(c.load_today, 0)} / ${nf(c.daily_capacity, 0)}</span><span>Price×</span><span>${c.price_mult}</span><span>Speed×</span><span>${c.speed_mult}</span><span>Max kg</span><span>${c.max_weight_kg}</span></div></div>
        <div class="muted sm" style="margin-bottom:10px">${esc(t('coverage'))}: ${c.coverage_states ? esc(c.coverage_states.join(' ')) : 'All Brazil'}</div>${adm ? (up ? `<button class="ghost sm danger" data-out="${esc(c.code)}">${icon('alert')} ${esc(t('outage'))}</button>` : `<button class="ghost sm" data-rec="${esc(c.code)}">${icon('check')} ${esc(t('recover'))}</button>`) : ''}</div>`; }).join('')}</div>
      <div class="panel"><h2>${esc(t('scorecard'))} <span class="muted">· ${nf(sc.routes_sampled, 0)} real routes</span></h2>${table(sc.carriers, [{label: t('carrier'), html: r => carrierTag(r.carrier_code)}, {label: t('coverage'), html: r => `<div class="bar" style="width:90px"><i style="width:${r.coverage_pct}%"></i></div>${nf(r.coverage_pct, 0)}%`}, {label: t('price'), html: r => brl(r.avg_price)},
        {label: t('eta_p50'), html: r => nf(r.avg_eta_p50, 1) + ' d'}, {label: t('eta_p90'), html: r => nf(r.avg_eta_p90, 1) + ' d'}, {label: t('risk'), html: r => pct(r.avg_late_risk, 1)}, {label: 'Wins', html: r => nf(r.win_share_pct, 0) + '%'}, {label: t('o_cost'), html: r => nf(r.cheapest_share_pct, 0) + '%'}, {label: t('o_speed'), html: r => nf(r.fastest_share_pct, 0) + '%'}])}<p class="muted sm">${esc(sc.note)}</p></div>`;
    $$('[data-out]').forEach(b => b.onclick = safe(async () => { await api(`/api/carriers/${b.dataset.out}/simulate-outage?down=true`, {method: 'POST'}); show('carriers'); }));
    $$('[data-rec]').forEach(b => b.onclick = safe(async () => { await api(`/api/carriers/${b.dataset.rec}/simulate-outage?down=false`, {method: 'POST'}); show('carriers'); }));
  },

  async analytics() {
    const [a, k, tl] = await Promise.all([api('/api/analytics'), api('/api/kpis'), api('/api/analytics/timeline')]), h = a.historical, months = tl.months.filter(m => m.shipments > 20);
    $('#view').innerHTML = `<div class="grid g4">${kpi(t('sla'), h.sla_breach_rate == null ? null : h.sla_breach_rate * 100, {ic: 'alert', color: 'var(--bad)', sub: `${nf(h.sla_breaches, 0)} / ${nf(h.delivered_with_deadline, 0)}`}).replace('data-count="' + (h.sla_breach_rate == null ? '' : h.sla_breach_rate * 100) + '"', 'data-count="' + (h.sla_breach_rate == null ? '' : h.sla_breach_rate * 100) + '" data-fmt="pct"')}
      ${kpi(t('avg_hist'), k.avg_historical_freight, {ic: 'coin', color: 'var(--brand2)'}).replace('data-count="' + k.avg_historical_freight + '"', 'data-count="' + k.avg_historical_freight + '" data-fmt="brl"')}${kpi(t('avg_transit'), k.avg_historical_transit_days, {ic: 'clock'}).replace('data-count="' + k.avg_historical_transit_days + '"', 'data-count="' + k.avg_historical_transit_days + '" data-dec="1"')}${kpi(t('avg_booked'), k.avg_booked_price, {ic: 'truck', color: 'var(--good)'}).replace('data-count="' + k.avg_booked_price + '"', 'data-count="' + (k.avg_booked_price ?? '') + '" data-fmt="brl"')}</div>
      <div class="grid g2"><div class="panel"><h2>${esc(t('late_rate'))} (%)</h2>${areaLine(months.filter(m => m.late_rate != null).map(m => ({x: m.month.slice(2), y: m.late_rate * 100})), {color: 'var(--bad)', fmtY: v => nf(v, 0) + '%'})}</div><div class="panel"><h2>${esc(t('avg_hist'))}</h2>${areaLine(months.filter(m => m.avg_freight != null).map(m => ({x: m.month.slice(2), y: m.avg_freight})), {color: 'var(--brand2)', fmtY: v => nf(v, 0)})}</div></div>
      <div class="panel"><h2>${esc(t('carriers'))} — booked</h2>${table(a.carriers, [{label: t('carrier'), html: r => carrierTag(r.carrier_code)}, {label: t('shipments'), key: 'n'}, {label: t('price'), html: r => brl(r.avg_price)}, {label: t('eta_p50'), html: r => nf(r.avg_eta, 1)}, {label: t('risk'), html: r => pct(r.avg_late_risk, 1)}, {label: 'On-time', html: r => r.on_time_rate == null ? '—' : pct(r.on_time_rate, 1)}, {label: 'SLA breaches', key: 'breaches'}])}</div>
      <div class="panel"><h2>${esc(t('routes_cost'))}</h2>${table(a.routes, [{label: t('route'), html: r => `<b>${esc(r.o)}</b> → <b>${esc(r.d)}</b>`}, {label: t('shipments'), html: r => nf(r.n, 0)}, {label: 'Avg cost', html: r => brl(r.avg_cost)}, {label: 'Avg transit', html: r => nf(r.avg_transit_days, 1) + ' d'}])}</div>`;
    runCounts($('#view'));
  },

  async backtest() {
    const b = await api('/api/backtest'), P = b.policies, G = b.gains, N = b.nearest_source, A = b.model_accuracy, nm = {random: 'Random carrier', status_quo_single_carrier: 'Status quo', cheapest: 'Always cheapest', fastest: 'Always fastest', system_cost_with_deadline: 'SYSTEM · cheapest within deadline', system_expected_cost: 'SYSTEM · lowest expected cost', system_balanced: 'SYSTEM · balanced'};
    const pts = Object.entries(P).map(([k, v]) => ({x: v.avg_cost_brl, y: v.expected_late_rate * 100, label: nm[k] || k, color: rawColor(k.startsWith('system') ? 'var(--good)' : k === 'status_quo_single_carrier' ? 'var(--amber)' : 'var(--brand)'), star: k.startsWith('system'), r: k.startsWith('system') ? 11 : 8}));
    const g1 = G.system_cost_with_deadline_vs_status_quo, g2 = G.system_balanced_vs_status_quo, pm = A.price_previous_model;
    $('#view').innerHTML = `<div class="notice info">${icon('flask')} Hold-out backtest: models fitted on orders before ${esc(b.train_until)}, evaluated on ${nf(b.holdout_orders_evaluated, 0)} unseen later orders. Carriers are simulated → savings hold inside the simulated universe.</div>
      <div class="grid g4">${kpi('Price model R² (hold-out)', A.price.r2, {ic: 'chart', sub: pm ? `was ${nf(pm.r2, 2)} (zone card)` : ''}).replace('data-count="' + A.price.r2 + '"', 'data-count="' + A.price.r2 + '" data-dec="2"')}${kpi('Price MAE (BRL)', A.price.mae_brl, {ic: 'coin', color: 'var(--brand2)', sub: pm ? `was ${nf(pm.mae_brl, 2)}` : ''}).replace('data-count="' + A.price.mae_brl + '"', 'data-count="' + A.price.mae_brl + '" data-dec="2"')}
        ${kpi('ETA P90 coverage', A.eta.p90_coverage * 100, {ic: 'clock', color: 'var(--good)', sub: 'target 90%'}).replace('data-count="' + A.eta.p90_coverage * 100 + '"', 'data-count="' + A.eta.p90_coverage * 100 + '" data-fmt="pct"')}${kpi('Actual late rate (same orders)', b.actual_history_same_orders.late_rate * 100, {ic: 'alert', color: 'var(--bad)'}).replace('data-count="' + b.actual_history_same_orders.late_rate * 100 + '"', 'data-count="' + b.actual_history_same_orders.late_rate * 100 + '" data-fmt="pct"')}</div>
      <div class="grid g21"><div class="panel"><h2>${esc(t('frontier'))}</h2>${bubbles(pts, {xLabel: 'Avg cost per order (BRL)', yLabel: 'Expected late rate (%)', fx: v => nf(v, 1), fy: v => nf(v, 1) + '%'})}</div>
        <div class="panel"><h2>Headline</h2><div class="kv"><span>Cheapest within deadline</span><span><b>${nf(Math.abs(g1.cost_saving_pct), 1)}%</b> ${g1.cost_saving_pct >= 0 ? 'cheaper' : 'more expensive'}</span><span></span><span><b>${nf(g1.late_rate_change_pp, 1)} pp</b> late rate</span><span></span><span><b>${nf(g1.eta_p50_change_days, 1)} d</b> ETA</span><span>Balanced</span><span><b>${nf(Math.abs(g2.cost_saving_pct), 1)}%</b> ${g2.cost_saving_pct >= 0 ? 'cheaper' : 'more expensive'}</span><span></span><span><b>${nf(g2.late_rate_change_pp, 1)} pp</b> late rate</span><span></span><span><b>${nf(g2.eta_p50_change_days, 1)} d</b> ETA</span></div>
        <hr style="border:0;border-top:1px solid var(--line);margin:14px 0"><div class="kv"><span>Nearest source</span><span>${nf(N.pct_orders_where_system_picks_different_seller, 0)}% different seller</span><span>Distance</span><span>${N.avg_distance_km.actual_seller} → ${N.avg_distance_km.system_pick} km</span><span>Price</span><span>R$ ${N.avg_price_brl.actual_seller} → ${N.avg_price_brl.system_pick}</span><span>ETA P50</span><span>${N.avg_eta_p50_days.actual_seller} → ${N.avg_eta_p50_days.system_pick} d</span></div></div></div>
      <div class="panel"><h2>${esc(t('policies'))}</h2>${table(Object.entries(P).map(([k, v]) => ({name: nm[k] || k, ...v, _best: k.startsWith('system')})), [{label: 'Policy', key: 'name'}, {label: 'Total', html: r => brl(r.total_cost_brl)}, {label: 'Avg', html: r => brl(r.avg_cost_brl)}, {label: t('eta_p50'), html: r => nf(r.avg_eta_p50_days, 1) + ' d'}, {label: 'Expected late', html: r => pct(r.expected_late_rate, 1)}])}</div>
      <div class="panel"><h2>Assumptions</h2><ul class="muted">${b.assumptions.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>`;
    runCounts($('#view'));
  },

  async notifications() {
    const r = await api('/api/notifications');
    $('#view').innerHTML = `<div class="panel"><h2>Channels</h2><div class="row"><span class="pill ${r.channels.email ? 'ok' : ''}"><span class="dot"></span>E-mail (SMTP) ${r.channels.email ? 'configured' : 'not configured'}</span><span class="pill ${r.channels.webhook ? 'ok' : ''}"><span class="dot"></span>Webhook ${r.channels.webhook ? 'configured' : 'not configured'}</span></div><p class="muted sm">Set NOTIFY_SMTP_HOST / NOTIFY_WEBHOOK_URL. Without them events are still recorded here as <code>skipped_unconfigured</code>.</p></div>
      <div class="panel">${table(r.items, [{label: 'When', key: 'created_at'}, {label: 'Channel', key: 'channel'}, {label: 'To', html: x => esc((x.recipient || '').slice(0, 40))}, {label: 'Event', key: 'event'}, {label: t('status'), html: x => `<span class="pill ${x.status === 'sent' ? 'ok' : x.status === 'failed' ? 'bad' : ''}">${esc(x.status)}</span>`}, {label: 'Subject', key: 'subject'}])}</div>`;
  },

  async import() {
    $('#view').innerHTML = `<div class="panel"><p class="muted">Joins orders × items × products × sellers × customers × geolocation, splits multi-seller orders into sub-shipments, maps statuses and bulk-inserts. Upload limit applies — for the full archive use <i>server folder</i> or <code>scripts/import_olist.py</code>.</p>
      <div class="row"><button class="primary" id="impSrv">${icon('upload')} Import from server folder</button><div class="field"><label>Olist archive (.zip)</label><input type="file" id="zipf" accept=".zip"></div><div class="field" style="max-width:130px"><label>Limit orders</label><input id="lim" type="number" min="1" placeholder="all"></div><button class="ghost" id="impZip">Upload &amp; import</button></div><div id="imsg" style="margin-top:16px"></div></div>`;
    const poll = async id => { for (;;) { const j = await api('/api/import/jobs/' + id); $('#imsg').innerHTML = `<div class="bar"><i style="width:${Math.round((j.progress || 0) * 100)}%"></i></div><div class="sm" style="margin-top:6px">${esc(j.status)} ${j.error ? esc(j.error) : ''}</div>${j.result ? `<pre class="sm mono">${esc(JSON.stringify(j.result, null, 2))}</pre>` : ''}`; if (['done', 'failed'].includes(j.status)) break; await new Promise(r => setTimeout(r, 1500)); } };
    const lim = () => $('#lim').value ? '?limit=' + $('#lim').value : '';
    $('#impSrv').onclick = safe(async () => poll((await api('/api/import/olist-server' + lim(), {method: 'POST'})).job_id));
    $('#impZip').onclick = safe(async () => { const f = $('#zipf').files[0]; if (!f) return toast('Choose a .zip'); const fd = new FormData(); fd.append('file', f); poll((await api('/api/import/olist-archive' + lim(), {method: 'POST', body: fd})).job_id); });
  }
};

// ------------------------------------------------------------------ shipment drawer (slide-over)
function closeOverlay() { $('#overlay').innerHTML = ''; }
function printLabel(s) {
  if (!s.tracking_code) return toast('Assign a carrier first');
  const w = window.open('', '_blank', 'width=520,height=760'); if (!w) return toast('Allow pop-ups to print');
  w.document.write(`<!doctype html><meta charset="utf-8"><title>Label ${esc(s.order_id)}</title><style>body{font:14px system-ui,sans-serif;margin:0;padding:18px}.l{border:3px solid #000;border-radius:10px;padding:16px;max-width:430px;margin:auto}h1{margin:0 0 4px;font-size:22px}.big{font-size:30px;font-weight:800;letter-spacing:1px}.r{display:flex;justify-content:space-between;gap:10px;border-top:2px dashed #000;margin-top:12px;padding-top:10px}.s{font-size:11px;color:#444}.st{transform:rotate(-8deg);border:3px solid #c00;color:#c00;padding:2px 10px;font-weight:800;display:inline-block}@media print{button{display:none}}</style>
  <div class="l"><h1>${esc(s.carrier_code)} · ${esc(s.status)}</h1><div class="s">Order ${esc(s.order_id)}</div><div class="r"><div><div class="s">FROM</div><b>${esc(s.origin)}</b></div><div><div class="s">TO</div><b>${esc(s.destination)}</b></div></div>
  <div class="r"><div><div class="s">WEIGHT</div><b>${nf(s.weight_kg, 2)} kg</b></div><div><div class="s">ETA (P50 / P90)</div><b>${nf(s.eta_p50, 1)} / ${nf(s.eta_p90, 1)} d</b></div><div class="st">SIMULATED</div></div>
  <div class="r" style="display:block;text-align:center">${code128B(s.tracking_code)}<div class="big">${esc(s.tracking_code)}</div></div></div><p style="text-align:center"><button onclick="print()">Print</button></p><script>setTimeout(()=>print(),300)<\/script>`);
  w.document.close();
}
async function openShipment(id, refresh) {
  const s = await api('/api/shipments/' + id), ov = $('#overlay'), stage = ['Pending', 'Booked', 'In Transit', 'Delivered'].indexOf(s.status), canOp = RANK[me.role] >= 2;
  ov.innerHTML = `<div class="scrim"></div><aside class="drawer"><div class="row" style="justify-content:space-between;align-items:flex-start"><div><h3 class="mono" style="word-break:break-all">${esc(s.order_id)}</h3>${stPill(s.status)}</div><button class="ghost sm" id="dClose">${icon('x')}</button></div>
    ${stage >= 0 ? `<div class="steps">${['Pending', 'Booked', 'In Transit', 'Delivered'].map((x, i) => `<div class="step ${i <= stage ? 'done' : ''} ${i === stage ? 'now' : ''}"><i>${i <= stage ? '✓' : i + 1}</i>${x}</div>`).join('')}</div>` : ''}
    <div class="kv" style="margin:8px 0 14px"><span>${esc(t('origin'))}</span><span>${esc(s.origin)}</span><span>${esc(t('destination'))}</span><span>${esc(s.destination)}</span><span>${esc(t('weight'))}</span><span>${nf(s.weight_kg, 2)} kg</span><span>Deadline</span><span>${esc((s.deadline || '—').slice(0, 10))}</span>
      <span>${esc(t('carrier'))}</span><span>${carrierTag(s.carrier_code)} ${s.tracking_code ? '· <code>' + esc(s.tracking_code) + '</code>' : ''}</span><span>${esc(t('price'))}</span><span>${brl(s.price)}${s.eta_p90 ? ` · P50 ${nf(s.eta_p50, 1)}d · P90 ${nf(s.eta_p90, 1)}d` : ''}</span>${s.notify_email ? `<span>${esc(t('notify_email'))}</span><span>${esc(s.notify_email)}</span>` : ''}</div>
    <div id="dBody"></div></aside>`;
  const close = () => { closeOverlay(); refresh && refresh(); }; $('.scrim', ov).onclick = close; $('#dClose').onclick = close;
  const body = $('#dBody'); let html = '';
  if (s.status === 'Pending') {
    html += `<h2 class="muted sm" style="letter-spacing:1.6px;text-transform:uppercase">${esc(t('race_title'))}</h2><div id="dRace"><div class="skel" style="height:160px"></div></div>`;
    body.innerHTML = html;
    const r = await api(`/api/shipments/${id}/quotes`, {json: {objective: 'expected_cost'}}); $('#dRace').innerHTML = race(r, {assign: canOp}) + excludedBlock(r);
    $$('[data-assign]', ov).forEach(b => b.onclick = safe(async () => { await api(`/api/shipments/${id}/assign`, {json: {carrier_code: b.dataset.assign}}); toast('✓ ' + t('booked')); openShipment(id, refresh); }));
  } else {
    const nxt = {Booked: ['In Transit', 'Canceled'], 'In Transit': ['Delivered']}[s.status] || [];
    html += `<div class="row" style="margin-bottom:14px">${canOp ? nxt.map(n => `<button class="ghost ${n === 'Canceled' ? 'danger' : ''}" data-mv="${n}">→ ${n}</button>`).join('') : ''}${s.status === 'Booked' && canOp ? `<button class="ghost" id="rr">${icon('route')} ${esc(t('reroute'))}</button>` : ''}${s.tracking_code ? `<button class="ghost" id="cpy">${icon('link')} ${esc(t('share_link'))}</button><button class="ghost" id="lbl">${icon('printer')} ${esc(t('print_label'))}</button>` : ''}</div><div id="rrbox"></div><h2 class="muted sm" style="letter-spacing:1.6px;text-transform:uppercase">${esc(t('tracking'))}</h2><div id="dTrack"></div>`;
    body.innerHTML = html;
    const tr = await api(`/api/shipments/${id}/tracking`), items = tr.events.length ? tr.events : tr.projected;
    $('#dTrack').innerHTML = `<ul class="tl">${items.map(e => `<li class="${e.simulated ? 'proj' : ''}"><b>${esc(e.event)}</b> <span class="muted sm mono">${esc((e.ts || '').slice(0, 16).replace('T', ' '))}</span><div class="muted sm">${esc(e.description || '')}${e.simulated ? ' (projected)' : ''}</div></li>`).join('')}</ul>`;
    $$('[data-mv]', ov).forEach(b => b.onclick = safe(async () => { await api(`/api/shipments/${id}`, {method: 'PATCH', json: {status: b.dataset.mv}}); openShipment(id, refresh); }));
    if ($('#cpy')) $('#cpy').onclick = safe(async () => { const u = `${location.origin}/track?code=${s.tracking_code}`; try { await navigator.clipboard.writeText(u); toast(t('copied') + ': ' + u); } catch { prompt('Copy link', u); } });
    if ($('#lbl')) $('#lbl').onclick = () => printLabel(s);
    if ($('#rr')) $('#rr').onclick = safe(async () => {
      const r = await api(`/api/shipments/${id}/reroute-suggestion`);
      $('#rrbox').innerHTML = `<div class="notice ${r.reroute_recommended ? 'bad' : 'info'}">${r.reroute_recommended ? icon('alert') + ' Re-route recommended: ' + esc(r.reasons.join('; ')) : icon('check') + ' Current carrier is healthy.'}</div>` + (r.alternatives.length ? `<div class="race" style="margin-bottom:14px">${r.alternatives.map(a => `<div class="lane" style="grid-template-columns:1fr auto auto"><div><b>${carrierTag(a.carrier_code)}</b><div class="muted sm">P90 ${nf(a.eta_p90_days, 1)}d</div></div><b>${brl(a.price)}</b><button class="primary" style="padding:6px 12px" data-rr="${esc(a.carrier_code)}">${esc(t('reroute'))}</button></div>`).join('')}</div>` : '');
      $$('[data-rr]', ov).forEach(b => b.onclick = safe(async () => { await api(`/api/shipments/${id}/reroute`, {json: {carrier_code: b.dataset.rr}}); toast('✓ ' + t('reroute')); openShipment(id, refresh); }));
    });
  }
}

// ------------------------------------------------------------------ command palette (Ctrl/Cmd+K)
function openCmdk() {
  const ov = $('#overlay'); ov.innerHTML = `<div class="scrim"></div><div class="cmdk"><input id="ck" placeholder="${esc(t('cmd_ph'))}" autofocus><ul id="cl"></ul></div>`; let items = [], sel = 0, tm;
  const pages = PAGES.filter(([, , r]) => me && RANK[me.role] >= RANK[r]).map(([p, ic]) => ({label: t(p), ic, run: () => show(p)}));
  const draw = () => { $('#cl').innerHTML = items.map((x, i) => `<li class="${i === sel ? 'sel' : ''}" data-i="${i}">${icon(x.ic)}<span>${esc(x.label)}</span><span class="muted sm" style="margin-inline-start:auto">${esc(x.hint || '')}</span></li>`).join(''); $$('#cl li').forEach(li => li.onclick = () => go(+li.dataset.i)); };
  const go = i => { const x = items[i]; if (x) { closeOverlay(); x.run(); } };
  const upd = async () => { const q = $('#ck').value.trim().toLowerCase(); items = pages.filter(p => !q || p.label.toLowerCase().includes(q)); sel = 0; draw();
    if (q.length >= 3) { try { const d = await api('/api/shipments?limit=5&q=' + encodeURIComponent(q)); if ($('#ck') && $('#ck').value.trim().toLowerCase() === q) { items = items.concat(d.items.map(s => ({label: s.order_id, ic: 'box', hint: s.status + ' · ' + s.destination, run: () => { show('shipments').then(() => openShipment(s.id, () => show('shipments'))); }}))); draw(); } } catch {} } };
  $('#ck').oninput = () => { clearTimeout(tm); tm = setTimeout(upd, 160); };
  $('#ck').onkeydown = e => { if (e.key === 'ArrowDown') { sel = Math.min(sel + 1, items.length - 1); draw(); e.preventDefault(); } else if (e.key === 'ArrowUp') { sel = Math.max(sel - 1, 0); draw(); e.preventDefault(); } else if (e.key === 'Enter') go(sel); else if (e.key === 'Escape') closeOverlay(); };
  $('.scrim', ov).onclick = closeOverlay; upd(); setTimeout(() => $('#ck') && $('#ck').focus(), 30);
}
document.addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k' && me) { e.preventDefault(); openCmdk(); } else if (e.key === 'Escape' && $('#overlay').innerHTML && !$('.cmdk')) closeOverlay(); });

initTheme(); applyLang(); hydrateIcons();
if (token) boot(); else $('#login').classList.remove('hidden');
