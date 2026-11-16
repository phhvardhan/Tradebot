#!/usr/bin/env python3
"""Opening-range-breakout day trader. With Alpaca keys in .env every buy and sell is a real order on the Alpaca PAPER account (class Alpaca below);
without them, fills are simulated. A fourth market on the same port, built on the trader-entry / trader-exit skills' rules.

Each session: the first `or_minutes` (15) set the range. The first 5-minute bar that closes above the range high buys, as long as the
entry window (`entries_until`) is open and the live price is still above the range. The stop is the middle of the range (trader-exit's
default), the target is `reward_risk` × that risk, the position is sized so the loss at the stop is at most `risk` dollars, and whatever is
still open is sold at `flat_by`. Once up `breakeven_r` R the stop moves to the entry price. One trade per symbol per day, `max_trades` a day,
no new trades once down `daily_loss` on the day. Data: Yahoo 5-minute bars, one chart call per symbol per check, parsed by the scanner skill.
Not built: short side, replay/backtest (Yahoo keeps 60 days of 5-minute bars), volume confirmation, the 1-minute chart.
"""
import copy
import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from decimal import InvalidOperation, ROUND_DOWN
from pathlib import Path

from .core import D, ROOT, Runner, utcnow
from .quotes import clock, notify_mac, scan

SLIP = D('.0005')
GAP = D('.005')  # a breakout whose live price is already this far over the breakout close is not chased
DEFAULT_RULES = dict(or_minutes='15', entries_until='11:30', flat_by='15:55', risk='50', per_trade='1000', reward_risk='2', breakeven_r='1', max_trades='3', daily_loss='100')
HHMM = re.compile(r'^(09|1[0-5]):[0-5]\d$')

def fetch(symbols, now):
    """{SYM: today's 5-minute series, live price, quote time} plus per-symbol problems."""
    out, problems = {}, {}
    for sym in symbols:
        try:
            series = scan.parse_chart(scan.yahoo_get(scan.chart_path(sym, '1d', '5m')), sym)
            price, at = series['meta'].get('regularMarketPrice'), series['meta'].get('regularMarketTime')
            if not price or price <= 0 or not at:
                raise ValueError('no price')
            out[sym] = dict(series=series, price=D(str(price)), at=float(at))
        except Exception as exc:
            problems[sym] = f'No quote: {exc}'
        time.sleep(scan.PAUSE)
    return out, problems

class Alpaca:
    """The day agent's orders at Alpaca paper. A buy is a bracket (market entry, the stop and target as standing orders that work even if this
    computer stops); the book records Alpaca's fill prices. Every order is appended to data/orders-alpaca.jsonl (the desk shows it), and
    the logged buy carries the position, so a book that lost it (a rolled-back check) adopts it again. Errors are raised as ValueError."""
    def __init__(self, log_path, get=None, send=None, sleep=time.sleep):
        from .desk import alpaca_get, alpaca_send  # desk.py owns the Alpaca transport; imported late so day.py loads without it
        self.get, self.send, self.sleep, self.log_path = get or alpaca_get, send or alpaca_send, sleep, log_path

    def log(self, **row):
        with open(self.log_path, 'a') as f:
            f.write(json.dumps(dict(at=time.time(), event='day', mode='day', **row), default=str) + '\n')

    def held(self):
        """{symbol: long whole-or-fractional qty} for everything the account holds, whoever bought it."""
        return {p['symbol']: D(str(p['qty'])) for p in self.get('/v2/positions') if p.get('side', 'long') == 'long'}

    def filled(self, oid):
        """Wait up to ~5 s for a market order; returns the order once filled. Unfilled: cancel, then use whatever did fill."""
        for _ in range(10):
            o = self.get(f'/v2/orders/{oid}', dict(nested='true'))
            if o['status'] == 'filled':
                return o
            if o['status'] in ('canceled', 'expired', 'rejected'):
                break
            self.sleep(.5)
        else:
            try:
                self.send('DELETE', f'/v2/orders/{oid}')
            except ValueError:
                pass  # filled between the last read and the cancel
            o = self.get(f'/v2/orders/{oid}', dict(nested='true'))
        if D(str(o.get('filled_qty') or 0)) <= 0:
            raise ValueError(f'order {o["status"]}, nothing filled')
        return o  # ponytail: a partial fill keeps legs sized for the full order; Alpaca trims them, the book uses filled_qty

    def buy(self, sym, qty, stop, target, day, position):
        body = dict(symbol=sym, qty=str(qty), side='buy', type='market', time_in_force='gtc', order_class='bracket', client_order_id=f'sd-day-buy-{sym}-{day}',
                    take_profit=dict(limit_price=str(target)), stop_loss=dict(stop_price=str(stop)))
        try:
            o = self.filled(self.send('POST', '/v2/orders', body)['id'])
        except ValueError as exc:
            self.log(day=day, symbol=sym, side='buy', qty=str(qty), ok=False, error=str(exc), request=body)
            raise
        legs = {('stop' if l.get('type') in ('stop', 'stop_limit') else 'target'): l['id'] for l in o.get('legs') or ()}
        qty, price = D(str(o['filled_qty'])), D(str(o['filled_avg_price']))
        position = dict(position, qty=str(qty), entry=str(price), cost=str(qty * price), alpaca=dict(id=o['id'], **legs))
        self.log(day=day, symbol=sym, side='buy', qty=str(qty), ok=True, id=o['id'], status=o['status'], price=str(price), plan=dict(stop=str(stop), target=str(target)), position=position)
        return position

    def exit_fill(self, p):
        """(reason, qty, price) when the bracket's stop or target already sold the position at Alpaca, else None."""
        ids = p.get('alpaca') or {}
        for k in ('stop', 'target'):
            if ids.get(k):
                o = self.get(f'/v2/orders/{ids[k]}')
                if o['status'] == 'filled':
                    return k, D(str(o['filled_qty'])), D(str(o['filled_avg_price']))
        return None

    def move_stop(self, p, price):
        self.send('PATCH', f'/v2/orders/{p["alpaca"]["stop"]}', dict(stop_price=str(price)))

    def sell(self, sym, p, day, reason):
        """Cancel the bracket's standing orders, then sell what Alpaca holds at market. Returns (qty, price), or None when a leg sold it first."""
        for o in self.get('/v2/orders', dict(status='open', symbols=sym)):
            try:
                self.send('DELETE', f'/v2/orders/{o["id"]}')
            except ValueError:
                pass  # it filled or was cancelled with its pair
        for _ in range(10):
            if not self.get('/v2/orders', dict(status='open', symbols=sym)):
                break
            self.sleep(.5)
        else:
            raise ValueError(f'the stop and target orders for {sym} did not cancel in time')
        qty = self.held().get(sym, D(0))
        if qty <= 0:
            return None
        body = dict(symbol=sym, qty=str(qty), side='sell', type='market', time_in_force='day', client_order_id=f'sd-day-sell-{sym}-{day}-{int(time.time())}')
        try:
            o = self.filled(self.send('POST', '/v2/orders', body)['id'])
        except ValueError as exc:
            self.log(day=day, symbol=sym, side='sell', qty=str(qty), ok=False, error=str(exc), request=body)
            raise
        self.log(day=day, symbol=sym, side='sell', qty=str(o['filled_qty']), ok=True, id=o['id'], status=o['status'], price=str(o['filled_avg_price']), reason=reason)
        return D(str(o['filled_qty'])), D(str(o['filled_avg_price']))

    def orphans(self, day):
        """Positions the agent bought today whose last logged event is that buy: they belong in the book."""
        last = {}
        try:
            for line in Path(self.log_path).read_text().splitlines():
                r = json.loads(line) if line.strip() else {}
                if r.get('event') == 'day' and r.get('ok') and r.get('day') == day:
                    last[r['symbol']] = r
        except (OSError, ValueError):
            return {}
        return {k: r['position'] for k, r in last.items() if r['side'] == 'buy' and r.get('position')}

def mins(hhmm):
    h, m = hhmm.split(':')
    return int(h) * 60 + int(m)

class DayEngine(Runner):
    interval = 60

    def __init__(self, db, symbols, capital=D('5000'), fetch=fetch, status=None, notify=None, broker=None):
        self.lock, self.wake = threading.RLock(), threading.Event()
        self.broker = broker  # Alpaca: real paper orders; None: simulated fills
        self.fetch, self.status, self.notify = fetch, status or clock.market_status, notify
        self.running = self.busy = False
        self.error, self.heartbeat_path, self.market_name, self.pending, self.unpriced = None, None, 'day', [], {}
        self.conn = sqlite3.connect(db, check_same_thread=False)
        self.conn.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, body TEXT NOT NULL)')
        self.conn.execute('CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY, time TEXT, symbol TEXT, side TEXT, qty TEXT, price TEXT, fee TEXT, pnl TEXT, reason TEXT)')
        saved = self.conn.execute('SELECT body FROM state WHERE id=1').fetchone()
        self.s = json.loads(saved[0]) if saved else dict(DEFAULT_RULES, capital=str(capital), cash=str(capital), equity=str(capital), realized='0', symbols=list(symbols),
            positions={}, traded={}, ranges={}, decisions={}, day=None, day_start=str(capital), market={}, history=[], daily_pnl={}, alerts=[], rule_log=[], updated=None, status='Ready',
            decision='Ready. Start the agent; it watches the first 15 minutes of each session.')
        if D(self.s['capital']) != capital:
            raise ValueError('Database capital differs. Use a new --db path for different settings.')
        self.save()

    rule = lambda self, k: D(self.s[k])

    def rules(self):
        return {k: self.s[k] for k in DEFAULT_RULES}

    def set_rules(self, body):
        """Change rules from the dashboard. Returns an error message, or None once saved."""
        checks = dict(or_minutes=(lambda x: 5 <= x <= 60 and x % 5 == 0, 'Opening range must be a multiple of 5 minutes, 5 to 60'),
                      risk=(lambda x: x > 0, 'Max loss per trade must be above 0'), per_trade=(lambda x: x >= 0, 'Per position must be 0 (no cap) or more'),
                      reward_risk=(lambda x: D('.5') <= x <= 10, 'Reward:risk must be between 0.5 and 10'), breakeven_r=(lambda x: 0 <= x <= 10, 'Break-even must be 0 (off) to 10 R'),
                      max_trades=(lambda x: 0 <= x <= 40 and x == x.to_integral_value(), 'Trades per day must be a whole number, 0 (no limit) to 40'),
                      daily_loss=(lambda x: x >= 0, 'Daily loss limit must be 0 (off) or more'))
        new = {}
        try:
            for k, v in body.items():
                if k in checks:
                    x = D(str(v))
                    if not x.is_finite() or not checks[k][0](x):
                        return checks[k][1] + '.'
                    new[k] = str(x)
                elif k in ('entries_until', 'flat_by'):
                    if not isinstance(v, str) or not HHMM.match(v):
                        return f'{k} must be a time between 09:00 and 15:59, like 11:30.'
                    new[k] = v
                elif k == 'symbols':
                    syms = list(dict.fromkeys(str(s).strip().upper() for s in v if str(s).strip())) if isinstance(v, list) else None
                    if not syms or len(syms) > 40 or not all(re.fullmatch(r'[A-Z0-9][A-Z0-9.-]{0,9}', s) for s in syms):
                        return 'Watchlist needs 1 to 40 symbols, letters, digits, dot or dash only.'
                    new[k] = syms
                else:
                    return f'Unknown rule: {k}.'
        except (InvalidOperation, TypeError, AttributeError):
            return 'Rules must be numbers.'
        merged = {**self.s, **new}
        if merged['entries_until'] >= merged['flat_by']:
            return 'The entry window must end before the flat-by time.'
        with self.lock:
            log = self.s.setdefault('rule_log', [])
            for k, v in new.items():
                if self.s.get(k) != v:
                    log.append(dict(at=time.time(), text=f'{k}: {self.s.get(k)} → {v}'))
            self.s['rule_log'] = log[-100:]
            self.s.update(new)
            self.save()

    def save(self):
        self.conn.execute('INSERT OR REPLACE INTO state VALUES (1, ?)', (json.dumps(self.s),))
        self.conn.commit()

    def trade(self, sym, side, qty, price, pnl, reason):
        self.conn.execute('INSERT INTO trades(time,symbol,side,qty,price,fee,pnl,reason) VALUES(?,?,?,?,?,?,?,?)',
                          (datetime.now(timezone.utc).isoformat(), sym, side, str(qty), str(price), '0', str(pnl), reason))
        self.alert(f'Day trade: {side} {sym} {qty:.4f} at {price:.2f}' + (f', P&L {pnl:+.2f}' if side == 'SELL' else '') + f'. {reason}')

    def alert(self, text):
        self.s['alerts'] = (self.s['alerts'] + [dict(at=time.time(), text=text)])[-50:]
        self.pending.append(text)

    def apply(self, status, data):
        s, pos, day, trading = self.s, self.s['positions'], status['date'], status['open']
        now = datetime.strptime(status['now_new_york'], '%Y-%m-%d %H:%M')  # naive New York time, as the scanner's bar helpers want it
        cash, realized = D(s['cash']), D(s['realized'])
        prices = {k: v['price'] for k, v in data.items()}
        equity = lambda: cash + sum(D(p['qty']) * prices.get(k, D(p['last'])) for k, p in pos.items())
        if day != s['day']:
            s['day'], s['day_start'], s['ranges'] = day, str(cash + sum(D(p['qty']) * D(p['last']) for p in pos.values())), {}
        n, decisions = int(self.rule('or_minutes')) // scan.BAR_MIN, {}
        broker, held = self.broker, None  # held: Alpaca's positions, read at most once a check
        try:
            lost = {k: v for k, v in broker.orphans(day).items() if k not in pos} if broker and trading else {}
            held = broker.held() if lost else None
        except ValueError:
            lost = {}  # Alpaca unreachable: try again at the next check
        for sym, p in lost.items():
            if sym in held:  # bought at Alpaca, missing from the book: a check that failed after the order rolled the book back
                pos[sym], s['traded'][sym] = p, day
                cash -= D(p['cost'])
                self.alert(f'{sym} day trade bought at Alpaca was missing from the book; it is tracked again.')
        # flat_by is set for a 16:00 close; an early close (13:00) moves it by the same amount, so 15:55 becomes 12:55
        flat = mins(status.get('closes', '16:00')) - mins(now.strftime('%H:%M')) <= mins('16:00') - mins(s['flat_by'])
        def close(sym, p, qty, fill, reason):
            nonlocal cash, realized
            pnl = qty * fill - D(p['cost'])
            cash += qty * fill; realized += pnl
            self.trade(sym, 'SELL', qty, fill, pnl, reason)
            del pos[sym]
            decisions[sym] = reason
        for sym, p in list(pos.items()):
            at_alpaca = bool(broker and p.get('alpaca'))
            if at_alpaca and trading:
                try:
                    done = broker.exit_fill(p)
                except ValueError as exc:
                    decisions[sym] = f'Holding: Alpaca could not be read ({exc}); its stop and target orders still stand there.'
                    continue
                if done:
                    close(sym, p, done[1], done[2], f'{done[0].title()} order filled at Alpaca at {done[2]:.2f}.')
                    continue
            if sym not in prices:
                decisions[sym] = 'Holding: no fresh price, exits not checked.' + (' Its stop and target orders stand at Alpaca.' if at_alpaca else '')
                continue
            price, qty, entry, risk = prices[sym], D(p['qty']), D(p['entry']), D(p['entry']) - D(p['stop0'])
            p['last'], p['high'] = str(price), str(max(D(p['high']), price))
            if not trading:
                decisions[sym] = 'Holding overnight: the market closed before it could be sold; it sells at the first check of the next session.'
                continue
            if self.rule('breakeven_r') and not p['be'] and price >= entry + risk * self.rule('breakeven_r'):
                be = entry.quantize(D('.01'), rounding=ROUND_DOWN) if at_alpaca else entry
                try:
                    if at_alpaca:
                        broker.move_stop(p, be)
                    p['be'], p['stop'] = True, str(be)
                except ValueError as exc:
                    decisions[sym] = f'Break-even: moving the stop at Alpaca to {be:.2f} failed ({exc}); trying again at the next check.'
                    continue
            r = (price - entry) / risk if risk else D(0)
            if price <= D(p['stop']):
                reason = f'{"Break-even stop" if p["be"] else "Stop"}: {price:.2f} is at or under {D(p["stop"]):.2f} ({r:+.1f}R).'
            elif price >= D(p['target']):
                reason = f'Target: {price:.2f} reached {D(p["target"]):.2f} ({r:+.1f}R).'
            elif flat or p['opened'] != day:
                reason = f'Flat by {s["flat_by"]}: day trade closed at {price:.2f} ({r:+.1f}R).' if p['opened'] == day else f'Left over from {p["opened"]}: sold at the first check ({r:+.1f}R).'
            else:
                decisions[sym] = f'Holding: {r:+.1f}R, stop {D(p["stop"]):.2f}, target {D(p["target"]):.2f}.'
                continue
            if not at_alpaca:
                close(sym, p, qty, price * (1 - SLIP), reason)
                continue
            try:
                got = broker.sell(sym, p, day, reason) or broker.exit_fill(p)
            except ValueError as exc:
                decisions[sym] = f'{reason} The sale at Alpaca failed ({exc}); trying again at the next check.'
                if not p.get('sell_error'):
                    p['sell_error'] = True
                    self.alert(f'Selling {sym} at Alpaca failed: {exc}. The agent retries every minute; check Alpaca.')
                continue
            if got and len(got) == 3:
                close(sym, p, got[1], got[2], f'{got[0].title()} order filled at Alpaca at {got[2]:.2f}.')
            elif got:
                close(sym, p, got[0], got[1], reason + ' Sold at Alpaca.')
            else:
                close(sym, p, qty, price, reason + ' Alpaca no longer held it; recorded at the last price.')
        today = equity() - D(s['day_start'])
        for sym in s['symbols']:
            if sym in pos or sym in decisions:
                continue
            if not trading:
                decisions[sym] = 'Market closed. The next range forms in the first %s minutes after the open.' % s['or_minutes']
                continue
            if sym not in data:
                continue
            bars = scan.session_bars(data[sym]['series'], now.date(), now)
            if len(bars) < n:
                decisions[sym] = f'Opening range forming: {len(bars)} of {n} bars.'
                continue
            rng = bars[:n]
            oh, ol = max(b['h'] if b['h'] is not None else b['c'] for b in rng), min(b['l'] if b['l'] is not None else b['c'] for b in rng)
            hit = next((b for b in bars[n:] if b['c'] > oh), None)
            s['ranges'][sym] = dict(high=round(oh, 2), low=round(ol, 2), bars=len(bars), hit=hit['start'].strftime('%H:%M') if hit else None)
            price = prices[sym]
            if flat:
                decisions[sym] = f'No new trades: past the flat-by time ({s["flat_by"]}, earlier on an early close).'
            elif s['traded'].get(sym) == day:
                decisions[sym] = 'Done for today: one trade per symbol a day.'
            elif not hit:
                decisions[sym] = f'No breakout: no 5-minute close above the range high {oh:.2f} yet.'
            elif hit['start'].strftime('%H:%M') >= s['entries_until']:
                decisions[sym] = f'Signal at {hit["start"]:%H:%M}, but entries blocked: after {s["entries_until"]}, no new day trades.'
            elif (now - hit['start']).total_seconds() > 2 * scan.BAR_MIN * 60:  # the entry is the bar's close; a bar older than the one before last was missed, not a setup
                decisions[sym] = f'Signal at {hit["start"]:%H:%M} was missed (the agent was not watching then); not chasing it {int((now - hit["start"]).total_seconds() // 60)} minutes later.'
            elif self.rule('daily_loss') and today <= -self.rule('daily_loss'):
                decisions[sym] = f'Signal, but entries blocked: down ${-today:.2f} today (limit ${self.rule("daily_loss"):.0f}).'
            elif self.rule('max_trades') and sum(v == day for v in s['traded'].values()) >= self.rule('max_trades'):
                decisions[sym] = f'Signal, but entries blocked: {self.rule("max_trades"):.0f} trades today (limit {self.rule("max_trades"):.0f}).'
            elif price < D(str(oh)):
                decisions[sym] = f'Signal, but price {price:.2f} is back under the range high {oh:.2f}.'
            elif price > D(str(hit['c'])) * (1 + GAP):
                decisions[sym] = f'Signal, but price {price:.2f} is over {GAP:.1%} above the {hit["c"]:.2f} breakout close; not chasing.'
            else:
                entry = price * (1 + SLIP)
                stop = (D(str(oh + ol)) / 2).quantize(D('.01'), rounding=ROUND_DOWN)
                risk = entry - stop
                if risk <= entry * D('.0005'):
                    decisions[sym] = f'Signal, but the range {ol:.2f}-{oh:.2f} is too narrow for a stop outside the spread.'
                    continue
                cap = min(self.rule('per_trade') or cash, cash)
                qty = min(self.rule('risk') / risk, cap / entry).quantize(D('.0001'), rounding=ROUND_DOWN)
                cost = qty * entry
                if cost < 1:
                    decisions[sym] = f'Signal, but no cash left (${cash:.2f}).'
                    continue
                target = entry + risk * self.rule('reward_risk')
                new = dict(qty=str(qty), entry=str(entry), cost=str(cost), stop=str(stop), stop0=str(stop), target=str(target), last=str(price), high=str(price), opened=day, be=False)
                if broker:
                    qty = qty.to_integral_value(rounding=ROUND_DOWN)  # bracket orders take whole shares
                    if qty < 1:
                        decisions[sym] = f'Signal, but the size rounds to 0 whole shares at {entry:.2f} (max loss ${self.rule("risk")}, per position ${self.rule("per_trade")}).'
                        continue
                    try:
                        held = broker.held() if held is None else held
                        if sym in held:
                            decisions[sym] = f'Signal, but Alpaca already holds {sym} (another agent\'s position); one owner per symbol.'
                            continue
                        target = target.quantize(D('.01'))
                        new = broker.buy(sym, int(qty), stop, target, day, dict(new, target=str(target)))
                    except ValueError as exc:
                        s['traded'][sym] = day  # one attempt per symbol a day: a refusal is not retried every minute
                        decisions[sym] = f'Signal, but the buy at Alpaca failed: {exc}'
                        self.alert(f'Day trade buy of {sym} at Alpaca failed: {exc}')
                        continue
                    qty, entry, cost = D(new['qty']), D(new['entry']), D(new['cost'])
                cash -= cost
                pos[sym] = new
                s['traded'][sym] = day
                decisions[sym] = reason = (f'Buy: the {hit["start"]:%H:%M} bar closed at {hit["c"]:.2f}, above the {s["or_minutes"]}-minute range high {oh:.2f}. '
                                           f'Stop {stop:.2f} (range middle, −${qty * (entry - stop):.2f}), target {target:.2f} ({self.rule("reward_risk")}R).' + (' Bought at Alpaca; the stop and target are orders there.' if broker else ''))
                self.trade(sym, 'BUY', qty, entry, D(0), reason)
        eq = equity()
        s.update(cash=str(cash), realized=str(realized), equity=str(eq), decisions=decisions, updated=time.time(), status=f'{len(pos)} open' if pos else 'Watching',
                 decision=f'{sum(v.startswith(("Buy", "Stop", "Break-even", "Target", "Flat", "Left")) for v in decisions.values())} actions, {len(pos)} open. Today {eq - D(s["day_start"]):+.2f}.')
        s['quotes'] = {k: str(v['price']) for k, v in data.items()}
        h = s['history']
        if not h or time.time() - h[-1][0] >= (300 if trading else 3600):
            h.append([time.time(), float(eq)])
        else:
            h[-1][1] = float(eq)
        s['history'] = h[-1200:]
        if trading or day in s['daily_pnl']:
            s['daily_pnl'][day] = str(eq - D(s['day_start']))
        self.save()
        sent, self.pending = self.pending, []
        for text in sent:
            if self.notify:
                self.notify(text)

    def tick(self):
        with self.lock:
            if self.busy:
                return
            self.busy = True
        try:
            now = utcnow()
            status = self.status(now)
            if not status.get('calendar_known'):
                raise ValueError('NYSE calendar for this year is not in the trader-alerts skill; no trades.')
            # Bars are only fetched in session or while something is still held; a closed market with nothing open costs no requests.
            data, problems = self.fetch(list(dict.fromkeys(self.s['symbols'] + list(self.s['positions']))), now) if status['open'] or self.s['positions'] else ({}, {})
            if status['open']:
                for sym in [k for k, v in data.items() if time.time() - v['at'] > 180]:
                    problems[sym] = f'Quote is stale ({time.time() - data.pop(sym)["at"]:.0f}s old); not traded.'
            self.unpriced = {s: self.unpriced.get(s, time.time()) for s in self.s['positions'] if s not in data} if status['open'] else {}
            with self.lock:
                previous = copy.deepcopy(self.s)
                try:
                    self.apply(status, data)
                    self.s['decisions'].update(problems)
                    self.s['market'] = status
                    self.save()
                except Exception:
                    self.conn.rollback()
                    self.s, self.pending = previous, []
                    raise
                self.error = None
            self.backup(status['date'])
        except Exception as exc:
            with self.lock:
                self.error = f'Market update blocked: {type(exc).__name__}: {exc}'
        finally:
            with self.lock:
                self.busy = False
            self.beat()

    def beat(self):
        dst = self.heartbeat_path  # read once: another thread may clear it mid-write
        if not dst:
            return
        hb = dict(market=self.market_name, at=time.time(), ok=self.error is None, error=self.error, last_ok=self.s.get('updated'), running=self.running,
                  positions=len(self.s['positions']), market_open=bool((self.s.get('market') or {}).get('open')), unpriced=self.unpriced, backup_error=getattr(self, 'backup_error', None), challenger_error=None)
        try:
            tmp = str(dst) + '.tmp'
            with open(tmp, 'w') as f:
                f.write(json.dumps(hb))
            os.replace(tmp, dst)
        except OSError:
            pass

    def snapshot(self):
        with self.lock:
            rows = self.conn.execute('SELECT * FROM trades ORDER BY id DESC LIMIT 101').fetchall()
            out = dict(self.s, running=self.running, busy=self.busy, error=self.error, more=len(rows) > 100, alpaca=self.broker is not None)
            out['trades'] = [dict(zip(['id', 'time', 'symbol', 'side', 'qty', 'price', 'fee', 'pnl', 'reason'], r)) for r in rows[:100]]
            return out

def build_day(db=None, symbols=None, **kw):
    from .swing import WATCHLIST
    (ROOT / 'data').mkdir(exist_ok=True)
    return DayEngine(db or ROOT / 'data' / 'day.sqlite', symbols or WATCHLIST, notify=notify_mac, **kw)
