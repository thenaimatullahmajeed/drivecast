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
  const t = document.createElement('div');
  t.className = 'toast' + (bad ? ' bad' : '');
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), bad ? 6000 : 3500);
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
  ext: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>', trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
};
const ic = (n, s = 18) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON[n] || ''}</svg>`;
const LOGO = '<span class="logo"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#0E1116" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M5 8v8M9.5 5v14M14 9v6M18.5 6.5v11"/></svg></span>';
const chip = (t, tone = 'neutral', dot = true) => `<span class="chip ${tone}">${dot ? '<i></i>' : ''}${esc(t)}</span>`;
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
  const s = a < 60 ? 'less than a minute' : a < 3600 ? `${Math.round(a / 60)} min` : a < 86400 ? `${Math.floor(a / 3600)} h ${Math.round((a % 3600) / 60)} min` : `${Math.round(a / 86400)} days`;
  return d >= 0 ? `in ${s}` : `${s} ago`;
}
function countdown(ts) {
  const d = Math.max(0, Math.round(ts - Date.now() / 1000));
  const h = Math.floor(d / 3600), m = Math.floor((d % 3600) / 60), s = d % 60;
  return h ? `${h}h ${String(m).padStart(2, '0')}m` : `${m}m ${String(s).padStart(2, '0')}s`;
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
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" role="img" aria-label="${esc(label)}">
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

// ------------------------------------------------------------------ router
const NAV = [
  ['Workspace', [['grid', 'Dashboard', 'dashboard'], ['cal', 'Schedule', 'schedule'], ['tv', 'Channels', 'channels'], ['net', 'IP groups', 'groups'], ['film', 'Videos', 'videos'], ['drive', 'Google Drive', 'drive'], ['gear', 'Settings', 'settings']]],
  ['Admin', [['users', 'Users', 'users'], ['phone', 'Server', 'server']]],
];
function go(r) { if (location.hash !== '#/' + r) location.hash = '#/' + r; else render(); }
window.addEventListener('hashchange', () => render());

function clearTimers() { S.timers.forEach(t => clearInterval(t)); S.timers = []; }
function every(ms, fn) { S.timers.push(setInterval(fn, ms)); }

async function render() {
  clearTimers();
  S.acts = {};
  const [r0, ...rest] = (location.hash.replace(/^#\/?/, '') || 'dashboard').split('/');
  if (!S.session) {
    try { S.session = await api('GET', '/api/session'); } catch (e) { return fatal(e); }
    S.user = S.session.user;
    if (S.user) S.tz = S.user.timezone || S.tz;
  }
  if (!S.user) return authPage(r0 === 'register' ? 'register' : (S.session.needs_setup ? 'setup' : 'login'));
  const page = PAGES[r0] || PAGES.dashboard;
  if (['users', 'server'].includes(r0) && S.user.role !== 'owner') return go('dashboard');
  try { await page(...rest); } catch (e) { mount(r0, 'Something went wrong', '', '', '', `<div class="card err-card"><h2>Could not load this page</h2><p class="muted">${esc(e.message)}</p><button class="btn" data-act="reload">Try again</button></div>`); }
}
function fatal(e) { document.getElementById('app').innerHTML = `<div style="padding:40px"><h1>DriveCast</h1><p>${esc(e.message)}</p><button class="btn" data-act="reload">Retry</button></div>`; }

function mount(active, title, sub, kick, actions, content) {
  const nav = NAV.filter(([g]) => g !== 'Admin' || S.user.role === 'owner').map(([g, items]) => `<div class="nav"><span class="kicker">${g}</span>${items.map(([i, l, r]) => `<a href="#/${r}" class="${r === active ? 'on' : ''}">${ic(i)}${l}</a>`).join('')}</div>`).join('');
  document.getElementById('app').innerHTML = `<div class="app">
    <nav class="rail" aria-label="Main">
      <a class="brand" href="#/dashboard" aria-label="DriveCast home" style="text-decoration:none">${LOGO}<div style="display:flex;flex-direction:column"><strong>DriveCast</strong><span class="kicker">Studio</span></div></a>
      <div class="srv"><span class="kicker">Server</span><div style="display:flex;align-items:center;gap:8px"><span id="srvdot" style="width:8px;height:8px;border-radius:9px;background:#3DDC97"></span><span class="mono ell" style="font-size:12px;color:#fff">${esc(location.host)}</span></div><span id="srvline" style="font-size:12px;color:#8E94A0">Online</span></div>
      ${nav}
      <div style="flex-grow:1"></div>
      <div class="me"><span class="av" style="width:34px;height:34px">${esc(S.user.name.charAt(0).toUpperCase())}</span><div style="display:flex;flex-direction:column;flex-grow:1;min-width:0"><span class="ell" style="font-size:13px;color:#fff;font-weight:600">${esc(S.user.name)}</span><span style="font-size:12px;color:#8E94A0">${S.user.role === 'owner' ? 'Owner' : 'Member'}</span></div><button data-act="logout" aria-label="Sign out" title="Sign out">${ic('logout')}</button></div>
    </nav>
    <main class="main"><div class="wrap">
      <header class="top"><div><div class="kick">${esc(kick)}</div><h1>${esc(title)}</h1><div class="sub">${sub}</div></div><div class="actions">${actions}</div></header>
      <div id="content">${content}</div>
    </div></main></div>`;
}

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
};
async function copyText(t) {
  try { await navigator.clipboard.writeText(t); } catch (e) {
    const a = document.createElement('textarea'); a.value = t; document.body.appendChild(a); a.select(); document.execCommand('copy'); a.remove();
  }
}
function modal(html) { closeModal(); const d = document.createElement('div'); d.className = 'modal-bg'; d.id = 'modal'; d.innerHTML = `<div class="modal" role="dialog" aria-modal="true">${html}</div>`; d.addEventListener('click', e => { if (e.target === d) closeModal(); }); document.body.appendChild(d); const f = d.querySelector('input,select,button'); if (f) f.focus(); }
function closeModal() { const m = document.getElementById('modal'); if (m) m.remove(); }
function safe(fn, title) { try { return fn(); } catch (e) { console.error(e); return `<section class="card err-card"><h2>${esc(title || 'This card')} could not load</h2><p class="muted">${esc(e.message)}</p></section>`; } }
const hd = (title, sub, right = '') => `<div class="hd"><div><h2>${esc(title)}</h2>${sub ? `<p>${sub}</p>` : ''}</div><div class="actions">${right}</div></div>`;
const addBtn = `<a class="btn primary" href="#/add">${ic('plus', 16)}Add channel</a>`;

// ------------------------------------------------------------------ auth
function browserTz() { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Karachi'; } catch (e) { return 'Asia/Karachi'; } }
function authPage(mode) {
  const eq = Array.from({ length: 18 }, (_, i) => `<span style="height:${[40, 86, 58, 120, 72, 150, 96, 64, 132, 48, 104, 80, 56, 140, 90, 62, 118, 70][i]}px"></span>`).join('');
  const field = (n, l, t = 'text', ph = '', extra = '') => `<label class="f">${l}<input class="i" name="${n}" type="${t}" placeholder="${esc(ph)}" ${extra}></label>`;
  let form;
  if (mode === 'setup') form = `<div><h2 style="font-size:28px">Set up this server</h2><p class="muted">Create the owner account. The setup code is shown in the phone app.</p></div>
    ${field('setup_code', 'Setup code', 'text', 'SETUP-XXXX-XXXX', 'required autocomplete="off"')}${field('name', 'Your name', 'text', '', 'required')}${field('email', 'Email', 'email', '', 'required autocomplete="email"')}${field('password', 'Password (8+ characters)', 'password', '', 'required minlength="8" autocomplete="new-password"')}
    <button class="btn primary big" type="submit">Create owner account</button>`;
  else form = `<div class="tabs"><button type="button" class="${mode === 'login' ? 'on' : ''}" data-act="tab-login">Sign in</button><button type="button" class="${mode === 'register' ? 'on' : ''}" data-act="tab-register">Create account</button></div>
    ${mode === 'login' ? `<div><h2 style="font-size:28px">Welcome back</h2><p class="muted">Sign in to your workspace on this server.</p></div>
      ${field('email', 'Email', 'email', 'you@example.com', 'required autocomplete="email"')}${field('password', 'Password', 'password', '', 'required autocomplete="current-password"')}
      <button class="btn primary big" type="submit">Sign in</button>`
      : `<div><h2 style="font-size:28px">Create your account</h2><p class="muted">You need an invite code from the server owner. Your workspace is private — your own proxies, Drive and channels.</p></div>
      ${field('invite_code', 'Invite code', 'text', 'DC-XXXX-XXXX', 'required autocomplete="off"')}${field('name', 'Your name', 'text', '', 'required')}${field('email', 'Email', 'email', '', 'required autocomplete="email"')}${field('password', 'Password (8+ characters)', 'password', '', 'required minlength="8" autocomplete="new-password"')}
      <button class="btn primary big" type="submit">Create account</button>`}`;
  document.getElementById('app').innerHTML = `<div class="auth">
    <section class="hero"><div class="brand">${LOGO}<strong style="font-family:var(--disp);font-size:20px">DriveCast</strong><span class="kicker">Studio</span></div>
      <div style="flex-grow:1"></div><h1>Your channels,<br>on air while<br>you sleep.</h1>
      <p>Drive folders in, YouTube posts out — each channel on its own static IP, published at real peak hours.</p>
      <div class="eq">${eq}</div><div class="mono" style="font-size:12px;color:#8E94A0">● ${esc(location.host)}</div></section>
    <section class="side"><form data-form="auth" autocomplete="on">${form}</form></section></div>`;
  S.acts['tab-login'] = () => { history.replaceState(null, '', '#/login'); authPage('login'); };
  S.acts['tab-register'] = () => { history.replaceState(null, '', '#/register'); authPage('register'); };
  S.acts.auth = async v => {
    v.timezone = browserTz();
    const path = mode === 'setup' ? '/api/setup' : mode === 'register' ? '/api/register' : '/api/login';
    const r = await api('POST', path, v);
    S.user = r.user; S.tz = r.user.timezone; S.session = { user: r.user };
    location.hash = mode === 'login' ? '#/dashboard' : '#/settings';
    render();
  };
}

// ------------------------------------------------------------------ dashboard
async function pageDashboard() {
  const d = await api('GET', '/api/dashboard');
  const hasChannels = d.today.channels.length || d.leaderboard.length;
  const nxt = d.today.next;
  const sub = hasChannels ? `${d.today.done} of ${d.today.total} posts done today${nxt ? ` · next ${esc(rel(nxt.due_at))}` : ''}` : 'Let’s get your first channel on air';
  const content = hasChannels ? [
    safe(() => todayCard(d), 'Today'),
    safe(() => kpis(d), 'Numbers'),
    `<div class="row"><div class="f2">${safe(() => trendCard(d), 'Views')}</div><div class="f1">${safe(() => leaderCard(d), 'Leaderboard')}</div></div>`,
    `<div class="row"><div class="f3">${safe(() => heatCard(d), 'Best hours')}</div><div class="f1">${safe(() => consCard(d), 'Consistency')}</div></div>`,
    `<div class="row"><div class="f3">${safe(() => upnextCard(d), 'Up next')}</div><div class="f1">${safe(() => attentionCard(d), 'Needs you')}</div></div>`,
  ].join('') : onboarding(d);
  mount('dashboard', 'On air today', sub, fmt(Date.now() / 1000, { weekday: 'long', day: 'numeric', month: 'long' }), `<button class="btn" data-act="reload">${ic('refresh', 16)}Refresh</button>${addBtn}`, content);
  every(1000, () => document.querySelectorAll('[data-count]').forEach(el => { el.textContent = countdown(+el.dataset.count); }));
  every(60000, () => { if (!document.getElementById('modal')) render(); });
  S.acts['post-now'] = async x => { await api('POST', `/api/channels/${x.id}/post-now`); toast('Queued — it will post in a moment'); render(); };
  S.acts.dismiss = async x => { await api('POST', `/api/events/${x.id}/dismiss`); render(); };
}
function onboarding(d) {
  const step = (n, t, p, href, b) => `<div style="display:flex;gap:14px;padding:16px 0;border-top:1px solid var(--line)"><span class="av av1" style="width:32px;height:32px">${n}</span><div style="flex:1"><strong>${t}</strong><p class="muted" style="margin:4px 0 0">${p}</p></div><a class="btn sm" href="${href}">${b}</a></div>`;
  return `<section class="today"><span class="onair"><i></i>READY</span><h2 style="margin-top:14px;font-size:28px">Three steps to your first post</h2><p style="color:#A9AFBA;margin:8px 0 0">Do these once. After that DriveCast publishes on its own, inside peak hours.</p></section>
    <section class="card"><div class="list">${step(1, 'Add your Google API app', 'Paste your OAuth Client ID and secret, and register the redirect link shown.', '#/settings', 'Open Settings')}
    ${step(2, 'Connect Google Drive', 'Authorize your Drive once and paste your main folder link.', '#/drive', 'Connect Drive')}
    ${step(3, 'Add a channel', 'Pick an IP group, send the sign-in link, choose its folder and plan.', '#/add', 'Add channel')}</div></section>`;
}
function todayCard(d) {
  const t = d.today;
  const nx = t.next ? `<strong data-count="${t.next.due_at}">${countdown(t.next.due_at)}</strong><span style="color:#C9CDD6">${esc(tTime(t.next.due_at))} · ${esc(t.next.title)}</span>` : '<strong>—</strong><span>Nothing planned</span>';
  const rows = t.channels.map(c => {
    const dots = c.states.map(s => `<i class="${s === 'done' ? 'done' : ['held', 'failed', 'uncertain', 'missed'].includes(s) ? 'held' : 'next'}" title="${esc(s)}"></i>`).join('');
    return `<div><span class="ell" style="color:#fff">${esc(c.title)}</span><span style="display:flex;align-items:center;gap:10px"><span class="dots">${dots}</span><span class="mono" style="font-size:11px;color:#8E94A0">${c.next ? esc(tTime(c.next)) : 'done'}</span></span></div>`;
  }).join('');
  return `<section class="today"><div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap"><span class="onair"><i></i>ON AIR</span><h2 style="font-size:20px">Today</h2><span class="mono" style="font-size:12px;color:#8E94A0">Times in ${esc(S.tz)}</span></div>
    <div class="cols"><div class="stat"><span>Published</span><strong>${t.done} / ${t.total}</strong></div><div class="stat"><span>Next post</span>${nx}</div>
    <div class="stat"><span>Views today</span><strong>${num(t.views_today)}</strong></div><div class="stat"><span>Held or missed</span><strong style="color:${t.held ? '#F2B54A' : '#fff'}">${t.held}</strong></div></div>
    ${rows ? `<div class="chrow">${rows}</div>` : ''}</section>`;
}
function kpis(d) {
  const k = d.kpis;
  const card = (label, val, dl, series, color, foot) => `<section class="card" style="padding:18px 20px"><div style="display:flex;flex-direction:column;gap:10px"><span class="muted" style="font-size:13px;font-weight:500">${label}</span>
    <div style="display:flex;align-items:flex-end;justify-content:space-between;gap:8px"><strong class="big">${val}</strong>${series ? spark(series, 84, 30, color) : ''}</div>
    <div style="display:flex;align-items:center;gap:8px">${dl}<span class="muted" style="font-size:12px">${foot}</span></div></div></section>`;
  return `<div class="grid g-auto">${card('Views · 7 days', num(k.views_7d), delta(k.views_7d, k.views_7d_prev), k.views_series, '#0E1116', 'vs previous 7 days')}
    ${card('Published today', `${k.published_today} / ${k.planned_today}`, '', null, '', 'planned for today')}
    ${card('Avg views · first day', k.avg_first24 == null ? '—' : num(k.avg_first24), delta(k.avg_first24 || 0, k.avg_first24_prev), null, '', k.avg_first24 == null ? 'needs a day of stats' : 'per post, last 30 days')}
    ${card('Posting health', k.health == null ? '—' : k.health + '%', '', null, '', 'posts that went out on time, 7 days')}</div>`;
}
function trendCard(d) {
  const t = d.trend, sum = t.views.reduce((a, b) => a + b, 0), prev = (t.previous || []).reduce((a, b) => a + b, 0);
  const hasData = t.views.some(v => v > 0);
  return `<section class="card">${hd('Views across all channels', 'Daily views gained, from YouTube channel stats')}
    <div style="display:flex;align-items:baseline;gap:12px;margin-bottom:4px"><strong class="big" style="font-size:28px">${num(sum)}</strong>${delta(sum, prev)}<span class="muted" style="font-size:12px">last 30 days · ${num(t.total_views)} lifetime</span></div>
    ${hasData ? areaChart(t.days, t.views, t.previous, 'Daily views, last 30 days') : '<div class="empty">The chart fills in after the first two days of stats.</div>'}
    <div style="display:flex;gap:16px;margin-top:6px;font-size:12px" class="muted"><span>━ This period</span><span>┄ Previous period</span></div></section>`;
}
function leaderCard(d) {
  const rows = d.leaderboard.map((c, i) => `<div style="display:grid;grid-template-columns:22px minmax(0,1fr) 72px 64px;gap:12px;align-items:center;padding:11px 0;border-top:1px solid var(--line)">
    <span class="mono muted">${i + 1}</span><div class="who">${avatar(c.title, hashI(c.title), c.thumbnail, 30)}<div><span class="ell" style="font-weight:600">${esc(c.title)}</span>${handle(c.handle)}</div></div>
    ${spark(c.series, 72, 24, c.views_7d >= c.prev_7d ? '#0B5E57' : '#B42318', false)}<div style="display:flex;flex-direction:column;align-items:flex-end"><strong>${num(c.views_7d)}</strong>${delta(c.views_7d, c.prev_7d)}</div></div>`).join('');
  return `<section class="card" style="height:100%">${hd('Channel leaderboard', 'Views gained in the last 7 days')}${rows || '<div class="empty">No stats yet.</div>'}</section>`;
}
function heatCard(d) {
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  if (d.heat_samples < 10) return `<section class="card">${hd('Best hours to post', 'Average first-day views by publish hour, in your time')}<div class="empty">This map appears after about 10 posts have a full day of stats. Until then the scheduler uses known peak hours for your audience.</div></section>`;
  const all = d.heatmap.flat().filter(v => v != null), mx = Math.max(1, ...all);
  let cells = '';
  d.heatmap.forEach((row, i) => { cells += `<span class="mono muted" style="font-size:11px;align-self:center">${days[i]}</span>` + row.map((v, h) => `<span class="c" title="${days[i]} ${h}:00 — ${v == null ? 'no posts' : num(v) + ' views'}" style="background:${v == null ? '#F2F1EC' : `rgba(15,118,110,${(0.12 + 0.88 * v / mx).toFixed(2)})`}"></span>`).join(''); });
  cells += '<span></span>' + Array.from({ length: 24 }, (_, h) => `<span class="mono muted" style="font-size:10px;text-align:center">${h % 3 === 0 ? String(h).padStart(2, '0') : ''}</span>`).join('');
  return `<section class="card">${hd('Best hours to post', `Average first-day views by publish hour (${esc(S.tz)}) · ${d.heat_samples} posts`)}<div class="tbl"><div class="heat">${cells}</div></div></section>`;
}
function consCard(d) {
  return `<section class="card" style="height:100%">${hd('Upload consistency', 'Published vs planned, last 14 days')}${barsChart(d.consistency, 'published', 'planned')}
    <div style="display:flex;gap:14px;margin-top:12px;font-size:12px" class="muted"><span>■ Published</span><span>┄ Planned</span><span style="color:#E8492A">■ Today</span></div></section>`;
}
function upnextCard(d) {
  const rows = d.upnext.map(s => `<div style="display:grid;grid-template-columns:90px minmax(0,1fr) auto;gap:14px;align-items:center;padding:12px 0;border-top:1px solid var(--line)">
    <div style="display:flex;flex-direction:column"><strong class="mono" style="font-size:14px">${esc(tTime(s.due_at))}</strong><span class="muted" style="font-size:11px">${esc(rel(s.due_at))}</span></div>
    <div class="who">${avatar(s.title, hashI(s.title), null, 30)}<div><span class="ell" style="font-weight:600">${esc(s.title)}</span><span class="muted" style="font-size:12px">Group ${esc(s.group_number ?? '—')} · ${esc(s.market)} audience</span></div></div>
    ${s.group_status === 'down' ? chip('Held', 'warn') : s.status === 'planned' ? `<button class="btn sm" data-act="post-now" data-id="${s.channel_id}">Post now</button>` : chip('Posting', 'info')}</div>`).join('');
  return `<section class="card">${hd('Up next', 'Natural times inside peak hours', '<a href="#/schedule" style="font-size:13px;font-weight:600">Full schedule</a>')}${rows || '<div class="empty">Nothing planned yet.</div>'}</section>`;
}
function attentionCard(d) {
  const href = { groups: '#/groups', channels: '#/channels', uncertain: '#/videos' };
  const rows = d.attention.map(a => `<div style="display:flex;gap:12px;padding:12px 0;border-top:1px solid var(--line)"><span class="chip ${a.level === 'error' ? 'bad' : 'warn'}" style="width:32px;height:32px;padding:0;justify-content:center;border-radius:10px">${ic('alert', 16)}</span>
    <div style="display:flex;flex-direction:column;gap:3px;flex:1;min-width:0"><strong style="font-size:14px">${esc(a.title)}</strong>${a.detail ? `<span class="muted" style="font-size:13px">${esc(a.detail)}</span>` : ''}${a.at ? `<span class="muted mono" style="font-size:11px">${esc(rel(a.at))}</span>` : ''}</div>
    ${a.action === 'event' ? `<button class="btn sm" data-act="dismiss" data-id="${a.ref}">Dismiss</button>` : `<a class="btn sm" href="${href[a.action] || '#/dashboard'}">Open</a>`}</div>`).join('');
  return `<section class="card" style="height:100%">${hd('Needs you', null, d.attention.length ? chip(d.attention.length + ' open', 'coralc') : chip('All clear', 'ok'))}${rows || '<div class="empty">Nothing needs you right now.</div>'}</section>`;
}

// ------------------------------------------------------------------ add channel wizard
const W = {};
async function pageAdd(preselect) {
  Object.assign(W, { step: 0, group: null, link: null, channel: null, folder: null, plan: { posts_per_day: 2, audience: 'US+UK', min_gap_min: 240, jitter_min: 30 }, poll: null });
  const [g, ch, dr, st] = await Promise.all([api('GET', '/api/groups'), api('GET', '/api/channels'), api('GET', '/api/drive'), api('GET', '/api/settings')]);
  W.groups = g.groups; W.drive = dr; W.pending = ch.channels.filter(c => c.state === 'setup'); W.settings = st;
  if (preselect) {
    const c = ch.channels.find(x => String(x.id) === String(preselect));
    if (c) { W.channel = c; W.group = W.groups.find(x => x.id === c.group_id) || null; W.step = W.group ? 2 : 0; }
  }
  drawWizard();
}
function drawWizard() {
  const meta = [['IP group', 'Proxy first'], ['Sign-in link', 'Owner presses Allow'], ['Drive folder', 'Where videos come from'], ['Posting plan', 'How often and when'], ['Review', 'Start publishing']];
  const can = i => i === 0 || (i === 1 && W.group) || (i === 2 && W.channel) || (i === 3 && W.folder) || (i === 4 && W.folder);
  const steps = meta.map((m, i) => `<button class="step ${i === W.step ? 'on' : ''} ${i < W.step ? 'done' : ''}" data-act="wstep" data-i="${i}" ${can(i) ? '' : 'disabled'}><span class="n">${i < W.step ? '✓' : i + 1}</span><span style="display:flex;flex-direction:column;gap:3px"><b style="font-size:14px">${m[0]}</b><span class="muted" style="font-size:12px">${m[1]}</span></span></button>`).join('');
  const body = [wGroup, wLink, wFolder, wPlan, wReview][W.step]();
  const node = (k, v) => `<div class="node ${v ? 'on' : 'off'}"><span class="kicker">${k}</span><b>${esc(v || 'Not set')}</b></div>`;
  const plan = W.step >= 3 ? `${W.plan.posts_per_day} / day · ${W.plan.audience}` : null;
  const route = `<section class="routebar"><span class="kicker" style="margin-right:6px">Route</span>${node('IP group', W.group && `Group ${W.group.number}`)}<span style="color:#4B5260">→</span>${node('YouTube', W.channel && W.channel.title)}<span style="color:#4B5260">←</span>${node('Drive folder', W.folder && W.folder.name)}<span style="color:#4B5260">·</span>${node('Plan', plan)}</section>`;
  mount('channels', 'Add a channel', 'IP group → sign-in link → Drive folder → posting plan', 'New route', '<a class="btn" href="#/channels">Cancel</a>',
    `<div class="row" style="align-items:flex-start"><div style="flex:1 1 260px">${`<div class="steps">${steps}<div class="muted" style="margin:8px 12px 6px;padding:12px;border-radius:12px;background:var(--paper);font-size:12px;line-height:1.5">Proxy comes first. Once a channel joins a group, all of its Drive and YouTube traffic uses that IP.</div></div>`}</div>
     <div style="flex:3 1 560px"><section class="card" style="padding:28px">${body}</section></div></div>${route}`);
  Object.assign(S.acts, WIZ_ACTS);
  if (W.step === 1 && W.link && !W.channel) startPoll();
}
const wHead = (n, t, s) => `<div style="margin-bottom:20px"><div class="kick mono muted" style="font-size:11px;letter-spacing:.14em">STEP ${n} OF 5</div><h2 style="font-size:26px;margin-top:6px">${t}</h2><p class="muted" style="margin:6px 0 0">${s}</p></div>`;
const wNav = (nextOk, label = 'Continue') => `<div style="display:flex;justify-content:space-between;gap:12px;margin-top:26px;padding-top:18px;border-top:1px solid var(--line)"><button class="btn" data-act="wback" ${W.step === 0 ? 'disabled' : ''}>${ic('back', 16)}Back</button><button class="btn primary" data-act="wnext" ${nextOk ? '' : 'disabled'}>${label}${ic('arrow', 16)}</button></div>`;
function wGroup() {
  const cards = W.groups.map(g => {
    const pct = Math.min(100, Math.round(100 * g.channel_count / g.max_channels));
    const note = g.status === 'down' ? ['Not responding', 'var(--bad)'] : g.channel_count >= g.max_channels ? [`Full — above ${g.max_channels} is not recommended`, 'var(--warn)'] : [`Healthy · ${g.latency_ms ?? '—'} ms`, 'var(--ok)'];
    return `<button class="pick ${W.group && W.group.id === g.id ? 'sel' : ''}" data-act="wgroup" data-id="${g.id}" ${g.status === 'down' ? 'disabled' : ''}>
      <span style="display:flex;justify-content:space-between;width:100%"><strong style="font-family:var(--disp);font-size:17px">Group ${g.number}</strong><span class="mono" style="font-size:12px">${g.channel_count}/${g.max_channels}</span></span>
      <span class="mono muted" style="font-size:12px">${esc(g.outgoing_ip || g.host)}</span><span class="meter" style="width:100%"><span style="width:${pct}%;background:${g.channel_count > g.max_channels ? '#C27A12' : '#0E1116'}"></span></span>
      <span style="font-size:12px;font-weight:600;color:${note[1]}">${esc(note[0])}</span></button>`;
  }).join('');
  return wHead(1, 'Choose an IP group', 'Every upload for this channel will leave from this group’s static SOCKS5 IP.') +
    (W.groups.length ? `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px">${cards}</div>` : '<div class="empty"><h3>No IP groups yet</h3>Add your first SOCKS5 proxy below.</div>') +
    `<details style="margin-top:18px" ${W.groups.length ? '' : 'open'}><summary style="cursor:pointer;font-weight:650">+ New IP group</summary>${groupForm()}</details>` +
    (W.group ? `<div class="note ok" style="margin-top:16px">${ic('shield', 18)}<span><b>Ready.</b> Outgoing IP ${esc(W.group.outgoing_ip)} · checked ${esc(rel(W.group.last_check))} · DNS through proxy</span></div>` : '') + wNav(!!W.group);
}
function wLink() {
  if (W.channel) return wHead(2, 'Channel connected', 'Confirm this is the right channel.') + channelCard(W.channel) + wNav(true);
  const pending = W.pending.filter(c => c.group_id === (W.group && W.group.id));
  const pend = pending.length ? `<div style="margin-top:18px"><b style="font-size:13px">Or finish one that is already authorized</b>${pending.map(c => `<div style="display:flex;align-items:center;gap:12px;padding:10px 0;border-top:1px solid var(--line)">${avatar(c.title, hashI(c.title), c.thumbnail)}<div style="flex:1"><b>${esc(c.title)}</b><div>${handle(c.handle)}</div></div><button class="btn sm" data-act="wuse" data-id="${c.id}">Use this</button></div>`).join('')}</div>` : '';
  const box = W.link ? `<div class="linkbox"><span class="kicker">One-time authorization link</span><div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap"><span class="mono" style="font-size:14px;flex:1 1 280px;word-break:break-all">${esc(W.link.url)}</span><button class="btn coral" data-act="copy" data-text="${esc(W.link.url)}">${ic('copy', 16)}Copy link</button><a class="btn" href="${esc(W.link.url)}" target="_blank" rel="noopener">Open here</a></div><span style="font-size:12px;color:#8E94A0">Single use · expires ${esc(rel(W.link.expires_at))} · tied to Group ${W.group.number}</span></div>
      <div style="display:flex;align-items:center;gap:14px;margin-top:16px;padding:16px;border:1px dashed #C9C6BD;border-radius:14px"><span class="pulse"></span><div style="flex:1"><b>Waiting for Google authorization…</b><p class="muted" style="margin:3px 0 0;font-size:13px">This updates by itself when the channel owner presses Allow.</p></div></div>`
    : `<button class="btn primary big" data-act="wlink">${ic('key', 16)}Create sign-in link</button>`;
  return wHead(2, 'Send the sign-in link', 'Open it on any device, in any browser. The channel owner signs in with Google and presses Allow.') + box + pend + wNav(false, 'Waiting for Allow');
}
function channelCard(c) {
  return `<div style="display:flex;align-items:center;gap:14px;padding:16px;border:1px solid var(--line);border-radius:14px">${avatar(c.title, hashI(c.title), c.thumbnail, 48)}<div style="flex:1;display:flex;flex-direction:column;gap:3px"><b style="font-size:16px">${esc(c.title)}</b><span class="mono muted" style="font-size:12px">${esc(c.handle || '')} · ${esc(c.external_id)}</span><span class="muted" style="font-size:12px">${num(c.subscribers)} subscribers</span></div>${chip('Authorized', 'ok')}</div>`;
}
function wFolder() {
  const dr = W.drive;
  if (!dr.connected || !dr.root_id) return wHead(3, 'Connect the Drive folder', 'Google Drive is not connected yet.') + `<div class="empty"><h3>Connect Google Drive first</h3>Authorize Drive and paste your main folder link, then come back.<div style="margin-top:14px"><a class="btn primary" href="#/drive">Open Google Drive</a></div></div>` + wNav(false);
  const q = (W.q || '').toLowerCase();
  const name = (W.channel.title || '').toLowerCase().trim();
  const list = dr.folders.filter(f => !q || f.path.toLowerCase().includes(q)).sort((a, b) => (b.name.toLowerCase().trim() === name) - (a.name.toLowerCase().trim() === name)).slice(0, 60);
  const rows = list.map(f => `<button class="pick ${W.folder && W.folder.id === f.id ? 'sel' : ''}" data-act="wfolder" data-id="${esc(f.id)}" ${f.used ? 'disabled' : ''} style="flex-direction:row;align-items:center;gap:14px">${ic('folder', 20)}<span style="display:flex;flex-direction:column;gap:2px;flex:1"><b>${esc(f.name)}</b><span class="muted" style="font-size:12px">${esc(f.path)}${f.used ? ' · already used by another channel' : ''}</span></span>${f.name.toLowerCase().trim() === name ? chip('Name match', 'info', false) : ''}</button>`).join('');
  return wHead(3, 'Connect the Drive folder', `Folders inside “${esc(dr.root_name)}”, refreshed every 5 minutes. Name matches are suggested, never auto-assigned.`) +
    `<div style="display:flex;gap:10px;margin-bottom:14px"><input class="i" id="fq" placeholder="Search folders" value="${esc(W.q || '')}" aria-label="Search folders"><button class="btn" data-act="wrefresh">${ic('refresh', 16)}Refresh</button></div>
     <div style="display:flex;flex-direction:column;gap:8px;max-height:420px;overflow:auto">${rows || '<div class="empty">No folders found.</div>'}</div>` + wNav(!!W.folder);
}
function wPlan() {
  const p = W.plan;
  const sel = (id, label, opts, val) => `<label class="f">${label}<select class="i" id="${id}">${opts.map(([v, t]) => `<option value="${v}" ${String(v) === String(val) ? 'selected' : ''}>${t}</option>`).join('')}</select></label>`;
  return wHead(4, 'Set the posting plan', 'Direct publish at a real moment inside peak hours — never pre-scheduled on YouTube.') +
    `<div class="form" style="grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:18px">
      <div style="display:flex;flex-direction:column;gap:8px"><b style="font-size:13px">Posts per day</b><div class="stepper"><button data-act="ppd" data-d="-1" aria-label="Fewer">−</button><strong>${p.posts_per_day}</strong><button data-act="ppd" data-d="1" aria-label="More">+</button></div><span class="help">Default 2. 1–3 is best for new channels.</span></div>
      ${sel('aud', 'Audience country', [['US+UK', 'USA + UK (default)'], ['US', 'USA'], ['UK', 'United Kingdom'], ['CA', 'Canada'], ['AU', 'Australia']], p.audience)}
      ${sel('gap', 'Minimum gap between posts', [[120, '2 hours'], [180, '3 hours'], [240, '4 hours'], [360, '6 hours']], p.min_gap_min)}
      <label class="f">Natural timing · ±<span id="jv">${p.jitter_min}</span> min<input type="range" id="jit" min="0" max="60" step="5" value="${p.jitter_min}" style="height:44px"><span class="help">Each post lands at a slightly different minute every day.</span></label>
    </div><div class="note info" style="margin-top:20px">${ic('cal', 18)}<span>Posts go out at natural times inside the audience’s peak hours. You will always see the times in your own timezone (${esc(S.tz)}).</span></div>` + wNav(true);
}
function wReview() {
  const r = [['IP group', `Group ${W.group.number} · ${W.group.outgoing_ip}`], ['YouTube channel', `${W.channel.title} ${W.channel.handle || ''}`], ['Drive folder', W.folder.path], ['Posts per day', W.plan.posts_per_day], ['Audience', W.plan.audience + ' · peak hours'], ['Timing', `${W.plan.min_gap_min / 60} h minimum gap · ±${W.plan.jitter_min} min natural`], ['Publishing', 'Direct publish · Public'], ['Title & description', 'From the video file name']];
  return wHead(5, 'Review and go live', 'Nothing posts until you press Start. You can pause this route any time.') +
    `<div style="border:1px solid var(--line);border-radius:14px;padding:4px 18px">${r.map(([k, v], i) => `<div style="display:flex;justify-content:space-between;gap:16px;padding:13px 0;${i ? 'border-top:1px solid var(--line)' : ''}"><span class="muted">${esc(k)}</span><b style="text-align:right">${esc(v)}</b></div>`).join('')}</div>
     <button class="btn coral big" style="width:100%;margin-top:18px" data-act="wstart">Start publishing</button>
     <div style="margin-top:14px"><button class="btn" data-act="wback">${ic('back', 16)}Back</button></div>`;
}
function readPlanInputs() {
  const a = $('#aud'), g = $('#gap'), j = $('#jit');
  if (a) W.plan.audience = a.value;
  if (g) W.plan.min_gap_min = +g.value;
  if (j) W.plan.jitter_min = +j.value;
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
  wgroup: d => { W.group = W.groups.find(g => g.id === +d.id); if (W.channel && W.channel.group_id !== W.group.id) { W.channel = null; W.link = null; } drawWizard(); },
  wlink: async () => { W.link = await api('POST', '/api/connect', { purpose: 'youtube', group_id: W.group.id }); drawWizard(); },
  wuse: d => { W.channel = W.pending.find(c => c.id === +d.id); W.step = 2; clearTimers(); drawWizard(); },
  wfolder: d => { W.folder = W.drive.folders.find(f => f.id === d.id); drawWizard(); },
  wrefresh: async () => { W.drive = await api('POST', '/api/drive/refresh'); drawWizard(); },
  ppd: d => { readPlanInputs(); W.plan.posts_per_day = Math.max(1, Math.min(6, W.plan.posts_per_day + +d.d)); drawWizard(); },
  wstart: async () => {
    await api('POST', `/api/channels/${W.channel.id}/route`, { folder_id: W.folder.id, group_id: W.group.id, plan: W.plan, start: true });
    toast(`${W.channel.title} is live`);
    go('channels');
  },
  'save-group': null,
};
document.addEventListener('input', e => {
  if (e.target.id === 'fq') { W.q = e.target.value; const pos = e.target.selectionStart; drawWizard(); const n = $('#fq'); if (n) { n.focus(); n.setSelectionRange(pos, pos); } }
  if (e.target.id === 'jit') { const v = $('#jv'); if (v) v.textContent = e.target.value; }
});

// ------------------------------------------------------------------ IP groups
function groupForm() {
  return `<form data-form="newgroup" style="margin-top:14px"><div class="form">
    <label class="f">Host or IP<input class="i" name="host" required placeholder="proxy.example.com"></label>
    <label class="f">Port<input class="i" name="port" required inputmode="numeric" placeholder="1080"></label>
    <label class="f">Username<input class="i" name="username" autocomplete="off"></label>
    <label class="f">Password<input class="i" name="password" type="password" autocomplete="new-password"></label>
    <label class="f">Channels on this IP<select class="i" name="max_channels"><option value="1">1 · dedicated</option><option>2</option><option>3</option><option>4</option><option value="5" selected>5 · recommended max</option><option value="8">8 · not recommended</option><option value="10">10 · not recommended</option></select></label>
    <label class="f">Priority<select class="i" name="priority"><option value="high">High</option><option value="normal" selected>Normal</option><option value="low">Low</option></select></label>
  </div><div class="actions" style="margin-top:14px"><button class="btn" type="button" data-act="testgroup">${ic('bolt', 16)}Test connection</button><button class="btn primary" type="submit">Save group</button><span id="gtest" class="muted" style="font-size:13px"></span></div></form>`;
}
const GROUP_FORM_ACTS = {
  newgroup: async v => {
    const r = await api('POST', '/api/groups', v);
    toast(`Group ${r.group.number} saved · ${r.group.outgoing_ip}`);
    if (location.hash.startsWith('#/add')) { W.groups = (await api('GET', '/api/groups')).groups; W.group = W.groups.find(g => g.id === r.group.id); drawWizard(); } else render();
  },
  testgroup: async (d, el) => {
    const f = el.closest('form'); const v = Object.fromEntries(new FormData(f).entries()); const out = $('#gtest');
    out.innerHTML = '<span class="spinner"></span> Testing…';
    const r = await api('POST', '/api/groups/test', v);
    out.innerHTML = r.ok ? `<span style="color:var(--ok)">✓ Works · outgoing IP <b class="mono">${esc(r.ip)}</b> · ${r.latency_ms} ms</span>` : `<span style="color:var(--bad)">✗ ${esc(r.error)}</span>`;
  },
};
Object.assign(WIZ_ACTS, GROUP_FORM_ACTS);
function ring(n, cap, color) {
  const r = 34, c = 2 * Math.PI * r, p = Math.min(1, n / Math.max(1, cap));
  return `<svg width="88" height="88" viewBox="0 0 88 88" role="img" aria-label="${n} of ${cap} channels"><circle cx="44" cy="44" r="${r}" fill="none" stroke="#EDEBE6" stroke-width="8"/><circle cx="44" cy="44" r="${r}" fill="none" stroke="${color}" stroke-width="8" stroke-linecap="round" stroke-dasharray="${(c * p).toFixed(1)} ${c.toFixed(1)}" transform="rotate(-90 44 44)"/><text x="44" y="49" text-anchor="middle" font-family="Bricolage Grotesque, sans-serif" font-size="18" font-weight="700" fill="#0E1116">${n}/${cap}</text></svg>`;
}
async function pageGroups() {
  const { groups } = await api('GET', '/api/groups');
  const cards = groups.map(g => {
    const over = g.channel_count > g.max_channels, down = g.status === 'down';
    const color = down ? '#B42318' : over ? '#C27A12' : '#0E1116';
    const lat = (g.latency || []).slice(-24), mx = Math.max(1, ...lat);
    const chans = g.channels.map((c, i) => `<div style="display:flex;align-items:center;gap:10px;padding:9px 0;border-top:1px solid var(--line)">${avatar(c.title, hashI(c.title), c.thumbnail, 28)}<div style="display:flex;flex-direction:column;min-width:0;flex:1"><span class="ell" style="font-weight:600;font-size:13px">${esc(c.title)}</span>${handle(c.handle)}</div>${chip(c.state, c.state === 'live' ? 'ok' : 'neutral', false)}</div>`).join('');
    const ipChanged = down && (g.last_error || '').includes('changed');
    return `<section class="card"><div style="display:flex;flex-direction:column;gap:14px">
      <div style="display:flex;align-items:center;gap:16px">${ring(g.channel_count, g.max_channels, color)}<div style="display:flex;flex-direction:column;gap:6px;flex:1;min-width:0"><div style="display:flex;justify-content:space-between;gap:8px;align-items:center"><strong style="font-family:var(--disp);font-size:22px">Group ${g.number}</strong>${down ? chip('Not responding', 'bad') : chip('Healthy', 'ok')}</div><span class="mono" style="font-size:13px;font-weight:500">${esc(g.outgoing_ip || '—')}</span><span class="muted" style="font-size:12px">SOCKS5 · ${esc(g.host)}:${g.port} · ${esc(g.priority)} priority</span></div></div>
      <div><div style="display:flex;justify-content:space-between;font-size:12px" class="muted"><span>Latency · last checks</span><span class="mono" style="color:var(--ink)">${down ? 'timeout' : (g.latency_ms ?? '—') + ' ms'}</span></div><div style="display:flex;align-items:flex-end;gap:3px;height:34px;margin-top:6px">${lat.map(v => `<span style="flex:1;height:${Math.max(3, Math.round(v / mx * 34))}px;border-radius:2px;background:${v ? '#0E1116' : '#F2B8AD'}"></span>`).join('')}</div></div>
      ${down ? `<div class="note bad">${ic('alert', 16)}<span>${esc(g.last_error || 'Proxy is not responding')}. Uploads for these channels are held — never sent from the server IP.</span></div>` : ''}
      ${over ? `<div class="note warn">${ic('alert', 16)}<span>${g.channel_count} channels on one IP — above the recommended ${g.max_channels}. Allowed, at higher risk.</span></div>` : ''}
      <div>${chans || '<span class="muted" style="font-size:13px">No channels yet.</span>'}</div>
      <div class="actions">${ipChanged ? `<button class="btn primary" data-act="acceptip" data-id="${g.id}">Accept new IP</button>` : ''}<button class="btn" data-act="gtest" data-id="${g.id}">${ic('bolt', 16)}Test now</button><a class="btn" href="#/add">${ic('plus', 16)}Add channel</a><button class="btn" data-act="gedit" data-id="${g.id}">Edit</button>${g.channel_count ? '' : `<button class="btn danger" data-act="gdel" data-id="${g.id}">Delete</button>`}</div></div></section>`;
  }).join('');
  const nextN = groups.reduce((m, g) => Math.max(m, g.number), 0) + 1;
  mount('groups', 'IP groups', 'One static SOCKS5 IP per group · 1–5 channels recommended', 'Workspace', `<button class="btn" data-act="testall">${ic('bolt', 16)}Test all</button>`,
    `${groups.length ? `<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(340px,1fr));align-items:start">${cards}</div>` : ''}
     <div class="row"><div class="f3"><section class="card">${hd('New IP group', `Next number: Group ${nextN}`)}${groupForm()}</section></div>
     <div class="f1"><section class="card">${hd('Rules every group follows')}${[['No fallback', 'A failed proxy holds uploads. Traffic never leaves from the server IP.'], ['One channel, one IP', 'A channel never switches IP mid-route. Pause it before moving groups.'], ['IP check before upload', 'Upload starts only when the outgoing IP matches the group.'], ['DNS through proxy', 'Lookups use socks5h, so the real IP never leaks.']].map(([a, b]) => `<div style="display:flex;gap:12px;padding:12px 0;border-top:1px solid var(--line)"><span style="color:var(--ok)">${ic('check')}</span><div><b>${a}</b><div class="muted" style="font-size:13px">${b}</div></div></div>`).join('')}</section></div></div>`);
  Object.assign(S.acts, GROUP_FORM_ACTS, {
    gtest: async d => { const r = await api('POST', `/api/groups/${d.id}/test`); toast(r.group.status === 'ok' ? `Group ${r.group.number} is healthy` : `Group ${r.group.number}: ${r.group.last_error}`, r.group.status !== 'ok'); render(); },
    testall: async () => { for (const g of groups) { await api('POST', `/api/groups/${g.id}/test`).catch(() => { }); } render(); },
    acceptip: async d => { await api('POST', `/api/groups/${d.id}/accept-ip`); toast('New IP accepted'); render(); },
    gdel: async d => { if (!confirm('Delete this IP group?')) return; await api('DELETE', `/api/groups/${d.id}`); render(); },
    gedit: d => {
      const g = groups.find(x => x.id === +d.id);
      modal(`<h2>Edit Group ${g.number}</h2><form data-form="gsave" style="display:flex;flex-direction:column;gap:14px"><label class="f">Channels on this IP<input class="i" name="max_channels" type="number" min="1" value="${g.max_channels}"><span class="help">1–5 recommended. Above 5 is allowed but not recommended.</span></label>
        <label class="f">Priority<select class="i" name="priority">${['high', 'normal', 'low'].map(p => `<option ${p === g.priority ? 'selected' : ''}>${p}</option>`).join('')}</select><span class="help">Used for automatic failover when more phones are added.</span></label>
        <label class="f">New proxy password (optional)<input class="i" name="password" type="password" autocomplete="new-password"></label>
        <div class="actions"><button class="btn" type="button" data-act="close">Cancel</button><button class="btn primary" type="submit">Save</button></div></form>`);
      S.acts.gsave = async v => { if (!v.password) delete v.password; await api('PATCH', `/api/groups/${g.id}`, v); closeModal(); render(); };
    },
  });
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
  const COLS = 'grid-template-columns:minmax(200px,1.4fr) minmax(260px,1.8fr) 64px 130px 120px 80px 160px;gap:14px';
  const rows = shown.map(c => {
    const gdown = c.group && c.group.status === 'down';
    const routeChips = `<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap"><span class="chip ${gdown ? 'bad' : 'info'}">${ic('net', 14)}Group ${c.group ? c.group.number : '—'}</span><span class="muted">→</span><span class="chip neutral">${ic('yt', 14)}YouTube</span><span class="muted">←</span><span class="chip neutral" style="max-width:170px" title="${esc(c.folder_name)}">${ic('folder', 14)}<span class="ell">${esc(c.folder_name || '—')}</span></span></div>`;
    const t = c.today || {};
    return `<div class="tr" style="${COLS}"><div class="who">${avatar(c.title, hashI(c.title), c.thumbnail, 38)}<div><b class="ell">${esc(c.title)}</b><span class="muted" style="font-size:12px">${handle(c.handle)} · ${c.plan.posts_per_day} / day · ${esc(c.plan.audience)}</span></div></div>
      <div>${routeChips}${c.state_reason ? `<div class="muted" style="font-size:12px;margin-top:6px">${esc(c.state_reason)}</div>` : ''}</div>
      <span class="mono">${t.done || 0} / ${t.total || 0}</span><span style="font-size:13px">${c.next ? `${esc(tTime(c.next.due_at))}<br><span class="muted" style="font-size:12px">${esc(rel(c.next.due_at))}</span>` : '—'}</span>
      <span>${gdown && c.state === 'live' ? chip('Held · proxy', 'warn') : chip(names[c.state] || c.state, tones[c.state] || 'neutral')}</span>${c.series ? spark(c.series, 80, 26, '#0E1116', false) : '<span class="muted">—</span>'}
      <div class="actions" style="justify-content:flex-end">${c.state === 'live' ? `<button class="btn sm" data-act="cact" data-a="post-now" data-id="${c.id}" ${gdown ? 'disabled' : ''}>Post now</button>` : `<button class="btn sm primary" data-act="cact" data-a="resume" data-id="${c.id}">Resume</button>`}<button class="btn sm" data-act="cmore" data-id="${c.id}" aria-label="More for ${esc(c.title)}">•••</button></div></div>`;
  }).join('');
  const counts = s => routed.filter(c => s === 'all' || c.state === s).length;
  const tabs = ['all', 'live', 'paused', 'attention'].map(s => `<button class="btn sm ${filter === s ? 'primary' : ''}" data-act="chf" data-f="${s}">${s === 'all' ? 'All' : names[s]} · ${counts(s)}</button>`).join('');
  const used = new Set(channels.map(c => c.folder_id));
  const freeFolders = (dr.folders || []).filter(f => !used.has(f.id)).slice(0, 8);
  const unrouted = pending.map(c => `<div style="display:flex;align-items:center;gap:12px;padding:12px 0;border-top:1px solid var(--line)">${avatar(c.title, hashI(c.title), c.thumbnail)}<div style="flex:1"><b>${esc(c.title)}</b><div class="muted" style="font-size:13px">Authorized · needs a Drive folder and plan</div></div><a class="btn sm" href="#/add/${c.id}">Finish setup</a><button class="btn sm danger" data-act="cdel" data-id="${c.id}">Remove</button></div>`).join('') +
    freeFolders.map(f => `<div style="display:flex;align-items:center;gap:12px;padding:12px 0;border-top:1px solid var(--line)">${ic('folder', 20)}<div style="flex:1"><b>${esc(f.name)}</b><div class="muted" style="font-size:13px">Drive folder without a channel</div></div><a class="btn sm" href="#/add">Add channel</a></div>`).join('');
  mount('channels', 'Channels', 'Every channel with its IP group, Drive folder and plan', 'Workspace', addBtn,
    `<section class="card"><div class="actions" style="margin-bottom:16px">${tabs}</div>${routed.length ? `<div class="tbl"><div style="min-width:1090px"><div class="tr th" style="${COLS}"><span>Channel</span><span>Route · IP group → YouTube ← Drive</span><span>Today</span><span>Next post</span><span>Status</span><span>7 days</span><span></span></div>${rows || '<div class="empty">No channels in this view.</div>'}</div></div>` : `<div class="empty"><h3>No channels yet</h3>Add your first channel — it takes about two minutes.<div style="margin-top:14px">${addBtn}</div></div>`}</section>
     ${unrouted ? `<section class="card" style="margin-top:16px">${hd('Not routed yet', 'A route needs an IP group, an authorized channel and a Drive folder. Names alone never start uploads.')}${unrouted}</section>` : ''}`);
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
        <span class="help">Published: ${c.published_total} · Folder videos waiting: ${c.queue_count ?? '—'} · Group ${c.group ? c.group.number + ' · ' + esc(c.group.outgoing_ip || '') : '—'}</span></div>
      <div class="actions" style="justify-content:flex-end"><button class="btn" data-act="close">Close</button></div>`);
    S.acts.csave = async v => { await api('PATCH', `/api/channels/${c.id}`, { plan: v }); closeModal(); toast('Plan saved — upcoming posts re-planned'); render(); };
    S.acts.creconnect = async () => {
      if (!c.group) throw new Error('Choose an IP group first');
      const l = await api('POST', '/api/connect', { purpose: 'youtube', group_id: c.group.id });
      modal(`<h2>Reconnect ${esc(c.title)}</h2><p class="muted">Open this link and sign in with the channel’s Google account. It uses Group ${c.group.number}.</p><div class="linkbox"><span class="mono" style="word-break:break-all">${esc(l.url)}</span><button class="btn coral" data-act="copy" data-text="${esc(l.url)}">${ic('copy', 16)}Copy link</button></div><div class="actions" style="justify-content:flex-end"><button class="btn" data-act="close">Done</button></div>`);
    };
  };
}

// ------------------------------------------------------------------ schedule / videos
async function pageSchedule() {
  const { slots } = await api('GET', '/api/schedule?days=3');
  const tone = { planned: 'neutral', queued: 'info', running: 'info', done: 'ok', missed: 'warn', held: 'warn', skipped: 'neutral', failed: 'bad', uncertain: 'bad' };
  const byDay = {};
  slots.forEach(s => { const k = tDay(s.due_at); (byDay[k] = byDay[k] || []).push(s); });
  const html = Object.entries(byDay).map(([day, list]) => `<section class="card" style="margin-bottom:16px">${hd(day, `${list.length} posts`)}${list.map(s => `<div class="tr" style="grid-template-columns:110px minmax(0,1fr) 120px 140px"><b class="mono">${esc(tTime(s.due_at))}</b><div class="who">${avatar(s.title, hashI(s.title), null, 30)}<div><b class="ell">${esc(s.title)}</b><span class="muted" style="font-size:12px">Group ${esc(s.group_number ?? '—')}${s.note ? ' · ' + esc(s.note) : ''}</span></div></div><span>${chip(s.market + ' audience', s.market === 'UK' ? 'coralc' : 'info', false)}</span><span>${chip(s.status, tone[s.status] || 'neutral')}</span></div>`).join('')}</section>`).join('');
  mount('schedule', 'Schedule', `Today, tomorrow and the day after — in your time (${esc(S.tz)})`, 'Workspace', '', html || '<div class="empty"><h3>Nothing planned</h3>Add a channel to see its posting times here.</div>');
}
async function pageVideos() {
  const { videos } = await api('GET', '/api/videos?limit=200');
  const tone = { published: 'ok', uploading: 'info', reserved: 'info', failed: 'bad', uncertain: 'bad' };
  const rows = videos.map(v => `<div class="tr" style="grid-template-columns:minmax(240px,2fr) minmax(160px,1.2fr) 130px 160px 90px 200px"><div style="min-width:0"><b class="ell" style="display:block">${esc(v.title)}</b><span class="muted mono" style="font-size:11px">${esc(v.file_name)}</span></div><span class="ell">${esc(v.channel_title)}</span>
    <span>${chip(v.status === 'published' ? (v.visibility || 'public') : v.status, tone[v.status] || 'neutral')}</span><span style="font-size:13px">${esc(tFull(v.published_at || v.reserved_at))}</span><span class="mono">${v.views == null ? '—' : num(v.views)}</span>
    <div class="actions" style="justify-content:flex-end">${v.external_video_id ? `<a class="btn sm" href="https://youtu.be/${esc(v.external_video_id)}" target="_blank" rel="noopener">${ic('ext', 14)}YouTube</a>` : ''}${v.status === 'uncertain' ? `<button class="btn sm" data-act="unc" data-id="${v.id}">Resolve</button>` : ''}</div></div>`).join('');
  mount('videos', 'Videos', 'Everything DriveCast published or tried to publish', 'Workspace', '',
    `<section class="card">${videos.length ? `<div class="tbl"><div style="min-width:1000px"><div class="tr th" style="grid-template-columns:minmax(240px,2fr) minmax(160px,1.2fr) 130px 160px 90px 200px"><span>Video</span><span>Channel</span><span>Status</span><span>When</span><span>Views</span><span></span></div>${rows}</div></div>` : '<div class="empty"><h3>No videos yet</h3>Published posts will appear here with their views.</div>'}</section>`);
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
async function pageSettings() {
  const s = await api('GET', '/api/settings');
  const tzs = [...new Set([s.timezone, browserTz(), ...TZS])];
  mount('settings', 'Settings', 'Your timezone, Google API app and password', 'Workspace', '',
    `<div class="row"><div class="f3"><section class="card">${hd('Google API app', 'Each account uses its own Google Cloud OAuth app. This keeps quotas and limits separate.')}
      <ol class="muted" style="font-size:13px;line-height:1.7;margin:0 0 16px;padding-left:18px"><li>Google Cloud Console → APIs &amp; Services → enable <b>YouTube Data API v3</b> and <b>Google Drive API</b>.</li><li>OAuth consent screen → External → publishing status <b>In production</b> (Testing tokens expire after 7 days).</li><li>Credentials → Create OAuth client → <b>Web application</b> → add the redirect URI below.</li><li>Paste the Client ID and Client secret here.</li></ol>
      <div class="linkbox" style="margin-bottom:16px"><span class="kicker">Authorized redirect URI</span><div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><span class="mono" style="flex:1 1 260px;word-break:break-all">${esc(s.redirect_uri)}</span><button class="btn coral" data-act="copy" data-text="${esc(s.redirect_uri)}">${ic('copy', 16)}Copy</button></div></div>
      <form data-form="gapp" style="display:flex;flex-direction:column;gap:12px"><label class="f">Client ID<input class="i" name="google_client_id" value="${esc(s.google_client_id || '')}" placeholder="…apps.googleusercontent.com" autocomplete="off"></label>
      <label class="f">Client secret<input class="i" name="google_client_secret" type="password" placeholder="${s.has_secret ? 'Saved — leave empty to keep' : ''}" autocomplete="off"></label><div class="actions"><button class="btn primary" type="submit">Save app</button>${s.google_client_id && s.has_secret ? chip('Saved · secret encrypted', 'ok') : ''}</div></form></section></div>
    <div class="f1"><section class="card" style="margin-bottom:16px">${hd('Your timezone', 'Every time in the panel is shown in this zone.')}<form data-form="tz" class="actions"><select class="i" name="timezone" style="flex:1">${tzs.map(t => `<option ${t === s.timezone ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select><button class="btn primary" type="submit">Save</button></form></section>
      <section class="card" style="margin-bottom:16px">${hd('Change password')}<form data-form="pw" style="display:flex;flex-direction:column;gap:12px"><input class="i" type="password" name="current_password" placeholder="Current password" autocomplete="current-password" aria-label="Current password"><input class="i" type="password" name="new_password" placeholder="New password (8+)" autocomplete="new-password" aria-label="New password"><button class="btn" type="submit">Update password</button></form></section>
      <section class="card">${hd('Your link')}<span class="mono" style="word-break:break-all">${esc(s.public_url)}</span></section></div></div>`);
  S.acts.gapp = async v => { if (!v.google_client_secret) delete v.google_client_secret; await api('POST', '/api/settings', v); toast('Google app saved'); render(); };
  S.acts.tz = async v => { await api('POST', '/api/settings', v); S.tz = v.timezone; S.user.timezone = v.timezone; toast('Timezone saved'); render(); };
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
  mount('users', 'Users', 'Students and accounts on this server', 'Admin', `<button class="btn primary" data-act="newcode">${ic('key', 16)}New code</button>`,
    `<section class="card" style="margin-bottom:16px">${hd('Accounts on this server', 'Open a student to see their 30-day report (new tab). You see usage — never their passwords or tokens.')}<div class="tbl"><div style="min-width:900px"><div class="tr th" style="${COLS}"><span>User</span><span>Role</span><span>Channels</span><span>IP groups</span><span>Today</span><span>Status</span><span></span></div>${rows}</div></div></section>
     <section class="card">${hd('Invite codes', 'One code = one account. It turns Used the moment the student registers.')}${inv || '<div class="empty">No codes yet. Press “New code”.</div>'}</section>`);
  S.acts.udel = d => deleteUserModal(d.id, d.name, () => render());
  S.acts.revoke = async d => { await api('POST', `/api/admin/invites/${d.code}/revoke`); render(); };
  S.acts.newcode = () => {
    modal(`<h2>New invite code</h2><p class="muted" style="margin:0">Who is this for? Only you see this name.</p><form data-form="mkcode" style="display:flex;flex-direction:column;gap:14px">
      <label class="f">Name or note<input class="i" name="label" required placeholder="e.g. Usman — Batch 3"></label>
      <div class="form" style="grid-template-columns:repeat(3,minmax(0,1fr))"><label class="f">Max channels<input class="i" name="max_channels" type="number" min="1" value="5"></label><label class="f">Max IP groups<input class="i" name="max_groups" type="number" min="1" value="2"></label><label class="f">Posts / day cap<input class="i" name="daily_cap" type="number" min="1" value="20"></label></div>
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
  modal(`<h2>Delete ${esc(name)}?</h2><p class="muted" style="margin:0">This removes their account, channels, IP groups, schedule and history from DriveCast. Videos already on YouTube and files in their Drive are not touched. This cannot be undone.</p>
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
    `<div class="grid g-auto">${stat(`Views · ${days} days`, num(sum), 'all channels')}${stat(`Published · ${days} days`, published, `${failed} failed or unclear`)}${stat('Channels', r.channels.length, lim.max_channels ? `limit ${lim.max_channels}` : 'no limit')}${stat('IP groups', r.groups.length, r.groups.some(g => g.status === 'down') ? 'one is down' : 'all healthy')}${stat('Avg views · first day', k.avg_first24 == null ? '—' : num(k.avg_first24), 'per post')}</div>
     <div class="row"><div class="f2"><section class="card">${hd('Daily views', 'From this user’s workspace')}${r.trend.views.some(v => v) ? areaChart(r.trend.days, r.trend.views, null, 'Daily views') : '<div class="empty">No stats yet.</div>'}</section></div>
       <div class="f1"><section class="card" style="height:100%">${hd('Uploads per day')}${barsChart(r.daily_uploads.slice(-30), 'published', null, 110)}</section></div></div>
     <div class="row"><div class="f3"><section class="card">${hd('Channels', 'Ranked by views in this period')}<div class="tbl"><div style="min-width:760px"><div class="tr th" style="grid-template-columns:minmax(200px,1.6fr) 100px 100px 90px 150px 100px"><span>Channel</span><span>IP group</span><span>Views</span><span>Uploads</span><span>Last post</span><span>State</span></div>${chans || '<div class="empty">No channels.</div>'}</div></div></section></div>
       <div class="f1"><section class="card">${hd('Limits & access')}<form data-form="lim" style="display:flex;flex-direction:column;gap:10px"><label class="f">Max channels<input class="i" name="max_channels" type="number" min="1" value="${esc(lim.max_channels || '')}"></label><label class="f">Max IP groups<input class="i" name="max_groups" type="number" min="1" value="${esc(lim.max_groups || '')}"></label><label class="f">Posts / day cap<input class="i" name="daily_cap" type="number" min="1" value="${esc(lim.daily_cap || '')}"></label><div class="actions"><button class="btn" type="submit">Save limits</button><button class="btn" type="button" data-act="rpw">Reset password</button><button class="btn danger" type="button" data-act="udel2">Delete</button><button class="btn ${u.status === 'active' ? 'danger' : 'primary'}" type="button" data-act="susp">${u.status === 'active' ? 'Suspend' : 'Activate'}</button></div></form></section></div></div>
     <section class="card">${hd('Activity')}${r.events.map(e => `<div style="display:flex;gap:14px;padding:10px 0;border-top:1px solid var(--line)"><span class="mono muted" style="font-size:12px;min-width:140px">${esc(tFull(e.at))}</span><span style="font-size:13px">${esc(e.message)}</span></div>`).join('') || '<div class="empty">No activity yet.</div>'}</section>`);
  S.acts.repd = d => { S.repDays = +d.n; render(); };
  S.acts.udel2 = () => deleteUserModal(id, u.name, () => go('users'));
  S.acts.rpw = async () => {
    if (!confirm(`Give ${u.name} a new temporary password? They will be signed out.`)) return;
    const r = await api('POST', `/api/admin/users/${id}/reset-password`);
    modal(`<h2>New password for ${esc(u.name)}</h2><p class="muted" style="margin:0">Send this to them. They can change it in Settings after signing in.</p><div class="linkbox" style="flex-direction:row;align-items:center;justify-content:space-between"><span class="mono" style="font-size:22px;letter-spacing:.06em">${esc(r.temporary_password)}</span><button class="btn coral" data-act="copy" data-text="${esc(r.temporary_password)}">${ic('copy', 16)}Copy</button></div><button class="btn primary" data-act="close">Done</button>`);
  };
  S.acts.lim = async v => { await api('POST', `/api/admin/users/${id}`, { limits: v }); toast('Limits saved'); };
  S.acts.susp = async () => { const next = u.status === 'active' ? 'suspended' : 'active'; if (next === 'suspended' && !confirm(`Suspend ${u.name}? Their channels pause and they are signed out.`)) return; await api('POST', `/api/admin/users/${id}`, { status: next }); render(); };
}

// ------------------------------------------------------------------ server
async function pageServer() {
  const s = await api('GET', '/api/server');
  const svc = Object.entries(s.services).map(([n, v]) => { const age = Date.now() / 1000 - v.at; const okay = v.state !== 'error' && age < 900; return `<div style="display:flex;align-items:center;gap:12px;padding:12px 0;border-top:1px solid var(--line)"><span style="width:8px;height:8px;border-radius:9px;background:${okay ? '#1F9D55' : '#B42318'}"></span><div style="flex:1"><b>${esc(n)}</b><div class="muted" style="font-size:13px">${esc(v.state)}${v.last_error ? ' · ' + esc(v.last_error) : ''}</div></div><span class="mono muted" style="font-size:12px">${esc(rel(v.at))}</span></div>`; }).join('');
  const up = s.uptime, upTxt = `${Math.floor(up / 86400)}d ${Math.floor((up % 86400) / 3600)}h`;
  let dev = {}; try { dev = JSON.parse(s.device || '{}'); } catch (e) { /* none */ }
  mount('server', 'Server', 'The phone that runs DriveCast — health and services', 'Admin', '',
    `<div class="grid g-auto">${[['Uptime', upTxt], ['Version', s.version], ['Free storage', (s.disk_free / 1e9).toFixed(1) + ' GB'], ['Queue', `${s.jobs.queued || 0} waiting · ${s.jobs.running || 0} running`], ['Battery', dev.battery != null ? dev.battery + '%' : '—'], ['Temperature', dev.temp != null ? dev.temp + '°C' : '—']].map(([a, b]) => `<section class="card" style="padding:18px 20px"><span class="muted" style="font-size:13px">${a}</span><div class="big" style="font-size:24px;margin-top:6px">${esc(b)}</div></section>`).join('')}</div>
     <div class="row"><div class="f3"><section class="card">${hd('Services', 'Each service reports a heartbeat and restarts itself after an error.')}${svc}</section></div><div class="f1"><section class="card">${hd('Public link')}<span class="mono" style="word-break:break-all">${esc(s.public_url)}</span>${dev.model ? `<p class="muted" style="font-size:13px">${esc(dev.model)} · Android ${esc(dev.android || '')}</p>` : ''}</section></div></div>`);
}

const PAGES = { dashboard: pageDashboard, add: pageAdd, channels: pageChannels, groups: pageGroups, schedule: pageSchedule, videos: pageVideos, drive: pageDrive, settings: pageSettings, users: pageUsers, server: pageServer, login: () => authPage('login'), register: () => authPage('register') };
render();
