'use strict';
let MAPI = null, last = null;
function applyLang() { document.documentElement.lang = lang; document.documentElement.dir = lang === 'ar' ? 'rtl' : 'ltr'; $$('[data-i18n]').forEach(e => e.textContent = t(e.dataset.i18n)); $('#langBtn').textContent = lang === 'ar' ? 'English' : 'العربية'; hydrateIcons(); }
$('#langBtn').onclick = () => { lang = lang === 'ar' ? 'en' : 'ar'; localStorage.setItem('lang', lang); applyLang(); if (last) render(last); };
$('#themeBtn').onclick = () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
const fmtDate = iso => iso ? new Date(iso).toLocaleDateString(lang === 'ar' ? 'ar-EG' : 'en-GB', {day: 'numeric', month: 'short', year: 'numeric'}) : '—';
const STAGE_LABEL = {Pending: 'Pending', Booked: 'Booked', 'In Transit': 'In Transit', Delivered: 'Delivered'};

async function render(d) {
  last = d; await BrazilMap.load(); const cv = $('#map'); if (!MAPI) MAPI = new BrazilMap(cv);
  const prog = Math.max(0, Math.min(1, (d.stage + (d.stage === 3 ? 0 : .5)) / 3));
  MAPI.set({arcs: [{a: [d.origin.lat, d.origin.lng], b: [d.destination.lat, d.destination.lng], color: 'var(--brand2)', w: 3, parcels: 0, progress: d.stage >= 1 ? prog : null}], points: [{lat: d.origin.lat, lng: d.origin.lng, label: d.origin.label, color: 'var(--good)', big: 1}, {lat: d.destination.lat, lng: d.destination.lng, label: d.destination.label, color: 'var(--amber)', big: 1, ph: .5}]});
  $('#res').innerHTML = `<div class="grid g3" style="margin-top:18px"><div class="card kpi"><div class="lbl">${icon('box')} ${esc(t('status'))}</div><div class="val" style="font-size:24px">${esc(d.status)}</div><div class="sub">${esc(d.carrier || '—')}</div></div>
      <div class="card kpi" style="--k:var(--good)"><div class="lbl">${icon('clock')} ${esc(t('expected_on'))}</div><div class="val" style="font-size:24px">${fmtDate(d.eta && d.eta.expected)}</div><div class="sub">${esc(t('latest_likely'))}: ${fmtDate(d.eta && d.eta.latest_likely)}</div></div>
      <div class="card kpi" style="--k:var(--amber)"><div class="lbl">${icon('shield')} ${esc(t('promised'))}</div><div class="val" style="font-size:24px">${fmtDate(d.promised_by)}</div><div class="sub mono">${esc(d.tracking_code)}</div></div></div>
    <div class="panel"><div class="steps">${d.stages.map((x, i) => `<div class="step ${i <= d.stage ? 'done' : ''} ${i === d.stage ? 'now' : ''}"><i>${i <= d.stage ? '✓' : i + 1}</i>${esc(STAGE_LABEL[x] || x)}</div>`).join('')}</div>
      <div class="row" style="justify-content:space-between"><span class="pill info">${icon('route')} ${esc(d.origin.label)} → ${esc(d.destination.label)}</span></div></div>
    <div class="panel"><h2>${esc(t('tracking'))}</h2><ul class="tl">${d.events.map(e => `<li><b>${esc(e.event)}</b> <span class="muted sm mono">${esc((e.ts || '').slice(0, 16).replace('T', ' '))}</span><div class="muted sm">${esc(e.description || '')}</div></li>`).join('') || `<li class="proj muted">${esc(t('empty'))}</li>`}</ul></div>`;
  hydrateIcons($('#res'));
}
async function lookup(code) {
  $('#err').textContent = ''; $('#res').innerHTML = '<div class="skel" style="height:200px;margin-top:18px"></div>';
  try { const r = await fetch('/api/public/track/' + encodeURIComponent(code.trim())); const d = await r.json(); if (!r.ok) throw new Error(r.status === 404 ? t('not_found') : (d.detail || 'Error')); history.replaceState(null, '', '?code=' + encodeURIComponent(code.trim())); await render(d); }
  catch (e) { $('#res').innerHTML = ''; $('#err').textContent = e.message; }
}
$('#tf').onsubmit = e => { e.preventDefault(); lookup($('#code').value); };
initTheme(); applyLang();
BrazilMap.load().then(() => { MAPI = new BrazilMap($('#map')); const c = new URLSearchParams(location.search).get('code'); if (c) { $('#code').value = c; lookup(c); } });
