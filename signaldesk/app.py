"""Entry point: the command line, the four markets on one port, and the server. `python3 run.py` calls main()."""
import argparse
from pathlib import Path

from . import congress as congress_data
from . import day, funds
from .account import AccountMonitor
from .core import D, ROOT, positive
from .desk import Desk
from .server import serve
from .swing import DEFAULT_RULES, WATCHLIST, build, build_challenger, build_crypto, history, replay, saved_rules

def startup_rules(db, capital, per_trade=None, daily_loss=None):
    """Defaults, then what the dashboard saved, then command-line flags."""
    rules = {**DEFAULT_RULES, 'per_trade': D(500), 'daily_loss': D(400), **saved_rules(db), 'capital': capital}
    rules.update({k: v for k, v in (('per_trade', per_trade), ('daily_loss', daily_loss)) if v is not None})
    return rules

def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port', type=int, default=8765)
    p.add_argument('--db')
    p.add_argument('--data-dir', default=str(ROOT / 'data'), help='where the databases, backups, decision records and challenger live')
    p.add_argument('--symbols', default=' '.join(WATCHLIST), help='space-separated US tickers')
    p.add_argument('--capital', type=positive, default=D('5000'))
    p.add_argument('--per-trade', type=positive, default=None, help='max dollars per position (default: the saved rule, else 500)')
    p.add_argument('--daily-loss', type=positive, default=None, help='no new entries once down this much today (default: the saved rule, else 400)')
    p.add_argument('--paused', action='store_true', help='start with the agent paused (default: it resumes, so stops and targets keep being checked)')
    p.add_argument('--replay', action='store_true', help='print the 5-year replay of the rule and exit')
    return p.parse_args(argv)

def build_app(a, offline=None):
    """Everything main() starts, minus the web server: both markets, the challenger, and the agent's running state.
    `offline` (tests, drills) swaps the network for {'stocks': quotes_fn, 'crypto': quotes_fn, 'status': status_fn}."""
    data = Path(a.data_dir)
    data.mkdir(parents=True, exist_ok=True)
    symbols = [s.upper() for s in a.symbols.split() if s.isascii() and s.replace('.', '').replace('-', '').isalnum()]
    stocks_db = a.db or data / 'stocks.sqlite'
    rules = startup_rules(stocks_db, a.capital, a.per_trade, a.daily_loss)
    stocks, crypto = build(stocks_db, symbols, **rules), build_crypto(data / 'crypto.sqlite')
    if offline:
        for e, key in ((stocks, 'stocks'), (crypto, 'crypto')):
            e.quotes, e.notify, e.edge_check, e.news, e.bench, e.congress = offline[key], None, None, None, None, None
        stocks.status = offline['status']
    for e, name in ((stocks, 'stocks'), (crypto, 'crypto')):
        e.market_name, e.heartbeat_path = name, data / f'heartbeat-{name}.json'
    markets = {'stocks': (stocks, 'stocks.html', 'stocks.js'), 'crypto': (crypto, 'stocks.html', 'stocks.js')}
    challenger = build_challenger(stocks, audit_dir=data)
    if offline:
        challenger.quotes, challenger.status, challenger.news, challenger.congress = offline['stocks'], offline['status'], None, None
    for engine, *_ in markets.values():
        engine.running = not a.paused
    markets['funds'] = (funds.build_funds(None if not offline else ':memory:'), 'funds.html', 'funds.js')
    if offline and 'funds' in offline:
        markets['funds'][0].fetch = offline['funds']
    markets['funds'][0].running = not a.paused
    markets['day'] = (day.build_day(':memory:' if offline else data / 'day.sqlite', symbols), 'day.html', 'day.js')
    if offline:
        markets['day'][0].fetch, markets['day'][0].status, markets['day'][0].notify = offline.get('day', lambda syms, now: ({}, {})), offline['status'], None
    markets['day'][0].heartbeat_path, markets['day'][0].running = data / 'heartbeat-day.json', not a.paused
    return {'desk': (Desk(stocks, data / 'holdings.json'), 'desk.html', 'desk.js'), **markets}  # the desk is the home page: what needs attention, and why

def main():
    a = parse_args()
    if a.replay:
        symbols = [s.upper() for s in a.symbols.split() if s.isascii() and s.replace('.', '').replace('-', '').isalnum()]
        rules = startup_rules(a.db or Path(a.data_dir) / 'stocks.sqlite', a.capital, a.per_trade, a.daily_loss)
        r = replay(history(symbols), congress=congress_data.bought, **rules)
        print(f'{r["first"]} to {r["last"]}, {len(symbols)} symbols: {r["entries"]} entries, {r["exits"]} exits ({r["wins"]} wins), '
              f'{r["open"]} still open; realized ${r["realized"]:.2f}, total ${r["total"]:.2f} on ${a.capital}')
        print('Rules used: ' + ', '.join(f'{k} {v}' for k, v in rules.items()))
        print(f'Buy and hold of the same stocks: ${r["buy_hold"]:.2f}. Worst drop {r["max_drawdown"]:.1%}. Invested {r["exposure"]:.0%} of the time. First half ${r["half1"]:.2f}, second half ${r["half2"]:.2f}.')
        return
    serve(build_app(a), a.port, AccountMonitor())
