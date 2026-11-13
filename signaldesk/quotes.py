"""Price feeds and the market clock: Yahoo daily and 5-minute charts, Coinbase candles, the NYSE calendar from the trader skills, the S&P 500 benchmark, macOS notifications."""
import json
import subprocess
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .core import D, positive, skill, utcnow

scan = skill('trader-scanner', 'scan')     # Yahoo chart fetch, complete daily bars, find_setups, 5-minute bars
clock = skill('trader-alerts', 'alerts')   # NYSE hours and holidays (2026-2027)

def coinbase(path, params, product='BTC-USD'):
    req = Request(f'https://api.exchange.coinbase.com/products/{product}/' + path + '?' + urlencode(params), headers={'User-Agent': 'tradebot-paper'})
    with urlopen(req, timeout=10) as res:
        return json.load(res)

def yahoo_quotes(symbols, now, rng='3mo'):
    """{SYM: closes, live price, quote time} from one Yahoo chart call each, plus per-symbol problems."""
    out, problems = {}, {}
    for sym in symbols:
        try:
            series = scan.parse_chart(scan.yahoo_get(scan.chart_path(sym, rng, '1d')), sym)
            price, at = series['meta'].get('regularMarketPrice'), series['meta'].get('regularMarketTime')
            if not price or price <= 0 or not at:
                raise ValueError('no price')
            out[sym] = dict(closes=scan.daily_closes(series, now), price=D(str(price)), at=float(at))
        except Exception as exc:
            problems[sym] = f'No quote: {exc}'
        time.sleep(scan.PAUSE)
    return out, problems

def coinbase_closes(sym, days):
    """Completed UTC daily closes [(date, close)], oldest first. Coinbase returns at most 300 candles per call."""
    end, rows = int(time.time()) // 86400 * 86400, {}
    for start in range(end - days * 86400, end, 300 * 86400):
        for r in coinbase('candles', {'granularity': 86400, 'start': start, 'end': min(start + 299 * 86400, end - 86400)}, sym + '-USD'):
            rows[r[0]] = r
    return [(datetime.fromtimestamp(t, timezone.utc).date(), float(r[4])) for t, r in sorted(rows.items()) if t < end]

def coinbase_quotes(symbols, now, days=60):
    out, problems = {}, {}
    for sym in symbols:
        try:
            t = coinbase('ticker', {}, sym + '-USD')
            at = datetime.fromisoformat(t['time'][:26].rstrip('Z')).replace(tzinfo=timezone.utc).timestamp()
            out[sym] = dict(closes=coinbase_closes(sym, days), price=positive(t['price']), at=at)
        except Exception as exc:
            problems[sym] = f'No quote: {exc}'
    return out, problems

def crypto_status(now):
    # Crypto never closes; a trading "day" (daily loss limit, signal day) is the UTC date.
    return dict(name='Crypto', open=True, calendar_known=True, date=str(now.date()), reason='trades 24/7; daily candles close at 00:00 UTC',
                now_utc=now.strftime('%Y-%m-%d %H:%M'))

def notify_mac(text):
    try:
        subprocess.run(['osascript', '-e', f'display notification {json.dumps(text, ensure_ascii=False)} with title "Signal Desk"'],
                       timeout=5, check=False, capture_output=True)
    except Exception:
        pass

_bench = {'at': 0.0, 'busy': False, 'data': None}

def sp500_since(first):
    """S&P 500 (SPY, price only) return from the close before `first` (YYYY-MM-DD) to now. Cached; refreshed in the background so a snapshot never waits."""
    def refresh():
        try:
            q, _ = yahoo_quotes(['SPY'], utcnow(), '5y')
            _bench['data'] = {'closes': [(str(d), float(c)) for d, c in q['SPY']['closes']], 'price': float(q['SPY']['price'])}
            _bench['at'] = time.time()
        except Exception:
            _bench['at'] = time.time() - 3000  # retry in about 10 minutes
        _bench['busy'] = False
    if not _bench['busy'] and time.time() - _bench['at'] > 3600:
        _bench['busy'] = True
        threading.Thread(target=refresh, daemon=True).start()
    d = _bench['data']
    base = next(((day, c) for day, c in reversed(d['closes']) if day < first), None) if d else None
    return {'since': base[0], 'spy': d['price'] / base[1] - 1} if base else None

def history(symbols, years=5):
    data, _ = yahoo_quotes(symbols, utcnow(), f'{years}y')
    return {k: v['closes'] for k, v in data.items()}
