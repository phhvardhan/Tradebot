#!/usr/bin/env python3
"""Replay the live engine's exact rule over real Coinbase BTC-USD 5-minute candles, with fees and slippage.

    python3 -m legacy.btc.backtest                 # last ~14 days, spread 0.10% and 0.50%
    python3 -m legacy.btc.backtest --days 7 --spread 0.002

History has no bid/ask, so each check uses close -/+ half the assumed spread.
One check per completed candle. Like the Time Machine: a replay, not a prediction.
"""
import argparse
import time
from signaldesk.core import D
from signaldesk.quotes import coinbase
from .bot import Engine, Market

def history(days):
    # Coinbase returns at most 300 candles per request, newest first.
    rows, end = {}, int(time.time()) // 300 * 300
    for start in range(end - days * 86400, end, 300 * 300):
        for r in coinbase('candles', {'granularity': 300, 'start': start, 'end': min(start + 299 * 300, end)}):
            rows[r[0]] = r
    return [(r[0], D(str(r[4])), D(str(r[5]))) for t, r in sorted(rows.items()) if t + 300 <= time.time()]

def replay(candles, spread, **engine_args):
    e = Engine(':memory:', Market(False), **engine_args)
    half = D(str(spread)) / 2
    for i in range(20, len(candles)):
        window = candles[max(0, i - 59):i + 1]
        if any(b[0] - a[0] != 300 for a, b in zip(window, window[1:])):
            continue  # the live engine refuses gapped history too
        close = window[-1][1]
        e.apply([(t, c) for t, c, _ in window], close * (1 - half), close * (1 + half), window[-1][0] + 300)
    trades = [dict(zip(['id', 'time', 'side', 'qty', 'price', 'fee', 'pnl', 'reason'], r))
              for r in e.conn.execute('SELECT * FROM trades ORDER BY id')]
    sells = [t for t in trades if t['side'] == 'SELL']
    pnl = [D(t['pnl']) for t in sells]
    wins = [p for p in pnl if p > 0]
    losses = [-p for p in pnl if p <= 0]
    return dict(spread=spread, round_trips=len(sells), wins=len(wins), realized=sum(pnl, D(0)),
                fees=D(e.s['fees']), equity=D(e.s['equity']),
                avg_win=sum(wins, D(0)) / len(wins) if wins else D(0),
                avg_loss=sum(losses, D(0)) / len(losses) if losses else D(0))

def verdict(r):
    if not r['round_trips']:
        return 'No trades: the rule never entered.'
    hit = r['wins'] / r['round_trips']
    need = r['avg_loss'] / (r['avg_win'] + r['avg_loss']) if r['avg_win'] else D(1)
    gap = (D(hit) - need) * 100
    lean = 'Close to break-even' if abs(gap) < 5 else ('Leans profitable' if gap > 0 else 'Leans losing')
    return f'{lean}: won {hit:.0%}, needs {need:.0%} to break even after costs.'

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--days', type=int, default=14)
    p.add_argument('--spread', type=float, action='append', help='assumed bid/ask spread, e.g. 0.002 (repeatable)')
    a = p.parse_args()
    candles = history(a.days)
    traded = sum(v > 0 for _, _, v in candles)
    print(f'{len(candles)} candles, {candles[0][0] and time.strftime("%Y-%m-%d %H:%M", time.gmtime(candles[0][0]))} to '
          f'{time.strftime("%Y-%m-%d %H:%M", time.gmtime(candles[-1][0]))} UTC; {traded} ({traded / len(candles):.0%}) had any volume')
    for s in a.spread or [0.001, 0.005]:
        r = replay(candles, s)
        print(f'\nspread {s:.2%}: {r["round_trips"]} round trips, {r["wins"]} wins, realized ${r["realized"]:.2f}, '
              f'fees ${r["fees"]:.2f}, end equity ${r["equity"]:.2f} (from $4000)')
        print(f'  avg win ${r["avg_win"]:.2f}, avg loss ${r["avg_loss"]:.2f}. {verdict(r)}')

if __name__ == '__main__':
    main()
