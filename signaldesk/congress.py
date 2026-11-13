"""Free congressional trades, read from the House Clerk's public PTR filings: no key, no account.
Parsing comes from the installed trader-pelosi skill. House only (Senate filings are not machine-readable).
Reading the PDFs needs pdftotext (brew install poppler). Filings trail the trades they report by days to weeks.
Everything read is kept in data/congress.json so the dashboard card, the entry rule and the backtest share one record."""
import importlib.util
import json
import os
import threading
import time
from datetime import date, timedelta
from pathlib import Path

from .core import ROOT, SKILLS
SKILL = SKILLS / 'trader-pelosi' / 'ptr.py'
PDFS = ROOT / 'data' / 'ptr-pdfs'
STORE = ROOT / 'data' / 'congress.json'
TTL = 3600   # seconds between index checks
DAYS = 60    # how far back the dashboard card looks
_lock = threading.Lock()
_rows, _done = [], set()  # parsed trades, and the filings (DocIDs) already read
_buys = {}                # symbol -> sorted dates a purchase was filed
_loaded = False
_state = {'busy': False, 'error': None, 'checked': 0.0}

def _ptr():
    spec = importlib.util.spec_from_file_location('skill_ptr', SKILL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def _merge(data):
    seen = {r['doc'] for r in _rows}
    _rows.extend(r for r in data.get('rows', []) if r['doc'] not in seen)
    _done.update(data.get('done', []))
    buys = {}
    for r in _rows:
        if r['side'] == 'Purchase':
            buys.setdefault(r['symbol'], []).append(r['date'])
    _buys.clear(); _buys.update({k: sorted(v) for k, v in buys.items()})

def _load():
    global _loaded
    if _loaded:
        return
    with _lock:
        _loaded = True
        try:
            _merge(json.loads(STORE.read_text()))
        except (OSError, ValueError):
            pass

def _save():
    """Merge with what another process wrote meanwhile (the backfill runs beside the server), then replace the file."""
    with _lock:
        try:
            _merge(json.loads(STORE.read_text()))
        except (OSError, ValueError):
            pass
        STORE.parent.mkdir(exist_ok=True)
        tmp = STORE.with_suffix('.tmp')
        tmp.write_text(json.dumps(dict(done=sorted(_done), rows=_rows)))
        os.replace(tmp, STORE)

def _read(filings, log=None):
    """Read each filing not yet in the store. A filing that fails to download or parse is skipped and tried again next time."""
    ptr, todo = _ptr(), [f for f in filings if f['doc_id'] not in _done]
    for n, f in enumerate(todo, 1):
        try:
            _, report = ptr._fetch_and_read(f['doc_id'], f['year'], str(PDFS), f['filed'])
        except ptr.PtrError as e:
            if log: log(f"  skipped {f['doc_id']}: {e}")
            continue
        if report is None:
            raise RuntimeError('pdftotext not found. Install it with: brew install poppler')
        rows = [dict(doc=f['doc_id'], date=f['filed'], traded=r['date'], symbol=r['ticker'], who=f['name'] + (f" ({r['owner_name']})" if r.get('owner') else ''),
                     side=r['type_name'].capitalize(), range=r['amount'], low=r.get('amount_low')) for r in report['rows'] if r.get('ticker')]
        with _lock:
            _rows.extend(rows); _done.add(f['doc_id'])
        if n % 25 == 0:
            _save()
            if log: log(f'  read {n} of {len(todo)}')
        time.sleep(0.3)  # be gentle with the Clerk's server
    _save()

def backfill(years, log=print):
    """Read every PTR filed in the given years into the store (about 450 to 500 PDFs a year; cached, so safe to rerun)."""
    _load()
    ptr = _ptr()
    for y in years:
        filings = ptr.select(ptr.load_index(y), kind='P')
        log(f'{y}: {len(filings)} filings, {len([f for f in filings if f["doc_id"] not in _done])} to read')
        _read(filings, log)
    log(f'done: {len(_done)} filings, {len(_rows)} trades with a ticker')

def bought(symbol, asof, days):
    """True if a purchase of `symbol` was FILED within `days` days up to and including `asof` (YYYY-MM-DD). Filing date, so no look-ahead."""
    _load()
    lo = (date.fromisoformat(asof) - timedelta(days=days)).isoformat()
    return any(lo <= d <= asof for d in _buys.get(symbol, ()))

def _refresh():
    error = None
    try:
        ptr, today = _ptr(), date.today()
        since = (today - timedelta(days=DAYS)).isoformat()
        _read(ptr.select(ptr.load_index(today.year), kind='P', since=since))
    except Exception as e:
        error = str(e) if isinstance(e, RuntimeError) else f'House Clerk filings unavailable ({type(e).__name__}: {e})'
    with _lock:
        _state.update(busy=False, error=error, checked=time.time() - (TTL - 120 if error else 0))  # after a failure, try again in 2 minutes

def snapshot(symbols, limit=300):
    if not SKILL.is_file():
        return {'configured': False}
    _load()
    start = False
    with _lock:
        if not _state['busy'] and time.time() - _state['checked'] > TTL:
            start = _state['busy'] = True
        cutoff = (date.today() - timedelta(days=DAYS)).isoformat()
        trades = sorted((t for t in _rows if t['symbol'] in symbols and t['date'] >= cutoff), key=lambda t: t['date'], reverse=True)[:limit]
        out = {'configured': True, 'busy': _state['busy'], 'error': _state['error'], 'checked': _state['checked'], 'days': DAYS, 'filings': len(_done),
               'trades': [{k: t.get(k) for k in ('date', 'traded', 'symbol', 'who', 'side', 'range')} for t in trades]}
    if start:
        threading.Thread(target=_refresh, daemon=True).start()
    return out

if __name__ == '__main__':
    import sys
    backfill([int(y) for y in sys.argv[1:]] or [date.today().year])
