"""Does requiring a recent congressional purchase help the 20-day-breakout rule?

Replays the live engine over the years the House filings cover, once as it is and once per filter length,
using only filings that were already public on each signal day (filing date, no look-ahead).
Run after: python3 congress.py 2023 2024 2025 2026
"""
import argparse
import json
import random
import sqlite3
import statistics
from decimal import Decimal as D

from signaldesk import congress
from signaldesk.core import ROOT
from signaldesk.swing import WATCHLIST, history, replay

def live_rules():
    try:
        s = json.loads(sqlite3.connect(ROOT / 'data' / 'stocks.sqlite').execute('SELECT body FROM state WHERE id=1').fetchone()[0])
        return {k: D(s[k]) for k in ('capital', 'per_trade', 'daily_loss', 'stop', 'target', 'alert_at')}
    except Exception:
        return dict(capital=D(5000), per_trade=D(500), daily_loss=D(400), stop=D('.1'), target=D(1), alert_at=D('.8'))

def row(name, r):
    n = r['entries']
    return f"{name:<26}{n:>8}{r['wins']:>7}{(r['wins'] / r['exits'] * 100 if r['exits'] else 0):>8.0f}%{float(r['total']):>12.2f}{(float(r['total']) / n if n else 0):>12.2f}"

def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--start', default='2023-01-01', help='first signal day to count (filings must cover it)')
    ap.add_argument('--days', type=int, nargs='+', default=[30, 45, 60, 90])
    ap.add_argument('--placebos', type=int, default=200)
    a = ap.parse_args()
    congress._load()
    first = min((r['date'] for r in congress._rows), default=None)
    print(f'{len(congress._done)} filings read; earliest filed {first}. Counting signals from {a.start}.')
    hist = {k: [(d, c) for d, c in v if str(d) >= str(int(a.start[:4]) - 1) + '-09-01'] for k, v in history(WATCHLIST).items()}
    rules = live_rules()
    gate = lambda ok: lambda sym, day, n: str(day) >= a.start and ok(sym, day, n)  # every arm, baseline included, may only buy from the start date
    modes = {'AS CONFIGURED (cash-limited)': rules, 'EVERY SIGNAL TAKEN (unlimited cash, no daily limit)': dict(rules, capital=D(10_000_000), daily_loss=D(0))}
    for title, rl in modes.items():
        print(f'\n{title}\n{"":<26}{"entries":>8}{"wins":>7}{"win%":>9}{"total $":>12}{"$/entry":>12}')
        base = replay(hist, congress_days=D(1), congress=gate(lambda *_: True), **rl)
        print(row('breakout rule alone', base))
        rows = {}
        for d in a.days:
            rows[d] = replay(hist, congress_days=D(d), congress=gate(congress.bought), **rl)
            print(row(f'+ purchase filed in {d}d', rows[d]))
        if not a.placebos or not base['entries']:
            continue
        print(f'\n  placebo: {a.placebos} random filters that keep the same share of signals as each filter above')
        for d, r in rows.items():
            keep = r['entries'] / base['entries']
            totals = sorted(float(replay(hist, congress_days=D(d), congress=gate(lambda s, day, n, i=i: random.Random(f'{i}{s}{day}').random() < keep), **rl)['total']) for i in range(a.placebos))
            beat = sum(t < float(r['total']) for t in totals) / len(totals)
            print(f'  {d:>3}d filter kept {keep:.0%} of signals: total ${float(r["total"]):.2f} vs random median ${statistics.median(totals):.2f} '
                  f'(5-95%: {totals[len(totals) // 20]:.2f} to {totals[-len(totals) // 20 - 1]:.2f}); beat {beat:.0%} of random filters')

if __name__ == '__main__':
    main()
