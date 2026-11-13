const $ = id => document.getElementById(id);
const usd = n => '$' + Number(n).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
const signed = n => (Number(n) > 0 ? '+' : Number(n) < 0 ? '−' : '') + usd(Math.abs(Number(n)));
const pct = (n, d = 1) => (n > 0 ? '+' : n < 0 ? '−' : '') + Math.abs(n * 100).toFixed(d) + '%';
const tone = (el, n) => { el.classList.toggle('up', Number(n) > 0); el.classList.toggle('down', Number(n) < 0); };
const [, MARKET, SUB] = location.pathname.split('/'), M = MARKET === 'crypto' ? 'crypto' : 'stocks', CRYPTO = M === 'crypto';
const pageOf = sub => ['watchlist', 'activity', 'congress'].includes(sub) && !(CRYPTO && sub === 'congress') ? sub : 'overview';
let PAGE = pageOf(SUB);  // sub pages share this document; the nav swaps sections without a reload
const px = n => '$' + Number(n).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: Math.abs(n) < 10 ? 5 : 2});  // prices: small coins need more decimals
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const NAMES = {AAPL: 'Apple', MSFT: 'Microsoft', NVDA: 'NVIDIA', AMZN: 'Amazon', GOOGL: 'Alphabet', META: 'Meta Platforms', TSLA: 'Tesla',
  AMD: 'Advanced Micro Devices', AVGO: 'Broadcom', NFLX: 'Netflix', JPM: 'JPMorgan Chase', V: 'Visa', MA: 'Mastercard', UNH: 'UnitedHealth',
  LLY: 'Eli Lilly', COST: 'Costco', WMT: 'Walmart', KO: 'Coca-Cola', PEP: 'PepsiCo', XOM: 'Exxon Mobil',
  BTC: 'Bitcoin', ETH: 'Ethereum', SOL: 'Solana', XRP: 'XRP', DOGE: 'Dogecoin', ADA: 'Cardano', AVAX: 'Avalanche', LINK: 'Chainlink', LTC: 'Litecoin', DOT: 'Polkadot'};
const UNIT = CRYPTO ? 'units' : 'shares';
document.title = `Signal Desk · ${CRYPTO ? 'Crypto' : 'US stocks'}`;
UI.shell(M); $('title').textContent = CRYPTO ? 'Crypto' : 'US stocks';
const pages = $('pages'), pill = pages.querySelector('.pill');
if (CRYPTO) $('nav-congress').hidden = true;  // House filings only cover stocks
// Liquid marker: its two edges are springs, the edge in the direction of travel stiff and the other soft, so the drop stretches toward the
// page you pick and snaps back. On long jumps it sheds droplets that merge back in (the goo filter fuses nearby blobs).
const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches, goo = pages.querySelector('.goo');
const liquid = {L: 0, R: 0, vL: 0, vR: 0, tL: 0, tR: 0, drops: [], raf: 0, last: 0, ready: false};
function paintPill(squash) {
  const q = liquid; pill.style.width = Math.max(q.R - q.L, 0) + 'px'; pill.style.transform = `translateX(${q.L}px) scaleY(${squash})`;
  q.drops.forEach(d => { d.el.style.transform = `translate(${d.x - 7}px, ${d.y}px) scale(${Math.max(d.r, 0) / 7})`; });
}
function tick(now) {
  const q = liquid, dt = Math.min((now - q.last) / 1000, 1 / 30), right = q.tL + q.tR > q.L + q.R; q.last = now;
  let still = true;
  [['L', 'vL', 'tL', !right], ['R', 'vR', 'tR', right]].forEach(([x, v, t, lead]) => {
    q[v] += ((lead ? 210 : 72) * (q[t] - q[x]) - (lead ? 25 : 13) * q[v]) * dt; q[x] += q[v] * dt;
    if (Math.abs(q[t] - q[x]) > .3 || Math.abs(q[v]) > 3) still = false;
  });
  q.drops = q.drops.filter(d => {
    d.vx += (d.k * (d.tx - d.x) - 9 * d.vx) * dt; d.x += d.vx * dt;
    if (Math.abs(d.tx - d.x) < (q.tR - q.tL) / 2) d.r -= dt * 22;
    if (d.r > .6) return true; d.el.remove(); return false;
  });
  if (q.drops.length) still = false;
  paintPill(1 - .18 * Math.min(Math.abs(q.vL + q.vR) / 2 / 1400, 1));
  q.raf = still ? 0 : requestAnimationFrame(tick);
  if (still) { paintPill(1); }
}
const movePill = () => {
  const a = pages.querySelector('[aria-current]'), q = liquid, tL = a.offsetLeft, tR = tL + a.offsetWidth, far = Math.abs(tL - q.L) > a.offsetWidth * 1.4;
  if (!q.ready || reduce) { Object.assign(q, {L: tL, R: tR, vL: 0, vR: 0, tL, tR, ready: true}); paintPill(1); return; }
  if (far && !q.raf) [.3, .6].forEach(f => {
    const el = document.createElement('i'); el.className = 'drop'; goo.append(el);
    q.drops.push({el, x: q.L + (tL - q.L) * f, y: 9 + (Math.random() - .5) * 6, vx: 0, tx: tL + a.offsetWidth / 2, k: 60 + Math.random() * 80, r: 6});
  });
  Object.assign(q, {tL, tR}); if (!q.raf) { q.last = performance.now(); q.raf = requestAnimationFrame(tick); }
};
function show(page) {
  if (page !== PAGE && document.startViewTransition && !reduce) { PAGE = page; return document.startViewTransition(() => paintPage()); }
  PAGE = page; paintPage();
}
function paintPage() {
  document.querySelectorAll('[data-page]').forEach(e => { e.hidden = e.dataset.page !== PAGE; });
  pages.querySelectorAll('a').forEach(a => a.toggleAttribute('aria-current', a.dataset.p === PAGE));
  movePill(); if (PAGE === 'congress') congress();
}
pages.querySelectorAll('a').forEach(a => { a.href = `/${M}${a.dataset.p === 'overview' ? '' : '/' + a.dataset.p}`; });
pages.onclick = e => {
  const a = e.target.closest('a'); if (!a || e.metaKey || e.ctrlKey || e.shiftKey) return;
  e.preventDefault(); a.classList.remove('hop'); void a.offsetWidth; a.classList.add('hop');
  if (a.dataset.p !== PAGE) { history.pushState(null, '', a.href); show(a.dataset.p); }
};
addEventListener('popstate', () => show(pageOf(location.pathname.split('/')[2])));
show(PAGE);
addEventListener('resize', movePill); document.fonts?.ready.then(movePill);
$('source').textContent = CRYPTO ? ' · Coinbase, paper' : ' · Yahoo, paper';
$('export').href = `/api/${M}/export`; $('export').download = `${M}-trades.csv`;
if (CRYPTO) {
  for (const id of ['r-congress-label', 'r-congress', 'f-congress-box']) $(id).hidden = true;  // House filings only cover stocks
  $('r-trades').textContent = '24/7'; $('r-slip-label').textContent = 'Fee + slippage';
  $('foot').textContent = 'Paper trading only: no exchange orders. Prices are Coinbase public USD quotes; fills are simulated at the last price with an assumed 0.6% fee plus 0.05% slippage per side. Crypto trades around the clock and can gap through a stop between checks. Not investment advice.';
}
// Decision text -> short status badge.
const BADGES = [[/^Holding/, 'Held', 'up', 'You own it. Stops and targets are being watched.'], [/^Buy/, 'Bought', 'up', 'Bought on the last check.'],
  [/^(Stop|Trailing|Channel|Target)/, 'Sold', '', 'Sold on the last check by one of your exit rules.'],
  [/^Signal, but price .* back under/, 'Faded', 'down', 'It broke out at the close, but the live price has slipped back under the breakout level, so no buy. It buys if the price climbs back over.'],
  [/^Signal, but no cash/, 'No cash', 'down', 'A valid buy signal, but there is no free cash at all. With less than the per-position cap it buys a smaller position; it skips only when the account is empty.'],
  [/^Signal(,| but)/, 'Blocked', 'down', 'A valid signal that a safety rule stopped (limit, filter or replay check). The reason is shown on the row.'],
  [/^Signal already/, 'Used', '', 'Already acted on today.'], [/^Signal/, 'Signal', 'up', 'New 20-day closing high, ready to buy.'], [/^No quote/, 'Error', 'down', 'No price came back, so nothing was traded.']];
// Motion. Numbers roll to their new value, a price that just changed flashes once, charts wipe in on first paint, and rows stagger in once.
// All of it is skipped under prefers-reduced-motion; the values are the same either way.
const lastPx = {}, wiped = new Set();
let firstPaint = true, range = 'all', hovering = false, heroSet = false;
// Odometer: every digit is a column 0-9 that slides to its value, so a change rolls instead of swapping.
function odo(id, text) {
  const node = $(id);
  if (reduce) { node.textContent = text; return; }
  const shape = text.replace(/\d/g, '#'), built = node._shape !== shape;
  node.setAttribute('role', 'text'); node.setAttribute('aria-label', text);
  if (built) {
    node._shape = shape;
    node.replaceChildren(...[...text].map(c => /\d/.test(c) ? el('span', 'dg', el('span', 'col', ...Array.from({length: 10}, (_, d) => el('i', '', String(d))))) : el('span', 'ch', c)));
    node.querySelectorAll('.col').forEach((c, k) => { c.style.setProperty('--n', 0); c.style.transitionDelay = k * 45 + 'ms'; });
  }
  node._text = text;  // a frame queued by an earlier call must roll to the newest value, not the one it was queued with
  const go = () => node.querySelectorAll('.col').forEach((c, k) => c.style.setProperty('--n', node._text.replace(/\D/g, '')[k]));
  built ? requestAnimationFrame(() => requestAnimationFrame(go)) : go();
}
function flashed(node, key, price) {
  const prev = lastPx[key], now = Number(price); lastPx[key] = now;
  if (prev !== undefined && prev !== now && !reduce) node.classList.add(now > prev ? 'tick-up' : 'tick-down');
  return node;
}
const intro = (node, i) => { if (firstPaint && !reduce) { node.classList.add('intro'); node.style.setProperty('--i', Math.min(i, 12)); } return node; };
let token = '', pending = false, actionError = '', last = null, shown = '';

function icon(name, cls = '') {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'), use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  svg.setAttribute('class', 'i ' + cls); svg.setAttribute('aria-hidden', 'true'); use.setAttribute('href', '#i-' + name); svg.append(use);
  return svg;
}
// Logo from /logos/<SYM>.png|svg, falling back to the first two letters if it fails to load.
const LOGO_BG = {V: '#1a1f71', UNH: '#002677', AMZN: '#232f3e'};  // these logos are white, made for a dark brand background
function logo(sym, cls = '') {
  const box = el('span', 'logo ' + cls), img = new Image();
  if (LOGO_BG[sym]) box.style.background = LOGO_BG[sym];
  img.alt = ''; img.src = `/logos/${sym}.${CRYPTO ? 'svg' : 'png'}`;
  img.onerror = () => { box.classList.add('text'); box.replaceChildren(sym.slice(0, 2)); };
  box.append(img); return box;
}
const el = (tag, cls, ...kids) => { const e = document.createElement(tag); if (cls) e.className = cls; e.append(...kids); return e; };

function rows(tbody, data) {
  tbody.replaceChildren(...data.map(cells => el('tr', '', ...cells.map(([content, cls]) => el('td', cls || '', content)))));
}

// 30-day mini chart for a watchlist card: line plus a faint area, colored by the 30-day direction.
function mini(closes, price, wipe) {
  const v = [...closes, Number(price)], lo = Math.min(...v), hi = Math.max(...v), span = hi - lo || 1, NS = 'http://www.w3.org/2000/svg';
  const pts = v.map((n, i) => `${(i / (v.length - 1) * 96).toFixed(1)},${(30 - (n - lo) / span * 26).toFixed(1)}`).join(' ');
  const svg = document.createElementNS(NS, 'svg'), cls = v.at(-1) >= v[0] ? 'up' : 'down';
  svg.setAttribute('viewBox', '0 0 96 32'); svg.setAttribute('class', 'mini ' + cls + (wipe ? ' wipe' : '')); svg.setAttribute('aria-hidden', 'true');
  for (const [tag, p] of [['polygon', `0,32 ${pts} 96,32`], ['polyline', pts]]) { const e = document.createElementNS(NS, tag); e.setAttribute('points', p); svg.append(e); }
  return svg;
}

// Hero chart: area + line in pixel space, a pulsing live dot, a range switch and a scrubbing crosshair.
const NS = 'http://www.w3.org/2000/svg', svgEl = (name, attrs = {}, parent) => { const e = document.createElementNS(NS, name); for (const k in attrs) e.setAttribute(k, attrs[k]); parent?.append(e); return e; };
function hero(history, capital) {
  const now = Date.now() / 1000;
  if (!heroSet) {  // open on the last day when it has enough points to draw, else on everything
    heroSet = true; range = history.filter(p => p[0] >= now - 86400).length >= 20 ? '1d' : 'all';
    document.querySelectorAll('.seg button[data-r]').forEach(x => x.setAttribute('aria-pressed', x.dataset.r === range));
  }
  const svg = $('hero'), box = $('hero-box'), span = {'1d': 86400, '1w': 604800}[range];
  let h = history.filter(p => !span || p[0] >= now - span); if (h.length < 2) h = history.slice(-2);
  let f = 0; while (f + 1 < h.length - 1 && h[f + 1][1] === h[0][1]) f++;  // a flat run before anything changed says nothing; keep its last point
  h = h.slice(f);
  svg.replaceChildren(); if (h.length < 2) { $('hero-read').textContent = '—'; return; }
  const W = box.clientWidth || 800, H = 230, R = 62, B = 8, v = h.map(p => p[1]), pad = (Math.max(...v, capital) - Math.min(...v, capital)) * .12 || 1;
  const lo = Math.min(...v, capital) - pad, hi = Math.max(...v, capital) + pad, X = i => i / (h.length - 1) * (W - R), Y = n => H - B - (n - lo) / (hi - lo) * (H - B - 10);
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`); svg.setAttribute('height', H);
  const up = v.at(-1) >= capital, c = css(up ? '--up' : '--down');
  [hi - pad, capital, lo + pad].forEach((n, k) => {
    svgEl('line', {x1: 0, x2: W - R + 8, y1: Y(n), y2: Y(n), stroke: css('--rule'), 'stroke-dasharray': k === 1 ? '3 4' : '', opacity: k === 1 ? 1 : .5}, svg);
    svgEl('text', {x: W - R + 14, y: Y(n) + 4, fill: css('--dim'), 'font-size': 11, class: 'num'}, svg).textContent = usd(n).replace(/\.\d\d$/, '');
  });
  const pts = v.map((n, i) => `${X(i).toFixed(1)},${Y(n).toFixed(1)}`), fresh = !wiped.has('hero' + range) && !reduce;
  const area = svgEl('polygon', {points: `0,${H - B} ${pts.join(' ')} ${X(v.length - 1)},${H - B}`, fill: c, opacity: .09}, svg);
  const line = svgEl('polyline', {points: pts.join(' '), fill: 'none', stroke: c, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round'}, svg);
  const ex = X(v.length - 1), ey = Y(v.at(-1));
  if (!reduce) svgEl('circle', {cx: ex, cy: ey, r: 4, fill: c, class: 'ping'}, svg);
  svgEl('circle', {cx: ex, cy: ey, r: 4, fill: c, stroke: css('--surface'), 'stroke-width': 2}, svg);
  if (fresh) {
    wiped.add('hero' + range); const len = Math.ceil(Number(line.getTotalLength?.()) || 2000);
    line.style.setProperty('--len', len); line.classList.add('drawn'); area.classList.add('fadein');
  }
  const cross = svgEl('line', {y1: 0, y2: H - B, stroke: css('--dim'), opacity: 0}, svg), dot = svgEl('circle', {r: 4.5, fill: css('--fg'), stroke: css('--surface'), 'stroke-width': 2, opacity: 0}, svg);
  const net = v.at(-1) - capital, read = (txt) => { $('hero-read').textContent = txt; };
  read(`${{'1d': 'Last 24 hours', '1w': 'Last 7 days', all: 'Since start'}[range]}: ${signed(v.at(-1) - v[0])} · overall ${signed(net)} (${pct(net / capital, 2)})`);
  svg.onpointermove = e => {
    hovering = true; const r = svg.getBoundingClientRect(), i = Math.round(Math.min(Math.max((e.clientX - r.left) / (W - R) / (r.width / W), 0), 1) * (h.length - 1)), tip = $('hero-tip');
    cross.setAttribute('x1', X(i)); cross.setAttribute('x2', X(i)); cross.setAttribute('opacity', .5); dot.setAttribute('cx', X(i)); dot.setAttribute('cy', Y(v[i])); dot.setAttribute('opacity', 1);
    tip.hidden = false; tip.textContent = `${usd(v[i])} · ${new Date(h[i][0] * 1000).toLocaleString([], {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'})}`;
    tip.style.left = Math.min(Math.max(X(i) - 70, 0), W - R - 140) + 'px';
  };
  svg.onpointerleave = () => { hovering = false; cross.setAttribute('opacity', 0); dot.setAttribute('opacity', 0); $('hero-tip').hidden = true; };
}
document.querySelectorAll('.seg button[data-r]').forEach(b => b.onclick = () => {
  range = b.dataset.r; document.querySelectorAll('.seg button[data-r]').forEach(x => x.setAttribute('aria-pressed', x === b)); if (last) hero(last.history, Number(last.capital));
});
// Allocation ring: one arc per position (green or red by gain) and the rest in cash; arcs draw in on the first paint.
function ring(pos, cash, equity) {
  const svg = $('ring'); svg.replaceChildren(); const C = 2 * Math.PI * 38, parts = pos.map(([sym, p]) => [Number(p.qty) * Number(p.last), Number(p.last) >= Number(p.entry)]);
  svgEl('circle', {cx: 48, cy: 48, r: 38, fill: 'none', stroke: css('--rule'), 'stroke-width': 8}, svg);
  let at = 0; const g = 1.6;
  parts.forEach(([val, good], i) => {
    const len = Math.max(val / equity * C - g, 0), a = svgEl('circle', {cx: 48, cy: 48, r: 38, fill: 'none', stroke: css(good ? '--up' : '--down'), 'stroke-width': 8, transform: 'rotate(-90 48 48)',
      'stroke-dasharray': `${len} ${C}`, 'stroke-dashoffset': -at}, svg);
    if (firstPaint && !reduce) { a.classList.add('arc'); a.style.setProperty('--d', i * 60 + 'ms'); a.style.setProperty('--len', len); a.style.setProperty('--c', C); }
    at += val / equity * C;
  });
  svgEl('text', {x: 48, y: 46, 'text-anchor': 'middle', fill: css('--fg'), 'font-size': 17, 'font-weight': 600, class: 'num'}, svg).textContent = Math.round((1 - cash / equity) * 100) + '%';
  svgEl('text', {x: 48, y: 61, 'text-anchor': 'middle', fill: css('--dim'), 'font-size': 9.5}, svg).textContent = 'invested';
}
// Price tape: built once per watchlist, then only the numbers change, so the scroll never restarts.
let tapeKey = '';
function tape(s) {
  const track = document.querySelector('#tape .tape-track'), key = s.symbols.join();
  if (key !== tapeKey) {
    tapeKey = key; const one = () => s.symbols.map(sym => el('span', 'tp', el('b', '', sym), el('span', 'num tp-p', '—'), el('span', 'num tp-c', '')));
    track.replaceChildren(...one(), ...one());
  }
  const items = track.children, n = s.symbols.length;
  s.symbols.forEach((sym, i) => {
    const [price, prev] = (s.quotes || {})[sym] || [], ch = price ? Number(price) / Number(prev) - 1 : null;
    [items[i], items[i + n]].forEach(it => { it.children[1].textContent = price ? px(price) : '—'; it.children[2].textContent = ch === null ? '' : pct(ch, 2); it.children[2].className = 'num tp-c ' + (ch >= 0 ? 'up' : 'down'); });
  });
}

// Line chart in pixel space for the stock detail and the replay: grid lines with labels at the right, area, line, optional draw-in.
function chart(svg, box, vals, o) {
  const W = box.clientWidth || 600, H = o.H, R = o.R, ext = [...vals, ...o.lines.map(l => l.v)], lo0 = Math.min(...ext), hi0 = Math.max(...ext), pad = (hi0 - lo0) * .1 || 1, lo = lo0 - pad, hi = hi0 + pad;
  const X = i => vals.length > 1 ? i / (vals.length - 1) * (W - R) : 0, Y = n => H - 6 - (n - lo) / (hi - lo) * (H - 14);
  svg.replaceChildren(); svg.setAttribute('viewBox', `0 0 ${W} ${H}`); svg.setAttribute('height', H);
  for (const l of o.lines) {
    svgEl('line', {x1: 0, x2: W - R + 8, y1: Y(l.v), y2: Y(l.v), stroke: l.color, 'stroke-dasharray': '4 4', opacity: .7}, svg);
    svgEl('text', {x: W - R + 12, y: Y(l.v) + 4, fill: l.color, 'font-size': 10.5}, svg).textContent = l.label;
  }
  const pts = vals.map((n, i) => `${X(i).toFixed(1)},${Y(n).toFixed(1)}`).join(' ');
  const area = svgEl('polygon', {points: `0,${H - 6} ${pts} ${X(vals.length - 1)},${H - 6}`, fill: o.color, opacity: .09}, svg);
  const line = svgEl('polyline', {points: pts, fill: 'none', stroke: o.color, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round'}, svg);
  if (o.fresh && !reduce) { line.style.setProperty('--len', Math.ceil(Number(line.getTotalLength?.()) || 1500)); line.classList.add('drawn'); area.classList.add('fadein'); }
  return {X, Y, W, H};
}
// Crosshair for a chart: returns a function that moves it to the nearest point and reports the index.
function scrubber(svg, ch, n, onMove, onLeave) {
  const cross = svgEl('line', {y1: 0, y2: ch.H - 6, stroke: css('--dim'), opacity: 0}, svg), dot = svgEl('circle', {r: 4.5, fill: css('--fg'), stroke: css('--surface'), 'stroke-width': 2, opacity: 0}, svg);
  svg.onpointermove = e => {
    const r = svg.getBoundingClientRect(), W = ch.X(n - 1) || 1, i = Math.round(Math.min(Math.max((e.clientX - r.left) * ch.W / r.width / W, 0), 1) * (n - 1));
    cross.setAttribute('x1', ch.X(i)); cross.setAttribute('x2', ch.X(i)); cross.setAttribute('opacity', .5); dot.setAttribute('cx', ch.X(i)); dot.setAttribute('opacity', 1); onMove(i, ch.X(i), dot);
  };
  svg.onpointerleave = () => { cross.setAttribute('opacity', 0); dot.setAttribute('opacity', 0); onLeave(); };
}
let detailFor = '', dHover = false;
function drawDetail(p) {
  if (dHover && detailFor === shown) return;  // keep the crosshair still while it is being used
  const q = (last.quotes || {})[shown]; if (!q || !q[2]) return;
  const vals = [...q[2], Number(p.last)], e = Number(p.entry), st = e * (1 - Number(last.stop)), tg = e * (1 + Number(last.target)), svg = $('d-chart'), tip = $('d-tip');
  const ch = chart(svg, $('d-box'), vals, {H: 210, R: 92, color: css(Number(p.last) >= e ? '--up' : '--down'), fresh: detailFor !== shown,
    lines: [{v: tg, label: `Target ${px(tg)}`, color: css('--up')}, {v: e, label: `Entry ${px(e)}`, color: css('--dim')}, {v: st, label: `Stop ${px(st)}`, color: css('--down')}]});
  detailFor = shown;
  scrubber(svg, ch, vals.length, (i, x, dot) => {
    dHover = true; dot.setAttribute('cy', ch.Y(vals[i])); tip.hidden = false; const ago = vals.length - 1 - i;
    tip.textContent = `${px(vals[i])} · ${ago ? ago + ' trading day' + (ago > 1 ? 's' : '') + ' ago' : 'now'}`; tip.style.left = Math.min(Math.max(x - 80, 0), ch.W - 92 - 170) + 'px';
  }, () => { dHover = false; tip.hidden = true; });
}
// Trades that fired since the last check: a toast, the new row pops in, and a sale's P&L flies into the total.
let seenTrade = null;
function toast(t) {
  const sell = t.side === 'SELL', pnl = Number(t.pnl), tone = sell ? (pnl >= 0 ? 'up' : 'down') : '';
  const node = el('div', 'toast ' + tone, logo(t.symbol, 'sm'), el('div', 't-text', el('strong', '', `${sell ? 'Sold' : 'Bought'} ${t.symbol}`), el('span', 'dim', t.reason)), el('b', 'num ' + tone, sell ? signed(pnl) : px(t.price)));
  $('toasts').append(node); setTimeout(() => { node.classList.add('out'); setTimeout(() => node.remove(), 400); }, 7000);
}
function fly(rect, pnl) {
  if (!rect || reduce) return;
  const to = $('gain').getBoundingClientRect(), f = el('b', 'fly num ' + (pnl >= 0 ? 'up' : 'down'), signed(pnl)), x = rect.left + rect.width / 2, y = rect.top + rect.height / 2;
  f.style.left = x + 'px'; f.style.top = y + 'px'; document.body.append(f);
  requestAnimationFrame(() => requestAnimationFrame(() => { f.style.transform = `translate(${to.left + 50 - x}px, ${to.top + to.height / 2 - y}px) scale(.7)`; f.style.opacity = 0; }));
  setTimeout(() => { f.remove(); const g = $('gain'); g.classList.remove('bump'); void g.offsetWidth; g.classList.add('bump'); }, 1000);
}
function moments(s, rects) {
  const fresh = s.trades.filter(t => t.id > (seenTrade ?? Infinity)).reverse();
  seenTrade = Math.max(seenTrade ?? 0, s.trades[0]?.id ?? 0);
  for (const t of fresh) {
    toast(t);
    if (t.side === 'BUY') $('holdings').querySelector(`[data-sym="${t.symbol}"]`)?.classList.add('enter');
    else fly(rects[t.symbol], Number(t.pnl));
  }
}
// Challenger test: the same portfolio run two ways on the same quotes, compared only on what a few months can show.
function challengerView(c) {
  $('chal').hidden = !c || !c.baseline || !c.challenger; if ($('chal').hidden) return;
  const b = c.baseline, h = c.challenger, p = x => `${(x * 100).toFixed(1)}%`, sp = x => `${x >= 0 ? '+' : '−'}${Math.abs(x * 100).toFixed(2)}%`;
  $('ch-sub').textContent = `since ${b.since} · ${b.days} trading day${b.days === 1 ? '' : 's'}`;
  $('ch-protocol').textContent = `Frozen on ${c.protocol.started}: ${c.protocol.text}`;
  $('ch-warn').hidden = !c.rule_changes.length || false;
  $('ch-warn').textContent = `The baseline's rules changed after the test began (${c.rule_changes.map(r => r.text).join('; ')}), so the comparison is no longer like for like.`;
  const rows = [['Return', sp(b.ret), sp(h.ret)], ['Worst drop', p(b.max_drawdown), p(h.max_drawdown)], ['Capital deployed (average)', p(b.deployed), p(h.deployed)],
    ['Days with any position open', p(b.days_with_position), p(h.days_with_position)], ['Open positions (average)', b.avg_positions.toFixed(1), h.avg_positions.toFixed(1)],
    ['Largest position, share of invested', p(b.largest_share), p(h.largest_share)], ['Entries · exits', `${b.entries} · ${b.exits}`, `${h.entries} · ${h.exits}`],
    ['Winning exits', `${b.wins} of ${b.exits}`, `${h.wins} of ${h.exits}`], ['Realized P&L', signed(b.realized), signed(h.realized)]];
  rows.forEach(r => { r[0] = [r[0]]; r[1] = [r[1]]; r[2] = [r[2]]; });
  $('ch-rows').replaceChildren(...rows.map(cells => el('tr', '', ...cells.map(([t]) => el('td', '', t)))));
  $('ch-pos').replaceChildren(...c.positions.map(x => el('span', 'chip', logo(x.symbol, 'sm'), `${x.symbol} · ${px(x.last)} · ${x.gap === null ? '—' : sp(x.gap) + ' above the low of ' + px(x.channel_low)}`)));
  const few = Math.min(b.exits, h.exits) < 30;
  $('ch-note').textContent = (c.error ? c.error + ' ' : '') + (few ? `Only ${b.exits} baseline and ${h.exits} challenger exits so far. That can show the test is running as designed, not which rule is better. ` : '')
    + 'Both start from the same positions. Decisions are recorded with price, channel low and reason in data/decisions-stocks*.jsonl.';
}
// Replay: step through the recorded trading days; the account value, the day's P&L and what was held all move together.
const rp = {days: [], eq: [], trades: [], key: '', i: 0, timer: 0};
function replayData(s) {
  const days = Object.keys(s.daily || {}).sort(), key = JSON.stringify(s.daily || {}) + (s.trades[0]?.id ?? '');
  if (key === rp.key) return; rp.key = key;
  const atEnd = rp.i >= rp.days.length - 1; let c = Number(s.capital);
  rp.days = days; rp.eq = days.map(d => c += Number(s.daily[d])); rp.trades = [...s.trades].reverse(); rp.i = atEnd ? days.length - 1 : rp.i;
  replayDraw();
}
function replayDraw() {
  const {days, eq} = rp, n = days.length; rp.i = Math.max(Math.min(rp.i, n - 1), 0);
  $('rp-note').textContent = n < 2 ? 'Needs two recorded trading days.' : ''; $('rp-play').disabled = n < 2; $('rp-range').max = Math.max(n - 1, 0); $('rp-range').value = rp.i;
  if (!n) return;
  const day = days[rp.i], d = Number(last.daily[day]), held = new Set();
  for (const t of rp.trades) { if (t.time.slice(0, 10) > day) break; t.side === 'BUY' ? held.add(t.symbol) : held.delete(t.symbol); }
  $('rp-date').textContent = day; odo('rp-eq', usd(eq[rp.i])); odo('rp-pnl', signed(d)); tone($('rp-pnl'), d);
  $('rp-pos').replaceChildren(...[...held].map(sym => el('span', 'chip', logo(sym, 'sm'), sym)));
  const svg = $('rp-chart'), cap = Number(last.capital), ch = chart(svg, $('rp-box'), eq, {H: 150, R: 64, color: css('--dim'), lines: [{v: cap, label: 'Start', color: css('--dim')}]});
  const c = css(eq[rp.i] >= cap ? '--up' : '--down');
  svgEl('polyline', {points: eq.slice(0, rp.i + 1).map((v, k) => `${ch.X(k).toFixed(1)},${ch.Y(v).toFixed(1)}`).join(' '), fill: 'none', stroke: c, 'stroke-width': 2.5, 'stroke-linecap': 'round', 'stroke-linejoin': 'round'}, svg);
  svgEl('line', {x1: ch.X(rp.i), x2: ch.X(rp.i), y1: 0, y2: ch.H - 6, stroke: c, opacity: .4}, svg);
  svgEl('circle', {cx: ch.X(rp.i), cy: ch.Y(eq[rp.i]), r: 5, fill: c, stroke: css('--surface'), 'stroke-width': 2}, svg);
}
$('rp-range').oninput = e => { rp.i = Number(e.target.value); replayDraw(); };
$('rp-play').onclick = () => {
  if (rp.timer) { clearInterval(rp.timer); rp.timer = 0; $('rp-play').textContent = 'Play'; return; }
  if (rp.i >= rp.days.length - 1) rp.i = 0;
  $('rp-play').textContent = 'Pause'; replayDraw();
  rp.timer = setInterval(() => { if (rp.i >= rp.days.length - 1) return $('rp-play').onclick(); rp.i++; replayDraw(); }, 750);
};
// Where the price sits between the stop (left) and the target (right); the tick marks the entry.
function track(p) {
  const st = Number(last.stop), tg = Number(last.target), e = Number(p.entry), lo = e * (1 - st), hi = e * (1 + tg), k = Math.min(Math.max((Number(p.last) - lo) / (hi - lo), 0), 1);
  const fill = el('i', 'fill ' + (Number(p.last) >= e ? 'up' : 'down')), mark = el('i', 'mark'), box = el('span', 'track', fill, mark, el('i', 'entry'), el('span', 'tl', `Stop ${px(lo)}`), el('span', 'tr', `Target ${px(hi)}`));
  box.style.setProperty('--e', st / (st + tg)); const set = () => box.style.setProperty('--p', k);
  if (firstPaint && !reduce) { box.style.setProperty('--p', st / (st + tg)); requestAnimationFrame(() => requestAnimationFrame(set)); } else set();
  return box;
}
function holding(sym, p) {
  const gain = Number(p.qty) * Number(p.last) - Number(p.cost), g = Number(p.last) / Number(p.entry) - 1, cls = gain >= 0 ? 'up' : 'down';
  const b = el('button', 'holding', logo(sym),
    el('span', 'h-name', el('strong', '', NAMES[sym] || sym), el('span', 'dim', `${Number(p.qty).toFixed(4)} ${UNIT} · since ${p.opened}`)),
    el('span', 'h-price', flashed(el('strong', 'num', px(p.last)), 'h' + sym, p.last), el('span', 'num ' + cls, icon(gain >= 0 ? 'up' : 'down'), `${signed(gain)} (${pct(g, 2)})`)),
    icon('chevron', 'dim'), track(p));
  b.setAttribute('aria-label', `View details for ${NAMES[sym] || sym}`); b.dataset.sym = sym;
  b.onclick = () => { shown = sym; $('detail').showModal(); detail(); };
  return b;
}

function detail() {
  const p = last?.positions[shown];
  if (!p) return $('detail').open && $('detail').close();
  const s = last, gain = Number(p.qty) * Number(p.last) - Number(p.cost), g = Number(p.last) / Number(p.entry) - 1;
  $('d-logo').replaceWith(Object.assign(logo(shown), {id: 'd-logo'})); $('d-sym').textContent = shown; $('d-name').textContent = NAMES[shown] || '';
  $('d-price').textContent = px(p.last);
  $('d-change').replaceChildren(icon(gain >= 0 ? 'up' : 'down'), ` ${signed(gain)} (${pct(g, 2)}) since entry`); $('d-change').className = 'num ' + (gain >= 0 ? 'up' : 'down');
  const items = [[CRYPTO ? 'Units' : 'Shares', Number(p.qty).toFixed(4)], ['Value', usd(Number(p.qty) * Number(p.last))], ['Entry price', px(p.entry)], ['Cost', usd(p.cost)],
    ['Stop (sell)', px(Number(p.entry) * (1 - Number(s.stop)))], ['Target (sell)', px(Number(p.entry) * (1 + Number(s.target)))], ['Opened', p.opened], ['Last check', s.decisions[shown] || '—']];
  drawDetail(p);
  $('d-list').replaceChildren(...items.map(([k, v]) => el('div', '', el('dt', '', k), el('dd', 'num', v))));
}

async function refresh() {
  try {
    const res = await fetch(`/api/${M}/state`); if (!res.ok) throw Error('cannot read server state');
    const s = last = await res.json(); token = s.token;
    const capital = Number(s.capital), eq = Number(s.equity), m = s.market || {};
    const pos = Object.entries(s.positions), invested = pos.reduce((a, [, p]) => a + Number(p.qty) * Number(p.last), 0);
    const cost = pos.reduce((a, [, p]) => a + Number(p.cost), 0), today = eq - Number(s.day_start), open = invested - cost;
    $('mode').textContent = m.name === 'Crypto' ? `20-day breakout on daily candles · Coinbase · ${m.reason} · UTC ${m.now_utc}` : m.date ? `20-day breakout · NYSE ${m.open ? 'open' : 'closed'}, ${m.reason} · New York ${m.now_new_york}${m.open ? '' : m.next_open ? ` · next open ${m.next_open}` : ''}` : '20-day breakout · no market check yet.';
    UI.clock(CRYPTO ? null : m);
    odo('equity', usd(eq)); $('return').textContent = `${pct(eq / capital - 1, 2)} from ${usd(capital)}`; tone($('return'), eq - capital);
    odo('today', signed(today)); tone($('today'), today);
    $('today-icon').replaceWith(Object.assign(icon(today < 0 ? 'down' : 'up', today > 0 ? 'up' : today < 0 ? 'down' : ''), {id: 'today-icon'}));
    $('limit').textContent = `limit −${usd(s.daily_loss)}`;
    odo('active', String(pos.length)); $('invested').textContent = `${usd(invested)} invested`;
    odo('cash', usd(s.cash)); $('realized').textContent = `realized ${signed(s.realized)}`; tone($('realized'), s.realized);
    const gain = Number(s.realized) + open;
    odo('gain', signed(gain)); tone($('gain'), gain);
    $('gain-pct').replaceChildren(icon(gain < 0 ? 'down' : 'up'), ` ${pct(gain / capital, 2)} return · open ${signed(open)} · realized ${signed(s.realized)}`); tone($('gain-pct'), gain);
    $('asof').textContent = s.updated ? `As of ${new Date(s.updated * 1000).toLocaleString([], {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'})}` : '';
    if (!CRYPTO) {  // account vs the S&P 500 since the close before the first trade
      const acct = (Number(s.equity) - Number(s.capital)) / Number(s.capital), b = s.bench;
      $('b-acct').textContent = s.bench ? pct(acct, 2) : '—'; tone($('b-acct'), acct);
      $('b-spy').textContent = b ? pct(b.spy, 2) : '—'; tone($('b-spy'), b && b.spy);
      $('b-diff').textContent = b ? `${pct(acct - b.spy, 2)} ${acct >= b.spy ? 'ahead' : 'behind'}` : '—'; tone($('b-diff'), b && acct - b.spy);
      $('b-note').textContent = b ? `Since the close before your first trade (${b.since}). SPY is price only, no dividends. Your account holds idle cash until it buys, which is part of the gap.` : 'Appears after the first trade.';
    } else $('bench').hidden = true;
    $('decision').textContent = s.decision;
    $('connection').textContent = s.updated ? `Checked ${new Date(s.updated * 1000).toLocaleTimeString()}` : '';
    const st = $('status'); st.textContent = s.error ? 'Blocked' : s.busy ? 'Checking' : s.running ? `Running · ${s.status}` : 'Paused';
    st.className = s.error ? 'err' : s.running ? 'live' : '';
    const age = s.updated ? Date.now() / 1000 - s.updated : 0, held = Object.keys(s.positions).length;
    const warn = !s.running && held ? `Agent is paused with ${held} open position${held > 1 ? 's' : ''}: stops and targets are NOT being checked. Press Start.`
      : s.running && !s.busy && age > 300 && (CRYPTO || m.open) ? `No check for ${Math.round(age / 60)} minutes while the market is open; stops and targets may not be running.` : '';
    const msg = actionError || s.error || warn; $('error').hidden = !msg; $('error').textContent = msg || '';
    const recent = s.alerts.slice(-3).reverse();
    $('alerts').hidden = !recent.length; $('alerts').textContent = recent.map(a => `🔔 ${a.text} (${new Date(a.at * 1000).toLocaleString()})`).join('  ·  ');
    $('start').disabled = pending || s.running || s.busy; $('pause').disabled = pending || !s.running; $('step').disabled = pending || s.running || s.busy;
    $('edit-rules').disabled = false;
    $('r-per').textContent = Number(s.per_trade) ? `≤ ${usd(s.per_trade)}` : 'No limit (all cash)'; $('r-stop').textContent = pct(-Number(s.stop), 0); $('r-target').textContent = pct(Number(s.target), 0);
    $('r-alert').textContent = `${pct(Number(s.alert_at), 0)} position or account`; $('r-loss').textContent = Number(s.daily_loss) ? usd(s.daily_loss) : 'Off'; $('r-slip').textContent = (Number(s.slippage) * 100).toFixed(2) + '%';
    $('r-trail').textContent = Number(s.trail) ? `−${pct(Number(s.trail), 0).slice(1)} from the high` : 'Off';
    $('r-exitlow').textContent = Number(s.exit_low) ? `Under the ${s.exit_low}-day closing low` : 'Off';
    $('r-be').textContent = Number(s.breakeven) ? `After +${pct(Number(s.breakeven), 0).slice(1)}, stop moves to entry` : 'Off';
    $('r-sector').textContent = Number(s.max_sector) ? `${s.max_sector} per sector` : 'No limit';
    $('r-maxopen').textContent = Number(s.max_open) ? `${s.max_open} at a time` : 'No limit';
    $('r-maxnew').textContent = Number(s.max_new) ? `${s.max_new} a day` : 'No limit';
    $('r-congress').textContent = Number(s.congress_days) ? `Purchase filed in last ${s.congress_days} days` : 'Off';
    const e = s.edge;
    $('edge').textContent = !e ? 'Runs at the first in-session check of each trading day.' : e.error ? `Unavailable: ${e.error}. ${s.gate === false ? 'Gate off.' : 'Entries blocked.'}`
      : `${e.first} to ${e.last}: ${e.entries} entries, ${e.exits} exits (${e.wins} wins), ${e.open} open. Total ${signed(e.total)}. ${s.gate === false ? 'Gate off (aggressive mode): entries allowed regardless.' : Number(e.total) > 0 && Number(e.entries) >= 5 ? 'Passed the basic check: it did not lose money. That is not proof the strategy works.' : 'Entries blocked.'}`
      + (e.buy_hold === undefined ? '' : ` Just holding the same stocks instead: ${signed(e.buy_hold)}. Worst drop ${(Number(e.max_drawdown) * 100).toFixed(1)}%, invested ${(Number(e.exposure) * 100).toFixed(0)}% of the time. First half ${signed(e.half1)}, second half ${signed(e.half2)}.`)
      + ' Watchlist picked with hindsight, so this is biased upward; the replay decides once a day at the close, while the live agent checks every minute.';
    $('noPos').hidden = pos.length > 0;
    const rects = {}; $('holdings').querySelectorAll('.holding').forEach(b => { rects[b.dataset.sym] = b.getBoundingClientRect(); });
    $('holdings').replaceChildren(...pos.map(([sym, p], i) => intro(holding(sym, p), i)));
    moments(s, rects);
    if ($('detail').open) detail();
    $('watch').replaceChildren(...s.symbols.map((sym, i) => {
      const d = s.decisions[sym] || '—', [, label, cls, why] = BADGES.find(([re]) => re.test(d)) || [0, 'No signal', 'none', 'No breakout today.'];
      const [price, prev, closes] = (s.quotes || {})[sym] || [], ch = price ? Number(price) / Number(prev) - 1 : null;
      const move = ch === null ? el('span', 'dim', '—') : el('span', 'num ' + (ch >= 0 ? 'up' : 'down'), icon(ch >= 0 ? 'up' : 'down'), pct(ch, 2));
      const wipe = closes?.length > 1 && !wiped.has(sym) && !reduce && wiped.add(sym);
      return intro(el('li', 'ticker', logo(sym),
        el('span', 't-name', el('strong', '', sym), el('span', 'dim', label === 'No signal' ? NAMES[sym] || '' : `${NAMES[sym] || ''} · ${d}`)),
        closes?.length > 1 ? mini(closes, price, wipe) : el('span', 'mini'),
        el('span', 't-price', flashed(el('strong', 'num', price ? px(price) : '—'), 'w' + sym, price || 0), move),
        Object.assign(el('span', 'badge ' + cls, label), {title: why})), i);
    }));
    $('count').textContent = s.trades.length ? `${s.trades.length}${s.more ? '+' : ''} trades` : '';
    $('empty').hidden = s.trades.length > 0;
    rows($('trades'), s.trades.map(t => [[new Date(t.time).toLocaleString([], {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'})],
      [el('span', 'sym', logo(t.symbol, 'sm'), t.symbol), 'side'], [t.side, t.side === 'SELL' ? 'down' : 'up'], [Number(t.qty).toFixed(4)], [px(t.price)],
      [t.side === 'SELL' ? signed(t.pnl) : '', t.side === 'SELL' ? (Number(t.pnl) >= 0 ? 'up' : 'down') : ''], [t.reason, 'reason']]));
    tape(s); ring(pos, Number(s.cash), eq);
    const log = (s.rule_log || []).slice(-8).reverse(); $('rl-empty').hidden = log.length > 0;
    $('rl-list').replaceChildren(...log.map(r => el('li', '', `${new Date(r.at * 1000).toLocaleString([], {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'})} · ${r.text}`)));
    if (!hovering) hero(s.history, capital);
    pnlMap(s.daily || {});
    replayData(s); challengerView(s.challenger);
    firstPaint = false;
  } catch (e) {
    $('error').hidden = false; $('error').textContent = `Dashboard disconnected (${e.message}). Values shown may be stale.`;
    $('status').textContent = 'Disconnected'; $('status').className = 'err';
    ['start', 'pause', 'step'].forEach(id => $(id).disabled = true);
  }
}

$('detail').addEventListener('close', () => { detailFor = ''; dHover = false; });
$('d-close').onclick = () => $('detail').close();
$('detail').onclick = e => e.target === $('detail') && $('detail').close();  // click on the backdrop
for (const id of ['start', 'pause', 'step']) $(id).addEventListener('click', async () => {
  pending = true; $(id).disabled = true;
  try { const res = await fetch(`/api/${M}/` + id, {method: 'POST', headers: {'X-App-Token': token}}); if (!res.ok) throw Error(await res.text()); actionError = ''; }
  catch (e) { actionError = `${$(id).textContent} failed: ${e.message}`; }
  finally { pending = false; await refresh(); }
});
// Rules editor: percentages in the form, fractions on the server.
const PCT = ['stop', 'target', 'alert_at', 'trail', 'breakeven'], MONEY = ['per_trade', 'daily_loss'];
$('edit-rules').onclick = () => {
  for (const k of PCT) $('f-' + k).value = +(Number(last[k]) * 100).toFixed(4);
  for (const k of MONEY) $('f-' + k).value = Number(last[k]);
  $('f-max_new').value = Number(last.max_new || 0); $('f-max_sector').value = Number(last.max_sector || 0); $('f-exit_low').value = Number(last.exit_low || 0); $('f-congress_days').value = Number(last.congress_days || 0); $('f-max_open').value = Number(last.max_open || 0);
  $('f-symbols').value = last.symbols.join(' '); $('f-gate').checked = last.gate; $('rules-error').hidden = true;
  $('rules').showModal();
};
$('rules-cancel').onclick = () => $('rules').close();
$('rules-form').addEventListener('submit', async e => {
  e.preventDefault();
  const body = {symbols: $('f-symbols').value.split(/[\s,]+/).filter(Boolean), gate: $('f-gate').checked};
  for (const k of PCT) body[k] = String(Number($('f-' + k).value) / 100);
  for (const k of MONEY) body[k] = $('f-' + k).value || '0';
  body.max_open = $('f-max_open').value || '0'; body.max_new = $('f-max_new').value || '0'; body.max_sector = $('f-max_sector').value || '0'; body.exit_low = $('f-exit_low').value || '0';
  if (!CRYPTO) body.congress_days = $('f-congress_days').value || '0';
  $('rules-save').disabled = true;
  try {
    const res = await fetch(`/api/${M}/rules`, {method: 'POST', headers: {'X-App-Token': token, 'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    if (!res.ok) throw Error(await res.text());
    $('rules').close(); await refresh();
  } catch (err) { $('rules-error').textContent = err.message; $('rules-error').hidden = false; }
  finally { $('rules-save').disabled = false; }
});
addEventListener('themechange', () => { if (last) refresh(); });  // charts read colors when drawn; the toggle lives in the shared sidebar (ui.js)
// Daily P&L calendar: one block per calendar year (Jan 1 to Dec 31, so it never stops mid-year), one square per day, green for a profitable
// day and red for a losing one, shaded by size against the biggest day. Account-value change per day, so it is the
// permanent record. It is the day's change in account value (open positions included), recorded by the server each check. Days that have not happened yet are faded.
const DAY_MS = 86400000, DAY_FMT = new Intl.DateTimeFormat('en-US', {weekday: 'short', month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC'});
const MONTH_FMT = new Intl.DateTimeFormat('en-US', {month: 'short', timeZone: 'UTC'});
let pmapKey = '', pmapDays = [], pmapFocus = -1;
function pnlMap(daily) {
  const today = new Date().toISOString().slice(0, 10), key = today + JSON.stringify(daily);
  if (key === pmapKey) return;
  pmapKey = key;
  const year = Number(today.slice(0, 4)), first = Math.min(year, ...Object.keys(daily).map(k => Number(k.slice(0, 4))));
  const years = [];
  pmapDays = [];
  for (let y = year; y >= first; y--) {  // newest year on top; days are indexed oldest to newest
    const start = Date.UTC(y, 0, 1), n = (Date.UTC(y + 1, 0, 1) - start) / DAY_MS;
    years.push({y, start, n, lead: new Date(start).getUTCDay(), base: 0});
  }
  [...years].reverse().forEach(yr => {
    yr.base = pmapDays.length;
    for (let i = 0; i < yr.n; i++) { const iso = new Date(yr.start + i * DAY_MS).toISOString().slice(0, 10); pmapDays.push({iso, pnl: iso in daily ? Number(daily[iso]) : null, future: iso > today}); }
  });
  if (pmapFocus < 0 || pmapFocus >= pmapDays.length) pmapFocus = pmapDays.findIndex(d => d.iso === today);
  const max = Math.max(0, ...pmapDays.map(d => Math.abs(d.pnl ?? 0)));
  const level = p => !p || !max ? 0 : Math.min(4, Math.ceil(Math.abs(p) / max * 4));
  const place = (node, col, row) => { node.style.gridColumn = col; node.style.gridRow = row; return node; };
  let wins = 0, losses = 0, total = 0;
  const blocks = years.map(({y, start, n, lead, base}) => {
    const kids = [];
    for (let i = 0; i < n; i++) if (pmapDays[base + i].iso.endsWith('-01')) kids.push(place(el('span', 'pmonth', MONTH_FMT.format(new Date(start + i * DAY_MS))), Math.floor((lead + i) / 7) + 2, 1));
    [[1, 'Mon'], [3, 'Wed'], [5, 'Fri']].forEach(([row, name]) => kids.push(place(el('span', 'pwday', name), 1, row + 2)));
    let yearTotal = 0;
    for (let i = 0; i < n; i++) {
      const d = pmapDays[base + i], g = base + i, cell = place(el('span', 'pcell'), Math.floor((lead + i) / 7) + 2, (lead + i) % 7 + 2), l = level(d.pnl);
      if (d.pnl) { d.pnl > 0 ? wins++ : losses++; total += d.pnl; yearTotal += d.pnl; }
      cell.dataset.i = g; cell.dataset.level = l; if (l) cell.dataset.tone = d.pnl > 0 ? 'up' : 'down'; if (d.future) cell.dataset.future = '';
      cell.style.setProperty('--w', Math.floor((lead + i) / 7) + (lead + i) % 7);
      cell.tabIndex = g === pmapFocus ? 0 : -1; cell.setAttribute('role', 'img');
      cell.setAttribute('aria-label', `${d.future ? 'Upcoming' : d.pnl === null ? 'No data' : signed(d.pnl)}, ${DAY_FMT.format(new Date(Date.parse(d.iso + 'T00:00:00Z')))}`);
      kids.push(cell);
    }
    const head = el('div', 'pyear-h', el('strong', '', String(y)), yearTotal ? el('span', 'num ' + (yearTotal > 0 ? 'up' : 'down'), signed(yearTotal)) : '');
    const grid = el('div', 'pgrid'); grid.style.setProperty('--weeks', Math.ceil((lead + n) / 7)); grid.replaceChildren(...kids);
    return el('div', 'pyear', head, grid);
  });
  $('pmap-grid').replaceChildren(...blocks);
  $('pmap-sum').textContent = wins + losses ? `${signed(total)} total · ${wins} green ${wins === 1 ? 'day' : 'days'} · ${losses} red` : 'No days recorded yet';
  tone($('pmap-sum'), total);
}
function pnlTip(cell) {
  const tip = $('pmap-tip'), d = cell && pmapDays[Number(cell.dataset.i)];
  tip.classList.toggle('on', !!d); if (!d) return;
  const card = $('pmap').getBoundingClientRect(), box = cell.getBoundingClientRect();
  tip.replaceChildren(el('strong', 'num', d.future ? 'Upcoming' : d.pnl === null ? 'No data' : signed(d.pnl)), DAY_FMT.format(new Date(Date.parse(d.iso + 'T00:00:00Z'))));
  tip.style.left = Math.min(Math.max(box.left - card.left + box.width / 2, 70), card.width - 70) + 'px'; tip.style.top = box.top - card.top + 'px';
}
{
  const grid = $('pmap-grid'), cellOf = e => e.target.closest?.('.pcell[data-i]');
  grid.addEventListener('pointerover', e => cellOf(e) && pnlTip(cellOf(e)));
  grid.addEventListener('pointerleave', () => { const f = document.activeElement; pnlTip(f?.matches?.('.pcell:focus-visible') ? f : null); });
  grid.addEventListener('focusin', e => { const c = cellOf(e); if (c) { pmapFocus = Number(c.dataset.i); pnlTip(c); } });
  grid.addEventListener('focusout', e => grid.contains(e.relatedTarget) || pnlTip(null));
  grid.addEventListener('keydown', e => {
    const c = cellOf(e); if (!c) return;
    const i = Number(c.dataset.i), moves = {ArrowUp: i - 1, ArrowDown: i + 1, ArrowLeft: i - 7, ArrowRight: i + 7, Home: 0, End: pmapDays.length - 1};
    if (e.key === 'Escape') return pnlTip(null);
    if (!(e.key in moves)) return;
    e.preventDefault(); grid.querySelector(`[data-i="${Math.min(Math.max(moves[e.key], 0), pmapDays.length - 1)}"]`)?.focus();
  });
}
// Congress trades from the House Clerk's public filings (free, read-only, stocks page only). Filings trail the trades by days to weeks.
// Congress page: what the filings are, what they add up to, what they mean for the stocks you hold, then the filings themselves.
const cg = {all: [], filter: 'all', symbol: '', shown: 20, first: true};
const OWNER = {spouse: 'Spouse', joint: 'Joint', dc: 'Child', 'dependent child': 'Child'};
function member(who) {
  const m = /\(([^)]+)\)\s*$/.exec(who), name = who.replace(/\s*\([^)]*\)\s*$/, '').replace(/^Hon\.\s*/, '').replace(/\b(Mr|Mrs|Ms|Dr)\.?\s+/, '');
  return {name, owner: m ? OWNER[m[1].toLowerCase()] || m[1] : ''};
}
const kind = side => /^purchase/i.test(side) ? 'buy' : /sale/i.test(side) ? 'sell' : 'other';
const ACTION = {buy: 'Bought', sell: 'Sold', other: 'Exchanged'};
const dayDiff = (a, b) => Math.round((Date.parse(b) - Date.parse(a)) / 864e5);
const ago = d => { const n = dayDiff(d, new Date().toISOString().slice(0, 10)); return n <= 0 ? 'today' : n === 1 ? 'yesterday' : `${n} days ago`; };
function congressView() {
  const held = new Set(Object.keys(last?.positions || {})), all = cg.all, buys = all.filter(t => kind(t.side) === 'buy'), sells = all.filter(t => kind(t.side) === 'sell');
  const days = cg.days || 60, rule = Number(last?.congress_days || 0);
  $('cg-rule').textContent = rule ? `Entry filter is ON: a breakout is only bought if a member filed a purchase of that stock in the last ${rule} days.`
    : 'Entry filter is OFF, so none of this changes what the bot buys. You can turn it on in Edit rules → Congress filter.';
  odo('cg-buys', String(buys.length)); odo('cg-sells', String(sells.length)); odo('cg-held-n', String(new Set(all.map(t => t.symbol).filter(s => held.has(s))).size));
  $('cg-bar-buy').style.width = all.length ? Math.round(buys.length / (buys.length + sells.length || 1) * 100) + '%' : '0%';
  $('cg-bar-note').textContent = !buys.length && !sells.length ? '' : buys.length === sells.length ? 'Equal buying and selling.'
    : `${Math.round(Math.max(buys.length, sells.length) / (buys.length + sells.length) * 100)}% of these filings are ${buys.length > sells.length ? 'purchases' : 'sales'}.`;
  const tally = {}; for (const t of all) tally[t.symbol] = (tally[t.symbol] || 0) + 1;
  const top = Object.entries(tally).sort((a, b) => b[1] - a[1]).slice(0, 6);
  $('cg-top').replaceChildren(...top.map(([sym, n]) => { const b = el('button', 'chip pick', logo(sym, 'sm'), `${sym} · ${n}`); b.setAttribute('aria-pressed', cg.symbol === sym); b.onclick = () => { cg.symbol = cg.symbol === sym ? '' : sym; cg.shown = 20; congressView(); }; return b; }));
  const mine = [...held].map(sym => {
    const r = all.filter(t => t.symbol === sym), b = r.filter(t => kind(t.side) === 'buy').length, s = r.filter(t => kind(t.side) === 'sell').length;
    return {sym, b, s, latest: r[0]?.date};
  }).filter(x => x.b + x.s);
  $('cg-held').replaceChildren(...mine.map(x => el('li', 'cg-mine', logo(x.sym, 'sm'),
    el('span', '', el('strong', '', x.sym), el('span', 'dim', ` · ${x.b} bought, ${x.s} sold by members · latest filing ${ago(x.latest)}`)),
    el('span', 'badge ' + (x.s > x.b ? 'down' : x.b > x.s ? 'up' : ''), x.s > x.b ? 'More selling' : x.b > x.s ? 'More buying' : 'Mixed'))));
  $('cg-held-none').textContent = mine.length ? 'Counts are filings in the last ' + days + ' days. Many members trade for reasons unrelated to the stock, so treat this as context only.'
    : held.size ? `None of the ${held.size} stocks you hold appear in filings from the last ${days} days.` : 'You hold nothing right now.';
  const view = all.filter(t => (cg.filter === 'all' || (cg.filter === 'held' ? held.has(t.symbol) : kind(t.side) === cg.filter)) && (!cg.symbol || t.symbol === cg.symbol));
  document.querySelectorAll('#cg-filter button').forEach(b => b.setAttribute('aria-pressed', b.dataset.f === cg.filter));
  $('cg-clear').hidden = !cg.symbol; $('cg-clear').textContent = `Showing ${cg.symbol} only · clear`;
  rows($('q-rows'), view.slice(0, cg.shown).map((t, i) => { const m = member(t.who), k = kind(t.side), lag = t.traded ? dayDiff(t.traded, t.date) : null;
    return [[el('span', '', t.date, el('span', 'dim sub block', ago(t.date)))],
      [el('span', 'sym', logo(t.symbol, 'sm'), t.symbol, held.has(t.symbol) ? el('span', 'badge up', 'You hold') : ''), 'side'],
      [el('span', '', m.name, m.owner ? el('span', 'owner', m.owner) : ''), 'name'],
      [el('span', 'act ' + k, k === 'sell' && /partial/i.test(t.side) ? 'Partly sold' : ACTION[k])],
      [el('span', 'num', t.range)],
      [lag === null ? '—' : el('span', 'num' + (lag > 45 ? ' late' : ''), `${lag} day${lag === 1 ? '' : 's'}`)]]; }));
  $('q-rows').closest('.wrap').hidden = !view.length;
  $('cg-more').hidden = view.length <= cg.shown; $('cg-more').textContent = `Show ${Math.min(20, view.length - cg.shown)} more of ${view.length - cg.shown}`;
  $('cg-count').textContent = view.length === all.length ? `${all.length} filings` : `${view.length} of ${all.length}`;
  $('q-empty').hidden = view.length > 0 || !cg.ready;
  $('q-empty').textContent = all.length ? 'Nothing matches this filter.' : `No filings for your watchlist in the last ${days} days.`;
}
document.querySelectorAll('#cg-filter button').forEach(b => b.onclick = () => { cg.filter = b.dataset.f; cg.shown = 20; congressView(); });
$('cg-clear').onclick = () => { cg.symbol = ''; congressView(); };
$('cg-more').onclick = () => { cg.shown += 20; congressView(); };
async function congress() {
  if (CRYPTO || PAGE !== 'congress') return;
  try {
    const q = await (await fetch('/api/congress')).json();
    cg.all = q.trades || []; cg.days = q.days; cg.ready = q.configured;
    $('cg-status').textContent = !q.configured ? 'The House filing reader (trader-pelosi skill) was not found, so there is nothing to show.' : q.error ? `${q.error}. Showing what was saved earlier.`
      : q.busy ? 'Reading new filings from the House Clerk…' : `${q.filings} filings read · last checked ${q.checked ? new Date(q.checked * 1000).toLocaleTimeString() : 'just now'} · House only (Senate filings are not machine readable).`;
    congressView();
  } catch { $('cg-status').textContent = 'Congress trades unavailable right now.'; }
}
congress(); setInterval(congress, 30000);
(async function poll() { await refresh(); setTimeout(poll, 2000); })();
