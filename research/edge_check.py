"""Does the breakout rule earn more per day in a position than simply holding the same stocks? (python3 -m research.edge_check)

Every signal is taken ($1 each, unlimited cash), so the answer does not depend on which signal happened to get the cash.
Fills follow the live agent on daily bars: buy at the open if it is inside the entry window (or at the level when the day trades up through it),
stop and target hit intraday at their levels, a gap fills at the open. Slippage 0.05% a side.
Measure: return per day in a position, minus the average daily return of the same stocks, with a 5-95% bootstrap range over trades.
A range that spans zero means the variant cannot be told apart from holding. Read every row that way before changing a rule.
"""
import random
import statistics as st
import time

from research.research_exits import FRESH
from signaldesk.quotes import scan
from signaldesk.swing import GAP, WATCHLIST

SLIP, GAP = .0005, float(GAP)
VARIANTS = [('live rule: stop 10%, target +25%', {}), ('stop judged at the close', dict(at_close=True)), ('stop 15%', dict(stop=.15)), ('stop 20%', dict(stop=.20)),
            ('target +50%', dict(target=.5)), ('no target', dict(target=99)), ('no target, trailing 15%', dict(target=99, trail=.15)),
            ('no target, exit under 20-day low', dict(target=99, exit_low=20)), ('10-day breakout', dict(window=10)), ('55-day breakout', dict(window=55)),
            ('only above own 200-day average', dict(ma=200)), ('stop 1.5x own volatility, target 2.5R', dict(vol=1.5))]

def bars(sym):
    s = scan.parse_chart(scan.yahoo_get(scan.chart_path(sym, '5y', '1d')), sym)
    rows = {str(scan.ts_to_eastern(t).date()): (o, h, l, c) for t, o, h, l, c in zip(s['t'], s['o'], s['h'], s['l'], s['c']) if None not in (o, h, l, c)}
    time.sleep(scan.PAUSE)
    return sorted((d, *v) for d, v in rows.items())

def trades(rows, stop=.10, target=.25, window=20, ma=0, vol=0, exit_low=0, trail=0, at_close=False):
    """[(return, days held)] for one stock."""
    out, pos, used = [], None, ''
    for i in range(max(30, ma, window + 1), len(rows)):
        day, o, h, l, c = rows[i]
        cs = [r[4] for r in rows[max(0, i - 260):i]]
        if pos:
            sl, tg = max(pos['sl'], pos['high'] * (1 - trail)) if trail else pos['sl'], pos['tg']
            px = (o if not at_close and o <= sl else o if o >= tg else sl if not at_close and l <= sl else tg if h >= tg
                  else c if (at_close and c <= sl) or (exit_low and c < min(cs[-exit_low:])) else None)
            pos['high'] = max(pos['high'], h)
            if px:
                out.append((px * (1 - SLIP) / pos['entry'] - 1, i - pos['i'] + 1)); pos, used = None, day
            continue
        level, close, top = max(cs[-window - 1:-1]), cs[-1], cs[-1] * (1 + GAP)
        if close <= level or used >= rows[i - 1][0] or (ma and close <= sum(cs[-ma:]) / ma):
            continue
        px = o if level <= o <= top else level if o < level <= h else top if o > top and l <= top else None
        if px:
            e, s = px * (1 + SLIP), stop
            if vol:  # stop distance from the stock's own 20-day volatility, target a fixed multiple of it
                s = min(.30, max(.04, vol * st.pstdev(cs[k] / cs[k - 1] - 1 for k in range(len(cs) - 20, len(cs))) * 20 ** .5)); target = s * 2.5
            pos, used = dict(i=i, entry=e, sl=e * (1 - s), tg=e * (1 + target), high=e), day
    if pos:
        out.append((rows[-1][4] / pos['entry'] - 1, len(rows) - pos['i']))
    return out

def edge(data, **rule):
    """(trades, average return, basis points a day in a position, the same for holding, 5% and 95% of the difference)."""
    t = [x for rows in data.values() for x in trades(rows, **rule)]
    hold = st.mean(st.mean(r[k][4] / r[k - 1][4] - 1 for k in range(31, len(r))) for r in data.values())
    per_day = lambda z: sum(r for r, _ in z) / sum(d for _, d in z)
    rnd = random.Random(1)
    boots = sorted(per_day(rnd.choices(t, k=len(t))) - hold for _ in range(400))
    return len(t), st.mean(r for r, _ in t), per_day(t) * 1e4, hold * 1e4, boots[20] * 1e4, boots[379] * 1e4

def main():
    for name, symbols in (('Live watchlist (picked with hindsight)', WATCHLIST), ('20 other large stocks', FRESH)):
        data = {s: bars(s) for s in symbols}
        print(f'\n{name}: {min(r[0][0] for r in data.values())} to {max(r[-1][0] for r in data.values())}')
        print(f'{"variant":<40}{"trades":>7}{"avg":>8}{"bp/day":>8}{"hold":>6}   excess over holding, 5-95%')
        for label, rule in VARIANTS:
            n, avg, bp, hold, lo, hi = edge(data, **rule)
            print(f'{label:<40}{n:>7}{avg:>+8.1%}{bp:>8.1f}{hold:>6.1f}   {lo:+.1f} to {hi:+.1f}{"" if lo <= 0 <= hi else "   <- does not span zero"}')

if __name__ == '__main__':
    main()
