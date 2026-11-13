// Action needed: the real account (read-only import) against the swing rules you chose, as cards you act on yourself.
const {$, el, usd, signed, pct, tone, when} = UI;
UI.shell('desk');
const px = n => '$' + Number(n).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
const ago = t => { const m = Math.round((Date.now() / 1000 - t) / 60); return m < 1 ? 'just now' : m < 60 ? `${m} min ago` : m < 2880 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} days ago`; };
const quoteLine = c => `Quote ${px(c.price)}${c.quote_at ? ` at ${when(c.quote_at)}` : ''} · Yahoo, may lag`;

function card(c) {
  const sell = c.action === 'SELL', head = el('div', 'card-head', el('h3', '', `${sell ? 'Sell' : 'Buy'} ${c.symbol}`),
    el('span', 'badge ' + (c.passes ? 'up' : 'down'), c.passes ? 'Passes your limits' : 'Fails a limit'));
  const rows = sell
    ? [['Shares', Number(c.qty).toFixed(4)], ['Proceeds at this quote', usd(c.proceeds)], ['Result vs entry', signed(c.est_pnl)]]
    : [['Proposed size', `${Number(c.qty).toFixed(4)} shares ≈ ${usd(c.cost)}`], ['Planned stop', px(c.stop)], ['Planned target', px(c.target)], ['Estimated loss at the stop', '−' + usd(c.est_loss)]];
  const dl = el('dl', '', ...rows.flatMap(([k, v]) => [el('dt', '', k), tone(el('dd', 'num', v), k.startsWith('Result') ? Number(c.est_pnl) : 0)]));
  return el('section', 'card desk-card', head, el('p', 'trigger', c.trigger), el('p', 'dim sub', quoteLine(c)), dl, el('p', 'est', c.est_note),
    el('ul', 'checks', ...c.checks.map(k => el('li', k.ok ? '' : 'fail', k.text))));
}

async function refresh() {
  try {
    const res = await fetch('/api/desk/state'); if (!res.ok) throw Error('cannot read server state');
    const s = await res.json(), h = s.holdings, r = s.rules;
    $('status').textContent = s.updated ? `Checked ${new Date(s.updated * 1000).toLocaleTimeString()}` : 'No check yet'; $('status').className = s.updated ? 'live' : '';
    $('error').hidden = true;
    $('notes').replaceChildren(...s.notes.map(n => el('div', 'banner', n.text)));
    const watch = s.watching, inv = watch.filter(w => w.purpose !== 'swing'), sw = watch.filter(w => w.purpose === 'swing');
    const sum = xs => xs.reduce((a, w) => a + Number(w.value), 0), cash = h && h.cash != null ? Number(h.cash) : null;
    $('account').textContent = h ? ` · ${h.account}` : ' · none imported';
    $('value').textContent = h ? usd((cash || 0) + sum(watch)) : '—';
    $('synced').textContent = h ? `Synced ${ago(h.synced_at)} (${new Date(h.synced_at * 1000).toLocaleString()}) from ${h.source}` : 'Import a brokerage CSV to begin.';
    $('cash').textContent = cash == null ? '—' : usd(cash); $('cash-sub').textContent = cash == null ? 'not in the import' : 'from the import';
    $('invest').textContent = usd(sum(inv)); $('invest-sub').textContent = `${inv.length} holding${inv.length === 1 ? '' : 's'}`;
    $('swing').textContent = usd(sum(sw)); $('swing-sub').textContent = `${sw.length} open`;
    $('todo').textContent = String(s.cards.length); $('todo-sub').textContent = `${s.cards.filter(c => c.passes).length} pass your limits`;
    $('rules').textContent = `Buy a 20-day closing-high breakout, ${Number(r.per_trade) ? 'up to ' + usd(r.per_trade) : 'all cash'} a position, ${Number(r.max_new) ? r.max_new + ' new a day, ' : ''}stop −${(Number(r.stop) * 100).toFixed(0)}%, target +${(Number(r.target) * 100).toFixed(0)}%${Number(r.max_open) ? `, at most ${r.max_open} open` : ''}. Edit them on the US stocks page.`;
    $('checked').textContent = s.market && s.market.date ? `NYSE ${s.market.open ? 'open' : 'closed'} · ${s.market.reason || ''}` : '';
    $('cards').replaceChildren(...s.cards.map(card)); $('cards-empty').hidden = s.cards.length > 0;
    $('watch-rows').replaceChildren(...watch.map(w => el('tr', '', el('td', '', el('span', 'sym', UI.avatar(w.symbol), w.symbol)), el('td', '', w.purpose === 'swing' ? 'Swing trade' : 'Investment'),
      el('td', 'num', Number(w.qty).toFixed(4)), el('td', 'num', px(w.price)), el('td', 'num', usd(w.value)), tone(el('td', 'num', w.gain == null ? '—' : pct(Number(w.gain), 1)), w.gain),
      el('td', 'why', w.plan ? `Stop ${px(w.plan.stop)} (${pct(Number(w.to_stop), 1)} above it) · target ${px(w.plan.target)}` : 'No swing rule: an investment, held to your plan.'))));
    $('watch-empty').hidden = watch.length > 0; $('watch-rows').closest('.wrap').hidden = !watch.length;
  } catch (e) {
    $('error').hidden = false; $('error').textContent = `Dashboard disconnected (${e.message}). Values shown may be stale.`;
    $('status').textContent = 'Disconnected'; $('status').className = 'err';
  }
}
(async function poll() { await refresh(); setTimeout(poll, 5000); })();
