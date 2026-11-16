'use strict';
/* Phone screen: setup checklist + status. Talks to the Android app through the
   `DC` bridge (system settings) and to the local server (/local/status). */
const B = window.DC || null;
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const LOGO = '<span class="logo"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#0E1116" stroke-width="2.4" stroke-linecap="round"><path d="M5 8v8M9.5 5v14M14 9v6M18.5 6.5v11"/></svg></span>';

async function status() {
  try { const r = await fetch('/local/status', { cache: 'no-store' }); return await r.json(); } catch (e) { return null; }
}
function checks() {
  if (!B) return { items: [], tunnel: {}, running: true };
  try { return JSON.parse(B.checks()); } catch (e) { return { items: [], tunnel: {}, running: true }; }
}

async function draw() {
  if (document.activeElement && document.activeElement.tagName === 'INPUT') return; // don't wipe what is being typed
  const open = document.getElementById('tlog'); if (open && open.open) return;
  const c = checks(), s = await status();
  const items = c.items || [];
  const done = items.filter(i => i.ok).length;
  const ready = items.length && done === items.length;
  const running = c.running !== false;
  const t = c.tunnel || {};
  const url = (s && s.public_url) || t.url || '';
  const chip = running ? `<span class="chip" style="background:#E8492A">● ON AIR</span>` : `<span class="chip" style="background:#8E94A0">STOPPED</span>`;
  const eq = [30, 60, 44, 82, 52, 90, 64, 40, 76, 34, 58, 86].map((h, i) => `<span style="height:${h}%;background:${running ? '#E8492A' : '#3A4250'};opacity:${[.5, .7, .6, 1, .7, 1, .8, .5, .9, .5, .7, 1][i]}"></span>`).join('');
  const rows = items.map(i => `<div class="row"><span class="mark ${i.ok ? 'ok' : i.manual ? 'manual' : 'no'}">${i.ok ? '✓' : ''}</span><div class="t"><b>${esc(i.title)}</b><span>${esc(i.help)}</span></div>
    ${i.ok ? '' : `<button class="btn sm" data-fix="${esc(i.id)}">Fix</button>${i.manual ? `<button class="btn sm" data-confirm="${esc(i.id)}">Done</button>` : ''}`}</div>`).join('');
  const setupCode = s && s.setup_code ? `<div class="card"><div class="kick">First time · owner setup code</div><div class="mono" style="font-size:22px;margin:8px 0;letter-spacing:.06em">${esc(s.setup_code)}</div><span class="muted" style="font-size:13px">Open the link below on your laptop, choose “Set up”, and enter this code.</span></div>` : '';
  const login = t.state === 'needs_login' && t.auth_url;
  const tunnelForm = `<div class="card"><div class="kick">Public link · Tailscale</div>
    ${login ? `<p style="font-size:14px;margin:10px 0 12px">Sign in to Tailscale to finish. After you approve, the link appears here by itself.</p><button class="btn coral wide" data-open="${esc(t.auth_url)}">Open Tailscale sign-in</button>`
      : `<p class="muted" style="font-size:13px;margin:8px 0 12px">${t.state === 'starting' || t.state === 'joined' ? 'Connecting… this can take a minute.' : t.error ? 'Error: ' + esc(t.error) : 'Sign in with your Tailscale account once. No key needed.'}</p>
      <label>Server name<input id="thost" value="${esc(t.hostname || 'drivecast')}" autocomplete="off"></label>
      <button class="btn coral wide" style="margin-top:12px" data-act="tunnel">Sign in to Tailscale</button>
      <details style="margin-top:12px"><summary class="muted" style="font-size:13px">Use an auth key instead</summary><label style="margin-top:10px">Auth key (tskey-auth-…)<input id="tkey" placeholder="tskey-auth-…" autocomplete="off"></label></details>`}</div>`;
  const urlNow = url || t.url || '';
  const logBox = `<details class="card" id="tlog"><summary class="muted" style="font-size:13px">Link details (for support)</summary>
    <p style="font-size:13px;margin:10px 0">State: <b>${esc(t.state || '—')}</b>${urlNow ? ' · ' + esc(urlNow) : ''}${t.error ? '<br><span style="color:#FF9B85">' + esc(t.error) + '</span>' : ''}</p>
    <button class="btn sm" data-copylog="1">Copy details</button>
    <pre style="white-space:pre-wrap;word-break:break-all;font-size:10px;line-height:1.4;max-height:320px;overflow:auto;background:#0B0E12;padding:10px;border-radius:10px;margin-top:10px">${esc(B && B.tunnelLog ? B.tunnelLog() : '')}</pre></details>`;
  document.getElementById('root').innerHTML = `
    <div class="top">${LOGO}<b>DriveCast Server</b>${chip}</div>
    ${!ready ? `<div><h1>Get this phone ready</h1><p class="muted" style="margin:8px 0 10px">${done} of ${items.length} done. Each one is checked for you.</p><div class="bar"><span style="width:${items.length ? Math.round(100 * done / items.length) : 0}%"></span></div></div>
      <div class="card">${rows}</div>` : `<div class="eq">${eq}</div><h1>${running ? 'Running in the background.' : 'Server is stopped.'}</h1>
      <p class="muted" style="margin:0">${running ? 'Everything is managed from your browser. Keep the phone on Wi-Fi and the charger.' : 'Uploads are paused. Start again and it resumes safely — nothing is posted twice.'}</p>`}
    ${setupCode}
    ${url ? `<div class="card"><div class="kick">Open on your laptop</div><div class="mono" style="font-size:15px;margin:8px 0;word-break:break-all">${esc(url)}</div><div style="display:flex;gap:8px"><button class="btn sm" data-copy="${esc(url)}">Copy link</button><button class="btn sm" data-open="${esc(url)}">Open</button></div></div>` : tunnelForm}
    ${s ? `<div class="grid"><div class="stat"><span>Accounts</span><b>${s.accounts}</b></div><div class="stat"><span>Live channels</span><b>${s.channels_live}</b></div><div class="stat"><span>Posted 24h</span><b>${s.published_today}</b></div></div>` : '<p class="muted">Server is starting…</p>'}
    ${c.device ? `<div class="grid"><div class="stat"><span>Battery</span><b>${esc(c.device.battery)}%</b></div><div class="stat"><span>Temp</span><b>${esc(c.device.temp)}°C</b></div><div class="stat"><span>Charging</span><b>${c.device.charging ? 'Yes' : 'No'}</b></div></div>` : ''}
    ${ready ? `<p class="muted" style="text-align:center;font-size:12px;margin:4px 0">You can close this app — the server keeps running.</p>` : ''}
    ${url && t.state === 'online' ? `<details><summary class="muted" style="font-size:13px">Change public link</summary>${tunnelForm}</details>` : ''}
    ${logBox}
    <button class="btn wide" data-act="${running ? 'stop' : 'start'}" style="${running ? 'color:#FF9B85' : 'background:#E8492A;color:#0E1116;border-color:#E8492A'}">${running ? 'Stop server' : 'Start server'}</button>`;
}

document.addEventListener('click', e => {
  const el = e.target.closest('button');
  if (!el || !B) return;
  if (el.dataset.copylog) { B.copy('state=' + JSON.stringify(checks().tunnel) + '\n' + B.tunnelLog()); el.textContent = 'Copied'; return; }
  if (el.dataset.fix) B.fix(el.dataset.fix);
  else if (el.dataset.confirm) { B.confirm(el.dataset.confirm); draw(); }
  else if (el.dataset.copy) { B.copy(el.dataset.copy); el.textContent = 'Copied'; }
  else if (el.dataset.open) B.open(el.dataset.open);
  else if (el.dataset.act === 'tunnel') {
    const ke = document.getElementById('tkey'), k = ke ? ke.value.trim() : '', h = document.getElementById('thost').value.trim() || 'drivecast';
    if (k && !/^tskey-auth-/.test(k)) { alertBox('An auth key starts with tskey-auth-. Or leave it empty and sign in.'); return; }
    B.setTunnel(k, h); el.textContent = 'Connecting…'; setTimeout(draw, 4000);
  } else if (el.dataset.act === 'stop') { if (confirm('Stop the server? Posting pauses until you start it again.')) B.stop(); }
  else if (el.dataset.act === 'start') B.start();
});
function alertBox(m) { const p = document.createElement('p'); p.style.color = '#FF9B85'; p.textContent = m; document.getElementById('root').appendChild(p); }
document.addEventListener('visibilitychange', () => { if (!document.hidden) draw(); });
draw();
setInterval(draw, 5000);
