'use strict';
/* WHY Partner Portal — read-only client. Every number comes from
   /partners/api/*; the browser formats, filters and draws, and never computes
   headline performance itself. All DOM writes go through textContent or
   element construction — no innerHTML with data. */
(function () {
  const $ = (s, r) => (r || document).querySelector(s);
  const API = '/partners/api/';
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

  async function api(ep, opts) {
    const r = await fetch(API + ep, Object.assign({ credentials: 'same-origin', cache: 'no-store' }, opts || {}));
    if (r.status === 401 && ep !== 'login') { location.href = '/partners/login'; throw new Error('unauthenticated'); }
    return r;
  }

  /* ---------- signup / verify pages ---------- */
  const fs = $('#fs'), fv = $('#fv');
  if (fs && fv) {
    const showVerify = (email) => { $('#step1').hidden = true; $('#step2').hidden = false; fv.email.value = email || ''; fv.code.focus(); };
    if (location.pathname.endsWith('/verify')) showVerify(new URLSearchParams(location.search).get('email'));
    fs.addEventListener('submit', async (ev) => {
      ev.preventDefault(); const b = $('#bs'), e = $('#es'); b.disabled = true; e.textContent = '';
      const fd = new FormData(fs);
      try {
        const exchange = (fd.get('api_key') || fd.get('api_secret')) ? { exchange: fd.get('exchange'), api_key: fd.get('api_key'), api_secret: fd.get('api_secret') } : undefined;
        const r = await api('signup', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: fd.get('name'), email: fd.get('email'), password: fd.get('password'), exchange }) });
        const j = await r.json().catch(() => ({}));
        if (r.ok) { showVerify(fd.get('email')); if (j.message) $('#msg').textContent = j.message; if (j.mail_configured === false) $('#msg').textContent = 'Your request is registered. Email delivery is not yet enabled — WHY will activate your account or send your code shortly.'; }
        else e.textContent = j.error || 'Could not start signup.';
      } catch (_) { e.textContent = 'Could not reach the portal. Try again.'; }
      b.disabled = false;
    });
    fv.addEventListener('submit', async (ev) => {
      ev.preventDefault(); const b = $('#bv'), e = $('#ev'); b.disabled = true; e.textContent = '';
      const fd = new FormData(fv);
      try {
        const r = await api('verify', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: fd.get('email'), code: fd.get('code') }) });
        if (r.ok) { location.href = '/partners'; return; }
        e.textContent = 'Invalid or expired code.';
      } catch (_) { e.textContent = 'Could not reach the portal. Try again.'; }
      b.disabled = false;
    });
    return;
  }

  /* ---------- login page ---------- */
  const form = $('#f');
  if (form) {
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const b = $('#b'), e = $('#e'); b.disabled = true; e.textContent = '';
      const fd = new FormData(form);
      try {
        const r = await api('login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: fd.get('username'), password: fd.get('password') }) });
        if (r.ok) { location.href = '/partners'; return; }
        e.textContent = r.status === 403 ? 'Request refused.' : 'Invalid credentials.';
      } catch (_) { e.textContent = 'Could not reach the portal. Try again.'; }
      b.disabled = false;
    });
    return;
  }

  /* ---------- formatting ---------- */
  const fmtUsd = (v, sign) => v == null ? '—' : (sign && v > 0 ? '+' : '') + (v < 0 ? '−' : '') + '$' + Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const fmtPct = (v, d) => v == null ? '—' : (v > 0 ? '+' : '') + v.toFixed(d == null ? 2 : d) + '%';
  const fmtPx = (v) => v == null ? '—' : v >= 1000 ? v.toLocaleString('en-US', { maximumFractionDigits: 2 }) : v >= 1 ? v.toFixed(4).replace(/0+$/, '').replace(/\.$/, '') : v.toPrecision(5).replace(/0+$/, '').replace(/\.$/, '');
  const fmtQty = (v) => v == null ? '—' : v >= 100 ? v.toLocaleString('en-US', { maximumFractionDigits: 1 }) : v.toPrecision(5).replace(/0+$/, '').replace(/\.$/, '');
  const pad = (n) => String(n).padStart(2, '0');
  const fmtTs = (ms) => { if (!ms) return '—'; const d = new Date(ms); return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}Z`; };
  const fmtDur = (min) => { if (min == null) return '—'; if (min < 60) return Math.round(min) + 'm'; const h = Math.floor(min / 60), m = Math.round(min % 60); return h < 48 ? `${h}h ${pad(m)}m` : `${Math.floor(h / 24)}d ${h % 24}h`; };
  const cls = (v) => v == null ? '' : v > 0 ? 'pos' : v < 0 ? 'neg' : '';
  const sideEl = (s) => el('span', 'side ' + s, s.toUpperCase());

  /* ---------- state ---------- */
  let summary = null, trades = [], open = [], equity = [], notes = {};
  let sortK = 'closed_ms', sortD = -1, page = 0, PAGE = 25, range = 0;

  /* ---------- KPIs ---------- */
  function kpi(label, val, sub, hi, valCls) {
    const k = el('div', 'kpi' + (hi ? ' hi' : '')); k.appendChild(el('div', 'l', label));
    const v = el('div', 'v' + (val == null ? ' na' : '') + (valCls ? ' ' + valCls : ''), val == null ? 'Unavailable' : val); k.appendChild(v);
    k.appendChild(el('div', 's', sub || '')); return k;
  }
  function renderKpis() {
    const s = summary, box = $('#kpis'); box.replaceChildren();
    box.append(
      kpi('Paper NAV', fmtUsd(s.nav_usd), `On $${s.capital_usd.toLocaleString()} starting capital · ${s.leverage_x}x`, true),
      kpi('Total P&L', fmtUsd(s.total_usd, true), 'Realized + unrealized', true, cls(s.total_usd)),
      kpi('Return', fmtPct(s.return_pct), `Realized ${fmtPct(s.realized_return_pct)}`, true, cls(s.return_pct)),
      kpi('Realized P&L', fmtUsd(s.realized_usd, true), `${s.closed_trades} closed trades`, false, cls(s.realized_usd)),
      kpi('Unrealized P&L', s.unrealized_usd == null ? null : fmtUsd(s.unrealized_usd, true), s.unrealized_usd == null ? 'No live marks' : `${s.unrealized_marked}/${s.unrealized_of} positions marked`, false, cls(s.unrealized_usd)),
      kpi('Open positions', String(s.open_positions), `${s.longs}L / ${s.shorts}S closed mix`),
      kpi('Win rate', s.win_pct == null ? null : s.win_pct.toFixed(1) + '%', `${s.wins}W / ${s.losses}L`),
      kpi('Profit factor', s.profit_factor == null ? null : s.profit_factor.toFixed(2), 'Gross win ÷ gross loss'),
      kpi('Average winner', fmtUsd(s.avg_win_usd, true), `Best ${fmtUsd(s.best_usd, true)}`, false, 'pos'),
      kpi('Average loser', fmtUsd(s.avg_loss_usd, true), `Worst ${fmtUsd(s.worst_usd, true)}`, false, 'neg'),
      kpi('Max drawdown', fmtUsd(s.max_drawdown_usd), `${fmtPct(s.max_drawdown_pct)} · closed-trade basis`, false, 'neg'),
      kpi('Avg hold', fmtDur(s.avg_hold_min), `${s.models} models active`)
    );
  }

  /* ---------- chart ---------- */
  const svgNS = 'http://www.w3.org/2000/svg';
  const sv = (t, a) => { const e = document.createElementNS(svgNS, t); for (const k in a) e.setAttribute(k, a[k]); return e; };
  function renderChart() {
    const svg = $('#svg'); svg.replaceChildren();
    const W = 900, H = 320, L = 64, R = 16, T = 18, B = 34;
    const now = Date.now();
    let pts = equity.filter(p => p.ts > 0);
    if (range) { const from = now - range * 864e5; const before = pts.filter(p => p.ts < from); pts = pts.filter(p => p.ts >= from); if (before.length) pts.unshift({ ...before[before.length - 1], ts: from, carried: true }); }
    $('#rangeinfo').textContent = pts.filter(p => !p.carried).length ? `${pts.filter(p => !p.carried).length} closes in range` : 'No closed trades in this range';
    if (pts.length < 1) { svg.appendChild(sv('text', { x: W / 2, y: H / 2, fill: '#6f6a7d', 'text-anchor': 'middle', 'font-size': 14 })).textContent = 'No closed trades in this range'; return; }
    if (pts.length === 1) pts = [{ ...pts[0], ts: pts[0].ts - 36e5 }, pts[0]];
    const cap = summary.capital_usd;
    const xs = pts.map(p => p.ts), t0 = Math.min(...xs), t1 = Math.max(now, ...xs);
    let lo = Math.min(cap, ...pts.map(p => p.nav_usd)), hi = Math.max(cap, ...pts.map(p => p.nav_usd));
    const padY = Math.max((hi - lo) * 0.15, cap * 0.004); lo -= padY; hi += padY;
    const X = t => L + (t - t0) / Math.max(1, t1 - t0) * (W - L - R), Y = v => T + (hi - v) / (hi - lo) * (H - T - B);
    // grid + y labels
    const g = sv('g', { class: 'grid' });
    for (let i = 0; i <= 4; i++) { const v = lo + (hi - lo) * i / 4, y = Y(v); g.appendChild(sv('line', { x1: L, x2: W - R, y1: y, y2: y, stroke: '#2a2836', 'stroke-width': 1 }));
      const t = sv('text', { x: L - 8, y: y + 4, fill: '#a8a2b5', 'font-size': 11, 'text-anchor': 'end', 'font-family': 'JetBrains Mono, monospace' }); t.textContent = '$' + Math.round(v).toLocaleString(); g.appendChild(t); }
    // x labels
    const nx = 5; for (let i = 0; i <= nx; i++) { const t = t0 + (t1 - t0) * i / nx, d = new Date(t); const lab = sv('text', { x: X(t), y: H - 10, fill: '#a8a2b5', 'font-size': 11, 'text-anchor': i === 0 ? 'start' : i === nx ? 'end' : 'middle' }); lab.textContent = (t1 - t0) < 3 * 864e5 ? `${pad(d.getUTCDate())} ${d.toLocaleString('en', { month: 'short', timeZone: 'UTC' })} ${pad(d.getUTCHours())}:00` : `${pad(d.getUTCDate())} ${d.toLocaleString('en', { month: 'short', timeZone: 'UTC' })}`; g.appendChild(lab); }
    svg.appendChild(g);
    // capital baseline
    svg.appendChild(sv('line', { x1: L, x2: W - R, y1: Y(cap), y2: Y(cap), stroke: '#a07d28', 'stroke-dasharray': '4 5', 'stroke-width': 1 }));
    // step path (each close books the money at once)
    let d = `M${X(pts[0].ts)},${Y(pts[0].nav_usd)}`, area = d;
    for (let i = 1; i < pts.length; i++) { d += ` H${X(pts[i].ts)} V${Y(pts[i].nav_usd)}`; }
    d += ` H${X(t1)}`;
    area = d + ` V${Y(lo)} H${X(pts[0].ts)} Z`;
    const defs = sv('defs', {}); const grad = sv('linearGradient', { id: 'ga', x1: 0, y1: 0, x2: 0, y2: 1 });
    grad.appendChild(sv('stop', { offset: '0', 'stop-color': '#f3c76a', 'stop-opacity': .28 })); grad.appendChild(sv('stop', { offset: '1', 'stop-color': '#f3c76a', 'stop-opacity': 0 })); defs.appendChild(grad); svg.appendChild(defs);
    svg.appendChild(sv('path', { d: area, fill: 'url(#ga)' }));
    // drawdown bars (from running peak)
    let peak = -Infinity; const dd = sv('g', {});
    for (let i = 0; i < pts.length; i++) { peak = Math.max(peak, pts[i].nav_usd); const nx2 = i + 1 < pts.length ? X(pts[i + 1].ts) : X(t1); if (pts[i].nav_usd < peak) dd.appendChild(sv('rect', { x: X(pts[i].ts), y: Y(peak), width: Math.max(1, nx2 - X(pts[i].ts)), height: Math.max(1, Y(pts[i].nav_usd) - Y(peak)), fill: '#d94f45', opacity: .18 })); }
    svg.appendChild(dd);
    svg.appendChild(sv('path', { d, fill: 'none', stroke: '#f3c76a', 'stroke-width': 2, 'stroke-linejoin': 'round' }));
    // hover
    const cursor = sv('line', { x1: 0, x2: 0, y1: T, y2: H - B, stroke: '#a8a2b5', 'stroke-width': 1, opacity: 0 }); svg.appendChild(cursor);
    const dot = sv('circle', { r: 4, fill: '#f3c76a', stroke: '#0f0e14', 'stroke-width': 2, opacity: 0 }); svg.appendChild(dot);
    const tip = $('#tip'), box = $('#chart');
    box.onmousemove = (ev) => { const rect = svg.getBoundingClientRect(); const mx = (ev.clientX - rect.left) / rect.width * W; const t = t0 + (mx - L) / (W - L - R) * (t1 - t0);
      let p = pts[0]; for (const q of pts) if (q.ts <= t) p = q; const x = Math.max(L, Math.min(W - R, mx));
      cursor.setAttribute('x1', x); cursor.setAttribute('x2', x); cursor.setAttribute('opacity', 1); dot.setAttribute('cx', x); dot.setAttribute('cy', Y(p.nav_usd)); dot.setAttribute('opacity', 1);
      tip.hidden = false; tip.replaceChildren(el('div', '', fmtTs(t)), el('div', '', 'NAV ' + fmtUsd(p.nav_usd)), el('div', cls(p.cum_usd), 'Realized ' + fmtUsd(p.cum_usd, true)), el('div', 'neg', 'DD ' + fmtUsd(p.dd_usd) + ' (' + fmtPct(p.dd_pct) + ')'));
      const px = ev.clientX - rect.left; tip.style.left = (px + 14 + 190 > rect.width ? px - 200 : px + 14) + 'px'; tip.style.top = Math.max(0, ev.clientY - rect.top - 60) + 'px'; };
    box.onmouseleave = () => { tip.hidden = true; cursor.setAttribute('opacity', 0); dot.setAttribute('opacity', 0); };
  }
  $('#range').addEventListener('click', (ev) => { const b = ev.target.closest('button'); if (!b) return; range = +b.dataset.r; for (const x of $('#range').children) x.classList.toggle('on', x === b); renderChart(); });

  /* ---------- open positions ---------- */
  function renderOpen() {
    const tb = $('#opentbl tbody'); tb.replaceChildren(); $('#opencount').textContent = open.length;
    if (!open.length) { const tr = el('tr'); const td = el('td', 'empty', 'No open positions'); td.colSpan = 12; tr.appendChild(td); tb.appendChild(tr); return; }
    const now = Date.now();
    for (const r of open) {
      const tr = el('tr'); tr.dataset.id = r.id;
      tr.append(el('td', 'sym', r.symbol), td(sideEl(r.side)), el('td', '', fmtTs(r.opened_ms)), el('td', 'num', fmtPx(r.entry_px)),
        el('td', 'num mark', fmtPx(r.mark_px)), el('td', 'num', fmtQty(r.qty)), el('td', 'num', fmtUsd(r.notional_usd)), el('td', 'num', fmtPx(r.target_px)), el('td', 'num', fmtPx(r.stop_px)),
        el('td', 'num upnl ' + cls(r.unrealized_usd), fmtUsd(r.unrealized_usd, true)), el('td', 'num upct ' + cls(r.unrealized_pct), fmtPct(r.unrealized_pct)), el('td', '', fmtDur((now - r.opened_ms) / 6e4)));
      tr.addEventListener('click', () => openDrawer(r, true)); tb.appendChild(tr);
    }
    function td(child) { const c = el('td'); c.appendChild(child); return c; }
  }
  // live marks: patch cells in place, recompute unrealized from server-authoritative entry/notional
  async function tick() {
    try {
      const r = await api('prices'); if (!r.ok) throw 0; const { prices } = await r.json(); let any = false, tot = 0, n = 0;
      for (const p of open) { const q = prices[p.symbol]; if (!q) continue; any = true; const sign = p.side === 'long' ? 1 : -1; const move = sign * (q.px / p.entry_px - 1);
        p.mark_px = q.px; p.unrealized_usd = Math.round(move * p.notional_usd * 100) / 100; p.unrealized_pct = move * 100; tot += p.unrealized_usd; n++;
        const tr = $(`#opentbl tr[data-id="${CSS.escape(p.id)}"]`); if (!tr) continue;
        $('.mark', tr).textContent = fmtPx(q.px); const a = $('.upnl', tr), b = $('.upct', tr); a.textContent = fmtUsd(p.unrealized_usd, true); a.className = 'num upnl ' + cls(p.unrealized_usd); b.textContent = fmtPct(p.unrealized_pct); b.className = 'num upct ' + cls(p.unrealized_pct); }
      $('#livedot').classList.toggle('on', any);
      if (summary && n) { summary.unrealized_usd = Math.round(tot * 100) / 100; summary.unrealized_marked = n; summary.total_usd = Math.round((summary.realized_usd + summary.unrealized_usd) * 100) / 100; summary.nav_usd = Math.round((summary.capital_usd + summary.total_usd) * 100) / 100; summary.return_pct = summary.total_usd / summary.capital_usd * 100; renderKpis(); }
    } catch (_) { $('#livedot').classList.remove('on'); }
  }

  /* ---------- closed trades ---------- */
  function filtered() {
    const q = $('#q').value.trim().toLowerCase(), sym = $('#fsym').value, side = $('#fside').value, res = $('#fres').value;
    const df = $('#dfrom').value ? Date.parse($('#dfrom').value + 'T00:00:00Z') : null, dt = $('#dto').value ? Date.parse($('#dto').value + 'T23:59:59Z') : null;
    let rows = trades.filter(r => (!sym || r.symbol === sym) && (!side || r.side === side) && (!res || (res === 'win' ? r.pnl_usd > 0 : r.pnl_usd <= 0))
      && (df == null || r.closed_ms >= df) && (dt == null || r.closed_ms <= dt)
      && (!q || r.symbol.toLowerCase().includes(q) || ('model ' + r.model).includes(q) || r.exit_reason.toLowerCase().includes(q) || r.side.includes(q)));
    rows.sort((a, b) => { const x = a[sortK], y = b[sortK]; if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1; return (x > y ? 1 : x < y ? -1 : 0) * sortD; });
    return rows;
  }
  function renderClosed() {
    const rows = filtered(), tb = $('#closedtbl tbody'); tb.replaceChildren();
    const pages = Math.max(1, Math.ceil(rows.length / PAGE)); page = Math.min(page, pages - 1);
    $('#closedcount').textContent = trades.length; $('#pinfo').textContent = rows.length ? `${page * PAGE + 1}–${Math.min(rows.length, (page + 1) * PAGE)} of ${rows.length}` : '0 trades'; $('#prev').disabled = page === 0; $('#next').disabled = page >= pages - 1;
    if (!rows.length) { const tr = el('tr'); const td = el('td', 'empty', 'No trades match'); td.colSpan = 15; tr.appendChild(td); tb.appendChild(tr); return; }
    for (const r of rows.slice(page * PAGE, (page + 1) * PAGE)) {
      const tr = el('tr'); const sd = el('td'); sd.appendChild(sideEl(r.side)); const ex = el('td'); ex.appendChild(el('span', 'badge ' + r.exit_reason, r.exit_reason));
      tr.append(el('td', 'sym', r.symbol), sd, el('td', '', fmtTs(r.opened_ms)), el('td', '', fmtTs(r.closed_ms)), el('td', 'num', fmtPx(r.entry_px)), el('td', 'num', fmtPx(r.exit_px)), el('td', 'num', fmtQty(r.qty)), el('td', 'num', fmtUsd(r.notional_usd)),
        el('td', 'num', fmtPx(r.target_px)), el('td', 'num', fmtPx(r.stop_px)), el('td', 'num ' + cls(r.pnl_usd), fmtUsd(r.pnl_usd, true)), el('td', 'num ' + cls(r.pnl_pct), fmtPct(r.pnl_pct)), ex, el('td', '', fmtDur(r.hold_min)), el('td', 'muted', r.model ? 'Model ' + r.model : '—'));
      tr.addEventListener('click', () => openDrawer(r, false)); tb.appendChild(tr);
    }
  }
  for (const th of document.querySelectorAll('#closedtbl th')) th.addEventListener('click', () => { const k = th.dataset.k; if (sortK === k) sortD = -sortD; else { sortK = k; sortD = k === 'symbol' || k === 'side' || k === 'exit_reason' ? 1 : -1; } for (const x of th.parentNode.children) { x.classList.toggle('on', x === th); x.classList.toggle('desc', x === th && sortD < 0); } page = 0; renderClosed(); });
  for (const id of ['q', 'fsym', 'fside', 'fres', 'dfrom', 'dto']) $('#' + id).addEventListener('input', () => { page = 0; renderClosed(); });
  $('#clear').addEventListener('click', () => { for (const id of ['q', 'fsym', 'fside', 'fres', 'dfrom', 'dto']) $('#' + id).value = ''; page = 0; renderClosed(); });
  $('#prev').addEventListener('click', () => { page--; renderClosed(); }); $('#next').addEventListener('click', () => { page++; renderClosed(); });

  /* ---------- drawer ---------- */
  function openDrawer(r, isOpen) {
    const b = $('#dbody'); b.replaceChildren();
    const head = el('div'); head.append(sideEl(r.side), el('span', 'muted', '  ' + (isOpen ? 'Open position' : 'Closed trade') + ' · Paper'));
    const pnl = isOpen ? r.unrealized_usd : r.pnl_usd, pct = isOpen ? r.unrealized_pct : r.pnl_pct;
    b.append(head, el('div', 'big ' + cls(pnl), fmtUsd(pnl, true) + (pct == null ? '' : '  (' + fmtPct(pct) + ')')));
    const dl = el('dl', 'kv'); const row = (k, v) => { dl.append(el('dt', '', k), el('dd', '', v)); }; const sep = () => dl.appendChild(el('div', 'sep'));
    row('Symbol', r.symbol); row('Direction', r.side.toUpperCase()); if (!isOpen && r.model) row('Model', 'Model ' + r.model); sep();
    row('Entry time', fmtTs(r.opened_ms)); if (!isOpen) row('Exit time', fmtTs(r.closed_ms)); row('Duration', fmtDur(isOpen ? (Date.now() - r.opened_ms) / 6e4 : r.hold_min)); sep();
    row('Entry price', fmtPx(r.entry_px)); row(isOpen ? 'Mark price' : 'Exit price', fmtPx(isOpen ? r.mark_px : r.exit_px)); if (!isOpen && r.move_pct != null) row('Price move (in trade direction)', fmtPct(r.move_pct));
    row('Target', fmtPx(r.target_px) + (r.target_pct != null ? '  (' + fmtPct(r.target_pct) + ')' : '')); row('Stop', fmtPx(r.stop_px) + (r.stop_pct != null ? '  (' + fmtPct(-r.stop_pct) + ')' : '')); sep();
    row('Quantity (derived)', fmtQty(r.qty)); row('Notional', fmtUsd(r.notional_usd)); if (!isOpen) row('Exit reason', r.exit_reason); if (!isOpen) row('Realized P&L (net of modelled fees)', fmtUsd(r.pnl_usd, true)); if (isOpen) row('Unrealized P&L', fmtUsd(r.unrealized_usd, true));
    b.appendChild(dl); b.appendChild(el('p', 'fine', 'Simulated trade. No order was placed and no money was at risk. Fees and slippage per trade are not recorded.'));
    $('#dtitle').textContent = r.symbol + ' · ' + r.side.toUpperCase(); $('#drawer').hidden = false; $('#dbg').hidden = false;
  }
  const closeDrawer = () => { $('#drawer').hidden = true; $('#dbg').hidden = true; };
  $('#dclose').addEventListener('click', closeDrawer); $('#dbg').addEventListener('click', closeDrawer); document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDrawer(); });

  /* ---------- exchange connection ---------- */
  const exd = $('#exdrawer');
  function renderEx(info) {
    const st = $('#exstatus'); st.replaceChildren();
    if (!info) { st.append(el('div', 'muted', 'No exchange connected yet.')); $('#exremove').hidden = true; return; }
    st.append(el('div', '', `Saved exchange key ending `), el('span', 'k', '…' + (info.key_tail || '????')));
    st.append(el('div', info.verified ? 'ok' : 'warn', info.verified ? 'Verified' : 'Unverified'));
    $('#exremove').hidden = false; 
  }
  async function loadEx() { try { const r = await api('exchange'); if (!r.ok) return; const j = await r.json(); $('#exnote').textContent = 'Legacy saved credentials. New connections are managed in your WHY account.'; renderEx(j.exchange); } catch (_) {} }
  const openEx = () => { exd.hidden = false; $('#dbg').hidden = false; loadEx(); };
  const closeEx = () => { exd.hidden = true; if ($('#drawer').hidden) $('#dbg').hidden = true; };
  $('#exbtn').addEventListener('click', openEx); $('#exclose').addEventListener('click', closeEx); $('#dbg').addEventListener('click', closeEx);
  $('#exremove').addEventListener('click', async () => { try { await api('exchange/remove', { method: 'POST' }); renderEx(null); } catch (_) {} });

  /* ---------- boot ---------- */
  $('#logout').addEventListener('click', async () => { try { await api('logout', { method: 'POST' }); } finally { location.href = '/partners/login'; } });
  async function load() {
    const [s, o, t, e, ses] = await Promise.all([api('summary'), api('open'), api('trades'), api('equity'), api('session')]);
    if (![s, o, t, e].every(r => r.ok)) { $('#kpis').replaceChildren(el('p', 'muted', 'Book temporarily unavailable. Retrying…')); return; }
    const S = await s.json(); summary = S.summary; notes = S.notes || {}; open = (await o.json()).open; trades = (await t.json()).trades; equity = (await e.json()).equity;
    if (ses.ok) { const u = await ses.json(); $('#who').textContent = u.user || ''; $('#logout').hidden = u.auth_required === false; }
    $('#since').textContent = 'since ' + fmtTs(Date.parse(S.since_iso)); $('#asof').textContent = fmtTs(S.ts * 1000);
    $('#clipnote').textContent = S.clip_usd ? `Per-position notional ${fmtUsd(S.clip_usd)}` : ''; $('#feenote').textContent = notes.fees || ''; if (notes.drawdown) $('#eqnote').textContent = notes.drawdown;
    const syms = [...new Set(trades.map(r => r.symbol))].sort(); const sel = $('#fsym'); const cur = sel.value; sel.replaceChildren(el('option', '', 'All symbols')); sel.firstChild.value = ''; for (const s2 of syms) { const op = el('option', '', s2); op.value = s2; sel.appendChild(op); } sel.value = cur;
    renderKpis(); renderChart(); renderOpen(); renderClosed();
  }
  load().catch(() => {}); tick();
  setInterval(tick, 5000); setInterval(() => load().catch(() => {}), 60000);
  window.addEventListener('resize', () => summary && renderChart());
})();
