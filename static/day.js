const {$, el, n2, usd, signed, pct, tone, icon, when} = UI;
UI.shell('day');
let last = null, token = '', pending = false;
const NAMES = {AAPL: 'Apple', MSFT: 'Microsoft', NVDA: 'NVIDIA', AMZN: 'Amazon', GOOGL: 'Alphabet', META: 'Meta Platforms', TSLA: 'Tesla', AMD: 'Advanced Micro Devices', AVGO: 'Broadcom', NFLX: 'Netflix',
  JPM: 'JPMorgan Chase', V: 'Visa', MA: 'Mastercard', UNH: 'UnitedHealth', LLY: 'Eli Lilly', COST: 'Costco', WMT: 'Walmart', KO: 'Coca-Cola', PEP: 'PepsiCo', XOM: 'Exxon Mobil'};
const LOGO_BG = {V: '#1a1f71', UNH: '#002677', AMZN: '#232f3e'};
function logo(sym) {
  const box = el('span', 'logo'), img = new Image(); if (LOGO_BG[sym]) box.style.background = LOGO_BG[sym];
  img.alt = ''; img.src = `/logos/${sym}.png`; img.onerror = () => { box.classList.add('text'); box.replaceChildren(sym.slice(0, 2)); }; box.append(img); return box;
}
const STATES = [  // [pattern, label, tone]
  [/^Buy/, 'Bought', 'up'], [/^Holding/, 'Held', 'up'], [/^(Stop|Break-even|Target|Flat|Left)/, 'Sold', ''], [/^Done for today/, 'Done', ''],
  [/^Signal, but price .* back under/, 'Faded', 'down'], [/^Signal, but no cash/, 'No cash', 'down'], [/missed/, 'Missed', 'warn'], [/^Signal/, 'Blocked', 'down'],
  [/^No breakout/, 'Watching', ''], [/^Opening range/, 'Range forming', 'warn'], [/^Market closed/, 'Closed', ''], [/^(No quote|Quote is stale)/, 'No data', 'down']];
const state = d => STATES.find(([re]) => re.test(d || '')) || [null, '—', ''];
const mins = hm => Number(hm.slice(0, 2)) * 60 + Number(hm.slice(3, 5));

// The trading day as one bar: opening range, entry window, the hold, then flat before the close, with a marker at the current New York time.
function session(s) {
  const m = s.market || {}, open = 570, close = mins(m.closes || '16:00'), rangeEnd = open + Number(s.or_minutes), until = mins(s.entries_until), flat = mins(s.flat_by);
  const box = $('session'), X = t => Math.min(Math.max((t - open) / (close - open), 0), 1) * 100;
  const seg = (cls, a, b) => { const i = el('i', cls); i.style.left = X(a) + '%'; i.style.width = (X(b) - X(a)) + '%'; return i; };
  const tick = (t, text, cls = '') => { const k = el('span', 'tk ' + cls, text); k.style.left = X(t) + '%'; return k; };
  box.replaceChildren(el('div', 'bar', seg('range', open, rangeEnd), seg('entry', rangeEnd, until), seg('hold', until, flat), seg('flat', flat, close)),
    tick(open, '9:30 open'), tick(rangeEnd, `${String(Math.floor(rangeEnd / 60)).padStart(2, '0')}:${String(rangeEnd % 60).padStart(2, '0')} range set`, 'alt'), tick(until, s.entries_until + ' last entry'), tick(flat, s.flat_by + ' flat', 'alt end'), tick(close, (m.closes || '16:00') + ' close', 'end'));
  if (m.now_new_york) {
    const t = mins(m.now_new_york.slice(11)), now = el('i', 'now'); now.style.left = X(t) + '%'; now.dataset.t = t < open ? 'pre-market' : t >= close ? 'closed' : m.now_new_york.slice(11); box.append(now);
  }
}
// Where the price sits between the stop (left) and the target (right); the tick marks the entry.
function rtrack(p) {
  const stop = Number(p.stop0), entry = Number(p.entry), target = Number(p.target), X = v => Math.min(Math.max((v - stop) / (target - stop), 0), 1);
  const e = X(entry), k = X(Number(p.last)), fill = el('i', 'fill ' + (k >= e ? '' : 'down')), mark = el('i', 'mark'), tick = el('i', 'tick'), tm = el('span', 'tm', 'Entry ' + n2(entry));
  fill.style.left = Math.min(e, k) * 100 + '%'; fill.style.width = Math.abs(k - e) * 100 + '%'; mark.style.left = k * 100 + '%'; tick.style.left = e * 100 + '%'; tm.style.left = e * 100 + '%';
  return el('span', 'rtrack', fill, tick, mark, el('span', 'tl', `Stop ${n2(p.stop)}${p.be ? ' (break-even)' : ''}`), tm, el('span', 'tr', 'Target ' + n2(target)));
}
function rangeBar(rg, price) {
  const bar = el('span', 'rbar'); if (rg.high == null || !price) return bar;
  const span = rg.high - rg.low || 1, k = Math.min(Math.max((Number(price) - rg.low) / span, -.15), 1.15), dot = el('i'); dot.style.left = ((k + .15) / 1.3 * 100) + '%';
  const body = el('b'); body.style.left = (.15 / 1.3 * 100) + '%'; body.style.width = (1 / 1.3 * 100) + '%';
  bar.title = `Range ${n2(rg.low)}–${n2(rg.high)} over ${rg.bars} bars; price ${n2(price)}`; bar.append(body, dot); return bar;
}

function render(s) {
  last = s; token = s.token; s.trades = s.trades || [];
  UI.controls('day', s, pending); $('edit-rules').disabled = false;
  const m = s.market || {}; UI.clock(m);
  const eq = Number(s.equity), cap = Number(s.capital), today = eq - Number(s.day_start), pos = Object.entries(s.positions), tradesToday = Object.values(s.traded).filter(d => d === s.day).length;
  const held = pos.length, age = s.updated ? Date.now() / 1000 - s.updated : 0;
  const warn = !s.running && held ? `Agent is paused with ${held} open day trade${held > 1 ? 's' : ''}: the stop, target and flat-by sale are NOT being checked. Press Start.`
    : s.running && !s.busy && age > 300 && m.open ? `No check for ${Math.round(age / 60)} minutes while the market is open.` : '';
  const msg = s.error || warn; $('error').hidden = !msg; $('error').textContent = msg || '';
  const al = (s.alerts || []).slice(-2).reverse(); $('alerts').hidden = !al.length; $('alerts').textContent = al.map(a => `${a.text} (${when(a.at)})`).join('  ·  ');
  $('equity').textContent = usd(eq); $('return').textContent = `${signed(eq - cap)} (${pct(eq / cap - 1, 2)}) since start · from ${usd(cap)}`; tone($('return'), eq - cap);
  $('clockline').textContent = m.now_new_york ? `${m.open ? 'Open' : 'Closed'} · ${m.reason}${!m.open && m.next_open ? ` · next ${m.next_open}` : ''}` : 'No market check yet';
  $('updated').textContent = s.updated ? `Last check ${new Date(s.updated * 1000).toLocaleTimeString()}` : '';
  session(s);
  $('today').textContent = signed(today); tone($('today'), today); $('limit').textContent = Number(s.daily_loss) ? `limit −${usd(s.daily_loss)}` : 'no daily limit';
  $('active').textContent = held; $('trades-today').textContent = `${tradesToday} of ${Number(s.max_trades) || '∞'} trades today`;
  $('cash').textContent = usd(s.cash); $('per-trade').textContent = `≤ ${usd(s.per_trade)} a trade · risk ${usd(s.risk)}`;
  const sells = s.trades.filter(t => t.side === 'SELL'), wins = sells.filter(t => Number(t.pnl) > 0).length;
  $('realized').textContent = signed(s.realized); tone($('realized'), s.realized); $('record').textContent = sells.length ? `${wins} of ${sells.length} closed trades won` : 'no closed trades yet';
  $('decision').textContent = (!s.running ? 'Paused: no checks, no exits, no flat-by sale until you press Start. ' : '') + (s.decision || '');
  $('connection').textContent = m.now_new_york ? `${m.now_new_york} New York · ${m.open ? 'regular session' : m.reason}` : '';
  const pr = $('pos-rows'); pr.replaceChildren(); $('pos-empty').hidden = held > 0;
  for (const [sym, p] of pos) {
    const entry = Number(p.entry), risk = entry - Number(p.stop0), r = risk ? (Number(p.last) - entry) / risk : 0, pnl = (Number(p.last) - entry) * Number(p.qty);
    pr.append(el('div', 'row pos', logo(sym),
      el('span', 'r-name', el('strong', '', `${sym} · ${NAMES[sym] || ''}`), el('span', '', `${n2(p.qty, 4)} shares at ${n2(entry)} · ${s.decisions[sym] || ''}`)),
      el('span', 'r-num', el('strong', 'num', n2(p.last)), tone(el('span', 'num', `${signed(pnl)} · ${r >= 0 ? '+' : '−'}${Math.abs(r).toFixed(2)}R`), pnl)),
      el('span', 'badge ' + (r >= 0 ? 'up' : 'down'), p.be ? 'Break-even on' : 'Open'), rtrack(p)));
  }
  const wr = $('watch-rows'); wr.replaceChildren();
  for (const sym of s.symbols) {
    const rg = (s.ranges || {})[sym] || {}, d = s.decisions[sym] || '', [, label, t] = state(d), price = s.quotes && s.quotes[sym];
    wr.append(el('div', 'row watch-row', logo(sym),
      el('span', 'r-name', el('strong', '', `${sym} · ${NAMES[sym] || ''}`), el('span', '', d || 'No check yet.')),
      rangeBar(rg, price),
      el('span', 'r-num', el('strong', 'num', price ? n2(price) : '—'), el('span', 'dim', rg.high != null ? `${n2(rg.low)}–${n2(rg.high)}${rg.hit ? ' · broke at ' + rg.hit : ''}` : 'no range yet')),
      el('span', 'badge ' + t, label)));
  }
  const r = s, list = $('rules-list'); list.replaceChildren();
  for (const [k, v] of [['Opening range', `first ${r.or_minutes} min`], ['Enter when', `5-min close > range high, before ${r.entries_until}`], ['Stop', 'middle of the range'], ['Target', `${r.reward_risk}× the risk`],
      ['Break-even', Number(r.breakeven_r) ? `stop to entry at +${r.breakeven_r}R` : 'Off'], ['Max loss per trade', usd(r.risk)], ['Per position', Number(r.per_trade) ? `≤ ${usd(r.per_trade)}` : 'No cap'],
      ['Trades per day', Number(r.max_trades) || 'No limit'], ['Daily loss limit', Number(r.daily_loss) ? usd(r.daily_loss) : 'Off'], ['Flat by', r.flat_by], ['Slippage', '0.05% each way']])
    list.append(el('dt', '', k), el('dd', '', String(v)));
  const rl = $('rl-list'); rl.replaceChildren(); $('rl-empty').hidden = (s.rule_log || []).length > 0;
  for (const x of (s.rule_log || []).slice(-8).reverse()) rl.append(el('li', '', `${when(x.at)} · ${x.text}`));
  const rows = $('rows'); rows.replaceChildren(); $('empty').hidden = s.trades.length > 0; $('count').textContent = s.trades.length ? `${s.trades.length}${s.more ? '+' : ''} trades` : '';
  for (const t of s.trades) {
    const tr = rows.insertRow(); tr.insertCell().textContent = when(Date.parse(t.time) / 1000); tr.insertCell().append(el('span', 'sym', logo(t.symbol), t.symbol)); tr.cells[1].className = 'side';
    const sd = tr.insertCell(); sd.textContent = t.side; sd.className = 'side ' + (t.side === 'SELL' ? 'down' : 'up'); tr.insertCell().textContent = n2(t.qty, 4); tr.insertCell().textContent = n2(t.price);
    const pl = tr.insertCell(); pl.textContent = t.side === 'SELL' ? signed(t.pnl) : ''; tone(pl, t.side === 'SELL' ? Number(t.pnl) : 0);
    const rs = tr.insertCell(); rs.textContent = t.reason; rs.className = 'reason';
  }
}

async function poll() { try { render(await (await fetch('/api/day/state')).json()); } catch (e) { $('error').hidden = false; $('error').textContent = 'Cannot reach the server.'; } }
const post = (path, body) => fetch('/api/day/' + path, {method: 'POST', headers: {'X-App-Token': token, 'Content-Type': 'application/json'}, body: JSON.stringify(body || {})});
for (const k of ['start', 'pause', 'step']) $(k).onclick = async () => { pending = true; $(k).disabled = true; await post(k); pending = false; poll(); };

const KEYS = ['or_minutes', 'entries_until', 'flat_by', 'risk', 'per_trade', 'reward_risk', 'breakeven_r', 'max_trades', 'daily_loss'];
$('edit-rules').onclick = () => { $('f-symbols').value = last.symbols.join(' '); for (const k of KEYS) $('f-' + k).value = last[k]; $('rules-error').hidden = true; $('rules').showModal(); };
$('rules-cancel').onclick = () => $('rules').close();
$('rules-form').addEventListener('submit', async e => {
  e.preventDefault();
  const body = {symbols: $('f-symbols').value.split(/[\s,]+/).filter(Boolean)};
  for (const k of KEYS) body[k] = $('f-' + k).value || '0';
  const res = await post('rules', body);
  if (res.ok) { $('rules').close(); poll(); } else { $('rules-error').hidden = false; $('rules-error').textContent = await res.text(); }
});
poll(); setInterval(poll, 5000);
