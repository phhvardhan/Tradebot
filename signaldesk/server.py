"""The local HTTP server: one page and one /api/<market>/ namespace per market, a per-process token for every POST, loopback only."""
import csv
import io
import json
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import congress
from .core import ROOT, host_ok

def serve(markets, port, account, static=None):
    # markets: {name: (engine, page, script)}; the first one is the home page. Pages come from `static` (default: the project's static/), shared files fall back to it.
    where = lambda name: (static / name) if static and (static / name).exists() else ROOT / 'static' / name
    token = secrets.token_urlsafe(32)
    expected_host = f'127.0.0.1:{port}'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, body, kind='application/json', status=200):
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def valid_host(self):
            return host_ok(self.headers.get('Host'), port)

        def do_GET(self):
            if not self.valid_host():
                return self.send(b'Invalid host', 'text/plain', 403)
            path = self.path.split('?')[0].rstrip('/') or '/'
            market, _, rest = path.removeprefix('/api/').partition('/')
            engine = markets.get(market, (None,))[0]
            if engine and rest == 'state':
                return self.send(json.dumps(dict(engine.snapshot(), token=token)).encode())
            if path == '/api/congress':
                return self.send(json.dumps(congress.snapshot(set(getattr(markets.get('stocks', (None,))[0], 'symbols', [])))).encode())
            if path == '/api/account':
                if self.headers.get('X-App-Token') != token:
                    return self.send(b'Forbidden', 'text/plain', 403)
                return self.send(json.dumps(account.snapshot()).encode())
            if engine and rest == 'export' and hasattr(engine, 'conn'):  # the desk has no trade table
                buf = io.StringIO()
                writer = csv.writer(buf)
                with engine.lock:
                    rows = engine.conn.execute('SELECT * FROM trades ORDER BY id')
                    writer.writerow([c[0] for c in rows.description])
                    writer.writerows(rows)
                return self.send(buf.getvalue().encode(), 'text/csv')
            files = {'/style.css': ('style.css', 'text/css'), '/ui.js': ('ui.js', 'text/javascript'), '/': (next(iter(markets.values()))[1], 'text/html')}
            for name, (_, page, script) in markets.items():
                files['/' + name], files['/' + script] = (page, 'text/html'), (script, 'text/javascript')
            name = path.removeprefix('/logos/')
            if name != path and re.fullmatch(r'[A-Z]{1,5}\.(png|svg)', name) and where('logos/' + name).is_file():
                return self.send(where('logos/' + name).read_bytes(), 'image/svg+xml' if name.endswith('svg') else 'image/png')
            page = re.sub(r'^(/(?:stocks|crypto))/(?:watchlist|activity|congress)$', r'\1', path)  # sub pages share one document; stocks.js shows the section for its path
            if page in files:
                name, mime = files[page]
                return self.send(where(name).read_bytes(), mime)
            self.send(b'Not found', 'text/plain', 404)

        def do_POST(self):
            if not self.valid_host() or self.headers.get('X-App-Token') != token:
                return self.send(b'Forbidden', 'text/plain', 403)
            if self.path == '/api/account/refresh':
                threading.Thread(target=account.refresh, daemon=True).start()
                return self.send(b'{"ok":true}')
            market, _, action = self.path.removeprefix('/api/').partition('/')
            if action == 'rules' and hasattr(markets.get(market, (None,))[0], 'set_rules'):
                try:
                    body = json.loads(self.rfile.read(max(0, min(int(self.headers.get('Content-Length') or 0), 10000))))
                except ValueError:
                    body = None
                error = markets[market][0].set_rules(body) if isinstance(body, dict) else 'Expected a JSON object.'
                return self.send(error.encode(), 'text/plain', 400) if error else self.send(b'{"ok":true}')
            if action in ('order', 'ask') and hasattr(markets.get(market, (None,))[0], action):
                try:
                    body = json.loads(self.rfile.read(max(0, min(int(self.headers.get('Content-Length') or 0), 10000))))
                except ValueError:
                    body = None
                error = getattr(markets[market][0], action)(body)
                return self.send(error.encode(), 'text/plain', 400) if error else self.send(b'{"ok":true}')
            if market not in markets or not markets[market][0].control('/api/' + action):
                return self.send(b'Unknown action or pause first', 'text/plain', 400)
            self.send(b'{"ok":true}')

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    for engine, *_ in markets.values():
        threading.Thread(target=engine.worker, daemon=True).start()
    print(f'Paper trading dashboard: http://{expected_host} | markets: {", ".join(markets)}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
