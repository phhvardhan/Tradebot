const $ = id => document.getElementById(id);
const fmt = (n, d=2) => new Intl.NumberFormat('en-US',{minimumFractionDigits:d,maximumFractionDigits:d}).format(Number(n));
const money = n => '$' + fmt(n);
const signed = n => (Number(n) > 0 ? '+' : Number(n) < 0 ? '−' : '') + '$' + fmt(Math.abs(Number(n)));
const tone = (el, n) => { el.classList.toggle('up', Number(n) > 0); el.classList.toggle('down', Number(n) < 0); };
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const NS = 'http://www.w3.org/2000/svg';
let token = '', pending = false, actionError = '';

function svgAdd(svg, name, attrs, text) {
  const e = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text) e.textContent = text;
  svg.append(e);
}

function ma(xs, n) { return xs.map((_, i) => i < n - 1 ? null : xs.slice(i - n + 1, i + 1).reduce((a, b) => a + b) / n); }

function chart(rows) {
  const svg = $('chart'); svg.replaceChildren();
  const W = 720, H = 260, R = 92, T = 12, B = 22, w = W - R;
  if (!rows.length) { svgAdd(svg, 'text', {x: 0, y: H / 2}, 'No market data yet. Run one check to load candles.'); return; }
  const closes = rows.map(r => r[1]), f = ma(closes, 5), s = ma(closes, 20);
  const all = closes.concat(f, s).filter(v => v != null), lo = Math.min(...all), hi = Math.max(...all), span = hi - lo || 1;
  const x = i => i / (closes.length - 1) * w, y = v => T + (1 - (v - lo) / span) * (H - T - B);
  for (const v of [hi, lo + span / 2, lo]) {
    svgAdd(svg, 'line', {x1: 0, x2: w, y1: y(v), y2: y(v), stroke: css('--rule')});
    svgAdd(svg, 'text', {x: w + 8, y: y(v) + 4}, fmt(v, 0));
  }
  const line = (vals, color, width) => svgAdd(svg, 'polyline', {fill: 'none', stroke: color, 'stroke-width': width, 'stroke-linejoin': 'round',
    points: vals.map((v, i) => v == null ? '' : `${x(i).toFixed(1)},${y(v).toFixed(1)}`).filter(Boolean).join(' ')});
  line(s, css('--ma20'), 1.5); line(f, css('--ma5'), 1.5); line(closes, css('--fg'), 1.75);
  const last = closes.at(-1);
  svgAdd(svg, 'circle', {cx: x(closes.length - 1), cy: y(last), r: 3, fill: css('--fg')});
  const t0 = new Date(rows[0][0] * 1000), t1 = new Date(rows.at(-1)[0] * 1000), hm = d => d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
  svgAdd(svg, 'text', {x: 0, y: H - 4}, hm(t0)); svgAdd(svg, 'text', {x: w, y: H - 4, 'text-anchor': 'end'}, hm(t1));
}

function spark(history, capital) {
  const svg = $('spark'); svg.replaceChildren();
  if (history.length < 2) return;
  const v = history.map(h => h[1]), lo = Math.min(...v, capital), hi = Math.max(...v, capital), span = hi - lo || 1;
  const x = i => i / (v.length - 1) * 300, y = n => 40 - (n - lo) / span * 38;
  svgAdd(svg, 'line', {x1: 0, x2: 300, y1: y(capital), y2: y(capital), stroke: css('--rule'), 'stroke-dasharray': '3 3'});
  svgAdd(svg, 'polyline', {fill: 'none', stroke: css(v.at(-1) >= capital ? '--up' : '--down'), 'stroke-width': 1.5, 'vector-effect': 'non-scaling-stroke',
    points: v.map((n, i) => `${x(i).toFixed(1)},${y(n).toFixed(1)}`).join(' ')});
}

function ago(t) {
  const s = Math.round(Date.now() / 1000 - t);
  return s < 90 ? `${Math.max(s, 0)}s ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : new Date(t * 1000).toLocaleString();
}

async function refresh() {
  try {
    const res = await fetch('/api/crypto/state'); if (!res.ok) throw Error('cannot read server state');
    const s = await res.json(); token = s.token;
    await refreshAccount();
    const demo = s.source === 'demo', capital = Number(s.capital), ret = (Number(s.equity) / capital - 1) * 100;
    $('source').textContent = demo ? 'DEMO DATA' : 'COINBASE · PAPER';
    $('mode').textContent = demo
      ? 'Synthetic prices. Each check advances one simulated 5-minute candle.'
      : 'Live public Coinbase BTC-USD prices, simulated fills. Checks every 60 seconds while running.';
    $('equity').textContent = money(s.equity);
    $('return').textContent = `${ret >= 0 ? '+' : '−'}${Math.abs(ret).toFixed(2)}% from ${money(capital)}`; tone($('return'), ret);
    $('cash').textContent = money(s.cash);
    $('realized').textContent = signed(s.realized); tone($('realized'), s.realized);
    $('fees').textContent = `fees paid ${money(s.fees)}`;
    const qty = Number(s.qty);
    $('position').textContent = qty ? money(qty * Number(s.mark)) : 'Flat';
    $('qty').textContent = qty ? `${qty.toFixed(8)} BTC @ ${money(s.entry)}` : 'no open position';
    $('price').textContent = Number(s.mark) ? money(s.mark) : '—';
    $('averages').textContent = s.fast ? `MA 5 ${money(s.fast)} · MA 20 ${money(s.slow)} · ${s.fast > s.slow ? 'trend up' : 'trend down'}` : '';
    $('decision').textContent = s.decision;
    $('connection').textContent = s.updated ? `${demo ? 'Simulated candle' : 'Market data'} ${demo ? new Date(s.updated * 1000).toLocaleString() : ago(s.updated)}` : 'No market update yet.';
    const st = $('status'); st.textContent = s.error ? 'Blocked' : s.busy ? 'Checking' : s.running ? `Running · ${s.status}` : 'Paused';
    st.className = s.error ? 'err' : s.running ? 'live' : '';
    $('fee').textContent = `${(Number(s.fee_rate) * 100).toFixed(2)}%`; $('slip').textContent = `${(Number(s.slippage) * 100).toFixed(2)}%`;
    const msg = actionError || s.error; $('error').hidden = !msg; $('error').textContent = msg || '';
    $('start').disabled = pending || s.running || s.busy; $('pause').disabled = pending || !s.running; $('step').disabled = pending || s.running || s.busy;
    chart(s.candles); spark(s.history, capital);
    $('count').textContent = s.trades.length ? `${s.trades.length}${s.more ? '+' : ''} trades` : '';
    $('empty').hidden = s.trades.length > 0;
    $('trades').replaceChildren(...s.trades.map(t => {
      const tr = document.createElement('tr'), sell = t.side === 'SELL';
      const cells = [[new Date(t.time).toLocaleString([], {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit'})],
        [t.side, 'side ' + (sell ? 'down' : 'up')], [Number(t.qty).toFixed(8)], [fmt(t.price)], [fmt(t.fee)],
        [sell ? signed(t.pnl) : '', sell ? (Number(t.pnl) >= 0 ? 'up' : 'down') : ''], [t.reason, 'reason']];
      for (const [text, cls] of cells) { const td = document.createElement('td'); td.textContent = text; if (cls) td.className = cls; tr.append(td); }
      return tr;
    }));
  } catch (e) {
    $('error').hidden = false; $('error').textContent = `Dashboard disconnected (${e.message}). Values shown may be stale.`;
    $('status').textContent = 'Disconnected'; $('status').className = 'err';
    ['start', 'pause', 'step', 'account-refresh'].forEach(id => $(id).disabled = true);
  }
}

async function refreshAccount() {
  try {
    const res = await fetch('/api/account', {headers: {'X-App-Token': token}}); if (!res.ok) throw Error('unavailable');
    const a = await res.json();
    $('account-refresh').disabled = !a.enabled || a.busy;
    $('account-status').textContent = !a.enabled ? 'Disabled. Launch with --account-readonly to enter a read-only key privately in Terminal.'
      : a.busy ? 'Reading account balances…' : !a.updated ? 'Ready. Click Refresh balances to read your account.'
      : `${a.stale ? 'STALE snapshot' : 'Snapshot'} · fetched ${new Date(a.updated * 1000).toLocaleString()}.` + (a.balances.length || a.error ? '' : ' No non-zero balances reported.');
    $('account-error').textContent = a.error || '';
    $('balances').replaceChildren(...a.balances.map(b => {
      const tr = document.createElement('tr');
      for (const v of [b.asset, b.free, b.locked, b.reserved_fee]) { const td = document.createElement('td'); td.textContent = v; tr.append(td); }
      return tr;
    }));
  } catch (e) { $('account-status').textContent = 'Account viewer disconnected. Displayed balances may be stale.'; $('account-refresh').disabled = true; }
}

for (const id of ['start', 'pause', 'step']) $(id).addEventListener('click', async () => {
  pending = true; $(id).disabled = true;
  try { const res = await fetch('/api/crypto/' + id, {method: 'POST', headers: {'X-App-Token': token}}); if (!res.ok) throw Error(await res.text()); actionError = ''; }
  catch (e) { actionError = `${$(id).textContent} failed: ${e.message}`; }
  finally { pending = false; await refresh(); }
});
$('account-refresh').addEventListener('click', async () => {
  $('account-refresh').disabled = true;
  try { const r = await fetch('/api/account/refresh', {method: 'POST', headers: {'X-App-Token': token}}); if (!r.ok) throw Error('Refresh request refused'); }
  catch (e) { $('account-error').textContent = e.message; }
  await refreshAccount();
});
(async function poll() { await refresh(); setTimeout(poll, 2000); })();
