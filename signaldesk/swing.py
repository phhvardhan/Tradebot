"""The swing engine: a 20-day-breakout paper trader for US stocks (daily Yahoo closes) and crypto (Coinbase daily candles). Simulated fills only.
Positions are held for days or weeks until the stop (-10%), the optional trailing stop or the target (+25%); see DEFAULT_RULES. The same class runs the frozen challenger
portfolio (CHALLENGER.md) and the 5-year replay that gates entries."""
import copy
import gzip
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta, timezone
from decimal import InvalidOperation, ROUND_DOWN
from pathlib import Path

from . import congress as congress_data
from . import news
from .core import D, ROOT, SKILLS, Runner, utcnow
from .quotes import clock, coinbase_closes, coinbase_quotes, crypto_status, history, notify_mac, scan, sp500_since, yahoo_quotes

SECTOR = {s: k for k, v in dict(tech='AAPL MSFT NVDA AMZN GOOGL META TSLA AMD AVGO NFLX', fin='JPM V MA', health='UNH LLY', staples='COST WMT KO PEP', energy='XOM').items() for s in v.split()}

WATCHLIST = 'AAPL MSFT NVDA AMZN GOOGL META TSLA AMD AVGO NFLX JPM V MA UNH LLY COST WMT KO PEP XOM'.split()

CRYPTO = 'BTC ETH SOL XRP DOGE ADA AVAX LINK LTC DOT'.split()

STALE = 180

GAP = D('.05')

JUMP = D('.40')  # live only: a held price this far from the last price seen, with no split reported, is not acted on for a session

DEFAULT_RULES = dict(stop=D('.10'), target=D('.25'), alert_at=D('.20'), max_new=D(3))

# Entry building blocks: the trader-scanner setups a recipe can buy on (rule `entry`, comma-separated, first match wins). breakout_20d is the original rule.
ENTRIES = dict(breakout_20d='Yesterday closed above the highest of the 20 closes before it; today\'s price must still be above that level.',
               bull_flag='Up more than 10% over the 10 sessions before the last 5, then the last 5 closes held within a 5% range.',
               reclaim_ma20='The previous close was under its 20-day average and the last close is back above it.',
               oversold='14-day RSI under 30.',
               oversold_bounce='RSI was under 30 yesterday and the last close is up from the one before.')


CLOSING_WINDOW = 10

sha = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]

CODE = dict(swing_py=sha(__file__), scan_py=sha(SKILLS / 'trader-scanner' / 'scan.py'), alerts_py=sha(SKILLS / 'trader-alerts' / 'alerts.py'))

class StockEngine(Runner):
    interval = 60
    # Rules live in the saved state so the dashboard can change them; per_trade 0 = no cap (all cash), daily_loss 0 = off.
    per_trade, daily_loss, stop, target, alert_at, max_new = (property(lambda self, k=k: D(self.s[k])) for k in ('per_trade', 'daily_loss', 'stop', 'target', 'alert_at', 'max_new'))
    congress_days = property(lambda self: D(self.s['congress_days']))
    exit_low = property(lambda self: D(self.s['exit_low']))  # sell when the price closes under the lowest close of the last N days, 0 = off
    symbols = property(lambda self: self.s['symbols'])
    gate = property(lambda self: self.s['gate'])
    trail = property(lambda self: D(self.s['trail']))  # sell when the price falls this far below its highest since entry, 0 = off
    breakeven = property(lambda self: D(self.s['breakeven']))  # once up this much, never sell at a loss: the stop moves to the entry price, 0 = off
    max_sector = property(lambda self: D(self.s['max_sector']))  # most positions held in one sector, 0 = no limit
    max_open = property(lambda self: D(self.s['max_open']))  # most positions held at once, 0 = no limit
    entry = property(lambda self: self.s['entry'])  # which scanner setups buy, 'a,b' (see ENTRIES)

    def __init__(self, db, quotes, symbols, capital=D('5000'), per_trade=D('500'), daily_loss=D('400'),
                 stop=D('.20'), target=D('1'), alert_at=D('.80'), slip=D('.0005'), edge_check=None, notify=None, status=None, source='yahoo', gate=True, congress=None, congress_days=D(0), max_open=D(0), trail=D(0), max_new=D(0), exit_low=D(0), breakeven=D(0), max_sector=D(0), news=None, bench=None, entry='breakout_20d'):
        self.lock = threading.RLock()
        self.quotes, self.notify, self.status, self.slip = quotes, notify, status or clock.market_status, slip
        self.news, self.bench = news, bench  # (symbol) -> note, and (first trade day) -> S&P 500 return; both optional, both information only
        self.congress = congress  # (symbol, day, days) -> bool; None where the rule does not apply (crypto)
        self.edge_check, self.edge = edge_check, None  # gate=False: replay is reported, never blocks
        self.edge_busy, self.edge_gen, self.pending, self.db, self.backed, self.journal = False, 0, [], str(db), None, None  # journal: a list when a replay wants every trade with its day
        self.followers, self.name, self.audit, self.audit_path, self.last_dec, self.last_prob, self.snapped = [], 'baseline', [], None, {}, {}, set()
        self.heartbeat_path, self.market_name = None, 'stocks'  # a small file rewritten after every check (and while paused), read by the watchdog
        self.gate_now = None  # the replay gate's verdict frozen for one live check ('' = open), shared with the challenger; None = ask edge_block
        self.backup_error = None
        self.unpriced = {}  # held symbol -> since when it has had no usable price while the market is open; the watchdog alerts on it
        self.extra = []  # symbols quoted but never traded: what the real account holds (desk.py), so their rules can be checked
        self.near_close = True  # live checks set this from the clock; a replay sets it on each day's closing price
        self.prev = None  # replay only: {symbol: the previous price sample today}; a level crossed between two samples fills at the level, as a minute-by-minute check would
        self.running = self.busy = False
        self.error = None
        self.wake = threading.Event()
        self.conn = sqlite3.connect(db, check_same_thread=False)
        self.conn.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, body TEXT NOT NULL)')
        self.conn.execute('CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY, time TEXT, symbol TEXT, side TEXT, qty TEXT, price TEXT, fee TEXT, pnl TEXT, reason TEXT)')
        saved = self.conn.execute('SELECT body FROM state WHERE id=1').fetchone()
        fixed = dict(capital=str(capital), slippage=str(slip))
        self.s = json.loads(saved[0]) if saved else dict(fixed, source=source, cash=str(capital), equity=str(capital), realized='0', fees='0',
            positions={}, last_entry={}, last_exit={}, day=None, day_start=str(capital), alerted_total=False, alerts=[], decisions={},
            market={}, history=[], updated=None, status='Ready', decision='Ready. Start the agent to collect data.')
        if any(D(self.s[k]) != D(v) for k, v in fixed.items()):
            raise ValueError('Database capital or slippage differs. Use a new --db path for different settings.')
        for k, v in dict(per_trade=per_trade, daily_loss=daily_loss, stop=stop, target=target, alert_at=alert_at, symbols=list(symbols), gate=gate, congress_days=congress_days, max_open=max_open, trail=trail, max_new=max_new, exit_low=exit_low, breakeven=breakeven, max_sector=max_sector, entry=entry).items():
            self.s.setdefault(k, v if k in ('symbols', 'gate') else str(v))  # a saved choice from the dashboard wins
        self.save()

    def rules(self):
        return dict(capital=D(self.s['capital']), slip=self.slip, entry=self.entry, **{k: getattr(self, k) for k in ('per_trade', 'daily_loss', 'stop', 'target', 'alert_at', 'max_open', 'trail', 'max_new', 'congress_days', 'exit_low', 'breakeven', 'max_sector')})

    def signal(self, closes):
        """The first entry setup of the recipe that the closes (oldest first) show, or None."""
        tags = scan.find_setups(closes)[0] if len(closes) >= 30 else []
        return next((t for t in self.entry.split(',') if t in tags), None)

    def set_rules(self, body, who='dashboard'):
        """Change rules from the dashboard (or the AI researcher, who='ai'). Returns an error message, or None once saved."""
        if self.s.get('frozen'):
            return 'These rules are frozen for the challenger test.'
        new, error = self.check_rules(body)
        if error:
            return error
        with self.lock:
            log = self.s.setdefault('rule_log', [])
            for k, v in new.items():
                if self.s.get(k) != v:
                    log.append(dict(at=time.time(), text=f'{k}: {self.s.get(k)} → {v}' + (' (AI researcher)' if who == 'ai' else '')))
            self.s['rule_log'] = log[-100:]
            self.s.update(new)
            self.edge, self.edge_gen = None, self.edge_gen + 1  # replay the new rules at the next check; a replay still running on the old ones is dropped
            self.save()

    @staticmethod
    def check_rules(body):
        """Validate a rules change without saving it: (the values as stored, None) or (None, the error message)."""
        checks = dict(per_trade=(lambda x: x >= 0, 'Per position must be 0 (no limit) or more'),
                      daily_loss=(lambda x: x >= 0, 'Daily loss limit must be 0 (off) or more'),
                      stop=(lambda x: 0 < x < 1, 'Stop must be between 0% and 100%'),
                      target=(lambda x: 0 < x <= 100, 'Target must be above 0%'),
                      alert_at=(lambda x: 0 < x <= 100, 'Alert must be above 0%'),
                      breakeven=(lambda x: 0 <= x < 1, 'Break-even trigger must be 0 (off) or between 0% and 100%'),
                      max_sector=(lambda x: 0 <= x <= 40 and x == x.to_integral_value(), 'Max per sector must be a whole number, 0 (no limit) to 40'),
                      trail=(lambda x: 0 <= x < 1, 'Trailing stop must be 0 (off) or between 0% and 100%'),
                      max_open=(lambda x: 0 <= x <= 40 and x == x.to_integral_value(), 'Max open positions must be a whole number, 0 (no limit) to 40'),
                      max_new=(lambda x: 0 <= x <= 40 and x == x.to_integral_value(), 'New buys per day must be a whole number, 0 (no limit) to 40'),
                      exit_low=(lambda x: 0 <= x <= 250 and x == x.to_integral_value(), 'Channel exit must be a whole number of days, 0 (off) to 250'),
                      congress_days=(lambda x: 0 <= x <= 365 and x == x.to_integral_value(), 'Congress filter must be a whole number of days, 0 (off) to 365'))
        new = {}
        try:
            for k, v in body.items():
                if k == 'entry':
                    tags = [t.strip() for t in (v.split(',') if isinstance(v, str) else v)]
                    if not 1 <= len(tags) <= len(ENTRIES) or any(t not in ENTRIES for t in tags) or len(set(tags)) != len(tags):
                        return None, 'Entry must be one or more of: ' + ', '.join(ENTRIES) + '.'
                    new[k] = ','.join(tags)
                elif k in checks:
                    x = D(str(v))
                    if not x.is_finite() or not checks[k][0](x):
                        return None, checks[k][1] + '.'
                    new[k] = str(x)
                elif k == 'symbols':
                    if not isinstance(v, list):
                        return None, 'Watchlist must be a list of symbols.'
                    syms = list(dict.fromkeys(str(s).strip().upper() for s in v if str(s).strip()))
                    if not 1 <= len(syms) <= 40 or not all(re.fullmatch(r'[A-Z0-9][A-Z0-9.-]{0,9}', s) for s in syms):
                        return None, 'Watchlist needs 1 to 40 symbols, letters, digits, dot or dash only.'
                    new[k] = syms
                elif k == 'gate':
                    new[k] = v is True
                else:
                    return None, f'Unknown rule: {k}.'
        except (InvalidOperation, TypeError, AttributeError):
            return None, 'Rules must be numbers.'
        return new, None

    def save(self):
        self.conn.execute('INSERT OR REPLACE INTO state VALUES (1, ?)', (json.dumps(self.s),))
        self.conn.commit()

    def alert(self, text):
        self.s['alerts'] = (self.s['alerts'] + [dict(at=time.time(), text=text)])[-50:]
        self.pending.append(text)  # sent once the state is saved, so a rolled-back check never notifies twice

    def trade(self, sym, side, qty, price, pnl, reason):
        tr = self.s.get('track')
        if tr:
            tr['entries'] += side == 'BUY'; tr['exits'] += side == 'SELL'; tr['wins'] += side == 'SELL' and pnl > 0; tr['realized'] += float(pnl)
        if self.journal is not None:
            self.journal.append(dict(day=self.s['day'], symbol=sym, side=side, qty=qty, price=price, pnl=pnl, reason=reason))
        self.conn.execute('INSERT INTO trades(time,symbol,side,qty,price,fee,pnl,reason) VALUES(?,?,?,?,?,?,?,?)',
                          (datetime.now(timezone.utc).isoformat(), sym, side, str(qty), str(price), '0', str(pnl), reason))

    def refresh_edge(self, day, wait=False):
        # Time Machine gate: replay this exact rule on the watchlist's last 5 years, once a day.
        # It runs on its own thread so exits keep being checked, and a failure is retried after an hour.
        e = self.edge
        if not self.edge_check or self.edge_busy or (e and (time.time() - e['at'] < 3600 if 'error' in e else e.get('day') == day)):
            return
        self.edge_busy, gen = True, self.edge_gen
        def run():
            try:
                out = dict(self.edge_check(), day=day, at=time.time())
                self.log_experiment(out)
            except Exception as exc:
                out = dict(error=f'{type(exc).__name__}: {exc}', day=day, at=time.time())
            with self.lock:
                if gen == self.edge_gen:
                    self.edge = out
                self.edge_busy = False
        t = threading.Thread(target=run, daemon=True)
        t.start()
        if wait:
            t.join()

    def log_experiment(self, result):
        # One line per replay: the rules, the universe, the period and the outcome, so any number on the dashboard can be reproduced and compared later.
        if self.db == ':memory:':
            return
        row = dict(at=datetime.now(timezone.utc).isoformat(), market=self.s.get('source'), symbols=self.symbols, rules={k: str(v) for k, v in self.rules().items()},
                   assumptions='daily bars walked open, low/high, close (low first on up days); levels crossed between samples fill at the level, gaps at the open; slippage on every fill; watchlist chosen with hindsight', code=CODE, result=result)
        with open(Path(self.db).parent / 'experiments.jsonl', 'a') as f:
            f.write(json.dumps(row, default=str) + '\n')

    def channel_low(self, q):
        n = int(self.exit_low)
        return D(str(min(c for _, c in q['closes'][-n:]))) if q and len(q['closes']) >= n else None

    def price_block(self, q, setup='breakout_20d'):
        # The signal is yesterday's close; make sure today's price still agrees with it (a breakout: still above its level; every setup: no gap chase).
        level, close, price = max(D(str(c)) for _, c in q['closes'][-21:-1]), D(str(q['closes'][-1][1])), q['price']
        if setup == 'breakout_20d' and price < level:
            return f'Signal, but price {price:.2f} is back under the breakout level {level:.2f}.'
        if price > close * (1 + GAP):
            return f'Signal, but price {price:.2f} gapped over {GAP:.0%} above the {close:.2f} close; not chasing.'

    def congress_block(self, sym, day):
        """Optional entry rule: only buy a breakout if a member of the House filed a purchase of this stock in the last N days (0 = off)."""
        days = int(D(self.s['congress_days']))
        if not days or not self.congress or self.congress(sym, day, days):
            return None
        return f'Signal, but entries blocked: no congressional purchase of {sym} filed in the last {days} days.'

    def edge_block(self):
        e = self.edge
        if not self.edge_check or not self.gate:
            return None
        if not e or 'error' in e:
            return 'Signal, but entries blocked: strategy replay unavailable (%s).' % (e or {}).get('error', 'replay running' if self.edge_busy else 'not run yet')
        if e['entries'] < 5:
            return f'Signal, but entries blocked: only {e["entries"]} trades in the 5-year replay.'
        if e['total'] <= 0:
            return f'Signal, but entries blocked: this rule lost ${-e["total"]:.2f} in a 5-year replay of the watchlist.'
        return None

    def apply(self, day, quotes, trading):
        s, pos = self.s, self.s['positions']
        cash, realized = D(s['cash']), D(s['realized'])
        prices = {k: v['price'] for k, v in quotes.items()}
        equity = lambda: cash + sum(D(p['qty']) * prices.get(k, D(p['last'])) for k, p in pos.items())
        if day != s['day']:
            if self.quotes and s['day'] and trading:  # days the agent was not running are not in the P&L map
                missed = sum((date.fromisoformat(s['day']) + timedelta(i)).weekday() < 5 for i in range(1, (date.fromisoformat(day) - date.fromisoformat(s['day'])).days))
                if missed and s['market'].get('name') != 'Crypto':
                    self.alert(f'The agent was off: {missed} weekday(s) between {s["day"]} and {day} were not tracked (some may be holidays). Stops and targets were not checked.')
            s['day'], s['day_start'] = day, str(cash + sum(D(p['qty']) * D(p['last']) for p in pos.values()))  # last check's prices: the overnight gap counts in the new day
        decisions = {}
        for sym, p in list(pos.items()):
            if sym not in prices:
                decisions[sym] = 'Holding: no fresh price, exits not checked.'
                continue
            price = prices[sym]
            for ex, ratio in quotes[sym].get('splits', ()):  # a split since the buy: same money, more (or fewer) shares at a lower (or higher) price
                if ex > p['opened'] and ex not in p.get('splits', []) and ratio > 0 and ratio != 1:
                    p['splits'] = p.get('splits', []) + [ex]
                    p.update(qty=str(D(p['qty']) * ratio), entry=str(D(p['entry']) / ratio), last=str(D(p['last']) / ratio), high=str(D(p.get('high', p['entry'])) / ratio))
                    p.pop('suspect', None)
                    self.alert(f'{sym} split {ratio.normalize()}-for-1 on {ex}: the position is now {D(p["qty"]):.4f} shares at an entry of {D(p["entry"]):.2f} (cost unchanged).')
                    self.rec('SPLIT', sym, price, None, f'Split {ratio.normalize()}-for-1 on {ex}; quantity, entry and high adjusted.')
            ref = D(p['last'])
            if self.quotes is not None and abs(price / ref - 1) > JUMP:  # live checks only: the replay steps a day at a time on split-adjusted closes
                sus = p.get('suspect')
                if not sus or sus['day'] == day:
                    if not sus:
                        p['suspect'] = dict(day=day, price=str(price), ref=str(ref))
                        self.alert(f'{sym} quote {price:.2f} is {price / ref - 1:+.0%} from the last price seen ({ref:.2f}) and no split is reported. '
                                   'Its stop and target are on hold until the next session in case this is a bad quote or an unreported split. Check it.')
                        self.rec('SUSPECT_PRICE', sym, price, None, f'Price {price:.2f} vs last seen {ref:.2f}; exits held for a session.')
                    decisions[sym] = f'Holding: quote {price:.2f} is {price / ref - 1:+.0%} from the last price seen {ref:.2f} with no split reported; exits wait a session.'
                    continue
            p.pop('suspect', None)  # back in line with the last price, or still there a session later with no split: a real move
            qty = D(p['qty'])
            p['last'] = str(price)
            high = p['high'] = str(max(D(p.get('high', p['entry'])), price))  # highest price seen since entry, for the trailing stop
            gain = price / D(p['entry']) - 1
            low = self.channel_low(quotes.get(sym)) if self.exit_low else None  # lowest close of the last N completed sessions, today excluded
            if gain >= self.alert_at and not p['alerted']:
                p['alerted'] = True
                self.alert(f'{sym} is up {gain:.0%} on your ${D(p["cost"]):.2f} position.')
            reason, level = None, price  # level: the price the rule fires at, where a replay fills when the path crossed it between two samples
            if trading and gain <= -self.stop:
                reason, level = f'Stop: down {-gain:.1%} (limit −{self.stop:.0%}).', D(p['entry']) * (1 - self.stop)
            elif trading and self.breakeven and D(high) >= D(p['entry']) * (1 + self.breakeven) and price <= D(p['entry']):
                reason, level = f'Break-even stop: reached +{self.breakeven:.0%}, then fell back to the entry price {D(p["entry"]):.2f}.', D(p['entry'])
            elif trading and self.trail and price <= D(high) * (1 - self.trail):
                reason, level = f'Trailing stop: down {1 - price / D(high):.1%} from the high of {D(high):.2f} (limit −{self.trail:.0%}).', D(high) * (1 - self.trail)
            elif trading and self.exit_low and self.near_close and low is not None and price < low:
                reason = f'Channel exit: price {price:.2f} fell under the {self.exit_low:.0f}-day closing low {low:.2f}.'
            elif trading and gain >= self.target:
                reason, level = f'Target: up {gain:.1%} (target +{self.target:.0%}).', D(p['entry']) * (1 + self.target)
            if not reason:
                if trading and self.near_close and (day, sym) not in self.snapped:  # one end-of-day record per position
                    self.snapped.add((day, sym)); self.rec('HOLD', sym, price, low, f'End-of-day check: {gain:+.1%} from entry; stop level {D(p["entry"]) * (1 - self.stop):.2f}.')
                decisions[sym] = f'Holding: {gain:+.1%}.' + ('' if trading else ' Market closed; exits wait for the open.')
                continue
            self.rec('SELL', sym, price, low, reason)
            fill = (level if self.prev is not None and sym in self.prev else price) * (1 - self.slip)  # the day's first sample (the open) fills where it is: a gap
            pnl = qty * fill - D(p['cost'])
            cash += qty * fill
            realized += pnl
            self.trade(sym, 'SELL', qty, fill, pnl, reason)
            del pos[sym]
            s['last_exit'][sym] = day
            decisions[sym] = reason
        today = equity() - D(s['day_start'])
        for sym in self.symbols:
            if sym in pos or sym in decisions or sym not in quotes:
                continue
            q = quotes[sym]
            if len(q['closes']) < 30:
                decisions[sym] = 'Not enough daily history.'
                continue
            setup = self.signal([c for _, c in q['closes']])
            signal_day = str(q['closes'][-1][0])
            if not setup:
                decisions[sym] = 'No signal: last close is not above its 20-day closing high.' if self.entry == 'breakout_20d' else f'No signal: none of {self.entry.replace(",", ", ")}.'
            elif not trading:
                decisions[sym] = ('Signal: 20-day breakout.' if setup == 'breakout_20d' else f'Signal: {setup}.') + ' Market closed; entry waits for the open.'
            elif self.daily_loss and today <= -self.daily_loss:
                decisions[sym] = f'Signal, but entries blocked: down ${-today:.2f} today (limit ${self.daily_loss:.0f}).'
            elif self.max_new and sum(v == day for v in s['last_entry'].values()) >= self.max_new:
                decisions[sym] = f'Signal, but entries blocked: {self.max_new:.0f} bought today (limit {self.max_new:.0f}); spreads entries over days.'
            elif self.max_open and len(pos) >= self.max_open:
                decisions[sym] = f'Signal, but entries blocked: {len(pos)} positions open (limit {self.max_open:.0f}).'
            elif self.max_sector and sum(SECTOR.get(o, o) == SECTOR.get(sym, sym) for o in pos) >= self.max_sector:
                decisions[sym] = f'Signal, but entries blocked: {self.max_sector:.0f} {SECTOR.get(sym, sym)} positions already open (limit {self.max_sector:.0f}).'
            elif block := (self.edge_block() if self.gate_now is None else self.gate_now):
                decisions[sym] = block
            elif why := self.congress_block(sym, day):
                decisions[sym] = why
            elif max(s['last_entry'].get(sym, ''), s['last_exit'].get(sym, '')) >= signal_day:
                decisions[sym] = 'Signal already used; waiting for a new breakout.'
            elif why := self.price_block(q, setup):
                decisions[sym] = why
            else:
                px, before = q['price'], (self.prev or {}).get(sym)
                if before is not None:  # replay: the path moved into the entry window between two samples, so it entered at the window's edge
                    lo, top = max(D(str(c)) for _, c in q['closes'][-21:-1]) if setup == 'breakout_20d' else D(0), D(str(q['closes'][-1][1])) * (1 + GAP)
                    px = lo if before < lo else top if before > top else px
                fill = px * (1 + self.slip)
                qty = (min(self.per_trade or cash, cash) / fill).quantize(D('.0001'), rounding=ROUND_DOWN)  # the cap, or whatever cash is left
                cost = qty * fill
                if cost < 1:  # only an empty account skips a signal; a position under a dollar is noise
                    decisions[sym] = f'Signal, but no cash left (${cash:.2f}).'
                    continue
                cash -= cost
                pos[sym] = dict(qty=str(qty), entry=str(fill), cost=str(cost), last=str(q['price']), opened=day, alerted=False)
                s['last_entry'][sym] = day
                why = f'close {q["closes"][-1][1]:.2f} broke the 20-day high.' if setup == 'breakout_20d' else f'{setup} on the {q["closes"][-1][1]:.2f} close.'
                decisions[sym] = reason = f'Buy: {why}' + (f' {note}' if self.news and (note := self.news(sym)) else '')
                self.trade(sym, 'BUY', qty, fill, D(0), reason)
                self.rec('BUY', sym, q['price'], None, reason, setup=setup, signal_close=q['closes'][-1][1], breakout_level=max(c for _, c in q['closes'][-21:-1]))
        for sym, d in decisions.items():  # a signal that did not become a trade is a decision too
            if d.startswith('Signal') and self.last_dec.get(sym) != d:
                self.rec('SIGNAL_BLOCKED' if 'blocked' in d or 'not chasing' in d or 'back under' in d or 'cash' in d else 'SIGNAL', sym, quotes[sym]['price'] if sym in quotes else None, None, d)
        self.last_dec = dict(decisions)
        eq = equity()
        self.track_tick(day, trading, eq, cash, pos)
        if eq >= D(s['capital']) * (1 + self.alert_at) and not s['alerted_total']:
            s['alerted_total'] = True
            self.alert(f'Your account is up {eq / D(s["capital"]) - 1:.0%}: equity ${eq:.2f}.')
        s.update(cash=str(cash), realized=str(realized), equity=str(eq), decisions=decisions, updated=time.time(),
                 status=f'{len(pos)} open' if pos else 'Watching',
                 decision=f'{sum(v.startswith(("Buy", "Stop", "Trailing", "Channel", "Target")) for v in decisions.values())} actions, {len(pos)} positions. Today {today:+.2f}.')
        s.setdefault('quotes', {}).update({k: [str(v['price']), str(v['closes'][-1][1]), [round(c, 6) for _, c in v['closes'][-30:]], v['at']] for k, v in quotes.items() if v['closes']})  # price, last close, 30 closes, quote time
        h = s['history']  # equity curve: one point per 10 minutes in session, per hour out of it; in between the last point follows the latest value
        if not h or time.time() - h[-1][0] >= (600 if trading else 3600):
            h.append([time.time(), float(eq)])
        else:
            h[-1][1] = float(eq)
        s['history'] = h[-1200:]
        daily = s.setdefault('daily_pnl', {})
        if trading or day in daily:  # change in account value that day, open positions included; kept for good
            daily[day] = str(eq - D(s['day_start']))
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
            held = [s for e in (self, *self.followers) for s in e.s['positions']]
            quotes, problems = self.quotes(list(dict.fromkeys(self.symbols + held + list(self.extra))), now)  # held but removed symbols still get exits, for every portfolio fed by this stream
            if status['open']:
                for sym in [k for k, v in quotes.items() if time.time() - v['at'] > STALE]:
                    problems[sym] = f'Quote is stale ({time.time() - quotes.pop(sym)["at"]:.0f}s old); not traded.'
            self.unpriced = {s: self.unpriced.get(s, time.time()) for s in held if s not in quotes} if status['open'] else {}
            if status['open']:
                self.refresh_edge(status['date'])
                if self.news:  # fetch headlines for fresh signals before taking the lock, so a slow feed never stalls the dashboard
                    for sym, q in quotes.items():
                        if sym not in self.s['positions'] and self.signal([c for _, c in q['closes']]):
                            self.news(sym)
            self.gate_now = self.edge_block() or ''  # read once: a replay finishing mid-check must not let one portfolio in and keep the other out
            for f in self.followers:
                f.gate_now = self.gate_now
            self.commit(status, quotes, problems)
            for f in self.followers:  # the same quotes, the same moment
                try:
                    f.follow(self, status, quotes, problems)
                except Exception as exc:
                    f.error = f'Challenger update blocked: {type(exc).__name__}: {exc}'
                    f.rec('TICK_ERROR', None, None, None, f.error); f.write_audit()
            for e in (self, *self.followers):
                e.backup(status['date'])
        except Exception as exc:
            with self.lock:
                self.error = f'Market update blocked: {type(exc).__name__}: {exc}'
            self.rec('TICK_ERROR', None, None, None, self.error); self.write_audit()
        finally:
            for e in (self, *self.followers):
                e.gate_now = None
            with self.lock:
                self.busy = False
            self.beat()

    def beat(self):
        dst = self.heartbeat_path  # read once: another thread may clear it mid-write
        if not dst:
            return
        hb = dict(market=self.market_name, at=time.time(), ok=self.error is None, error=self.error, last_ok=self.s.get('updated'), running=self.running, pid=os.getpid(),
                  positions=len(self.s['positions']), market_open=bool((self.s.get('market') or {}).get('open')), unpriced=self.unpriced, backup_error=next((e.backup_error for e in (self, *self.followers) if e.backup_error), None), challenger_error=next((f.error for f in self.followers if f.error), None))
        try:
            tmp = Path(str(dst) + '.tmp')
            tmp.write_text(json.dumps(hb))
            os.replace(tmp, dst)
        except OSError:
            pass  # a heartbeat that cannot be written is itself noticed: the watchdog sees it go stale

    def commit(self, status, quotes, problems):
        # Apply one check under the lock, save it, then write its decision records. A failed check leaves state, alerts and records as they were.
        self.near_close = minutes_to_close(status) <= CLOSING_WINDOW if status['open'] else False
        with self.lock:
            previous, aux = copy.deepcopy(self.s), (dict(self.last_dec), dict(self.last_prob), set(self.snapped))
            try:
                self.apply(status['date'], quotes, status['open'])
                self.s['decisions'].update(problems)
                self.s['market'] = status
                for sym, msg in problems.items():
                    if self.last_prob.get(sym) != msg:
                        self.rec('NO_DATA', sym, None, None, msg)
                self.last_prob = dict(problems)
                self.save()
            except Exception:
                self.conn.rollback()
                self.s, self.pending, self.audit = previous, [], []
                self.last_dec, self.last_prob, self.snapped = aux  # what was recorded as already seen goes back too, so the retry records it again
                raise
            self.error = None
        self.write_audit()

    def follow(self, leader, status, quotes, problems):
        self.s['symbols'] = list(leader.symbols)  # same watchlist
        self.commit(status, quotes, dict(problems))

    def rec(self, action, sym, price, low, reason, **extra):
        # Decision record: when, what, the price seen, the channel threshold, and why. Written only where an audit file is set.
        if self.audit_path:
            self.audit.append(dict(ts=datetime.now(timezone.utc).isoformat(), portfolio=self.name, day=self.s.get('day'), symbol=sym, action=action,
                                   price=None if price is None else str(price), channel_low=None if low is None else str(low), reason=reason, **extra))

    def write_audit(self):
        rows, self.audit = self.audit, []
        if rows and self.audit_path:
            with open(self.audit_path, 'a') as f:
                f.writelines(json.dumps(r) + '\n' for r in rows)

    def reset_track(self, day):
        # Start the scoreboard: both portfolios call this at the same moment so the comparison covers the same period.
        eq = float(self.s['equity'])
        self.s['track'] = dict(since=day, equity0=eq, n=0, expo=0.0, npos=0, conc=0.0, conc_n=0, peak=eq, dd=0.0, entries=0, exits=0, wins=0, realized=0.0, days=[], pos_days=[])
        self.save()

    def track_tick(self, day, trading, eq, cash, pos):
        tr = self.s.get('track')
        if not tr or not trading or not eq:
            return
        tr['n'] += 1; tr['expo'] += float((eq - cash) / eq); tr['npos'] += len(pos)
        tr['peak'] = max(tr['peak'], float(eq)); tr['dd'] = max(tr['dd'], 1 - float(eq) / tr['peak'])
        if day not in tr['days']:
            tr['days'].append(day)
        if pos:
            vals = [D(p['qty']) * D(p['last']) for p in pos.values()]
            tr['conc'] += float(max(vals) / sum(vals)); tr['conc_n'] += 1
            if day not in tr['pos_days']:
                tr['pos_days'].append(day)

    def track_summary(self):
        tr = self.s.get('track')
        if not tr:
            return None
        n, days = max(tr['n'], 1), max(len(tr['days']), 1)
        return dict(since=tr['since'], days=len(tr['days']), ret=float(self.s['equity']) / tr['equity0'] - 1, equity=float(self.s['equity']), max_drawdown=tr['dd'], deployed=tr['expo'] / n,
                    days_with_position=len(tr['pos_days']) / days, avg_positions=tr['npos'] / n, largest_share=tr['conc'] / max(tr['conc_n'], 1),
                    entries=tr['entries'], exits=tr['exits'], wins=tr['wins'], realized=tr['realized'])

    def compare(self):
        # Baseline against its challenger, plus anything that makes the comparison unfair.
        if not self.followers:
            return None
        f = self.followers[0]
        with f.lock:
            held = []
            for sym, p in f.s['positions'].items():
                closes = (f.s.get('quotes', {}).get(sym) or [0, 0, []])[2][-int(f.exit_low or 20):]
                low = min(closes) if closes else None
                held.append(dict(symbol=sym, entry=p['entry'], last=p['last'], channel_low=low, gap=(float(p['last']) / low - 1) if low else None))
            proto = f.s.get('protocol') or {}
            return dict(protocol=proto, baseline=self.track_summary(), challenger=f.track_summary(), positions=held, error=f.error,
                        rule_changes=[r for r in self.s.get('rule_log', []) if r['at'] > proto.get('started_at', 1e18)])

    def snapshot(self):
        with self.lock:
            rows = self.conn.execute('SELECT * FROM trades ORDER BY id DESC LIMIT 101').fetchall()
            out = dict(self.s, running=self.running, busy=self.busy, error=self.error, symbols=self.symbols, gate=self.gate,
                       edge={k: str(v) for k, v in self.edge.items()} if self.edge else None, more=len(rows) > 100)
            out['trades'] = [dict(zip(['id', 'time', 'symbol', 'side', 'qty', 'price', 'fee', 'pnl', 'reason'], r)) for r in rows[:100]]
            out['daily'] = dict(self.s.get('daily_pnl', {}))
            first = self.conn.execute('SELECT MIN(substr(time,1,10)) FROM trades').fetchone()[0]
            out['bench'] = self.bench(first) if self.bench and first else None
            out['challenger'] = self.compare()
            return out

def path(row):
    """The prices one daily bar is walked through: (date, close) is just the close; (date, open, high, low, close) is open, then low and high (the low first
    on an up day, the high first on a down day), then the close. A minute-by-minute check sees roughly this order; the exact intraday path is unknown."""
    if len(row) < 5:
        return [row[-1]]
    _, o, h, l, c = row
    return [o, l, h, c] if c >= o else [o, h, l, c]

def replay(hist, congress=None, record=False, **rules):
    """Run the engine day by day over {SYM: [(date, close)]} or {SYM: [(date, open, high, low, close)]}.
    With full bars each day is walked through its open, low/high and close: entries and exits can fire on any of them, a level crossed between two of them
    fills at the level (as the live agent's minute checks would), a gap fills at the open, and channel exits are decided on the close.
    With closes alone it decides once a day at the close, so intraday stop crossings and recoveries are not reproduced."""
    e = StockEngine(':memory:', None, list(hist), congress=congress, **rules)
    e.journal = [] if record else None
    cols = {k: ([r[0] for r in h], [r[-1] for r in h], [path(r) for r in h]) for k, h in hist.items()}
    days = sorted({r[0] for h in hist.values() for r in h})
    at, curve = dict.fromkeys(hist, 0), []
    for day in days:
        today = {}  # sym: (completed closes before today, today's price samples)
        for sym, (ds, cs, ps) in cols.items():
            i = at[sym]
            if i < len(ds) and ds[i] == day:
                if i >= 30:
                    today[sym] = (list(zip(ds[i - 60 if i > 60 else 0:i], cs[i - 60 if i > 60 else 0:i])), ps[i])
                at[sym] = i + 1
        steps = max((len(ps) for _, ps in today.values()), default=1)
        e.prev = {}
        for k in range(steps):
            e.near_close = k == steps - 1
            quotes = {sym: dict(closes=closes, price=D(str(ps[k - steps + len(ps)])), at=0) for sym, (closes, ps) in today.items() if k - steps + len(ps) >= 0}
            e.apply(str(day), quotes, True)
            e.prev.update({sym: q['price'] for sym, q in quotes.items()})
        curve.append((D(e.s['equity']), D(e.s['cash']), len(e.s['positions'])))
    trades = e.conn.execute('SELECT side, pnl FROM trades').fetchall()
    return dict(entries=sum(t[0] == 'BUY' for t in trades), exits=sum(t[0] == 'SELL' for t in trades),
                wins=sum(t[0] == 'SELL' and D(t[1]) > 0 for t in trades), realized=D(e.s['realized']),
                total=D(e.s['equity']) - D(e.s['capital']), open=len(e.s['positions']),
                first=str(days[0]) if days else None, last=str(days[-1]) if days else None, data_sha256=data_hash(hist), **evidence(curve, hist, D(e.s['capital'])), **(dict(journal=e.journal) if record else {}))

def data_hash(hist):
    h = hashlib.sha256()
    for sym in sorted(hist):
        h.update(sym.encode())
        for row in hist[sym]:
            h.update((':'.join(map(str, row)) + ';').encode())  # (date, close) hashes as before
    return h.hexdigest()

def save_history(hist, folder):
    """Keep the exact prices a replay used, named by their hash, so the result can be reproduced after Yahoo's data changes."""
    path = Path(folder) / f'{data_hash(hist)[:16]}.json.gz'
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, 'wt') as f:
            json.dump({s: [[str(r[0]), *r[1:]] for r in h] for s, h in hist.items()}, f)
    return path

def saved_rules(db):
    """The rules saved from the dashboard, so a CLI replay evaluates what the agent is actually running."""
    try:
        body = json.loads(sqlite3.connect(f'file:{db}?mode=ro', uri=True).execute('SELECT body FROM state WHERE id=1').fetchone()[0])
    except Exception:
        return {}
    return {**{k: D(body[k]) for k in ('stop', 'target', 'alert_at', 'trail', 'max_open', 'max_new', 'congress_days', 'per_trade', 'daily_loss', 'breakeven', 'max_sector', 'exit_low') if k in body}, **({'entry': body['entry']} if 'entry' in body else {})}

def minutes_to_close(status):
    """Minutes from now (New York) to the close; 0 where there is no close (crypto), 999 if the clock cannot be read."""
    if 'closes' not in status:
        return 0
    try:
        (h, m), (nh, nm) = (map(int, status['closes'].split(':')), map(int, status['now_new_york'][11:16].split(':')))
        return h * 60 + m - nh * 60 - nm
    except Exception:
        return 999

def build_challenger(leader, db=None, audit_dir=None):
    """The frozen challenger: the baseline's portfolio cloned at this moment, then run on the same quote stream with two differences, no +25% target
    and a sale when the price in the last minutes of the session is under the lowest close of the previous 20 sessions. Rules cannot be edited afterwards."""
    audit_dir = Path(audit_dir or ROOT / 'data')
    db = Path(db or audit_dir / 'stocks-e.sqlite')
    fresh = not db.exists()
    rules = dict(per_trade=leader.per_trade, daily_loss=leader.daily_loss, stop=leader.stop, alert_at=leader.alert_at, trail=leader.trail, max_open=leader.max_open,
                 max_new=leader.max_new, congress_days=leader.congress_days, target=D(100), exit_low=D(20))
    f = StockEngine(db, leader.quotes, leader.symbols, capital=D(leader.s['capital']), slip=leader.slip, status=leader.status, congress=leader.congress, news=leader.news, gate=False, **rules)
    f.name, f.audit_path, leader.audit_path = 'challenger', audit_dir / 'decisions-stocks-e.jsonl', audit_dir / 'decisions-stocks.jsonl'
    if fresh:
        with leader.lock:
            for k in ('cash', 'realized', 'fees', 'positions', 'last_entry', 'last_exit', 'day', 'day_start', 'alerted_total', 'equity'):
                f.s[k] = copy.deepcopy(leader.s[k])
            day = leader.s.get('day') or str(date.today())
            f.s['frozen'] = True
            f.s['protocol'] = dict(started=day, started_at=time.time(), code=CODE, baseline_equity=leader.s['equity'], rules={k: str(v) for k, v in f.rules().items()},
                                   text='Cloned from the baseline portfolio; same stocks, same quotes, same entries; no +25% target; sells when the price in the last '
                                        f'{CLOSING_WINDOW} minutes of the session is under the lowest close of the previous 20 completed sessions; the 10% stop stays and is checked every minute.')
            leader.reset_track(day); f.reset_track(day)
        f.save()
    f.s['frozen'] = True
    f.edge_block = lambda: leader.edge_block()  # entry permission comes from the baseline's replay gate, so the only difference left is the exit rule
    rev = f.s['protocol'].setdefault('revisions', [])
    if not any(r['change'] == 'gate' for r in rev):
        rev.append(dict(change='gate', at=time.time(), code=CODE, text="Challenger now takes entry permission from the baseline's replay gate (before: no gate, so it could enter while the baseline was blocked)."))
        f.save()
    leader.followers.append(f)
    return f

def evidence(curve, hist, capital):
    """What a profitable replay does not tell you: how it compares with just holding the same stocks, the worst drop on the way, how much of the time money was invested, and whether the second half held up as well as the first."""
    if not curve:
        return {}
    peak, dd = capital, D(0)
    for eq, _, _ in curve:
        peak = max(peak, eq); dd = max(dd, (peak - eq) / peak)
    held = [h[-1][-1] / h[0][-1] - 1 for h in hist.values() if len(h) > 1 and h[0][-1]]  # close to close
    mid = curve[len(curve) // 2][0]
    return dict(max_drawdown=dd, exposure=sum((eq - cash) / eq for eq, cash, _ in curve) / len(curve), days_open=sum(n > 0 for _, _, n in curve) / len(curve), half1=mid - capital, half2=curve[-1][0] - mid,
                buy_hold=capital * D(str(sum(held) / len(held))) if held else D(0))

def build_crypto(db=None, symbols=CRYPTO, capital=D('4000'), slip=D('.0065'), per_trade=D('400'), **rules):
    # Same rules as stocks; slip folds in an assumed 0.6% Coinbase fee per side plus 0.05% slippage.
    # Same exits as stocks, a $400 cap per position (a tenth of the capital), and the gate blocks entries when the replay loses.
    (ROOT / 'data').mkdir(exist_ok=True)
    e = StockEngine(db or ROOT / 'data' / 'crypto.sqlite', coinbase_quotes, symbols, status=crypto_status, source='coinbase',
                    notify=notify_mac, capital=capital, slip=slip, per_trade=per_trade, **dict(DEFAULT_RULES, **rules))
    def edge():
        h = {s: coinbase_closes(s, 5 * 365, bars=True) for s in e.symbols}
        save_history(h, ROOT / 'data' / 'history')
        return replay(h, **e.rules())
    e.edge_check = edge
    return e

def build(db=None, symbols=WATCHLIST, **rules):
    (ROOT / 'data').mkdir(exist_ok=True)
    e = StockEngine(db or ROOT / 'data' / 'stocks.sqlite', yahoo_quotes, symbols, notify=notify_mac, congress=congress_data.bought, news=news.note, bench=sp500_since, **dict(DEFAULT_RULES, **rules))
    def edge():
        h = history(e.symbols, bars=True)  # full daily bars: the replay walks each day as the live agent sees it
        save_history(h, ROOT / 'data' / 'history')
        return replay(h, congress=congress_data.bought, **e.rules())  # the same entry filter the live agent applies
    e.edge_check = edge
    return e
