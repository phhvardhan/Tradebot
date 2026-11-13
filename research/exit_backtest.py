"""Compare exit settings with the live engine: stop, trailing stop and profit target, 20-day breakout entry, $500 positions."""
import itertools
from decimal import Decimal as D
from signaldesk.swing import WATCHLIST, history, replay

STOPS, TRAILS, TARGETS = ('.10', '.20'), ('0', '.15', '.20', '.25'), ('.25', '.50', '1')

def main():
    full = history(WATCHLIST, 5)
    live = dict(capital=D(5000), per_trade=D(500), daily_loss=D(400), alert_at=D('.8'))
    for label, start in [('5 years', '0000'), ('last 2 years', '2024-08-01')]:
        h = {k: [(d, c) for d, c in v if str(d) >= start] for k, v in full.items()}
        print(f'\n{label}: total P&L on $5,000 (realized in brackets), wins/exits', flush=True)
        print(f'{"stop":>5}{"trail":>7}' + ''.join(f'{"target +" + str(int(float(t) * 100)) + "%":>26}' for t in TARGETS))
        for stop, trail in itertools.product(STOPS, TRAILS):
            cells = []
            for tgt in TARGETS:
                r = replay(h, stop=D(stop), trail=D(trail), target=D(tgt), **live)
                cells.append(f'{float(r["total"]):>8.0f} ({float(r["realized"]):>6.0f}) {r["wins"]:>2}/{r["exits"]:<3}')
            print(f'{float(stop) * 100:>4.0f}%{("off" if trail == "0" else f"{float(trail) * 100:.0f}%"):>7}' + ''.join(f'{c:>26}' for c in cells), flush=True)

if __name__ == '__main__':
    main()
