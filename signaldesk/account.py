"""Opt-in account viewer. This module contains no trading or funding operations."""
import copy
import hashlib
import hmac
import json
import threading
import time
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

BASE = 'https://api.wazirx.com'
READ_PATHS = frozenset(('/sapi/v1/account', '/sapi/v1/funds'))

class AccountError(Exception):
    """Messages are safe for the UI: never include signed URLs or response bodies."""

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AccountError('Exchange redirect refused. Check the official API status.')

class ReadOnlyAccount:
    def __init__(self, key, secret):
        if not key or not secret:
            raise AccountError('Both a read-only API key and secret are required.')
        self._key, self._secret = key, secret.encode()
        self._opener = build_opener(NoRedirect())

    def _get(self, path):
        if path not in READ_PATHS:
            raise AccountError('Endpoint is not allowed by the read-only connector.')
        query = urlencode({'recvWindow': 5000, 'timestamp': int(time.time() * 1000)})
        signature = hmac.new(self._secret, query.encode(), hashlib.sha256).hexdigest()
        request = Request(BASE + path + '?' + query + '&signature=' + signature,
                          headers={'X-Api-Key': self._key}, method='GET')
        try:
            with self._opener.open(request, timeout=10) as response:
                return json.load(response)
        except AccountError:
            raise
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise AccountError('Account access refused. Check key permissions and IP restrictions.') from None
            if exc.code == 429:
                raise AccountError('Exchange rate limit reached. Wait before refreshing.') from None
            raise AccountError(f'Exchange request failed (HTTP {exc.code}). Check your key and system clock.') from None
        except Exception:
            raise AccountError('Account request failed. Check connectivity and the exchange status.') from None

    def fetch(self):
        info = self._get('/sapi/v1/account')
        funds = self._get('/sapi/v1/funds')
        if not isinstance(info, dict) or not isinstance(info.get('accountType'), str) or not isinstance(funds, list):
            raise AccountError('Unexpected account response. No balances were updated.')
        balances, seen = [], set()
        try:
            for row in funds:
                asset = row['asset']
                if not isinstance(asset, str) or not asset.isascii() or not asset.isalnum() or len(asset) > 20 or asset.lower() in seen:
                    raise ValueError('Invalid asset')
                seen.add(asset.lower())
                values = [Decimal(str(row[k])) for k in ('free', 'locked')]
                reserved = Decimal(str(row.get('reservedFee', '0')))
                if any(not n.is_finite() or n < 0 for n in values + [reserved]):
                    raise ValueError('Invalid balance')
                if any(n != 0 for n in values + [reserved]):
                    balances.append(dict(asset=asset.upper(), free=str(values[0]), locked=str(values[1]), reserved_fee=str(reserved)))
        except (KeyError, TypeError, ValueError, InvalidOperation):
            raise AccountError('Unexpected balance data. No balances were updated.') from None
        # Account-level canTrade/canWithdraw are not proof of this key's permissions.
        return {'balances': sorted(balances, key=lambda b: b['asset']), 'updated': time.time()}

class AccountMonitor:
    """Snapshots stay only in memory; failed refreshes visibly invalidate freshness."""
    def __init__(self, client=None):
        self.client = client
        self.lock = threading.RLock()
        self.busy = False
        self.last_attempt = None
        self.state = dict(enabled=client is not None, balances=[], updated=None, error=None, stale=True)

    def refresh(self):
        with self.lock:
            now = time.monotonic()
            if self.client is None or self.busy or (self.last_attempt is not None and now - self.last_attempt < 15):
                return False
            self.busy, self.last_attempt = True, now
        try:
            result = self.client.fetch()
            with self.lock:
                self.state.update(result, error=None, stale=False)
        except Exception as exc:
            with self.lock:
                self.state.update(stale=True, error=str(exc) if isinstance(exc, AccountError) else 'Account refresh failed. Previous balances may be stale.')
        finally:
            with self.lock:
                self.busy = False
        return True

    def snapshot(self):
        with self.lock:
            result = copy.deepcopy(self.state)
            if result['updated'] is None or time.time() - result['updated'] > 60:
                result['stale'] = True
            return dict(result, busy=self.busy)
