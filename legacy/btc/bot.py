"""The earlier BTC-USD 5-minute paper bot (moving-average crossover on Coinbase candles). Kept runnable: python3 -m legacy.btc.bot --demo"""
import argparse
import copy
import getpass
import json
import math
import sqlite3
import sys
import threading
import time
import warnings
from datetime import datetime, timezone
from decimal import ROUND_DOWN
from pathlib import Path

from signaldesk.account import AccountMonitor, ReadOnlyAccount
from signaldesk.core import D, ROOT, Runner, positive, stamp
from signaldesk.quotes import coinbase
from signaldesk.server import serve

def validate_market(rows, quote, now):
    at = stamp(quote['at'])
    if not -10 <= now - at <= 120:
        raise ValueError('Quote is stale or its clock is ahead; no simulated orders placed')
    bid, ask = positive(quote['bidPrice']), positive(quote['askPrice'])
    if bid > ask:
        raise ValueError('Crossed order book')
    candles = sorted([(stamp(r[0]), positive(r[4])) for r in rows if stamp(r[0]) + 300 <= now])
    if len(candles) < 21:
        raise ValueError('Need at least 21 completed five-minute candles')
    candles = candles[-60:]
    if not 0 <= now - (candles[-1][0] + 300) <= 360:
        raise ValueError('Completed candles are stale')
    if any(b[0] - a[0] != 300 for a, b in zip(candles, candles[1:])):
        raise ValueError('Candle history contains gaps or duplicates')
    return candles, bid, ask, at

class Market:
    def __init__(self, demo=False):
        self.demo = demo
        self.step = 0

    def get(self):
        if self.demo:
            self.step += 1
            now = time.time() + self.step * 300
            end = int(now // 300) * 300
            def price(i):
                return 80_000 * (1 + .04 * math.sin(i / 7))
            rows = [[end - (60 - i) * 300, 0, 0, 0, price(self.step - 60 + i), 1] for i in range(60)]
            p = price(self.step)
            return validate_market(rows, {'at': now, 'bidPrice': p * .9995, 'askPrice': p * 1.0005}, now)
        rows = coinbase('candles', {'granularity': 300})  # [time, low, high, open, close, volume], newest first
        t = coinbase('ticker', {})
        quote = {'at': datetime.fromisoformat(t['time'][:26].rstrip('Z')).replace(tzinfo=timezone.utc).timestamp(), 'bidPrice': t['bid'], 'askPrice': t['ask']}
        return validate_market(rows, quote, time.time())

class Engine(Runner):
    def __init__(self, db, market, capital=D('4000'), fee=D('.004'), slip=D('.001'), edge_check=None):
        self.lock = threading.RLock()
        self.edge_check, self.edge = edge_check, None
        self.market, self.fee, self.slip = market, fee, slip
        self.conn = sqlite3.connect(db, check_same_thread=False)
        self.conn.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, body TEXT NOT NULL)')
        self.conn.execute('CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY, time TEXT, side TEXT, qty TEXT, price TEXT, fee TEXT, pnl TEXT, reason TEXT)')
        saved = self.conn.execute('SELECT body FROM state WHERE id=1').fetchone()
        self.s = json.loads(saved[0]) if saved else dict(capital=str(capital), cash=str(capital), qty='0', basis='0', entry='0', mark='0', equity=str(capital), fees='0', realized='0', last_bar=0, last_entry=0, last_exit=0, history=[], candles=[], decision='Ready. Start the agent to collect data.', updated=None, status='Ready', source='demo' if market.demo else 'coinbase', fee_rate=str(fee), slippage=str(slip), demo_step=0)
        self.s.setdefault('last_exit', 0)
        if self.s['source'] != ('demo' if market.demo else 'coinbase') or (D(self.s['fee_rate']), D(self.s['slippage']), D(self.s['capital'])) != (fee, slip, capital):
            raise ValueError('Database configuration differs. Use a new --db path for different settings.')
        self.market.step = self.s['demo_step']
        self.running = False  # Explicitly resume after every process restart.
        self.busy = False
        self.error = None
        self.wake = threading.Event()
        self.save()

    def save(self):
        self.conn.execute('INSERT OR REPLACE INTO state VALUES (1, ?)', (json.dumps(self.s),))
        self.conn.commit()

    def refresh_edge(self, spread):
        # Time Machine gate: re-run this rule on recent real candles at today's spread, hourly.
        if not self.edge_check or (self.edge and time.time() - self.edge['at'] < 3600):
            return
        try:
            self.edge = dict(self.edge_check(spread), at=time.time())
        except Exception as exc:
            self.edge = dict(error=f'{type(exc).__name__}: {exc}', at=time.time())

    def edge_block(self):
        e = self.edge
        if not self.edge_check:
            return None
        if not e or 'error' in e:
            return 'New entries blocked: strategy replay unavailable (%s).' % (e or {}).get('error', 'not run yet')
        if e['round_trips'] < 5:
            return f'New entries blocked: only {e["round_trips"]} trades in the 7-day replay, too few to show an edge.'
        if e['realized'] <= 0:
            return f'New entries blocked: this rule lost ${-e["realized"]:.2f} over {e["round_trips"]} trades in a 7-day replay of real candles after costs.'
        return None

    def tick(self):
        with self.lock:
            if self.busy:
                return
            self.busy = True
        try:
            data = self.market.get()
            self.refresh_edge((data[2] - data[1]) / data[2])
            with self.lock:
                previous = copy.deepcopy(self.s)
                try:
                    self.apply(*data)
                except Exception:
                    self.conn.rollback()
                    self.s = previous
                    raise
                self.error = None
        except Exception as exc:
            with self.lock:
                self.error = f'Market update blocked: {exc}'
        finally:
            with self.lock:
                self.busy = False

    def apply(self, candles, bid, ask, at):
        s = self.s
        closes = [v for _, v in candles]
        fast, slow = sum(closes[-5:]) / 5, sum(closes[-20:]) / 20
        bar = candles[-1][0]
        qty, cash, basis = D(s['qty']), D(s['cash']), D(s['basis'])
        equity = cash + qty * bid
        risk_block = equity <= D(s['capital']) * D('.95')
        spread = (ask - bid) / ask
        reason, side = 'No entry: short trend is below the long trend.', None
        # Exit checks continue while new entries are risk-blocked.
        if qty:
            entry = D(s['entry'])
            if bid <= entry * D('.98'):
                side, reason = 'SELL', 'Two percent stop threshold crossed.'
            elif bid >= entry * D('1.04'):
                side, reason = 'SELL', 'Four percent profit threshold crossed.'
            elif fast < slow:
                side, reason = 'SELL', 'Five-bar average fell below the twenty-bar average.'
            else:
                reason = 'Hold: trend remains positive; exit thresholds not reached.'
        elif risk_block:
            reason = 'New entries blocked: equity is at least 5% below starting capital.'
        elif spread > D('.005'):
            reason = 'New entry blocked: bid/ask spread exceeds 0.5%.'
        elif fast > slow and self.edge_block():
            reason = self.edge_block()
        elif fast > slow and bar > max(s['last_entry'], s['last_exit']):
            side, reason = 'BUY', 'Five-bar average is above the twenty-bar average.'
        elif fast > slow:
            reason = 'Wait: already entered or exited on this completed candle.'
        if side == 'BUY':
            price = ask * (1 + self.slip)
            allocation = min(cash, equity * D('.20'))
            qty = (allocation / (price * (1 + self.fee))).quantize(D('.00000001'), rounding=ROUND_DOWN)
            if qty > 0:
                notional = qty * price
                fee = notional * self.fee
                basis = notional + fee
                cash -= basis
                s.update(entry=str(price), last_entry=bar)
                self.trade(side, qty, price, fee, D(0), reason)
            else:
                side, reason = None, 'Balance is too small for the simulator quantity precision.'
        elif side == 'SELL':
            price = bid * (1 - self.slip)
            fee = qty * price * self.fee
            proceeds = qty * price - fee
            pnl = proceeds - basis
            cash += proceeds
            s['realized'] = str(D(s['realized']) + pnl)
            self.trade(side, qty, price, fee, pnl, reason)
            qty, basis = D(0), D(0)
            s.update(entry='0', last_exit=bar)
        equity = cash + qty * bid
        s.update(cash=str(cash), qty=str(qty), basis=str(basis), mark=str(bid), equity=str(equity), last_bar=bar, decision=reason, updated=at, status='Holding' if qty else 'Watching', candles=[[t, float(v)] for t, v in candles], fast=float(fast), slow=float(slow), demo_step=self.market.step)
        s['history'] = (s['history'] + [[time.time(), float(equity)]])[-400:]
        self.save()

    def trade(self, side, qty, price, fee, pnl, reason):
        self.s['fees'] = str(D(self.s['fees']) + fee)
        self.conn.execute('INSERT INTO trades(time,side,qty,price,fee,pnl,reason) VALUES(?,?,?,?,?,?,?)', (datetime.now(timezone.utc).isoformat(), side, str(qty), str(price), str(fee), str(pnl), reason))

    def snapshot(self):
        with self.lock:
            out = dict(self.s, running=self.running, busy=self.busy, error=self.error,
                       edge={k: str(v) for k, v in self.edge.items()} if self.edge else None)
            rows = self.conn.execute('SELECT * FROM trades ORDER BY id DESC LIMIT 101').fetchall()
            out['trades'] = [dict(zip(['id','time','side','qty','price','fee','pnl','reason'], r)) for r in rows[:100]]
            out['more'] = len(rows) > 100
            return out

    @property
    def interval(self):
        return 5 if self.market.demo else 60

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--demo', action='store_true', help='Synthetic prices; separate database. Never silently falls back to demo.')
    parser.add_argument('--account-readonly', action='store_true', help='Prompt securely in Terminal for a read-only WazirX key; never places real orders')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--db')
    parser.add_argument('--capital', type=positive, default=D('4000'), help='Simulated USD')
    parser.add_argument('--fee', type=positive, default=D('.006'), help='Assumed fee per side (about Coinbase entry-tier maker); not a fee quote')
    parser.add_argument('--slippage', type=positive, default=D('.001'))
    args = parser.parse_args()
    if args.fee >= 1 or args.slippage >= 1:
        parser.error('Fee and slippage must be less than 1')
    data = ROOT / 'data'
    data.mkdir(exist_ok=True)
    def live_edge(spread):
        from . import backtest  # replays this same rule on the last 7 days of real candles
        return backtest.replay(backtest.history(7), spread, capital=args.capital, fee=args.fee, slip=args.slippage)
    engine = Engine(args.db or data / ('demo.sqlite' if args.demo else 'paper.sqlite'), Market(args.demo), args.capital, args.fee, args.slippage, edge_check=None if args.demo else live_edge)
    account = AccountMonitor()
    if args.account_readonly:
        if not sys.stdin.isatty():
            parser.error('--account-readonly requires an interactive Terminal with hidden input')
        warnings.simplefilter('error', getpass.GetPassWarning)
        print('Use a read-only key. Leave trading, transfer and withdrawal permissions disabled.')
        key = getpass.getpass('WazirX API key (hidden): ')
        secret = getpass.getpass('WazirX API secret (hidden): ')
        account = AccountMonitor(ReadOnlyAccount(key, secret))
        del key, secret
    serve({'crypto': (engine, 'index.html', 'app.js')}, args.port, account, static=Path(__file__).parent / 'static')


if __name__ == '__main__':
    main()
