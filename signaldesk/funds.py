#!/usr/bin/env python3
"""Signal Desk mutual funds: paper SIPs into Indian funds (NAVs from mfapi.in, i.e. AMFI) and US funds (Yahoo). No real orders, ever.

Served as a third market by run.py (http://127.0.0.1:8765/funds). `python3 -m signaldesk.funds --replay` compares a plain SIP with a step-up SIP on real NAV history.
Rules come from the MoneyPurse notes: step-up SIP, a minimum holding horizon per category, and a pause on thematic funds that just ran hot.
India (INR) and US (USD) are kept as two separate portfolios; nothing is converted. Not modelled yet (add when wanted): exit load and sells, parking idle
cash in a liquid fund, cheap-market lump-sum top-ups (needs a reliable free index P/E source), tax."""
import argparse
import json
import re
import sqlite3
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta
from decimal import Decimal as D, ROUND_DOWN
from pathlib import Path
from .core import ROOT, Runner, utcnow
from .quotes import scan, yahoo_quotes

FUNDS = [  # id, currency, source, code, category, default monthly SIP. Edit amounts and goals from the page.
    dict(id='IN120716', name='UTI Nifty 50 Index Fund (Direct)', cur='INR', src='mfapi', code='120716', cat='index', monthly=5000),
    dict(id='IN122639', name='Parag Parikh Flexi Cap (Direct)', cur='INR', src='mfapi', code='122639', cat='flexi', monthly=5000),
    dict(id='IN118778', name='Nippon India Small Cap (Direct)', cur='INR', src='mfapi', code='118778', cat='small', monthly=2000),
    dict(id='IN118759', name='Nippon India Pharma (Direct)', cur='INR', src='mfapi', code='118759', cat='thematic', monthly=1000),
    dict(id='IN120586', name='ICICI Prudential Large Cap (Direct)', cur='INR', src='mfapi', code='120586', cat='large', monthly=3000),
    dict(id='IN118955', name='HDFC Flexi Cap (Direct)', cur='INR', src='mfapi', code='118955', cat='flexi', monthly=3000),
    dict(id='IN147622', name='Motilal Oswal Nifty Midcap 150 Index (Direct)', cur='INR', src='mfapi', code='147622', cat='mid', monthly=2000),
    dict(id='IN120828', name='Quant Small Cap (Direct)', cur='INR', src='mfapi', code='120828', cat='small', monthly=1000),
    dict(id='IN119091', name='HDFC Liquid Fund (Direct)', cur='INR', src='mfapi', code='119091', cat='liquid', monthly=2000),
    dict(id='VFIAX', name='Vanguard 500 Index (VFIAX)', cur='USD', src='yahoo', code='VFIAX', cat='index', monthly=200),
    dict(id='VTSAX', name='Vanguard Total Stock Market (VTSAX)', cur='USD', src='yahoo', code='VTSAX', cat='index', monthly=150),
    dict(id='VTIAX', name='Vanguard Total International Stock (VTIAX)', cur='USD', src='yahoo', code='VTIAX', cat='index', monthly=100),
    dict(id='VIMAX', name='Vanguard Mid-Cap Index (VIMAX)', cur='USD', src='yahoo', code='VIMAX', cat='mid', monthly=100),
    dict(id='VSMAX', name='Vanguard Small-Cap Index (VSMAX)', cur='USD', src='yahoo', code='VSMAX', cat='small', monthly=100),
    dict(id='VBTLX', name='Vanguard Total Bond Market (VBTLX)', cur='USD', src='yahoo', code='VBTLX', cat='bond', monthly=100)]
DEFAULT_RULES = dict(step_up='0', horizon_equity='5', horizon_small='7', hot_theme='0')  # years / fractions; hot_theme 0 = off
UNITS = D('.001')
CATS = ('index', 'flexi', 'large', 'mid', 'small', 'thematic', 'bond', 'liquid')

def months(first, last):
    """['YYYY-MM', ...] from first's month through last's month, inclusive."""
    out, y, m = [], first.year, first.month
    while (y, m) <= (last.year, last.month):
        out.append(f'{y}-{m:02d}')
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out

def nav_on_or_after(series, day):
    """(date, nav) of the first NAV on or after day, or None. series is oldest first."""
    return next(((d, n) for d, n in series if d >= day), None)

def ret_1y(series, day):
    """Return over the year to `day` (a fraction), or None with under a year of history."""
    now = [(d, n) for d, n in series if d <= day]
    old = [(d, n) for d, n in now if d <= day - timedelta(365)]
    return now[-1][1] / old[-1][1] - 1 if now and old else None

def horizon_need(cat, rules):
    """Years the fund needs: equity categories 5, small caps and thematic 7 (video 13), debt none."""
    return D(rules['horizon_small']) if cat in ('small', 'thematic') else D(rules['horizon_equity']) if cat in ('index', 'flexi', 'large', 'mid') else D(0)

def sip_plan(series, cat, start, upto, monthly, rules, goal_years, after=None):
    """What a SIP does each calendar month from start's month through upto's month, skipping months <= `after`:
    [(month, date, nav, amount, None)] for a buy, or [(month, date, nav, 0, reason)] for a month the rules refuse.
    The price is the first NAV on or after the 1st (or the start date, in the first month). Step-up raises the amount each year."""
    out, step = [], D(rules['step_up'])
    for i, month in enumerate(months(start, upto)):
        if after and month <= after:
            continue
        got = nav_on_or_after(series, max(date.fromisoformat(month + '-01'), start))
        if not got or got[0] > upto:
            continue
        day, nav = got
        amount = (D(monthly) * (1 + step) ** (i // 12)).quantize(D('.01'))
        need, hot = horizon_need(cat, rules), D(rules['hot_theme'])
        if D(goal_years) < need:
            out.append((month, day, nav, D(0), f'Blocked: goal is {goal_years} years but a {cat} fund needs {need:.0f}+ (equity SIPs under 5 years, small cap and thematic under 7, are refused).'))
        elif hot and cat == 'thematic' and (r := ret_1y(series, day)) is not None and r > hot:
            out.append((month, day, nav, D(0), f'Paused: this thematic fund is up {r:.0%} over a year (limit {hot:.0%}); chasing hot themes tends to end badly.'))
        elif amount > 0:
            out.append((month, day, nav, amount, None))
    return out

def xirr(flows):
    """Annual rate for [(date, amount)] (money in negative, value out positive), by bisection; None if it has no solution."""
    flows = sorted(flows)
    if len(flows) < 2 or not any(a < 0 for _, a in flows) or not any(a > 0 for _, a in flows) or (flows[-1][0] - flows[0][0]).days < 30:
        return None
    f = lambda r: sum(float(a) / (1 + r) ** ((d - flows[0][0]).days / 365) for d, a in flows)
    lo, hi = -0.99, 100.0
    if f(lo) * f(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(lo) * f(mid) > 0 else (lo, mid)
    return (lo + hi) / 2

def fetch_series(fund):
    """[(date, nav Decimal)] oldest first. Raises on any failure; there is no fallback and no made-up price."""
    if fund['src'] == 'mfapi':
        req = urllib.request.Request(f'https://api.mfapi.in/mf/{fund["code"]}', headers={'User-Agent': 'signal-desk'})
        rows = json.load(urllib.request.urlopen(req, timeout=30))['data']
        out = sorted((datetime.strptime(r['date'], '%d-%m-%Y').date(), D(r['nav'])) for r in rows)
    else:
        data, problems = yahoo_quotes([fund['code']], utcnow(), '10y')
        if fund['code'] not in data:
            raise ValueError(problems.get(fund['code'], 'no data'))
        out = [(d, D(str(c))) for d, c in data[fund['code']]['closes']]
    if not out or out[-1][0] < date.today() - timedelta(10):
        raise ValueError(f'stale or empty NAV data (latest {out[-1][0] if out else "none"})')
    return out

def scheme_name(code):
    req = urllib.request.Request(f'https://api.mfapi.in/mf/{code}/latest', headers={'User-Agent': 'signal-desk'})
    return json.load(urllib.request.urlopen(req, timeout=30))['meta']['scheme_name']

class SipEngine(Runner):
    interval = 900
    market_name = 'funds'

    def __init__(self, db, fetch=fetch_series, today=date.today, funds=FUNDS):
        self.lock, self.fetch, self.today = threading.RLock(), fetch, today
        self.running = self.busy = False
        self.error, self.wake, self.series = None, threading.Event(), {}
        self.conn = sqlite3.connect(db, check_same_thread=False)
        self.conn.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, body TEXT NOT NULL)')
        self.conn.execute('CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY, time TEXT, fund TEXT, side TEXT, units TEXT, nav TEXT, amount TEXT, nav_date TEXT, reason TEXT)')
        saved = self.conn.execute('SELECT body FROM state WHERE id=1').fetchone()
        self.s = json.loads(saved[0]) if saved else dict(started=self.today().isoformat(), rules=dict(DEFAULT_RULES), funds={}, updated=None, decision='Ready. Start the agent to begin the paper SIPs.')
        self.funds = {f['id']: f for f in [*funds, *self.s.setdefault('custom', [])]}  # funds added from the page are saved with the state
        for f in self.funds.values():  # a saved choice wins
            self.s['funds'].setdefault(f['id'], dict(monthly=str(f['monthly']), goal_years='10', units='0', invested='0', last_month=None, nav=None, nav_date=None, error=None))
        self.save()

    def save(self):
        self.conn.execute('INSERT OR REPLACE INTO state VALUES (1, ?)', (json.dumps(self.s),))
        self.conn.commit()

    def set_rules(self, body):
        """Rules and per-fund settings from the page. Returns an error message, or None once saved."""
        checks = dict(step_up=(lambda x: 0 <= x <= 1, 'Step-up must be 0 to 100%'), horizon_equity=(lambda x: 0 <= x <= 40, 'Equity horizon must be 0 to 40 years'),
                      horizon_small=(lambda x: 0 <= x <= 40, 'Small-cap horizon must be 0 to 40 years'), hot_theme=(lambda x: 0 <= x <= 10, 'Hot-theme limit must be 0 (off) to 1000%'))
        fund_checks = dict(monthly=(lambda x: x >= 0, 'Monthly SIP must be 0 or more'), goal_years=(lambda x: 0 < x <= 60, 'Goal must be 1 to 60 years'))
        rules, funds = {}, {}
        if isinstance(body.get('add_fund'), dict):
            return self.add_fund(body['add_fund'])
        try:
            for k, v in body.items():
                if k in checks:
                    x = D(str(v))
                    if not x.is_finite() or not checks[k][0](x):
                        return checks[k][1] + '.'
                    rules[k] = str(x)
                elif k == 'funds' and isinstance(v, dict):
                    for fid, vals in v.items():
                        if fid not in self.funds or not isinstance(vals, dict):
                            return f'Unknown fund {fid}.'
                        for fk, fv in vals.items():
                            x = D(str(fv))
                            if fk not in fund_checks or not x.is_finite() or not fund_checks[fk][0](x):
                                return fund_checks.get(fk, (0, f'Unknown setting {fk}'))[1] + '.'
                            funds.setdefault(fid, {})[fk] = str(x)
        except (ArithmeticError, ValueError, TypeError):
            return 'Enter numbers only.'
        with self.lock:
            self.s['rules'].update(rules)
            for fid, vals in funds.items():
                self.s['funds'][fid].update(vals)
            self.save()
        self.wake.set()
        return None

    def add_fund(self, b):
        """Add a fund by its mfapi.in scheme code (India) or Yahoo ticker (US). It is checked against live data first. Returns an error message, or None."""
        src, code, cat = b.get('src'), str(b.get('code', '')).strip(), b.get('cat')
        if src not in ('mfapi', 'yahoo') or cat not in CATS or not re.fullmatch(r'\d{5,7}' if src == 'mfapi' else r'[A-Za-z0-9.\-]{1,12}', code):
            return 'Pick India (a 5-7 digit AMFI scheme code) or US (a ticker), and a category.'
        fid = 'IN' + code if src == 'mfapi' else code.upper()
        try:
            monthly = D(str(b.get('monthly', 0)))
            if not monthly.is_finite() or monthly < 0:
                raise ValueError
        except (ArithmeticError, ValueError):
            return 'Monthly SIP must be 0 or more.'
        if fid in self.funds:
            return f'{fid} is already on the page.'
        fund = dict(id=fid, name=str(b.get('name') or '').strip()[:80], cur='INR' if src == 'mfapi' else 'USD', src=src, code=code.upper() if src == 'yahoo' else code, cat=cat, monthly=str(monthly))
        try:
            series = self.fetch(fund)
            fund['name'] = fund['name'] or (scheme_name(code) if src == 'mfapi' else fund['code'])
        except Exception as exc:
            return f'Could not load NAV data for {fid}: {exc}'
        with self.lock:
            self.series[fid] = series
            self.funds[fid] = fund
            self.s['custom'].append(fund)
            self.s['funds'][fid] = dict(monthly=str(monthly), goal_years='10', units='0', invested='0', last_month=None, nav=None, nav_date=None, error=None)
            self.save()
        self.wake.set()
        return None

    def tick(self):
        with self.lock:
            if self.busy:
                return
            self.busy = True
        try:
            today, errors = self.today(), []
            for fid, f in self.funds.items():
                st = self.s['funds'][fid]
                try:
                    self.series[fid] = self.fetch(f)
                    st['error'] = None
                except Exception as exc:  # no data = no buy this check; the page shows why, and the next check retries
                    st['error'] = f'No NAV: {exc}'
                    errors.append(f'{fid}: {exc}')
                    continue
                series = self.series[fid]
                st['nav_date'], st['nav'] = series[-1][0].isoformat(), str(series[-1][1])
                for month, day, nav, amount, why in sip_plan(series, f['cat'], date.fromisoformat(self.s['started']), today, st['monthly'], self.s['rules'], st['goal_years'], st['last_month']):
                    units = (amount / nav).quantize(UNITS, rounding=ROUND_DOWN) if amount else D(0)
                    if amount and units > 0:
                        st['units'], st['invested'] = str(D(st['units']) + units), str(D(st['invested']) + amount)
                    self.conn.execute('INSERT INTO trades (time, fund, side, units, nav, amount, nav_date, reason) VALUES (?,?,?,?,?,?,?,?)',
                                      (datetime.now().isoformat(timespec='seconds'), fid, 'BUY' if units > 0 else 'SKIP', str(units), str(nav), str(amount), day.isoformat(),
                                       why or f'{month} SIP: {amount} at NAV {nav} ({day}).'))
                    st['last_month'] = month
            with self.lock:
                self.error = '; '.join(errors) or None
                self.s['updated'], self.s['decision'] = time.time(), self.error or 'All NAVs fetched; SIPs are up to date.'
                self.save()
            self.backup(today.isoformat())
        finally:
            self.busy = False

    def snapshot(self):
        with self.lock:
            rows = self.conn.execute('SELECT id, time, fund, side, units, nav, amount, nav_date, reason FROM trades ORDER BY id DESC LIMIT 100').fetchall()
            buys = self.conn.execute("SELECT fund, nav_date, amount FROM trades WHERE side='BUY'").fetchall()
            out = dict(self.s, running=self.running, busy=self.busy, error=self.error or getattr(self, 'backup_error', None), trades=[dict(zip(['id', 'time', 'fund', 'side', 'units', 'nav', 'amount', 'nav_date', 'reason'], r)) for r in rows])
            out['funds'] = {fid: dict(st, **{k: self.funds[fid][k] for k in ('name', 'cur', 'cat', 'src')}) for fid, st in self.s['funds'].items()}
            totals = {}
            for fid, f in out['funds'].items():
                value = D(f['units']) * D(f['nav']) if f['nav'] else D(0)
                flows = [(date.fromisoformat(d), -D(a)) for b, d, a in buys if b == fid]
                year = [float(n) for d, n in self.series.get(fid, []) if d >= date.today() - timedelta(365)]
                f['spark'] = year[::max(1, len(year) // 52)] + year[-1:] if len(year) > 1 else []  # about weekly points over the last year, for the trend line
                f['value'], f['xirr'] = str(value.quantize(D('.01'))), (xirr(flows + [(date.fromisoformat(f['nav_date']), value)]) if flows and f['nav_date'] else None)
                t = totals.setdefault(f['cur'], dict(invested=D(0), value=D(0), flows=[]))
                t['invested'] += D(f['invested']); t['value'] += value; t['flows'] += flows
                t['asof'] = max(t.get('asof', ''), f['nav_date'] or '')
            out['totals'] = {c: dict(invested=str(t['invested']), value=str(t['value'].quantize(D('.01'))), xirr=xirr(t['flows'] + [(date.fromisoformat(t['asof']), t['value'])]) if t['flows'] and t['asof'] else None)
                             for c, t in totals.items()}
            return out

def build_funds(db=None):
    (ROOT / 'data').mkdir(exist_ok=True)
    return SipEngine(db or ROOT / 'data' / 'funds.sqlite')

def replay(series, cat, years=10, step_up='0', goal_years='10', monthly=1000):
    """A SIP of `monthly` into one fund for the last `years` years, decided on the same rules as the live engine: {invested, value, xirr, buys}."""
    end = series[-1][0]
    rules = dict(DEFAULT_RULES, step_up=step_up)
    buys = [b for b in sip_plan(series, cat, end - timedelta(365 * years), end, monthly, rules, goal_years) if b[3]]
    units = sum(a / n for _, _, n, a, _ in buys)
    value = units * series[-1][1]
    return dict(invested=sum(a for *_, a, _ in buys), value=value, buys=len(buys), xirr=xirr([(d, -a) for _, d, _, a, _ in buys] + [(end, value)]))

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--replay', action='store_true', help='compare a plain SIP with a 10%% step-up SIP on each starter fund')
    p.add_argument('--years', type=int, default=10)
    a = p.parse_args()
    if not a.replay:
        p.error('Run run.py to serve the Funds page; use --replay here.')
    print(f'{a.years}-year SIP replay, one {"month"} at a time at the first NAV of the month. Past results do not predict future ones.')
    for f in FUNDS:
        try:
            s = fetch_series(f)
        except Exception as exc:
            print(f'{f["id"]:9} no data: {exc}')
            continue
        row = []
        for label, step in (('plain', '0'), ('step-up 10%', '.1')):
            r = replay(s, f['cat'], a.years, step)
            row.append(f'{label}: invested {r["invested"]:,.0f} -> {r["value"]:,.0f} ({"n/a" if r["xirr"] is None else format(r["xirr"], ".1%")} XIRR)')
        print(f'{f["id"]:9} {f["cat"]:9} ' + ' | '.join(row))

if __name__ == '__main__':
    main()
