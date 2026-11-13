"""Exit experiment, run as a fixed protocol (python3 research_exits.py).

Declared before the first run:
  Baseline (frozen): the rules saved from the dashboard (stop 10%, target +25%, $500 a position, 3 new buys a day, no Congress filter).
  One component changes per variant, and only the exit: the +25% target, then a trailing stop or a close under the N-day low. The entry never changes.
  Development data: the 20 live watchlist stocks. Their prices were already looked at in earlier backtests, so they cannot validate anything.
  Fresh data: the 20 stocks below, fixed now, never used in earlier tests, and not picked for having done well (INTC, PFE, BA, NKE, DIS fell).
  Measures: net total, drawdown, share of time invested, entries (turnover), wins, first and second half, all against buy and hold.
  Costs: 0.05% slippage on each fill, already in the engine. No fees.
Also answers three questions about the baseline trades: what happened after target exits, after stop exits, and where the gap to buy and hold came from.
"""
import json
import statistics as st
import sys
from datetime import date
from decimal import Decimal as D

from signaldesk.app import startup_rules
from signaldesk.swing import CODE, ROOT, WATCHLIST, data_hash, history, replay, save_history

FRESH = 'ADBE ORCL CRM CSCO INTC QCOM TXN HD MCD NKE DIS PFE MRK ABBV BAC WFC CVX CAT BA IBM'.split()
NO_TARGET = D(100)  # 10,000%: the target never fires
VARIANTS = [
    ('A  baseline: stop 10%, target +25%', {}),
    ('B  no target, stop 10% only', dict(target=NO_TARGET)),
    ('C  trailing 15%, no target', dict(target=NO_TARGET, trail=D('.15'))),
    ('D  trailing 25%, no target', dict(target=NO_TARGET, trail=D('.25'))),
    ('E  exit under 20-day low, no target', dict(target=NO_TARGET, exit_low=D(20))),
    ('F  exit under 10-day low, no target', dict(target=NO_TARGET, exit_low=D(10))),
]

def fwd(hist, sym, day, k):
    """Return k trading days after `day` (None when the data ends before that)."""
    rows = hist[sym]
    i = next((n for n, (d, _) in enumerate(rows) if str(d) == day), None)
    return None if i is None or i + k >= len(rows) else rows[i + k][1] / rows[i][1] - 1

def after_exits(hist, journal):
    out = {'target': [], 'stop': []}
    entry = {}
    for j in journal:
        if j['side'] == 'BUY':
            entry[j['symbol']] = float(j['price'])
        else:
            kind = 'target' if j['reason'].startswith('Target') else 'stop' if j['reason'].startswith('Stop') else None
            if kind:
                out[kind].append((j['symbol'], j['day'], float(j['price']), entry.get(j['symbol'])))
    res = {}
    for kind, rows in out.items():
        r = {}
        for k in (5, 20):
            f = [x for x in (fwd(hist, s, d, k) for s, d, _, _ in rows) if x is not None]
            r[f'd{k}'] = dict(n=len(f), median=st.median(f) if f else None, up=sum(x > 0 for x in f) / len(f) if f else None)
        if kind == 'stop':   # did it get back to the entry price within 20 trading days, or keep falling another 10%?
            back = fall = n = 0
            for s, d, px, e in rows:
                path = [c for dd, c in hist[s] if str(dd) > d][:20]
                if len(path) < 20 or not e:
                    continue
                n += 1; back += max(path) >= e; fall += min(path) <= px * .9
            r['recovered_to_entry'] = back / n if n else None; r['fell_another_10pct'] = fall / n if n else None; r['n_path'] = n
        res[kind] = dict(count=len(rows), **r)
    return res

def table(name, hist, base):
    rows, base_total = [], None
    print(f'\n### {name}: {len(hist)} stocks, {min(h[0][0] for h in hist.values())} to {max(h[-1][0] for h in hist.values())}\n')
    print('| variant | net total | vs baseline | max drawdown | capital deployed | days with a position | entries | wins/exits | 1st half | 2nd half |')
    print('|---|---|---|---|---|---|---|---|---|---|')
    first = None
    for label, over in VARIANTS:
        r = replay(hist, record=not over, **{**base, **over})
        base_total = r['total'] if base_total is None else base_total
        first = first or r
        rows.append(dict(variant=label, total=float(r['total']), max_drawdown=float(r['max_drawdown']), exposure=float(r['exposure']), days_open=float(r['days_open']), entries=r['entries'], exits=r['exits'], wins=r['wins'],
                         half1=float(r['half1']), half2=float(r['half2'])))
        print(f"| {label} | {r['total']:+,.0f} | {r['total'] - base_total:+,.0f} | {r['max_drawdown']:.1%} | {r['exposure']:.0%} | {r['days_open']:.0%} | {r['entries']} | {r['wins']}/{r['exits']} | {r['half1']:+,.0f} | {r['half2']:+,.0f} |")
    bh = first['buy_hold']
    print(f'\nBuy and hold of the same stocks over the same days: {bh:+,.0f}. Baseline captured {first["total"] / bh:.0%} of it.')
    return rows, first

def main():
    base = {**startup_rules(ROOT / 'data' / 'stocks.sqlite', D(5000)), 'congress_days': D(0)}
    print('Frozen baseline rules:', ', '.join(f'{k} {v}' for k, v in base.items()))
    out = dict(date=str(date.today()), code=CODE, baseline={k: str(v) for k, v in base.items()}, universes={})
    for name, symbols in (('Development: live watchlist', WATCHLIST), ('Fresh: 20 stocks never tested before', FRESH)):
        hist = history(symbols)
        missing = set(symbols) - set(hist)
        if missing:
            print('missing data:', missing, file=sys.stderr)
        save_history(hist, ROOT / 'data' / 'history')
        rows, first = table(name, hist, base)
        ex = after_exits(hist, first['journal'])
        print('\nAfter baseline exits (return of the stock from the exit day):')
        for kind in ('target', 'stop'):
            e = ex[kind]
            print(f"- {kind} exits: {e['count']}. +5 days: median {e['d5']['median']:+.1%}, up {e['d5']['up']:.0%} of the time. +20 days: median {e['d20']['median']:+.1%}, up {e['d20']['up']:.0%}." if e['count'] else f'- {kind} exits: none')
            if kind == 'stop' and e.get('n_path'):
                print(f"  back to the entry price within 20 days: {e['recovered_to_entry']:.0%}; fell a further 10% within 20 days: {e['fell_another_10pct']:.0%} (n={e['n_path']})")
        exposure, total, bh = float(first['exposure']), float(first['total']), float(first['buy_hold'])
        print(f'\nGap to buy and hold: baseline {total:+,.0f} vs {bh:+,.0f} (gap {bh - total:,.0f}). Invested {exposure:.0%} of the time; scaled to fully invested the baseline would be about {total / exposure:+,.0f}, '
              f'so idle cash explains about {total / exposure - total:,.0f} of the gap and stock selection/timing the other {bh - total / exposure:,.0f}. (Approximation: it assumes the return per invested dollar is the same while in cash.)')
        out['universes'][name] = dict(data_sha256=data_hash(hist), variants=rows, after_exits={k: {kk: vv for kk, vv in v.items()} for k, v in ex.items()}, buy_hold=bh)
    (ROOT / 'data' / 'research').mkdir(parents=True, exist_ok=True)
    path = ROOT / 'data' / 'research' / f'exits-{date.today()}.json'
    path.write_text(json.dumps(out, indent=1, default=str))
    print(f'\nSaved {path.relative_to(ROOT)} (rules, code hashes, data hashes, results).')

if __name__ == '__main__':
    main()
