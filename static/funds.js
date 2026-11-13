const {$, el, n2, pct, tone, spark, avatar, when} = UI;
UI.shell('funds');
const CUR = {INR: '₹', USD: '$'}, TITLE = {INR: ['India', 'AMFI NAVs via mfapi.in, in rupees'], USD: ['US', 'Yahoo NAVs, in dollars']};
const money = (c, x) => CUR[c] + n2(x), CATS = ['index', 'flexi', 'large', 'mid', 'small', 'thematic', 'bond', 'liquid'];
for (const c of CATS) $('a-cat').append(new Option(c, c));
let last = null, token = '', pending = false;

function status(f, trades) {  // [tone, label, reason]
  if (f.error) return ['down', 'No data', f.error];
  const t = trades.find(x => x.fund === f.id);
  if (t && t.side === 'SKIP' && f.last_month && t.nav_date.slice(0, 7) === f.last_month) return ['warn', t.reason.startsWith('Paused') ? 'Paused' : 'Blocked', t.reason];
  if (Number(f.monthly) === 0) return ['', 'SIP is 0', 'Set a monthly amount in Edit rules.'];
  if (!f.last_month) return ['', 'Waiting', 'The first SIP buys at the first NAV on or after the start date.'];
  return ['up', `SIP ${f.last_month}`, ''];
}

function card(cur, funds, s) {
  const box = $('pf-' + cur); box.replaceChildren();
  const t = s.totals[cur], head = el('div', 'hero-head'), title = el('div'), gain = t ? Number(t.value) - Number(t.invested) : 0, ret = t && Number(t.invested) ? gain / Number(t.invested) : null;
  title.append(el('p', 'label', TITLE[cur][0] + ' portfolio'), el('span', 'val num', t ? money(cur, t.value) : '—'), tone(el('p', 'hero-read num', t && Number(t.invested) ? `${gain < 0 ? '−' : '+'}${money(cur, Math.abs(gain))} (${pct(ret)}) on ${money(cur, t.invested)} invested` : TITLE[cur][1]), gain));
  const add = el('button', '', 'Add fund'); add.type = 'button'; add.onclick = () => openAdd(cur);
  head.append(title, el('div', '', add)); box.append(head);
  const kp = el('div', 'kpis');
  for (const [label, text, x] of [['Invested', t ? money(cur, t.invested) : '—'], ['Current value', t ? money(cur, t.value) : '—'], ['Gain', t && Number(t.invested) ? pct(ret) : '—', gain], ['XIRR', t ? pct(t.xirr) : '—', t && t.xirr]]) {
    const v = el('span', 'val num', text); if (x != null) tone(v, x); kp.append(el('div', 'kpi', el('span', 'label', label), v));
  }
  box.append(kp);
  if (!funds.length) { box.append(el('p', 'empty', 'No funds yet. Use Add fund.')); return; }
  const total = funds.reduce((a, f) => a + Number(f.value), 0);
  if (total > 0) {
    const bar = el('div', 'alloc'), key = el('div', 'alloc-key');
    funds.filter(f => Number(f.value) > 0).forEach(f => { const i = el('i'); i.style.width = (Number(f.value) / total * 100) + '%'; i.title = `${f.name}: ${pct(Number(f.value) / total, 0).replace('+', '')}`; bar.append(i); key.append(el('span', '', `${f.name.replace(/ \(Direct\)| Fund| Index/g, '')} ${(Number(f.value) / total * 100).toFixed(0)}%`)); });
    box.append(bar, key);
  }
  const rows = el('div', 'rows'); rows.style.marginTop = '14px';
  for (const f of funds) {
    const first = f.spark[0], year = f.spark.length > 1 ? f.spark[f.spark.length - 1] / first - 1 : null, g = Number(f.invested) ? Number(f.value) / Number(f.invested) - 1 : null, [t, label, why] = status(f, s.trades);
    const row = el('div', 'row fund-row', avatar(f.name.replace(/^(UTI|HDFC|ICICI|Nippon|Parag|Motilal|Quant|Vanguard)\s*/i, '').slice(0, 2) || f.name),
      el('span', 'r-name', el('strong', '', f.name), el('span', '', `${f.cat} · ${n2(f.units, 3)} units · goal ${f.goal_years} y · SIP ${money(cur, f.monthly)}/month`)),
      spark(f.spark, year), el('span', 'r-num', el('strong', 'num', f.nav ? n2(f.nav, 2) : '—'), el('span', 'dim', f.nav_date ? 'NAV ' + f.nav_date : 'no NAV')),
      el('span', 'r-num', el('strong', 'num', money(cur, f.invested)), el('span', 'dim', 'invested')),
      el('span', 'r-num', el('strong', 'num', money(cur, f.value)), tone(el('span', 'num', g == null ? '—' : `${pct(g)} · XIRR ${pct(f.xirr)}`), g)),
      Object.assign(el('span', 'badge ' + t, label), {title: why}));
    if (why && t) row.append(el('span', 'dim sub', why)), (row.lastChild.style.gridColumn = '2 / -1');
    rows.append(row);
  }
  box.append(rows);
}

function render(s) {
  last = s; token = s.token; s.trades = s.trades || [];
  UI.controls('funds', {...s, status: `${Object.keys(s.funds).length} funds`}, pending); $('edit-rules').disabled = false;
  $('error').hidden = !s.error; $('error').textContent = s.error || '';
  UI.clock(null);
  const funds = Object.entries(s.funds).map(([id, f]) => ({...f, id}));
  card('INR', funds.filter(f => f.cur === 'INR'), s); card('USD', funds.filter(f => f.cur === 'USD'), s);
  $('decision').textContent = (!s.running ? 'Paused: no new SIPs are bought until you press Start. ' : !s.trades.length ? `Running. The first buys happen at the first NAV on or after ${s.started}. ` : '')
    + s.decision + (s.updated ? ` Last check ${new Date(s.updated * 1000).toLocaleString()}.` : '');
  const r = s.rules, list = $('rules-list'); list.replaceChildren();
  for (const [k, v] of [['Buys', 'first NAV on or after the 1st'], ['Step-up', Number(r.step_up) ? `${(r.step_up * 100).toFixed(1)}% a year` : 'Off'], ['Equity horizon', `${r.horizon_equity} years`], ['Small cap / thematic horizon', `${r.horizon_small} years`], ['Hot theme limit', Number(r.hot_theme) ? `${(r.hot_theme * 100).toFixed(0)}% over a year` : 'Off']])
    list.append(el('dt', '', k), el('dd', '', v));
  const rows = $('rows'); rows.replaceChildren(); $('empty').hidden = s.trades.length > 0; $('count').textContent = s.trades.length ? `${s.trades.length} rows` : '';
  for (const t of s.trades) {
    const tr = rows.insertRow(), f = s.funds[t.fund]; tr.insertCell().textContent = t.time.replace('T', ' '); tr.insertCell().textContent = f ? f.name : t.fund; tr.cells[1].className = 'name';
    const sd = tr.insertCell(); sd.textContent = t.side; sd.className = 'side ' + (t.side === 'BUY' ? 'up' : ''); tr.insertCell().textContent = n2(t.units, 3); tr.insertCell().textContent = n2(t.nav, 4); tr.insertCell().textContent = n2(t.amount);
    const rs = tr.insertCell(); rs.textContent = t.reason; rs.className = 'reason';
  }
}

async function poll() { try { render(await (await fetch('/api/funds/state')).json()); } catch (e) { $('error').hidden = false; $('error').textContent = 'Cannot reach the server.'; } }
const post = (path, body) => fetch('/api/funds/' + path, {method: 'POST', headers: {'X-App-Token': token, 'Content-Type': 'application/json'}, body: JSON.stringify(body || {})});
for (const k of ['start', 'pause', 'step']) $(k).onclick = async () => { pending = true; $(k).disabled = true; await post(k); pending = false; poll(); };

$('edit-rules').onclick = () => {
  const r = last.rules; $('f-step_up').value = +(r.step_up * 100).toFixed(4); $('f-horizon_equity').value = r.horizon_equity; $('f-horizon_small').value = r.horizon_small; $('f-hot_theme').value = +(r.hot_theme * 100).toFixed(4);
  const box = $('f-funds'); box.replaceChildren();
  for (const [id, f] of Object.entries(last.funds)) {
    const row = el('p'), name = el('span', 'dim', f.name); row.append(name);
    for (const [k, label] of [['monthly', 'SIP ' + CUR[f.cur]], ['goal_years', 'goal years']]) {
      const i = document.createElement('input'); i.type = 'number'; i.min = 0; i.step = 'any'; i.value = f[k]; i.dataset.fund = id; i.dataset.key = k; i.style.width = '90px'; i.title = label; i.setAttribute('aria-label', `${f.name} ${label}`);
      row.append(i);
    }
    box.append(row);
  }
  $('rules-error').hidden = true; $('rules').showModal();
};
$('rules-cancel').onclick = () => $('rules').close();
$('rules-form').addEventListener('submit', async e => {
  e.preventDefault();
  const body = {step_up: String(Number($('f-step_up').value) / 100), horizon_equity: $('f-horizon_equity').value, horizon_small: $('f-horizon_small').value, hot_theme: String(Number($('f-hot_theme').value) / 100), funds: {}};
  for (const i of $('f-funds').querySelectorAll('input')) (body.funds[i.dataset.fund] ||= {})[i.dataset.key] = i.value || '0';
  const res = await post('rules', body);
  if (res.ok) { $('rules').close(); poll(); } else { $('rules-error').hidden = false; $('rules-error').textContent = await res.text(); }
});

const hint = () => $('a-hint').textContent = $('a-src').value === 'mfapi' ? 'The AMFI scheme code, e.g. 120503. Find one at https://api.mfapi.in/mf/search?q=fund name' : 'The Yahoo ticker of a US mutual fund, e.g. VWELX.';
function openAdd(cur) { $('a-src').value = cur === 'USD' ? 'yahoo' : 'mfapi'; hint(); $('a-code').value = ''; $('a-name').value = ''; $('add-error').hidden = true; $('add').showModal(); }
$('a-src').onchange = hint; $('add-cancel').onclick = () => $('add').close();
$('add-form').addEventListener('submit', async e => {
  e.preventDefault(); $('add-save').disabled = true; $('add-error').hidden = true;
  const res = await post('rules', {add_fund: {src: $('a-src').value, code: $('a-code').value, cat: $('a-cat').value, monthly: $('a-monthly').value || '0', name: $('a-name').value}});
  $('add-save').disabled = false;
  if (res.ok) { $('add').close(); poll(); } else { $('add-error').hidden = false; $('add-error').textContent = await res.text(); }
});
poll(); setInterval(poll, 5000);
