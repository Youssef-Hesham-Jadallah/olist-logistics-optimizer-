'use strict';
// ---- small helpers shared by the console (app.js) and the public tracking page (track.js)
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
let lang = localStorage.getItem('lang') || 'en';
const t = k => (I18N[lang] && I18N[lang][k]) || I18N.en[k] || k;
const nf = (n, d = 2) => n == null || isNaN(n) ? '—' : Number(n).toLocaleString(lang === 'ar' ? 'ar-EG' : 'en-US', {minimumFractionDigits: d, maximumFractionDigits: d});
const iso = s => '\u2066' + s + '\u2069';   // bidi isolate: keeps 'R$ 12.30' and '9.5%' intact inside RTL text
const brl = n => n == null ? '—' : iso('R$ ' + nf(n));
const pct = (n, d = 1) => n == null ? '—' : iso(nf(n * 100, d) + '%');

const ICONS = {
  home: '<path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/>', box: '<path d="M21 8l-9-5-9 5 9 5 9-5z"/><path d="M3 8v8l9 5 9-5V8"/><path d="M12 13v8"/>',
  compare: '<path d="M12 3v18"/><path d="M5 7h14"/><path d="M5 7l-3 7a3 3 0 006 0L5 7z"/><path d="M19 7l-3 7a3 3 0 006 0l-3-7z"/>', pin: '<path d="M12 21s7-6.2 7-11a7 7 0 10-14 0c0 4.8 7 11 7 11z"/><circle cx="12" cy="10" r="2.5"/>',
  layers: '<path d="M12 3l9 5-9 5-9-5 9-5z"/><path d="M3 13l9 5 9-5"/>', sliders: '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
  truck: '<path d="M2 6h11v10H2z"/><path d="M13 9h5l4 4v3h-9"/><circle cx="7" cy="18" r="2"/><circle cx="17" cy="18" r="2"/>', chart: '<path d="M4 20V4"/><path d="M4 20h16"/><path d="M8 16v-5M12 16V8M16 16v-8"/>',
  flask: '<path d="M9 3h6"/><path d="M10 3v6L4 19a2 2 0 002 3h12a2 2 0 002-3l-6-10V3"/>', upload: '<path d="M12 16V4"/><path d="M7 9l5-5 5 5"/><path d="M4 20h16"/>', bell: '<path d="M6 8a6 6 0 1112 0c0 7 3 8 3 8H3s3-1 3-8"/><path d="M10 20a2 2 0 004 0"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/>', sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M5 19l1.5-1.5M17.5 6.5L19 5"/>', moon: '<path d="M21 13A9 9 0 1111 3a7 7 0 0010 10z"/>',
  printer: '<path d="M6 9V3h12v6"/><rect x="3" y="9" width="18" height="8" rx="2"/><path d="M7 14h10v7H7z"/>', link: '<path d="M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.7l1-1"/>',
  route: '<circle cx="6" cy="19" r="2"/><circle cx="18" cy="5" r="2"/><path d="M8 19h7a3 3 0 000-6H9a3 3 0 010-6h7"/>', x: '<path d="M6 6l12 12M18 6L6 18"/>', alert: '<path d="M12 3l10 18H2L12 3z"/><path d="M12 10v5M12 18h0"/>', check: '<path d="M5 12l5 5 9-10"/>',
  coin: '<circle cx="12" cy="12" r="9"/><path d="M12 7v10M9.5 9.5c0-1 1-1.7 2.5-1.7s2.5.7 2.5 1.7-1 1.5-2.5 1.8-2.5.8-2.5 1.8 1 1.7 2.5 1.7 2.5-.7 2.5-1.7"/>', clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', zap: '<path d="M13 2L4 14h7l-1 8 9-12h-7l1-8z"/>',
  shield: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3z"/>'
};
const icon = n => `<svg class="i" viewBox="0 0 24 24" aria-hidden="true">${ICONS[n] || ''}</svg>`;
const hydrateIcons = (root = document) => $$('[data-icon]', root).forEach(e => { if (!e.dataset.done) { e.innerHTML = icon(e.dataset.icon); e.dataset.done = 1; } });

const CARRIER_VARS = ['ALPHA', 'BETA', 'GAMMA', 'DELTA', 'EPSILON'];
const ccol = code => CARRIER_VARS.includes(code) ? `var(--c-${code})` : `hsl(${[...String(code)].reduce((a, c) => a + c.charCodeAt(0) * 7, 0) % 360} 70% 60%)`;
const cssVar = (name, el = document.documentElement) => getComputedStyle(el).getPropertyValue(name).trim();
const rawColor = c => { const m = /^var\((--[\w-]+)\)$/.exec(c); return m ? cssVar(m[1]) : c; };

function setTheme(th) { document.documentElement.dataset.theme = th; localStorage.setItem('theme', th); const b = $('#themeBtn'); if (b) b.innerHTML = icon(th === 'dark' ? 'sun' : 'moon'); const m = $('meta[name=theme-color]'); if (m) m.content = th === 'dark' ? '#060A15' : '#F2F5FC'; }
const initTheme = () => setTheme(localStorage.getItem('theme') || 'dark');

function toast(msg, ms = 4200) { const e = document.createElement('div'); e.className = 'toast'; e.style.insetBlockEnd = (20 + $$('.toast').length * 60) + 'px'; e.textContent = msg; document.body.append(e); setTimeout(() => e.remove(), ms); }

function countUp(el, to, {dec = 0, ms = 900, fmt = v => nf(v, dec)} = {}) {
  if (to == null || isNaN(to)) { el.textContent = '—'; return; }
  const t0 = performance.now(); const step = now => { const k = Math.min(1, (now - t0) / ms), e = 1 - Math.pow(1 - k, 3); el.textContent = fmt(to * e); if (k < 1 && document.body.contains(el)) requestAnimationFrame(step); };
  requestAnimationFrame(step);
}

// ---- tiny SVG charts (no libraries, work offline, theme-aware through CSS variables)
let _gid = 0;
function areaLine(pts, {color = 'var(--brand)', h = 200, fmtY = v => nf(v, 0), marker = -1, labels = true} = {}) {
  if (!pts.length) return `<div class="empty">${esc(t('empty'))}</div>`;
  const W = 640, H = h, L = 46, R = 14, T = 12, B = 26, ys = pts.map(p => p.y), mn = Math.min(0, ...ys), mx = Math.max(...ys) * 1.08 || 1, id = 'g' + (++_gid);
  const X = i => L + (pts.length === 1 ? .5 : i / (pts.length - 1)) * (W - L - R), Y = v => H - B - (v - mn) / ((mx - mn) || 1) * (H - B - T);
  const line = pts.map((p, i) => `${X(i).toFixed(1)},${Y(p.y).toFixed(1)}`).join(' ');
  const grid = [0, .25, .5, .75, 1].map(f => { const v = mn + (mx - mn) * f; return `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--line)"/><text x="${L - 6}" y="${Y(v) + 4}" text-anchor="end">${esc(fmtY(v))}</text>`; }).join('');
  const step = Math.max(1, Math.ceil(pts.length / 7));
  const xl = labels ? pts.map((p, i) => i % step === 0 ? `<text x="${X(i)}" y="${H - 8}" text-anchor="middle">${esc(p.x)}</text>` : '').join('') : '';
  const mk = marker >= 0 && pts[marker] ? `<line x1="${X(marker)}" x2="${X(marker)}" y1="${T}" y2="${H - B}" stroke="${color}" stroke-dasharray="4 4"/><circle cx="${X(marker)}" cy="${Y(pts[marker].y)}" r="6" fill="${color}" stroke="var(--panel-solid)" stroke-width="3"/>` : '';
  return `<svg class="chart" viewBox="0 0 ${W} ${H}"><defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".45"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>${grid}${xl}
    <polygon points="${L},${Y(mn)} ${line} ${X(pts.length - 1)},${Y(mn)}" fill="url(#${id})"/><polyline points="${line}" fill="none" stroke="${color}" stroke-width="2.6" stroke-linejoin="round" stroke-linecap="round"/>${pts.length < 40 ? pts.map((p, i) => `<circle cx="${X(i)}" cy="${Y(p.y)}" r="3" fill="${color}"/>`).join('') : ''}${mk}</svg>`;
}
function spark(vals, color = 'var(--brand)') {
  if (vals.length < 2) return ''; const W = 120, H = 34, mn = Math.min(...vals), mx = Math.max(...vals), X = i => i / (vals.length - 1) * W, Y = v => H - 3 - (v - mn) / ((mx - mn) || 1) * (H - 8);
  return `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}"><polyline points="${vals.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(' ')}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round"/></svg>`;
}
function donut(items, {size = 170, center = ''} = {}) {
  const tot = items.reduce((a, i) => a + i.value, 0) || 1, r = 62, c = 2 * Math.PI * r; let off = 0;
  const arcs = items.filter(i => i.value > 0).map(i => { const len = i.value / tot * c, s = `<circle r="${r}" cx="80" cy="80" fill="none" stroke="${i.color}" stroke-width="20" stroke-dasharray="${Math.max(len - 2, 0)} ${c}" stroke-dashoffset="${-off}" transform="rotate(-90 80 80)"/>`; off += len; return s; }).join('');
  return `<svg viewBox="0 0 160 160" width="${size}" height="${size}"><circle r="${r}" cx="80" cy="80" fill="none" stroke="var(--line)" stroke-width="20"/>${arcs}<text x="80" y="78" text-anchor="middle" class="tx" style="font-size:20px;font-weight:700">${esc(center)}</text></svg>`;
}
function bubbles(points, {xLabel = '', yLabel = '', fx = v => nf(v, 1), fy = v => nf(v, 1)} = {}) {
  const W = 640, H = 330, L = 56, R = 24, T = 20, B = 40, xs = points.map(p => p.x), ys = points.map(p => p.y), x0 = Math.min(...xs) * .92, x1 = Math.max(...xs) * 1.06, y0 = Math.min(...ys) * .85, y1 = Math.max(...ys) * 1.1;
  const X = v => L + (v - x0) / ((x1 - x0) || 1) * (W - L - R), Y = v => H - B - (v - y0) / ((y1 - y0) || 1) * (H - B - T);
  const gr = [0, .25, .5, .75, 1].map(f => { const vx = x0 + (x1 - x0) * f, vy = y0 + (y1 - y0) * f; return `<line x1="${X(vx)}" x2="${X(vx)}" y1="${T}" y2="${H - B}" stroke="var(--line)"/><text x="${X(vx)}" y="${H - B + 16}" text-anchor="middle">${esc(fx(vx))}</text><line x1="${L}" x2="${W - R}" y1="${Y(vy)}" y2="${Y(vy)}" stroke="var(--line)"/><text x="${L - 8}" y="${Y(vy) + 4}" text-anchor="end">${esc(fy(vy))}</text>`; }).join('');
  const placed = []; const pts = points.map(p => { let ly = Y(p.y) + 4; const lx = X(p.x) + (p.r || 9) + 6; while (placed.some(q => Math.abs(q[0] - lx) < 150 && Math.abs(q[1] - ly) < 13)) ly += 14; placed.push([lx, ly]);
    return `<g><circle cx="${X(p.x)}" cy="${Y(p.y)}" r="${p.r || 9}" fill="${p.color}" fill-opacity=".85" stroke="${p.star ? 'var(--text)' : 'none'}" stroke-width="2.5"/><text x="${lx}" y="${ly}" class="tx" style="font-size:11px">${esc(p.label)}</text></g>`; }).join('');
  return `<svg class="chart" viewBox="0 0 ${W} ${H}">${gr}<text x="${W / 2}" y="${H - 4}" text-anchor="middle">${esc(xLabel)}</text><text x="12" y="${H / 2}" text-anchor="middle" transform="rotate(-90 12 ${H / 2})">${esc(yLabel)}</text>${pts}</svg>`;
}

// ---- dot-matrix Brazil map on canvas, animated arcs with travelling parcels
class BrazilMap {
  static async load() { if (!BrazilMap.data) BrazilMap.data = await (await fetch('/static/brazil_dots.json')).json(); return BrazilMap.data; }
  constructor(canvas) { this.c = canvas; this.x = canvas.getContext('2d'); this.arcs = []; this.pts = []; this.t0 = performance.now(); this.d = BrazilMap.data; this.resize(); this.ro = new ResizeObserver(() => this.resize()); this.ro.observe(canvas); this.loop = this.loop.bind(this); requestAnimationFrame(this.loop); }
  resize() { const dpr = Math.min(window.devicePixelRatio || 1, 2), w = this.c.clientWidth || 400, h = this.c.clientHeight || 300; this.c.width = w * dpr; this.c.height = h * dpr; this.x.setTransform(dpr, 0, 0, dpr, 0, 0); this.w = w; this.h = h; }
  P(lat, lng) { const b = this.d.bounds, pad = 18, k = Math.min((this.w - pad * 2) / (b.lng[1] - b.lng[0]), (this.h - pad * 2) / (b.lat[1] - b.lat[0])); return [(this.w - (b.lng[1] - b.lng[0]) * k) / 2 + (lng - b.lng[0]) * k, (this.h - (b.lat[1] - b.lat[0]) * k) / 2 + (b.lat[1] - lat) * k, k]; }
  set({arcs = [], points = []}) { this.arcs = arcs.map((a, i) => ({...a, col: rawColor(a.color || '#6C8CFF'), ph: Math.random(), i})); this.pts = points.map(p => ({...p, col: rawColor(p.color || '#22D3EE')})); }
  loop(now) {
    if (!document.body.contains(this.c)) { this.ro.disconnect(); return; }
    const x = this.x, tt = (now - this.t0) / 1000, dc = cssVar('--dotc') || '130,160,255'; x.clearRect(0, 0, this.w, this.h);
    for (const [la, ln, dn] of this.d.dots) { const [px, py, k] = this.P(la, ln), r = Math.max(1.1, k * this.d.cell * .34 * (.55 + .45 * dn)); x.fillStyle = `rgba(${dc},${.10 + .48 * dn})`; x.beginPath(); x.arc(px, py, r, 0, 6.2832); x.fill(); }
    for (const a of this.arcs) {
      const [x1, y1] = this.P(a.a[0], a.a[1]), [x2, y2] = this.P(a.b[0], a.b[1]), dist = Math.hypot(x2 - x1, y2 - y1), cx = (x1 + x2) / 2, cy = (y1 + y2) / 2 - Math.max(18, dist * .32);
      x.strokeStyle = a.col; x.globalAlpha = a.dim ? .15 : .5; x.lineWidth = a.w || 1.6; x.setLineDash(a.dash ? [5, 5] : []); x.beginPath(); x.moveTo(x1, y1); x.quadraticCurveTo(cx, cy, x2, y2); x.stroke(); x.setLineDash([]); x.globalAlpha = 1;
      const n = a.dim ? 0 : (a.parcels ?? 2);
      for (let j = 0; j < n; j++) { const s = ((tt * (a.speed || .22) + a.ph + j / n) % 1), q = 1 - s, bx = q * q * x1 + 2 * q * s * cx + s * s * x2, by = q * q * y1 + 2 * q * s * cy + s * s * y2; x.fillStyle = a.col; x.shadowColor = a.col; x.shadowBlur = 12; x.beginPath(); x.arc(bx, by, (a.w || 1.6) + 1.6, 0, 6.2832); x.fill(); x.shadowBlur = 0; }
      if (a.progress != null) { const s = a.progress, q = 1 - s, bx = q * q * x1 + 2 * q * s * cx + s * s * x2, by = q * q * y1 + 2 * q * s * cy + s * s * y2; x.fillStyle = '#fff'; x.shadowColor = a.col; x.shadowBlur = 18; x.beginPath(); x.arc(bx, by, 6, 0, 6.2832); x.fill(); x.shadowBlur = 0; }
    }
    for (const p of this.pts) { const [px, py] = this.P(p.lat, p.lng), pulse = (tt * .8 + (p.ph || 0)) % 1; x.strokeStyle = p.col; x.globalAlpha = 1 - pulse; x.lineWidth = 2; x.beginPath(); x.arc(px, py, 5 + pulse * 14, 0, 6.2832); x.stroke(); x.globalAlpha = 1; x.fillStyle = p.col; x.beginPath(); x.arc(px, py, p.big ? 6 : 4.2, 0, 6.2832); x.fill();
      if (p.label) { x.font = '600 11px system-ui'; x.fillStyle = cssVar('--text'); x.textAlign = px > this.w - 90 ? 'right' : 'left'; x.fillText(p.label, px + (px > this.w - 90 ? -10 : 10), py - 8); } }
    requestAnimationFrame(this.loop);
  }
}

// ---- typeahead on the local geo tables (/api/geo/suggest)
function attachAC(input, apiFn) {
  input.setAttribute('autocomplete', 'off'); let box = null, items = [], sel = -1, tm;
  const close = () => { box && box.remove(); box = null; items = []; sel = -1; };
  const pick = i => { if (items[i]) { input.value = items[i].value; close(); input.dispatchEvent(new Event('change', {bubbles: true})); } };
  const draw = () => { if (!items.length) return close(); if (!box) { box = document.createElement('div'); box.className = 'ac'; input.parentElement.append(box); } box.innerHTML = items.map((s, i) => `<div class="${i === sel ? 'sel' : ''}" data-i="${i}"><span>${esc(s.label)}</span><span class="muted sm">${esc(s.kind)}</span></div>`).join(''); $$('div', box).forEach(d => d.onmousedown = e => { e.preventDefault(); pick(+d.dataset.i); }); };
  input.addEventListener('input', () => { clearTimeout(tm); tm = setTimeout(async () => { const q = input.value.trim(); if (q.length < 2) return close(); try { items = await apiFn('/api/geo/suggest?q=' + encodeURIComponent(q)); sel = -1; draw(); } catch { close(); } }, 160); });
  input.addEventListener('keydown', e => { if (!box) return; if (e.key === 'ArrowDown') { sel = (sel + 1) % items.length; draw(); e.preventDefault(); } else if (e.key === 'ArrowUp') { sel = (sel - 1 + items.length) % items.length; draw(); e.preventDefault(); } else if (e.key === 'Enter' && sel >= 0) { pick(sel); e.preventDefault(); } else if (e.key === 'Escape') close(); });
  input.addEventListener('blur', () => setTimeout(close, 120));
}

// ---- Code 128 (subset B) barcode as SVG
const C128 = ['212222', '222122', '222221', '121223', '121322', '131222', '122213', '122312', '132212', '221213', '221312', '231212', '112232', '122132', '122231', '113222', '123122', '123221', '223211', '221132', '221231', '213212', '223112', '312131', '311222', '321122', '321221', '312212', '322112', '322211', '212123', '212321', '232121', '111323', '131123', '131321', '112313', '132113', '132311', '211313', '231113', '231311', '112133', '112331', '132131', '113123', '113321', '133121', '313121', '211331', '231131', '213113', '213311', '213131', '311123', '311321', '331121', '312113', '312311', '332111', '314111', '221411', '431111', '111224', '111422', '121124', '121421', '141122', '141221', '112214', '112412', '122114', '122411', '142112', '142211', '241211', '221114', '413111', '241112', '134111', '111242', '121142', '121241', '114212', '124112', '124211', '411212', '421112', '421211', '212141', '214121', '412121', '111143', '111341', '131141', '114113', '114311', '411113', '411311', '113141', '114131', '311141', '411131', '211412', '211214', '211232', '2331112'];
function code128B(text, {h = 56, mod = 2} = {}) {
  const vals = [104]; let sum = 104; [...String(text)].forEach((ch, i) => { const v = ch.charCodeAt(0) - 32; if (v < 0 || v > 94) throw new Error('char not supported'); vals.push(v); sum += v * (i + 1); });
  vals.push(sum % 103, 106); let x = 10 * mod, bars = '';
  for (const v of vals) { [...C128[v]].forEach((w, i) => { const wd = +w * mod; if (i % 2 === 0) bars += `<rect x="${x}" y="0" width="${wd}" height="${h}" fill="#000"/>`; x += wd; }); }
  const W = x + 10 * mod; return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${h}" width="${W}" height="${h}" shape-rendering="crispEdges"><rect width="${W}" height="${h}" fill="#fff"/>${bars}</svg>`;
}
if (typeof module !== 'undefined') module.exports = {code128B, C128};
