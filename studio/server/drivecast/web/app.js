'use strict';
/* DriveCast Studio web panel. No build step, no framework.
   Every card renders inside safe(): one failing card never breaks the page. */

const S = { user: null, tz: 'Asia/Karachi', acts: {}, timers: [], session: null };
const $ = (s, el = document) => el.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// ------------------------------------------------------------------ api
async function api(method, path, body) {
  const opt = { method, credentials: 'same-origin', headers: { 'X-DC': '1' } };
  if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  let r;
  try { r = await fetch(path, opt); } catch (e) { throw new Error('Cannot reach the server. Is the phone online?'); }
  let data = {};
  try { data = await r.json(); } catch (e) { /* empty */ }
  if (r.status === 401 && !path.startsWith('/api/login') && !path.startsWith('/api/session')) { S.user = null; go('login'); throw new Error('Please sign in'); }
  if (!r.ok) throw new Error(data.error || `Request failed (${r.status})`);
  return data;
}

function toast(msg, bad) {
  let box = document.getElementById('toasts');
  if (!box) { box = document.createElement('div'); box.id = 'toasts'; box.className = 'toasts'; box.setAttribute('aria-live', 'polite'); document.body.appendChild(box); }
  const t = document.createElement('div');
  const ms = bad ? 6000 : 3500;
  t.className = 'toast' + (bad ? ' bad' : '');
  t.innerHTML = `<span class="ti">${ic(bad ? 'alert' : 'check', 15)}</span><span class="tm"></span><i class="tp" style="animation-duration:${ms}ms"></i>`;
  t.querySelector('.tm').textContent = msg;
  deskin(t);
  box.appendChild(t);
  while (box.children.length > 4) box.firstChild.remove();
  setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 260); }, ms);
}

// ------------------------------------------------------------------ icons & bits
const ICON = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  cal: '<rect x="3" y="4" width="18" height="17" rx="2"/><path d="M3 9h18M8 2v4M16 2v4"/>',
  tv: '<rect x="2" y="5" width="20" height="14" rx="3"/><path d="m10 9 5 3-5 3z"/>',
  net: '<circle cx="12" cy="5" r="2.5"/><circle cx="5" cy="19" r="2.5"/><circle cx="19" cy="19" r="2.5"/><path d="M12 7.5v4M12 11.5 6.5 17M12 11.5l5.5 5.5"/>',
  film: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 3v18M17 3v18M3 8h4M3 16h4M17 8h4M17 16h4"/>',
  drive: '<path d="M8 3h8l6 10-4 7H6l-4-7z"/><path d="M8 3l6 10h8M2 13h12l-4 7"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2 20c0-3.5 3-6 7-6s7 2.5 7 6"/><circle cx="17" cy="7" r="2.5"/><path d="M17 13c2.8 0 5 1.8 5 4.5"/>',
  phone: '<rect x="6" y="2" width="12" height="20" rx="2.5"/><path d="M11 18h2"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9 7 7M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1"/>',
  plus: '<path d="M12 5v14M5 12h14"/>', arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>', back: '<path d="M19 12H5M11 6l-6 6 6 6"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  alert: '<path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17h.01"/>', check: '<path d="m5 12 4 4 10-10"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
  yt: '<rect x="2" y="5" width="20" height="14" rx="4"/><path d="m10 9 5 3-5 3z"/>', up: '<path d="M7 17 17 7M9 7h8v8"/>', down: '<path d="M7 7l10 10M17 9v8H9"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9M17 6l3 3M15 8l2 2"/>', bolt: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
  logout: '<path d="M15 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4M10 17l5-5-5-5M15 12H4"/>', shield: '<path d="M12 3 4 6v6c0 4.5 3.4 8 8 9 4.6-1 8-4.5 8-9V6z"/><path d="m9 12 2 2 4-4"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>', search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  ig: '<rect x="3" y="3" width="18" height="18" rx="5"/><circle cx="12" cy="12" r="4"/><circle cx="17.5" cy="6.5" r="1"/>',
  fb: '<path d="M14 8h3V4h-3a4 4 0 0 0-4 4v2H8v4h2v7h4v-7h3l1-4h-4V8z"/>',
  inbox: '<path d="M4 13v6h16v-6M12 4v11M7 9l5-5 5 5"/>', repeat: '<path d="M17 2l4 4-4 4"/><path d="M3 11V9a3 3 0 0 1 3-3h15M7 22l-4-4 4-4"/><path d="M21 13v2a3 3 0 0 1-3 3H3"/>',
  cookie: '<circle cx="12" cy="12" r="9"/><circle cx="9" cy="9" r="1"/><circle cx="15" cy="13" r="1"/><circle cx="10" cy="15" r="1"/>', pause: '<rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/>',
  tt: '<path d="M14 3v11.5a3.5 3.5 0 1 1-3.5-3.5"/><path d="M14 3c.4 2.6 2.2 4.4 5 4.6"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  play: '<path d="M8 5v14l11-7z"/>',
  eyeoff: '<path d="M3 3l18 18"/><path d="M10.6 5.1A9.8 9.8 0 0 1 12 5c6 0 9.5 7 9.5 7a17 17 0 0 1-3 3.8M6.2 6.3A16.6 16.6 0 0 0 2.5 12S6 19 12 19a9.4 9.4 0 0 0 4.3-1"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
  spark: '<path d="M12 3l1.8 4.9L19 9.7l-5.2 1.8L12 16.4l-1.8-4.9L5 9.7l5.2-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>',
  auto: '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor"/>',
  bell: '<path d="M6 8a6 6 0 1 1 12 0c0 7 3 8 3 8H3s3-1 3-8"/><path d="M10 20a2 2 0 0 0 4 0"/>',
  chev: '<path d="m9 6 6 6-6 6"/>',
  side: '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M9 4v16"/>',
  cmd: '<path d="M9 6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3z"/>',
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
  cc: '<rect x="2" y="5" width="20" height="14" rx="3"/><path d="M10 10.5a2.5 2.5 0 1 0 0 3M17 10.5a2.5 2.5 0 1 0 0 3"/>',
  lang: '<path d="M4 5h9M8.5 3v2M6 5c0 4 3 7 6 8M11 5c0 4-3 7-7 9"/><path d="m13 21 4-10 4 10M14.5 17.5h5"/>',
  edit: '<path d="M4 20h4l10.5-10.5a2.8 2.8 0 0 0-4-4L4 16v4z"/><path d="M13.5 6.5l4 4"/>',
  more: '<circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/>',
  batt: '<rect x="2" y="7" width="17" height="10" rx="2"/><path d="M22 11v2"/>', flame: '<path d="M12 22c4 0 7-3 7-7 0-5-5-7-5-12-3 2-5 5-5 8-1-1-2-2-2-4-2 2-2 5-2 8 0 4 3 7 7 7z"/>',
  eye: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>', clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  ext: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>', trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
};
const ic = (n, s = 18) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON[n] || ''}</svg>`;
const LOGO = '<span class="logo"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M6.2 7.6a6.5 6.5 0 0 0 0 8.8M17.8 7.6a6.5 6.5 0 0 1 0 8.8" stroke="#fff" stroke-width="1.9" stroke-linecap="round"/><path d="M10 9.3c0-.7.8-1.2 1.4-.8l4.4 2.7c.6.4.6 1.2 0 1.6l-4.4 2.7c-.6.4-1.4 0-1.4-.8z" fill="#E8492A"/></svg></span>';
const chip = (t, tone = 'neutral', dot = true) => `<span class="chip ${tone}">${dot ? '<i></i>' : ''}${esc(t)}</span>`;
// Brand lock-up: DriveCast, and under it “Studio | By Naimat”
function BRAND(dark = true, big = false) {
  return `<span class="bl ${big ? 'big' : ''}">${LOGO}<span class="bl-t"><strong style="color:${dark ? '#fff' : 'var(--ink)'}">DriveCast</strong><span class="bl-s">Studio <i>|</i> By Naimat</span></span></span>`;
}
function avatar(name, i = 0, img = null, size = 32) {
  const inner = img ? `<img src="${esc(img)}" alt="">` : esc((name || '?').trim().charAt(0).toUpperCase());
  return `<span class="av av${Math.abs(i) % 5}" style="width:${size}px;height:${size}px">${inner}</span>`;
}
const hashI = s => [...String(s)].reduce((a, c) => a + c.charCodeAt(0), 0);
const handle = h => h ? `<span class="mono muted" style="font-size:11px">${esc(h.startsWith('@') ? h : '@' + h)}</span>` : '';

// ------------------------------------------------------------------ time (always the viewer's timezone)
function fmt(ts, opts) { try { return new Intl.DateTimeFormat('en-GB', { timeZone: S.tz, ...opts }).format(new Date(ts * 1000)); } catch (e) { return new Date(ts * 1000).toLocaleString(); } }
const tTime = ts => ts ? fmt(ts, { hour: 'numeric', minute: '2-digit', hour12: true }) : '—';
const tDay = ts => fmt(ts, { weekday: 'short', day: 'numeric', month: 'short' });
const tFull = ts => ts ? `${tDay(ts)} · ${tTime(ts)}` : '—';
function rel(ts) {
  if (!ts) return '—';
  const d = ts - Date.now() / 1000, a = Math.abs(d);
  const s = a < 60 ? 'less than a minute' : a < 3600 ? `${Math.round(a / 60)} min` : a < 86400 ? `${Math.floor(a / 3600)} h ${Math.round((a % 3600) / 60)} min` : `${Math.floor(a / 86400)} d ${Math.floor((a % 86400) / 3600)} h`;
  return d >= 0 ? `in ${s}` : `${s} ago`;
}
const flag = cc => cc && /^[A-Za-z]{2}$/.test(cc) ? String.fromCodePoint(...[...cc.toUpperCase()].map(c => 127397 + c.charCodeAt(0))) : '';
const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
function where(g) { const parts = [g.city, g.country_name || g.country].filter(Boolean); return parts.length ? `${flag(g.country)} ${parts.join(', ')}` : ''; }
function countdown(ts) {
  const d = Math.max(0, Math.round(ts - Date.now() / 1000));
  const days = Math.floor(d / 86400), h = Math.floor((d % 86400) / 3600), m = Math.floor((d % 3600) / 60), s = d % 60;
  if (days) return `${days}d ${h}h ${String(m).padStart(2, '0')}m`;
  return h ? `${h}h ${String(m).padStart(2, '0')}m ${String(s).padStart(2, '0')}s` : `${m}m ${String(s).padStart(2, '0')}s`;
}
const num = n => n == null ? '—' : n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e4 ? (n / 1e3).toFixed(1) + 'K' : n.toLocaleString('en');
function delta(cur, prev) {
  if (!prev) return '';
  const p = Math.round(100 * (cur - prev) / prev);
  return `<span class="delta ${p >= 0 ? 'up' : 'down'}">${ic(p >= 0 ? 'up' : 'down', 14)}${p >= 0 ? '+' : ''}${p}%</span>`;
}

// ------------------------------------------------------------------ charts
function spark(vals, w = 90, h = 28, color = '#0E1116', fill = true) {
  if (!vals || vals.length < 2) return `<svg width="${w}" height="${h}" aria-hidden="true"></svg>`;
  const mx = Math.max(...vals), mn = Math.min(...vals), n = vals.length;
  const pts = vals.map((v, i) => [(i * (w - 2) / (n - 1) + 1).toFixed(1), (h - 2 - (v - mn) / ((mx - mn) || 1) * (h - 6)).toFixed(1)]);
  const d = 'M' + pts.map(p => p.join(' ')).join(' L');
  const area = fill ? `<path d="${d} L${pts[n - 1][0]} ${h} L${pts[0][0]} ${h} Z" fill="${color}" opacity="0.08"/>` : '';
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">${area}<path d="${d}" fill="none" stroke="${color}" stroke-width="1.75" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
}
function areaChart(days, vals, prev, label) {
  const W = 720, H = 240, PL = 46, PB = 28;
  const mx = Math.max(10, ...vals, ...(prev || [])) * 1.1;
  const P = arr => arr.map((a, i) => [PL + i * (W - PL - 8) / Math.max(1, arr.length - 1), H - PB - (a / mx) * (H - PB - 12)]);
  const pc = P(vals), pp = prev && prev.length === vals.length ? P(prev) : null;
  const path = pts => 'M' + pts.map(p => p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' L');
  let g = '';
  for (let k = 0; k <= 4; k++) {
    const y = (H - PB - k * (H - PB - 12) / 4).toFixed(1);
    g += `<line x1="${PL}" x2="${W - 8}" y1="${y}" y2="${y}" stroke="#ECEAE4"/><text x="${PL - 8}" y="${+y + 4}" text-anchor="end" font-family="JetBrains Mono" font-size="10" fill="#5B606B">${num(Math.round(mx * k / 4))}</text>`;
  }
  const idx = [0, Math.floor(days.length / 3), Math.floor(2 * days.length / 3), days.length - 1];
  idx.forEach(i => { if (days[i]) g += `<text x="${pc[i][0].toFixed(1)}" y="${H - 8}" text-anchor="middle" font-family="JetBrains Mono" font-size="10" fill="#5B606B">${esc(new Date(days[i] + 'T12:00:00Z').toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }))}</text>`; });
  const last = pc[pc.length - 1];
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;max-height:${H}px;display:block" role="img" aria-label="${esc(label)}">
    <defs><linearGradient id="ga" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#0E1116" stop-opacity="0.14"/><stop offset="1" stop-color="#0E1116" stop-opacity="0"/></linearGradient></defs>
    ${g}${pp ? `<path d="${path(pp)}" fill="none" stroke="#B9B6AD" stroke-width="1.5" stroke-dasharray="4 4"/>` : ''}
    <path d="${path(pc)} L${last[0].toFixed(1)} ${H - PB} L${pc[0][0].toFixed(1)} ${H - PB} Z" fill="url(#ga)"/>
    <path d="${path(pc)}" fill="none" stroke="#0E1116" stroke-width="2.2" stroke-linejoin="round"/>
    <circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="5" fill="#E8492A" stroke="#fff" stroke-width="2"/></svg>`;
}
function barsChart(items, valueKey, planKey, height = 150) {
  const mx = Math.max(1, ...items.map(x => Math.max(x[valueKey] || 0, planKey ? x[planKey] || 0 : 0)));
  const every = items.length > 16 ? 5 : 1;
  return `<div class="bars" style="height:${height + 22}px">${items.map((x, i) => {
    const hv = Math.round((x[valueKey] || 0) / mx * height), hp = planKey ? Math.round((x[planKey] || 0) / mx * height) : 0;
    const col = x.today ? '#E8492A' : (planKey && (x[valueKey] || 0) < (x[planKey] || 0) ? '#5B606B' : '#0E1116');
    return `<div class="b" title="${esc(x.day)}: ${x[valueKey] || 0}${planKey ? ' of ' + (x[planKey] || 0) : ''}"><div style="position:relative;width:100%;max-width:22px;height:${height}px;display:flex;align-items:flex-end">
      ${planKey ? `<div style="position:absolute;bottom:0;left:0;right:0;height:${hp}px;border:1.5px dashed #C9C6BD;border-radius:6px"></div>` : ''}
      <div style="position:relative;width:100%;height:${hv}px;border-radius:6px;background:${col}"></div></div>
      <span class="mono muted" style="font-size:10px;height:12px">${i % every === 0 || i === items.length - 1 ? esc(String(x.day).slice(8)) : ''}</span></div>`;
  }).join('')}</div>`;
}

function liveBar(p, dark) {
  if (!p) return '';
  const pct = p.pct ?? 0;
  return `<div style="display:flex;flex-direction:column;gap:6px;min-width:0"><div style="display:flex;justify-content:space-between;gap:8px;font-size:12px;${dark ? 'color:#C9CDD6' : ''}"><span class="ell">${esc(p.stage || 'Working')}${p.title ? ' · ' + esc(p.title) : ''}</span><b class="mono">${pct}%</b></div>
    <div style="height:8px;border-radius:99px;background:${dark ? '#232A35' : '#EDEBE6'};overflow:hidden"><div class="barfill" style="width:${pct}%;height:100%;border-radius:99px;background:#E8492A"></div></div></div>`;
}

// ------------------------------------------------------------------ router
const NAV = [
  ['Workspace', [['grid', 'Dashboard', 'dashboard'], ['cal', 'Schedule', 'schedule'], ['tv', 'Clients', 'channels'], ['net', 'Pools', 'groups'], ['film', 'Videos', 'videos'], ['users', 'Accounts', 'accounts'], ['spark', 'AI captions', 'ai'], ['drive', 'Google Drive', 'drive'], ['gear', 'Settings', 'settings']]],
  ['Admin', [['users', 'Users', 'users'], ['batt', 'Devices', 'devices'], ['phone', 'Server', 'server']]],
];
function go(r) { if (location.hash !== '#/' + r) location.hash = '#/' + r; else render(); }
window.addEventListener('hashchange', () => render());

function clearTimers() { S.timers.forEach(t => clearInterval(t)); S.timers = []; }
function every(ms, fn) { S.timers.push(setInterval(fn, ms)); }

async function render() {
  clearTimers();
  closeModal();
  S.acts = {};
  const hraw = location.hash.replace(/^#\/?/, '') || 'dashboard';
  S.hq = new URLSearchParams(hraw.includes('?') ? hraw.split('?')[1] : '');
  const [r0, ...rest] = hraw.split('?')[0].split('/');
  if (!S.session) {
    try { S.session = await api('GET', '/api/session'); S.ver = S.session.version; } catch (e) { return fatal(e); }
    S.user = S.session.user;
    if (S.user) S.tz = S.user.timezone || S.tz;
  }
  if (r0 === 'reset' && rest[0]) return resetPage(rest[0]);
  if (!S.user) return authPage(r0 === 'register' ? 'register' : (S.session.needs_setup ? 'setup' : 'login'));
  const navd = S.lastHash !== location.hash; S.lastHash = location.hash; S.anim = navd;
  const cont = document.getElementById('content');
  if (navd && cont) { cont.className = ''; cont.innerHTML = SKELETON; window.scrollTo({ top: 0 }); }
  const page = PAGES[r0] || PAGES.dashboard;
  if (['users', 'server', 'devices'].includes(r0) && S.user.role !== 'owner') return go('dashboard');
  try { await page(...rest); } catch (e) { mount(r0, 'Something went wrong', '', '', '', `<div class="card err-card"><h2>Could not load this page</h2><p class="muted">${esc(e.message)}</p><button class="btn" data-act="reload">Try again</button></div>`); }
}
function fatal(e) { document.getElementById('app').innerHTML = `<div style="padding:40px"><h1>DriveCast</h1><p>${esc(e.message)}</p><button class="btn" data-act="reload">Retry</button></div>`; }

const MTABS = [['grid', 'Home', 'dashboard'], ['cal', 'Schedule', 'schedule'], ['tv', 'Clients', 'channels'], ['film', 'Videos', 'videos']];
function mount(active, title, sub, kick, actions, content) {
  const groups = NAV.filter(([g]) => g !== 'Admin' || S.user.role === 'owner');
  const nav = groups.map(([g, items]) => `<div class="nav"><span class="kicker">${g}</span>${items.map(([i, l, r]) => `<a href="#/${r}" class="${r === active ? 'on' : ''}" data-tip="${l}">${ic(i)}<span class="nl">${l}</span>${r === 'ai' ? '<em class="nbadge">New</em>' : ''}</a>`).join('')}</div>`).join('');
  const inTabs = MTABS.some(t => t[2] === active);
  const sheet = groups.flatMap(([, items]) => items).filter(([, , r]) => !MTABS.some(t => t[2] === r)).map(([i, l, r]) => `<a href="#/${r}" class="${r === active ? 'on' : ''}">${ic(i, 20)}${l}</a>`).join('');
  const mac = /Mac|iPhone|iPad/.test(navigator.platform || '');
  const th = themePref();
  const tbar = `<div class="tbar"><nav class="crumbs" aria-label="Breadcrumb"><a href="#/dashboard">Studio</a>${ic('chev', 12)}<span>${esc(kick || title)}</span></nav>
    <div class="tbar-r"><a class="up-pill hidden" id="uppill" href="#/server" title="A new version is ready">${ic('down', 14)}<span></span></a><button class="cmdk" data-act="palette" aria-label="Search">${ic('search', 15)}<span>Search or jump to…</span><kbd>${mac ? '⌘' : 'Ctrl'} K</kbd></button>
    <button class="tb-btn" data-act="theme" title="Theme: ${th}" aria-label="Change theme">${ic(th === 'dark' ? 'moon' : th === 'light' ? 'sun' : 'auto', 17)}</button>
    <a class="tb-btn" href="#/dashboard" title="Needs you" aria-label="Needs you">${ic('bell', 17)}<i class="tb-n hidden" id="tbn"></i></a></div></div>`;
  document.getElementById('app').innerHTML = `<div class="app ${railMini() ? 'mini' : ''}">
    <header class="mtop"><a class="brand" href="#/dashboard" aria-label="DriveCast home" style="text-decoration:none;padding:0">${BRAND()}</a><span class="pill" id="mdev"><span class="beat" id="mbeat"></span><span id="mlive">Live</span></span></header>
    <nav class="rail" aria-label="Main">
      <div class="rail-hd"><a class="brand" href="#/dashboard" aria-label="DriveCast home" style="text-decoration:none">${BRAND()}</a><button class="rail-tg" data-act="railmini" title="Collapse sidebar" aria-label="Collapse sidebar">${ic('side', 16)}</button></div><span class="rail-v mono" title="DriveCast version">${S.ver ? 'v' + esc(S.ver) : ''}</span>
      ${nav}
      <div style="flex-grow:1"></div>
      <a class="srv" href="${S.user.role === 'owner' ? '#/devices' : '#/dashboard'}" style="text-decoration:none" title="Devices"><span class="kicker" style="display:flex;justify-content:space-between"><span>Live session</span><span id="srvup" class="mono"></span></span><div style="display:flex;align-items:center;gap:8px"><span class="beat" id="srvdot"></span><span class="mono ell" style="font-size:12px;color:#fff">${esc(location.host)}</span></div><div id="livenow" class="livenow"></div><div class="dev" id="devline"><span>Online</span></div></a>
      <div class="me"><span class="av" style="width:32px;height:32px">${esc(S.user.name.charAt(0).toUpperCase())}</span><div style="display:flex;flex-direction:column;flex-grow:1;min-width:0"><span class="ell" style="font-size:13px;color:#fff;font-weight:600">${esc(S.user.name)}</span><span style="font-size:11.5px;color:#8E94A0">${S.user.role === 'owner' ? 'Owner' : 'Member'}</span></div><button data-act="logout" aria-label="Sign out" title="Sign out">${ic('logout')}</button></div>
    </nav>
    <main class="main"><div class="wrap">
      ${tbar}
      <header class="top"><div><div class="kick">${esc(kick)}</div><h1>${esc(title)}</h1><div class="sub">${sub}</div></div><div class="actions">${actions}</div></header>
      <div id="content" class="${S.anim ? 'enter' : ''}">${content}</div>
    </div></main>
    <nav class="mnav" aria-label="Main">${MTABS.map(([i, l, r]) => `<a href="#/${r}" class="${r === active ? 'on' : ''}">${ic(i, 20)}${l}</a>`).join('')}<button data-act="msheet" class="${inTabs ? '' : 'on'}">${ic('more', 20)}More</button></nav>
    <div class="sheet" id="sheet"><div><span class="kicker" style="color:var(--muted);padding:6px 10px">More</span>${sheet}<button data-act="logout">${ic('logout', 20)}Sign out · ${esc(S.user.name)}</button></div></div></div>`;
  showDevice();
  showPause();
  liveStart();
  paintLive();
  if (S.anim) requestAnimationFrame(() => countUp(document.getElementById('content')));
  deskin(document.getElementById('app'));
  updatePill();
}

// ------------------------------------------------------------------ live session: heartbeat + auto refresh
function liveStart() {
  if (S.liveT) return;
  const tick = async () => {
    if (!S.user) return;
    try {
      const l = await api('GET', '/api/live');
      const changed = S.live && S.live.mark !== l.mark;
      S.live = l; S.liveFail = 0; S.liveAt = Date.now();
      paintLive();
      // something happened (new post, new event, a post started or ended): refresh the page you are on
      const r0 = (location.hash.replace(/^#\/?/, '') || 'dashboard').split('/')[0];
      if (changed && ['dashboard', 'channel', 'channels', 'videos', 'schedule'].includes(r0) && !document.getElementById('modal')
          && !(document.activeElement && ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName))) render();
    } catch (e) { S.liveFail = (S.liveFail || 0) + 1; paintLive(); }
  };
  S.liveT = setInterval(tick, 5000);
  tick();
}
function upTxt(s) { return s >= 86400 ? `${Math.floor(s / 86400)}d ${Math.floor(s % 86400 / 3600)}h` : s >= 3600 ? `${Math.floor(s / 3600)}h ${Math.floor(s % 3600 / 60)}m` : `${Math.floor(s / 60)}m`; }
function paintLive() {
  const l = S.live, off = (S.liveFail || 0) >= 2;
  const state = off ? 'off' : !l ? '' : l.replaced ? 'warn' : l.paused ? 'paused' : l.running.length ? 'busy' : l.healthy ? '' : 'warn';
  ['srvdot', 'mbeat'].forEach(id => { const d = document.getElementById(id); if (d) d.className = 'beat ' + state; });
  const up = document.getElementById('srvup'); if (up && l) up.textContent = off ? 'offline' : 'up ' + upTxt(l.up);
  deskin(document.querySelector('.srv'));
  const tn = document.getElementById('tbn'); if (tn && l) { tn.textContent = l.attention > 9 ? '9+' : l.attention; tn.classList.toggle('hidden', !l.attention); }
  const ml = document.getElementById('mlive'); if (ml) ml.textContent = off ? 'Reconnecting' : l && l.running.length ? 'Posting' : 'Live';
  const box = document.getElementById('livenow');
  if (box) box.innerHTML = off ? '<span style="color:#FF9B85">Phone not answering — retrying…</span>'
    : !l ? '' : l.running.length ? l.running.map(r => `<div class="ln"><b class="ell">${esc(r.client)}</b><span class="ell">${esc(r.stage || 'Working')}</span><span class="lbar"><i style="width:${r.pct ?? 8}%"></i></span></div>`).join('')
      : `<span>${l.replaced ? '<b style="color:#F2B54A">This phone was replaced — posting stopped</b>' : l.paused ? 'Paused — nothing posts' : l.healthy ? 'All services healthy' : 'A service is slow: ' + esc(l.stale.join(', '))}</span>`;
}
async function loadDevice(force) {
  if (S.user.role !== 'owner') return (S.live && S.live.device) || null;
  if (!force && S.dev && Date.now() - S.devAt < 60000) return S.dev;
  try { S.dev = await api('GET', '/api/device'); S.devAt = Date.now(); } catch (e) { /* keep last */ }
  return S.dev;
}
function devText(d) {
  if (!d || d.battery == null) return null;
  const stale = d.at && Date.now() / 1000 - d.at > 600;
  const low = d.battery <= 20 && !d.charging;
  return { stale, low, html: `${ic('batt', 14)}<b style="color:${low ? '#FF9B85' : '#fff'}">${d.battery}%</b>${d.charging ? `<span class="bolt">${ic('bolt', 13)}</span><span>Charging</span>` : '<span style="color:#F2B54A">On battery</span>'}${d.temp ? `<span>· ${d.temp}°C</span>` : ''}${stale ? '<span>· not reporting</span>' : ''}` };
}
async function showDevice() {
  const d = await loadDevice();
  const t = devText(d);
  const line = document.getElementById('devline'), pill = document.getElementById('mdev');
  if (t && line) line.innerHTML = t.html;
  if (t && pill) pill.innerHTML = `${d.battery}%${d.charging ? `<span class="bolt">${ic('bolt', 12)}</span>` : ''}${t.stale ? ' · offline?' : ''}`;
}
document.addEventListener('click', e => { const sh = document.getElementById('sheet'); if (sh && sh.classList.contains('open') && (e.target === sh || e.target.closest('.sheet a'))) sh.classList.remove('open'); });

// global click / submit delegation
document.addEventListener('click', async e => {
  const el = e.target.closest('[data-act]');
  if (!el) return;
  const fn = S.acts[el.dataset.act] || GLOBAL_ACTS[el.dataset.act];
  if (!fn) return;
  e.preventDefault();
  if (el.dataset.busy) return;
  el.dataset.busy = '1';
  const old = el.innerHTML;
  if (el.tagName === 'BUTTON' && !el.classList.contains('step') && !el.classList.contains('pick')) el.disabled = true;
  try { await fn(el.dataset, el, e); } catch (err) { toast(err.message, true); }
  finally { delete el.dataset.busy; if (document.body.contains(el)) { el.disabled = false; if (el.innerHTML !== old && el.dataset.keep !== '1') { /* page may have re-rendered */ } } }
});
document.addEventListener('submit', async e => {
  const f = e.target.closest('form[data-form]');
  if (!f) return;
  e.preventDefault();
  const fn = S.acts[f.dataset.form];
  if (!fn) return;
  const btn = f.querySelector('button[type=submit]');
  if (btn) btn.disabled = true;
  try { await fn(Object.fromEntries(new FormData(f).entries()), f); } catch (err) { toast(err.message, true); }
  finally { if (btn) btn.disabled = false; }
});
const GLOBAL_ACTS = {
  logout: async () => { await api('POST', '/api/logout').catch(() => { }); S.user = null; S.session = null; go('login'); },
  reload: () => render(),
  copy: async d => { await copyText(d.text); toast('Copied'); },
  close: () => closeModal(),
  msheet: () => document.getElementById('sheet').classList.add('open'),
  palette: () => openPalette(),
  pweye: (x, el) => { const i = el.parentElement.querySelector('input'); const show = i.type === 'password'; i.type = show ? 'text' : 'password'; el.innerHTML = ic(show ? 'eyeoff' : 'eye', 16); el.setAttribute('aria-label', show ? 'Hide password' : 'Show password'); el.title = show ? 'Hide password' : 'Show password'; },
  theme: () => { const order = ['auto', 'light', 'dark']; const n = order[(order.indexOf(themePref()) + 1) % 3]; store_('dc_theme', n); applyTheme(); toast(`Theme: ${n === 'auto' ? 'follows your device' : n}`); const b = document.querySelector('[data-act=theme]'); if (b) { b.innerHTML = ic(n === 'dark' ? 'moon' : n === 'light' ? 'sun' : 'auto', 17); b.title = 'Theme: ' + n; } },
  railmini: () => { const m = !railMini(); store_('dc_rail', m ? 'mini' : ''); document.querySelector('.app').classList.toggle('mini', m); },
};
async function copyText(t) {
  try { await navigator.clipboard.writeText(t); } catch (e) {
    const a = document.createElement('textarea'); a.value = t; document.body.appendChild(a); a.select(); document.execCommand('copy'); a.remove();
  }
}
function modal(html) { closeModal(); const d = document.createElement('div'); d.className = 'modal-bg'; d.id = 'modal'; d.innerHTML = `<div class="modal" role="dialog" aria-modal="true">${html}</div>`; d.addEventListener('click', e => { if (e.target === d) closeModal(); }); document.body.appendChild(d); deskin(d); const f = d.querySelector('input,select,button'); if (f) f.focus(); }
function closeModal() { const m = document.getElementById('modal'); if (m) m.remove(); }
function safe(fn, title) { try { return fn(); } catch (e) { console.error(e); return `<section class="card err-card"><h2>${esc(title || 'This card')} could not load</h2><p class="muted">${esc(e.message)}</p></section>`; } }
const hd = (title, sub, right = '') => `<div class="hd"><div><h2>${esc(title)}</h2>${sub ? `<p>${sub}</p>` : ''}</div><div class="actions">${right}</div></div>`;
const addBtn = `<a class="btn primary" href="#/add">${ic('plus', 16)}Add client</a>`;

// ------------------------------------------------------------------ auth
function browserTz() { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Karachi'; } catch (e) { return 'Asia/Karachi'; } }
function authPage(mode) {
  const eq = Array.from({ length: 18 }, (_, i) => `<span style="height:${[40, 86, 58, 120, 72, 150, 96, 64, 132, 48, 104, 80, 56, 140, 90, 62, 118, 70][i]}px"></span>`).join('');
  const field = (n, l, t = 'text', ph = '', extra = '') => t === 'password'
    ? `<label class="f">${l}<span class="pw"><input class="i" name="${n}" type="password" placeholder="${esc(ph)}" ${extra}><button type="button" class="pw-eye" data-act="pweye" aria-label="Show password" title="Show password">${ic('eye', 16)}</button></span></label>`
    : `<label class="f">${l}<input class="i" name="${n}" type="${t}" placeholder="${esc(ph)}" ${extra}></label>`;
  const gOn = S.session && S.session.google_login;
  const gBtn = label => gOn ? `<a class="btn big gbtn" href="/oauth/google?mode=${mode === 'register' ? 'signup' : 'login'}${mode === 'register' ? '' : ''}" data-g="1">${GLOGO}${label}</a><div class="or"><span>or with email</span></div>` : '';
  let form;
  if (mode === 'restore') form = `<div class="tabs"><button type="button" data-act="tab-setup">New server</button><button type="button" class="on">Restore a backup</button></div>
    <div><h2 style="font-size:28px">Restore from a backup</h2><p class="muted">Brings back every account, channel, proxy, Instagram cookie and Google sign-in. Needs the setup code from the phone and your backup password.</p></div>
    ${field('setup_code', 'Setup code (on the phone)', 'text', 'SETUP-XXXX-XXXX', 'required autocomplete="off"')}
    <label class="f">Backup file (.dcbackup)<input class="i" type="file" id="bkfile" accept=".dcbackup" style="padding-top:9px"></label>
    ${field('link', 'Or a Google Drive link to it (shared as “Anyone with the link”)', 'url', 'https://drive.google.com/file/d/…')}
    ${field('password', 'Backup password', 'password', '', 'required autocomplete="off"')}
    <div id="rsmsg"></div><button class="btn primary big" type="submit">Restore</button>`;
  else if (mode === 'setup') form = `<div class="tabs"><button type="button" class="on">New server</button><button type="button" data-act="tab-restore">Restore a backup</button></div><div><h2 style="font-size:28px">Set up this server</h2><p class="muted">Create the owner account. The setup code is shown in the phone app.</p></div>
    ${field('setup_code', 'Setup code', 'text', 'SETUP-XXXX-XXXX', 'required autocomplete="off"')}${field('name', 'Your name', 'text', '', 'required')}${field('email', 'Email', 'email', '', 'required autocomplete="email"')}${field('password', 'Password (8+ characters)', 'password', '', 'required minlength="8" autocomplete="new-password"')}
    <button class="btn primary big" type="submit">Create owner account</button>`;
  else form = `<div class="tabs"><button type="button" class="${mode === 'login' ? 'on' : ''}" data-act="tab-login">Sign in</button><button type="button" class="${mode === 'register' ? 'on' : ''}" data-act="tab-register">Create account</button></div>
    ${mode === 'login' ? `<div><h2 style="font-size:28px">Welcome back</h2><p class="muted">Sign in to your workspace on this server.</p></div>
      ${gBtn('Continue with Google')}
      ${field('email', 'Email', 'email', 'you@example.com', 'required autocomplete="email"')}${field('password', 'Password', 'password', '', 'required autocomplete="current-password"')}
      <div style="display:flex;justify-content:flex-end;margin-top:-6px"><button type="button" class="linkbtn" data-act="forgot">Forgot password?</button></div>
      <button class="btn primary big" type="submit">Sign in</button>`
      : `<div><h2 style="font-size:28px">Create your account</h2><p class="muted">You need an invite code from the server owner. Your workspace is private — your own proxies, Drive and channels.</p></div>
      ${field('invite_code', S.session && S.session.signup_mode === 'approval' ? 'Invite code (optional)' : 'Invite code', 'text', 'DC-XXXX-XXXX', (S.session && S.session.signup_mode === 'approval' ? '' : 'required ') + 'autocomplete="off"')}
      ${S.session && S.session.signup_mode === 'approval' ? '<span class="help" style="margin-top:-6px">No code? Sign up anyway — the studio owner approves your account, usually within a day.</span>' : ''}
      ${gOn ? `<button type="button" class="btn big gbtn" data-act="gsignup">${GLOGO}Sign up with Google</button><div class="or"><span>or with email</span></div>` : ''}${field('name', 'Your name', 'text', '', 'required')}${field('email', 'Email', 'email', '', 'required autocomplete="email"')}${field('password', 'Password (8+ characters)', 'password', '', 'required minlength="8" autocomplete="new-password"')}
      <button class="btn primary big" type="submit">Create account</button>`}`;
  document.getElementById('app').innerHTML = `<div class="auth">
    <section class="hero"><div class="brand">${BRAND(true, true)}</div>
      <div style="flex-grow:1"></div><h1>Your clients,<br>on air while<br>you sleep.</h1>
      <p>Instagram, YouTube, TikTok and Drive in — YouTube, Facebook and Instagram out. Every client on its own IP, every file cleaned, every post at a real peak hour.</p>
      <div class="eq">${eq}</div><div class="mono" style="font-size:12px;color:#8E94A0">● ${esc(location.host)}</div></section>
    <section class="side"><form data-form="auth" autocomplete="on">${form}</form></section></div>`;
  // links from drivecast.site: #/register?invite=CODE and #/login?email=…
  const hq = S.hq || new URLSearchParams();
  const fill = (n, v) => { const i = document.querySelector(`[name=${n}]`); if (i && v) i.value = v; };
  fill('invite_code', hq.get('invite')); fill('email', hq.get('email'));
  if (mode === 'register' && hq.get('invite') === null && S.session && S.session.open_signup) {
    const i = document.querySelector('[name=invite_code]'); if (i) { i.required = false; i.placeholder = 'Optional — sign-ups are open'; }
  }
  S.acts['tab-login'] = () => { history.replaceState(null, '', '#/login'); authPage('login'); };
  S.acts['tab-register'] = () => { history.replaceState(null, '', '#/register'); authPage('register'); };
  S.acts.gsignup = () => { const i = document.querySelector('[name=invite_code]'); const code = (i && i.value.trim()) || ''; if (!code && !(S.session && (S.session.open_signup || S.session.signup_mode === 'approval'))) { toast('Enter your invite code first', true); if (i) i.focus(); return; } location.href = '/oauth/google?mode=signup' + (code ? '&invite=' + encodeURIComponent(code) : ''); };
  S.acts.forgot = () => {
    const e = (document.querySelector('[name=email]') || {}).value || '';
    modal(`<h2>Forgot your password?</h2><p class="muted" style="margin:0">Enter the email of your account. ${S.session && S.session.email_reset ? 'We send you a link to choose a new password.' : 'The studio owner gets your request and sends you a reset link.'}</p>
      <form data-form="forgotgo" style="display:flex;flex-direction:column;gap:12px"><label class="f">Email<input class="i" name="email" type="email" required value="${esc(e)}" autocomplete="email"></label>
      <div class="actions" style="justify-content:flex-end"><button type="button" class="btn" data-act="close">Cancel</button><button class="btn primary" type="submit">Send</button></div></form>
      ${gOn ? `<p class="mini" style="margin:0">Tip: <b>Continue with Google</b> signs you in without a password.</p>` : ''}`);
    S.acts.forgotgo = async v => { const r = await api('POST', '/api/auth/forgot', v); closeModal(); toast(r.emailed ? 'If that email has an account, a reset link is on its way — check your inbox and spam.' : 'Request sent. The studio owner will send you a reset link.'); };
  };
  S.acts['tab-restore'] = () => authPage('restore');
  S.acts['tab-setup'] = () => authPage('setup');
  S.acts.auth = async v => {
    if (mode === 'restore') return doRestore(v, $('#rsmsg'));
    v.timezone = browserTz();
    const path = mode === 'setup' ? '/api/setup' : mode === 'register' ? '/api/register' : '/api/login';
    const r = await api('POST', path, v);
    if (r.pending) {
      document.querySelector('.auth .side').innerHTML = `<div style="display:flex;flex-direction:column;gap:14px;max-width:420px"><span class="ai-ic" style="width:48px;height:48px">${ic('cal', 22)}</span><h2 style="font-size:26px;margin:0">Waiting for approval</h2><p class="muted" style="margin:0">${esc(r.message)}</p><a class="btn" href="#/login" data-act="tab-login">Back to sign in</a></div>`;
      return;
    }
    S.user = r.user; S.tz = r.user.timezone; S.session = { ...S.session, user: r.user };
    location.hash = mode === 'login' ? '#/dashboard' : '#/settings';
    render();
  };
}

function readFileB64(input) {
  return new Promise((ok, bad) => {
    const f = input && input.files && input.files[0];
    if (!f) return ok(null);
    const r = new FileReader(); r.onload = () => ok(String(r.result).split(',')[1]); r.onerror = () => bad(new Error('Could not read the file')); r.readAsDataURL(f);
  });
}
async function doRestore(v, out) {
  const file_b64 = await readFileB64($('#bkfile'));
  if (!file_b64 && !v.link) throw new Error('Choose the backup file or paste its link');
  out.innerHTML = '<div class="note info"><span class="spinner"></span><span>Checking the backup…</span></div>';
  const r = await api('POST', '/api/restore', { setup_code: v.setup_code, password: v.password, link: v.link || null, file_b64 });
  const sm = r.summary || {};
  out.innerHTML = `<div class="note ok">${ic('check', 16)}<span><b>Backup accepted</b> — ${sm.accounts || 0} account(s), ${sm.channels || 0} channel(s), ${sm.groups || 0} Pool(s), ${sm.posts || 0} post(s). The server is restarting with it…</span></div>`;
  for (let i = 0; i < 90; i++) {
    await new Promise(z => setTimeout(z, 2000));
    try { const h = await fetch('/healthz', { cache: 'no-store' }); if (h.ok && i > 1) { S.session = null; S.user = null; location.hash = '#/login'; return render(); } } catch (e) { /* restarting */ }
  }
  out.innerHTML += '<div class="note warn"><span>Still restarting. Open the DriveCast app on the phone once — it starts the server — then sign in here.</span></div>';
}

// ------------------------------------------------------------------ backup & restore (Server page)
async function drawBackup() {
  const box = document.getElementById('bkcard'); if (!box) return;
  const b = await api('GET', '/api/backup');
  const sm = b.summary || {};
  box.innerHTML = `<section class="card">${hd('Backup & restore', 'Everything on this server in one encrypted file: accounts, channels, proxies, Instagram cookies, Google sign-ins and the full post history.', b.has_password ? chip('Nightly backup on', 'ok') : chip('Not protected yet', 'bad'))}
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(280px,1fr));margin:0">
      <div style="display:flex;flex-direction:column;gap:10px;padding:14px;border:1px solid var(--line);border-radius:12px">
        <b style="font-size:14px">1 · Automatic backup to your Drive</b>
        <span class="muted" style="font-size:12.5px">${b.has_password ? `Every night into “_DriveCast backups” in your main Drive folder · last 7 kept.<br>${b.last_at ? `Last: <b>${esc(rel(b.last_at))}</b> · ${esc(b.last_name || '')}` : 'First backup is being made…'}` : 'Choose a backup password. Write it down — without it no one (not even you) can open a backup.'}</span>
        ${b.error ? `<div class="note warn">${ic('alert', 14)}<span>${esc(b.error)}</span></div>` : ''}
        <form data-form="bkpw" class="actions"><input class="i" type="password" name="password" placeholder="${b.has_password ? 'Change backup password' : 'Backup password (8+)'}" autocomplete="new-password" style="flex:1;min-width:160px" aria-label="Backup password"><button class="btn primary" type="submit">${b.has_password ? 'Change' : 'Turn on'}</button></form>
        ${b.has_password ? `<span class="actions"><button class="btn" data-act="bknow">${ic('refresh', 16)}Back up to Drive now</button><button class="btn" data-act="bktest">${ic('shield', 16)}Test restore</button></span><span class="help">The newest Drive backup is test-opened every week by itself.</span>` : ''}
      </div>
      <div style="display:flex;flex-direction:column;gap:10px;padding:14px;border:1px solid var(--line);border-radius:12px">
        <b style="font-size:14px">2 · Download a backup file</b>
        <span class="muted" style="font-size:12.5px">Keep a copy on your laptop too. Now: ${sm.accounts || 0} account(s), ${sm.channels || 0} channel(s), ${sm.groups || 0} group(s), ${sm.posts || 0} post(s).</span>
        <form data-form="bkdl" class="actions">${b.has_password ? '' : '<input class="i" type="password" name="password" placeholder="Password for this file (8+)" style="flex:1;min-width:160px" aria-label="Password for this file">'}<button class="btn" type="submit">${ic('down', 16)}Download .dcbackup</button></form>
      </div>
      <div style="display:flex;flex-direction:column;gap:10px;padding:14px;border:1px solid var(--line);border-radius:12px">
        <b style="font-size:14px">3 · Restore</b>
        <span class="muted" style="font-size:12.5px">New or reset phone? Install the app, open the panel and choose <b>Restore a backup</b> on the first screen. Here it replaces everything on this server.</span>
        <button class="btn danger" data-act="bkrestore">Restore a backup here…</button>
      </div></div></section>`;
  Object.assign(S.acts, {
    bkpw: async v => { await api('POST', '/api/backup/password', v); toast('Backup password saved · first backup is being made'); setTimeout(drawBackup, 4000); drawBackup(); },
    bknow: async () => { await api('POST', '/api/backup/now'); toast('Backing up to Drive…'); setTimeout(drawBackup, 6000); },
    bktest: async () => { try { const r = await api('POST', '/api/backup/test'); toast(`Backup opens fine · ${r.name}`); } catch (e) { toast(e.message, true); } },
    bkdl: async v => {
      const r = await fetch('/api/backup/export', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-DC': '1' }, body: JSON.stringify(v.password ? { password: v.password } : {}) });
      if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.error || 'Download failed'); }
      const blob = await r.blob(); const a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = (r.headers.get('Content-Disposition') || '').split('filename="')[1]?.replace('"', '') || 'DriveCast.dcbackup'; a.click();
      toast('Backup downloaded');
    },
    bkrestore: () => {
      modal(`<h2>Restore a backup</h2><div class="note bad">${ic('alert', 16)}<span>Everything on this server is replaced by the backup. The current data is kept aside on the phone, not deleted.</span></div>
        <form data-form="bkrs" style="display:flex;flex-direction:column;gap:12px"><label class="f">Backup file (.dcbackup)<input class="i" type="file" id="bkfile" accept=".dcbackup" style="padding-top:9px"></label>
        <label class="f">Or a Google Drive link (shared as “Anyone with the link”)<input class="i" name="link" type="url"></label>
        <label class="f">Backup password<input class="i" name="password" type="password" required></label><div id="rsmsg"></div>
        <div class="actions" style="justify-content:flex-end"><button class="btn" type="button" data-act="close">Cancel</button><button class="btn danger" type="submit">Restore and restart</button></div></form>`);
      S.acts.bkrs = v => doRestore(v, $('#rsmsg'));
    },
  });
}

// ------------------------------------------------------------------ pause everything (vacation)
async function showPause() {
  try { S.pause = await api('GET', '/api/pause'); } catch (e) { return; }
  const c = document.getElementById('content');
  if (!c || !S.pause.paused || document.getElementById('pausebar')) return;
  const p = S.pause;
  c.insertAdjacentHTML('afterbegin', `<div class="away" id="pausebar" style="background:#FFF6E5;border-color:#F2D29A">${ic('pause', 16)}<span><b>Everything is paused</b> ${p.forever ? '— until you resume' : `— resumes by itself ${esc(tFull(p.until))}`}. ${p.keep_collecting ? 'New Instagram posts are still collected.' : 'Nothing is collected.'}</span><button class="btn sm primary" style="margin-left:auto" data-act="resumeall">Resume now</button></div>`);
  S.acts.resumeall = async () => { await api('POST', '/api/pause', { resume: true }); toast('Posting resumed'); render(); };
}
function pauseModal() {
  modal(`<h2>Pause everything</h2><p class="muted" style="margin:0">Going away for a while? No channel posts until you resume — or until the time you pick. Nothing is lost: the queue simply waits.</p>
    <form data-form="pauseall" style="display:flex;flex-direction:column;gap:12px">
      <div class="actions">${[['0', 'Until I resume'], ['1', '1 day'], ['3', '3 days'], ['7', '1 week'], ['14', '2 weeks']].map(([d, t], i) => `<label class="btn sm" style="cursor:pointer"><input type="radio" name="days" value="${d}" ${i === 0 ? 'checked' : ''} style="margin:0 6px 0 0">${t}</label>`).join('')}</div>
      <label class="f">Or until a date and time<input class="i" type="datetime-local" name="until"></label>
      <label style="display:flex;gap:10px;align-items:center;font-size:13px"><input type="checkbox" name="keep" checked> Keep collecting new posts from Instagram while paused (they wait, ready to post)</label>
      <div class="actions" style="justify-content:flex-end"><button class="btn" type="button" data-act="close">Cancel</button><button class="btn primary" type="submit">${ic('pause', 16)}Pause everything</button></div></form>`);
  S.acts.pauseall = async v => {
    let until = null;
    if (v.until) until = new Date(v.until).getTime() / 1000; else if (+v.days) until = Date.now() / 1000 + (+v.days) * 86400;
    await api('POST', '/api/pause', { until, keep_collecting: !!v.keep });
    closeModal(); toast('Everything paused'); render();
  };
}

// ------------------------------------------------------------------ dashboard
async function pageDashboard() {
  let last = 0; try { last = +localStorage.getItem('dc_seen') || 0; } catch (e) { /* private mode */ }
  const d = await api('GET', `/api/dashboard${last ? '?since=' + last : ''}`);
  if (!S.awayShown) { S.away = d.away; S.awayShown = true; try { localStorage.setItem('dc_seen', String(Math.floor(Date.now() / 1000))); } catch (e) { /* */ } }
  const hasChannels = d.today.channels.length || d.pulse.length;
  const nxt = d.today.next;
  const fresh = d.kpis.stats_at ? `stats ${esc(rel(d.kpis.stats_at))}` : 'waiting for first stats';
  const sub = hasChannels ? `${d.today.done} of ${plural(d.today.total, 'post')} done${nxt ? ` · next ${esc(rel(nxt.due_at))}` : ''} · <span title="YouTube numbers refresh about every 20 minutes">${fresh}</span>` : 'Let’s get your first channel on air';
  const content = hasChannels ? [
    S.away ? safe(() => awayBar(S.away), 'Since last visit') : '',
    safe(() => todayCard(d), 'Today'),
    safe(() => kpis(d), 'Numbers'),
    `<div class="dgrid"><div class="s8">${safe(() => trendCard(d), 'Views')}</div><div class="s4">${safe(() => postsCard(d), 'Latest posts')}</div></div>`,
    `<div class="dgrid"><div class="s12">${safe(() => pulseCard(d), 'Channels')}</div></div>`,
    `<div class="dgrid"><div class="s4">${safe(() => upnextCard(d), 'Up next')}</div><div class="s4">${safe(() => feedCard(d), 'Activity')}</div><div class="s4">${safe(() => attentionCard(d), 'Needs you')}</div></div>`,
    `<div class="dgrid"><div class="s8">${safe(() => heatCard(d), 'Best hours')}</div><div class="s4">${safe(() => consCard(d), 'Consistency')}</div></div>`,
  ].join('') : onboarding(d);
  mount('dashboard', 'On air today', sub, fmt(Date.now() / 1000, { weekday: 'long', day: 'numeric', month: 'long' }), `<button class="btn" data-act="pausemodal" title="Going away? Pause every channel">${ic('pause', 16)}Pause all</button><button class="btn" data-act="statsnow" title="Fetch fresh numbers from YouTube now">${ic('refresh', 16)}Refresh stats</button>${addBtn}`, content);
  every(1000, () => document.querySelectorAll('[data-count]').forEach(el => { el.textContent = countdown(+el.dataset.count); }));
  const liveNow = (d.today.live || []).length > 0;
  every(liveNow ? 4000 : 60000, () => { if (!document.getElementById('modal') && !document.querySelector('.sheet.open')) render(); });
  S.acts['post-now'] = async x => { await api('POST', `/api/channels/${x.id}/post-now`); toast('Queued — it will post in a moment'); render(); };
  S.acts.dismiss = async x => { await api('POST', `/api/events/${x.id}/dismiss`); render(); };
  S.acts.fixit = x => runFix(S.attention[+x.i]);
  S.acts.pausemodal = () => pauseModal();
  S.acts.evdetail = x => eventDetail(+x.id);
  S.acts.awayx = () => { S.away = null; render(); };
  S.acts.statsnow = async (x, el) => {
    const r = await api('POST', '/api/stats/refresh');
    el.innerHTML = `<span class="spinner"></span>Fetching from YouTube…`;
    toast(r.started ? 'Fetching fresh numbers through each channel’s proxy' : 'Already refreshing — one moment');
    setTimeout(() => { if (location.hash.includes('dashboard') || !location.hash.replace('#/', '')) render(); }, 9000);
    el.dataset.keep = '1';
  };
}
function awayBar(a) {
  const hrs = (Date.now() / 1000 - a.since) / 3600;
  const ago = hrs < 24 ? `${Math.max(1, Math.round(hrs))}h` : `${Math.round(hrs / 24)}d`;
  const bits = [`<b>${plural(a.published, 'post')}</b> published`, `<b>+${num(a.views)}</b> views`, a.problems ? `<b style="color:var(--bad)">${plural(a.problems, 'problem')}</b>` : '<span style="color:var(--ok)">no problems</span>'];
  return `<div class="away">${ic('clock', 16)}<span>While you were away <span class="muted">(${ago})</span>:</span>${bits.join('<span class="muted">·</span>')}<button class="btn sm" style="margin-left:auto;height:26px" data-act="awayx" aria-label="Hide">Got it</button></div>`;
}
function onboarding(d) {
  const step = (n, t, p, href, b) => `<div style="display:flex;gap:14px;padding:16px 0;border-top:1px solid var(--line)"><span class="av av1" style="width:32px;height:32px">${n}</span><div style="flex:1"><strong>${t}</strong><p class="muted" style="margin:4px 0 0">${p}</p></div><a class="btn sm" href="${href}">${b}</a></div>`;
  return `<section class="today"><span class="onair"><i></i>READY</span><h2 style="margin-top:14px;font-size:28px">Three steps to your first post</h2><p style="color:#A9AFBA;margin:8px 0 0">Do these once. After that DriveCast publishes on its own, inside peak hours.</p></section>
    <section class="card"><div class="list">${step(1, 'Add your Google API app', 'Paste your OAuth Client ID and secret, and register the redirect link shown.', '#/settings', 'Open Settings')}
    ${step(2, 'Connect Google Drive', 'Authorize your Drive once and paste your main folder link.', '#/drive', 'Connect Drive')}
    ${step(3, 'Add a client', 'Pick a pool, then the client’s sources (Drive, Instagram, YouTube, TikTok) and where it posts.', '#/add', 'Add client')}</div></section>`;
}
function ringSvg(done, total, size = 44) {
  const r = size / 2 - 4, c = 2 * Math.PI * r, p = total ? done / total : 0;
  return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" aria-hidden="true"><circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="#232A35" stroke-width="5"/><circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="#3DDC97" stroke-width="5" stroke-linecap="round" stroke-dasharray="${(c * p).toFixed(1)} ${c.toFixed(1)}" transform="rotate(-90 ${size / 2} ${size / 2})"/></svg>`;
}
function dotsHtml(states) {
  return `<span class="dots">${(states || []).map(s => `<i class="${s === 'done' ? 'done' : ['held', 'failed', 'uncertain', 'missed'].includes(s) ? 'held' : 'next'}" title="${esc(s)}"></i>`).join('')}</span>`;
}
function todayCard(d) {
  const t = d.today, k = d.kpis;
  const vy = k.views_yesterday ? delta(k.views_today, k.views_yesterday) : '';
  const nx = t.next ? `<strong data-count="${t.next.due_at}">${countdown(t.next.due_at)}</strong><em class="ell">${esc(tTime(t.next.due_at))} · ${esc(t.next.title)}</em>${t.next.item ? `<em class="ell" style="color:#C9CDD6">${originChip(t.next.item.origin, true)} ${esc(t.next.item.name)}</em>` : ''}` : '<strong>—</strong><em>Nothing planned</em>';
  const live = (t.live || []);
  const rows = t.channels.map(c => `<div><span class="ell" style="color:#fff">${esc(c.title)}</span><span style="display:flex;align-items:center;gap:10px">${dotsHtml(c.states)}<span class="mono" style="font-size:11px;color:#8E94A0;min-width:52px;text-align:right">${c.next ? esc(tTime(c.next)) : 'done'}</span></span></div>`).join('');
  return `<section class="today"><div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap"><span class="onair"><i></i>${live.length ? 'POSTING' : 'ON AIR'}</span><h2 style="font-size:17px">Today</h2><span class="mono" style="font-size:11px;color:#8E94A0">Times in ${esc(S.tz)}</span>${S.tz !== browserTz() ? `<a href="#/settings" style="font-size:11px;color:#FF9B85;margin-left:auto">Your device is in ${esc(browserTz())} — change?</a>` : ''}</div>
    <div class="strip">
      <div><span>Published</span><div style="display:flex;align-items:center;gap:10px">${ringSvg(t.done, t.total, 34)}<strong>${t.done}<span style="color:#5B606B"> / ${t.total}</span></strong></div></div>
      <div><span>Next post</span>${nx}</div>
      <div><span>Views today</span><strong>${num(t.views_today)}</strong><em>${vy || 'live from your videos'}</em></div>
      <div><span>Posting now</span><strong style="color:${live.length ? '#FF9B85' : '#fff'}">${live.length}</strong><em>${live.length ? esc(live[0].stage || '') : 'idle'}</em></div>
      <div><span>Held or missed</span><strong style="color:${t.held ? '#F2B54A' : '#fff'}">${t.held}</strong><em>${t.held ? 'see Needs you' : 'all good'}</em></div>
    </div>
    ${live.length ? `<div style="margin-top:12px;display:flex;flex-direction:column;gap:10px">${live.map(p => `<div style="display:grid;grid-template-columns:minmax(110px,200px) 1fr;gap:14px;align-items:center"><span class="who" style="gap:8px">${avatar(p.channel, hashI(p.channel || ''), p.thumbnail, 24)}<b class="ell" style="font-size:13px">${esc(p.channel || '')}</b></span>${liveBar(p, true)}</div>`).join('')}</div>` : ''}
    ${rows ? `<div class="chrow" style="margin-top:12px;padding-top:10px">${rows}</div>` : ''}</section>`;
}
function kpis(d) {
  const k = d.kpis;
  const tile = (label, icon, val, foot, extra = '', cls = '') => `<div class="kpi ${cls}"><span class="l">${ic(icon, 13)}${label}</span><div style="display:flex;align-items:flex-end;justify-content:space-between;gap:6px"><span class="v">${val}</span>${extra}</div><span class="f">${foot}</span></div>`;
  const rw = k.runway_min;
  const L = d.lenses;
  const st = (label, val, foot = '') => `<div class="lst"><span class="l">${label}</span><span class="lv">${val}</span><span class="f">${foot}</span></div>`;
  const lens = L ? `<div class="lens2">
    <section class="lens"><div class="lens-h"><span class="lens-ic">${ic('tv', 16)}</span><div><b>Whole accounts</b><span>Everything on ${plural(L.account.channels, 'channel')} — old videos too</span></div></div>
      <div class="lens-g">${st('All-time views', num(L.account.views_total))}${st('Views · 7 days', num(L.account.views_7d), `${delta(k.views_7d, k.views_7d_prev)}<span>vs last week</span>`)}
      ${st('Subscribers', num(L.account.subscribers), L.account.subs_7d ? `<span class="delta ${L.account.subs_7d >= 0 ? 'up' : 'down'}">${L.account.subs_7d >= 0 ? '+' : ''}${num(L.account.subs_7d)}</span><span>this week</span>` : '<span>all channels</span>')}
      ${st('Videos on the channels', L.account.videos ? num(L.account.videos) : '—', '<span>YouTube count</span>')}</div></section>
    <section class="lens posted"><div class="lens-h"><span class="lens-ic">${ic('spark', 16)}</span><div><b>Posted by DriveCast</b><span>Only the posts this studio made</span></div></div>
      <div class="lens-g">${st('Posts', num(L.posted.posts), `<span>${num(L.posted.youtube_posts)} on YouTube</span>`)}${st('Views on them', num(L.posted.views), L.posted.likes ? `<span>${num(L.posted.likes)} likes</span>` : '<span>YouTube views</span>')}
      ${st('Gained · 7 days', num(L.posted.views_7d), '<span>on these posts</span>')}${st('Avg / post', L.posted.avg == null ? '—' : num(L.posted.avg), '<span>YouTube</span>')}</div>
      ${L.posted.best ? `<a class="lens-best" href="${L.posted.best.external_video_id ? 'https://youtu.be/' + esc(L.posted.best.external_video_id) : '#/videos'}" ${L.posted.best.external_video_id ? 'target="_blank" rel="noopener"' : ''}><span class="kicker">Best post</span><b class="ell">${esc(L.posted.best.title)}</b><span class="mini ell">${esc(L.posted.best.channel)}</span><b class="mono">${num(L.posted.best.views)} views</b></a>` : ''}
      <a class="lens-more" href="#/videos">Every post with its views ${ic('chev', 13)}</a></section></div>` : '';
  return `${lens}<div class="kp" ${L ? 'style="grid-template-columns:repeat(4,minmax(0,1fr))"' : ''}>
    ${L ? '' : tile('Views · 7 days', 'eye', num(k.views_7d), `${delta(k.views_7d, k.views_7d_prev)}<span>vs last week</span>`, spark(k.views_series, 64, 24, '#0E1116'))}
    ${L ? '' : tile('Subscribers', 'users', num(k.subscribers), '<span>all channels</span>')}
    ${tile('Posts · 7 days', 'film', k.posts_7d, `${delta(k.posts_7d, k.posts_7d_prev)}<span>${k.published_today}/${k.planned_today} today</span>`)}
    ${tile('Avg views / post', 'bolt', k.avg_first24 != null ? num(k.avg_first24) : num(k.avg_views), k.avg_first24 != null ? '<span>after the first day</span>' : '<span>so far · last 30 days</span>')}
    ${tile('Posting health', 'check', k.health == null ? '—' : k.health + '%', '<span>on time · 7 days</span>', '', k.health != null && k.health < 80 ? 'alert' : '')}
    ${tile('Content runway', 'folder', rw == null ? '—' : rw < 1 ? '<1 day' : `${rw} ${rw === 1 ? 'day' : 'days'}`, rw == null ? '<span>videos left in Drive</span>' : '<span>shortest channel</span>', '', rw != null && rw < 3 ? 'alert' : '')}
  </div>`;
}
function trendCard(d) {
  const t = d.trend, sum = t.views.reduce((a, b) => a + b, 0), prev = (t.previous || []).reduce((a, b) => a + b, 0);
  const nonzero = t.views.filter(v => v > 0).length;
  const chart = nonzero >= 2 ? areaChart(t.days, t.views, t.previous, 'Daily views, last 30 days')
    : nonzero === 1 ? barsChart(t.days.slice(-14).map((day, i) => ({ day, published: t.views.slice(-14)[i], today: i === 13 })), 'published', null, 150) + '<p class="mini" style="margin:6px 0 0">The line chart appears once there are two days of numbers.</p>'
      : '<div class="empty">Views appear here after the first stats come in (about 5 minutes after a post).</div>';
  return `<section class="card" style="height:100%">${hd('Views across all channels', 'Views gained per day, in your time')}
    <div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:4px"><strong class="big" style="font-size:24px">${num(sum)}</strong>${delta(sum, prev)}<span class="mini">last 30 days · ${num(t.total_views)} lifetime</span></div>
    ${chart}
    ${nonzero >= 2 ? '<div style="display:flex;gap:14px;margin-top:4px;font-size:11.5px" class="muted"><span>━ This period</span><span>┄ Previous period</span></div>' : ''}</section>`;
}
function postsCard(d) {
  const ps = d.recent || [];
  const rates = ps.filter(p => p.per_hour != null).map(p => p.per_hour);
  const avg = rates.length ? rates.reduce((a, b) => a + b, 0) / rates.length : 0;
  const rows = ps.slice(0, 6).map(p => {
    const hot = p.per_hour != null && p.views >= 100 && rates.length > 1 && p.per_hour >= 1.5 * avg;
    return `<div class="post"><div style="min-width:0;display:flex;flex-direction:column;gap:2px"><a class="ell" style="font-weight:600;color:var(--ink);text-decoration:none" ${p.video_id ? `href="https://youtu.be/${esc(p.video_id)}" target="_blank" rel="noopener"` : ''} title="${esc(p.title)}">${esc(p.title)}</a><span class="mini ell">${esc(p.channel)} · ${esc(rel(p.published_at))}</span></div>
      <div style="text-align:right;display:flex;flex-direction:column;align-items:flex-end;gap:2px"><b class="mono" style="font-size:14px">${num(p.views)}</b><span class="mini">${hot ? `<span class="chip coralc" style="height:18px;font-size:10.5px;padding:0 6px">${ic('flame', 11)}Hot</span> ` : ''}${p.per_hour != null ? `${num(p.per_hour)}/h` : ''}${p.last_hour ? ` · <span style="color:var(--ok)">+${num(p.last_hour)}</span> last hr` : ''}</span></div></div>`;
  }).join('');
  return `<section class="card" style="height:100%">${hd('Latest posts', 'Views and speed since publishing', '<a href="#/videos" style="font-size:12px;font-weight:600">All videos</a>')}${rows || '<div class="empty">Your posts will show here with live views.</div>'}</section>`;
}
function runwayHtml(p) {
  if (p.queue == null) return '<span class="mini">—</span>';
  const days = p.runway_days, pct = Math.min(100, Math.round(100 * days / 14));
  const col = days < 3 ? '#B42318' : days < 7 ? '#C27A12' : '#0B5E57';
  return `<div style="display:flex;flex-direction:column;gap:4px;min-width:0"><span style="font-size:12px"><b style="color:${col}">${days < 1 ? '<1' : days} d</b> <span class="mini">· ${plural(p.queue, 'video')}</span></span><div class="runway"><span style="width:${pct}%;background:${col}"></span></div></div>`;
}
function pulseCard(d) {
  const rows = d.pulse.map((p, i) => {
    const gdown = p.group_status === 'down';
    const next = p.live ? liveBar(p.live) : p.next ? `<div style="display:flex;flex-direction:column"><b class="mono" data-count="${p.next}" style="font-size:13px">${countdown(p.next)}</b><span class="mini">${esc(tTime(p.next))}</span></div>` : `<span class="mini">${p.state === 'live' ? 'Nothing planned' : esc(p.state)}</span>`;
    return `<div class="pt tr"><div class="who">${avatar(p.title, hashI(p.title), p.thumbnail, 32)}<div><b class="ell">${esc(p.title)}</b><span class="mini ell">${p.handle ? esc(p.handle.startsWith('@') ? p.handle : '@' + p.handle) + ' · ' : ''}${gdown ? '<span style="color:var(--bad)">Group ' + esc(p.group_number) + ' down</span>' : `Pool ${esc(p.group_number ?? '—')} ${flag(p.group_country)}`}</span></div></div>
      <div style="display:flex;flex-direction:column;gap:4px">${dotsHtml(p.states)}<span class="mini">${p.done}/${p.total} today</span></div>
      <div>${next}</div>
      <div><b class="mono">${num(p.views_today)}</b><div class="mini">today</div></div>
      <div style="display:flex;align-items:center;gap:8px">${spark(p.series, 64, 24, p.views_7d >= p.prev_7d ? '#0B5E57' : '#B42318', false)}<div><b class="mono">${num(p.views_7d)}</b>${delta(p.views_7d, p.prev_7d) || '<div class="mini">7 days</div>'}</div></div>
      <div><b class="mono">${num(p.subscribers)}</b><div class="mini">${p.subs_7d ? `<span style="color:${p.subs_7d > 0 ? 'var(--ok)' : 'var(--bad)'}">${p.subs_7d > 0 ? '+' : ''}${num(p.subs_7d)}</span> this week` : 'subscribers'}</div></div>
      ${runwayHtml(p)}
      <div style="min-width:0">${p.last_post ? `<span class="ell" style="display:block;font-size:12.5px;font-weight:600" title="${esc(p.last_post.title)}">${esc(p.last_post.title)}</span><span class="mini">${num(p.last_post.views)} views · ${esc(rel(p.last_post.at))}</span>` : '<span class="mini">No posts yet</span>'}</div></div>`;
  }).join('');
  return `<section class="card">${hd('Channel pulse', 'Every channel at a glance — today, next post, growth and how long its Drive videos will last', `<a href="#/channels" style="font-size:12px;font-weight:600">Manage channels</a>`)}
    <div class="scroll-x"><div style="min-width:1060px" class="pmin"><div class="pt tr th"><span>Channel</span><span>Today</span><span>Next post</span><span>Views</span><span>Last 7 days</span><span>Subscribers</span><span>Runway</span><span>Last post</span></div>${rows}</div></div></section>`;
}
function heatCard(d) {
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  if (d.heat_samples < 10) return `<section class="card" style="height:100%">${hd('Best hours to post', 'Average first-day views by publish hour, in your time')}<div class="empty" style="padding:16px">Appears after about 10 posts have a full day of stats (${d.heat_samples}/10). Until then the scheduler uses known peak hours for each audience.</div></section>`;
  const all = d.heatmap.flat().filter(v => v != null), mx = Math.max(1, ...all);
  let cells = '';
  d.heatmap.forEach((row, i) => { cells += `<span class="mono muted" style="font-size:11px;align-self:center">${days[i]}</span>` + row.map((v, h) => `<span class="c" title="${days[i]} ${h}:00 — ${v == null ? 'no posts' : num(v) + ' views'}" style="background:${v == null ? '#F2F1EC' : `rgba(15,118,110,${(0.12 + 0.88 * v / mx).toFixed(2)})`}"></span>`).join(''); });
  cells += '<span></span>' + Array.from({ length: 24 }, (_, h) => `<span class="mono muted" style="font-size:10px;text-align:center">${h % 3 === 0 ? String(h).padStart(2, '0') : ''}</span>`).join('');
  return `<section class="card" style="height:100%">${hd('Best hours to post', `Average first-day views by publish hour (${esc(S.tz)}) · ${d.heat_samples} posts`)}<div class="tbl" style="overflow-x:auto"><div class="heat">${cells}</div></div></section>`;
}
function consCard(d) {
  return `<section class="card" style="height:100%">${hd('Upload consistency', 'Published vs planned, last 14 days')}${barsChart(d.consistency, 'published', 'planned', 120)}
    <div style="display:flex;gap:12px;margin-top:8px;font-size:11.5px" class="muted"><span>■ Published</span><span>┄ Planned</span><span style="color:#E8492A">■ Today</span></div></section>`;
}
function upnextCard(d) {
  const rows = d.upnext.slice(0, 7).map(s => `<div style="display:grid;grid-template-columns:84px minmax(0,1fr) auto;gap:10px;align-items:center;padding:8px 0;border-top:1px solid var(--line)">
    <div style="display:flex;flex-direction:column;min-width:0"><strong class="mono" style="font-size:13px">${esc(tTime(s.due_at))}</strong><span class="mini ell" data-count="${s.due_at}">${countdown(s.due_at)}</span></div>
    <div class="who" style="gap:8px">${avatar(s.title, hashI(s.title), s.thumbnail, 26)}<div><span class="ell" style="font-weight:600;font-size:13px">${esc(s.title)}</span><span class="mini">${esc(s.market)} audience</span></div></div>
    ${s.group_status === 'down' ? chip('Held', 'warn') : s.status === 'planned' ? `<button class="btn sm" data-act="post-now" data-id="${s.channel_id}">Post now</button>` : chip('Posting', 'info')}</div>`).join('');
  return `<section class="card" style="height:100%">${hd('Up next', 'Natural times inside peak hours', '<a href="#/schedule" style="font-size:12px;font-weight:600">Schedule</a>')}${rows || '<div class="empty">Nothing planned yet.</div>'}</section>`;
}
function feedCard(d) {
  const col = { ok: '#1F9D55', info: '#4F46E5', warn: '#C27A12', error: '#B42318' };
  const rows = (d.feed || []).slice(0, 9).map(f => `<div><span class="fdot" style="background:${col[f.level] || '#8E94A0'}"></span><div style="min-width:0"><span style="display:block;line-height:1.35">${esc(f.message)}</span>${f.channel && !f.message.includes(f.channel) ? `<span class="mini">${esc(f.channel)}</span>` : ''}</div><span class="mini mono" style="white-space:nowrap">${esc(rel(f.at))}</span></div>`).join('');
  return `<section class="card" style="height:100%">${hd('Activity', 'What the server did, newest first')}<div class="feed">${rows || '<div class="empty">Nothing yet.</div>'}</div></section>`;
}
function attentionCard(d) {
  const FIX = { ai_key: 'Add key', replace_proxy: 'Replace proxy', confirm_ip: 'Review IP', replace_cookies: 'Replace cookies', add_cookies: 'Add cookies', edit_source: 'Fix source', check_source: 'Open', open_channel: 'Open', reconnect_channel: 'Reconnect', connect_drive: 'Connect Drive', connect_facebook: 'Connect', reconnect_facebook: 'Reconnect', fb_app: 'Settings', resolve_upload: 'Check', update_app: 'Update', test_group: 'Test proxy', update_downloader: 'Update', connect_ig_dest: 'Reconnect', ig_app: 'Settings', source_cookies: 'Open' };
  const rows = d.attention.map((a, i) => `<div class="attn ${a.fix ? 'go' : ''}" ${a.fix ? `data-act="fixit" data-i="${i}" role="button" tabindex="0" title="Open where this is fixed"` : ''} style="display:flex;gap:10px;padding:9px 0;border-top:1px solid var(--line)"><span class="chip ${a.level === 'error' ? 'bad' : 'warn'}" style="width:28px;height:28px;padding:0;justify-content:center;border-radius:8px;flex-shrink:0">${ic('alert', 14)}</span>
    <div style="display:flex;flex-direction:column;gap:2px;flex:1;min-width:0"><strong style="font-size:13px">${esc(a.title)}</strong>${a.detail ? `<span class="mini">${esc(a.detail)}</span>` : ''}${a.at ? `<span class="mini mono">${esc(rel(a.at))}${a.has_detail ? ` · <button class="linkbtn" data-act="evdetail" data-id="${a.ref}">details</button>` : ''}</span>` : ''}</div>
    <span class="actions" style="flex-shrink:0">${a.fix ? `<button class="btn sm primary" data-act="fixit" data-i="${i}">${FIX[a.fix] || 'Fix'}</button>` : ''}${a.action === 'event' ? `<button class="btn sm" data-act="dismiss" data-id="${a.ref}">Dismiss</button>` : ''}</span></div>`).join('');
  S.attention = d.attention;
  return `<section class="card" style="height:100%">${hd('Needs you', null, d.attention.length ? chip(d.attention.length + ' open', 'coralc') : chip('All clear', 'ok'))}${rows || `<div class="empty" style="padding:16px"><span style="color:var(--ok)">${ic('check', 22)}</span><div style="margin-top:6px">Nothing needs you right now.</div></div>`}</section>`;
}
function runFix(a) {
  const ref = a.action === 'event' ? null : a.ref;
  const ch = a.channel_id || ref;
  switch (a.fix) {
    case 'replace_proxy': case 'confirm_ip': S.focusGroup = ref; S.openGroup = ref; return go('groups');
    case 'test_group': S.focusGroup = ref; S.testGroup = ref; return go('groups');
    case 'replace_cookies': case 'add_cookies': if (ref) { S.focusGroup = ref; S.openCookies = ref; } return go('groups');
    case 'reconnect_channel': if (ch) { S.openReauth = +ch; return go(`channel/${ch}`); } return go('channels');
    case 'edit_source': case 'check_source': case 'open_channel': case 'source_cookies': return go(ch ? `channel/${ch}` : 'channels');
    case 'connect_drive': return go('drive');
    case 'connect_facebook': case 'reconnect_facebook': S.focusGroup = ref; S.openFbConnect = ref; return go('groups');
    case 'connect_ig_dest': S.focusGroup = ref; if (ref) S.openIgLogin = ref; return go('groups');
    case 'fb_app': case 'ig_app': return go('settings');
    case 'ai_key': return go('ai');
    case 'resolve_upload': return go('videos');
    case 'update_app': case 'update_downloader': return go('server');
    default: return ch ? go(`channel/${ch}`) : go('dashboard');
  }
}
async function eventDetail(id) {
  const { event: e } = await api('GET', `/api/events/${id}`);
  modal(`<h2>${esc(e.info ? e.info.title : 'Details')}</h2><p class="muted" style="margin:0">${esc(e.message)}</p>${e.info && e.info.message !== e.message ? `<p style="margin:0">${esc(e.info.message)}</p>` : ''}
    <details><summary class="muted" style="cursor:pointer;font-size:13px">Technical details (for support)</summary><div class="mono" style="font-size:11px;white-space:pre-wrap;word-break:break-all;background:var(--paper);padding:10px;border-radius:10px;margin-top:8px;max-height:260px;overflow:auto">${esc((e.code ? e.code + '\n' : '') + (e.detail || 'No more details.'))}</div></details>
    <div class="actions" style="justify-content:flex-end"><button class="btn" data-act="close">Close</button></div>`);
}

// ------------------------------------------------------------------ content source editor (wizard + client page)
const CT = { videos: ['Videos only', 'Reels and video posts'], mix: ['Videos + photos', 'Photos and carousels go to Facebook / Instagram'], photos: ['Photos only', 'Photos and carousels only · reels skipped'] };
const SRC = { drive: ['folder', 'Drive folder', 'Your own edits + client Inbox', '#EFEDE7', '#3F434B'], instagram: ['ig', 'Instagram', 'Client’s posts · test-account cookies', '#FDEBE6', '#B83A1F'], youtube: ['yt', 'YouTube', 'Client’s channel link · no sign-in', '#FDE7E7', '#B42318'], tiktok: ['tt', 'TikTok', 'Client’s profile link · no sign-in', '#E3F4F3', '#0F766E'] };
const DST = { youtube: ['yt', 'YouTube', '#FDE7E7', '#B42318'], facebook: ['fb', 'Facebook', '#E7EEFB', '#1D4FA8'], instagram: ['ig', 'Instagram', '#FDEBE6', '#B83A1F'] };
const ORIGIN = { instagram: 'Instagram', youtube: 'YouTube', tiktok: 'TikTok', inbox: 'Client Inbox', drive: 'Your Drive' };
const ORIGIN_IC = { instagram: ['ig', '#FDEBE6', '#B83A1F'], youtube: ['yt', '#FDE7E7', '#B42318'], tiktok: ['tt', '#E3F4F3', '#0F766E'], inbox: ['inbox', '#EEF0FF', '#4338CA'], drive: ['folder', '#EFEDE7', '#3F434B'] };
// where a post comes from: downloaded from Instagram/YouTube/TikTok, uploaded by the client (Inbox), or your own Drive file
function originChip(o, dark) {
  const m = ORIGIN_IC[o] || ORIGIN_IC.drive;
  return `<span style="display:inline-flex;align-items:center;gap:5px;padding:2px 8px 2px 4px;border-radius:99px;font-size:11.5px;font-weight:600;background:${dark ? '#1C222C' : m[1]};color:${dark ? '#fff' : m[2]};white-space:nowrap"><span style="display:inline-flex;width:16px;height:16px;border-radius:5px;align-items:center;justify-content:center;background:${m[1]};color:${m[2]}">${ic(m[0], 11)}</span>${ORIGIN[o] || 'Your Drive'}</span>`;
}
function srcIcon(k, s = 16) { const m = SRC[k]; return `<span class="ri" style="background:${m[3]};color:${m[4]}">${ic(m[0], s)}</span>`; }
function dstIcon(k, s = 16) { const m = DST[k]; return `<span class="ri" style="background:${m[2]};color:${m[3]}">${ic(m[0], s)}</span>`; }
function newSrc(prev) { return Object.assign({ drive: true, ig: false, yt: false, tt: false, handle: '', profile: null, ctype: 'videos', include_old: true, cap: 6, ytLink: '', ttLink: '', encore: true, inbox: true, checking: false, err: null }, prev || {}); }
// Instagram part of a source (profile check, content type, older posts)
function sourceEditor(X, group, opts = {}) {
  const pick = (act, val, on, t, d) => `<button type="button" class="pick ${on ? 'sel' : ''}" data-act="${act}" data-v="${val}"><b style="font-size:14px">${t}</b><span class="muted" style="font-size:12px">${d}</span></button>`;
  const acc = group && group.instagram;
  const accLine = !group ? '' : !acc ? `<div class="note warn">${ic('alert', 16)}<span>Pool ${group.number} has no Instagram account yet. <button type="button" class="btn sm" data-act="cookies" data-id="${group.id}">${ic('cookie', 14)}Add cookies</button></span></div>`
    : acc.status === 'needs_cookies' ? `<div class="note bad">${ic('alert', 16)}<span>Pool ${group.number}’s Instagram sign-in expired. <button type="button" class="btn sm" data-act="cookies" data-id="${group.id}">Replace cookies</button></span></div>`
      : `<span class="help">Read with Pool ${group.number}’s test account @${esc(acc.username || '')}${acc.proxy_mode === 'phone' ? ' · through the phone’s own internet' : ' · through the pool IP'}.</span>`;
  const prof = X.profile ? `<div style="display:flex;align-items:center;gap:12px;padding:12px;border-radius:12px;background:var(--paper);flex-wrap:wrap">${avatar(X.profile.username, hashI(X.profile.username), X.profile.picture, 44)}<div style="flex:1 1 200px;display:flex;flex-direction:column;gap:2px"><b>@${esc(X.profile.username)}</b><span class="muted" style="font-size:12px">${esc(X.profile.full_name || '')}${X.profile.posts != null ? ` · ${num(X.profile.posts)} posts` : ''}${X.profile.is_private ? ' · private' : ' · public'}</span></div>${X.profile.unchecked ? chip('Will be checked', 'neutral') : X.profile.is_private && !X.profile.followed ? chip('Private — follow it from the test account', 'warn') : chip('Profile found' + (X.profile.via ? ' · ' + X.profile.via : ''), 'ok')}</div>` : '';
  const igPart = `<div style="display:flex;flex-direction:column;gap:14px">
    <label class="f">Client’s Instagram link or @handle<div style="display:flex;gap:8px;flex-wrap:wrap"><input class="i" id="srch" style="flex:1 1 240px" placeholder="instagram.com/handle or @handle" value="${esc(X.handle)}" autocomplete="off"><button type="button" class="btn primary" data-act="srccheck" ${acc && acc.status !== 'needs_cookies' ? '' : 'disabled'}>${X.checking ? '<span class="spinner"></span>Checking…' : 'Check profile'}</button></div></label>
    ${accLine}${X.err ? `<div class="note bad">${ic('alert', 16)}<span>${esc(X.err)}${X.handle ? ` <button type="button" class="btn sm" data-act="srcskip">Continue without the check</button>` : ''}${acc && acc.proxy_mode !== 'phone' ? ` <button type="button" class="btn sm" data-act="srcphone">Read through the phone’s internet</button>` : ''}</span></div>` : ''}${prof}
    <div><b style="font-size:13px">What should we take?</b><div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin-top:8px">${Object.entries(CT).map(([v, [t, d]]) => pick('srctype', v, X.ctype === v, t, d)).join('')}</div></div>
    ${oldPosts(X)}</div>`;
  const toggles = opts.toggles === false ? '' : `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-top:16px">
    ${toggle('srcenc', X.encore, 'Encore', 'When everything is posted: best posts again · 60-day cooldown · max 3× · 1 a day')}
    ${toggle('srcinbox', X.inbox, 'Client Inbox folder', 'The client can upload to Drive himself; files are cleaned before they post')}</div>`;
  return igPart + toggles;
}
function oldPosts(X, word = 'posts') {
  return `<div style="margin-top:14px;display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px">
      <div style="display:flex;flex-direction:column;gap:8px"><b style="font-size:13px">Older ${word}</b><div class="actions"><button type="button" class="btn sm ${X.include_old ? 'primary' : ''}" data-act="srcold" data-v="1">Include older</button><button type="button" class="btn sm ${X.include_old ? '' : 'primary'}" data-act="srcold" data-v="0">Only new from today</button></div><span class="help">${X.include_old ? 'Newest first, about a week always kept ready.' : 'Nothing from before today is downloaded.'}</span></div>
      <div style="display:flex;flex-direction:column;gap:8px"><b style="font-size:13px">Older ${word} per day</b><div class="stepper"><button type="button" data-act="srccap" data-d="-1" aria-label="Fewer">−</button><strong>${X.cap}</strong><button type="button" data-act="srccap" data-d="1" aria-label="More">+</button></div><span class="help">New ones are always taken right away.</span></div></div>`;
}
function toggle(act, on, title, help) {
  return `<button type="button" class="tgl ${on ? 'on' : ''}" data-act="${act}" aria-pressed="${on}"><span class="sw"><i></i></span><span style="display:flex;flex-direction:column;gap:2px;text-align:left"><b style="font-size:13px">${title}</b><span class="muted" style="font-size:12px">${help}</span></span></button>`;
}
function srcActs(X, redraw, getGroup) {
  const keep = () => { const a = $('#ytl'), b = $('#ttl'); if (a) X.ytLink = a.value; if (b) X.ttLink = b.value; };
  return {
    srcon: d => { keep(); X[d.v] = !X[d.v]; redraw(); },
    srctype: d => { keep(); X.ctype = d.v; redraw(); },
    srcold: d => { keep(); X.include_old = d.v === '1'; redraw(); },
    srccap: d => { keep(); X.cap = Math.max(1, Math.min(30, X.cap + +d.d)); redraw(); },
    srcenc: () => { keep(); X.encore = !X.encore; redraw(); },
    srcinbox: () => { keep(); X.inbox = !X.inbox; redraw(); },
    srcskip: () => { keep(); const h = String(X.handle || '').trim().replace(/^.*instagram\.com\//, '').replace(/[/?#].*$/, '').replace(/^@/, ''); if (!h) return; X.profile = { username: h, full_name: 'Not checked yet — checked automatically', picture: null, id: null, is_private: false, unchecked: true }; X.handle = h; X.err = null; redraw(); },
    srcphone: async () => { keep(); const g = getGroup(); if (!g) return; await api('POST', `/api/groups/${g.id}/instagram/proxy`, { proxy_mode: 'phone' }); if (g.instagram) g.instagram.proxy_mode = 'phone'; X.err = null; toast('Instagram is now read through the phone’s own internet'); redraw(); },
    srccheck: async () => {
      keep();
      const h = ($('#srch') || {}).value || X.handle; X.handle = h; X.err = null; X.profile = null; X.checking = true; redraw();
      try {
        const p = (await api('POST', '/api/ig/lookup', { group_id: getGroup().id, handle: h })).profile;
        X.handle = p.username;
        X.profile = p.pending ? { ...p, unchecked: true, full_name: p.maybe_typo ? 'Could not confirm this profile — check the spelling' : 'Instagram is busy — confirmed automatically in a few minutes' } : p;
      } catch (e) { X.err = e.message; }
      X.checking = false; redraw();
    },
    cookies: d => cookiesModal(+d.id, async () => { const g = (await api('GET', '/api/groups')).groups; if (W.groups) W.groups = g; const gg = g.find(x => x.id === +d.id); if (getGroup() && gg) Object.assign(getGroup(), gg); redraw(); }),
  };
}
document.addEventListener('input', e => {
  const X = S.srcX; if (!X) return;
  if (e.target.id === 'srch') { X.handle = e.target.value; X.profile = null; }
  if (e.target.id === 'ytl') X.ytLink = e.target.value;
  if (e.target.id === 'ttl') X.ttLink = e.target.value;
  if (W.step === 2 && X === W.src) { const n = document.querySelector('[data-act=wnext]'); if (n) n.disabled = !srcReady(); }
});
function srcPayload(X) {
  if (!X.ig) return null;
  return { handle: X.handle, content_type: X.ctype, include_old: X.include_old, daily_cap: X.cap, ig_user_id: X.profile && X.profile.id, full_name: X.profile && !X.profile.unchecked ? X.profile.full_name : null, picture: X.profile && X.profile.picture };
}
function vidSources(X) {
  const out = [];
  if (X.yt && X.ytLink.trim()) out.push({ kind: 'youtube', link: X.ytLink.trim(), include_old: X.include_old, daily_cap: X.cap });
  if (X.tt && X.ttLink.trim()) out.push({ kind: 'tiktok', link: X.ttLink.trim(), include_old: X.include_old, daily_cap: X.cap });
  return out;
}

// ------------------------------------------------------------------ Instagram cookies (per Pool)
function cookiesModal(gid, done) {
  modal(`<h2>Instagram account for this pool</h2>
    <p class="muted" style="margin:0">Each Pool reads Instagram with its own test account, through its own IP. Paste that account’s cookies — we check they work before saving, and the old ones stay until the new ones pass.</p>
    <ol class="muted" style="font-size:13px;line-height:1.6;margin:4px 0 0;padding-left:18px"><li>In Chrome, sign in to instagram.com with the pool’s test account — ideally through the same proxy or country. <b>Do this in the same browser you use for this panel</b>: DriveCast then keeps using that browser’s identity for this account, so Instagram always sees one consistent device.</li><li>Install the extension <b>“Get cookies.txt LOCALLY”</b>, open instagram.com, click it, then <b>Export</b> or <b>Copy</b>.</li><li>Paste below, or choose the downloaded file.</li></ol>
    <div id="vpick"></div>
    <form data-form="savecookies" style="display:flex;flex-direction:column;gap:12px">
      <label class="f">Cookies<textarea class="i mono" name="cookies" id="cktext" rows="7" placeholder="# Netscape HTTP Cookie File&#10;.instagram.com  TRUE  /  TRUE  …  sessionid  …" style="height:auto;padding:10px;font-size:12px"></textarea><span class="help">cookies.txt, a JSON export, or “sessionid=…; ds_user_id=…; csrftoken=…” all work.</span></label>
      <label class="btn" style="width:fit-content;cursor:pointer">${ic('up', 16)}Choose cookies file<input type="file" id="ckfile" accept=".txt,.json" style="display:none"></label>
      <div style="display:flex;flex-direction:column;gap:6px"><b style="font-size:13px">Read Instagram through</b><span class="actions"><label class="btn sm" style="cursor:pointer"><input type="radio" name="proxy_mode" value="group" checked> Group IP</label><label class="btn sm" style="cursor:pointer"><input type="radio" name="proxy_mode" value="phone"> Phone’s own internet</label></span><span class="help">Use the phone’s internet if the cookies come from a phone or home Wi-Fi and the pool proxy is a datacenter IP. Reading only — posting always uses the pool IP.</span></div>
      <div id="ckres"></div>
      <div class="actions" style="justify-content:flex-end"><button class="btn" type="button" data-act="close">Cancel</button><button class="btn primary" type="submit">Test and save</button></div></form>`);
  api('GET', '/api/ig/vault').then(r => {
    const free = r.vault.filter(v => (!v.group_id || v.group_id === gid) && v.status !== 'bad');
    const box = $('#vpick'); if (!box || !free.length) return;
    box.innerHTML = `<div style="border:1px solid var(--line);border-radius:12px;padding:12px;display:flex;flex-direction:column;gap:8px"><b style="font-size:13px">Pick a prepared test account</b>
      <div style="display:flex;gap:8px;flex-wrap:wrap">${free.map(v => `<button type="button" class="btn sm" data-act="vuse" data-id="${v.id}">${ic('ig', 14)}${v.username ? '@' + esc(v.username) : esc(v.label || 'Account ' + v.id)}</button>`).join('')}</div><span class="help">Or paste new cookies below.</span></div>`;
  }).catch(() => { });
  S.acts.vuse = async d => {
    const out = $('#ckres'); out.innerHTML = '<div class="note info"><span class="spinner"></span><span>Connecting the prepared account…</span></div>';
    const pm = (document.querySelector('[name=proxy_mode]:checked') || {}).value || 'group';
    try { const r = await api('POST', `/api/groups/${gid}/instagram/from-vault`, { vault_id: +d.id, proxy_mode: pm }); closeModal(); toast(r.verified ? `Instagram connected · @${r.username}` : 'Account saved — checked again on first use', !r.verified); if (done) done(); }
    catch (e) { out.innerHTML = `<div class="note bad">${ic('alert', 16)}<span>${esc(e.message)}</span></div>`; }
  };
  S.acts.savecookies = async v => {
    const out = $('#ckres'); out.innerHTML = '<div class="note info"><span class="spinner"></span><span>Signing in to Instagram through the pool’s proxy…</span></div>';
    try {
      const r = await api('POST', `/api/groups/${gid}/instagram`, { cookies: v.cookies, user_agent: navigator.userAgent, proxy_mode: v.proxy_mode || 'group' });
      closeModal();
      toast(r.verified ? `Instagram connected · @${r.username}` : `Cookies saved · ${r.note || 'Instagram did not answer clearly'} — checked again on first use`, !r.verified);
      if (done) done();
    }
    catch (e) { out.innerHTML = `<div class="note bad">${ic('alert', 16)}<span>${esc(e.message)}</span></div>`; }
  };
}
document.addEventListener('change', async e => {
  if (e.target.dataset && e.target.dataset.igassign) {
    try { await api('POST', `/api/instagram/destinations/${e.target.dataset.igassign}/assign`, { channel_id: e.target.value ? +e.target.value : null }); toast(e.target.value ? 'Instagram linked' : 'Instagram unlinked'); render(); }
    catch (err) { toast(err.message, true); render(); }
    return;
  }
  if (e.target.dataset && e.target.dataset.assign) {
    try { await api('POST', `/api/facebook/pages/${e.target.dataset.assign}/assign`, { channel_id: e.target.value ? +e.target.value : null }); toast(e.target.value ? 'Page linked' : 'Page unlinked'); render(); }
    catch (err) { toast(err.message, true); }
    return;
  }
  if (e.target.id !== 'ckfile' || !e.target.files[0]) return;
  const r = new FileReader(); r.onload = () => { const t = $('#cktext'); if (t) t.value = r.result; }; r.readAsText(e.target.files[0]);
});

// ------------------------------------------------------------------ add a client (route wizard)
const W = {};
async function pageAdd(preselect) {
  Object.assign(W, { step: 0, group: null, mode: null, name: '', link: null, channel: null, folder: null, src: newSrc(), dest: { youtube: true, fb: null, ig: null }, plan: { posts_per_day: 2, audience: 'US+UK', min_gap_min: 240, jitter_min: 30 }, poll: null });
  const [g, ch, dr, st, fb, igd] = await Promise.all([api('GET', '/api/groups'), api('GET', '/api/channels'), api('GET', '/api/drive'), api('GET', '/api/settings'),
    api('GET', '/api/facebook').catch(() => ({ groups: [], app_ready: false })), api('GET', '/api/instagram/destinations').catch(() => ({ destinations: [] }))]);
  W.groups = g.groups; W.drive = dr; W.pending = ch.channels.filter(c => c.state === 'setup'); W.settings = st; W.fb = fb; W.igd = igd.destinations;
  if (preselect) {
    const c = ch.channels.find(x => String(x.id) === String(preselect));
    if (c) { W.channel = c; W.mode = c.platform === 'youtube' ? 'yt' : 'none'; W.group = W.groups.find(x => x.id === c.group_id) || null; W.step = W.group ? 2 : 0; }
  }
  drawWizard();
}
function wDests() {
  const out = [];
  if (W.channel && W.channel.platform === 'youtube' && W.dest.youtube) out.push('YouTube');
  if (W.dest.fb) out.push('Facebook');
  if (W.dest.ig) out.push('Instagram');
  return out;
}
function drawWizard() {
  const meta = [['Pool', 'One IP = one identity'], ['Client', 'YouTube sign-in, or just a name'], ['Sources', 'Where posts come from'], ['Destinations', 'Where they go, and when'], ['Review', 'Start publishing']];
  const can = i => i === 0 || (i === 1 && W.group) || (i === 2 && W.channel) || (i === 3 && W.channel && srcReady()) || (i === 4 && W.channel && srcReady() && wDests().length);
  const steps = meta.map((m, i) => `<button class="step ${i === W.step ? 'on' : ''} ${i < W.step ? 'done' : ''}" data-act="wstep" data-i="${i}" ${can(i) ? '' : 'disabled'}><span class="n">${i < W.step ? '✓' : i + 1}</span><span style="display:flex;flex-direction:column;gap:3px"><b style="font-size:14px">${m[0]}</b><span class="muted" style="font-size:12px">${m[1]}</span></span></button>`).join('');
  S.srcX = W.src;
  const body = [wGroup, wClient, wSource, wDest, wReview][W.step]();
  const node = (k, v) => `<div class="node ${v ? 'on' : 'off'}"><span class="kicker">${k}</span><b>${esc(v || 'Not set')}</b></div>`;
  const route = `<section class="routebar"><span class="kicker" style="margin-right:6px">Route</span>${node('Sources', srcLabel())}<span style="color:#4B5260">→</span>${node('Server · Pool', W.group && `Pool ${W.group.number}`)}<span style="color:#4B5260">→</span>${node('Destinations', wDests().join(' + '))}</section>`;
  mount('channels', 'Add a client', 'Pool → client → sources → destinations', 'New route', '<a class="btn" href="#/channels">Cancel</a>',
    `<div class="row" style="align-items:flex-start"><div style="flex:1 1 260px"><div class="steps">${steps}<div class="muted" style="margin:8px 12px 6px;padding:12px;border-radius:12px;background:var(--paper);font-size:12px;line-height:1.5">Proxy comes first. Everything this client posts leaves from that one IP — YouTube, Facebook and Instagram alike.</div></div></div>
     <div style="flex:3 1 560px"><section class="card" style="padding:28px">${body}</section></div></div>${route}`);
  Object.assign(S.acts, WIZ_ACTS, srcActs(W.src, drawWizard, () => W.group));
  if (W.step === 1 && W.link && !W.channel) startPoll();
}
const wHead = (n, t, s) => `<div style="margin-bottom:20px"><div class="kick mono muted" style="font-size:11px;letter-spacing:.14em">STEP ${n} OF 5</div><h2 style="font-size:26px;margin-top:6px">${t}</h2><p class="muted" style="margin:6px 0 0">${s}</p></div>`;
const wNav = (nextOk, label = 'Continue') => `<div style="display:flex;justify-content:space-between;gap:12px;margin-top:26px;padding-top:18px;border-top:1px solid var(--line)"><button class="btn" data-act="wback" ${W.step === 0 ? 'disabled' : ''}>${ic('back', 16)}Back</button><button class="btn primary" data-act="wnext" ${nextOk ? '' : 'disabled'}>${label}${ic('arrow', 16)}</button></div>`;
function wGroup() {
  const cards = W.groups.map(g => {
    const pct = Math.min(100, Math.round(100 * g.channel_count / g.max_channels));
    const note = g.status === 'down' ? ['Not responding', 'var(--bad)'] : g.channel_count >= g.max_channels ? [`Full — above ${g.max_channels} is not recommended`, 'var(--warn)'] : [`Healthy · ${g.latency_ms ?? '—'} ms`, 'var(--ok)'];
    return `<button class="pick ${W.group && W.group.id === g.id ? 'sel' : ''}" data-act="wgroup" data-id="${g.id}" ${g.status === 'down' ? 'disabled' : ''}>
      <span style="display:flex;justify-content:space-between;width:100%"><strong style="font-family:var(--disp);font-size:17px">Pool ${g.number}</strong><span class="mono" style="font-size:12px">${g.channel_count}/${g.max_channels}</span></span>
      ${g.label ? `<span style="font-size:12.5px;font-weight:600">${esc(g.label)}</span>` : ''}<span class="mono muted" style="font-size:12px">${esc(g.outgoing_ip || g.host)}${g.readiness ? ` · ready ${g.readiness.score}/${g.readiness.of}` : ''}${g.apps && g.apps.google ? ' · own Google app' : ''}</span><span class="meter" style="width:100%"><span style="width:${pct}%;background:${g.channel_count > g.max_channels ? '#C27A12' : '#0E1116'}"></span></span>
      <span style="font-size:12px;font-weight:600;color:${note[1]}">${esc(note[0])}</span></button>`;
  }).join('');
  return wHead(1, 'Choose a pool', 'Every post for this client leaves from this pool’s static IP — never from the phone.') +
    (W.groups.length ? `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px">${cards}</div>` : '<div class="empty"><h3>No Pools yet</h3>Add your first SOCKS5 proxy below.</div>') +
    `<details style="margin-top:18px" ${W.groups.length ? '' : 'open'}><summary style="cursor:pointer;font-weight:650">+ New Pool</summary>${groupForm()}</details>` +
    (W.group ? `<div class="note ok" style="margin-top:16px">${ic('shield', 18)}<span><b>Ready.</b> Outgoing IP ${esc(W.group.outgoing_ip)} · checked ${esc(rel(W.group.last_check))} · DNS through proxy</span></div>` : '') + wNav(!!W.group);
}
function wClient() {
  if (W.channel) return wHead(2, W.channel.platform === 'youtube' ? 'YouTube channel connected' : 'Client created', 'Confirm this is the right client.') + channelCard(W.channel) + wNav(true);
  const pick = (v, t, d, i) => `<button class="pick ${W.mode === v ? 'sel' : ''}" data-act="wmode" data-v="${v}" style="flex-direction:row;align-items:center;gap:12px">${i}<span style="display:flex;flex-direction:column;gap:3px;text-align:left"><b style="font-size:14px">${t}</b><span class="muted" style="font-size:12px">${d}</span></span></button>`;
  const modes = `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px;margin-bottom:18px">${pick('yt', 'Posts to a YouTube channel', 'The owner presses Allow once (Google)', dstIcon('youtube', 18))}${pick('none', 'No YouTube', 'Facebook and/or Instagram only — just give the client a name', dstIcon('instagram', 18))}</div>`;
  if (W.mode === 'none') return wHead(2, 'Name this client', 'Use the client’s or brand’s name — it names the Drive folder too.') + modes +
    `<form data-form="wname" style="display:flex;gap:10px;flex-wrap:wrap"><input class="i" name="title" style="flex:1 1 240px" placeholder="e.g. Art by Zara" value="${esc(W.name)}" required autocomplete="off"><button class="btn primary" type="submit">Create client</button></form>` + wNav(false);
  if (W.mode !== 'yt') return wHead(2, 'Who is this client?', 'A client can post to YouTube, Facebook and Instagram — or any one of them.') + modes + wNav(false);
  const pending = W.pending.filter(c => c.group_id === (W.group && W.group.id));
  const pend = pending.length ? `<div style="margin-top:18px"><b style="font-size:13px">Or finish one that is already authorized</b>${pending.map(c => `<div style="display:flex;align-items:center;gap:12px;padding:10px 0;border-top:1px solid var(--line)">${avatar(c.title, hashI(c.title), c.thumbnail)}<div style="flex:1"><b>${esc(c.title)}</b><div>${handle(c.handle)}</div></div><button class="btn sm" data-act="wuse" data-id="${c.id}">Use this</button></div>`).join('')}</div>` : '';
  const box = W.link ? `<div class="linkbox"><span class="kicker">One-time authorization link</span><div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap"><span class="mono" style="font-size:14px;flex:1 1 280px;word-break:break-all">${esc(W.link.url)}</span><button class="btn coral" data-act="copy" data-text="${esc(W.link.url)}">${ic('copy', 16)}Copy link</button><a class="btn" href="${esc(W.link.url)}" target="_blank" rel="noopener">Open here</a></div><span style="font-size:12px;color:#8E94A0">Single use · expires ${esc(rel(W.link.expires_at))} · tied to Pool ${W.group.number}</span></div>
      <div style="display:flex;align-items:center;gap:14px;margin-top:16px;padding:16px;border:1px dashed #C9C6BD;border-radius:14px"><span class="pulse"></span><div style="flex:1"><b>Waiting for Google authorization…</b><p class="muted" style="margin:3px 0 0;font-size:13px">This updates by itself when the channel owner presses Allow.</p></div></div>`
    : `<button class="btn primary big" data-act="wlink">${ic('key', 16)}Create sign-in link</button>`;
  return wHead(2, 'Send the YouTube sign-in link', 'Open it on any device. The channel owner signs in with Google and presses Allow.') + modes + box + pend + wNav(false, 'Waiting for Allow');
}
function channelCard(c) {
  const yt = c.platform === 'youtube';
  return `<div style="display:flex;align-items:center;gap:14px;padding:16px;border:1px solid var(--line);border-radius:14px">${avatar(c.title, hashI(c.title), c.thumbnail, 48)}<div style="flex:1;display:flex;flex-direction:column;gap:3px"><b style="font-size:16px">${esc(c.title)}</b><span class="mono muted" style="font-size:12px">${yt ? `${esc(c.handle || '')} · ${esc(c.external_id)}` : 'No YouTube · Facebook / Instagram route'}</span>${yt ? `<span class="muted" style="font-size:12px">${num(c.subscribers)} subscribers</span>` : ''}</div>${chip(yt ? 'Authorized' : 'Ready', 'ok')}</div>`;
}
function srcReady() {
  const X = W.src;
  const any = X.drive || X.ig || X.yt || X.tt;
  if (!any) return false;
  if (X.drive && !W.folder) return false;
  if (X.ig && !(X.profile && !(X.profile.is_private && !X.profile.followed))) return false;
  if (X.yt && !X.ytLink.trim()) return false;
  if (X.tt && !X.ttLink.trim()) return false;
  return true;
}
function srcLabel() {
  const X = W.src;
  if (W.step < 2) return null;
  const parts = [];
  if (X.drive && W.folder) parts.push(W.folder.name);
  if (X.ig && X.profile) parts.push('IG @' + X.profile.username);
  if (X.yt && X.ytLink) parts.push('YouTube');
  if (X.tt && X.ttLink) parts.push('TikTok');
  return parts.join(' + ') || null;
}
function wSource() {
  const dr = W.drive;
  if (!dr.connected || !dr.root_id) return wHead(3, 'Sources', 'Google Drive is not connected yet.') + `<div class="empty"><h3>Connect Google Drive first</h3>Every client gets a folder in your main Drive folder — every download lands there first. Authorize Drive and paste your main folder link, then come back.<div style="margin-top:14px"><a class="btn primary" href="#/drive">Open Google Drive</a></div></div>` + wNav(false);
  const X = W.src;
  const card = (k, on) => `<button type="button" class="srccard ${on ? 'sel' : ''}" data-act="srcon" data-v="${k === 'drive' ? 'drive' : k === 'instagram' ? 'ig' : k === 'youtube' ? 'yt' : 'tt'}"><span class="top2">${srcIcon(k)}<b style="font-size:14px;flex:1">${SRC[k][1]}</b><span class="sw-mini">${on ? chip('On', 'ok', false) : chip('Off', 'neutral', false)}</span></span><span class="muted" style="font-size:12px">${SRC[k][2]}</span></button>`;
  const panel = (k, body) => `<div style="margin-top:14px;padding:16px;border:1px solid var(--line);border-radius:14px"><div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">${srcIcon(k, 14)}<b>${SRC[k][1]}</b></div>${body}</div>`;
  const name = (X.ig && X.profile && X.profile.username) || (W.channel && W.channel.title) || 'the client';
  let panels = '';
  if (X.drive) panels += panel('drive', wFolderList());
  else panels += `<div class="note info" style="margin-top:14px">${ic('folder', 16)}<span>A folder <b>${esc(name)}</b> is created in “${esc(dr.root_name)}” with an Inbox inside, and on the phone (hidden from the Gallery).</span></div>`;
  if (X.ig) panels += panel('instagram', sourceEditor(X, W.group, { toggles: false }));
  if (X.yt) panels += panel('youtube', `<label class="f">Client’s YouTube channel link<input class="i" id="ytl" placeholder="https://www.youtube.com/@channel" value="${esc(X.ytLink)}" autocomplete="off"><span class="help">New uploads first (checked every 40–80 min), then older videos newest first. 1080p with sound, H.264 so Facebook and Instagram accept it. Read through the phone’s own internet — no client sign-in.</span></label>${X.ig ? '' : oldPosts(X, 'videos')}`);
  if (X.tt) panels += panel('tiktok', `<label class="f">Client’s TikTok profile link<input class="i" id="ttl" placeholder="https://www.tiktok.com/@name" value="${esc(X.ttLink)}" autocomplete="off"><span class="help">Public profiles need no login. Without the watermark. If TikTok ever asks for sign-in, add a test account’s cookies on the client page — or paste video links by hand.</span></label>${X.ig || X.yt ? '' : oldPosts(X, 'videos')}`);
  const toggles = `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-top:16px">${toggle('srcenc', X.encore, 'Encore', 'When nothing new is left: best posts again · 60-day cooldown · max 3×')}${toggle('srcinbox', X.inbox, 'Client Inbox folder', 'The client can drop files in Drive himself; cleaned before posting')}</div>`;
  return wHead(3, 'Where do this client’s posts come from?', 'Pick any mix. One ledger for all of them: nothing is missed, nothing posts twice, nothing mixes between clients.') +
    `<div class="srcgrid">${card('drive', X.drive)}${card('instagram', X.ig)}${card('youtube', X.yt)}${card('tiktok', X.tt)}</div>` + panels + toggles +
    `<div class="note ok" style="margin-top:16px">${ic('shield', 16)}<span><b>Order:</b> fresh posts → your Drive files → older posts (newest first) → Encore. Every file is cleaned and tagged before it leaves the phone.</span></div>` + wNav(srcReady());
}
function wFolderList() {
  const dr = W.drive;
  const q = (W.q || '').toLowerCase();
  const name = ((W.channel && W.channel.title) || '').toLowerCase().trim();
  const list = dr.folders.filter(f => !q || f.path.toLowerCase().includes(q)).sort((a, b) => (b.name.toLowerCase().trim() === name) - (a.name.toLowerCase().trim() === name)).slice(0, 60);
  const rows = list.map(f => `<button class="pick ${W.folder && W.folder.id === f.id ? 'sel' : ''}" data-act="wfolder" data-id="${esc(f.id)}" ${f.used ? 'disabled' : ''} style="flex-direction:row;align-items:center;gap:14px">${ic('folder', 20)}<span style="display:flex;flex-direction:column;gap:2px;flex:1"><b>${esc(f.name)}</b><span class="muted" style="font-size:12px">${esc(f.path)}${f.used ? ' · already used by another client' : ''}</span></span>${f.name.toLowerCase().trim() === name ? chip('Name match', 'info', false) : ''}</button>`).join('');
  return `<p class="muted" style="font-size:12px;margin:0 0 10px">Folders inside “${esc(dr.root_name)}”. Name matches are suggested, never auto-assigned.</p>
    <div style="display:flex;gap:10px;margin-bottom:14px"><input class="i" id="fq" placeholder="Search folders" value="${esc(W.q || '')}" aria-label="Search folders"><button class="btn" data-act="wrefresh">${ic('refresh', 16)}Refresh</button></div>
     <div style="display:flex;flex-direction:column;gap:8px;max-height:300px;overflow:auto">${rows || '<div class="empty">No folders found.</div>'}</div>`;
}
function wDest() {
  const g = W.group, p = W.plan;
  const fbG = (W.fb.groups || []).find(x => x.group_id === g.id) || { account: null, pages: [] };
  const pages = fbG.pages.filter(pg => pg.can_post && (!pg.channel || pg.channel.id === W.channel.id));
  const igs = (W.igd || []).filter(d => d.group_id === g.id && (!d.channel_id || d.channel_id === W.channel.id));
  const yt = W.channel.platform === 'youtube';
  const row = (k, title, body) => `<div style="display:flex;gap:14px;align-items:flex-start;padding:14px 0;border-top:1px solid var(--line)">${dstIcon(k, 18)}<div style="flex:1;min-width:0;display:flex;flex-direction:column;gap:8px"><b>${title}</b>${body}</div></div>`;
  const ytRow = yt ? toggle('wyt', W.dest.youtube, W.dest.youtube ? `On · ${esc(W.channel.title)}` : 'Off', 'Shorts and videos go to this YouTube channel') : '<span class="muted" style="font-size:13px">No YouTube for this client.</span>';
  const fbRow = !W.fb.app_ready ? `<span class="muted" style="font-size:13px">Add your Meta app in <a href="#/settings">Settings</a> first.</span>`
    : !fbG.account ? `<span class="muted" style="font-size:13px">Pool ${g.number} has no Facebook yet. <a href="#/groups">Connect Facebook on Pools</a> — one Allow brings every page.</span>`
      : `<select class="i" id="wfb"><option value="">— Don’t post to Facebook —</option>${pages.map(pg => `<option value="${pg.id}" ${W.dest.fb === pg.id ? 'selected' : ''}>${esc(pg.name)}</option>`).join('')}</select><span class="help">Videos, photos and carousels post to this page through the official API.</span>`;
  const igRow = igs.length ? `<select class="i" id="wig"><option value="">— Don’t post to Instagram —</option>${igs.map(d => `<option value="${d.id}" ${W.dest.ig === d.id ? 'selected' : ''}>@${esc(d.username || d.ig_user_id)} · via ${d.via === 'facebook' ? 'Facebook page' : 'Instagram login'}</option>`).join('')}</select><span class="help">Reels, photos and carousels · about 50 posts a day · Professional account.</span>`
    : `<span class="muted" style="font-size:13px">No Instagram account on Pool ${g.number} yet.</span><span class="actions">${fbG.account ? `<button type="button" class="btn sm" data-act="wigfind">${ic('search', 14)}Find via Facebook pages</button>` : ''}<button type="button" class="btn sm" data-act="wiglogin">${ic('ig', 14)}Connect with Instagram login</button></span>`;
  const sel = (id, label, opts, val) => `<label class="f">${label}<select class="i" id="${id}">${opts.map(([v, t]) => `<option value="${v}" ${String(v) === String(val) ? 'selected' : ''}>${t}</option>`).join('')}</select></label>`;
  const loop = W.src.ig && W.dest.ig ? `<div class="note info" style="margin-top:8px">${ic('repeat', 16)}<span><b>Loop guard:</b> a post read from an Instagram account is never posted back to that same account.</span></div>` : '';
  return wHead(4, 'Where do the posts go — and when?', 'Any mix. A problem on one destination never stops the others.') +
    row('youtube', 'YouTube', ytRow) + row('facebook', 'Facebook page', fbRow) + row('instagram', 'Instagram', igRow) + loop +
    `<div style="margin-top:18px;padding-top:18px;border-top:1px solid var(--line)"><b style="font-size:14px">Posting plan</b><div class="form" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-top:10px">
      <div style="display:flex;flex-direction:column;gap:8px"><b style="font-size:13px">Posts per day</b><div class="stepper"><button data-act="ppd" data-d="-1" aria-label="Fewer">−</button><strong>${p.posts_per_day}</strong><button data-act="ppd" data-d="1" aria-label="More">+</button></div></div>
      ${sel('aud', 'Audience country', [['US+UK', 'USA + UK'], ['US', 'USA'], ['UK', 'United Kingdom'], ['CA', 'Canada'], ['AU', 'Australia']], p.audience)}
      ${sel('gap', 'Minimum gap', [[120, '2 hours'], [180, '3 hours'], [240, '4 hours'], [360, '6 hours']], p.min_gap_min)}
      <label class="f">Natural timing · ±<span id="jv">${p.jitter_min}</span> min<input type="range" id="jit" min="0" max="60" step="5" value="${p.jitter_min}" style="height:44px"></label></div></div>` + wNav(wDests().length > 0);
}
function wReview() {
  const X = W.src;
  const srcs = [X.drive && W.folder ? `Drive: ${W.folder.path}` : null, X.ig && X.profile ? `Instagram @${X.profile.username} · ${CT[X.ctype][0]}` : null, X.yt ? `YouTube ${X.ytLink}` : null, X.tt ? `TikTok ${X.ttLink}` : null].filter(Boolean);
  const fbName = W.dest.fb ? ((W.fb.groups || []).flatMap(g => g.pages).find(p => p.id === W.dest.fb) || {}).name : null;
  const igName = W.dest.ig ? '@' + ((W.igd || []).find(d => d.id === W.dest.ig) || {}).username : null;
  const r = [['Pool', `Pool ${W.group.number} · ${W.group.outgoing_ip}`], ['Client', W.channel.title], ['Sources', srcs.join(' · ')],
    ['Older posts', (X.ig || X.yt || X.tt) ? (X.include_old ? `newest first, ${X.cap}/day` : 'new only') : '—'],
    ['Destinations', [W.channel.platform === 'youtube' && W.dest.youtube ? 'YouTube' : null, fbName ? `Facebook · ${fbName}` : null, igName ? `Instagram · ${igName}` : null].filter(Boolean).join(' · ')],
    ['Posts per day', W.plan.posts_per_day], ['Audience', W.plan.audience + ' · peak hours'], ['Timing', `${W.plan.min_gap_min / 60} h minimum gap · ±${W.plan.jitter_min} min natural`],
    ['Encore', X.encore ? 'On' : 'Off'], ['Client Inbox', X.inbox ? 'On' : 'Off'], ['Privacy', 'Every file cleaned + DriveCast tag · sent only from the pool IP']];
  return wHead(5, 'Review and go live', 'Nothing posts until you press Start. You can pause any time.') +
    `<div style="border:1px solid var(--line);border-radius:14px;padding:4px 18px">${r.map(([k, v], i) => `<div style="display:flex;justify-content:space-between;gap:16px;padding:13px 0;${i ? 'border-top:1px solid var(--line)' : ''}"><span class="muted">${esc(k)}</span><b style="text-align:right;word-break:break-word">${esc(v)}</b></div>`).join('')}</div>
     <button class="btn coral big" style="width:100%;margin-top:18px" data-act="wstart">Start publishing</button>
     <div style="margin-top:14px"><button class="btn" data-act="wback">${ic('back', 16)}Back</button></div>`;
}
function readPlanInputs() {
  const a = $('#aud'), g = $('#gap'), j = $('#jit'), f = $('#wfb'), i = $('#wig'), y = $('#ytl'), t = $('#ttl');
  if (a) W.plan.audience = a.value;
  if (g) W.plan.min_gap_min = +g.value;
  if (j) W.plan.jitter_min = +j.value;
  if (f) W.dest.fb = f.value ? +f.value : null;
  if (i) W.dest.ig = i.value ? +i.value : null;
  if (y) W.src.ytLink = y.value;
  if (t) W.src.ttLink = t.value;
}
function startPoll() {
  every(3000, async () => {
    if (!W.link || W.channel) return;
    try {
      const s = await api('GET', `/api/connect/${W.link.code}`);
      if (s.status === 'done' && s.channel) { W.channel = s.channel; drawWizard(); toast('Channel authorized'); }
      else if (s.status === 'error') { toast(s.error || 'Authorization failed', true); W.link = null; drawWizard(); }
      else if (s.status === 'expired') { W.link = null; drawWizard(); }
    } catch (e) { /* keep polling */ }
  });
}
const WIZ_ACTS = {
  wstep: d => { readPlanInputs(); W.step = +d.i; clearTimers(); drawWizard(); },
  wback: () => { readPlanInputs(); W.step = Math.max(0, W.step - 1); clearTimers(); drawWizard(); },
  wnext: () => { readPlanInputs(); W.step = Math.min(4, W.step + 1); clearTimers(); drawWizard(); },
  wgroup: d => { W.group = W.groups.find(g => g.id === +d.id); if (W.channel && W.channel.group_id !== W.group.id) { W.channel = null; W.link = null; } W.dest.fb = null; W.dest.ig = null; drawWizard(); },
  wmode: d => { W.mode = d.v; drawWizard(); },
  wname: async v => { W.name = v.title; const r = await api('POST', '/api/channels', { title: v.title, group_id: W.group.id }); W.channel = r.channel; W.step = 2; drawWizard(); },
  wlink: async () => { W.link = await api('POST', '/api/connect', { purpose: 'youtube', group_id: W.group.id }); drawWizard(); },
  wuse: d => { W.channel = W.pending.find(c => c.id === +d.id); W.step = 2; clearTimers(); drawWizard(); },
  wfolder: d => { readPlanInputs(); W.folder = W.drive.folders.find(f => f.id === d.id); drawWizard(); },
  wrefresh: async () => { readPlanInputs(); W.drive = await api('POST', '/api/drive/refresh'); drawWizard(); },
  wyt: () => { readPlanInputs(); W.dest.youtube = !W.dest.youtube; drawWizard(); },
  wigfind: async () => { readPlanInputs(); try { const r = await api('POST', `/api/instagram/destinations/discover/${W.group.id}`); W.igd = r.destinations; toast(r.found ? `${r.found} Instagram account(s) found` : 'No Instagram account is linked to these pages', !r.found); } catch (e) { toast(e.message, true); } drawWizard(); },
  wiglogin: () => { readPlanInputs(); connectIgLogin(W.group.id, async () => { W.igd = (await api('GET', '/api/instagram/destinations')).destinations; drawWizard(); }); },
  ppd: d => { readPlanInputs(); W.plan.posts_per_day = Math.max(1, Math.min(6, W.plan.posts_per_day + +d.d)); drawWizard(); },
  wstart: async () => {
    const X = W.src, c = W.channel;
    if (W.dest.fb) await api('POST', `/api/facebook/pages/${W.dest.fb}/assign`, { channel_id: c.id });
    if (W.dest.ig) await api('POST', `/api/instagram/destinations/${W.dest.ig}/assign`, { channel_id: c.id });
    await api('POST', `/api/channels/${c.id}/route`, { folder_id: X.drive && W.folder ? W.folder.id : null, group_id: W.group.id, plan: W.plan, start: true,
      instagram: srcPayload(X), sources: vidSources(X), encore: X.encore, inbox: X.inbox, youtube_on: c.platform === 'youtube' ? W.dest.youtube : false });
    toast(`${c.title} is live`);
    go(`channel/${c.id}`);
  },
  'save-group': null,
};
document.addEventListener('input', e => {
  if (e.target.id === 'fq') { W.q = e.target.value; const pos = e.target.selectionStart; drawWizard(); const n = $('#fq'); if (n) { n.focus(); n.setSelectionRange(pos, pos); } }
  if (e.target.id === 'jit') { const v = $('#jv'); if (v) v.textContent = e.target.value; }
});
document.addEventListener('change', e => { if (['wfb', 'wig'].includes(e.target.id) && W.step === 3) { readPlanInputs(); drawWizard(); } });

// ------------------------------------------------------------------ connect Instagram (as a destination) with Instagram login
async function connectIgLogin(groupId, done) {
  let link;
  try { link = await api('POST', '/api/connect', { purpose: 'instagram', group_id: groupId }); }
  catch (e) { toast(e.message, true); if (/Settings/.test(e.message)) go('settings'); return; }
  modal(`<h2>Connect Instagram</h2><p class="muted" style="margin:0">Open this link, sign in to the client’s Instagram <b>Professional</b> account (Business or Creator) and press <b>Allow</b>. No password is ever given to DriveCast.</p>
    <div class="linkbox"><span class="kicker">One-time authorization link</span><div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap"><span class="mono" style="font-size:14px;flex:1 1 260px;word-break:break-all">${esc(link.url)}</span><button class="btn coral" data-act="copy" data-text="${esc(link.url)}">${ic('copy', 16)}Copy</button><a class="btn" href="${esc(link.url)}" target="_blank" rel="noopener">Open here</a></div><span style="font-size:12px;color:#8E94A0">Single use · tied to this pool’s IP</span></div>
    <div id="igwait" style="display:flex;align-items:center;gap:12px;margin-top:16px;padding:14px;border:1px dashed #C9C6BD;border-radius:12px"><span class="pulse"></span><div style="flex:1"><b>Waiting for Instagram…</b><p class="muted" style="margin:3px 0 0;font-size:13px">This updates by itself once Allow is pressed.</p></div></div>
    <div class="actions" style="justify-content:flex-end;margin-top:12px"><button class="btn" data-act="close">Close</button></div>`);
  const t = setInterval(async () => {
    if (!document.getElementById('modal')) return clearInterval(t);
    try {
      const st = await api('GET', `/api/connect/${link.code}`);
      if (st.status === 'done') { clearInterval(t); closeModal(); toast(`Instagram connected${st.result && st.result.instagram ? ' · @' + st.result.instagram : ''}`); if (done) done(); else render(); }
      else if (st.status === 'error') { clearInterval(t); const w = $('#igwait'); if (w) w.innerHTML = `<div class="note bad" style="margin:0">${ic('alert', 16)}<span>${esc(st.error || 'Could not connect')}</span></div>`; }
      else if (st.status === 'expired') { clearInterval(t); const w = $('#igwait'); if (w) w.innerHTML = '<span class="muted">Link expired. Close and try again.</span>'; }
    } catch (e) { /* keep waiting */ }
  }, 3000);
  S.timers.push(t);
}

// ------------------------------------------------------------------ Pools
function groupForm(g) {
  const v = g || {};
  return `<form data-form="${g ? 'gsave' : 'newgroup'}" style="margin-top:14px">
    <label class="f">Paste the whole proxy<input class="i mono" name="proxy" id="pxpaste" placeholder="107.181.148.140:6000:username:password" autocomplete="off"><span class="help">We split it for you: host:port:username:password, user:pass@host:port or socks5://… all work.</span></label>
    <div class="form" style="margin-top:14px">
    <label class="f">Host or IP<input class="i" name="host" required placeholder="proxy.example.com" value="${esc(v.host || '')}"></label>
    <label class="f">Port<input class="i" name="port" required inputmode="numeric" placeholder="1080" value="${esc(v.port || '')}"></label>
    <label class="f">Username<input class="i" name="username" autocomplete="off" value="${esc(v.username || '')}"></label>
    <label class="f">Password<input class="i" name="password" type="password" autocomplete="new-password" placeholder="${g && v.has_password ? 'Saved — leave empty to keep' : ''}"></label>
    ${g ? `<label class="f">Channels on this IP<input class="i" name="max_channels" type="number" min="1" value="${v.max_channels}"></label>
      <label class="f">Priority<select class="i" name="priority">${['high', 'normal', 'low'].map(p => `<option ${p === v.priority ? 'selected' : ''}>${p}</option>`).join('')}</select></label>`
      : `<label class="f">Channels on this IP<select class="i" name="max_channels"><option value="1">1 · dedicated</option><option>2</option><option>3</option><option>4</option><option value="5" selected>5 · recommended max</option><option value="8">8 · not recommended</option><option value="10">10 · not recommended</option></select></label>
      <label class="f">Priority<select class="i" name="priority"><option value="high">High</option><option value="normal" selected>Normal</option><option value="low">Low</option></select></label>`}
  </div><div id="gtest" style="margin-top:14px"></div><div class="actions" style="margin-top:14px"><button class="btn" type="button" data-act="testgroup">${ic('bolt', 16)}Test connection</button>${g ? '<button class="btn" type="button" data-act="close">Cancel</button>' : ''}<button class="btn primary" type="submit">${g ? 'Save changes' : 'Save group'}</button></div></form>`;
}
function proxyFromForm(f) {
  const v = Object.fromEntries(new FormData(f).entries());
  delete v.proxy;
  return v;
}
document.addEventListener('input', e => {
  if (e.target.id !== 'pxpaste') return;
  const raw = e.target.value.trim(), f = e.target.closest('form');
  let t = raw.replace(/^(socks5h?|socks|https?):\/\//i, ''), user = '', pass = '';
  if (t.includes('@')) { const i = t.lastIndexOf('@'); const cred = t.slice(0, i); t = t.slice(i + 1); [user, ...pass] = cred.split(':'); pass = pass.join(':'); }
  const p = t.split(':');
  let host = '', port = '';
  if (p.length >= 4 && !user) { [host, port, user] = p; pass = p.slice(3).join(':'); } else if (p.length >= 2) { [host, port] = p; }
  if (!host || !/^\d+$/.test(port || '')) return;
  f.host.value = host; f.port.value = port; f.username.value = user || ''; if (pass) f.password.value = pass;
  e.target.style.borderColor = 'var(--ok)';
});
const GROUP_FORM_ACTS = {
  newgroup: async (v, f) => {
    const r = await api('POST', '/api/groups', proxyFromForm(f));
    toast(`Pool ${r.group.number} saved · ${r.group.outgoing_ip}`);
    if (location.hash.startsWith('#/add')) { W.groups = (await api('GET', '/api/groups')).groups; W.group = W.groups.find(g => g.id === r.group.id); drawWizard(); } else render();
  },
  testgroup: async (d, el) => {
    const f = el.closest('form'); const v = proxyFromForm(f); const out = f.querySelector('#gtest');
    if (!v.password && f.dataset.form === 'gsave') v.password = '';
    out.innerHTML = '<div class="note info"><span class="spinner"></span><span>Testing through the proxy…</span></div>';
    const r = await api('POST', '/api/groups/test', v);
    out.innerHTML = r.ok ? `<div class="note ok">${ic('check', 18)}<span><b>Works.</b> Outgoing IP <b class="mono">${esc(r.ip)}</b> · ${esc(where(r) || 'location unknown')}${r.isp ? ' · ' + esc(r.isp) : ''} · ${r.latency_ms} ms</span></div>` : `<div class="note bad">${ic('alert', 18)}<span>${esc(r.error)}</span></div>`;
  },
};
Object.assign(WIZ_ACTS, GROUP_FORM_ACTS);
function ring(n, cap, color) {
  const r = 34, c = 2 * Math.PI * r, p = Math.min(1, n / Math.max(1, cap));
  return `<svg width="88" height="88" viewBox="0 0 88 88" role="img" aria-label="${n} of ${cap} channels"><circle cx="44" cy="44" r="${r}" fill="none" stroke="#EDEBE6" stroke-width="8"/><circle cx="44" cy="44" r="${r}" fill="none" stroke="${color}" stroke-width="8" stroke-linecap="round" stroke-dasharray="${(c * p).toFixed(1)} ${c.toFixed(1)}" transform="rotate(-90 44 44)"/><text x="44" y="49" text-anchor="middle" font-family="Bricolage Grotesque, sans-serif" font-size="18" font-weight="700" fill="#0E1116">${n}/${cap}</text></svg>`;
}
function igCard(g) {
  const a = g.instagram;
  const st = !a ? ['Not added', 'neutral', 'No Instagram account yet — needed only for clients with an Instagram source.']
    : a.status === 'needs_cookies' ? ['Sign-in expired', 'bad', 'Paste fresh cookies to continue pulling posts.']
      : a.status === 'resting' && a.paused_until > Date.now() / 1000 ? ['Resting', 'warn', `Instagram asked this account to slow down · resumes ${rel(a.paused_until)} · meanwhile another test account reads for this pool`]
        : ['Connected', 'ok', `${a.sources} Instagram source(s) · ${a.today} action(s) today · reads via ${a.proxy_mode === 'phone' ? 'phone internet' : 'group IP'}`];
  return `<div class="igbox"><span style="width:34px;height:34px;border-radius:10px;background:#FDEBE6;color:#B83A1F;display:inline-flex;align-items:center;justify-content:center">${ic('ig', 18)}</span>
    <div style="flex:1;min-width:160px;display:flex;flex-direction:column;gap:2px"><span style="display:flex;align-items:center;gap:8px"><b style="font-size:13px">Instagram ${a && a.username ? '· @' + esc(a.username) : ''}</b>${chip(st[0], st[1])}</span><span class="muted" style="font-size:12px">${esc(st[2])}</span></div>
    <button class="btn sm ${!a || a.status === 'needs_cookies' ? 'primary' : ''}" data-act="cookies" data-id="${g.id}">${ic('cookie', 14)}${a ? 'Replace cookies' : 'Add cookies'}</button></div>
    ${a && a.shared_with && a.shared_with.length ? `<div class="note warn" style="margin-top:-4px">${ic('alert', 16)}<span><b>@${esc(a.username)} is also used by Pool ${a.shared_with.join(', ')}.</b> Instagram counts one account’s requests together, so these pools slow each other down. Give each pool its own test account (vault → Use).</span></div>` : ''}`;
}
function fbCard(g) {
  const fb = (S.fb && S.fb.groups || []).find(x => x.group_id === g.id) || { account: null, pages: [] };
  if (!S.fb || !S.fb.app_ready) return `<div class="igbox"><span style="width:34px;height:34px;border-radius:10px;background:#E7EEFB;color:#1D4FA8;display:inline-flex;align-items:center;justify-content:center">${ic('fb', 18)}</span><div style="flex:1;min-width:160px"><b style="font-size:13px">Facebook</b><div class="muted" style="font-size:12px">Add your Meta app in Settings to post to pages.</div></div><a class="btn sm" href="#/settings">Settings</a></div>`;
  const a = fb.account;
  const st = !a ? ['Not connected', 'neutral'] : a.status === 'needs_reconnect' ? ['Sign-in expired', 'bad'] : ['Connected', 'ok'];
  const onGroup = (S.allChannels || []).filter(c => c.group && c.group.id === g.id);
  const opts = c => onGroup.map(ch => `<option value="${ch.id}" ${c && c.id === ch.id ? 'selected' : ''}>${esc(ch.title)}</option>`).join('');
  const pages = fb.pages.map(p => `<div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-top:1px solid var(--line)">
    ${avatar(p.name, hashI(p.name), p.picture, 26)}<div style="flex:1;min-width:0"><span class="ell" style="font-weight:600;font-size:13px">${esc(p.name)}</span>${p.can_post ? '' : '<span class="mini" style="color:var(--warn)"> · you are not an admin/editor</span>'}</div>
    <select class="i" style="height:34px;max-width:170px" data-assign="${p.id}" ${p.can_post ? '' : 'disabled'}><option value="">— no channel —</option>${opts(p.channel)}</select></div>`).join('');
  return `<div class="igbox" style="flex-direction:column;align-items:stretch;gap:10px"><div style="display:flex;align-items:center;gap:10px"><span style="width:34px;height:34px;border-radius:10px;background:#E7EEFB;color:#1D4FA8;display:inline-flex;align-items:center;justify-content:center">${ic('fb', 18)}</span>
    <div style="flex:1;min-width:140px"><span style="display:flex;align-items:center;gap:8px"><b style="font-size:13px">Facebook${a ? ' · ' + esc(a.name) : ''}</b>${chip(st[0], st[1])}</span><span class="muted" style="font-size:12px">${a ? `${fb.pages.length} page(s) — link each to a channel below` : 'One Allow brings every page of this ID.'}</span></div>
    <button class="btn sm ${!a || a.status === 'needs_reconnect' ? 'primary' : ''}" data-act="fbconnect" data-id="${g.id}">${ic('fb', 14)}${a ? 'Reconnect' : 'Connect Facebook'}</button>${a ? `<button class="btn sm" data-act="fbrefresh" data-id="${g.id}">${ic('refresh', 14)}</button>` : ''}</div>
    ${a && fb.pages.length ? `<div>${pages}</div>` : ''}</div>`;
}
// The client's own apps for this pool + how ready the pool is for a new client
function appsCard(g) {
  const a = g.apps || {}, r = g.readiness || { steps: [], score: 0, of: 0 };
  const pill = (ok, t) => `<span style="display:inline-flex;align-items:center;gap:5px;font-size:11.5px;font-weight:600;padding:3px 8px;border-radius:99px;background:${ok ? '#E6F6EE' : '#F4F3EF'};color:${ok ? '#0B6B3A' : '#6B7080'}">${ok ? '✓' : '○'} ${t}</span>`;
  return `<div class="igbox" style="flex-direction:column;align-items:stretch;gap:10px"><div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap"><span style="width:34px;height:34px;border-radius:10px;background:#EEF0FF;color:#4338CA;display:inline-flex;align-items:center;justify-content:center">${ic('key', 18)}</span>
    <div style="flex:1;min-width:150px"><b style="font-size:13px">Client’s own apps</b><div class="muted" style="font-size:12px">${a.google || a.meta ? 'This pool signs in with the client’s own Google / Meta apps.' : 'Uses your apps from Settings. Add the client’s own for their own quota and no app review.'}</div></div>
    <button class="btn sm" data-act="gapps" data-id="${g.id}">${ic('edit', 14)}Apps</button></div>
    <div style="display:flex;gap:6px;flex-wrap:wrap">${pill(a.google, 'Google')}${pill(a.meta, 'Meta')}${pill(a.instagram, 'Instagram login')}${pill(a.groq, 'Groq AI')}</div>
    <div><div style="display:flex;justify-content:space-between;font-size:12px"><b>Ready for a client</b><span class="mono">${r.score}/${r.of}</span></div><div class="meter" style="margin-top:6px"><span style="width:${Math.round(100 * r.score / Math.max(1, r.of))}%;background:${r.score === r.of ? '#1F9D55' : '#0E1116'}"></span></div>
    <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px">${r.steps.map(x => pill(x.ok, x.name)).join('')}</div></div></div>`;
}
async function groupAppsModal(gid) {
  const [{ groups }, st] = await Promise.all([api('GET', '/api/groups'), api('GET', '/api/settings')]);
  const g = groups.find(x => x.id === +gid); const a = g.apps || {};
  const red = (u, t) => `<div class="linkbox" style="padding:10px"><span class="kicker">${t}</span><div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><span class="mono" style="flex:1 1 220px;word-break:break-all;font-size:12px">${esc(u)}</span><button type="button" class="btn coral sm" data-act="copy" data-text="${esc(u)}">${ic('copy', 14)}Copy</button></div></div>`;
  modal(`<h2>Pool ${g.number} · client’s own apps</h2><p class="muted" style="margin:0">The client makes these in their own accounts (10–15 minutes). Leave empty to use yours. Sign-ins already made keep working with the app that made them.</p>
    <form data-form="gappsave" style="display:flex;flex-direction:column;gap:14px">
      <label class="f">Client / label for this pool<input class="i" name="label" value="${esc(g.label || '')}" placeholder="e.g. Jess Bacon"></label>
      <div style="border:1px solid var(--line);border-radius:12px;padding:14px;display:flex;flex-direction:column;gap:10px"><b>Google (YouTube)</b>
        <ol class="muted" style="font-size:12.5px;line-height:1.7;margin:0;padding-left:18px"><li>${deep('https://console.cloud.google.com/projectcreate', 'Create a project')} in the client’s Google account.</li><li>Enable ${deep('https://console.cloud.google.com/apis/library/youtube.googleapis.com', 'YouTube Data API v3')}.</li><li>${deep('https://console.cloud.google.com/apis/credentials/consent', 'Consent screen')} → External → <b>In production</b> (never “Testing”: sign-ins expire in 7 days). It stays unverified; the client presses “Continue” once.</li><li>${deep('https://console.cloud.google.com/apis/credentials/oauthclient', 'Create OAuth client')} → Web application → add this redirect URI:</li></ol>
        ${red(st.redirect_uri, 'Google redirect URI')}
        <div class="form"><label class="f">Client ID<input class="i" name="google_client_id" value="${esc(a.google_client_id || '')}" autocomplete="off"></label><label class="f">Client secret<input class="i" name="google_client_secret" type="password" placeholder="${a.google ? 'Saved — leave empty to keep' : ''}" autocomplete="off"></label></div></div>
      <div style="border:1px solid var(--line);border-radius:12px;padding:14px;display:flex;flex-direction:column;gap:10px"><b>Meta (Facebook + Instagram)</b>
        <ol class="muted" style="font-size:12.5px;line-height:1.7;margin:0;padding-left:18px"><li>${deep('https://developers.facebook.com/apps/creation/', 'Create a Meta app')} in the client’s Facebook account (type Business). The client is the app’s admin, so his own pages need no app review.</li><li>Facebook Login for Business → add this redirect URI:</li></ol>
        ${red(st.fb_redirect_uri, 'Facebook redirect URI')}
        <div class="form"><label class="f">App ID<input class="i" name="fb_app_id" value="${esc(a.fb_app_id || '')}" autocomplete="off"></label><label class="f">App secret<input class="i" name="fb_app_secret" type="password" placeholder="${a.meta ? 'Saved — leave empty to keep' : ''}" autocomplete="off"></label></div>
        <details><summary style="cursor:pointer;font-size:13px;font-weight:600">Instagram login (only for Instagram not linked to a page)</summary>${red(st.ig_redirect_uri, 'Instagram redirect URI')}
        <div class="form"><label class="f">Instagram app ID<input class="i" name="ig_app_id" value="${esc(a.ig_app_id || '')}" autocomplete="off"></label><label class="f">Instagram app secret<input class="i" name="ig_app_secret" type="password" placeholder="${a.instagram ? 'Saved — leave empty to keep' : ''}" autocomplete="off"></label></div></details></div>
      <div style="border:1px solid var(--line);border-radius:12px;padding:14px;display:flex;flex-direction:column;gap:10px"><b style="display:flex;align-items:center;gap:8px">${ic('spark', 16)}Groq (AI captions)</b>
        <span class="muted" style="font-size:12.5px">The client makes a free key at ${deep('https://console.groq.com/keys', 'console.groq.com/keys')}. Their own key = their own daily limit (about 130 posts a day). Empty = yours from the AI page.</span>
        <div class="form"><label class="f">Groq API key<input class="i" name="groq_key" type="password" placeholder="${a.groq ? 'Saved — leave empty to keep' : 'gsk_…'}" autocomplete="off"></label>
        ${a.groq ? '<label class="f" style="flex-direction:row;align-items:center;gap:8px;align-self:end;font-weight:500"><input type="checkbox" name="groq_key_clear" value="1">Remove this key</label>' : ''}</div></div>
      <div class="actions" style="justify-content:flex-end"><button type="button" class="btn" data-act="close">Cancel</button><button class="btn primary" type="submit">Save apps</button></div></form>`);
  S.acts.gappsave = async v => {
    ['google_client_secret', 'fb_app_secret', 'ig_app_secret', 'groq_key'].forEach(k => { if (!v[k]) delete v[k]; });
    if (v.groq_key_clear) v.groq_key_clear = true;
    try { await api('PUT', `/api/groups/${gid}/apps`, v); closeModal(); toast('Apps saved for this pool'); render(); } catch (e) { toast(e.message, true); }
  };
}
// Instagram test accounts loaded in advance; any group takes one with a tap
function vaultCard(vault) {
  const tone = { ready: ['Ready', 'ok'], used: ['In use', 'neutral'], bad: ['Signed out', 'bad'], unchecked: ['Not checked', 'warn'] };
  const rows = vault.map(v => `<div style="display:flex;align-items:center;gap:10px;padding:9px 0;border-top:1px solid var(--line)">${avatar(v.username || v.label || '?', hashI(v.username || v.label || '?'), null, 28)}
    <div style="flex:1;min-width:0;display:flex;flex-direction:column"><b class="ell" style="font-size:13px">${v.username ? '@' + esc(v.username) : esc(v.label || 'Test account')}${v.label && v.username ? `<span class="muted" style="font-weight:400"> · ${esc(v.label)}</span>` : ''}</b><span class="mini">${v.group_number ? 'Group ' + v.group_number : 'free'}${v.error ? ' · ' + esc(v.error) : ''}${v.checked_at ? ' · checked ' + esc(rel(v.checked_at)) : ''}</span></div>
    ${chip(...(tone[v.status] || tone.unchecked), false)}<button class="btn sm" data-act="vcheck" data-id="${v.id}" title="Check">${ic('refresh', 14)}</button><button class="btn sm" data-act="vdel" data-id="${v.id}" title="Remove">${ic('trash', 14)}</button></div>`).join('');
  const free = vault.filter(v => !v.group_id && v.status !== 'bad').length;
  return `<section class="card" style="margin-bottom:16px">${hd('Instagram test accounts · vault', 'Load test-account cookies in advance. When you make a pool or a client, pick one — it is already there.', chip(`${free} free`, free ? 'ok' : 'warn'))}
    <form data-form="vadd" style="display:flex;flex-direction:column;gap:8px"><div style="display:flex;gap:8px;flex-wrap:wrap"><input class="i" name="label" placeholder="Label (optional)" style="flex:0 1 200px"><textarea class="i mono" name="cookies" rows="2" style="flex:1 1 320px;height:auto;padding:8px;font-size:12px" placeholder="Paste cookies (cookies.txt, JSON or sessionid=…; ds_user_id=…)"></textarea><button class="btn primary" type="submit">${ic('plus', 16)}Add</button></div></form>
    ${rows ? `<div style="margin-top:8px">${rows}</div>` : ''}</section>`;
}
function igDestCard(g) {
  const list = (S.igd || []).filter(d => d.group_id === g.id);
  const fbA = ((S.fb && S.fb.groups) || []).find(x => x.group_id === g.id);
  const onGroup = (S.allChannels || []).filter(c => c.group && c.group.id === g.id);
  const rows = list.map(d => `<div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-top:1px solid var(--line)">${avatar(d.username || '?', hashI(d.username || '?'), d.picture, 26)}
    <div style="flex:1;min-width:0;display:flex;flex-direction:column"><span class="ell" style="font-weight:600;font-size:13px">@${esc(d.username || d.ig_user_id)}</span><span class="mini">via ${d.via === 'facebook' ? 'page' : 'login'} · ${d.today}/${d.limit} today${d.status !== 'ok' ? ' · <b style="color:var(--bad)">reconnect</b>' : ''}</span></div>
    <select class="i" style="height:34px;max-width:170px" data-igassign="${d.id}"><option value="">— no client —</option>${onGroup.map(ch => `<option value="${ch.id}" ${d.channel_id === ch.id ? 'selected' : ''}>${esc(ch.title)}</option>`).join('')}</select></div>`).join('');
  return `<div class="igbox" style="flex-direction:column;align-items:stretch;gap:10px"><div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap"><span style="width:34px;height:34px;border-radius:10px;background:#FDEBE6;color:#B83A1F;display:inline-flex;align-items:center;justify-content:center">${ic('ig', 18)}</span>
    <div style="flex:1;min-width:140px"><b style="font-size:13px">Instagram · posting</b><div class="muted" style="font-size:12px">${list.length ? `${list.length} account(s) ready to post` : 'Professional accounts this pool posts to (official API).'}</div></div>
    ${fbA && fbA.account ? `<button class="btn sm" data-act="igfindg" data-id="${g.id}">${ic('search', 14)}Find via pages</button>` : ''}<button class="btn sm" data-act="iglogin" data-id="${g.id}">${ic('ig', 14)}Instagram login</button></div>${rows ? `<div>${rows}</div>` : ''}</div>`;
}
async function pageGroups() {
  const [{ groups }, fb, chs, igd] = await Promise.all([api('GET', '/api/groups'), api('GET', '/api/facebook').catch(() => ({ groups: [], app_ready: false })), api('GET', '/api/channels').catch(() => ({ channels: [] })), api('GET', '/api/instagram/destinations').catch(() => ({ destinations: [] }))]);
  S.fb = fb; S.allChannels = chs.channels; S.igd = igd.destinations;
  const vault = (await api('GET', '/api/ig/vault').catch(() => ({ vault: [] }))).vault; S.vault = vault;
  const cards = groups.map(g => {
    const over = g.channel_count > g.max_channels, down = g.status === 'down' && !g.moved_to;
    g._el = 'grp-' + g.id;
    const color = down ? '#B42318' : over ? '#C27A12' : '#0E1116';
    const lat = (g.latency || []).slice(-24), mx = Math.max(1, ...lat);
    const chans = g.channels.map((c, i) => `<div style="display:flex;align-items:center;gap:10px;padding:9px 0;border-top:1px solid var(--line)">${avatar(c.title, hashI(c.title), c.thumbnail, 28)}<div style="display:flex;flex-direction:column;min-width:0;flex:1"><span class="ell" style="font-weight:600;font-size:13px">${esc(c.title)}</span>${handle(c.handle)}</div>${chip(c.state, c.state === 'live' ? 'ok' : 'neutral', false)}</div>`).join('');
    const ipChanged = down && (g.last_error || '').includes('changed');
    return `<section class="card" id="grp-${g.id}"><div style="display:flex;flex-direction:column;gap:14px">
      <div style="display:flex;align-items:center;gap:16px">${ring(g.channel_count, g.max_channels, color)}<div style="display:flex;flex-direction:column;gap:6px;flex:1;min-width:0"><div style="display:flex;justify-content:space-between;gap:8px;align-items:center"><strong style="font-family:var(--disp);font-size:22px">Pool ${g.number}${g.label ? `<span class="muted" style="font:600 14px var(--font)"> · ${esc(g.label)}</span>` : ''}</strong>${g.moved_to ? chip('Moved to ' + g.moved_to, 'neutral') : down ? chip('Not responding', 'bad') : chip('Healthy', 'ok')}</div><span class="mono" style="font-size:13px;font-weight:500">${esc(g.outgoing_ip || '—')}</span>${where(g) ? `<span style="font-size:13px;font-weight:600">${esc(where(g))}${g.isp ? `<span class="muted" style="font-weight:400"> · ${esc(g.isp)}</span>` : ''}</span>` : ''}<span class="muted" style="font-size:12px">SOCKS5 · ${esc(g.host)}:${g.port} · ${esc(g.priority)} priority</span></div></div>
      <div><div style="display:flex;justify-content:space-between;font-size:12px" class="muted"><span>Latency · last checks</span><span class="mono" style="color:var(--ink)">${down ? 'timeout' : (g.latency_ms ?? '—') + ' ms'}</span></div><div style="display:flex;align-items:flex-end;gap:3px;height:34px;margin-top:6px">${lat.map(v => `<span style="flex:1;height:${Math.max(3, Math.round(v / mx * 34))}px;border-radius:2px;background:${v ? '#0E1116' : '#F2B8AD'}"></span>`).join('')}</div></div>
      ${down ? `<div class="note bad">${ic('alert', 16)}<span>${esc(g.last_error || 'Proxy is not responding')}. Posts wait — never sent from another IP. Still testing every 5 minutes; it resumes by itself when the proxy answers.</span><button class="btn sm" data-act="gtest" data-id="${g.id}">${ic('bolt', 14)}Test now</button></div>` : ''}
      ${g.status === 'unstable' ? `<div class="note warn">${ic('refresh', 16)}<span><b>Retrying.</b> The proxy missed ${g.fail_count || 1} check(s) — DriveCast keeps trying and only calls it down after 10 minutes without an answer. Posts still go when it answers.</span></div>` : ''}
      ${over ? `<div class="note warn">${ic('alert', 16)}<span>${g.channel_count} channels on one IP — above the recommended ${g.max_channels}. Allowed, at higher risk.</span></div>` : ''}
      ${igCard(g)}
      ${fbCard(g)}
      ${igDestCard(g)}
      ${appsCard(g)}
      <div>${chans || '<span class="muted" style="font-size:13px">No channels yet.</span>'}</div>
      <div class="actions">${ipChanged ? `<button class="btn primary" data-act="acceptip" data-id="${g.id}">Accept new IP</button>` : ''}<button class="btn" data-act="gtest" data-id="${g.id}">${ic('bolt', 16)}Test now</button><a class="btn" href="#/add">${ic('plus', 16)}Add client</a><button class="btn" data-act="gedit" data-id="${g.id}">Edit</button>${g.channel_count ? '' : `<button class="btn danger" data-act="gdel" data-id="${g.id}">Delete</button>`}</div></div></section>`;
  }).join('');
  const nextN = groups.reduce((m, g) => Math.max(m, g.number), 0) + 1;
  mount('groups', 'Pools', 'One static SOCKS5 IP per pool · 1–5 channels recommended', 'Workspace', `<button class="btn" data-act="testall">${ic('bolt', 16)}Test all</button>`,
    `${vaultCard(vault)}${groups.length ? `<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(340px,1fr));align-items:start">${cards}</div>` : ''}
     <div class="row"><div class="f3"><section class="card">${hd('New Pool', `Next number: Pool ${nextN}`)}${groupForm()}</section></div>
     <div class="f1"><section class="card">${hd('Rules every group follows')}${[['No fallback', 'A failed proxy holds uploads. Traffic never leaves from the server IP.'], ['One channel, one IP', 'A channel never switches IP mid-route. Pause it before moving groups.'], ['IP check before upload', 'Upload starts only when the outgoing IP matches the pool.'], ['DNS through proxy', 'Lookups use socks5h, so the real IP never leaks.']].map(([a, b]) => `<div style="display:flex;gap:12px;padding:12px 0;border-top:1px solid var(--line)"><span style="color:var(--ok)">${ic('check')}</span><div><b>${a}</b><div class="muted" style="font-size:13px">${b}</div></div></div>`).join('')}</section></div></div>`);
  if (S.openCookies) { const gid = S.openCookies; S.openCookies = null; setTimeout(() => cookiesModal(gid, render), 50); }
  if (S.openFbConnect) { const gid = S.openFbConnect; S.openFbConnect = null; setTimeout(() => connectFacebook(gid), 50); }
  if (S.openGroup) { const gid = S.openGroup; S.openGroup = null; setTimeout(() => S.acts.gedit({ id: gid }), 50); }
  if (S.focusGroup) { const gid = S.focusGroup; S.focusGroup = null; setTimeout(() => { const el = document.getElementById('grp-' + gid); if (el) { el.scrollIntoView({ behavior: 'smooth', block: 'start' }); el.classList.add('flash'); } }, 80); }
  if (S.testGroup) { const gid = S.testGroup; S.testGroup = null; setTimeout(() => S.acts.gtest({ id: gid }), 400); }
  if (S.openIgLogin) { const gid = S.openIgLogin; S.openIgLogin = null; setTimeout(() => connectIgLogin(gid, render), 120); }
  Object.assign(S.acts, GROUP_FORM_ACTS, {
    cookies: d => cookiesModal(+d.id, render),
    gapps: d => groupAppsModal(+d.id),
    vadd: async v => { try { await api('POST', '/api/ig/vault', { cookies: v.cookies, label: v.label, user_agent: navigator.userAgent }); toast('Added to the vault'); } catch (e) { toast(e.message, true); } render(); },
    vcheck: async d => { await api('POST', `/api/ig/vault/${d.id}/check`); render(); },
    vdel: async d => { if (confirm('Remove this account from the vault? A group that uses it keeps working.')) { await api('DELETE', `/api/ig/vault/${d.id}`); render(); } },
    gtest: async d => { const r = await api('POST', `/api/groups/${d.id}/test`); toast(r.group.status === 'ok' ? `Pool ${r.group.number} is healthy` : `Pool ${r.group.number}: ${r.group.last_error}`, r.group.status !== 'ok'); render(); },
    testall: async () => { for (const g of groups) { await api('POST', `/api/groups/${g.id}/test`).catch(() => { }); } render(); },
    acceptip: async d => { await api('POST', `/api/groups/${d.id}/accept-ip`); toast('New IP accepted'); render(); },
    fbconnect: d => connectFacebook(+d.id),
    igfindg: async d => { try { const r = await api('POST', `/api/instagram/destinations/discover/${d.id}`); toast(r.found ? `${r.found} Instagram account(s) found` : 'No Instagram account is linked to these pages', !r.found); } catch (e) { toast(e.message, true); } render(); },
    iglogin: d => connectIgLogin(+d.id),
    fbrefresh: async d => { await api('POST', `/api/facebook/refresh/${d.id}`); toast('Pages refreshed'); render(); },
    gdel: async d => { if (!confirm('Delete this Pool?')) return; await api('DELETE', `/api/groups/${d.id}`); render(); },
    gedit: d => {
      const g = groups.find(x => x.id === +d.id);
      modal(`<h2>Edit Pool ${g.number}</h2><p class="muted" style="margin:0">Change the proxy, its login, or how many channels it holds. A new proxy is tested before it is saved.</p>${groupForm(g)}`);
      S.acts.gsave = async (v, f) => {
        const body = proxyFromForm(f);
        if (!body.password) delete body.password;
        const changed = body.host !== g.host || +body.port !== g.port || (body.username || null) !== (g.username || null) || body.password;
        if (!changed) { delete body.host; delete body.port; delete body.username; }
        try { await api('PATCH', `/api/groups/${g.id}`, body); }
        catch (e) {
          if (!/Confirm to continue/.test(e.message)) throw e;
          if (!confirm(e.message)) return;
          await api('PATCH', `/api/groups/${g.id}`, { ...body, accept_new_ip: true });
        }
        closeModal(); toast(`Pool ${g.number} saved`); render();
      };
    },
  });
}

// ------------------------------------------------------------------ Facebook connect
async function connectFacebook(groupId) {
  let link;
  try { link = await api('POST', '/api/connect', { purpose: 'facebook', group_id: groupId }); }
  catch (e) { return toast(e.message, true); }
  modal(`<h2>Connect Facebook</h2><p class="muted" style="margin:0">Open this link, sign in to Facebook with the ID that manages the pages, and press <b>Continue / Allow</b> on every screen. All of that ID’s pages will come in at once.</p>
    <div class="linkbox"><span class="kicker">One-time authorization link</span><div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap"><span class="mono" style="font-size:14px;flex:1 1 260px;word-break:break-all">${esc(link.url)}</span><button class="btn coral" data-act="copy" data-text="${esc(link.url)}">${ic('copy', 16)}Copy</button><a class="btn" href="${esc(link.url)}" target="_blank" rel="noopener">Open here</a></div><span style="font-size:12px;color:#8E94A0">Single use · tied to this pool’s IP</span></div>
    <div id="fbwait" style="display:flex;align-items:center;gap:12px;margin-top:16px;padding:14px;border:1px dashed #C9C6BD;border-radius:12px"><span class="pulse"></span><div style="flex:1"><b>Waiting for Facebook…</b><p class="muted" style="margin:3px 0 0;font-size:13px">This updates by itself once you press Allow.</p></div></div>
    <div class="actions" style="justify-content:flex-end;margin-top:12px"><button class="btn" data-act="close">Close</button></div>`);
  const t = setInterval(async () => {
    if (!document.getElementById('modal')) return clearInterval(t);
    try {
      const st = await api('GET', `/api/connect/${link.code}`);
      if (st.status === 'done') { clearInterval(t); closeModal(); toast('Facebook connected'); render(); }
      else if (st.status === 'error') { clearInterval(t); const w = $('#fbwait'); if (w) w.innerHTML = `<div class="note bad" style="margin:0">${ic('alert', 16)}<span>${esc(st.error || 'Could not connect')}</span></div>`; }
      else if (st.status === 'expired') { clearInterval(t); const w = $('#fbwait'); if (w) w.innerHTML = '<span class="muted">Link expired. Close and try again.</span>'; }
    } catch (e) { /* keep waiting */ }
  }, 3000);
  S.timers.push(t);
}

// ------------------------------------------------------------------ channels
async function pageChannels() {
  const { channels } = await api('GET', '/api/channels');
  const dr = await api('GET', '/api/drive').catch(() => ({ folders: [] }));
  const routed = channels.filter(c => c.state !== 'setup'), pending = channels.filter(c => c.state === 'setup');
  const filter = S.chFilter || 'all';
  const tones = { live: 'ok', paused: 'neutral', held: 'warn', attention: 'bad' };
  const names = { live: 'Live', paused: 'Paused', held: 'Held', attention: 'Needs you' };
  const shown = routed.filter(c => filter === 'all' || c.state === filter);
  const COLS = 'grid-template-columns:minmax(200px,1.3fr) minmax(300px,1.9fr) 60px 150px 110px 80px 210px;gap:14px';
  const rows = shown.map(c => {
    const gdown = c.group && c.group.status === 'down';
    const routeChips = `<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap"><span class="chip ${gdown ? 'bad' : 'info'}">${ic('net', 14)}Pool ${c.group ? c.group.number : '—'}</span><span class="muted">→</span>${(c.youtube_on ? '<span class="chip neutral">' + ic('yt', 14) + 'YouTube</span>' : '') + (c.facebook ? `<span class="chip ${c.facebook.ok ? 'neutral' : 'warn'}">${ic('fb', 14)}FB</span>` : '') + (c.instagram ? `<span class="chip ${c.instagram.ok ? 'neutral' : 'warn'}">${ic('ig', 14)}IG</span>` : '')}<span class="muted">←</span>${(c.sources || []).map(sx => `<span class="chip ${sx.error_code ? 'warn' : sx.state === 'paused' ? 'neutral' : 'coralc'}" title="${esc(SRC[sx.kind] ? SRC[sx.kind][1] : sx.kind)} source">${ic(SRC[sx.kind] ? SRC[sx.kind][0] : 'ig', 14)}${esc(sx.handle.startsWith('@') ? sx.handle : '@' + sx.handle)}</span>`).join('')}${c.sources && c.sources.length ? '<span class="muted">+</span>' : ''}<span class="chip neutral" style="max-width:170px" title="${esc(c.folder_name)}">${ic('folder', 14)}<span class="ell">${esc(c.folder_name || '—')}</span></span></div>`;
    const t = c.today || {};
    return `<div class="tr" style="${COLS}"><div class="who">${avatar(c.title, hashI(c.title), c.thumbnail, 38)}<div><a class="ell" href="#/channel/${c.id}" style="font-weight:700;color:var(--ink);text-decoration:none">${esc(c.title)}</a><span class="muted" style="font-size:12px">${handle(c.handle)} · ${c.plan.posts_per_day} / day · ${esc(c.plan.audience)}</span>${c.auth_email ? `<span class="mini ell" title="Google account that authorized this channel" style="display:flex;align-items:center;gap:4px">${ic('key', 11)}${esc(c.auth_email)}</span>` : c.platform === 'youtube' ? '<span class="mini" title="Reconnect YouTube once to show which Google account authorized it">authorized by — (reconnect to show)</span>' : ''}</div></div>
      <div>${routeChips}${c.state_reason ? `<div class="muted" style="font-size:12px;margin-top:6px">${esc(c.state_reason)}</div>` : ''}</div>
      <span class="mono">${t.done || 0} / ${t.total || 0}</span><span style="font-size:13px">${c.live ? liveBar(c.live) : c.next ? `<b class="mono" data-count="${c.next.due_at}" style="font-size:14px">${countdown(c.next.due_at)}</b><br><span class="muted" style="font-size:12px">${esc(tFull(c.next.due_at))}</span>` : '—'}</span>
      <span>${gdown && c.state === 'live' ? chip('Held · proxy', 'warn') : chip(names[c.state] || c.state, tones[c.state] || 'neutral')}</span>${c.series ? spark(c.series, 80, 26, '#0E1116', false) : '<span class="muted">—</span>'}
      <div class="actions" style="justify-content:flex-end">${c.state === 'live' ? `<button class="btn sm" data-act="cact" data-a="post-now" data-id="${c.id}" ${gdown ? 'disabled' : ''}>Post now</button>` : `<button class="btn sm primary" data-act="cact" data-a="resume" data-id="${c.id}">Resume</button>`}<a class="btn sm" href="#/channel/${c.id}">Open</a><button class="btn sm" data-act="cmore" data-id="${c.id}" aria-label="More for ${esc(c.title)}">•••</button></div></div>`;
  }).join('');
  const counts = s => routed.filter(c => s === 'all' || c.state === s).length;
  const tabs = ['all', 'live', 'paused', 'attention'].map(s => `<button class="btn sm ${filter === s ? 'primary' : ''}" data-act="chf" data-f="${s}">${s === 'all' ? 'All' : names[s]} · ${counts(s)}</button>`).join('');
  const used = new Set(channels.map(c => c.folder_id));
  const freeFolders = (dr.folders || []).filter(f => !used.has(f.id)).slice(0, 8);
  const unrouted = pending.map(c => `<div style="display:flex;align-items:center;gap:12px;padding:12px 0;border-top:1px solid var(--line)">${avatar(c.title, hashI(c.title), c.thumbnail)}<div style="flex:1"><b>${esc(c.title)}</b><div class="muted" style="font-size:13px">Authorized · needs a Drive folder and plan</div></div><a class="btn sm" href="#/add/${c.id}">Finish setup</a><button class="btn sm danger" data-act="cdel" data-id="${c.id}">Remove</button></div>`).join('') +
    freeFolders.map(f => `<div style="display:flex;align-items:center;gap:12px;padding:12px 0;border-top:1px solid var(--line)">${ic('folder', 20)}<div style="flex:1"><b>${esc(f.name)}</b><div class="muted" style="font-size:13px">Drive folder without a channel</div></div><a class="btn sm" href="#/add">Add channel</a></div>`).join('');
  mount('channels', 'Clients', 'Every client with its sources, destinations, Pool and plan', 'Workspace', addBtn,
    `<section class="card"><div class="actions" style="margin-bottom:16px">${tabs}</div>${routed.length ? `<div class="tbl"><div style="min-width:1200px"><div class="tr th" style="${COLS}"><span>Channel</span><span>Route · Pool → destinations ← sources</span><span>Today</span><span>Next post in</span><span>Status</span><span>7 days</span><span></span></div>${rows || '<div class="empty">No channels in this view.</div>'}</div></div>` : `<div class="empty"><h3>No channels yet</h3>Add your first channel — it takes about two minutes.<div style="margin-top:14px">${addBtn}</div></div>`}</section>
     ${unrouted ? `<section class="card" style="margin-top:16px">${hd('Not routed yet', 'A route needs a pool, an authorized channel and a Drive folder. Names alone never start uploads.')}${unrouted}</section>` : ''}`);
  every(1000, () => document.querySelectorAll('[data-count]').forEach(el => { el.textContent = countdown(+el.dataset.count); }));
  every(channels.some(c => c.live) ? 4000 : 60000, () => { if (!document.getElementById('modal')) render(); });
  S.acts.chf = d => { S.chFilter = d.f; render(); };
  S.acts.cact = async d => { closeModal(); await api('POST', `/api/channels/${d.id}/${d.a}`); toast({ 'post-now': 'Queued — posting now', pause: 'Paused', resume: 'Live again' }[d.a]); render(); };
  S.acts.cdel = async d => { if (!confirm('Remove this channel from DriveCast? Videos on YouTube and files in Drive stay untouched.')) return; await api('POST', `/api/channels/${d.id}/delete`); render(); };
  S.acts.cmore = d => {
    const c = channels.find(x => x.id === +d.id), p = c.plan;
    const sel = (n, opts, v) => `<select class="i" name="${n}">${opts.map(([a, b]) => `<option value="${a}" ${String(a) === String(v) ? 'selected' : ''}>${b}</option>`).join('')}</select>`;
    modal(`<div style="display:flex;align-items:center;gap:12px">${avatar(c.title, hashI(c.title), c.thumbnail, 44)}<div><h2>${esc(c.title)}</h2>${handle(c.handle)}</div></div>
      <form data-form="csave" style="display:flex;flex-direction:column;gap:12px"><div class="form">
        <label class="f">Posts per day${sel('posts_per_day', [1, 2, 3, 4, 5, 6].map(n => [n, n]), p.posts_per_day)}</label>
        <label class="f">Audience${sel('audience', [['US+UK', 'USA + UK'], ['US', 'USA'], ['UK', 'UK'], ['CA', 'Canada'], ['AU', 'Australia']], p.audience)}</label>
        <label class="f">Minimum gap${sel('min_gap_min', [[120, '2 h'], [180, '3 h'], [240, '4 h'], [360, '6 h']], p.min_gap_min)}</label>
        <label class="f">Natural timing${sel('jitter_min', [[0, '±0'], [15, '±15 min'], [30, '±30 min'], [45, '±45 min'], [60, '±60 min']], p.jitter_min)}</label></div>
        <div class="actions"><button class="btn primary" type="submit">Save plan</button></div></form>
      <div style="border-top:1px solid var(--line);padding-top:14px;display:flex;flex-direction:column;gap:10px"><b style="font-size:13px">Other actions</b><div class="actions">
        ${c.state === 'live' ? `<button class="btn sm" data-act="cact" data-a="pause" data-id="${c.id}">Pause channel</button>` : ''}
        <button class="btn sm" data-act="creconnect" data-id="${c.id}">${ic('key', 14)}Reconnect (new sign-in link)</button>
        <a class="btn sm" href="https://studio.youtube.com/channel/${esc(c.external_id)}" target="_blank" rel="noopener">${ic('ext', 14)}YouTube Studio</a>
        <button class="btn sm danger" data-act="cdel" data-id="${c.id}">${ic('trash', 14)}Remove</button></div>
        <span class="help">Published: ${c.published_total} · Folder videos waiting: ${c.queue_count ?? '—'} · Pool ${c.group ? c.group.number + ' · ' + esc(c.group.outgoing_ip || '') : '—'}</span></div>
      <div class="actions" style="justify-content:flex-end"><button class="btn" data-act="close">Close</button></div>`);
    S.acts.csave = async v => { await api('PATCH', `/api/channels/${c.id}`, { plan: v }); closeModal(); toast('Plan saved — upcoming posts re-planned'); render(); };
    S.acts.creconnect = async () => {
      if (!c.group) throw new Error('Choose a pool first');
      const l = await api('POST', '/api/connect', { purpose: 'youtube', group_id: c.group.id });
      modal(`<h2>Reconnect ${esc(c.title)}</h2><p class="muted">Open this link and sign in with the channel’s Google account. It uses Pool ${c.group.number}.</p><div class="linkbox"><span class="mono" style="word-break:break-all">${esc(l.url)}</span><button class="btn coral" data-act="copy" data-text="${esc(l.url)}">${ic('copy', 16)}Copy link</button></div><div class="actions" style="justify-content:flex-end"><button class="btn" data-act="close">Done</button></div>`);
    };
  };
}


// ------------------------------------------------------------------ client page (one channel, its whole pipeline)
const LANE = { fresh: ['FRESH', 'coralc'], drive: ['DRIVE', 'neutral'], backfill: ['OLDER', 'ok'], encore: ['ENCORE', 'info'] };
function srcStatus(sx, acc) {
  if (sx.state === 'paused') return ['Paused', '#8E94A0'];
  if (sx.kind === 'instagram' && acc && acc.status === 'needs_cookies') return ['Sign-in expired', '#FF6B57'];
  if (sx.kind === 'instagram' && acc && acc.status === 'resting') return ['Resting', '#F2B54A'];
  if (sx.error) return [sx.error.title, sx.error.fix ? '#FF6B57' : '#F2B54A'];
  return [sx.last_check ? `checked ${rel(sx.last_check)}` : 'first check within a minute', '#3DDC97'];
}
// The client's whole route on one screen: sources → phone → destinations
function routeMap(c, d) {
  const acc = d.ig_account;
  const node = (act, id, icon, title, sub, dot, off) => `<button type="button" class="rnode ${off ? 'off' : ''}" data-act="${act}" data-id="${id || ''}">${icon}<span class="rt"><b>${esc(title)}</b><span>${esc(sub)}</span></span>${dot ? `<span class="rs" style="background:${dot}"></span>` : ''}</button>`;
  const srcs = [];
  if (c.folder_id) srcs.push(`<a class="rnode" href="https://drive.google.com/drive/folders/${esc(c.folder_id)}" target="_blank" rel="noopener">${srcIcon('drive')}<span class="rt"><b>${esc(c.folder_name || 'Drive folder')}</b><span>${d.has_inbox ? 'your files + client Inbox' : 'your files'} · ${c.queue_count ?? 0} waiting</span></span>${ic('ext', 14)}</a>`);
  for (const sx of d.sources) {
    const st = srcStatus(sx, acc);
    srcs.push(node('srcopen', sx.id, srcIcon(sx.kind), `${SRC[sx.kind][1]} · ${sx.handle.startsWith('@') || sx.kind !== 'instagram' ? sx.handle : '@' + sx.handle}`, `${st[0]} · ${num(sx.total_found)} found`, st[1], sx.state === 'paused'));
  }
  srcs.push(`<button type="button" class="rnode add" data-act="srcadd">${ic('plus', 16)}Add a source</button>`);
  const dst = [];
  const yt = c.platform === 'youtube';
  dst.push(node('dyt', '', dstIcon('youtube'), yt ? c.title : 'YouTube', yt ? (c.youtube_on ? 'on · direct publish' : 'off for this client') : 'no YouTube for this client', yt && c.youtube_on ? '#3DDC97' : null, !(yt && c.youtube_on)));
  const fb = d.facebook;
  dst.push(node('fblink', '', dstIcon('facebook'), fb && fb.linked ? fb.linked.name : 'Facebook page', fb && fb.linked ? (fb.status === 'needs_reconnect' ? 'reconnect needed' : 'videos + photos · API') : 'tap to link a page', fb && fb.linked ? (fb.status === 'needs_reconnect' ? '#FF6B57' : '#3DDC97') : null, !(fb && fb.linked)));
  const ig = d.instagram_dest && d.instagram_dest.linked;
  dst.push(node('iglink', '', dstIcon('instagram'), ig ? '@' + (ig.username || ig.ig_user_id) : 'Instagram', ig ? (ig.status === 'ok' ? `reels + photos · via ${ig.via === 'facebook' ? 'page' : 'login'}` : 'reconnect needed') : 'tap to connect', ig ? (ig.status === 'ok' ? '#3DDC97' : '#FF6B57') : null, !ig));
  const live = c.live;
  const hub = `<div class="rhub"><div class="rwire"><span class="rflow ${live ? 'on' : ''}"></span></div><div class="core ${live ? 'busy' : ''}">${LOGO.replace('width="20" height="20"', 'width="34" height="34"')}</div>
    <small>${live ? `<b style="color:#fff">${esc(live.stage || 'Working')}</b>${live.pct != null ? ` · ${live.pct}%` : ''}` : `Pool ${c.group ? c.group.number : '—'} · ${esc(c.group && c.group.outgoing_ip || '')}<br>clean · title · Drive · ledger`}</small><div class="rwire"><span class="rflow ${live ? 'on' : ''}"></span></div></div>`;
  return `<section class="rmap"><div class="rcol"><span class="kicker">Sources</span>${srcs.join('')}</div>${hub}<div class="rcol"><span class="kicker">Destinations</span>${dst.join('')}</div></section>`;
}
async function pageChannel(id) {
  const d = await api('GET', `/api/channels/${id}/detail`);
  const c = d.channel, acc = d.ig_account, p = d.pipeline;
  const pp = c.plan.posts_per_day || 2;
  const stage = (k, n, sub, col) => `<div class="stg"><span>${k}</span><strong style="color:${col}">${num(n)}</strong><em>${sub}</em></div>`;
  const pipe = `<section class="today" style="padding:16px 18px"><div style="display:flex;justify-content:space-between;align-items:baseline;gap:10px;flex-wrap:wrap"><h2 style="font-size:16px">Pipeline</h2><span class="mono" style="font-size:11px;color:#8E94A0">every post of this client, by stage · one ledger</span></div>
    <div class="stgs">${stage('Waiting', p.waiting, 'to download', '#C9CDD6')}${stage('Downloading', p.downloading, 'right now', '#FF9B85')}${stage('Ready in Drive', p.ready, `target ~${d.ready_target}`, '#3DDC97')}${stage('Published', p.published, d.destinations.join(' · ') || 'no destination', '#FFFFFF')}${stage('Photo posts', p.photos, 'for Facebook / Instagram', '#9B8CFF')}${stage('Skipped', p.skipped, 'see below', '#F2B54A')}</div>
    ${c.live ? `<div style="margin-top:12px">${liveBar(c.live, true)}</div>` : ''}</section>`;
  const slots = d.slots.slice();
  const qrows = d.queue.map((m, i) => {
    const sl = slots[i];
    const ln = LANE[m.lane] || LANE.drive;
    const st = m.state === 'in_drive' ? ['Ready', 'var(--ok)'] : m.state === 'downloading' ? ['Downloading', 'var(--coral)'] : ['Waiting', 'var(--muted)'];
    return `<div class="qrow"><span style="display:flex;flex-direction:column"><b class="mono" style="font-size:13px">${sl ? esc(tTime(sl.due_at)) : '—'}</b><span class="mini">${sl ? esc(tDay(sl.due_at)) : 'later'}</span></span>
      <span>${chip(ln[0], ln[1], false)}</span>
      <span style="min-width:0;display:flex;flex-direction:column;gap:2px"><b class="ell" style="font-size:13px">${esc(m.title || (m.caption || '').split('\n')[0] || 'Title comes from the caption')}</b><span class="mini">${esc(ORIGIN[m.origin] || 'Your Drive')}${m.taken_at && m.origin !== 'drive' ? ' · ' + esc(tDay(m.taken_at)) : ''}${m.width ? ` · ${m.width}×${m.height}` : ''}${m.ref ? ` · ${esc(m.ref)}` : ''}</span></span>
      <span style="font-size:12px;font-weight:600;color:${st[1]};display:flex;align-items:center;gap:6px"><span style="width:7px;height:7px;border-radius:9px;background:${st[1]}"></span>${st[0]}</span></div>`;
  }).join('');
  const real = (d.upnext || []).map((it, i) => {
    const sl = slots[i]; const ln = LANE[it.lane] || LANE.drive;
    return `<div class="qrow"><span style="display:flex;flex-direction:column"><b class="mono" style="font-size:13px">${sl ? esc(tTime(sl.due_at)) : '—'}</b><span class="mini">${sl ? esc(tDay(sl.due_at)) : 'later'}</span></span>
      <span>${chip(ln[0], ln[1], false)}</span>
      <span style="min-width:0;display:flex;flex-direction:column;gap:3px"><b class="ell" style="font-size:13px">${esc(it.name || 'Untitled')}${it.photo ? ' · photos' : ''}</b><span class="mini" style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">${originChip(it.origin)}<span>${it.origin === 'drive' ? 'your file in the Drive folder' : it.origin === 'inbox' ? 'uploaded by the client, cleaned' : 'downloaded and cleaned, waiting in Drive'}${it.taken && it.origin !== 'drive' ? ' · posted there ' + esc(tDay(it.taken)) : ''}${it.size ? ' · ' + esc(it.size) : ''}</span></span></span>
      <span style="font-size:12px;font-weight:600;color:var(--ok);display:flex;align-items:center;gap:6px"><span style="width:7px;height:7px;border-radius:9px;background:var(--ok)"></span>Ready</span></div>`;
  }).join('');
  const prep = d.queue.filter(m => m.state !== 'in_drive').slice(0, 6).map(m => `<div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:7px 0;border-top:1px solid var(--line);font-size:12.5px"><span class="ell" style="min-width:0">${esc(m.title || (m.caption || '').split('\n')[0] || 'New post')}</span><span style="display:flex;gap:8px;align-items:center">${originChip(m.origin)}<span class="mini">${m.state === 'downloading' ? 'downloading now' : 'waiting to download'}</span></span></div>`).join('');
  const queue = `<section class="card" style="height:100%">${hd('Up next · real posting order', `${d.upnext_at ? 'checked ' + esc(rel(d.upnext_at)) + ' · ' : ''}Fresh → your Drive files → older posts → Encore · ${pp} posts/day`, `<span class="mini">${c.queue_count != null ? `${c.queue_count} file(s) ready in Drive` : ''}</span>`)}
    ${real || qrows || `<div class="empty">${d.sources.length ? 'Nothing ready yet — the first check runs within a minute.' : 'Add files to the Drive folder or add a source.'}</div>`}
    ${prep ? `<div style="margin-top:12px"><b style="font-size:12.5px">Being prepared</b>${prep}</div>` : ''}</section>`;
  const enc = d.encore.map((e, i) => `<div class="erow"><b class="mono" style="color:var(--muted)">${i + 1}</b><span style="min-width:0;display:flex;flex-direction:column;gap:2px"><b class="ell" style="font-size:13px">${esc(e.title)}</b><span class="mini">posted ${e.reposts + 1}× · last ${esc(rel(e.last_at))}</span></span><span style="text-align:right;display:flex;flex-direction:column;gap:2px"><b class="mono">${num(e.views)}</b><span class="mini" style="color:${e.status === 'eligible' ? 'var(--ok)' : e.status === 'cooldown' ? 'var(--warn)' : 'var(--muted)'}">${e.status === 'cooldown' ? 'ready ' + esc(tDay(e.eligible_at)) : e.status}</span></span></div>`).join('');
  const encCard = `<section class="card">${hd('Encore ranking', 'Used only when nothing new is left', toggle('encset', d.encore_on, d.encore_on ? 'On' : 'Off', ''))}${enc || '<div class="empty" style="padding:14px">Appears after the first posts get views.</div>'}</section>`;
  const sk = d.skipped.map(m => `<div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:8px 0;border-top:1px solid var(--line);font-size:12.5px"><span class="ell" style="min-width:0">${esc(m.title || (m.caption || '').split('\n')[0] || (m.kind === 'video' ? 'Video' : 'Photo post') + (m.taken_at ? ' · ' + tDay(m.taken_at) : ''))}</span><span style="display:flex;align-items:center;gap:8px;white-space:nowrap"><span class="muted">${esc(m.skip_reason || m.error_code || '')}</span><button class="btn sm" data-act="mretry" data-id="${m.id}" title="Try again">Retry</button></span></div>`).join('');
  const skCard = `<section class="card">${hd('Skipped, and why')}${sk || '<div class="empty" style="padding:14px">Nothing skipped.</div>'}</section>`;
  const titlesCard = `<section class="card">${hd('Title ideas', 'Used only when a post has no caption or a camera file name. One per line, never repeated.')}<form data-form="savetitles" style="display:flex;flex-direction:column;gap:10px"><textarea class="i" name="titles" rows="5" style="height:auto;padding:10px" placeholder="Fine line magic&#10;Behind the ink">${esc(d.titles.join('\n'))}</textarea><div class="actions" style="justify-content:flex-end"><button class="btn sm primary" type="submit">Save titles</button></div></form></section>`;
  const ev = d.events.map(e => `<div><span class="fdot" style="background:${{ error: '#B42318', warn: '#C27A12', info: '#4F46E5' }[e.level] || '#1F9D55'}"></span><span>${esc(e.message)}</span><span class="mini mono" style="white-space:nowrap">${esc(rel(e.at))}</span></div>`).join('');
  const evCard = `<section class="card">${hd('Activity')}<div class="feed">${ev || '<div class="empty" style="padding:14px">Nothing yet.</div>'}</div></section>`;
  const actions = `${d.sources.length ? `<button class="btn" data-act="srccheckall">${ic('refresh', 16)}Check sources now</button>` : ''}<button class="btn" data-act="routeedit">${ic('edit', 16)}Edit route</button><button class="btn primary" data-act="srcadd">${ic('plus', 16)}Add source</button>`;
  const head = `<div style="display:flex;align-items:center;gap:12px">${avatar(c.title, hashI(c.title), c.thumbnail, 44)}<span>${esc(c.title)}</span></div>`;
  const warn = d.sources.filter(s => s.error && s.error.fix).map(s => `<div class="note warn" style="margin-bottom:12px">${ic('alert', 16)}<span><b>${esc(SRC[s.kind][1])}: ${esc(s.error.title)}.</b> ${esc(s.error.message)} <button class="btn sm" data-act="srcopen" data-id="${s.id}">Open</button></span></div>`).join('');
  mount('channels', c.title, `${d.sources.map(s => SRC[s.kind][1]).concat(c.folder_id ? ['Drive'] : []).join(' + ') || 'No source yet'} → ${d.destinations.map(x => DST[x][1]).join(' + ') || 'no destination yet'} · Pool ${c.group ? c.group.number : '—'} · ${pp}/day${c.auth_email ? ' · authorized by ' + esc(c.auth_email) : ''}`, 'Client', actions,
    `${acc && acc.error ? `<div class="note bad" style="margin-bottom:12px">${ic('alert', 16)}<span><b>${esc(acc.error.title)}.</b> ${esc(acc.error.message)} <button class="btn sm" data-act="cookies" data-id="${c.group.id}">Replace cookies</button></span></div>` : ''}${warn}
     ${routeMap(c, d)}${pipe}
     <div class="dgrid"><div class="s7">${queue}</div><div class="s5" style="display:flex;flex-direction:column;gap:12px">${encCard}${skCard}</div></div>
     <div class="dgrid"><div class="s7">${safe(() => aiCard(c, d.ai), 'AI captions')}</div><div class="s5">${titlesCard}</div></div>
     ${evCard}`);
  document.querySelector('.top h1').innerHTML = head;
  every(c.live ? 6000 : 30000, () => { if (!document.getElementById('modal')) render(); });
  if (S.openReauth === c.id) { S.openReauth = null; setTimeout(() => ytLinkModal(c, !!(d.ai && d.ai.settings.subtitles)), 120); }
  Object.assign(S.acts, srcActs({}, () => { }, () => c.group), d.ai ? aiActs(c, d.ai) : {}, {
    srccheckall: async () => { await api('POST', `/api/channels/${c.id}/source/check`); toast('Checking every source within a minute'); render(); },
    encset: async () => { await api('PUT', `/api/channels/${c.id}/settings`, { encore: !d.encore_on }); render(); },
    mretry: async x => { await api('POST', `/api/media/${x.id}/retry`); toast('Will try again'); render(); },
    savetitles: async v => { await api('PUT', `/api/channels/${c.id}/settings`, { titles: v.titles }); toast('Titles saved'); },
    srcadd: () => addSourceModal(c, d),
    routeedit: () => routeEditModal(c, d),
    srcopen: x => { const sx = d.sources.find(s => s.id === +x.id); if (!sx) return; if (sx.kind === 'instagram') editSourceModal(c, sx, d); else videoSourceModal(c, sx, sx.kind, d); },
    fblink: () => fbLinkModal(c, d),
    iglink: () => igDestModal(c, d),
    dyt: async () => {
      if (c.platform !== 'youtube') return toast('This client has no YouTube channel. Add a new client with YouTube if needed.', true);
      if (!confirm(c.youtube_on ? 'Stop posting to YouTube for this client? Facebook / Instagram keep going.' : 'Post to YouTube again for this client?')) return;
      await api('PUT', `/api/channels/${c.id}/settings`, { youtube_on: !c.youtube_on }); render();
    },
  });
}
// Everything about a client's route, editable in one place
async function routeEditModal(c, d) {
  const [{ groups }, dr] = await Promise.all([api('GET', '/api/groups'), api('GET', '/api/drive').catch(() => ({ folders: [] }))]);
  const p = Object.assign({ posts_per_day: 2, audience: 'US+UK', min_gap_min: 240, jitter_min: 30 }, c.plan || {});
  const sel = (name, opts, val) => `<select class="i" name="${name}">${opts.map(([v, t]) => `<option value="${esc(v)}" ${String(v) === String(val) ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select>`;
  const live = c.state === 'live';
  modal(`<h2>Edit route · ${esc(c.title)}</h2><p class="muted" style="margin:0">Sources and destinations are edited on the route map (tap any box). Here: name, plan, folder, Pool and sign-ins.</p>
    <form data-form="routesave" style="display:flex;flex-direction:column;gap:14px">
      <label class="f">Client name<input class="i" name="title" value="${esc(c.title)}"></label>
      <div class="form" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px">
        <label class="f">Posts per day${sel('ppd', [1, 2, 3, 4, 5, 6].map(n => [n, n]), p.posts_per_day)}</label>
        <label class="f">Audience${sel('audience', [['US+UK', 'USA + UK'], ['US', 'USA'], ['UK', 'United Kingdom'], ['CA', 'Canada'], ['AU', 'Australia']], p.audience)}</label>
        <label class="f">Minimum gap${sel('gap', [[120, '2 hours'], [180, '3 hours'], [240, '4 hours'], [360, '6 hours']], p.min_gap_min)}</label>
        <label class="f">Natural timing${sel('jitter', [[0, 'exact'], [15, '±15 min'], [30, '±30 min'], [45, '±45 min'], [60, '±60 min']], p.jitter_min)}</label></div>
      <label class="f">Drive folder${sel('folder_id', [['', c.folder_name || '—']].concat((dr.folders || []).filter(f => !f.used || f.id === c.folder_id).map(f => [f.id, f.path])), c.folder_id || '')}</label>
      <label class="f">Pool${sel('group_id', groups.filter(g => !g.moved_to).map(g => [g.id, `Pool ${g.number}${g.label ? ' · ' + g.label : ''} · ${g.outgoing_ip || g.host}`]), c.group ? c.group.id : '')}<span class="help">${live ? 'Pause the client first to move it. ' : ''}Moving unlinks its Facebook page and Instagram account — they belong to the old pool’s identity.</span></label>
      <div class="actions" style="justify-content:flex-end"><button type="button" class="btn" data-act="close">Cancel</button><button class="btn primary" type="submit">Save</button></div></form>
    <div style="border-top:1px solid var(--line);padding-top:12px;display:flex;flex-direction:column;gap:8px"><b style="font-size:13px">Sign-ins</b>
      <div class="actions">${c.platform === 'youtube' ? `<button class="btn sm" data-act="reauthyt">${ic('key', 14)}Reconnect YouTube</button>` : ''}<button class="btn sm" data-act="fblink">${ic('fb', 14)}Facebook page</button><button class="btn sm" data-act="iglink">${ic('ig', 14)}Instagram posting</button></div>
      <b style="font-size:13px;margin-top:6px">Client</b>
      <div class="actions"><button class="btn sm" data-act="chstate">${ic(live ? 'pause' : 'play', 14)}${live ? 'Pause client' : 'Resume client'}</button><button class="btn sm danger" data-act="chdel">${ic('trash', 14)}Delete client</button></div></div>`);
  Object.assign(S.acts, {
    routesave: async v => {
      const body = { title: v.title, plan: { posts_per_day: +v.ppd, audience: v.audience, min_gap_min: +v.gap, jitter_min: +v.jitter } };
      if (v.folder_id && v.folder_id !== c.folder_id) body.folder_id = v.folder_id;
      if (v.group_id && +v.group_id !== (c.group && c.group.id)) body.group_id = +v.group_id;
      try { await api('PATCH', `/api/channels/${c.id}`, body); closeModal(); toast('Route saved'); render(); } catch (e) { toast(e.message, true); }
    },
    reauthyt: () => ytLinkModal(c, !!(d.ai && d.ai.settings.subtitles)),
    chstate: async () => { try { await api('POST', `/api/channels/${c.id}/${live ? 'pause' : 'resume'}`); closeModal(); render(); } catch (e) { toast(e.message, true); } },
    chdel: async () => { if (!confirm(`Delete ${c.title}? Its Drive files stay in Drive; the history on this phone is removed.`)) return; try { await api('POST', `/api/channels/${c.id}/delete`); closeModal(); go('channels'); } catch (e) { toast(e.message, true); } },
  });
}
function addSourceModal(c, d) {
  const has = k => d.sources.some(s => s.kind === k);
  const opt = k => `<button type="button" class="srccard" data-pick="${k}" ${has(k) ? 'disabled' : ''}><span class="top2">${srcIcon(k)}<b style="font-size:14px;flex:1">${SRC[k][1]}</b>${has(k) ? chip('Added', 'ok', false) : ''}</span><span class="muted" style="font-size:12px">${SRC[k][2]}</span></button>`;
  modal(`<h2>Add a source to ${esc(c.title)}</h2><p class="muted" style="margin:0">Same ledger for all sources — nothing posts twice. A post is never sent back to the account it came from.</p>
    <div class="srcgrid" style="margin:12px 0">${opt('instagram')}${opt('youtube')}${opt('tiktok')}</div>
    <div class="actions" style="justify-content:flex-end"><button class="btn" data-act="close">Close</button></div>`);
  document.querySelectorAll('[data-pick]').forEach(b => b.addEventListener('click', () => {
    const k = b.dataset.pick;
    if (k === 'instagram') editSourceModal(c, null, d); else videoSourceModal(c, null, k, d);
  }));
}
function editSourceModal(c, src, d) {
  const X = newSrc({ ig: true, handle: src ? src.handle : '', profile: src ? { username: src.handle, full_name: src.full_name, picture: src.picture, id: null } : null,
    ctype: src ? src.content_type : 'videos', include_old: src ? !!src.include_old : true, cap: src ? src.daily_cap : 6, encore: d.encore_on, inbox: d.has_inbox });
  S.srcX = X;
  const draw = async () => {
    const gr = (await api('GET', '/api/groups')).groups.find(g => g.id === c.group.id);
    const acc = gr && gr.instagram;
    modal(`<h2 style="display:flex;align-items:center;gap:10px">${srcIcon('instagram')}${src ? 'Instagram source' : 'Connect Instagram'}</h2><p class="muted" style="margin:0">Changes apply right away. Nothing already posted is posted again — switching back to a handle resumes where it left off.</p>
      <div>${sourceEditor(X, gr)}</div>
      ${acc ? `<div style="display:flex;align-items:center;gap:10px;padding:12px;border-radius:12px;background:var(--paper);flex-wrap:wrap"><b style="font-size:13px;flex:1 1 200px">Read Instagram through</b><span class="actions"><button type="button" class="btn sm ${acc.proxy_mode === 'phone' ? '' : 'primary'}" data-act="igpx" data-v="group">Pool ${gr.number} IP</button><button type="button" class="btn sm ${acc.proxy_mode === 'phone' ? 'primary' : ''}" data-act="igpx" data-v="phone">Phone’s own internet</button></span><span class="help" style="flex-basis:100%">Reading only. Posting always uses the pool IP.</span></div>` : ''}
      <div class="actions" style="justify-content:space-between;margin-top:6px">${src ? `<span class="actions"><button class="btn" data-act="srcpause">${ic(src.state === 'paused' ? 'play' : 'pause', 14)}${src.state === 'paused' ? 'Resume' : 'Pause'}</button><button class="btn danger" data-act="srcremove">${ic('trash', 14)}Remove</button></span>` : '<span></span>'}<span class="actions"><button class="btn" data-act="close">Cancel</button><button class="btn primary" data-act="srcsave" ${X.profile ? '' : 'disabled'}>Save</button></span></div>`);
    Object.assign(S.acts, srcActs(X, draw, () => gr), {
      srcsave: async () => {
        await api('PUT', `/api/channels/${c.id}/source`, Object.assign({ kind: 'instagram' }, srcPayload(X)));
        await api('PUT', `/api/channels/${c.id}/settings`, { encore: X.encore });
        await api('POST', `/api/channels/${c.id}/inbox`, { enabled: X.inbox });
        closeModal(); toast('Source saved'); render();
      },
      igpx: async v => { await api('POST', `/api/groups/${gr.id}/instagram/proxy`, { proxy_mode: v.v }); toast(v.v === 'phone' ? 'Reading through the phone’s internet' : `Reading through Pool ${gr.number}`); draw(); },
      srcpause: async () => { await api('PUT', `/api/channels/${c.id}/source`, { kind: 'instagram', paused: src.state !== 'paused' }); closeModal(); render(); },
      srcremove: async () => { if (!confirm('Stop pulling from Instagram? Files already in Drive still post; nothing is deleted.')) return; await api('PUT', `/api/channels/${c.id}/source`, { kind: 'instagram', enabled: false }); closeModal(); render(); },
    });
  };
  draw();
}
function videoSourceModal(c, src, kind, d) {
  const X = { link: src ? src.url : '', include_old: src ? !!src.include_old : true, cap: src ? src.daily_cap : 6, mode: src ? src.proxy_mode : 'phone' };
  const site = SRC[kind][1];
  const draw = () => {
    modal(`<h2 style="display:flex;align-items:center;gap:10px">${srcIcon(kind)}${site} source</h2>
      <p class="muted" style="margin:0">${kind === 'youtube' ? 'The client’s own channel. New uploads first, then older ones (newest first) while about a week stays ready. 1080p with sound.' : 'The client’s own profile. Public profiles need no login. Saved without the watermark.'}</p>
      ${src && src.error ? `<div class="note ${src.error.fix ? 'bad' : 'warn'}">${ic('alert', 16)}<span><b>${esc(src.error.title)}.</b> ${esc(src.error.message)}</span></div>` : ''}
      <label class="f">${site} link<input class="i" id="vlink" value="${esc(X.link)}" placeholder="${kind === 'youtube' ? 'https://www.youtube.com/@channel' : 'https://www.tiktok.com/@name'}" autocomplete="off"></label>
      ${oldPosts(X, 'videos')}
      <div style="display:flex;align-items:center;gap:10px;padding:12px;border-radius:12px;background:var(--paper);flex-wrap:wrap"><b style="font-size:13px;flex:1 1 200px">Read ${site} through</b><span class="actions"><button type="button" class="btn sm ${X.mode === 'phone' ? 'primary' : ''}" data-act="vpx" data-v="phone">Phone’s own internet</button><button type="button" class="btn sm ${X.mode === 'group' ? 'primary' : ''}" data-act="vpx" data-v="group">Pool ${c.group ? c.group.number : ''} IP</button></span><span class="help" style="flex-basis:100%">${site} trusts home and mobile internet far more than datacenter IPs. Reading only — posting always uses the pool IP.</span></div>
      <details ${src && src.error && /LOGIN|BOT/.test(src.error_code || '') ? 'open' : ''}><summary style="cursor:pointer;font-weight:600;font-size:13px">${ic('cookie', 14)} Cookies (optional)${src && src.has_cookies ? ' · saved' : ''}</summary>
        <p class="muted" style="font-size:12.5px;margin:8px 0">Only if ${site} asks for sign-in. Use a <b>test account</b>, never the client’s. Export with “Get cookies.txt LOCALLY” on ${kind === 'youtube' ? 'youtube.com' : 'tiktok.com'}.</p>
        <textarea class="i mono" id="vck" rows="4" style="height:auto;padding:10px;font-size:12px" placeholder="# Netscape HTTP Cookie File"></textarea>
        ${src && src.has_cookies ? '<button type="button" class="btn sm" data-act="vckclear" style="margin-top:8px">Remove saved cookies</button>' : ''}</details>
      ${src ? `<details><summary style="cursor:pointer;font-weight:600;font-size:13px">${ic('link', 14)} Paste video links by hand</summary><p class="muted" style="font-size:12.5px;margin:8px 0">Fallback if listing the profile is blocked. One link per line — they join the queue as fresh.</p><textarea class="i mono" id="vlinks" rows="4" style="height:auto;padding:10px;font-size:12px"></textarea><button type="button" class="btn sm" data-act="vaddlinks" style="margin-top:8px">Add links</button></details>` : ''}
      <div class="actions" style="justify-content:space-between;margin-top:6px">${src ? `<span class="actions"><button class="btn" data-act="vpause">${ic(src.state === 'paused' ? 'play' : 'pause', 14)}${src.state === 'paused' ? 'Resume' : 'Pause'}</button><button class="btn danger" data-act="vremove">${ic('trash', 14)}Remove</button></span>` : '<span></span>'}<span class="actions"><button class="btn" data-act="close">Cancel</button><button class="btn primary" data-act="vsave">Save</button></span></div>`);
  };
  const keep = () => { const l = $('#vlink'); if (l) X.link = l.value; };
  Object.assign(S.acts, {
    srcold: v => { keep(); X.include_old = v.v === '1'; draw(); },
    srccap: v => { keep(); X.cap = Math.max(1, Math.min(30, X.cap + +v.d)); draw(); },
    vpx: v => { keep(); X.mode = v.v; draw(); },
    vsave: async () => {
      keep();
      const ck = ($('#vck') || {}).value || '';
      await api('PUT', `/api/channels/${c.id}/source`, { kind, link: X.link, include_old: X.include_old, daily_cap: X.cap, proxy_mode: X.mode, cookies: ck.trim() || undefined });
      closeModal(); toast(`${site} source saved — first check within a minute`); render();
    },
    vckclear: async () => { await api('PUT', `/api/channels/${c.id}/source`, { kind, clear_cookies: true }); closeModal(); toast('Cookies removed'); render(); },
    vaddlinks: async () => { const r = await api('POST', `/api/channels/${c.id}/source/${src.id}/links`, { links: ($('#vlinks').value || '').split(/\s+/) }); toast(`${r.added} video(s) added`); closeModal(); render(); },
    vpause: async () => { await api('PUT', `/api/channels/${c.id}/source`, { kind, paused: src.state !== 'paused' }); closeModal(); render(); },
    vremove: async () => { if (!confirm(`Stop pulling from ${site}? Files already in Drive still post; nothing is deleted.`)) return; await api('PUT', `/api/channels/${c.id}/source`, { kind, enabled: false }); closeModal(); render(); },
  });
  draw();
}
function fbLinkModal(c, d) {
  const fb = d.facebook;
  if (!fb || !fb.app_ready) { toast('Add your Meta app in Settings first', true); return go('settings'); }
  if (!fb.connected) { S.openFbConnect = c.group.id; toast('Connect this pool’s Facebook first — one Allow brings every page'); return go('groups'); }
  const opts = fb.available.map(p => `<button type="button" class="pick ${fb.linked && fb.linked.page_id === p.page_id ? 'sel' : ''}" data-fbpage="${p.id}" ${p.can_post ? '' : 'disabled'} style="flex-direction:row;align-items:center;gap:12px">${avatar(p.name, hashI(p.name), p.picture, 30)}<span style="flex:1;text-align:left"><b style="font-size:14px">${esc(p.name)}</b>${p.can_post ? '' : '<span class="mini" style="color:var(--warn)"> · not an admin</span>'}</span>${fb.linked && fb.linked.page_id === p.page_id ? chip('Linked', 'ok', false) : ''}</button>`).join('');
  modal(`<h2 style="display:flex;align-items:center;gap:10px">${dstIcon('facebook')}Facebook page for ${esc(c.title)}</h2><p class="muted" style="margin:0">Every post of this client — videos, photos and carousels — also goes to this page, through the official API.</p>
    <div style="display:flex;flex-direction:column;gap:8px;margin:14px 0">${opts || '<div class="empty">No pages on this pool.</div>'}</div>
    ${fb.linked ? `<button class="btn danger sm" data-fbpage="" data-unlink="1">Unlink</button>` : ''}
    <div class="actions" style="justify-content:flex-end"><button class="btn" data-act="close">Close</button></div>`);
  document.querySelectorAll('[data-fbpage]').forEach(b => b.addEventListener('click', async () => {
    const pid = b.dataset.fbpage;
    await api('POST', `/api/facebook/pages/${pid || fb.linked.id}/assign`, { channel_id: pid ? c.id : null });
    closeModal(); toast(pid ? 'Page linked' : 'Page unlinked'); render();
  }));
}
function igDestModal(c, d) {
  const x = d.instagram_dest || { available: [], linked: null };
  const opts = x.available.map(a => `<button type="button" class="pick ${x.linked && x.linked.id === a.id ? 'sel' : ''}" data-igd="${a.id}" style="flex-direction:row;align-items:center;gap:12px">${avatar(a.username || '?', hashI(a.username || '?'), a.picture, 30)}<span style="flex:1;text-align:left;display:flex;flex-direction:column"><b style="font-size:14px">@${esc(a.username || a.ig_user_id)}</b><span class="mini">via ${a.via === 'facebook' ? 'Facebook page' : 'Instagram login'}${a.status !== 'ok' ? ' · reconnect needed' : ''}</span></span>${x.linked && x.linked.id === a.id ? chip('Linked', 'ok', false) : ''}</button>`).join('');
  modal(`<h2 style="display:flex;align-items:center;gap:10px">${dstIcon('instagram')}Instagram for ${esc(c.title)}</h2>
    <p class="muted" style="margin:0">Posts as Reels, photos and carousels through Meta’s official publishing API. Needs a <b>Professional</b> (Business or Creator) account · about 50 posts a day.</p>
    <div style="display:flex;flex-direction:column;gap:8px;margin:14px 0">${opts || `<div class="empty" style="padding:14px">No Instagram account on Pool ${c.group.number} yet.</div>`}</div>
    <div class="actions">${x.fb_ready ? `<button class="btn sm" data-act="igfind">${ic('search', 14)}Find via Facebook pages</button>` : ''}${x.app_ready ? `<button class="btn sm" data-act="iglogin">${ic('ig', 14)}Connect with Instagram login</button>` : `<a class="btn sm" href="#/settings">Add Instagram app in Settings</a>`}${x.linked ? `<button class="btn sm danger" data-act="igunlink">Unlink</button>` : ''}</div>
    <div class="actions" style="justify-content:flex-end"><button class="btn" data-act="close">Close</button></div>`);
  document.querySelectorAll('[data-igd]').forEach(b => b.addEventListener('click', async () => {
    await api('POST', `/api/instagram/destinations/${b.dataset.igd}/assign`, { channel_id: c.id }); closeModal(); toast('Instagram linked'); render();
  }));
  Object.assign(S.acts, {
    igfind: async () => { try { const r = await api('POST', `/api/instagram/destinations/discover/${c.group.id}`); toast(r.found ? `${r.found} account(s) found` : 'No Instagram account is linked to these pages', !r.found); } catch (e) { toast(e.message, true); } closeModal(); const nd = await api('GET', `/api/channels/${c.id}/detail`); igDestModal(c, nd); },
    iglogin: () => connectIgLogin(c.group.id, async () => { const nd = await api('GET', `/api/channels/${c.id}/detail`); igDestModal(c, nd); }),
    igunlink: async () => { await api('POST', `/api/instagram/destinations/${x.linked.id}/assign`, { channel_id: null }); closeModal(); render(); },
  });
}

// ------------------------------------------------------------------ schedule / videos
async function pageSchedule() {
  const days = S.schedDays || 3;
  const { slots, live } = await api('GET', `/api/schedule?days=${days}`);
  const now = Date.now() / 1000;
  const ST = {
    planned: ['Planned', 'neutral', 'cal'], queued: ['Starting', 'info', 'bolt'], running: ['Posting', 'info', 'bolt'],
    done: ['Published', 'ok', 'check'], missed: ['Missed', 'warn', 'alert'], held: ['Held', 'warn', 'alert'],
    skipped: ['Skipped', 'neutral', 'alert'], failed: ['Failed', 'bad', 'alert'], uncertain: ['Check Studio', 'bad', 'alert'],
  };
  const upcoming = slots.filter(s => ['planned', 'queued', 'running'].includes(s.status) && s.due_at >= now - 300);
  const next = upcoming[0];
  const liveRows = Object.entries(live || {});
  const byDay = new Map();
  slots.filter(s => s.due_at >= now - 86400 * 0.6 || ['done', 'running'].includes(s.status)).forEach(s => {
    const k = fmt(s.due_at, { year: 'numeric', month: '2-digit', day: '2-digit' });
    if (!byDay.has(k)) byDay.set(k, { label: tDay(s.due_at), first: s.due_at, list: [] });
    byDay.get(k).list.push(s);
  });
  const todayKey = fmt(now, { year: 'numeric', month: '2-digit', day: '2-digit' });
  const tChip = s => s.market === 'UK' ? chip('UK audience', 'coralc', false) : s.market === 'US' ? chip('US audience', 'info', false) : chip(s.market + ' audience', 'neutral', false);
  const row = s => {
    const st = ST[s.status] || [s.status, 'neutral', 'cal'];
    const past = s.due_at < now && !['queued', 'running'].includes(s.status);
    const lv = live && live[s.channel_id] && ['queued', 'running'].includes(s.status) ? live[s.channel_id] : null;
    const right = s.status === 'done' && s.external_video_id ? `<a class="btn sm" href="https://youtu.be/${esc(s.external_video_id)}" target="_blank" rel="noopener">${ic('ext', 14)}Watch</a>`
      : s.status === 'planned' && s.due_at > now ? `<b class="mono" data-count="${s.due_at}" style="font-size:13px">${countdown(s.due_at)}</b>` : '';
    return `<div class="slot ${past ? 'past' : ''} ${s.status}">
      <div class="slot-time"><b class="mono">${esc(tTime(s.due_at))}</b>${tChip(s)}</div>
      <div class="slot-body"><div class="who">${avatar(s.title, hashI(s.title), s.thumbnail, 34)}<div><b class="ell">${esc(s.title)}</b><span class="muted" style="font-size:12px">Pool ${esc(s.group_number ?? '—')}${s.group_country ? ' · ' + flag(s.group_country) + ' ' + esc(s.group_country) : ''}${s.video_title ? ' · “' + esc(s.video_title) + '”' : s.note && s.status !== 'planned' ? ' · ' + esc(s.note) : ''}</span></div></div>
        ${lv ? liveBar(lv) : ''}</div>
      <div class="slot-side"><span class="chip ${st[1]}">${ic(st[2], 13)}${st[0]}</span>${right}</div></div>`;
  };
  const cols = [...byDay.entries()].map(([k, d]) => {
    const done = d.list.filter(s => s.status === 'done').length, total = d.list.length;
    const isToday = k === todayKey;
    let body = '';
    let nowDrawn = !isToday;
    d.list.forEach(s => {
      if (!nowDrawn && s.due_at > now) { body += `<div class="nowline"><span>Now · ${esc(tTime(now))}</span></div>`; nowDrawn = true; }
      body += row(s);
    });
    if (!nowDrawn) body += `<div class="nowline"><span>Now · ${esc(tTime(now))}</span></div>`;
    return `<section class="card daycol ${isToday ? 'today-col' : ''}"><div class="dayhead"><div><span class="kicker" style="color:var(--muted)">${isToday ? 'Today' : fmt(d.first, { weekday: 'long' })}</span><h2>${esc(d.label)}</h2></div><div style="text-align:right"><b class="mono">${done}/${total}</b><div class="muted" style="font-size:12px">published</div></div></div>
      <div class="meter" style="margin:12px 0 4px"><span style="width:${total ? Math.round(100 * done / total) : 0}%;background:var(--ok)"></span></div>${body}</section>`;
  }).join('');
  const hero = next ? `<section class="today" style="display:flex;flex-wrap:wrap;gap:22px;align-items:center;justify-content:space-between">
      <div style="display:flex;flex-direction:column;gap:8px"><span class="onair"><i></i>NEXT UP</span><div class="big" style="color:#fff;font-size:44px" data-count="${next.due_at}">${countdown(next.due_at)}</div><span style="color:#A9AFBA">${esc(tFull(next.due_at))} · ${esc(S.tz)}</span></div>
      <div style="display:flex;align-items:center;gap:14px;flex:1 1 280px">${avatar(next.title, hashI(next.title), next.thumbnail, 52)}<div style="display:flex;flex-direction:column;gap:4px;min-width:0"><b style="font-size:18px" class="ell">${esc(next.title)}</b><span style="color:#A9AFBA;font-size:13px">Pool ${esc(next.group_number ?? '—')}${next.group_country ? ' · ' + flag(next.group_country) + ' ' + esc(next.group_country) : ''} · ${esc(next.market)} audience peak</span></div></div>
      ${next.status === 'planned' ? `<button class="btn coral" data-act="spost" data-id="${next.channel_id}">${ic('bolt', 16)}Post now</button>` : chip('Posting', 'info')}</section>` : '';
  const liveHtml = liveRows.length ? `<section class="card" style="margin-bottom:16px">${hd('Posting right now', 'Live progress from the phone')}${liveRows.map(([cid, p]) => { const s = slots.find(x => String(x.channel_id) === cid) || {}; return `<div style="display:grid;grid-template-columns:minmax(140px,220px) 1fr;gap:16px;align-items:center;padding:10px 0;border-top:1px solid var(--line)"><b class="ell">${esc(s.title || 'Channel')}</b>${liveBar(p)}</div>`; }).join('')}</section>` : '';
  const counts = k => slots.filter(s => s.status === k && fmt(s.due_at, { year: 'numeric', month: '2-digit', day: '2-digit' }) === todayKey).length;
  const pills = `<div class="grid g-auto" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr))">${[['Published today', counts('done'), 'ok'], ['Still to post today', counts('planned') + counts('queued') + counts('running'), 'info'], ['Held', counts('held'), 'warn'], ['Missed or failed', counts('missed') + counts('failed') + counts('uncertain'), 'bad']].map(([l, n, t]) => `<section class="card" style="padding:16px 18px;display:flex;align-items:center;justify-content:space-between"><span class="muted" style="font-size:13px">${l}</span><span class="chip ${t}" style="font-size:15px;height:30px;padding:0 12px">${n}</span></section>`).join('')}</div>`;
  mount('schedule', 'Schedule', `Natural times inside each audience’s peak hours — shown in your time (${esc(S.tz)})`, 'Workspace',
    `<div class="seg">${[3, 7].map(n => `<button class="${n === days ? 'on' : ''}" data-act="sdays" data-n="${n}">${n} days</button>`).join('')}</div>`,
    slots.length ? `${hero}${liveHtml}${pills}<div class="daygrid">${cols}</div>` : '<div class="empty"><h3>Nothing planned</h3>Add a channel to see its posting times here.<div style="margin-top:14px">' + addBtn + '</div></div>');
  every(1000, () => document.querySelectorAll('[data-count]').forEach(el => { el.textContent = countdown(+el.dataset.count); }));
  every(liveRows.length ? 4000 : 60000, () => { if (!document.getElementById('modal')) render(); });
  S.acts.sdays = d => { S.schedDays = +d.n; render(); };
  S.acts.spost = async d => { await api('POST', `/api/channels/${d.id}/post-now`); toast('Posting now'); render(); };
}
async function pageVideos() {
  const { videos } = await api('GET', '/api/videos?limit=200');
  const tone = { published: 'ok', uploading: 'info', reserved: 'info', failed: 'bad', uncertain: 'bad' };
  const VC = 'grid-template-columns:minmax(0,2.2fr) minmax(0,1.2fr) minmax(0,1fr) 96px 44px';
  const where = v => { const k = (v.destination || 'youtube').split(':')[0]; const kk = DST[k] ? k : 'youtube'; return { kind: kk, name: { youtube: 'YouTube', facebook: 'Facebook', instagram: 'Instagram' }[kk] }; };
  const link = v => { const w = where(v).kind, id = v.external_video_id; if (!id) return ''; if (w === 'youtube') return `https://youtu.be/${id}`; if (w === 'facebook') return `https://www.facebook.com/${id}`; return ''; };
  const rows = videos.map(v => { const w = where(v), href = link(v);
    return `<div class="tr vrow" style="${VC}"><div class="vt">${dstIcon(w.kind, 14)}<div style="min-width:0"><b class="ell" style="display:block">${esc(v.title)}</b><span class="muted ell" style="display:block;font-size:11.5px">${esc(v.file_name)}</span></div></div>
    <div style="min-width:0"><b class="ell" style="display:block;font-weight:550">${esc(v.channel_title)}</b><span class="mini ell" style="display:block" title="The IP ${esc(w.name)} saw for this post">${v.outgoing_ip ? `${esc(v.outgoing_ip)} · Pool ${esc(v.group_number ?? '?')}${v.group_ip && v.group_ip !== v.outgoing_ip ? ' (old IP)' : ''}` : 'not sent'}</span></div>
    <div style="min-width:0;display:flex;flex-direction:column;gap:3px;align-items:flex-start">${v.yt_state === 'rejected' ? chip('Rejected by YouTube', 'bad') : v.yt_state === 'removed' ? chip('Not on YouTube any more', 'bad') : v.status === 'failed' && /Duplicate/.test(v.error || '') ? chip('Skipped · duplicate', 'warn') : chip(v.status === 'published' ? (v.yt_state === 'processing' ? 'processing' : v.visibility || 'public') : v.status, tone[v.status] || 'neutral')}<span class="mini">${esc(tFull(v.published_at || v.reserved_at))}</span>${v.yt_reason ? `<span class="mini" style="color:var(--bad)">${esc({ duplicate: 'same video already on the channel', copyright: 'copyright', inappropriate: 'content rules', termsOfUse: 'terms of use' }[v.yt_reason] || v.yt_reason)}</span>` : v.status === 'failed' && v.error ? `<span class="mini ell" title="${esc(v.error)}">${esc(v.error)}</span>` : ''}</div>
    <div style="text-align:right"><b class="mono">${v.yt_state === 'rejected' || v.yt_state === 'removed' ? '—' : v.views == null ? (w.kind === 'youtube' && v.status === 'published' ? '<span class="mini" title="Views arrive with the next stats round (every 20 min)">soon</span>' : '—') : num(v.views)}</b>${v.likes != null ? `<span class="mini" style="display:block">${num(v.likes)} likes</span>` : ''}</div>
    <div class="actions" style="justify-content:flex-end">${href ? `<a class="tb-btn" style="width:32px;height:32px" href="${esc(href)}" target="_blank" rel="noopener" title="Open on ${esc(w.name)}" aria-label="Open on ${esc(w.name)}">${ic('ext', 14)}</a>` : ''}${v.status === 'uncertain' ? `<button class="btn sm" data-act="unc" data-id="${v.id}">Resolve</button>` : ''}</div></div>`; }).join('');
  const tot = videos.filter(v => v.status === 'published' && v.yt_state !== 'rejected' && v.yt_state !== 'removed');
  const sum = tot.reduce((n, v) => n + (v.views || 0), 0);
  mount('videos', 'Videos', `${plural(tot.length, 'post')} published · ${num(sum)} views on them · every post leaves only from its pool’s proxy IP`, 'Workspace', '',
    `<section class="card">${videos.length ? `<div class="tbl"><div style="min-width:680px"><div class="tr th" style="${VC}"><span>Video</span><span>Client · sent from</span><span>Status</span><span style="text-align:right">Views</span><span></span></div>${rows}</div></div>` : '<div class="empty"><h3>No videos yet</h3>Published posts will appear here with their views.</div>'}</section>`);
  S.acts.unc = d => {
    modal(`<h2>Was it published?</h2><p class="muted">The upload ended without a clear answer, so DriveCast did not retry it. Check YouTube Studio for this channel.</p>
      <form data-form="uncok" style="display:flex;flex-direction:column;gap:12px"><label class="f">If it is on YouTube, paste the video link or ID<input class="i" name="video_id" placeholder="https://youtu.be/…"></label>
      <div class="actions"><button class="btn primary" type="submit">It is on YouTube</button><button class="btn" type="button" data-act="uncno">It is not on YouTube</button></div></form>`);
    S.acts.uncok = async v => { const id = (v.video_id || '').trim().replace(/^.*(?:youtu\.be\/|v=|shorts\/)([A-Za-z0-9_-]{6,}).*$/, '$1'); if (!id) throw new Error('Paste the link or ID'); await api('POST', `/api/uploads/${d.id}/resolve`, { video_id: id }); closeModal(); render(); };
    S.acts.uncno = async () => { await api('POST', `/api/uploads/${d.id}/resolve`, {}); closeModal(); toast('Marked as not published. Move the file from Processing back to the folder to post it again.'); render(); };
  };
}

// ------------------------------------------------------------------ drive
async function pageDrive() {
  const d = await api('GET', '/api/drive');
  const conn = d.connected ? `<div style="display:flex;align-items:center;gap:12px">${chip('Connected', 'ok')}<b>${esc(d.email || '')}</b><button class="btn sm" data-act="dconnect">Reconnect</button></div>`
    : `<button class="btn primary" data-act="dconnect" ${d.app_ready ? '' : 'disabled'}>${ic('drive', 16)}Connect Google Drive</button>${d.app_ready ? '' : '<p class="help">Add your Google API app in Settings first.</p>'}`;
  const folders = d.folders.map(f => `<div class="tr" style="grid-template-columns:minmax(0,1fr) 160px">${`<div style="display:flex;gap:10px;align-items:center;min-width:0">${ic('folder', 18)}<div style="min-width:0"><b class="ell" style="display:block">${esc(f.name)}</b><span class="muted" style="font-size:12px">${esc(f.path)}</span></div></div>`}<span style="text-align:right">${f.used ? chip('Linked', 'ok') : '<a class="btn sm" href="#/add">Use for a channel</a>'}</span></div>`).join('');
  mount('drive', 'Google Drive', 'One main folder · one subfolder per channel', 'Workspace', d.root_id ? `<button class="btn" data-act="drefresh">${ic('refresh', 16)}Refresh folders</button>` : '',
    `<div class="note info" style="margin-bottom:16px">${ic('folder', 18)}<span><b>One project folder → one subfolder per channel → YouTube.</b> Videos move to Processing, Done or Failed inside each subfolder. Title = file name.</span></div>
     <section class="card" style="margin-bottom:16px">${hd('1. Connect your Drive account', 'Authorize once. Use the Google account that can edit your main folder.')}${conn}</section>
     <section class="card" style="margin-bottom:16px">${hd('2. Main folder', d.root_name ? `Current: <b>${esc(d.root_name)}</b>` : 'Paste the link of the folder that holds every channel folder.')}
       <form data-form="droot" class="actions"><input class="i" name="folder" style="flex:1 1 320px" placeholder="https://drive.google.com/drive/folders/…" value="${esc(d.root_id || '')}" ${d.connected ? '' : 'disabled'}><button class="btn primary" type="submit" ${d.connected ? '' : 'disabled'}>Save</button></form></section>
     <section class="card">${hd('3. Channel folders', d.scanned_at ? `${d.folders.length} folders · scanned ${esc(rel(d.scanned_at))}${d.error ? ` · <span style="color:var(--bad)">last scan failed: ${esc(d.error)}</span>` : ''}` : 'Appear here after the main folder is saved.')}${folders || '<div class="empty">No folders yet.</div>'}</section>`);
  S.acts.drefresh = async () => { await api('POST', '/api/drive/refresh'); render(); };
  S.acts.droot = async v => { await api('POST', '/api/drive/root', v); toast('Main folder saved'); render(); };
  S.acts.dconnect = async () => {
    const l = await api('POST', '/api/connect', { purpose: 'drive' });
    window.open(l.url, '_blank', 'noopener');
    toast('Finish sign-in in the new tab');
    every(3000, async () => { const s = await api('GET', `/api/connect/${l.code}`).catch(() => null); if (s && s.status === 'done') { clearTimers(); render(); } });
  };
}

// ------------------------------------------------------------------ settings
const TZS = ['Asia/Karachi', 'Asia/Dubai', 'Asia/Kolkata', 'Asia/Riyadh', 'Europe/London', 'Europe/Berlin', 'America/New_York', 'America/Chicago', 'America/Los_Angeles', 'Australia/Sydney', 'UTC'];
// Guides send you straight to the right screen
function deep(url, label) { return `<a class="deep" href="${esc(url)}" target="_blank" rel="noopener">${esc(label)}${ic('ext', 12)}</a>`; }
async function pageSettings() {
  const s = await api('GET', '/api/settings');
  const meta = (path, label) => deep(s.fb_app_id ? `https://developers.facebook.com/apps/${encodeURIComponent(s.fb_app_id)}/${path}` : 'https://developers.facebook.com/apps/', label);
  const tzs = [...new Set([s.timezone, browserTz(), ...TZS])];
  mount('settings', 'Settings', 'Your apps (Google, Meta), timezone and password', 'Workspace', '',
    `<div class="row"><div class="f3"><section class="card">${hd('Google API app', 'Each account uses its own Google Cloud OAuth app. This keeps quotas and limits separate.')}
      <ol class="muted" style="font-size:13px;line-height:1.8;margin:0 0 16px;padding-left:18px">
        <li>${deep('https://console.cloud.google.com/projectcreate', 'Create a project')} (or pick yours).</li>
        <li>Enable ${deep('https://console.cloud.google.com/apis/library/youtube.googleapis.com', 'YouTube Data API v3')} and ${deep('https://console.cloud.google.com/apis/library/drive.googleapis.com', 'Google Drive API')}.</li>
        <li>${deep('https://console.cloud.google.com/apis/credentials/consent', 'OAuth consent screen')} → External → publishing status <b>In production</b> (Testing tokens expire after 7 days).</li>
        <li>${deep('https://console.cloud.google.com/apis/credentials/oauthclient', 'Create OAuth client')} → <b>Web application</b> → add the redirect URI below.</li>
        <li>Paste the Client ID and Client secret here.</li></ol>
      <div class="linkbox" style="margin-bottom:16px"><span class="kicker">Authorized redirect URI</span><div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><span class="mono" style="flex:1 1 260px;word-break:break-all">${esc(s.redirect_uri)}</span><button class="btn coral" data-act="copy" data-text="${esc(s.redirect_uri)}">${ic('copy', 16)}Copy</button></div></div>
      <form data-form="gapp" style="display:flex;flex-direction:column;gap:12px"><label class="f">Client ID<input class="i" name="google_client_id" value="${esc(s.google_client_id || '')}" placeholder="…apps.googleusercontent.com" autocomplete="off"></label>
      <label class="f">Client secret<input class="i" name="google_client_secret" type="password" placeholder="${s.has_secret ? 'Saved — leave empty to keep' : ''}" autocomplete="off"></label><div class="actions"><button class="btn primary" type="submit">Save app</button>${s.google_client_id && s.has_secret ? chip('Saved · secret encrypted', 'ok') : ''}</div></form></section>
      <section class="card" style="margin-top:16px">${hd('Google app for Drive — optional, recommended', 'A second Google app used only for your own Drive. Then the app your clients see asks only for YouTube, which makes Google verification quick (no yearly security audit for Drive).', s.drive_uses_own_app ? chip('Drive uses it', 'ok') : s.drive_client_id ? chip('Saved — reconnect Drive to use it', 'warn') : '')}
      <ol class="muted" style="font-size:13px;line-height:1.8;margin:0 0 16px;padding-left:18px"><li>${deep('https://console.cloud.google.com/projectcreate', 'Create a second project')} (e.g. “DriveCast Library”), enable ${deep('https://console.cloud.google.com/apis/library/drive.googleapis.com', 'Google Drive API')}.</li><li>${deep('https://console.cloud.google.com/apis/credentials/consent', 'Consent screen')} → External → In production. It stays unverified — only you sign in, and you press “Continue” once.</li><li>${deep('https://console.cloud.google.com/apis/credentials/oauthclient', 'Create OAuth client')} → Web application → same redirect URI as above. Paste its ID and secret here, then reconnect Drive on the Google Drive page.</li><li>In your main (client) app, remove the Drive API and Drive scope from the consent screen.</li></ol>
      <form data-form="driveapp" style="display:flex;flex-direction:column;gap:12px"><div class="form"><label class="f">Drive app Client ID<input class="i" name="drive_client_id" value="${esc(s.drive_client_id || '')}" autocomplete="off"></label>
      <label class="f">Drive app Client secret<input class="i" name="drive_client_secret" type="password" placeholder="${s.has_drive_secret ? 'Saved — leave empty to keep' : ''}" autocomplete="off"></label></div>
      <div class="actions"><button class="btn primary" type="submit">Save Drive app</button><a class="btn" href="#/drive">Reconnect Drive →</a></div></form></section>
      <section class="card" style="margin-top:16px">${hd('Meta app — Facebook + Instagram', 'Only if you post to Facebook Pages or Instagram. Make your own Meta app once; then one “Allow” per Facebook ID brings all its pages and their linked Instagram accounts.')}
      <ol class="muted" style="font-size:13px;line-height:1.8;margin:0 0 16px;padding-left:18px">
        <li>${deep('https://developers.facebook.com/apps/creation/', 'Create a Meta app')} → use case <b>Other</b> → type <b>Business</b>.</li>
        <li>Add <b>Facebook Login for Business</b> ${meta('fb-login/settings/', 'Login settings')} → paste the redirect URI below.</li>
        <li>${meta('settings/basic/', 'App settings → Basic')}: copy the <b>App ID</b> and <b>App secret</b> here.</li>
        <li>${meta('roles/roles/', 'App roles')}: add each Facebook ID you connect (Admin/Tester) while the app is in development. Public use later needs App Review for pages_manage_posts and instagram_content_publish.</li></ol>
      <div class="linkbox" style="margin-bottom:16px"><span class="kicker">Valid OAuth redirect URI</span><div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><span class="mono" style="flex:1 1 260px;word-break:break-all">${esc(s.fb_redirect_uri)}</span><button class="btn coral" data-act="copy" data-text="${esc(s.fb_redirect_uri)}">${ic('copy', 16)}Copy</button></div></div>
      <form data-form="fbapp" style="display:flex;flex-direction:column;gap:12px"><div class="form"><label class="f">App ID<input class="i" name="fb_app_id" value="${esc(s.fb_app_id || '')}" autocomplete="off"></label>
      <label class="f">App secret<input class="i" name="fb_app_secret" type="password" placeholder="${s.has_fb_secret ? 'Saved — leave empty to keep' : ''}" autocomplete="off"></label>
      <label class="f">Graph API version<input class="i mono" name="fb_graph_version" value="${esc(s.fb_graph_version || '')}" placeholder="v21.0"><span class="help">Change only if Meta’s posting fields change.</span></label></div>
      <div class="actions"><button class="btn primary" type="submit">Save Facebook app</button>${s.fb_app_id && s.has_fb_secret ? chip('Saved', 'ok') : ''}<a class="btn" href="#/groups">Connect a Facebook ID →</a></div></form></section>
      <section class="card" style="margin-top:16px">${hd('Instagram login — optional', 'Only for Instagram accounts that are not linked to a Facebook page. Instagram accounts linked to a page need nothing here — they come with Facebook.')}
      <ol class="muted" style="font-size:13px;line-height:1.8;margin:0 0 16px;padding-left:18px">
        <li>In the same Meta app ${meta('dashboard/', 'dashboard')} → add the <b>Instagram</b> product → <b>API setup with Instagram login</b>.</li>
        <li>Under “Set up Instagram business login” paste the redirect URI below, then copy the <b>Instagram app ID</b> and <b>Instagram app secret</b> here.</li>
        <li>The client’s account must be ${deep('https://help.instagram.com/502981923235522', 'Professional (Business or Creator)')}.</li></ol>
      <div class="linkbox" style="margin-bottom:16px"><span class="kicker">Instagram redirect URI</span><div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><span class="mono" style="flex:1 1 260px;word-break:break-all">${esc(s.ig_redirect_uri)}</span><button class="btn coral" data-act="copy" data-text="${esc(s.ig_redirect_uri)}">${ic('copy', 16)}Copy</button></div></div>
      <form data-form="igapp" style="display:flex;flex-direction:column;gap:12px"><div class="form"><label class="f">Instagram app ID<input class="i" name="ig_app_id" value="${esc(s.ig_app_id || '')}" autocomplete="off"></label>
      <label class="f">Instagram app secret<input class="i" name="ig_app_secret" type="password" placeholder="${s.has_ig_secret ? 'Saved — leave empty to keep' : ''}" autocomplete="off"></label></div>
      <div class="actions"><button class="btn primary" type="submit">Save Instagram app</button>${s.ig_app_id && s.has_ig_secret ? chip('Saved', 'ok') : ''}</div></form></section></div>
    <div class="f1"><section class="card" style="margin-bottom:16px">${hd('Your timezone', 'Every time in the panel is shown in this zone.')}<form data-form="tz" class="actions"><select class="i" name="timezone" style="flex:1">${tzs.map(t => `<option ${t === s.timezone ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select><button class="btn primary" type="submit">Save</button></form></section>
      <section class="card" style="margin-bottom:16px">${hd('Change password', 'Signed up with Google? Use “Forgot password?” on the sign-in page to set one.')}<form data-form="pw" style="display:flex;flex-direction:column;gap:12px"><span class="pw"><input class="i" type="password" name="current_password" placeholder="Current password" autocomplete="current-password" aria-label="Current password"><button type="button" class="pw-eye" data-act="pweye" aria-label="Show password">${ic('eye', 16)}</button></span><span class="pw"><input class="i" type="password" name="new_password" placeholder="New password (8+)" autocomplete="new-password" aria-label="New password (8+)"><button type="button" class="pw-eye" data-act="pweye" aria-label="Show password">${ic('eye', 16)}</button></span><button class="btn" type="submit">Update password</button></form></section>
      ${S.user.role === 'owner' ? `<div id="tgcard"></div><div id="mailcard"></div>` : ''}
      <section class="card">${hd('Your link')}<span class="mono" style="word-break:break-all">${esc(s.public_url)}</span>
        ${S.user.role === 'owner' ? `<details style="margin-top:12px"><summary class="muted" style="cursor:pointer;font-size:12px">Advanced · own domain</summary><form data-form="advurl" style="display:flex;flex-direction:column;gap:8px;margin-top:10px"><input class="i" name="public_url_override" placeholder="https://studio.yourdomain.com" aria-label="Own domain"><span class="help">Only if you point your own domain at this server later. Leave empty to use the built-in link. Add the new redirect URI to your Google app too.</span><button class="btn sm" type="submit">Save</button></form></details>` : ''}</section></div></div>`);
  if (S.user.role === 'owner') api('GET', '/api/settings/advanced').then(a => { const i = document.querySelector('[name=public_url_override]'); if (i) i.value = a.public_url_override || ''; }).catch(() => { });
  S.acts.advurl = async v => { await api('POST', '/api/settings/advanced', v); toast(v.public_url_override ? 'Own domain saved' : 'Using the built-in link'); render(); };
  S.acts.gapp = async v => { if (!v.google_client_secret) delete v.google_client_secret; await api('POST', '/api/settings', v); toast('Google app saved'); render(); };
  S.acts.driveapp = async v => { if (!v.drive_client_secret) delete v.drive_client_secret; await api('POST', '/api/settings', v); toast('Drive app saved — reconnect Drive to use it'); render(); };
  S.acts.igapp = async v => { if (!v.ig_app_secret) delete v.ig_app_secret; await api('POST', '/api/settings', v); toast('Instagram app saved'); render(); };
  S.acts.fbapp = async v => { if (!v.fb_app_secret) delete v.fb_app_secret; await api('POST', '/api/settings', v); toast('Facebook app saved'); render(); };
  S.acts.tz = async v => { await api('POST', '/api/settings', v); S.tz = v.timezone; S.user.timezone = v.timezone; toast('Timezone saved'); render(); };
  if (S.user.role === 'owner') { mailCard(); tgCard(); }
  S.acts.pw = async (v, f) => { await api('POST', '/api/settings', v); f.reset(); toast('Password updated'); };
}

// ------------------------------------------------------------------ admin: users
async function pageUsers(id) {
  if (id) return pageUserReport(id);
  const [{ users }, { invites }] = await Promise.all([api('GET', '/api/admin/users'), api('GET', '/api/admin/invites')]);
  const COLS = 'grid-template-columns:minmax(220px,1.6fr) 90px 90px 90px 100px 130px 96px';
  const rows = users.map((u, i) => {
    const st = u.status === 'suspended' ? chip('Suspended', 'bad') : u.groups_down ? chip('Proxy down', 'warn') : u.role === 'student' && !u.groups_n ? chip('Needs proxy', 'warn') : chip('Active', 'ok');
    const inner = `<div class="who">${avatar(u.name, i, null, 36)}<div><b>${esc(u.name)}</b><span class="mono muted" style="font-size:11px">${esc(u.email)}</span></div></div><span>${u.role === 'owner' ? 'Owner' : 'Student'}</span><span class="mono">${u.channels}</span><span class="mono">${u.groups_n}</span><span class="mono">${u.today}</span><span>${st}</span><span class="actions" style="justify-content:flex-end;gap:6px">${u.role === 'owner' ? '' : `<a class="btn sm icon-btn" href="#/users/${u.id}" target="_blank" rel="noopener" aria-label="Open report for ${esc(u.name)}" title="30-day report">${ic('ext', 16)}</a><button class="btn sm icon-btn danger" data-act="udel" data-id="${u.id}" data-name="${esc(u.name)}" aria-label="Delete ${esc(u.name)}" title="Delete user">${ic('trash', 16)}</button>`}</span>`;
    return `<div class="tr" style="${COLS}">${inner}</div>`;
  }).join('');
  const now = Date.now() / 1000;
  const inv = invites.map(k => { const s = k.used_by ? ['Used', 'neutral'] : k.expires_at < now ? ['Expired', 'warn'] : ['Unused', 'info']; return `<div style="display:flex;align-items:center;gap:12px;padding:12px 0;border-top:1px solid var(--line);flex-wrap:wrap"><div style="flex:1 1 200px"><b>${esc(k.label)}</b><div class="muted" style="font-size:12px">${k.used_by ? `Registered by ${esc(k.used_name)} · ${esc(tFull(k.used_at))}` : `Created ${esc(rel(k.created_at))} · expires ${esc(rel(k.expires_at))}`}</div></div><span class="mono" style="font-weight:500">${esc(k.code)}</span>${chip(s[0], s[1], false)}${!k.used_by ? `<button class="btn sm" data-act="copy" data-text="${esc(k.code)}">${ic('copy', 14)}</button><button class="btn sm danger" data-act="revoke" data-code="${esc(k.code)}">Revoke</button>` : ''}</div>`; }).join('');
  mount('users', 'Users', 'Every account, on the server that holds it — drag a card to move someone', 'Admin', `<button class="btn primary" data-act="newcode">${ic('key', 16)}New code</button>`,
    `<div id="upend"></div><div id="uboard"></div><section class="card" style="margin-bottom:16px">${hd('Accounts on this server', 'Open a student to see their 30-day report (new tab). You see usage — never their passwords or tokens.')}<div class="tbl"><div style="min-width:900px"><div class="tr th" style="${COLS}"><span>User</span><span>Role</span><span>Channels</span><span>Pools</span><span>Today</span><span>Status</span><span></span></div>${rows}</div></div></section>
     <section class="card">${hd('Invite codes', 'One code = one account. It turns Used the moment the student registers.')}${inv || '<div class="empty">No codes yet. Press “New code”.</div>'}</section>`);
  drawBoard(); drawPending();
  S.acts.udel = d => deleteUserModal(d.id, d.name, () => render());
  S.acts.revoke = async d => { await api('POST', `/api/admin/invites/${d.code}/revoke`); render(); };
  S.acts.newcode = () => {
    modal(`<h2>New invite code</h2><p class="muted" style="margin:0">Who is this for? Only you see this name.</p><form data-form="mkcode" style="display:flex;flex-direction:column;gap:14px">
      <label class="f">Name or note<input class="i" name="label" required placeholder="e.g. Usman — Batch 3"></label>
      <div class="form" style="grid-template-columns:repeat(3,minmax(0,1fr))"><label class="f">Max channels<input class="i" name="max_channels" type="number" min="1" value="5"></label><label class="f">Max Pools<input class="i" name="max_groups" type="number" min="1" value="2"></label><label class="f">Posts / day cap<input class="i" name="daily_cap" type="number" min="1" value="20"></label></div>
      <div class="actions" style="justify-content:flex-end"><button class="btn" type="button" data-act="close">Cancel</button><button class="btn primary" type="submit">Generate code</button></div></form>`);
    S.acts.mkcode = async v => {
      const r = await api('POST', '/api/admin/invites', { label: v.label, limits: { max_channels: v.max_channels, max_groups: v.max_groups, daily_cap: v.daily_cap } });
      modal(`<span class="kicker" style="color:var(--muted)">Code for ${esc(v.label)}</span><h2>Share this code</h2><div class="linkbox" style="flex-direction:row;align-items:center;justify-content:space-between"><span class="mono" style="font-size:24px;letter-spacing:.06em">${esc(r.code)}</span><button class="btn coral" data-act="copy" data-text="${esc(r.code)}">${ic('copy', 16)}Copy</button></div>
        <div class="muted" style="font-size:13px;line-height:1.8">1 · Student opens <span class="mono" style="color:var(--ink)">${esc(location.host)}</span> and taps Create account<br>2 · Enters this code → account is active, code becomes Used<br>3 · Single use · expires in 7 days if unused</div><button class="btn primary" data-act="donecode">Done</button>`);
      S.acts.donecode = () => { closeModal(); render(); };
    };
  };
}
function deleteUserModal(id, name, done) {
  modal(`<h2>Delete ${esc(name)}?</h2><p class="muted" style="margin:0">This removes their account, channels, Pools, schedule and history from DriveCast. Videos already on YouTube and files in their Drive are not touched. This cannot be undone.</p>
    <form data-form="udelok" style="display:flex;flex-direction:column;gap:12px"><label class="f">Type <b>${esc(name)}</b> to confirm<input class="i" name="confirm" autocomplete="off"></label>
    <div class="actions" style="justify-content:flex-end"><button class="btn" type="button" data-act="close">Cancel</button><button class="btn primary" type="submit" style="background:var(--bad);border-color:var(--bad)">Delete account</button></div></form>`);
  S.acts.udelok = async v => {
    if ((v.confirm || '').trim() !== name.trim()) throw new Error('The name does not match');
    await api('POST', `/api/admin/users/${id}/delete`);
    closeModal(); toast(`${name} was deleted`); done();
  };
}
async function pageUserReport(id) {
  const days = S.repDays || 30;
  const r = await api('GET', `/api/admin/users/${id}/report?days=${days}`);
  const u = r.user, k = r.kpis;
  const sum = r.trend.views.reduce((a, b) => a + b, 0);
  const published = r.status.published || 0, failed = (r.status.failed || 0) + (r.status.uncertain || 0);
  const stat = (a, b, c) => `<section class="card" style="padding:18px 20px"><span class="muted" style="font-size:13px">${a}</span><div class="big" style="font-size:30px;margin:6px 0">${b}</div><span class="muted" style="font-size:12px">${c}</span></section>`;
  const chans = r.channels.map(c => `<div class="tr" style="grid-template-columns:minmax(200px,1.6fr) 100px 100px 90px 150px 100px"><div class="who">${avatar(c.title, hashI(c.title), null, 30)}<div><b class="ell">${esc(c.title)}</b>${handle(c.handle)}</div></div><span>${c.group_number ? chip('Group ' + c.group_number, 'info', false) : '—'}</span><span class="mono">${num(c.views)}</span><span class="mono">${c.uploads}</span><span style="font-size:13px">${c.last_post ? esc(rel(c.last_post)) : '—'}</span><span>${chip(c.state, c.state === 'live' ? 'ok' : 'neutral', false)}</span></div>`).join('');
  const lim = u.limits || {};
  mount('users', u.name, `${esc(u.email)} · member since ${esc(tDay(u.created_at))}${u.invite_code ? ' · invite ' + esc(u.invite_code) : ''}`, `Student · ${days}-day report`,
    `<div class="seg">${[7, 30, 90].map(n => `<button class="${n === days ? 'on' : ''}" data-act="repd" data-n="${n}">${n}D</button>`).join('')}</div>${u.status === 'active' ? chip('Active', 'ok') : chip('Suspended', 'bad')}<a class="btn" href="#/users">${ic('back', 16)}All users</a>`,
    `<div class="grid g-auto">${stat(`Views · ${days} days`, num(sum), 'all channels')}${stat(`Published · ${days} days`, published, `${failed} failed or unclear`)}${stat('Channels', r.channels.length, lim.max_channels ? `limit ${lim.max_channels}` : 'no limit')}${stat('Pools', r.groups.length, r.groups.some(g => g.status === 'down') ? 'one is down' : 'all healthy')}${stat('Avg views · first day', k.avg_first24 == null ? '—' : num(k.avg_first24), 'per post')}</div>
     <div class="row"><div class="f2"><section class="card">${hd('Daily views', 'From this user’s workspace')}${r.trend.views.some(v => v) ? areaChart(r.trend.days, r.trend.views, null, 'Daily views') : '<div class="empty">No stats yet.</div>'}</section></div>
       <div class="f1"><section class="card" style="height:100%">${hd('Uploads per day')}${barsChart(r.daily_uploads.slice(-30), 'published', null, 110)}</section></div></div>
     <div class="row"><div class="f3"><section class="card">${hd('Channels', 'Ranked by views in this period')}<div class="tbl"><div style="min-width:760px"><div class="tr th" style="grid-template-columns:minmax(200px,1.6fr) 100px 100px 90px 150px 100px"><span>Channel</span><span>Pool</span><span>Views</span><span>Uploads</span><span>Last post</span><span>State</span></div>${chans || '<div class="empty">No channels.</div>'}</div></div></section></div>
       <div class="f1"><section class="card">${hd('Limits & access')}<form data-form="lim" style="display:flex;flex-direction:column;gap:10px"><label class="f">Max channels<input class="i" name="max_channels" type="number" min="1" value="${esc(lim.max_channels || '')}"></label><label class="f">Max Pools<input class="i" name="max_groups" type="number" min="1" value="${esc(lim.max_groups || '')}"></label><label class="f">Posts / day cap<input class="i" name="daily_cap" type="number" min="1" value="${esc(lim.daily_cap || '')}"></label><div class="actions"><button class="btn" type="submit">Save limits</button><button class="btn" type="button" data-act="rlink">Send reset link</button><button class="btn" type="button" data-act="rpw">Temporary password</button><button class="btn danger" type="button" data-act="udel2">Delete</button><button class="btn ${u.status === 'active' ? 'danger' : 'primary'}" type="button" data-act="susp">${u.status === 'active' ? 'Suspend' : 'Activate'}</button></div></form></section></div></div>
     <section class="card">${hd('Activity')}${r.events.map(e => `<div style="display:flex;gap:14px;padding:10px 0;border-top:1px solid var(--line)"><span class="mono muted" style="font-size:12px;min-width:140px">${esc(tFull(e.at))}</span><span style="font-size:13px">${esc(e.message)}</span></div>`).join('') || '<div class="empty">No activity yet.</div>'}</section>`);
  S.acts.repd = d => { S.repDays = +d.n; render(); };
  S.acts.udel2 = () => deleteUserModal(id, u.name, () => go('users'));
  S.acts.rlink = async () => {
    const r = await api('POST', `/api/admin/users/${id}/reset-link`);
    modal(`<h2>Reset link · ${esc(r.email)}</h2><p class="muted" style="margin:0">${r.emailed ? 'Emailed to them. You can also send this link yourself (WhatsApp, etc.).' : 'Send this link to them (WhatsApp, email…). Set up Settings → Email to send it automatically next time.'} It works once, for 24 hours.</p>
      <div class="linkbox"><span class="kicker">Reset link</span><div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><span class="mono" style="flex:1 1 260px;word-break:break-all">${esc(r.url)}</span><button class="btn coral" data-act="copy" data-text="${esc(r.url)}">${ic('copy', 16)}Copy</button></div></div>
      <div class="actions" style="justify-content:flex-end"><button class="btn" data-act="close">Done</button></div>`);
  };
  S.acts.rpw = async () => {
    if (!confirm(`Give ${u.name} a new temporary password? They will be signed out.`)) return;
    const r = await api('POST', `/api/admin/users/${id}/reset-password`);
    modal(`<h2>New password for ${esc(u.name)}</h2><p class="muted" style="margin:0">Send this to them. They can change it in Settings after signing in.</p><div class="linkbox" style="flex-direction:row;align-items:center;justify-content:space-between"><span class="mono" style="font-size:22px;letter-spacing:.06em">${esc(r.temporary_password)}</span><button class="btn coral" data-act="copy" data-text="${esc(r.temporary_password)}">${ic('copy', 16)}Copy</button></div><button class="btn primary" data-act="close">Done</button>`);
  };
  S.acts.lim = async v => { await api('POST', `/api/admin/users/${id}`, { limits: v }); toast('Limits saved'); };
  S.acts.susp = async () => { const next = u.status === 'active' ? 'suspended' : 'active'; if (next === 'suspended' && !confirm(`Suspend ${u.name}? Their channels pause and they are signed out.`)) return; await api('POST', `/api/admin/users/${id}`, { status: next }); render(); };
}

// ------------------------------------------------------------------ server
// ------------------------------------------------------------------ updates without reinstalling the APK
async function drawUpdates() {
  const box = $('#upcard'); if (!box) return;
  let u; try { u = await api('GET', '/api/update'); } catch (e) { return; }
  const lt = u.latest;
  const needsApk = u.shell_api < 2;
  box.innerHTML = `<section class="card">${hd('Updates', 'New versions install themselves at night (2–5 am), never during a post. If a new version ever fails to start, the phone goes back to the previous one by itself.', chip(u.newer ? 'Update ready' : 'Up to date', u.newer ? 'info' : 'ok'))}
    <div style="display:flex;gap:24px;flex-wrap:wrap;font-size:13px;margin-bottom:12px"><div><span class="mini">Running</span><div class="mono"><b>${esc(u.version)}</b> · ${u.running_from === 'update' ? 'updated copy' : 'app copy'}</div></div><div><span class="mini">Newest</span><div class="mono"><b>${esc(lt ? lt.version : '—')}</b></div></div>${u.last ? `<div><span class="mini">Last update</span><div>${esc((JSON.parse(u.last).from || '') + ' → ' + (JSON.parse(u.last).to || ''))}</div></div>` : ''}</div>
    ${u.error ? `<div class="note warn">${ic('alert', 16)}<span>${esc(u.error)}</span></div>` : ''}
    ${needsApk && lt && lt.apk ? `<div class="note info">${ic('down', 16)}<span>Install the new app once: <a class="deep" href="${esc(lt.apk)}">download APK</a>. After that, updates come here by themselves.</span></div>` : ''}
    <div class="actions">${toggle('upauto', u.auto, 'Install updates by themselves', 'At night, checked releases from your own GitHub only')}${u.newer && !needsApk ? `<button class="btn primary" data-act="upnow">${ic('down', 16)}Update ${u.fleet_role === 'hub' ? 'this phone' : 'now'} to ${esc(lt.version)}</button>` : ''}${u.fleet_role === 'hub' && !needsApk ? `<button class="btn coral" data-act="upall">${ic('down', 16)}Update all ${u.phones} phones</button>` : ''}${u.running_from === 'update' ? '<button class="btn" data-act="upback">Use the app copy</button>' : ''}${lt && lt.apk ? `<a class="btn" href="${esc(lt.apk)}">${ic('phone', 16)}Download APK ${esc(lt.version)}</a>` : ''}<a class="btn" href="https://github.com/thenaimatullahmajeed/drivecast/releases" target="_blank" rel="noopener">${ic('ext', 15)}All releases</a></div></section>`;
  Object.assign(S.acts, {
    upauto: async () => { await api('POST', '/api/update', { auto: !u.auto }); drawUpdates(); },
    upall: async () => { if (!confirm(`Update every phone of the studio to the newest version? Each one restarts once (about 20 seconds), within 5 minutes, and waits if it is uploading.`)) return; try { const r = await api('POST', '/api/update', { all: true }); toast(r.later ? r.message : r.restarting ? 'Updating all phones — this panel reconnects by itself' : 'All phones are asked to update — this one is already up to date'); } catch (e) { toast(e.message, true); } },
    upnow: async () => { if (!confirm(`Update to ${lt.version}? The server restarts (about 20 seconds).`)) return; try { await api('POST', '/api/update', {}); toast('Updating — the panel reconnects by itself'); } catch (e) { toast(e.message, true); } },
    upback: async () => { if (confirm('Go back to the version inside the app?')) { await api('POST', '/api/update', { action: 'back_to_app' }); toast('Restarting…'); } },
  });
}

// ------------------------------------------------------------------ fleet: several phones behind drivecast.site
async function drawFleet() {
  const box = $('#flcard'); if (!box) return;
  let f; try { f = await api('GET', '/api/fleet'); } catch (e) { return; }
  const roleTxt = f.role === 'hub' ? 'Hub — serves the landing page' : f.role === 'node' ? `Joined · hub ${f.hub}` : 'Single phone';
  const devs = (f.devices || []).map(d => { const st = d.stats || {}; return `<div style="display:grid;grid-template-columns:14px minmax(140px,1.2fr) repeat(5,minmax(70px,1fr)) auto;gap:10px;align-items:center;padding:9px 0;border-top:1px solid var(--line);font-size:12.5px"><span class="beat ${d.online ? (st.replaced ? 'warn' : '') : 'off'}"></span><span style="display:flex;flex-direction:column"><b class="mono">${esc(d.host)}</b><span class="mini">${d.online ? 'online' : 'offline'} · ${d.last_seen ? esc(rel(d.last_seen)) : ''} · ${esc(d.version || '')}</span></span><span><b>${d.users}</b>/${d.max_users}<br><span class="mini">accounts</span></span><span><b>${st.live ?? '—'}</b>/${st.channels ?? '—'}<br><span class="mini">clients live</span></span><span><b>${st.posts_24h ?? '—'}</b>${st.failed_24h ? ` <span style="color:var(--bad)">· ${st.failed_24h}✕</span>` : ''}<br><span class="mini">posts 24h</span></span><span><b>${num(st.views_7d || 0)}</b><br><span class="mini">views 7d</span></span><span><b>${st.battery ?? '—'}%</b>${st.charging ? ' ⚡' : ''}<br><span class="mini">${st.temp ? st.temp + '°C' : ''}</span></span>${d.host !== f.host ? `<button class="btn sm" data-act="flhide" data-host="${esc(d.host)}">Hide</button>` : '<span class="mini">this phone</span>'}</div>`; }).join('');
  const t = f.totals;
  const totals = t ? `<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin:4px 0 12px">${[['Phones', `${t.online}/${t.phones}`, 'online'], ['Accounts', t.users, 'all phones'], ['Clients live', `${t.live}/${t.channels}`, `${t.groups} Pools`], ['Posts · 24h', t.posts_24h, t.failed_24h ? t.failed_24h + ' failed or unclear' : 'all fine'], ['Views · 7 days', num(t.views_7d), 'all phones']].map(([k, v, h]) => `<div style="padding:12px;border:1px solid var(--line);border-radius:12px"><span class="mini">${k}</span><div style="font:700 20px var(--disp)">${v}</div><span class="mini">${h}</span></div>`).join('')}</div>` : '';
  const movable = (f.groups || []).filter(g => !g.moved_to);
  const move = f.role && (f.targets || []).length && movable.length ? `<form data-form="flmove" style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:12px;padding-top:12px;border-top:1px solid var(--line)"><b style="font-size:13px">Move a pool (with all its clients) to</b><select class="i" name="host" style="max-width:220px">${f.targets.map(h => `<option>${esc(h)}</option>`).join('')}</select><select class="i" name="group_id" style="max-width:220px">${movable.map(g => `<option value="${g.id}">Pool ${g.number} · ${esc(g.outgoing_ip || '')}</option>`).join('')}</select><button class="btn sm" type="submit">Move</button><span class="help" style="flex-basis:100%">Proxy, Instagram/Facebook connections, sources and the full history move together; this phone stops posting for them. Both phones should use the same Google Drive and Google app.</span></form>` : '';
  box.innerHTML = `<section class="card">${hd('Studios · drivecast.site', 'Several phones behind one domain. The landing page sends new users to a phone with room, and returning users to the phone that holds their account.', chip(roleTxt, f.role ? 'ok' : 'neutral'))}
    ${!f.host ? `<div class="note warn">${ic('alert', 16)}<span>Turn on this phone’s <b>Own domain</b> first (phone screen), e.g. s1.drivecast.site.</span></div>` : ''}
    ${f.error ? `<div class="note bad">${ic('alert', 16)}<span>${esc(f.error)}</span></div>` : ''}
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(260px,1fr));margin:0 0 12px">
      <div style="display:flex;flex-direction:column;gap:8px">${toggle('flopen', f.open_signup, 'Open sign-ups on this phone', 'People can create an account from drivecast.site without an invite, until the limit')}
        <label class="f">Accounts limit on this phone<span style="display:flex;gap:8px"><input class="i" id="flmax" type="number" min="1" max="500" value="${f.max_users}"><button class="btn sm" data-act="flmaxsave">Save</button></span><span class="help">${f.users} account(s) now</span></label></div>
      <div style="display:flex;flex-direction:column;gap:8px">
        ${f.role === 'hub' ? `<b style="font-size:13px">Fleet key</b><div class="linkbox" style="padding:12px"><span class="mono" style="word-break:break-all;font-size:12px">${esc(f.key)}</span><button class="btn coral sm" data-act="copy" data-text="${esc(f.key)}">${ic('copy', 14)}Copy</button></div><span class="help">On every other phone: Server → Studios → Join, paste <b>${esc('https://' + f.host)}</b> and this key. Keep it private.</span>`
          : f.role === 'node' ? `<span class="help">Last reached the hub ${f.seen ? esc(rel(f.seen)) : 'not yet'}.</span><button class="btn sm danger" data-act="flleave">Leave the fleet</button>`
            : `<span class="actions"><button class="btn sm primary" data-act="flhub" ${f.host ? '' : 'disabled'}>Make this phone the hub</button></span><span class="help">Use on the phone that serves drivecast.site (the bare domain route).</span>
               <form data-form="fljoin" style="display:flex;flex-direction:column;gap:8px;margin-top:6px"><input class="i" name="hub" placeholder="Hub address, e.g. https://s1.drivecast.site" aria-label="Hub address"><input class="i" name="key" type="password" placeholder="Fleet key from the hub" aria-label="Fleet key"><button class="btn sm" type="submit" ${f.host ? '' : 'disabled'}>Join a hub</button></form>`}
      </div></div>
    ${f.role === 'hub' ? `${totals}<div>${devs || '<span class="muted">No phones yet.</span>'}</div>` : ''}${move}</section>`;
  const save = async b => { try { await api('POST', '/api/fleet', b); } catch (e) { toast(e.message, true); } drawFleet(); };
  Object.assign(S.acts, {
    flopen: () => save({ open_signup: !f.open_signup }),
    flmaxsave: () => save({ max_users: +$('#flmax').value }),
    flhub: () => save({ action: 'hub' }),
    fljoin: v => save({ action: 'join', hub: v.hub, key: v.key }),
    flleave: () => { if (confirm('Leave the fleet? This phone keeps working on its own address.')) save({ action: 'leave' }); },
    flhide: d => save({ action: 'hide', host: d.host }),
    flmove: async v => { if (!confirm(`Move this pool and all its clients to ${v.host}?`)) return; try { const r = await api('POST', '/api/fleet/move', { group_id: +v.group_id, host: v.host }); toast(`Moved · now Pool ${r.group_number} on ${v.host}${r.same_google_app ? '' : ' — reconnect YouTube there'}`); } catch (e) { toast(e.message, true); } drawFleet(); },
  });
}
async function drawDownloader() {
  const box = $('#dlcard'); if (!box) return;
  let d = {}; try { d = await api('GET', '/api/downloader'); } catch (e) { d = {}; }
  box.innerHTML = `<section class="card">${hd('Video downloader', 'Reads YouTube and TikTok sources. YouTube and TikTok change often — update here, no new app needed.', `<button class="btn sm primary" data-act="dlupd">${ic('refresh', 14)}Update downloader</button>`)}
    <div style="display:flex;gap:24px;flex-wrap:wrap;font-size:13px"><div><span class="mini">Version</span><div class="mono"><b>${esc(d.version || 'not loaded yet')}</b></div></div><div><span class="mini">Last update</span><div>${d.updated_at ? esc(rel(d.updated_at)) : 'built-in copy'}</div></div></div></section>`;
  S.acts.dlupd = async () => { box.querySelector('[data-act=dlupd]').innerHTML = '<span class="spinner"></span>Updating…'; try { const r = await api('POST', '/api/downloader/update'); toast(`Downloader updated · ${r.version}`); } catch (e) { toast(e.message, true); } drawDownloader(); };
}
async function pageServer() {
  const s = await api('GET', '/api/server');
  const svc = Object.entries(s.services).map(([n, v]) => { const age = Date.now() / 1000 - v.at; const okay = v.state !== 'error' && age < 900; return `<div style="display:flex;align-items:center;gap:12px;padding:8px 0;border-top:1px solid var(--line)"><span style="width:8px;height:8px;border-radius:9px;background:${okay ? '#1F9D55' : '#B42318'}"></span><div style="flex:1"><b>${esc(n)}</b><div class="muted" style="font-size:13px">${esc(v.state)}${v.last_error ? ' · ' + esc(v.last_error) : ''}</div></div><span class="mono muted" style="font-size:12px">${esc(rel(v.at))}</span></div>`; }).join('');
  const up = s.uptime, upTxt = `${Math.floor(up / 86400)}d ${Math.floor((up % 86400) / 3600)}h`;
  let dev = {}; try { dev = JSON.parse(s.device || '{}'); } catch (e) { /* none */ }
  const d2 = await loadDevice(true); if (d2) dev = { ...dev, ...d2 };
  const b = dev.battery, low = b != null && b <= 20 && !dev.charging;
  const battCol = low ? '#B42318' : b != null && b < 40 ? '#C27A12' : '#0B5E57';
  const tile = (label, icon, val, foot, cls = '') => `<div class="kpi ${cls}"><span class="l">${ic(icon, 13)}${label}</span><span class="v">${val}</span><span class="f">${foot}</span></div>`;
  const okSvc = Object.values(s.services).filter(v => v.state !== 'error' && Date.now() / 1000 - v.at < 900).length, allSvc = Object.keys(s.services).length;
  mount('server', 'Server', 'The phone that runs DriveCast — power, health and services', 'Admin', `<button class="btn" data-act="reload">${ic('refresh', 16)}Refresh</button>`,
    `<div class="dgrid" style="margin-bottom:12px"><div class="s12" id="upcard"></div></div><div class="kp">
      ${tile('Battery', 'batt', b != null ? b + '%' : '—', b == null ? 'not reported yet' : `<div class="runway" style="flex:1"><span style="width:${b}%;background:${battCol}"></span></div>`, low ? 'alert' : '')}
      ${tile('Power', 'bolt', dev.charging == null ? '—' : dev.charging ? '<span style="color:var(--ok)">Charging</span>' : '<span style="color:var(--warn)">On battery</span>', dev.charging === false ? 'plug the charger in' : dev.at ? 'reported ' + esc(rel(dev.at)) : '', dev.charging === false ? 'alert' : '')}
      ${tile('Temperature', 'flame', dev.temp != null ? dev.temp + '°C' : '—', dev.temp >= 42 ? '<span style="color:var(--bad)">too hot — move to a cooler spot</span>' : 'normal is below 40°C', dev.temp >= 42 ? 'alert' : '')}
      ${tile('Uptime', 'clock', upTxt, 'since the server last started')}
      ${tile('Free storage', 'folder', (s.disk_free / 1e9).toFixed(1) + ' GB', 'videos are deleted after upload')}
      ${tile('Services', 'check', `${okSvc}/${allSvc}`, `${s.jobs.queued || 0} waiting · ${s.jobs.running || 0} uploading`, okSvc < allSvc ? 'alert' : '')}
    </div>
     <div class="dgrid"><div class="s8"><section class="card" style="height:100%">${hd('Services', 'Each service reports a heartbeat and restarts itself after an error.')}${svc}</section></div><div class="s4"><section class="card" style="height:100%">${hd('This phone')}
       <div style="display:flex;flex-direction:column;gap:10px;font-size:13px">${dev.model ? `<div><span class="mini">Device</span><div><b>${esc(dev.model)}</b> · Android ${esc(dev.android || '')}</div></div>` : ''}
       <div><span class="mini">Public link</span><div class="mono" style="word-break:break-all">${esc(s.public_url)}</div></div>
       <div><span class="mini">Version</span><div class="mono">${esc(s.version)}</div></div>
       <div class="note info">${ic('phone', 16)}<span>Keep the phone on Wi-Fi and the charger. Use “Erase and start fresh” on the phone screen only to reset it completely.</span></div></div></section></div></div>
     <div class="dgrid"><div class="s12"><section class="card">${hd('Phone modes', 'Make the phone a dedicated DriveCast machine. Both are optional — the server runs either way.', `<a class="btn sm" href="/dedicated-mode.bat" download>${ic('down', 14)}dedicated-mode.bat</a>`)}
       <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(240px,1fr));margin:0">
         ${[['Quiet mode', dev.quiet, 'No notification or sound interrupts. Turn on from the phone screen.'],
            ['Dedicated phone mode', dev.dedicated, 'DriveCast owns the phone: other apps paused, permissions given, no lock screen, night-only updates. One-time setup: factory reset (no Google account) → USB debugging → run dedicated-mode.bat → tap Apply on the phone.'],
            ['Storage folder', dev.storage, 'Downloads go to “Shared with my tool DriveCast” on the phone, hidden from the Gallery. Allow it from the phone checklist.'],
            ['Video tools', dev.ffmpeg, 'Built into the app — cleans every file before it leaves the phone.']].map(([t, on, h]) => `<div style="display:flex;gap:10px;padding:12px;border:1px solid var(--line);border-radius:12px"><span style="color:${on ? 'var(--ok)' : 'var(--muted)'}">${ic(on ? 'check' : 'clock', 18)}</span><div style="display:flex;flex-direction:column;gap:3px"><b style="font-size:13px">${t} · ${on ? 'On' : on === false ? 'Off' : 'unknown'}</b><span class="muted" style="font-size:12px">${h}</span></div></div>`).join('')}
       </div></section></div></div>
     <div class="dgrid"><div class="s12" id="flcard"></div></div>
     <div class="dgrid"><div class="s12" id="dlcard"></div></div>
     <div class="dgrid"><div class="s12" id="bkcard"></div></div>`);
  drawBackup();
  drawDownloader();
  drawFleet();
  drawUpdates();
  every(60000, () => { if (!document.getElementById('modal')) render(); });
}

// ------------------------------------------------------------------ Devices (admin): what every phone needs to stay on air
function gb(n) { return n == null ? '—' : n >= 1e12 ? (n / 1e12).toFixed(1) + ' TB' : (n / 1e9).toFixed(1) + ' GB'; }
async function pageDevices() {
  const d = await api('GET', '/api/devices');
  const t = d.this, dv = t.device || {}, st = t.stats || {};
  const tile = (k, v, h, tone) => `<div style="padding:14px;border:1px solid var(--line);border-radius:14px;background:var(--card)"><span class="mini">${k}</span><div style="font:700 22px var(--disp);color:${tone || 'inherit'}">${v}</div><span class="mini">${h || ''}</span></div>`;
  const bat = dv.battery == null ? '—' : dv.battery + '%';
  const batTone = dv.battery != null && dv.battery <= 20 && !dv.charging ? 'var(--bad)' : null;
  const hot = (dv.temp || 0) >= 43;
  const freePct = t.disk_total ? Math.round(100 * t.disk_free / t.disk_total) : null;
  const svc = t.services.map(x => `<div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-top:1px solid var(--line);font-size:13px"><span class="beat ${x.ok ? '' : 'warn'}" style="animation:none"></span><b style="flex:1">${esc(x.name)}</b><span class="mini">every ${x.every >= 3600 ? Math.round(x.every / 3600) + ' h' : Math.round(x.every / 60) + ' min'} · last ${esc(upTxt(x.age))} ago${x.errors ? ` · <span style="color:var(--bad)">${x.errors} error(s)</span>` : ''}</span></div>`).join('');
  const al = t.alerts.map(a => `<div class="note ${a.level === 'error' ? 'bad' : 'warn'}">${ic('alert', 16)}<span><b>${esc(a.title)}.</b> ${esc(a.message)}</span></div>`).join('');
  const check = (ok, txt) => `<div style="display:flex;gap:10px;align-items:center;padding:7px 0;border-top:1px solid var(--line);font-size:13px"><span style="color:${ok ? 'var(--ok)' : 'var(--warn)'}">${ok ? ic('check', 16) : ic('alert', 16)}</span>${txt}</div>`;
  const fleetRows = (d.fleet || []).map(f => { const s2 = f.stats || {}; return `<div style="display:grid;grid-template-columns:14px minmax(150px,1.3fr) repeat(5,minmax(80px,1fr));gap:10px;align-items:center;padding:10px 0;border-top:1px solid var(--line);font-size:12.5px"><span class="beat ${f.online ? (s2.replaced ? 'warn' : '') : 'off'}"></span><span><b class="mono">${esc(f.host)}</b><br><span class="mini">${f.online ? 'online' : 'offline'} · ${f.last_seen ? esc(rel(f.last_seen)) : ''} · ${esc(f.version || '')}</span></span><span><b>${s2.battery ?? '—'}%</b>${s2.charging ? ' ⚡' : ''}<br><span class="mini">battery</span></span><span><b>${s2.temp ? s2.temp + '°C' : '—'}</b><br><span class="mini">temperature</span></span><span><b>${f.users}</b>/${f.max_users}<br><span class="mini">accounts</span></span><span><b>${s2.live ?? '—'}</b>/${s2.channels ?? '—'}<br><span class="mini">clients live</span></span><span><b>${s2.posts_24h ?? '—'}</b>${s2.failed_24h ? ` · <span style="color:var(--bad)">${s2.failed_24h}✕</span>` : ''}<br><span class="mini">posts 24h</span></span></div>`; }).join('');
  mount('devices', 'Devices', 'Every phone that runs DriveCast — what it needs to stay on air', 'Admin', `<a class="btn" href="#/server">${ic('phone', 16)}Server settings</a>`,
    `${al}
    <section class="card" style="margin-bottom:12px"><div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:12px"><span class="beat ${t.replaced ? 'warn' : ''}"></span><b style="font:700 20px var(--disp)">${esc(t.host)}</b><span class="muted">${esc(dv.model || 'Phone')} · Android ${esc(dv.android || '?')} · this phone</span><span style="margin-left:auto" class="mono mini">v${esc(t.version)} · ${t.running_from === 'update' ? 'updated copy' : 'app copy'} · id ${esc(t.instance)}</span></div>
      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px">
        ${tile('Battery', bat, dv.charging ? 'charging ⚡' : 'on battery', batTone)}${tile('Temperature', dv.temp ? dv.temp + '°C' : '—', hot ? 'too hot — cool it down' : 'normal', hot ? 'var(--bad)' : null)}
        ${tile('Free storage', gb(t.disk_free), freePct != null ? freePct + '% of ' + gb(t.disk_total) : '', freePct != null && freePct < 10 ? 'var(--bad)' : null)}${tile('Up for', upTxt(t.uptime), 'since the last restart')}
        ${tile('Posting now', t.jobs.running || 0, (t.jobs.queued || 0) + ' waiting')}${tile('Posts · 24h', st.posts_24h ?? 0, st.failed_24h ? st.failed_24h + ' failed or unclear' : 'all fine', st.failed_24h ? 'var(--warn)' : null)}</div></section>
    <div class="dgrid"><div class="s6"><section class="card">${hd('Checklist', 'What a server phone needs')}
      ${check(!!dv.charging, 'Plugged in to a charger')}${check(!hot, 'Temperature under 43°C')}${check(freePct == null || freePct >= 10, 'At least 10% storage free')}
      ${check(!!dv.dedicated, 'Dedicated mode (nothing else can stop the app)')}${check(!!dv.storage, 'Storage permission for the phone folder')}${check(!!dv.ffmpeg, 'Video tools inside the app')}
      ${check(!!t.own_domain, 'Own domain on' + (t.own_domain ? ' · ' + esc(t.own_domain.replace('https://', '')) : ''))}${check(!!t.tailscale, 'Tailscale backup link' + (t.tailscale ? ' · ' + esc(t.tailscale.replace('https://', '')) : ''))}
      ${check(!!t.backup_at, 'Nightly backup in Drive' + (t.backup_at ? ' · ' + esc(rel(t.backup_at)) : ''))}${check(!!t.backup_test_at, 'Backup test-opened' + (t.backup_test_at ? ' · ' + esc(rel(t.backup_test_at)) : ''))}
      ${check(!t.replaced, t.replaced ? 'This phone was replaced — posting stopped' : 'Runs its own address (no other phone took over)')}</section></div>
      <div class="s6"><section class="card">${hd('Background services', 'Each one is judged by its own schedule')}${svc || '<div class="empty">Starting…</div>'}</section></div></div>
    ${d.fleet && d.fleet.length ? `<section class="card" style="margin-top:12px">${hd('All phones', `${d.totals.online}/${d.totals.phones} online · ${d.totals.live}/${d.totals.channels} clients live · ${d.totals.posts_24h} posts in 24 h`)}${fleetRows}</section>` : ''}`);
  every(15000, () => { if (!document.getElementById('modal')) render(); });
}
// ------------------------------------------------------------------ v3 shell: theme, sidebar, motion, command palette, shortcuts
function load_(k, d) { try { return localStorage.getItem(k) ?? d; } catch (e) { return d; } }
function store_(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } }
function themePref() { const t = load_('dc_theme', 'auto'); return ['auto', 'light', 'dark'].includes(t) ? t : 'auto'; }
function applyTheme() {
  const t = themePref();
  const dark = t === 'dark' || (t === 'auto' && window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
}
applyTheme();
try { matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyTheme); } catch (e) { /* old browser */ }
const railMini = () => load_('dc_rail', '') === 'mini';
const GLOGO = '<svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true"><path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z"/><path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/><path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-7.9l-6.5 5C9.5 39.6 16.2 44 24 44z"/><path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z"/></svg>';
async function tgCard() {
  const box = document.getElementById('tgcard'); if (!box) return;
  let t; try { t = await api('GET', '/api/telegram'); } catch (e) { return; }
  box.innerHTML = `<section class="card" style="margin-bottom:16px">${hd('Telegram alerts', 'A message on your phone when a server is offline for 1 hour, a pool’s proxy is down for real, or something serious needs you. Every server of the studio uses this bot.', t.ready ? chip('On' + (t.bot ? ' · @' + esc(t.bot) : ''), 'ok') : chip('Off', 'neutral'))}
    <ol class="muted" style="font-size:12.5px;line-height:1.7;margin:0 0 10px;padding-left:18px"><li>In Telegram open ${deep('https://t.me/BotFather', '@BotFather')} → <b>/newbot</b> → copy the token.</li><li>Open your new bot in Telegram, press <b>Start</b> and send “hi”.</li><li>Paste the token below and press <b>Save</b> — the chat is found by itself and a test message arrives.</li></ol>
    <form data-form="tgsave" style="display:flex;flex-direction:column;gap:10px">
      <label class="f">Bot token<span class="pw"><input class="i" name="token" type="password" autocomplete="off" placeholder="${t.has_token ? 'Saved — paste a new one to replace' : '123456:ABC…'}"><button type="button" class="pw-eye" data-act="pweye" aria-label="Show token">${ic('eye', 16)}</button></span></label>
      <label class="f">Chat id<input class="i" name="chat_id" value="${esc(t.chat_id)}" placeholder="filled by Find my chat"></label>
      <div class="actions"><button class="btn primary" type="submit">Save</button>${t.has_token ? '<button type="button" class="btn" data-act="tgfind">Find my chat</button>' : ''}${t.ready ? '<button type="button" class="btn" data-act="tgtest">Send a test</button><button type="button" class="btn danger" data-act="tgoff">Turn off</button>' : ''}</div></form></section>`;
  S.acts.tgsave = async v => {
    const b = { chat_id: v.chat_id }; if (v.token) b.token = v.token;
    await api('POST', '/api/telegram', b);
    if (!v.chat_id) {
      try { const r = await api('POST', '/api/telegram/find'); await api('POST', '/api/telegram/test'); toast(`Connected to ${r.name || 'your chat'} — a test message is in Telegram`); }
      catch (e) { toast('Token saved. ' + e.message, true); }
    } else toast('Telegram saved');
    tgCard();
  };
  S.acts.tgfind = async () => { const r = await api('POST', '/api/telegram/find'); toast(`Found ${r.name || 'your chat'} · ${r.chat_id}`); tgCard(); };
  S.acts.tgtest = async () => { await api('POST', '/api/telegram/test'); toast('Test sent — check Telegram'); };
  S.acts.tgoff = async () => { await api('POST', '/api/telegram', { clear: true }); tgCard(); };
}
async function mailCard() {
  const box = document.getElementById('mailcard'); if (!box) return;
  let m; try { m = await api('GET', '/api/mail'); } catch (e) { return; }
  box.innerHTML = `<section class="card" style="margin-bottom:16px">${hd('Email for password resets', 'People who forget their password get a reset link by email, sent from your mailbox.', m.ready ? chip('Working', 'ok') : chip('Off', 'neutral'))}
    <form data-form="mailsave" style="display:flex;flex-direction:column;gap:10px">
      <div class="form" style="grid-template-columns:minmax(0,2fr) 90px"><label class="f">SMTP server<input class="i" name="host" value="${esc(m.host || 'smtp.gmail.com')}"></label><label class="f">Port<input class="i" name="port" type="number" value="${esc(m.port || 465)}"></label></div>
      <label class="f">Email address<input class="i" name="user" type="email" value="${esc(m.user)}" placeholder="support@… or you@gmail.com"></label>
      <label class="f">App password<span class="pw"><input class="i" name="password" type="password" placeholder="${m.ready ? 'Saved — leave empty to keep' : '16 letters from Google'}" autocomplete="off"><button type="button" class="pw-eye" data-act="pweye" aria-label="Show password">${ic('eye', 16)}</button></span><span class="help">Gmail: turn on 2-Step Verification, then ${deep('https://myaccount.google.com/apppasswords', 'make an app password')}. Not your normal password.</span></label>
      <label class="f">Sender name<input class="i" name="from_name" value="${esc(m.from_name)}"></label>
      <div class="actions"><button class="btn primary" type="submit">Save</button>${m.ready ? '<button type="button" class="btn" data-act="mailtest">Send me a test</button><button type="button" class="btn danger" data-act="mailoff">Turn off</button>' : ''}</div></form></section>`;
  S.acts.mailsave = async v => { await api('POST', '/api/mail', v); toast('Email saved'); mailCard(); };
  S.acts.mailtest = async () => { const r = await api('POST', '/api/mail/test'); toast('Test sent to ' + r.to); };
  S.acts.mailoff = async () => { await api('POST', '/api/mail', { clear: true }); mailCard(); };
}
async function updatePill() {
  if (!S.user || S.user.role !== 'owner') return;
  if (!S.upd || Date.now() - S.upd.at > 30 * 60000) { try { S.upd = { at: Date.now(), u: await api('GET', '/api/update') }; } catch (e) { return; } }
  const u = S.upd.u, p = document.getElementById('uppill');
  if (!p || !u || !u.newer || !u.latest) return;
  p.querySelector('span').textContent = `Update to ${u.latest.version}`;
  p.classList.remove('hidden');
}
// ------------------------------------------------------------------ Users across servers: drag and drop between servers
async function drawPending() {
  const box = document.getElementById('upend'); if (!box) return;
  let d; try { d = await api('GET', '/api/admin/pending'); } catch (e) { return; }
  const rows = d.users.map(u => `<div class="tr" style="grid-template-columns:minmax(0,2fr) 140px auto"><div class="who">${avatar(u.name, hashI(u.name), null, 30)}<div><b>${esc(u.name)}</b><span class="mini">${esc(u.email)}${u.google ? ' · Google' : ''}</span></div></div><span class="mini">${esc(rel(u.created_at))}</span>
    <div class="actions" style="justify-content:flex-end"><button class="btn sm primary" data-act="pappr" data-id="${u.id}">${ic('check', 14)}Approve</button><button class="btn sm danger" data-act="prej" data-id="${u.id}">Reject</button></div></div>`).join('');
  box.innerHTML = `<section class="card" style="margin-bottom:16px">${hd('Sign-ups', 'Without an invite code, a new account waits here until you approve it. With a code it works at once.',
    `<div class="seg"><button class="${d.mode === 'approval' ? 'on' : ''}" data-act="pmode" data-v="approval">Approval</button><button class="${d.mode === 'invite' ? 'on' : ''}" data-act="pmode" data-v="invite">Invite only</button></div>`)}
    ${d.users.length ? `<div class="tbl"><div>${rows}</div></div>` : `<div class="empty" style="padding:14px">${d.mode === 'approval' ? 'Nobody is waiting.' : 'Only invite codes can create accounts.'}</div>`}</section>`;
  S.acts.pmode = async x => { await api('POST', '/api/admin/pending', { mode: x.v }); toast(x.v === 'approval' ? 'Sign-ups without a code now wait for your approval' : 'Only invite codes can create accounts'); drawPending(); };
  S.acts.pappr = async x => { const r = await api('POST', `/api/admin/users/${x.id}/approve`, {}); toast(r.emailed ? 'Approved — they got an email' : 'Approved — they can sign in now'); render(); };
  S.acts.prej = async x => { if (!confirm('Reject and delete this sign-up?')) return; await api('POST', `/api/admin/users/${x.id}/reject`); drawPending(); };
}
async function drawBoard() {
  const box = document.getElementById('uboard'); if (!box) return;
  let d; try { d = await api('GET', '/api/fleet/users'); } catch (e) { return; }
  const health = sv => { const st = sv.stats || {}; const bad = !sv.online || (st.temp || 0) >= 43 || (st.battery != null && st.battery <= 15 && !st.charging); return bad ? (sv.online ? 'warn' : 'bad') : 'ok'; };
  const col = sv => {
    const st = sv.stats || {}, hl = health(sv);
    const people = (sv.users_list || []).filter(u => u.role !== 'owner');
    const card = u => `<div class="ub-card ${u.status === 'suspended' ? 'off' : ''}" draggable="${d.hub ? 'true' : 'false'}" data-email="${esc(u.email)}" data-host="${esc(sv.host)}">
        <div class="who">${avatar(u.name, hashI(u.name), null, 30)}<div style="min-width:0"><b class="ell">${esc(u.name)}</b><span class="mini ell">${esc(u.email)}</span></div>${u.status === 'suspended' ? chip('Suspended', 'bad') : u.pools_down ? chip('Proxy down', 'warn') : ''}</div>
        <div class="ub-stats"><span><b>${u.live}</b>/${u.clients} live</span><span><b>${u.pools}</b> pools</span><span><b>${u.posts_7d}</b> posts</span><span><b>${num(u.views_7d)}</b> views</span>${u.failed_7d ? `<span style="color:var(--bad)"><b>${u.failed_7d}</b> failed</span>` : ''}</div>
        <div class="ub-act"><button class="linkbtn" data-act="uba" data-a="${u.status === 'suspended' ? 'activate' : 'suspend'}" data-email="${esc(u.email)}" data-host="${esc(sv.host)}">${u.status === 'suspended' ? 'Activate' : 'Suspend'}</button><button class="linkbtn" data-act="uba" data-a="reset_link" data-email="${esc(u.email)}" data-host="${esc(sv.host)}">Reset link</button><button class="linkbtn" style="color:var(--bad)" data-act="uba" data-a="delete" data-email="${esc(u.email)}" data-host="${esc(sv.host)}">Delete</button>${sv.snapshots && sv.snapshots[''] ? '' : ''}</div></div>`;
    return `<section class="ub-col ${hl}" data-drop="${esc(sv.host)}"><div class="ub-h"><span class="beat ${sv.online ? (hl === 'warn' ? 'warn' : '') : 'off'}"></span><div style="min-width:0;flex:1"><b class="mono ell">${esc(sv.label || sv.host)}</b><span class="mini ell">${esc(sv.host)}</span></div><span class="mono mini">${sv.users}/${sv.max_users || '∞'}</span></div>
      <div class="ub-m">${sv.online ? 'online' : 'offline'}${st.battery != null ? ` · ${st.battery}%${st.charging ? '⚡' : ''}` : ''}${st.temp ? ` · ${st.temp}°C` : ''}${st.posts_24h != null ? ` · ${st.posts_24h} posts 24h` : ''}${st.failed_24h ? ` · <span style="color:var(--bad)">${st.failed_24h} failed</span>` : ''}</div>
      <div class="ub-list">${people.map(card).join('') || '<div class="ub-empty">Drop a user here</div>'}</div></section>`;
  };
  const log = (d.log || []).slice(0, 5).map(m => `<div class="mini">${esc(rel(m.at))} · <b>${esc(m.email)}</b> ${esc(m.from)} → ${esc(m.to)}${m.live ? '' : ` · from the copy of ${esc(tFull(m.snapshot_at))}`}</div>`).join('');
  box.innerHTML = `<section class="card" style="margin-bottom:16px">${hd('Servers and their users', d.hub ? 'Drag a user to another server: everything moves with them — pools, proxies, authorizations, cookies, routes and history. Nothing to authorize again.' : 'Make this phone the hub (Server → Studios) to see every server here and move users between them.')}
    <div class="ub">${d.servers.map(col).join('')}</div>${log ? `<div style="margin-top:12px;display:flex;flex-direction:column;gap:4px"><b style="font-size:12.5px">Recent moves</b>${log}</div>` : ''}</section>`;
  let drag = null;
  box.querySelectorAll('.ub-card[draggable=true]').forEach(c => {
    c.addEventListener('dragstart', e => { drag = { email: c.dataset.email, from: c.dataset.host }; c.classList.add('dragging'); e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', drag.email); });
    c.addEventListener('dragend', () => { c.classList.remove('dragging'); box.querySelectorAll('.ub-col').forEach(x => x.classList.remove('over')); });
  });
  box.querySelectorAll('.ub-col').forEach(col => {
    col.addEventListener('dragover', e => { if (drag && col.dataset.drop !== drag.from) { e.preventDefault(); col.classList.add('over'); } });
    col.addEventListener('dragleave', () => col.classList.remove('over'));
    col.addEventListener('drop', e => { e.preventDefault(); col.classList.remove('over'); if (!drag || col.dataset.drop === drag.from) return; const m = drag; drag = null; moveModal(m.email, m.from, col.dataset.drop, d); });
  });
  S.acts.uba = async x => {
    if (x.a === 'delete' && !confirm(`Delete ${x.email} and everything in their workspace? Posts already on YouTube and files in Drive stay.`)) return;
    if (x.a === 'suspend' && !confirm(`Suspend ${x.email}? Their posting pauses and they cannot sign in.`)) return;
    const r = await api('POST', '/api/fleet/users/action', { host: x.host, email: x.email, action: x.a });
    if (x.a === 'reset_link') {
      modal(`<h2>Reset link · ${esc(x.email)}</h2><p class="muted" style="margin:0">Send this link to them. It works once, for 24 hours.</p><div class="linkbox"><span class="kicker">Reset link</span><div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><span class="mono" style="flex:1 1 260px;word-break:break-all">${esc(r.url)}</span><button class="btn coral" data-act="copy" data-text="${esc(r.url)}">${ic('copy', 16)}Copy</button></div></div><div class="actions" style="justify-content:flex-end"><button class="btn" data-act="close">Done</button></div>`);
      return;
    }
    toast({ suspend: 'Suspended', activate: 'Active again', delete: 'Deleted' }[x.a]); setTimeout(drawBoard, 1500);
  };
}
function moveModal(email, from, to, d) {
  const src = d.servers.find(s => s.host === from), dst = d.servers.find(s => s.host === to);
  const off = src && !src.online;
  modal(`<h2>Move ${esc(email)}</h2><p class="muted" style="margin:0"><b>${esc(from)}</b> → <b>${esc(to)}</b></p>
    <div class="note info">${ic('check', 16)}<span>Everything moves: pools with their proxies and apps, YouTube / Facebook / Instagram authorizations, cookies, routes, schedule, AI settings and history. Posting continues on ${esc(to)} from the same proxies — nothing to authorize again. ${esc(from)} stops posting for them at once.</span></div>
    ${off ? `<div class="note warn">${ic('alert', 16)}<span><b>${esc(from)} is offline.</b> The move uses the last saved copy of this user. Posts and stats since that copy may be missing from the history; accounts, proxies and routes are safe. If ${esc(from)} comes back, it stays silent for this user.</span></div>` : ''}
    ${dst && dst.max_users && dst.users >= dst.max_users ? `<div class="note warn">${ic('alert', 16)}<span>${esc(to)} is at its account limit (${dst.users}/${dst.max_users}).</span></div>` : ''}
    <div class="actions" style="justify-content:flex-end"><button class="btn" data-act="close">Cancel</button><button class="btn primary" data-act="mvgo">${ic('arrow', 15)}Move now</button></div>`);
  S.acts.mvgo = async (x, el) => {
    el.innerHTML = '<span class="spinner"></span> Moving…';
    const r = await api('POST', '/api/fleet/users/move', { email, from, to });
    closeModal(); toast(`Moved to ${to}: ${r.clients} client(s), ${r.pools} pool(s)${r.live ? '' : ' — from the saved copy'}`); setTimeout(drawBoard, 1200);
  };
}
function resetPage(token) {
  document.getElementById('app').innerHTML = `<div class="auth"><section class="hero"><div class="brand">${BRAND(true, true)}</div><div style="flex-grow:1"></div><h1>A fresh<br>password.</h1><p>Choose a new password for your DriveCast Studio account. Every other sign-in is closed for safety.</p><div style="flex-grow:1"></div></section>
    <section class="side"><form data-form="resetgo"><div><h2 style="font-size:28px">Choose a new password</h2><p class="muted">At least 8 characters.</p></div>
    <label class="f">New password<span class="pw"><input class="i" name="password" type="password" required minlength="8" autocomplete="new-password"><button type="button" class="pw-eye" data-act="pweye" aria-label="Show password">${ic('eye', 16)}</button></span></label>
    <label class="f">Same password again<span class="pw"><input class="i" name="again" type="password" required minlength="8" autocomplete="new-password"><button type="button" class="pw-eye" data-act="pweye" aria-label="Show password">${ic('eye', 16)}</button></span></label>
    <button class="btn primary big" type="submit">Save and sign in</button><a class="linkbtn" href="#/login" style="align-self:center">Back to sign in</a></form></section></div>`;
  S.acts.resetgo = async v => {
    if (v.password !== v.again) throw new Error('The two passwords are not the same');
    const r = await api('POST', '/api/auth/reset', { token, password: v.password });
    S.user = r.user; S.tz = r.user.timezone; S.session = { user: r.user };
    toast('Password changed — you are signed in'); location.hash = '#/dashboard'; render();
  };
}
// people who are not the owner see "server", never "phone"
function deskin(root) {
  if (!root || !S.user || S.user.role === 'owner') return;
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const fix = t => t.replace(/\b(P|p)hones\b/g, (m, c) => c === 'P' ? 'Servers' : 'servers').replace(/\b(P|p)hone(’s|'s)?\b/g, (m, c, p2) => (c === 'P' ? 'Server' : 'server') + (p2 || ''));
  for (let n = w.nextNode(); n; n = w.nextNode()) { if (/[Pp]hone/.test(n.nodeValue)) n.nodeValue = fix(n.nodeValue); }
  root.querySelectorAll('[title*="hone"],[placeholder*="hone"]').forEach(e => { if (e.title) e.title = fix(e.title); if (e.placeholder) e.placeholder = fix(e.placeholder); });
}
const SKELETON = `<div class="skel"><div class="sk-row">${'<i class="sk sk-kpi"></i>'.repeat(4)}</div><div class="sk-row"><i class="sk sk-big"></i><i class="sk sk-side"></i></div><div class="sk-row"><i class="sk sk-line"></i></div></div>`;

// numbers count up when a page opens (not on live refreshes)
function countUp(root) {
  if (!root || (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches)) return;
  root.querySelectorAll('.kpi .v, .strip strong, .stg strong, .big, .ai-n, .lv').forEach(el => {
    if (el.children.length) return;
    const m = el.textContent.match(/^(\D*?)(\d[\d,]*\.?\d*)(.*)$/);
    if (!m) return;
    const raw = m[2], dec = (raw.split('.')[1] || '').length, target = parseFloat(raw.replace(/,/g, '')), comma = raw.includes(',');
    if (!isFinite(target) || target === 0) return;
    const t0 = performance.now(), D = 650;
    const f = t => {
      const k = Math.min(1, (t - t0) / D), e = 1 - Math.pow(1 - k, 3), v = target * e;
      let out = dec ? v.toFixed(dec) : String(Math.round(v));
      if (comma) out = Number(out).toLocaleString('en', { minimumFractionDigits: dec, maximumFractionDigits: dec });
      el.textContent = m[1] + out + m[3];
      if (k < 1) requestAnimationFrame(f);
    };
    requestAnimationFrame(f);
  });
}

// ⌘K / Ctrl K — jump anywhere, run anything
async function paletteItems() {
  const own = S.user.role === 'owner';
  const items = NAV.filter(([g]) => g !== 'Admin' || own).flatMap(([g, list]) => list.map(([i, l, r]) => ({ icon: i, label: l, hint: g === 'Admin' ? 'Admin' : 'Page', run: () => go(r), kw: r })));
  items.push(
    { icon: 'plus', label: 'Add a client', hint: 'Action', run: () => go('add'), kw: 'new route wizard' },
    { icon: 'pause', label: 'Pause or resume everything', hint: 'Action', run: () => { go('dashboard'); setTimeout(() => pauseModal(), 400); }, kw: 'vacation hold stop' },
    { icon: 'moon', label: 'Change theme (light / dark / auto)', hint: 'Action', run: () => GLOBAL_ACTS.theme(), kw: 'dark mode' },
    { icon: 'side', label: 'Collapse / expand sidebar', hint: 'Action', run: () => GLOBAL_ACTS.railmini(), kw: 'menu' },
    { icon: 'logout', label: 'Sign out', hint: 'Action', run: () => GLOBAL_ACTS.logout(), kw: 'logout' });
  if (!S.pal || Date.now() - S.pal.at > 60000) {
    const [ch, gr] = await Promise.all([api('GET', '/api/channels').catch(() => ({ channels: [] })), api('GET', '/api/groups').catch(() => ({ groups: [] }))]);
    S.pal = { at: Date.now(), ch: ch.channels || [], gr: gr.groups || [] };
  }
  S.pal.ch.forEach(c => items.push({ icon: 'tv', label: c.title, hint: 'Client', run: () => go('channel/' + c.id), kw: (c.handle || '') + ' client route', img: c.thumbnail }));
  S.pal.gr.forEach(g => items.push({ icon: 'net', label: `Pool ${g.number}${g.label ? ' · ' + g.label : ''}`, hint: 'Pool', run: () => go('groups'), kw: `${g.outgoing_ip || ''} ${g.host || ''} proxy` }));
  return items;
}
function fuzzy(q, t) {
  q = q.toLowerCase(); t = t.toLowerCase();
  if (!q) return 1;
  const i = t.indexOf(q);
  if (i >= 0) return 100 - i;
  let j = 0, score = 0;
  for (const ch of t) { if (ch === q[j]) { j++; score++; } if (j === q.length) return score; }
  return 0;
}
async function openPalette() {
  if (document.getElementById('pal')) return;
  const bg = document.createElement('div');
  bg.className = 'pal-bg'; bg.id = 'pal';
  bg.innerHTML = `<div class="pal" role="dialog" aria-label="Search"><div class="pal-in">${ic('search', 17)}<input id="palq" placeholder="Search clients, groups, pages or actions…" autocomplete="off" spellcheck="false"><kbd>Esc</kbd></div><div class="pal-list" id="pall"><div class="pal-empty"><span class="spinner"></span></div></div>
    <div class="pal-ft"><span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>↵</kbd> open</span><span><kbd>G</kbd> then <kbd>D</kbd> <kbd>C</kbd> <kbd>I</kbd> <kbd>S</kbd> <kbd>A</kbd> jump</span></div></div>`;
  bg.addEventListener('click', e => { if (e.target === bg) closePalette(); });
  document.body.appendChild(bg);
  const q = bg.querySelector('#palq'); q.focus();
  let items = [], shown = [], sel = 0;
  const draw = () => {
    const v = q.value.trim();
    shown = items.map(it => [fuzzy(v, it.label + ' ' + (it.kw || '') + ' ' + it.hint), it]).filter(x => x[0] > 0).sort((a, b) => b[0] - a[0]).slice(0, 12).map(x => x[1]);
    sel = Math.min(sel, Math.max(0, shown.length - 1));
    const list = bg.querySelector('#pall');
    list.innerHTML = shown.length ? shown.map((it, i) => `<button class="pal-it ${i === sel ? 'on' : ''}" data-i="${i}">${it.img ? `<img src="${esc(it.img)}" alt="">` : `<span class="pal-ic">${ic(it.icon, 15)}</span>`}<span class="ell">${esc(it.label)}</span><em>${esc(it.hint)}</em></button>`).join('')
      : `<div class="pal-empty">Nothing matches “${esc(v)}”.</div>`;
    const on = list.querySelector('.on'); if (on) on.scrollIntoView({ block: 'nearest' });
  };
  const pick = i => { const it = shown[i]; if (!it) return; closePalette(); it.run(); };
  bg.querySelector('#pall').addEventListener('click', e => { const b = e.target.closest('.pal-it'); if (b) pick(+b.dataset.i); });
  bg.querySelector('#pall').addEventListener('mousemove', e => { const b = e.target.closest('.pal-it'); if (b && +b.dataset.i !== sel) { sel = +b.dataset.i; draw(); } });
  q.addEventListener('input', () => { sel = 0; draw(); });
  q.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(shown.length - 1, sel + 1); draw(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(0, sel - 1); draw(); }
    else if (e.key === 'Enter') { e.preventDefault(); pick(sel); }
    else if (e.key === 'Escape') closePalette();
  });
  try { items = await paletteItems(); } catch (e) { items = []; }
  draw();
}
function closePalette() { const p = document.getElementById('pal'); if (p) p.remove(); }
document.addEventListener('keydown', e => {
  if (!S.user) return;
  const typing = document.activeElement && (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName) || document.activeElement.isContentEditable);
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); document.getElementById('pal') ? closePalette() : openPalette(); return; }
  if (e.key === 'Escape') { if (document.getElementById('pal')) closePalette(); else closeModal(); return; }
  if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === '/') { e.preventDefault(); openPalette(); return; }
  if (S.gKey && Date.now() - S.gKey < 1200) {
    const to = { d: 'dashboard', s: 'schedule', c: 'channels', i: 'groups', v: 'videos', a: 'ai', e: 'settings', r: 'server', p: 'devices' }[e.key.toLowerCase()];
    S.gKey = 0;
    if (to && (S.user.role === 'owner' || !['server', 'devices'].includes(to))) go(to);
    return;
  }
  if (e.key.toLowerCase() === 'g') S.gKey = Date.now();
});

// ------------------------------------------------------------------ AI captions (Groq)
const AI_LANGS_DEFAULT = ['auto', 'English', 'Urdu', 'Hindi', 'Arabic', 'Spanish'];
function aiMeter(used, limit, label, unit = '') {
  const pct = Math.min(100, Math.round((used || 0) / (limit || 1) * 100));
  const col = pct > 90 ? 'var(--bad)' : pct > 70 ? 'var(--warn)' : 'var(--accent)';
  return `<div class="ai-m"><div><span>${label}</span><b class="mono">${num(used || 0)}${unit} / ${num(limit)}${unit}</b></div><div class="meter"><span style="width:${pct}%;background:${col}"></span></div></div>`;
}
function aiSwitch(k, on, title, help, icon) {
  return `<button type="button" class="tgl ai-t ${on ? 'on' : ''}" data-act="aitog" data-k="${k}" aria-pressed="${on}"><span class="ai-ic">${ic(icon, 16)}</span><span style="display:flex;flex-direction:column;gap:2px;text-align:left;flex:1;min-width:0"><b style="font-size:13px">${title}</b><span class="muted" style="font-size:12px">${help}</span></span><span class="sw"><i></i></span></button>`;
}
function aiCard(c, a) {
  if (!a) return '';
  const s = a.settings, u = a.usage, on = s.captions || s.subtitles || s.check;
  const langs = a.languages || AI_LANGS_DEFAULT;
  const needPerm = s.subtitles && !a.subtitles_allowed && c.platform === 'youtube';
  const status = !a.key ? `<div class="note warn">${ic('key', 16)}<span><b>No Groq key yet.</b> Add yours on the <a href="#/ai">AI page</a>, or the client’s own on the Pool (Apps). Posts go out with their normal text until then.</span></div>`
    : `<div class="ai-key"><span class="chip ${u && u.key_refused ? 'bad' : u && u.resting_until ? 'warn' : 'ok'}"><i></i>${u && u.key_refused ? 'Key refused' : u && u.resting_until ? 'Resting' : 'Ready'}</span><span class="mini">Key from <b>${esc(a.key)}</b></span></div>
       ${u ? `<div class="ai-ms">${aiMeter(u.text, u.text_limit, 'Captions today')}${aiMeter(Math.round(u.audio_s / 60), Math.round(u.audio_limit / 60), 'Listening today', ' min')}</div>` : ''}`;
  return `<section class="card ai-card ${on ? 'is-on' : ''}">${hd('AI captions', 'Groq writes each platform’s text from what the video says. Never stops a post.', `<button class="btn sm" data-act="aitry" ${a.key ? '' : 'disabled'}>${ic('spark', 14)}Try it</button>`)}
    ${status}
    <div class="ai-sw">${aiSwitch('captions', s.captions, 'Captions per platform', 'YouTube title + description, Facebook caption, Instagram caption + hashtags', 'spark')}
      ${aiSwitch('subtitles', s.subtitles, 'YouTube subtitles', 'Listens to the video and adds subtitles (uses some YouTube quota)', 'cc')}
      ${aiSwitch('check', s.check, 'Check before posting', 'Holds a post with abuse, false claims or another brand’s name', 'shield')}
      ${aiSwitch('pick', s.pick, 'AI picks the next post', 'Among the next ready videos, chooses the one most like this client’s best performers (fresh posts still first)', 'bolt')}</div>
    ${needPerm ? `<div class="note info">${ic('cc', 16)}<span><b>Subtitles need one more Allow.</b> The channel owner opens a new YouTube link once. <button class="btn sm" data-act="aisubsauth">Get the link</button></span></div>` : ''}
    <form data-form="aisave" class="ai-f"><label class="f">Language<select class="i" name="language">${langs.map(l => `<option value="${esc(l)}" ${l === s.language ? 'selected' : ''}>${l === 'auto' ? 'Same as the post' : esc(l)}</option>`).join('')}</select></label>
      <label class="f">Brand voice<input class="i" name="voice" value="${esc(s.voice)}" maxlength="300" placeholder="e.g. warm, a little funny, never salesy"></label>
      <div class="actions" style="align-self:end"><button class="btn sm primary" type="submit">Save</button></div></form>
    ${a.report ? `<div class="ai-rep"><div style="display:flex;justify-content:space-between;gap:8px;align-items:center"><b style="font-size:12.5px">Weekly summary · ${esc(a.report.week)}</b><button class="linkbtn" data-act="aireport">Write again</button></div><p>${esc(a.report.text)}</p></div>`
      : on && a.key ? `<div class="mini" style="display:flex;justify-content:space-between;gap:8px;align-items:center"><span>A short weekly summary appears here every week.</span><button class="btn sm" data-act="aireport">Write it now</button></div>` : ''}</section>`;
}
function aiActs(c, a) {
  return {
    aitog: async x => { const k = x.k; await api('PATCH', `/api/channels/${c.id}`, { ai: { [k]: !a.settings[k] } }); toast(`${{ captions: 'AI captions', subtitles: 'Subtitles', check: 'AI check', pick: 'AI picks the next post' }[k]} ${a.settings[k] ? 'off' : 'on'}`); render(); },
    aisave: async v => { await api('PATCH', `/api/channels/${c.id}`, { ai: { language: v.language, voice: v.voice } }); toast('AI settings saved'); render(); },
    aireport: async () => { toast('Writing the summary…'); await api('POST', `/api/channels/${c.id}/ai/report`); render(); },
    aisubsauth: () => ytLinkModal(c, true),
    aitry: () => {
      modal(`<h2>${ic('spark', 18)} Try AI captions · ${esc(c.title)}</h2><p class="muted" style="margin:0">Paste a caption like one of this client’s posts. Nothing is posted.</p>
        <form data-form="aitryrun" style="display:flex;flex-direction:column;gap:12px"><label class="f">Title<input class="i" name="title" placeholder="${esc(c.title)}"></label>
        <label class="f">Caption<textarea class="i" name="caption" rows="4" style="height:auto;padding:10px" placeholder="What the original post says…"></textarea></label>
        <div class="actions" style="justify-content:flex-end"><button type="button" class="btn" data-act="close">Close</button><button class="btn primary" type="submit">${ic('spark', 15)}Write</button></div></form><div id="aiout"></div>`);
      S.acts.aitryrun = async v => {
        const out = document.getElementById('aiout'); out.innerHTML = '<div class="sk sk-line" style="height:120px"></div>';
        try {
          const r = await api('POST', '/api/ai/preview', { channel_id: c.id, title: v.title, caption: v.caption });
          if (r.held) { out.innerHTML = `<div class="note bad">${ic('shield', 16)}<span><b>The check would hold this post:</b> ${esc(r.held)}</span></div>`; return; }
          const box = (k, t, body) => `<div class="ai-pv"><span class="ai-pk">${dstIcon(k, 14)}${t}</span><div>${body}</div></div>`;
          out.innerHTML = `<div class="ai-pvs">${box('youtube', 'YouTube', `<b>${esc(r.youtube.title)}</b><p>${esc(r.youtube.description)}</p>`)}${box('facebook', 'Facebook', `<p>${esc(r.facebook)}</p>`)}${box('instagram', 'Instagram', `<p>${esc(r.instagram)}</p>`)}</div>`;
        } catch (e) { out.innerHTML = `<div class="note bad">${ic('alert', 16)}<span>${esc(e.message)}</span></div>`; }
      };
    },
  };
}
async function ytLinkModal(c, subtitles) {
  let l; try { l = await api('POST', '/api/connect', { purpose: 'youtube', group_id: c.group.id, subtitles: !!subtitles }); } catch (e) { return toast(e.message, true); }
  modal(`<h2>${subtitles ? 'Allow subtitles' : 'Reconnect YouTube'} · ${esc(c.title)}</h2><p class="muted" style="margin:0">The channel owner opens this link and presses Allow.${subtitles ? ' Google adds one line: “manage your YouTube videos” — that is what lets DriveCast add subtitles.' : ''} The same channel is updated — nothing is posted twice.</p>
    <div class="linkbox"><span class="kicker">One-time link</span><div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><span class="mono" style="flex:1 1 260px;word-break:break-all">${esc(l.url)}</span><button class="btn coral" data-act="copy" data-text="${esc(l.url)}">${ic('copy', 16)}Copy</button><a class="btn" href="${esc(l.url)}" target="_blank" rel="noopener">Open here</a></div></div>
    <div class="actions" style="justify-content:flex-end"><button class="btn" data-act="close">Close</button></div>`);
  const t = setInterval(async () => { if (!document.getElementById('modal')) return clearInterval(t); try { const st = await api('GET', `/api/connect/${l.code}`); if (st.status === 'done') { clearInterval(t); closeModal(); toast(subtitles ? 'Subtitles allowed' : 'YouTube reconnected'); render(); } } catch (e) { } }, 3000);
  S.timers.push(t);
}
async function pageAI() {
  const [d, st] = await Promise.all([api('GET', '/api/ai'), api('GET', '/api/settings')]);
  const w = d.week || {};
  const keys = d.keys.map(k => `<div class="ai-krow"><div class="who"><span class="ai-ic">${ic('key', 15)}</span><div><b>${esc(k.where)}</b><span class="mini">${k.usage.key_refused ? '<span style="color:var(--bad)">Groq refused this key — paste a new one</span>' : k.usage.resting_until ? `resting until ${esc(tTime(k.usage.resting_until))}` : 'working'}</span></div></div>
    ${aiMeter(k.usage.text, k.usage.text_limit, 'Captions today')}${aiMeter(Math.round(k.usage.audio_s / 60), Math.round(k.usage.audio_limit / 60), 'Listening', ' min')}</div>`).join('');
  const owner = `<section class="card">${hd('Your Groq key', 'Used for every client whose Pool has no key of its own.', st.has_groq ? chip('Saved', 'ok') : chip('Not set', 'neutral'))}
    <form data-form="aikey" class="form" style="grid-template-columns:minmax(0,2fr) minmax(0,1fr) auto;align-items:start">
      <label class="f">API key<input class="i" name="groq_key" type="password" autocomplete="off" placeholder="${st.has_groq ? 'Saved — paste a new one to replace' : 'gsk_…'}"><span class="help">Free at ${deep('https://console.groq.com/keys', 'console.groq.com/keys')} — no card needed. Tested before saving.</span></label>
      <label class="f">Model<select class="i" name="ai_model"><option value="auto" ${d.choice === 'auto' ? 'selected' : ''}>Automatic${d.models.length ? ' · ' + esc(d.model) : ''}</option>${(d.models || []).map(m => `<option value="${esc(m)}" ${m === d.choice ? 'selected' : ''}>${esc(m)}</option>`).join('')}</select><span class="help">${d.models.length ? 'Groq’s live list. Automatic picks the best and switches by itself if Groq retires it.' : 'Save your key — the list comes from Groq.'}</span></label>
      <div class="actions"><button class="btn primary" type="submit">Save</button>${st.has_groq ? '<button type="button" class="btn danger" data-act="aikeydel">Remove</button>' : ''}</div></form></section>`;
  const stat = (n, l, i) => `<div class="kpi"><span class="l">${ic(i, 13)}${l}</span><span class="v ai-n">${num(n || 0)}</span><span class="f"><span>last 7 days</span></span></div>`;
  const kp = `<div class="kp" style="grid-template-columns:repeat(4,minmax(0,1fr))">${stat(w.captions, 'Captions written', 'spark')}${stat(w.subs, 'Subtitles added', 'cc')}${stat(w.held, 'Posts held by the check', 'shield')}${stat(Math.round((w.audio || 0) / 60), 'Minutes listened', 'mic')}</div>`;
  const rows = d.channels.map(c => {
    const s = c.settings;
    const mini = (k, label) => `<button class="ai-pill ${s[k] ? 'on' : ''}" data-act="aiq" data-id="${c.id}" data-k="${k}" data-v="${s[k] ? 0 : 1}">${label}</button>`;
    return `<div class="tr" style="grid-template-columns:minmax(200px,1.4fr) minmax(130px,1fr) minmax(250px,1.6fr) 120px 30px"><a class="who" href="#/channel/${c.id}" style="text-decoration:none;color:inherit">${avatar(c.title, hashI(c.title), c.thumbnail, 30)}<div><b class="ell">${esc(c.title)}</b></div></a>
      <span class="mini">${c.key ? 'key · ' + esc(c.key) : '<span style="color:var(--warn)">no key</span>'}</span>
      <span style="display:flex;gap:6px;flex-wrap:wrap">${mini('captions', 'Captions')}${mini('subtitles', 'Subtitles')}${mini('check', 'Check')}${mini('pick', 'Picks')}${s.subtitles && !c.subtitles_allowed ? '<span class="mini" title="Needs one more Allow on the client page" style="color:var(--warn)">needs Allow</span>' : ''}</span>
      <span class="mini">${s.language === 'auto' ? 'same as post' : esc(s.language)}</span><a href="#/channel/${c.id}" class="muted" aria-label="Open">${ic('chev', 16)}</a></div>`;
  }).join('');
  const how = `<section class="card">${hd('How it stays safe')}<div class="ai-how">
    <div>${ic('check', 16)}<span><b>Never stops a post.</b> No key, a refused key, the daily limit or a Groq problem → the post goes out with its normal text.</span></div>
    <div>${ic('users', 16)}<span><b>Each client’s own key.</b> Put the client’s key on its Pool: their own free daily limit, separate from everyone else.</span></div>
    <div>${ic('mic', 16)}<span><b>Only the sound leaves the phone</b> — a small audio file, never the video.</span></div>
    <div>${ic('shield', 16)}<span><b>The check only holds when it is sure.</b> A held post waits in Failed; move it back to the folder and it posts without asking again.</span></div></div></section>`;
  mount('ai', 'AI captions', `Groq · ${d.keys.length ? plural(d.keys.length, 'key') + ' active' : 'no key yet'} · ${d.channels.filter(c => c.settings.captions || c.settings.subtitles || c.settings.check || c.settings.pick).length} of ${d.channels.length} clients on`, 'Studio intelligence', '',
    `${kp}<div class="dgrid"><div class="s7" style="display:flex;flex-direction:column;gap:12px">${owner}<section class="card">${hd('Keys and today’s free limit', 'Groq’s free plan resets every day at midnight UTC.')}${keys || '<div class="empty" style="padding:14px">No key yet — add yours above, or a client’s on its Pool (Apps).</div>'}</section></div><div class="s5">${how}</div></div>
     <section class="card">${hd('Clients', 'Tap to switch. Language, brand voice and subtitles permission are on each client page.')}<div class="tbl"><div style="min-width:760px">${rows || '<div class="empty">No clients yet.</div>'}</div></div></section>`);
  Object.assign(S.acts, {
    aikey: async v => { const b = { ai_model: v.ai_model }; if (v.groq_key) b.groq_key = v.groq_key; await api('POST', '/api/settings', b); toast(v.groq_key ? 'Key tested and saved' : 'Saved'); render(); },
    aikeydel: async () => { if (!confirm('Remove your Groq key? Clients without their own key go back to normal text.')) return; await api('POST', '/api/settings', { groq_key_clear: true }); render(); },
    aiq: async x => { await api('PATCH', `/api/channels/${x.id}`, { ai: { [x.k]: x.v === '1' } }); render(); },
  });
}

// ------------------------------------------------------------------ Accounts: every connected account, by platform
async function pageAccounts() {
  const d = await api('GET', '/api/accounts');
  const tab = ['youtube', 'facebook', 'instagram'].includes(S.hq.get('tab')) ? S.hq.get('tab') : 'youtube';
  const list = d[tab] || [];
  const counts = { youtube: d.youtube.length, facebook: d.facebook.length, instagram: d.instagram.length };
  const tabs = `<div class="acc-tabs" role="tablist">${['youtube', 'facebook', 'instagram'].map(k => `<a role="tab" aria-selected="${k === tab}" class="acc-tab ${k === tab ? 'on' : ''}" href="#/accounts?tab=${k}">${dstIcon(k, 15)}<span>${DST[k][1]}</span><em>${counts[k]}</em></a>`).join('')}</div>`;
  const stat = (l, v) => `<div><span class="mini">${l}</span><b class="mono">${v}</b></div>`;
  const card = a => `<a class="acc-card" href="/#/account/${tab}/${a.id}" target="_blank" rel="noopener" title="Open deep stats in a new tab">
      <div class="acc-top">${avatar(a.name, hashI(a.name), a.picture, 44)}<div style="min-width:0;flex:1"><b class="ell" style="display:block;font-size:14px">${esc(a.name)}</b>
        <span class="mini ell" style="display:block">${a.pool ? `Pool ${esc(a.pool)}` : 'no pool'}${a.client ? ' · ' + esc(a.client) : a.handle ? ' · ' + esc(a.handle) : ''}</span>${a.auth_email || a.authorized_by ? `<span class="mini ell" style="display:block">${ic('key', 11)} ${esc(a.auth_email || a.authorized_by)}</span>` : ''}</div>
        ${tab === 'youtube' ? chip(a.state === 'live' ? 'Live' : a.state, a.state === 'live' ? 'ok' : a.state === 'attention' ? 'bad' : 'neutral') : tab === 'instagram' && a.status !== 'ok' ? chip('Reconnect', 'bad') : chip('Linked', 'ok')}</div>
      <div class="acc-stats">${tab === 'youtube' ? stat('Subscribers', num(a.subscribers)) + stat('All-time views', num(a.total_views)) + stat('Videos', num(a.videos)) : stat('Posts', num(a.posts)) + stat('Posts · 7 d', num(a.posts_7d)) + stat('Last post', a.last_post ? esc(rel(a.last_post)) : '—')}</div>
      <div class="acc-foot"><span>${tab === 'youtube' ? `${num(a.posts)} posts by DriveCast · ${num(a.views)} views` : a.failed_7d ? `<span style="color:var(--bad)">${a.failed_7d} failed this week</span>` : 'all fine this week'}</span><span class="acc-open">Deep stats ${ic('ext', 13)}</span></div></a>`;
  const empty = { youtube: 'No YouTube channel yet — add a client.', facebook: 'No Facebook page yet — connect Facebook on a pool.', instagram: 'No Instagram account yet — connect Instagram on a pool.' }[tab];
  mount('accounts', 'Accounts', `${counts.youtube} YouTube · ${counts.facebook} Facebook · ${counts.instagram} Instagram — open any account for its deep stats`, 'Workspace', '',
    `${tabs}<div class="acc-grid">${list.map(card).join('') || `<div class="empty" style="grid-column:1/-1">${empty}</div>`}</div>`);
}

async function pageAccount(kind, id) {
  const d = await api('GET', `/api/accounts/${kind}/${id}`);
  const h = d.head, s = d.summary;
  const kpi = (l, v, f = '') => `<div class="kpi"><span class="l">${l}</span><span class="v">${v}</span><span class="f"><span>${f}</span></span></div>`;
  const kp = kind === 'youtube'
    ? `${kpi('Subscribers', num(h.subscribers), s.subs_30d != null ? `${s.subs_30d >= 0 ? '+' : ''}${num(s.subs_30d)} in 30 days` : '')}${kpi('All-time views', num(h.total_views), s.views_30d != null ? `${num(s.views_30d)} in 30 days` : '')}${kpi('Videos on channel', num(h.videos))}${kpi('Posts by DriveCast', num(s.posts), `${s.success_rate ?? '—'}% success`)}${kpi('Avg views / post', num(s.avg_views), s.avg_first_day != null ? `${num(s.avg_first_day)} after day one` : '')}${kpi('Likes · comments', `${num(s.likes)} · ${num(s.comments)}`)}`
    : `${kpi('Posts by DriveCast', num(s.posts))}${kpi('Failed', num(s.failed))}${kpi('Success rate', s.success_rate == null ? '—' : s.success_rate + '%')}${kpi('Posts · 30 days', num(d.cadence.reduce((n, x) => n + x.published, 0)))}`;
  const growth = d.growth.length > 2 ? `<section class="card">${hd('Growth', 'Views gained per day (whole channel)')}${areaChart(d.growth.map(g => g.day), d.growth.map(g => g.views), null, 'Views per day')}
      <div style="margin-top:10px">${barsChart(d.growth.slice(-30).map(g => ({ day: g.day, subs: Math.max(0, g.subs) })), 'subs', null, 70)}</div><span class="mini">Subscribers gained per day · last 30 days</span></section>` : '';
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const all = d.heat.flat().filter(v => v != null), mx = Math.max(1, ...all);
  let cells = '';
  d.heat.forEach((row, i) => { cells += `<span class="mono muted" style="font-size:11px;align-self:center">${days[i]}</span>` + row.map((v, hh) => `<span class="c" title="${days[i]} ${hh}:00 — ${v == null ? 'no posts' : num(v) + ' views'}" style="background:${v == null ? 'var(--line2)' : `rgba(232,73,42,${(0.15 + 0.85 * v / mx).toFixed(2)})`}"></span>`).join(''); });
  cells += '<span></span>' + Array.from({ length: 24 }, (_, hh) => `<span class="mono muted" style="font-size:10px;text-align:center">${hh % 3 === 0 ? String(hh).padStart(2, '0') : ''}</span>`).join('');
  const heat = d.has_views ? `<section class="card">${hd('Best hours', `Average first-day views by publish hour (${esc(S.tz)})`)}${all.length >= 3 ? `<div class="tbl"><div class="heat">${cells}</div></div>${s.best_hours.length ? `<div class="mini" style="margin-top:8px">Best: ${s.best_hours.map(b => `<b>${String(b.hour).padStart(2, '0')}:00</b> (${num(b.avg)})`).join(' · ')}</div>` : ''}` : '<div class="empty">Appears after a few posts have views.</div>'}</section>` : '';
  const cad = `<section class="card">${hd('Posting rhythm', 'Published per day · last 30 days')}${barsChart(d.cadence.map(c => ({ day: c.day, published: c.published })), 'published', null, 90)}${d.cadence.some(c => c.failed) ? `<span class="mini" style="color:var(--bad)">${d.cadence.reduce((n, c) => n + c.failed, 0)} failed or unclear in 30 days</span>` : ''}</section>`;
  const om = Object.entries(d.origins), ot = om.reduce((n, [, v]) => n + v, 0) || 1;
  const origins = `<section class="card">${hd('Where posts came from')}${om.length ? om.sort((a, b) => b[1] - a[1]).map(([k, v]) => `<div class="ai-m" style="margin-bottom:10px"><div><span>${esc(ORIGIN[k] || (k === 'encore' ? 'Encore (best again)' : k))}</span><b class="mono">${v} · ${Math.round(100 * v / ot)}%</b></div><div class="meter"><span style="width:${Math.round(100 * v / ot)}%;background:var(--accent)"></span></div></div>`).join('') : '<div class="empty">No posts yet.</div>'}</section>`;
  const aiBox = `<section class="card ai-card ${d.ai_ready ? 'is-on' : ''}">${hd('AI analysis', 'Groq reads this account’s numbers and tells you what works and what to do next', `<button class="btn sm" data-act="accai" ${d.ai_ready ? '' : 'disabled'}>${ic('spark', 14)}Analyse</button>`)}
      ${d.ai_pick ? `<div class="ai-rep"><b style="font-size:12.5px">Last AI pick · ${esc(rel(d.ai_pick.at))}</b><p>“${esc(d.ai_pick.name)}” — ${esc(d.ai_pick.why || '')}</p></div>` : ''}
      <div id="accaiout">${d.ai_ready ? '<span class="mini">Press Analyse — takes a few seconds.</span>' : '<span class="mini">Add a Groq key on the <a href="#/ai">AI page</a> to use this.</span>'}</div></section>`;
  const rows = d.posts.map(p => `<div class="tr" style="grid-template-columns:minmax(0,2.4fr) 110px 90px 90px 80px 40px"><div style="min-width:0"><b class="ell" style="display:block">${esc(p.title || '—')}</b><span class="mini">${p.origin ? esc(ORIGIN[p.origin] || p.origin) : p.encore ? 'Encore' : 'Your Drive'} · ${esc(tFull(p.at))}</span></div>
    <span>${chip(p.status === 'published' ? 'published' : p.status, p.status === 'published' ? 'ok' : p.status === 'failed' || p.status === 'uncertain' ? 'bad' : 'info')}</span>
    <b class="mono" style="text-align:right">${d.has_views ? num(p.views) : '—'}</b><span class="mono mini" style="text-align:right">${d.has_views && p.first_day != null ? num(p.first_day) + ' d1' : ''}</span><span class="mono mini" style="text-align:right">${p.likes != null ? num(p.likes) + ' ♥' : ''}</span>
    <span>${p.video_id && kind === 'youtube' ? `<a class="tb-btn" style="width:30px;height:30px" href="https://youtu.be/${esc(p.video_id)}" target="_blank" rel="noopener" aria-label="Open">${ic('ext', 13)}</a>` : ''}</span></div>`).join('');
  const top = d.top.length ? `<section class="card">${hd('Top posts')}${d.top.map((t, i) => `<div class="lens-best" style="margin-bottom:8px;grid-template-columns:24px minmax(0,1fr) auto"><b class="mono">${i + 1}</b><b class="ell">${esc(t.title)}</b><b class="mono">${num(t.views)}</b></div>`).join('')}</section>` : '';
  mount('accounts', h.name, `${DST[kind][1]}${h.handle ? ' · ' + esc(h.handle) : ''}${h.auth_email ? ' · authorized by ' + esc(h.auth_email) : ''}${h.stats_at ? ' · stats ' + esc(rel(h.stats_at)) : ''}`, 'Account', `${h.url ? `<a class="btn" href="${esc(h.url)}" target="_blank" rel="noopener">${ic('ext', 15)}Open on ${DST[kind][1]}</a>` : ''}${h.client_id ? `<a class="btn" href="#/channel/${h.client_id}">${ic('tv', 15)}Client page</a>` : ''}`,
    `<div class="kp" style="grid-template-columns:repeat(${kind === 'youtube' ? 6 : 4},minmax(0,1fr))">${kp}</div>
     ${!d.has_views ? `<div class="note info" style="margin-bottom:12px">${ic('alert', 16)}<span>${DST[kind][1]} views are not read yet — posts, timing and reliability are shown. YouTube accounts show full view stats.</span></div>` : ''}
     ${growth ? `<div class="dgrid"><div class="s8">${growth}</div><div class="s4">${top || origins}</div></div>` : ''}
     <div class="dgrid"><div class="s6">${heat || cad}</div><div class="s6">${heat ? cad : origins}</div></div>
     <div class="dgrid"><div class="s5" style="display:flex;flex-direction:column;gap:12px">${growth ? origins : top}${aiBox}</div><div class="s7"><section class="card">${hd('Every post', `${d.posts.length} most recent`)}<div class="tbl"><div style="min-width:620px"><div class="tr th" style="grid-template-columns:minmax(0,2.4fr) 110px 90px 90px 80px 40px"><span>Post</span><span>Status</span><span style="text-align:right">Views</span><span style="text-align:right">Day one</span><span style="text-align:right">Likes</span><span></span></div>${rows || '<div class="empty">No posts yet.</div>'}</div></div></section></div></div>`);
  S.acts.accai = async () => {
    const out = document.getElementById('accaiout'); out.innerHTML = '<div class="sk sk-line" style="height:110px"></div>';
    try { const r = await api('POST', `/api/accounts/${kind}/${id}/analyse`); out.innerHTML = `<div class="ai-rep"><p style="white-space:pre-wrap;margin:0">${esc(r.text)}</p></div>`; }
    catch (e) { out.innerHTML = `<div class="note bad">${ic('alert', 16)}<span>${esc(e.message)}</span></div>`; }
  };
}

const PAGES = { accounts: pageAccounts, account: () => { const [, k, i] = location.hash.replace(/^#\/?/, '').split('?')[0].split('/'); return pageAccount(k, i); }, ai: pageAI, devices: pageDevices, channel: pageChannel, dashboard: pageDashboard, add: pageAdd, channels: pageChannels, groups: pageGroups, schedule: pageSchedule, videos: pageVideos, drive: pageDrive, settings: pageSettings, users: pageUsers, server: pageServer, login: () => authPage('login'), register: () => authPage('register') };
render();
