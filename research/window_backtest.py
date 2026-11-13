"""Compare breakout window lengths with the live engine. Every window starts buying on the same day (after 60 closes of warm-up)."""
from decimal import Decimal as D
from signaldesk import swing as stocks
from signaldesk.swing import WATCHLIST, history, replay

def run(h, n, **rules):
    real = stocks.scan.find_setups
    stocks.scan.find_setups = lambda c: (['breakout_20d'] if len(c) >= 60 and c[-1] > max(c[-n - 1:-1]) else [], {})
    try:
        return replay(h, **rules)
    finally:
        stocks.scan.find_setups = real

if __name__ == '__main__':
    full = history(WATCHLIST, 5)
    live = dict(capital=D(5000), per_trade=D(500), daily_loss=D(400), alert_at=D('.8'), target=D(1))
    for label, start in [('5 years', '0000'), ('last 2 years', '2024-08-01')]:  # 2y start leaves 60 closes of warm-up before Nov 2024
        h = {k: [(d, c) for d, c in v if str(d) >= start] for k, v in full.items()}
        bh = [v[-1][1] / v[60][1] - 1 for v in h.values() if len(v) > 60]
        print(f'\n{label}: buy and hold, same stocks equal-weight {sum(bh) / len(bh) * 100:+.0f}%')
        print(f'{"window":>7}{"stop":>6}{"entries":>9}{"exits":>7}{"wins":>6}{"realized":>10}{"total":>9}')
        for stop in ('.10', '.20'):
            for n in (10, 20, 30, 40, 55):
                r = run(h, n, stop=D(stop), **live)
                print(f'{n:>6}d{float(stop) * 100:>5.0f}%{r["entries"]:>9}{r["exits"]:>7}{r["wins"]:>6}{float(r["realized"]):>10.0f}{float(r["total"]):>9.0f}')
