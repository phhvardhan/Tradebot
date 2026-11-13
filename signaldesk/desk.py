"""The decision desk: one real brokerage account imported read-only, and the "Action needed" view that applies the swing rules you chose to what you actually
own and to today's signals. Nothing here places an order: you decide, place it with your broker, then re-import. Holdings, cash and fills live in
data/holdings.json, apart from every paper portfolio. An investment and a swing trade can share a ticker: swing exits apply only to lots tagged swing.

  python3 -m signaldesk.desk import FILE.csv [--account NAME]       Robinhood "account activity" CSV: Buy/Sell rows become fills, netted into positions (cash is not in the file)
  python3 -m signaldesk.desk tag SYM swing --entry E --stop S --target T [--opened YYYY-MM-DD]
  python3 -m signaldesk.desk tag SYM invest
  python3 -m signaldesk.desk sync                                     Alpaca, read-only: positions, cash and fills (keys in .env; the server also syncs every 5 minutes)
  python3 -m signaldesk.desk show
"""
import argparse
import csv
import json
import os
import re
import threading
import time
from datetime import date, datetime, timezone
from decimal import InvalidOperation, ROUND_DOWN
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .core import D, ROOT, secret
from .quotes import scan
from .swing import SECTOR, STALE

HOLDINGS = ROOT / 'data' / 'holdings.json'
SYNC_EVERY = 5 * 60    # the server re-reads the broker this often when Alpaca keys are set
SYNC_OLD = 24 * 3600   # a snapshot older than this is flagged: trades since then are unknown here
QUOTE_OLD = STALE + 120  # in session, a desk card on a quote older than this fails: the agent checks every minute, so it means the feed stopped

def load(path=HOLDINGS):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None

def save(h, path=HOLDINGS):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + '.tmp')
    tmp.write_text(json.dumps(h, indent=1))
    tmp.replace(path)

def snapshot(account, cash, positions, fills, source, previous=None, notes=(), synced_at=None):
    """The holdings record; positions are {symbol, qty, avg_cost, since}. A symbol's purpose and swing plan survive a re-import only while it is the same
    holding: if it was sold out and bought again (a new `since`), the old tag and its stop and target are dropped and it starts as an investment.
    `synced_at` is when the data was exported (the CSV's file time), not when it was imported."""
    tagged = {p['symbol']: p for p in (previous or {}).get('positions', [])}
    notes, rows = list(notes), []
    for p in positions:
        old, since = tagged.get(p['symbol'], {}), p.get('since')
        same = not old or not since or not old.get('since') or old['since'] == since
        if not same and (old.get('purpose') == 'swing' or old.get('plan')):
            notes.append(f'{p["symbol"]}: sold out and bought again on {since}, so its old swing tag and plan were dropped. Tag it again if this lot is a swing trade.')
        rows.append(dict(symbol=p['symbol'], qty=str(p['qty']), avg_cost=None if p.get('avg_cost') is None else str(p['avg_cost']), since=since,
                         purpose=old.get('purpose', 'invest') if same else 'invest', plan=old.get('plan') if same else None))
    return dict(account=account, source=source, synced_at=synced_at or time.time(), cash=None if cash is None else str(cash), positions=sorted(rows, key=lambda p: p['symbol']), fills=list(fills), notes=notes)

def iso(text):
    for fmt in ('%Y-%m-%d', '%m/%d/%Y'):
        try:
            return datetime.strptime(text.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f'Unreadable date: {text!r}')

def read_csv(path):
    """Robinhood account-activity export: Buy and Sell rows are fills; everything else (dividends, transfers) is skipped.
    Returns (fills oldest first, positions they net to, notes). Average cost follows the broker's way: a sale removes its share of the cost, not the price.
    Robinhood lists the newest row first; rows within one day keep the file's order, reversed, so a same-day buy then sell replays in that order."""
    fills = []
    with open(path, newline='', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            code = (r.get('Trans Code') or '').strip()
            if code not in ('Buy', 'Sell'):
                continue
            try:
                qty, price = D(r['Quantity'].replace(',', '').strip()), D(r['Price'].replace('$', '').replace(',', '').strip())
            except (KeyError, InvalidOperation, AttributeError):
                raise ValueError(f'Unreadable row: {r}') from None
            if qty <= 0 or price <= 0:
                raise ValueError(f'Unreadable row: {r}')
            fills.append(dict(at=iso(r['Activity Date']), symbol=r['Instrument'].strip().upper(), side=code.upper(), qty=str(qty), price=str(price)))
    if fills and fills[0]['at'] >= fills[-1]['at']:
        fills.reverse()  # newest first (Robinhood), or a single day: same-day rows are then oldest first too
    fills.sort(key=lambda x: x['at'])  # stable: an oldest-first file keeps its own same-day order
    return (fills, *net_fills(fills))

def net_fills(fills):
    """Fills oldest first -> (positions {symbol, qty, avg_cost, since}, notes). `since` is the first buy after the symbol was last flat."""
    net, notes, flagged = {}, [], set()
    for x in fills:
        p = net.setdefault(x['symbol'], dict(qty=D(0), cost=D(0), since=None))
        q, px = D(x['qty']), D(x['price'])
        if x['side'] == 'BUY':
            if p['qty'] <= 0:
                p['since'] = x['at']  # a new holding starts: flat (or short of history) before this buy
            p['qty'] += q; p['cost'] += q * px
        else:
            if q > p['qty'] and x['symbol'] not in flagged:
                flagged.add(x['symbol'])
                notes.append(f'{x["symbol"]}: the file sells more than it bought, so it does not start at the account opening; its position and cost are incomplete.')
            p['cost'] -= p['cost'] * min(q / p['qty'], D(1)) if p['qty'] > 0 else D(0)
            p['qty'] -= q
    positions = [dict(symbol=s, qty=p['qty'], avg_cost=(p['cost'] / p['qty']).quantize(D('.0001')), since=p['since']) for s, p in net.items() if p['qty'] > 0]
    return positions, notes

ALPACA = {'paper': 'https://paper-api.alpaca.markets', 'live': 'https://api.alpaca.markets'}

def alpaca_get(path, params=None):
    """One read from Alpaca's trading API. GET only: nothing here can place, change or cancel an order."""
    key, sec = secret('ALPACA_KEY_ID'), secret('ALPACA_SECRET_KEY')
    if not key or not sec:
        raise ValueError('Alpaca keys are not set: put ALPACA_KEY_ID and ALPACA_SECRET_KEY in .env')
    url = ALPACA['live' if secret('ALPACA_LIVE') == '1' else 'paper'] + path + ('?' + urlencode(params) if params else '')
    with urlopen(Request(url, headers={'APCA-API-KEY-ID': key, 'APCA-API-SECRET-KEY': sec, 'Accept': 'application/json', 'User-Agent': 'signaldesk'}), timeout=15) as r:
        return json.load(r)

def read_alpaca(get=alpaca_get):
    """(account label, cash, positions, fills oldest first, notes) from Alpaca. The broker's positions and average cost are the truth;
    the fill history only says when each holding started (for tags) and is kept as the record."""
    acct, held, fills, token = get('/v2/account'), get('/v2/positions'), [], None
    while True:
        page = get('/v2/account/activities/FILL', dict(direction='desc', page_size=100, **({'page_token': token} if token else {})))
        fills += page
        if len(page) < 100:
            break
        token = page[-1]['id']
    def day(t):  # an ISO time in UTC -> the New York trading date
        at = datetime.fromisoformat(re.sub(r'\.\d+', '', t).replace('Z', '+00:00')).astimezone(timezone.utc).timestamp()
        return str(scan.ts_to_eastern(at).date())
    rows = sorted((dict(at=day(f['transaction_time']), time=f['transaction_time'], symbol=f['symbol'], side='BUY' if f['side'] == 'buy' else 'SELL', qty=str(f['qty']), price=str(f['price']))
                   for f in fills), key=lambda x: (x['at'], x['time']))
    since = {p['symbol']: p['since'] for p in net_fills(rows)[0]}
    positions, notes = [], []
    for p in held:
        if p.get('asset_class', 'us_equity') != 'us_equity' or p.get('side', 'long') != 'long':
            notes.append(f'{p["symbol"]}: a {p.get("side", "")} {p.get("asset_class", "")} position is not shown; the desk covers long US stocks.'.replace('  ', ' '))
            continue
        positions.append(dict(symbol=p['symbol'], qty=D(str(p['qty'])), avg_cost=D(str(p['avg_entry_price'])).quantize(D('.0001')), since=since.get(p['symbol'])))
    mode = 'live' if secret('ALPACA_LIVE') == '1' else 'paper'
    return f'Alpaca {mode} ••••{str(acct.get("account_number", ""))[-4:]}', D(str(acct['cash'])), positions, rows, notes

def sync_alpaca(path=HOLDINGS, get=alpaca_get):
    """Replace the holdings with Alpaca's, keeping each holding's tag while it stays open. Returns the new record."""
    label, cash, positions, fills, notes = read_alpaca(get)
    h = snapshot(label, cash, positions, fills, 'alpaca (read-only API)', previous=load(path), notes=notes)
    save(h, path)
    return h

def tag(h, sym, purpose, plan=None):
    """Mark one holding an investment or a swing trade with its own exit plan (entry, stop, target, opened). Returns an error text or None."""
    p = next((p for p in h['positions'] if p['symbol'] == sym), None)
    if not p:
        return f'{sym} is not in the imported holdings.'
    if purpose == 'swing':
        try:
            entry, stop, target = (D(str(plan[k])) for k in ('entry', 'stop', 'target'))
        except (KeyError, TypeError, InvalidOperation):
            return 'A swing lot needs entry, stop and target prices.'
        if not 0 < stop < entry < target:
            return 'Needs stop < entry < target.'
        p['plan'] = dict(entry=str(entry), stop=str(stop), target=str(target), opened=plan.get('opened') or str(date.today()))
    else:
        p['plan'] = None
    p['purpose'] = purpose

def text(why):
    """An engine block reason, as a check line."""
    t = why.removeprefix('Signal, but ').removeprefix('entries blocked: ')
    return t[0].upper() + t[1:]

def actions(h, e, now=None):
    """What needs attention: sells for swing lots at their stop or target, buys for today's breakouts, each re-checked against the real account and your rules;
    plus the holdings being watched and every reason the picture may be incomplete. `e` is the stocks engine (rules, last quotes, replay gate)."""
    now = now or time.time()
    with e.lock:
        s = e.s
        quotes, decisions, updated, day, market = dict(s.get('quotes') or {}), dict(s.get('decisions') or {}), s.get('updated'), s.get('day'), dict(s.get('market') or {})
        symbols, r = list(e.symbols), e.rules()
    out = dict(holdings=h, rules={k: str(v) for k, v in r.items()}, updated=updated, market=market, cards=[], watching=[], notes=[])
    positions = (h or {}).get('positions', [])
    if not h:
        out['notes'].append(dict(kind='import', text='No account imported yet: proposals are sized by the per-position cap alone. Import a brokerage CSV with: python3 -m signaldesk.desk import FILE.csv'))
    else:
        age = now - h['synced_at']
        if age > SYNC_OLD:
            out['notes'].append(dict(kind='stale', text=f'Holdings were synced {age / 3600:.0f} hours ago. Trades since then are not known here, so the checks below may be working from an old picture.'))
        if h.get('cash') is None:
            out['notes'].append(dict(kind='cash', text='Cash is not in the import, so proposed sizes use the per-position cap and are not checked against what you can spend.'))
        out['notes'] += [dict(kind='import', text=t) for t in h.get('notes', [])]
    is_open = bool(market.get('open'))
    def fresh(at):
        """A check line for the quote's age: in session a quote the feed stopped updating fails, so no card passes on an old price."""
        if not is_open:
            return None
        age = now - at if at else None
        if age is None or age > QUOTE_OLD:
            return dict(ok=False, text='Quote is ' + ('of unknown age' if age is None else f'{age / 60:.0f} min old') + ': the price feed is not updating, so this price is not trusted')
        return None
    unpriced = []
    swing = [p for p in positions if p.get('purpose') == 'swing' and p.get('plan')]
    for p in positions:
        q = quotes.get(p['symbol'])
        if not q:
            unpriced.append(p['symbol'])
            out['notes'].append(dict(kind='unpriced', text=f'No price for {p["symbol"]} yet, so its rule cannot be checked. It is quoted from the stocks agent\'s next check.'))
            continue
        price, at, qty = D(q[0]), q[3] if len(q) > 3 else None, D(p['qty'])
        row = dict(symbol=p['symbol'], purpose=p.get('purpose', 'invest'), qty=str(qty), price=str(price), quote_at=at, value=str(qty * price), avg_cost=p.get('avg_cost'),
                   gain=None if not p.get('avg_cost') else str(price / D(p['avg_cost']) - 1), plan=None)
        if p in swing:
            plan = p['plan']
            entry, stop, target = (D(plan[k]) for k in ('entry', 'stop', 'target'))
            row.update(plan=plan, to_stop=str(price / stop - 1), to_target=str(target / price - 1))
            reason = (f'Stop: {price:.2f} is at or under your stop {stop:.2f} (entry {entry:.2f}, {price / entry - 1:+.1%}).' if price <= stop else
                      f'Target: {price:.2f} reached your target {target:.2f} (entry {entry:.2f}, {price / entry - 1:+.1%}).' if price >= target else None)
            if reason:
                out['cards'].append(dict(action='SELL', symbol=p['symbol'], trigger=reason, price=str(price), quote_at=at, qty=str(qty), proceeds=str(qty * price), est_pnl=str(qty * (price - entry)),
                                         est_note='At this quote. A stop order can fill below the stop price, so the result is an estimate until the fill.',
                                         checks=(checks := [c for c in (fresh(at), dict(ok=is_open, text='Market open' if is_open else 'Market closed: place it for the open')) if c]),
                                         passes=all(c['ok'] for c in checks)))
        out['watching'].append(row)
    today = day or str(date.today())
    cash = None if not h or h.get('cash') is None else D(h['cash'])
    for sym in symbols:
        q = quotes.get(sym)
        if not q or len(q[2]) < 30 or 'breakout_20d' not in scan.find_setups(q[2])[0]:
            continue  # the same test the paper agent runs, on the closes it saved: yesterday's close above the 20 before it
        price, closes, at = D(q[0]), q[2], q[3] if len(q) > 3 else None
        checks = [dict(ok=sym not in [p['symbol'] for p in swing], text='Already in a swing trade on it' if sym in [p['symbol'] for p in swing] else 'No open swing trade on it')]
        if r['max_new']:
            n = sum(p['plan'].get('opened') == today for p in swing)
            checks.append(dict(ok=n < r['max_new'], text=f'{n} of {r["max_new"]:.0f} new buys today'))
        if r['max_open']:
            checks.append(dict(ok=len(swing) < r['max_open'], text=f'{len(swing)} of {r["max_open"]:.0f} swing positions open'))
        if r['max_sector']:
            n = sum(SECTOR.get(p['symbol'], p['symbol']) == SECTOR.get(sym, sym) for p in swing)
            checks.append(dict(ok=n < r['max_sector'], text=f'{n} of {r["max_sector"]:.0f} open in {SECTOR.get(sym, "this sector")}'))
        for why in (e.price_block(dict(closes=[(None, c) for c in closes], price=price)), e.edge_block(), e.congress_block(sym, today)):
            if why:
                checks.append(dict(ok=False, text=text(why)))
        if cash is not None:
            checks.append(dict(ok=cash >= 1, text=f'Cash ${cash:.2f}' + ('' if cash >= 1 else ': nothing to buy with')))
        if stale := fresh(at):
            checks.append(stale)
        checks.append(dict(ok=is_open, text='Market open' if is_open else 'Market closed: the entry waits for the open'))
        budget = r['per_trade'] or (cash if cash and cash >= 1 else D(0))
        if cash is not None and cash >= 1:
            budget = min(budget, cash)  # with no cash the card still shows what the rule would size, and the cash check fails
        qty = (budget / price).quantize(D('.0001'), rounding=ROUND_DOWN) if budget > 0 else D(0)
        if qty <= 0:
            checks.append(dict(ok=False, text='Size is 0 shares: set a per-position cap on the US stocks page, or import your cash'))
        stop_px, target_px = price * (1 - r['stop']), price * (1 + r['target'])
        out['cards'].append(dict(action='BUY', symbol=sym, trigger=f'Close {closes[-1]:.2f} broke the 20-day closing high {max(closes[-21:-1]):.2f}. Agent: {decisions.get(sym, "—")}',
                                 price=str(price), quote_at=at, qty=str(qty), cost=str(qty * price), stop=str(stop_px), target=str(target_px), est_loss=str(qty * (price - stop_px)),
                                 est_note=f'Loss if the stop fills at {stop_px:.2f}. A stop can fill lower (a gap, a fast market), so this is an estimate, not a limit.',
                                 checks=checks, passes=all(c['ok'] for c in checks)))
    out['unpriced'] = unpriced  # left out of the account value until they have a price
    out['cards'].sort(key=lambda c: (c['action'] != 'SELL', not c['passes'], c['symbol']))
    return out

class Held:
    """The symbols the holdings file lists, read again every time the stocks agent asks, so an import shows up at its next check."""
    def __init__(self, path):
        self.path = path

    def __iter__(self):
        return iter([p['symbol'] for p in (load(self.path) or {}).get('positions', [])])

class Desk:
    """The 'market' behind /desk: no agent of its own. Every request reads the holdings file and the stocks agent's last check."""
    def __init__(self, stocks, path=HOLDINGS, get=alpaca_get):
        self.stocks, self.path, self.running, self.error, self.get = stocks, Path(path), False, None, get
        self.lock, self.wake = threading.Lock(), threading.Event()
        stocks.extra = Held(self.path)  # held but not on the watchlist: quoted at every check, never traded

    def sync(self):
        """One broker read when Alpaca keys are set; a failure is kept for the page and retried at the next interval."""
        if not secret('ALPACA_KEY_ID'):
            return
        try:
            sync_alpaca(self.path, self.get)
            self.error = None
        except Exception as exc:
            self.error = f'Alpaca sync failed: {type(exc).__name__}: {exc}. Holdings below are from the last good sync.'

    def worker(self):
        while True:
            self.sync()
            self.wake.wait(SYNC_EVERY)
            self.wake.clear()

    def control(self, path):
        return False

    def snapshot(self):
        out = actions(load(self.path), self.stocks)
        if self.error:
            out['notes'].insert(0, dict(kind='sync', text=self.error))
        return out

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('cmd', choices=['import', 'sync', 'tag', 'show'])
    p.add_argument('arg', nargs='?', help='CSV path for import, symbol for tag')
    p.add_argument('purpose', nargs='?', choices=['swing', 'invest'])
    p.add_argument('--account', default='Brokerage (CSV)'); p.add_argument('--file', default=str(HOLDINGS))
    for k in ('entry', 'stop', 'target'):
        p.add_argument('--' + k)
    p.add_argument('--opened')
    a = p.parse_args(argv)
    h = load(a.file)
    if a.cmd == 'import':
        fills, positions, notes = read_csv(a.arg)
        exported = min(os.path.getmtime(a.arg), time.time())  # the download time: an old export imported today is still old
        h = snapshot(a.account, (h or {}).get('cash'), positions, fills, f'csv {Path(a.arg).name}', previous=h, notes=notes, synced_at=exported)
        save(h, a.file)
        print(f'{len(fills)} fills, {len(positions)} positions -> {a.file}' + ''.join('\n  ' + n for n in notes))
    elif a.cmd == 'sync':
        h = sync_alpaca(a.file)
        print(f'{h["account"]}: {len(h["positions"])} positions, cash ${D(h["cash"]):.2f}, {len(h["fills"])} fills -> {a.file}' + ''.join('\n  ' + n for n in h['notes']))
    elif a.cmd == 'tag':
        if not h:
            return print('Import holdings first.') or 1
        err = tag(h, (a.arg or '').upper(), a.purpose or 'invest', dict(entry=a.entry, stop=a.stop, target=a.target, opened=a.opened))
        if err:
            return print(err) or 1
        save(h, a.file)
    print(json.dumps(h, indent=1) if h else 'No holdings imported.')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
