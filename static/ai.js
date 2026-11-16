// AI brain: what the analyst decided on each Alpaca buy, what the researcher changed in the strategy, and whether either helped.
const {$, el, usd, signed, pct, tone, when} = UI;
UI.shell('ai');
let token = '', armed = 0, flash = '';
const money = x => '$' + Number(x || 0).toFixed(Number(x) < 1 ? 3 : 2);
const LABEL = {entry: 'Buy on', stop: 'Stop', target: 'Target', trail: 'Trailing stop', breakeven: 'Break-even after', exit_low: 'Channel exit', congress_days: 'Congress filter', max_open: 'Most open', max_sector: 'Most per sector', symbols: 'Watchlist'};
function value(k, v) {
  if (k === 'entry') return [].concat(v).join(' or ');
  if (k === 'symbols') return `${v.length} stocks: ${v.join(' ')}`;
  if (['stop', 'trail', 'breakeven'].includes(k)) return Number(v) ? (k === 'stop' ? '−' : '') + (Number(v) * 100).toFixed(1).replace(/\.0$/, '') + '%' : 'off';
  if (k === 'target') return Number(v) >= 100 ? 'none' : '+' + (Number(v) * 100).toFixed(1).replace(/\.0$/, '') + '%';
  if (k === 'exit_low') return Number(v) ? `under the ${v}-day closing low` : 'off';
  if (k === 'congress_days') return Number(v) ? `a House purchase filed in the last ${v} days` : 'off';
  return Number(v) ? String(v) : 'no limit';
}
async function post(path, body) {
  const res = await fetch('/api/ai/' + path, {method: 'POST', headers: {'X-App-Token': token, 'Content-Type': 'application/json'}, body: body ? JSON.stringify(body) : undefined});
  if (!res.ok) throw Error(await res.text());
}
function run(r) {
  if (r.kind === 'rollback') return el('div', 'banner', `${when(r.at)} · You undid the last change: ${Object.entries(r.restored).map(([k, v]) => `${LABEL[k] || k} back to ${value(k, v)}`).join(', ')}.`);
  const head = el('div', 'card-head', el('h3', '', `${when(r.at)} · ${r.trigger === 'weekly' ? 'Weekly' : 'On request'}`),
    el('span', 'badge ' + (r.decision === 'adopt' ? 'up' : r.decision === 'error' ? 'down' : ''), r.decision === 'adopt' ? 'Changed the strategy' : r.decision === 'error' ? 'Failed' : 'Kept the strategy'));
  const kids = [head, el('p', 'trigger', r.memo || r.error || '')];
  if (r.changes) kids.push(el('p', 'dim sub', r.changes.map(k => `${LABEL[k] || k}: ${value(k, r.before[k])} → ${value(k, r.after[k])}`).join(' · ')));
  if (r.baseline) kids.push(el('p', 'dim sub', `5-year replay: ${signed(r.baseline.total)} before` + (r.chosen ? `, ${signed(r.chosen.total)} after` : '') + ` · holding the same stocks ${signed(r.baseline.buy_hold)} · ${r.replays.length} recipes tested · ${r.model || ''} · ${money(r.cost)}`));
  if (r.replays && r.replays.length) {
    const rows = r.replays.map(t => el('tr', '', el('td', 'why', t.note), el('td', '', Object.entries(t.recipe || {}).map(([k, v]) => `${LABEL[k] || k} ${value(k, v)}`).join(', ') || 'current'),
      t.result.error ? el('td', 'why', t.result.error) : tone(el('td', 'num', signed(t.result.total)), t.result.total), el('td', 'num', t.result.error ? '' : `${t.result.entries} · ${pct(t.result.max_drawdown)}`)));
    const d = el('details', '', el('summary', 'dim sub', 'Recipes it tested'), el('div', 'wrap', el('table', 'grid', el('thead', '', el('tr', '', el('th', '', 'Test'), el('th', '', 'Recipe'), el('th', '', 'Replay'), el('th', '', 'Trades · worst drop'))), el('tbody', '', ...rows))));
    kids.push(d);
  }
  return el('section', 'card desk-card', ...kids);
}
async function refresh() {
  try {
    const res = await fetch('/api/ai/state'); if (!res.ok) throw Error('cannot read server state');
    const s = await res.json(), sc = s.scorecard, a = sc.analyst; token = s.token;
    $('error').hidden = !s.error; $('error').textContent = s.error || '';
    $('setup').hidden = s.configured;
    $('mode').textContent = `Trade analyst and strategy researcher · ${s.model}` + (flash ? ' · ' + flash : '');
    $('status').textContent = !s.configured ? 'Off' : s.busy ? s.progress || 'Researching' : 'On'; $('status').className = s.configured && !s.busy ? 'live' : '';
    $('research').disabled = !s.configured || s.busy;
    const rb = $('rollback'), ready = Date.now() - armed < 8000; rb.hidden = !s.can_rollback; rb.textContent = ready ? 'Click again to undo' : 'Undo last change';
    const changes = sc.changes, last = changes[changes.length - 1];
    $('headline').textContent = a.approved + a.vetoed ? (a.vetoed_move == null || a.approved_move == null ? `${a.approved + a.vetoed} reviewed` : `${pct(a.approved_move - a.vetoed_move)}`) : '—';
    $('headline-sub').textContent = a.vetoed_move != null && a.approved_move != null ? 'approved buys minus vetoed ones, average move since the review (positive = the vetoes helped)' : 'Needs approved and vetoed buys to compare. Spend: ' + money(sc.cost_30d) + ' in 30 days, ' + money(sc.cost_total) + ' in all.';
    $('k-changes').textContent = String(changes.length);
    $('k-changes-sub').textContent = last ? `last: replay ${signed(last.replay_before)} → ${signed(last.replay_after)}` + (last.sim_since != null ? ` · ${pct(last.sim_since)} since` : '') : s.next_run ? `next research ${when(s.next_run)}` : 'first research once the key is set';
    $('k-reviews').textContent = String(a.approved + a.vetoed); $('k-reviews-sub').textContent = `${a.approved} approved · ${a.vetoed} vetoed · ${money(sc.cost_30d)} spent in 30 days`;
    tone($('k-approved'), a.approved_move).textContent = pct(a.approved_move); tone($('k-vetoed'), a.vetoed_move).textContent = pct(a.vetoed_move);
    $('recipe').replaceChildren(...Object.entries(s.recipe).flatMap(([k, v]) => [el('dt', '', LABEL[k] || k), el('dd', '', value(k, v), k === 'entry' ? el('span', 'dim sub', ' · ' + [].concat(v).map(x => s.entries[x]).join(' / ')) : null)]));
    $('locked').textContent = `Fixed by you, out of the AI's reach: ${usd(s.locked.per_trade)} a position in the simulated book (Alpaca uses its own amount on Action needed), no new buys once down ${usd(s.locked.daily_loss)}, ${s.locked.max_new} new buys a day.` + (s.next_run ? ` Next weekly research ${when(s.next_run)}.` : '');
    $('runs').replaceChildren(...s.runs.map(run)); $('runs-empty').hidden = s.runs.length > 0;
    $('review-rows').replaceChildren(...s.reviews.map(r => el('tr', '', el('td', '', when(r.at)), el('td', '', el('span', 'sym', UI.avatar(r.symbol), r.symbol)),
      el('td', '', el('span', 'badge ' + (r.verdict === 'approve' ? 'up' : r.verdict === 'veto' ? 'down' : 'warn'), r.verdict === 'error' ? 'Error' : `${r.verdict === 'approve' ? 'Approved' : 'Vetoed'} · ${Math.round(r.confidence * 100)}%`)),
      el('td', 'why', r.verdict === 'error' ? r.error : (r.reasons || []).join(' ') + (r.sources && r.sources.length ? ` Sources: ${r.sources.join('; ')}` : '')),
      el('td', 'num', r.price ? usd(r.price) : '—'))));
    holdings(s); chat(s);
    $('reviews-empty').hidden = s.reviews.length > 0; $('review-rows').closest('.wrap').hidden = !s.reviews.length;
  } catch (e) {
    $('error').hidden = false; $('error').textContent = `Dashboard disconnected (${e.message}). Values shown may be stale.`;
    $('status').textContent = 'Disconnected'; $('status').className = 'err';
  }
}
function holdings(s) {
  const h = s.holdings_review;
  $('hold-run').disabled = !s.configured || s.reviewing; $('hold-run').textContent = s.reviewing ? 'Reviewing…' : 'Review holdings now';
  $('hold-sub').textContent = h ? `${when(h.at)} · ${h.trigger === 'button' ? 'on request' : 'daily'} · ${h.model || ''} · advice only, nothing is sold` : 'the AI reviews every Alpaca holding once a day in session · advice only, nothing is sold';
  $('hold-summary').textContent = h ? (h.error ? `Review failed: ${h.error}` : h.summary) : ''; $('hold-summary').hidden = !h;
  const rows = h && !h.error ? h.holdings : [];
  $('hold-rows').replaceChildren(...rows.map(r => {
    const then = Number((h.prices || {})[r.symbol]);
    return el('tr', '', el('td', '', el('span', 'sym', UI.avatar(r.symbol), r.symbol)),
      el('td', '', el('span', 'badge ' + (r.verdict === 'hold' ? 'up' : 'down'), `${r.verdict === 'hold' ? 'Hold' : 'Consider exit'} · ${Math.round(r.confidence * 100)}%`)),
      el('td', 'why', r.reasons.join(' ') + (r.sources.length ? ` Sources: ${r.sources.join('; ')}` : '')), el('td', 'why', r.risks.join(' ')),
      el('td', 'num', then ? usd(then) : '—'));
  }));
  $('hold-empty').hidden = rows.length > 0; $('hold-rows').closest('.wrap').hidden = !rows.length;
  $('hold-empty').textContent = s.configured ? 'No holdings review yet. It runs once a day while the market is open, or click Review holdings now.' : 'The AI is off.';
}
let seen = -1;
function chat(s) {
  const log = $('ask-log'), items = s.chat.flatMap(r => [el('p', 'ask-q', r.question), el('p', 'ask-a' + (r.answer ? '' : ' down'), r.answer || `Could not answer: ${r.error}`)]);
  if (s.asking && s.pending) items.push(el('p', 'ask-q', s.pending), el('p', 'ask-a dim', 'Thinking… free models can take up to a minute.'));
  if (!items.length) items.push(el('p', 'dim sub', 'No questions yet.'));
  const n = s.chat.length + (s.asking ? .5 : 0);
  if (n !== seen) { log.replaceChildren(...items); log.scrollTop = log.scrollHeight; seen = n; }
  $('ask-send').disabled = !s.configured || s.asking; $('ask-open').disabled = !s.configured;
}
$('ask-open').onclick = () => { $('ask').showModal(); $('ask-q').focus(); const l = $('ask-log'); l.scrollTop = l.scrollHeight; };
$('ask-close').onclick = () => $('ask').close();
$('ask-q').onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('ask-form').requestSubmit(); } };
$('ask-form').onsubmit = async e => {
  e.preventDefault(); const q = $('ask-q').value.trim(); if (!q) return;
  try { await post('ask', {question: q}); $('ask-q').value = ''; $('ask-msg').textContent = ''; } catch (err) { $('ask-msg').textContent = err.message; }
  refresh();
};
$('hold-run').onclick = async () => { try { await post('holdings'); flash = 'Holdings review started'; } catch (e) { flash = e.message; } refresh(); };
if (location.hash === '#ask') setTimeout(() => $('ask-open').click(), 300);
$('research').onclick = async () => { try { await post('research'); flash = 'Research started'; } catch (e) { flash = e.message; } refresh(); };
$('rollback').onclick = async () => {
  if (Date.now() - armed > 8000) { armed = Date.now(); return refresh(); }
  armed = 0; try { await post('rollback'); flash = 'Last change undone'; } catch (e) { flash = e.message; } refresh();
};
(async function poll() { await refresh(); setTimeout(poll, 4000); })();
