// Shared shell for every market page: the icon sprite, the sidebar (markets, market clock, theme), number formatting and small DOM helpers.
// Loaded before each page's own script; everything it exports hangs off `UI`.
const UI = (() => {
  const $ = id => document.getElementById(id);
  const el = (tag, cls, ...kids) => { const e = document.createElement(tag); if (cls) e.className = cls; e.append(...kids.filter(k => k != null)); return e; };
  const n2 = (x, d = 2) => Number(x).toLocaleString('en-US', {minimumFractionDigits: d, maximumFractionDigits: d});
  const usd = x => '$' + n2(x);
  const signed = x => (Number(x) > 0 ? '+' : Number(x) < 0 ? '−' : '') + usd(Math.abs(Number(x)));
  const pct = (x, d = 1) => x == null || !isFinite(x) ? '—' : (x > 0 ? '+' : x < 0 ? '−' : '') + Math.abs(x * 100).toFixed(d) + '%';
  const tone = (e, x) => { e.classList.toggle('up', Number(x) > 0); e.classList.toggle('down', Number(x) < 0); return e; };
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const when = (t, o = {}) => new Date(t * 1000).toLocaleString([], {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', ...o});

  const SPRITE = `<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
<symbol id="i-dollar" viewBox="0 0 24 24"><path d="M12 2v20"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></symbol>
<symbol id="i-wallet" viewBox="0 0 24 24"><path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4Z"/></symbol>
<symbol id="i-up" viewBox="0 0 24 24"><path d="M22 7 13.5 15.5l-5-5L2 17"/><path d="M16 7h6v6"/></symbol>
<symbol id="i-down" viewBox="0 0 24 24"><path d="M22 17 13.5 8.5l-5 5L2 7"/><path d="M16 17h6v-6"/></symbol>
<symbol id="i-bars" viewBox="0 0 24 24"><path d="M3 3v18h18"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/></symbol>
<symbol id="i-activity" viewBox="0 0 24 24"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></symbol>
<symbol id="i-chevron" viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"/></symbol>
<symbol id="i-sun" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></symbol>
<symbol id="i-moon" viewBox="0 0 24 24"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"/></symbol>
<symbol id="i-x" viewBox="0 0 24 24"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></symbol>
<symbol id="n-home" viewBox="0 0 24 24"><path d="M3.5 11.2 12 4l8.5 7.2"/><path d="M6 9.6V20h12V9.6"/></symbol>
<symbol id="n-list" viewBox="0 0 24 24"><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/></symbol>
<symbol id="n-cal" viewBox="0 0 24 24"><rect x="3" y="4.5" width="18" height="16" rx="2"/><path d="M3 10h18M8 2.5v4M16 2.5v4"/></symbol>
<symbol id="n-bank" viewBox="0 0 24 24"><path d="M3 10 12 4l9 6M5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 20h18"/></symbol>
<symbol id="n-stocks" viewBox="0 0 24 24"><path d="M3 17l6-6 4 4 8-8"/><path d="M14 7h7v7"/></symbol>
<symbol id="n-crypto" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M9.5 7.5h3.8a2 2 0 0 1 0 4H9.5h4.3a2 2 0 0 1 0 4H9.5M11 5.5v2M11 15.5v2"/></symbol>
<symbol id="n-day" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></symbol>
<symbol id="n-funds" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-9-9"/><path d="M12 3a9 9 0 0 1 9 9h-9z"/></symbol>
<symbol id="n-desk" viewBox="0 0 24 24"><path d="M9 6h12M9 12h12M9 18h12"/><path d="m3 6 1.5 1.5L7 5M3 12l1.5 1.5L7 11M3 18l1.5 1.5L7 17"/></symbol>
</defs></svg>
<svg width="0" height="0" style="position:absolute" aria-hidden="true"><filter id="goo" x="-20%" y="-60%" width="140%" height="220%" color-interpolation-filters="sRGB"><feGaussianBlur in="SourceGraphic" stdDeviation="5"/><feColorMatrix values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 20 -8"/></filter></svg>`;

  const MARKETS = [['desk', 'Action needed', 'n-desk'], ['stocks', 'US stocks', 'n-stocks'], ['crypto', 'Crypto', 'n-crypto'], ['day', 'Day trades', 'n-day'], ['funds', 'Funds', 'n-funds']];
  function shell(market) {
    document.body.insertAdjacentHTML('afterbegin', SPRITE);
    const nav = el('nav', 'side-nav'); nav.setAttribute('aria-label', 'Markets'); nav.append(el('span', 'side-h', 'Markets'));
    for (const [id, name, icon] of MARKETS) {
      const a = el('a', '', svgIcon(icon), name); a.href = '/' + id; a.id = 'nav-' + id; if (id === market) a.setAttribute('aria-current', 'page'); nav.append(a);
    }
    const brand = el('a', 'brand'); brand.href = '/stocks';
    brand.insertAdjacentHTML('afterbegin', '<svg class="mark" viewBox="0 0 24 24" aria-hidden="true"><rect width="24" height="24" rx="6" fill="currentColor"/><path d="M6 15.5l4-4 3 3 5-6" fill="none" stroke="var(--bg)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>');
    brand.append(el('span', '', 'Signal Desk'), el('span', 'tag', 'Paper'));
    const clock = el('div', 'clock', el('i'), el('b', '', 'NYSE'), el('span', '', '')); clock.id = 'clock';
    const theme = el('button', 'theme-btn', svgIcon('i-sun', 'sun'), svgIcon('i-moon', 'moon')); theme.id = 'theme'; theme.type = 'button';
    const side = el('aside', 'sidebar', brand, nav, el('div', 'side-foot', clock, theme));
    document.querySelector('.app').prepend(side);
    theme.onclick = () => { const mode = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light'; try { localStorage.setItem('mode', mode); } catch {} setTheme(mode); };
    let saved = null; try { saved = localStorage.getItem('mode'); } catch {}
    setTheme(saved === 'light' ? 'light' : 'dark');
    tickClock(); setInterval(tickClock, 15000);
  }
  function svgIcon(name, cls = '') {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'), use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    svg.setAttribute('class', 'i ' + cls); svg.setAttribute('aria-hidden', 'true'); use.setAttribute('href', '#' + name); svg.append(use); return svg;
  }
  function setTheme(mode) {
    document.documentElement.dataset.theme = mode;
    const b = $('theme'); if (b) { b.setAttribute('aria-label', `Switch to ${mode === 'light' ? 'dark' : 'light'} mode`); b.title = b.getAttribute('aria-label'); }
    dispatchEvent(new Event('themechange'));
  }
  // Market clock: New York time is computed here; open/closed comes from the last server status (holidays, early closes) and falls back to the regular hours.
  let status = null;
  function clock(m) { status = m || status; tickClock(); }
  function tickClock() {
    const c = $('clock'); if (!c) return;
    const p = Object.fromEntries(new Intl.DateTimeFormat('en-US', {timeZone: 'America/New_York', weekday: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'}).formatToParts(new Date()).map(x => [x.type, x.value]));
    const hm = `${p.hour}:${p.minute}`, mins = Number(p.hour) * 60 + Number(p.minute), weekday = !['Sat', 'Sun'].includes(p.weekday);
    const open = status && 'open' in status ? status.open : weekday && mins >= 570 && mins < 960;
    c.classList.toggle('open', !!open); c.children[1].textContent = open ? 'NYSE open' : 'NYSE closed';
    c.children[2].textContent = `${hm} New York${!open && status?.next_open ? ` · opens ${status.next_open.slice(5)}` : ''}`;
  }
  // Sparkline: a thin line and an end dot; the title carries the number.
  function spark(points, change, w = 96, h = 28) {
    const NS = 'http://www.w3.org/2000/svg', svg = document.createElementNS(NS, 'svg'); svg.setAttribute('class', 'mini ' + (change > 0 ? 'up' : change < 0 ? 'down' : ''));
    svg.setAttribute('viewBox', `0 0 ${w} ${h}`); svg.setAttribute('width', w); svg.setAttribute('height', h); svg.setAttribute('role', 'img');
    const t = document.createElementNS(NS, 'title'); t.textContent = points.length > 1 ? `Change over the period: ${pct(change)}` : 'Not enough history yet'; svg.append(t);
    if (points.length < 2) return svg;
    const lo = Math.min(...points), hi = Math.max(...points), y = v => hi === lo ? h / 2 : h - 3 - (v - lo) / (hi - lo) * (h - 6), x = i => 2 + i / (points.length - 1) * (w - 4);
    const line = document.createElementNS(NS, 'polyline'); line.setAttribute('points', points.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' '));
    const dot = document.createElementNS(NS, 'circle'); dot.setAttribute('cx', x(points.length - 1)); dot.setAttribute('cy', y(points[points.length - 1])); dot.setAttribute('r', 2.5); dot.setAttribute('fill', 'currentColor');
    svg.append(line, dot); return svg;
  }
  const avatar = (text, cls = '') => el('span', 'avatar ' + cls, text.slice(0, 2).toUpperCase());
  function controls(prefix, state, pending) {
    const s = state;
    $('start').disabled = pending || s.running || s.busy; $('pause').disabled = pending || !s.running; $('step').disabled = pending || s.running || s.busy;
    const st = $('status'); st.textContent = s.error ? 'Blocked' : s.busy ? 'Checking' : s.running ? `Running · ${s.status}` : 'Paused'; st.className = s.error ? 'err' : s.running ? 'live' : '';
  }
  return {$, el, n2, usd, signed, pct, tone, reduce, when, shell, icon: svgIcon, clock, spark, avatar, controls};
})();
