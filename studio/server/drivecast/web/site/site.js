'use strict';
/* drivecast.site — landing actions: live studios, Sign in (finds your phone), Get started (allocates one). */
(function () {
  const $ = s => document.querySelector(s);
  const post = (url, body) => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-DC': '1' }, body: JSON.stringify(body) })
    .then(r => r.json().then(j => { if (!r.ok) throw new Error(j.error || 'Something went wrong'); return j; }));
  function open(id) { document.querySelectorAll('.bg').forEach(b => b.classList.remove('on')); const d = $(id); d.classList.add('on'); const i = d.querySelector('input'); if (i) i.focus(); }
  document.addEventListener('click', e => {
    const t = e.target.closest('[data-open]'); if (t) { e.preventDefault(); open(t.dataset.open); }
    if (e.target.classList.contains('bg') || e.target.closest('[data-close]')) document.querySelectorAll('.bg').forEach(b => b.classList.remove('on'));
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') document.querySelectorAll('.bg').forEach(b => b.classList.remove('on')); });
  function wire(form, url, field, errSel) {
    $(form).addEventListener('submit', e => {
      e.preventDefault();
      const btn = $(form).querySelector('button[type=submit]'), err = $(errSel);
      err.textContent = ''; btn.disabled = true; const label = btn.textContent; btn.textContent = 'Finding your studio…';
      post(url, { [field]: $(form).querySelector('input').value.trim() })
        .then(j => { location.href = j.url; })
        .catch(x => { err.textContent = x.message; btn.disabled = false; btn.textContent = label; });
    });
  }
  wire('#fin', '/site/api/where', 'email', '#ein');
  wire('#fup', '/site/api/join', 'invite', '#eup');
  // studios online, live
  function fleet() {
    fetch('/site/api/fleet', { cache: 'no-store' }).then(r => r.json()).then(f => {
      const box = $('#devs'); if (!box) return;
      box.innerHTML = f.devices.map(d => `<span class="dev ${d.online ? '' : 'off'}"><i></i>${String(d.label).replace(/[<>&"]/g, '')}</span>`).join('');
      const on = f.devices.filter(d => d.online).length;
      $('#devn').textContent = `${on} studio${on === 1 ? '' : 's'} on air`;
      $('#upnote').textContent = f.open ? 'Sign-ups are open. An invite code is optional.' : 'Sign-ups are by invite. Enter the code you received.';
      $('#upcode').required = !f.open;
    }).catch(() => { });
  }
  fleet(); setInterval(fleet, 20000);
})();
