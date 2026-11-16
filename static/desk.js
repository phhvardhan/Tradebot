// Action needed: the real account (read-only import) against the swing rules you chose, as cards you act on yourself.
const {$, el, usd, signed, pct, tone, when} = UI;
UI.shell('desk');
const px = n => '$' + Number(n).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
const ago = t => { const m = Math.round((Date.now() / 1000 - t) / 60); return m < 1 ? 'just now' : m < 60 ? `${m} min ago` : m < 2880 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} days ago`; };
let token = '', armed = null, armedAt = 0, busy = false, flash = '', dirty = false;
async function post(path, body) {
  const res = await fetch('/api/desk/' + path, {method: 'POST', headers: {'X-App-Token': token, 'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  if (!res.ok) throw Error(await res.text());
}
// Place order is two clicks: the first arms the button and says exactly what will be sent, the second sends it. No browser dialogs.
async function place(c) {
  const key = c.action + c.symbol;
  if (armed !== key || Date.now() - armedAt > 8000) { armed = key; armedAt = Date.now(); return refresh(); }
  armed = null; busy = true; flash = `Sending ${c.action.toLowerCase()} ${c.symbol}…`; refresh();
  try { await post('order', {symbol: c.symbol, side: c.action.toLowerCase()}); flash = `${c.action === 'BUY' ? 'Buy' : 'Sell'} ${c.symbol} sent to Alpaca paper.`; }
  catch (e) { flash = `Not sent: ${e.message}`; }
  busy = false; refresh();
}
const shares = q => Number.isInteger(Number(q)) ? String(Number(q)) : Number(q).toFixed(4);
const quoteLine = c => `Quote ${px(c.price)}${c.quote_at ? ` at ${when(c.quote_at)}` : ''} · Yahoo, may lag`;

function card(c) {
  const sell = c.action === 'SELL', head = el('div', 'card-head', el('h3', '', `${sell ? 'Sell' : 'Buy'} ${c.symbol}`),
    el('span', 'badge ' + (c.passes ? 'up' : 'down'), c.passes ? 'Passes your limits' : 'Fails a limit'));
  const rows = sell
    ? [['Shares', shares(c.qty)], ['Proceeds at this quote', usd(c.proceeds)], ['Result vs entry', signed(c.est_pnl)]]
    : [['Proposed size', `${shares(c.qty)} shares ≈ ${usd(c.cost)}`], ['Planned stop', px(c.stop)], ['Planned target', px(c.target)], ['Estimated loss at the stop', '−' + usd(c.est_loss)]];
  const dl = el('dl', '', ...rows.flatMap(([k, v]) => [el('dt', '', k), tone(el('dd', 'num', v), k.startsWith('Result') ? Number(c.est_pnl) : 0)]));
  let act = null;
  if (c.can_place) {
    const key = c.action + c.symbol, ready = armed === key && Date.now() - armedAt <= 8000;
    const what = sell ? `sell ${Number(c.qty)} ${c.symbol} at market` : `buy ${Number(c.qty)} ${c.symbol} at market, stop ${px(c.stop)}, target ${px(c.target)}`;
    const b = el('button', 'primary', ready ? `Confirm: ${what}` : 'Place order'); b.type = 'button'; b.disabled = busy; b.onclick = () => place(c);
    act = el('div', 'card-actions', b, ready ? el('span', 'dim sub', 'Click again to send it to Alpaca paper') : null);
  }
  return el('section', 'card desk-card', head, el('p', 'trigger', c.trigger), el('p', 'dim sub', quoteLine(c)), dl, el('p', 'est', c.est_note),
    el('ul', 'checks', ...c.checks.map(k => el('li', k.ok ? '' : 'fail', k.text))), act);
}

async function refresh() {
  try {
    const res = await fetch('/api/desk/state'); if (!res.ok) throw Error('cannot read server state');
    const s = await res.json(), h = s.holdings, r = s.rules; token = s.token;
    agent(s.agent);
    $('status').textContent = s.updated ? `Checked ${new Date(s.updated * 1000).toLocaleTimeString()}` : 'No check yet'; $('status').className = s.updated ? 'live' : '';
    $('error').hidden = true;
    $('notes').replaceChildren(...s.notes.map(n => el('div', 'banner', n.text)));
    const watch = s.watching, inv = watch.filter(w => w.purpose !== 'swing'), sw = watch.filter(w => w.purpose === 'swing');
    const sum = xs => xs.reduce((a, w) => a + Number(w.value), 0), cash = h && h.cash != null ? Number(h.cash) : null;
    $('account').textContent = h ? ` · ${h.account}` : ' · none imported';
    $('value').textContent = h ? usd((cash || 0) + sum(watch)) : '—';
    const partial = s.unpriced.length ? ` · value leaves out ${s.unpriced.join(', ')} (no price yet)` : '';
    $('synced').textContent = h ? `Synced ${ago(h.synced_at)} (${new Date(h.synced_at * 1000).toLocaleString()}) from ${h.source}${partial}` : 'Import a brokerage CSV to begin.';
    $('cash').textContent = cash == null ? '—' : usd(cash); $('cash-sub').textContent = cash == null ? 'not in the import' : h.source.startsWith('alpaca') ? 'from Alpaca' : 'from the import';
    $('invest').textContent = usd(sum(inv)); $('invest-sub').textContent = `${inv.length} holding${inv.length === 1 ? '' : 's'}`;
    $('swing').textContent = usd(sum(sw)); $('swing-sub').textContent = `${sw.length} open`;
    $('todo').textContent = String(s.cards.length); $('todo-sub').textContent = `${s.cards.filter(c => c.passes).length} pass your limits`;
    $('rules').textContent = `Buy a 20-day closing-high breakout, ${Number(r.per_trade) ? 'up to ' + usd(r.per_trade) : 'all cash'} a position, ${Number(r.max_new) ? r.max_new + ' new a day, ' : ''}stop −${(Number(r.stop) * 100).toFixed(0)}%, target +${(Number(r.target) * 100).toFixed(0)}%${Number(r.max_open) ? `, at most ${r.max_open} open` : ''}. Edit them on the US stocks page.`;
    $('checked').textContent = s.market && s.market.date ? `NYSE ${s.market.open ? 'open' : 'closed'} · ${s.market.reason || ''}` : '';
    $('cards').replaceChildren(...s.cards.map(card)); $('cards-empty').hidden = s.cards.length > 0;
    $('watch-rows').replaceChildren(...watch.map(w => el('tr', '', el('td', '', el('span', 'sym', UI.avatar(w.symbol), w.symbol)), el('td', '', w.purpose === 'swing' ? 'Swing trade' : 'Investment'),
      el('td', 'num', shares(w.qty)), el('td', 'num', px(w.price)), el('td', 'num', usd(w.value)), tone(el('td', 'num', w.gain == null ? '—' : pct(Number(w.gain), 1)), w.gain),
      el('td', 'why', w.plan && w.plan.bracket ? `Stop ${px(w.plan.stop)} and target ${px(w.plan.target)} are orders at Alpaca` : w.plan ? `Stop ${px(w.plan.stop)} (${Math.abs(Number(w.to_stop) * 100).toFixed(1)}% ${Number(w.to_stop) >= 0 ? 'above' : 'under'} it) · target ${px(w.plan.target)}` : 'No swing rule: an investment, held to your plan.'))));
    $('watch-empty').hidden = watch.length > 0; $('watch-rows').closest('.wrap').hidden = !watch.length;
  } catch (e) {
    $('error').hidden = false; $('error').textContent = `Dashboard disconnected (${e.message}). Values shown may be stale.`;
    $('status').textContent = 'Disconnected'; $('status').className = 'err';
  }
}
function agent(a) {
  const box = $('agent'); box.hidden = !a || !a.connected; if (box.hidden) return;
  const st = a.settings;
  box.querySelectorAll('#agent-mode button').forEach(b => { b.setAttribute('aria-pressed', String(b.dataset.mode === st.mode)); b.disabled = !a.paper; });
  $('agent-mode-text').textContent = a.paper ? a.modes[st.mode] + (flash ? ' · ' + flash : '') : 'Orders are placed on a paper account only. ALPACA_LIVE=1 is set, so placing is off.';
  $('cards-sub').textContent = st.mode === 'auto' ? 'the agent places every card that passes, within your limits' : st.mode === 'ask' ? 'Place order on a card that passes sends it to Alpaca paper' : 'proposals from the last check · you place the order';
  if (!dirty) for (const k of ['per_trade', 'max_new', 'daily_loss']) $('a-' + k).value = st[k];
  $('order-rows').replaceChildren(...a.orders.map(o => el('tr', '', el('td', '', when(o.at)), el('td', '', `${o.side === 'buy' ? 'Buy' : 'Sell'} ${Number(o.qty)} ${o.symbol}` + (o.plan ? ` · stop ${px(o.plan.stop)} · target ${px(o.plan.target)}` : '')),
    el('td', '', o.mode === 'auto' ? 'Automatic' : o.mode === 'day' ? 'Day trades' : 'You'), tone(el('td', 'why', o.ok ? `Accepted (${o.status || 'sent'})` : o.error), o.ok ? 0 : -1))));
  $('orders-empty').hidden = a.orders.length > 0; $('order-rows').closest('.wrap').hidden = !a.orders.length;
}
async function save(body, msg) {
  try { await post('rules', body); $('agent-msg').textContent = msg; dirty = false; } catch (e) { $('agent-msg').textContent = e.message; }
  refresh();
}
document.querySelectorAll('#agent-mode button').forEach(b => b.onclick = () => save({mode: b.dataset.mode}, ''));
$('agent-form').oninput = () => { dirty = true; };
$('agent-form').onsubmit = ev => { ev.preventDefault(); save({per_trade: $('a-per_trade').value, max_new: $('a-max_new').value, daily_loss: $('a-daily_loss').value}, 'Saved.'); };
(async function poll() { await refresh(); setTimeout(poll, 5000); })();
