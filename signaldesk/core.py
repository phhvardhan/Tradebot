"""Shared pieces: decimals, project paths, the trader-skill loader, the clock and the Runner every market engine is built on."""
import importlib.util
import math
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent            # the project folder: data/, static/, vendor/ live here
SKILLS = Path(os.environ.get('TRADER_SKILLS') or (ROOT / 'vendor' if (ROOT / 'vendor').is_dir() else Path.home() / '.claude' / 'skills'))
D = Decimal

def skill(folder, name):
    """Load one of the trader skills' python files (scanner, alerts, news, pelosi) from SKILLS as a module."""
    spec = importlib.util.spec_from_file_location('skill_' + name, SKILLS / folder / (name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def positive(value):
    try:
        x = D(str(value))
    except InvalidOperation:
        raise ValueError('Expected a number') from None
    if not x.is_finite() or x <= 0:
        raise ValueError('Expected a finite positive number')
    return x

def stamp(value):
    x = float(value)
    if not math.isfinite(x):
        raise ValueError('Invalid timestamp')
    return x / 1000 if x > 100_000_000_000 else x

def host_ok(host, port):
    # Exact Host match blocks DNS rebinding; both loopback spellings are fine.
    return host in (f'127.0.0.1:{port}', f'localhost:{port}')

BACKUPS = 14

class Runner:
    """Start/pause/step controls and the polling loop. Needs lock, running, wake, tick, interval."""
    def control(self, path):
        with self.lock:
            if path == '/api/start':
                self.running = True
            elif path == '/api/pause':
                self.running = False
            elif path == '/api/step' and not self.running:
                threading.Thread(target=self.tick, daemon=True).start()
            else:
                return False
            self.error = None
        self.wake.set()
        return True

    def backup(self, day):
        """One copy of the database a day in data/backups/, newest BACKUPS kept. Needs conn and lock.
        A failure never stops trading: it is kept in backup_error (heartbeat, dashboard) and retried at the next check."""
        db = self.conn.execute('PRAGMA database_list').fetchone()[2]
        if not db or getattr(self, 'backed', None) == day:
            return  # in-memory database, or already copied today
        try:
            db = Path(db); folder = db.parent / 'backups'
            folder.mkdir(exist_ok=True)
            dst = sqlite3.connect(folder / f'{db.stem}-{day}.sqlite')
            try:
                with self.lock:
                    self.conn.backup(dst)
            finally:
                dst.close()
            for old in sorted(folder.glob(f'{db.stem}-[0-9]*.sqlite'))[:-BACKUPS]:  # the digit keeps stocks-e-* out of stocks' list
                old.unlink()
        except (OSError, sqlite3.Error) as exc:
            self.backup_error = f'Backup of {db.name} failed: {type(exc).__name__}: {exc}'
            return
        self.backed, self.backup_error = day, None

    def worker(self):
        while True:
            began = time.monotonic()
            with self.lock:
                run = self.running
            if run:
                try:
                    self.tick()
                except Exception as exc:  # the loop must outlive any one bad check, or the agent stops while still showing Running
                    with self.lock:
                        self.error, self.busy = f'Check failed: {type(exc).__name__}: {exc}', False
            elif hasattr(self, 'beat'):
                self.beat()  # paused is not dead: say so, so the watchdog can tell the two apart
            self.wake.wait(max(0, self.interval - (time.monotonic() - began)))
            self.wake.clear()

def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)
