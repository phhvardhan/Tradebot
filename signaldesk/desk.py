"""The decision desk: one real brokerage account imported read-only, and the "Action needed" view that applies the swing rules you chose to what you actually
own and to today's signals. Nothing here places an order: you decide, place it with your broker, then re-import. Holdings, cash and fills live in
data/holdings.json, apart from every paper portfolio. An investment and a swing trade can share a ticker: swing exits apply only to lots tagged swing.

  python3 -m signaldesk.desk import FILE.csv [--account NAME]       Robinhood "account activity" CSV: Buy/Sell rows become fills, netted into positions (cash is not in the file)
  python3 -m signaldesk.desk tag SYM swing --entry E --stop S --target T [--opened YYYY-MM-DD]
  python3 -m signaldesk.desk tag SYM invest
  python3 -m signaldesk.desk show
"""
import argparse
import csv
import json
import time
from datetime import date, datetime
from decimal import InvalidOperation, ROUND_DOWN
from pathlib import Path

from .core import D, ROOT
from .quotes import scan
from .swing import SECTOR

HOLDINGS = ROOT / 'data' / 'holdings.json'
SYNC_OLD = 24 * 3600   # a snapshot older than this is flagged: trades since then are unknown here

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

def snapshot(account, cash, positions, fills, source, previous=None, notes=()):
    """The holdings record. A symbol's purpose and swing plan survive a re-import; positions are {symbol, qty, avg_cost}."""
    tagged = {p['symbol']: p for p in (previous or {}).get('positions', [])}
    rows = [dict(symbol=p['symbol'], qty=str(p['qty']), avg_cost=None if p.get('avg_cost') is None else str(p['avg_cost']),
                 purpose=tagged.get(p['symbol'], {}).get('purpose', 'invest'), plan=tagged.get(p['symbol'], {}).get('plan')) for p in positions]
    return dict(account=account, source=source, synced_at=time.time(), cash=None if cash is None else str(cash), positions=sorted(rows, key=lambda p: p['symbol']), fills=list(fills), notes=list(notes))

def iso(text):
    for fmt in ('%Y-%m-%d', '%m/%d/%Y'):
        try:
            return datetime.strptime(text.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f'Unreadable date: {text!r}')

def read_csv(path):
    """Robinhood account-activity export: Buy and Sell rows are fills; everything else (dividends, transfers) is skipped.
    Returns (fills oldest first, positions they net to, notes). Average cost follows the broker's way: a sale removes its share of the cost, not the price."""
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
    fills.sort(key=lambda x: x['at'])
    net, notes = {}, []
    for x in fills:
        p = net.setdefault(x['symbol'], dict(qty=D(0), cost=D(0)))
        q, px = D(x['qty']), D(x['price'])
        if x['side'] == 'BUY':
            p['qty'] += q; p['cost'] += q * px
        else:
            if q > p['qty'] and x['symbol'] not in ''.join(notes):
                notes.append(f'{x["symbol"]}: the file sells more than it bought, so it does not start at the account opening; its position and cost are incomplete.')
            p['cost'] -= p['cost'] * min(q / p['qty'], D(1)) if p['qty'] > 0 else D(0)
            p['qty'] -= q
    positions = [dict(symbol=s, qty=p['qty'], avg_cost=(p['cost'] / p['qty']).quantize(D('.0001'))) for s, p in net.items() if p['qty'] > 0]
    return fills, positions, notes

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
    swing = [p for p in positions if p.get('purpose') == 'swing' and p.get('plan')]
    for p in positions:
        q = quotes.get(p['symbol'])
        if not q:
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
                                         checks=[dict(ok=is_open, text='Market open' if is_open else 'Market closed: place it for the open')], passes=is_open))
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
        checks.append(dict(ok=is_open, text='Market open' if is_open else 'Market closed: the entry waits for the open'))
        budget = r['per_trade'] or (cash if cash and cash >= 1 else D(0))
        if cash is not None and cash >= 1:
            budget = min(budget, cash)  # with no cash the card still shows what the rule would size, and the cash check fails
        qty = (budget / price).quantize(D('.0001'), rounding=ROUND_DOWN) if budget > 0 else D(0)
        stop_px, target_px = price * (1 - r['stop']), price * (1 + r['target'])
        out['cards'].append(dict(action='BUY', symbol=sym, trigger=f'Close {closes[-1]:.2f} broke the 20-day closing high {max(closes[-21:-1]):.2f}. Agent: {decisions.get(sym, "—")}',
                                 price=str(price), quote_at=at, qty=str(qty), cost=str(qty * price), stop=str(stop_px), target=str(target_px), est_loss=str(qty * (price - stop_px)),
                                 est_note=f'Loss if the stop fills at {stop_px:.2f}. A stop can fill lower (a gap, a fast market), so this is an estimate, not a limit.',
                                 checks=checks, passes=all(c['ok'] for c in checks)))
    out['cards'].sort(key=lambda c: (c['action'] != 'SELL', not c['passes'], c['symbol']))
    return out

class Desk:
    """The 'market' behind /desk: no agent of its own. Every request reads the holdings file and the stocks agent's last check."""
    def __init__(self, stocks, path=HOLDINGS):
        self.stocks, self.path, self.running, self.error = stocks, Path(path), False, None

    def worker(self):
        pass

    def control(self, path):
        return False

    def snapshot(self):
        h = load(self.path)
        self.stocks.extra = [p['symbol'] for p in (h or {}).get('positions', [])]  # held but not on the watchlist: quoted from the next check on
        return actions(h, self.stocks)

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('cmd', choices=['import', 'tag', 'show'])
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
        h = snapshot(a.account, (h or {}).get('cash'), positions, fills, f'csv {Path(a.arg).name}', previous=h, notes=notes)
        save(h, a.file)
        print(f'{len(fills)} fills, {len(positions)} positions -> {a.file}' + ''.join('\n  ' + n for n in notes))
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
