"""Startup and failure drills. They run the real startup path (parse_args + build_app + the HTTP server) on a scratch data folder with the network
replaced by fakes, then break things the way production does: crashes, outages, stale data, a locked database, a laptop that slept, a failing challenger."""
import copy
import json
import socket
import sqlite3
import threading
import time
import unittest
import urllib.request
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch
import tempfile

from signaldesk import server
from signaldesk.app import build_app, parse_args
from signaldesk.swing import CRYPTO, WATCHLIST
from tests.test_swing import hist


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.mode, self.price, self.problems, self.day, self.hm = 'ok', {}, {}, '2026-11-04', '15:55'
        def stocks(syms, now):
            if self.mode == 'down':
                raise ConnectionError('Yahoo unreachable')
            return {s: dict(closes=hist(), price=D(str(self.price.get(s, 110))), at=self.at()) for s in syms}, dict(self.problems)
        self.at = time.time
        self.offline = dict(stocks=stocks, crypto=lambda syms, now: ({}, {}),
                            status=lambda now: dict(open=True, date=self.day, calendar_known=True, closes='16:00', now_new_york=f'{self.day} {self.hm}'))

    def build(self, *flags):
        markets = build_app(parse_args(['--data-dir', str(self.dir), *flags]), self.offline)
        self.markets = markets
        self.addCleanup(lambda: [setattr(m[0], 'heartbeat_path', None) for m in markets.values()])  # background workers must stop writing before the folder is removed
        return markets['stocks'][0], markets['crypto'][0]

    def audit(self, name='decisions-stocks.jsonl'):
        p = self.dir / name
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


class StartupTests(Case):
    def test_the_real_startup_path_boots_both_markets_and_the_challenger_on_the_scratch_folder(self):
        stocks, crypto = self.build('--paused')
        self.assertEqual(len(stocks.followers), 1)
        self.assertEqual((stocks.running, crypto.running), (False, False))      # --paused is honored
        for name in ('stocks.sqlite', 'crypto.sqlite', 'stocks-e.sqlite'):
            self.assertTrue((self.dir / name).is_file(), name)                  # nothing was written to the real data folder
        self.assertEqual(stocks.rules()['target'], D('0.25'))

    def test_the_agent_resumes_by_default_so_stops_keep_being_checked(self):
        stocks, crypto = self.build()
        self.assertTrue(stocks.running and crypto.running)

    def test_served_api_pages_and_controls_work_end_to_end(self):
        stocks, crypto = self.build('--paused')
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
        threading.Thread(target=server.serve, args=(self.markets, port, None), daemon=True).start(); time.sleep(.4)
        stocks.tick(); crypto.tick()
        url = lambda p: f'http://127.0.0.1:{port}{p}'
        state = json.load(urllib.request.urlopen(url('/api/stocks/state')))
        self.assertEqual((state['error'], len(state['positions'])), (None, 3))  # three buys: max 3 new buys a day
        self.assertEqual(state['challenger']['baseline']['since'], state['challenger']['challenger']['since'])
        self.assertIsNone(json.load(urllib.request.urlopen(url('/api/crypto/state')))['error'])
        for page in ('/stocks', '/stocks/activity', '/crypto', '/crypto/watchlist', '/api/congress'):
            self.assertEqual(urllib.request.urlopen(url(page)).status, 200, page)
        post = lambda path: urllib.request.urlopen(urllib.request.Request(url(path), method='POST', headers={'X-App-Token': state['token']}))
        self.assertEqual(post('/api/stocks/pause').status, 200)
        self.assertFalse(stocks.running)


class DrillTests(Case):
    def essentials(self, e):
        s = e.s
        return copy.deepcopy(dict(positions=s['positions'], cash=s['cash'], equity=s['equity'], realized=s['realized'], daily=s.get('daily_pnl'), last_entry=s['last_entry'], track=s.get('track')))

    def test_crash_and_restart_loses_nothing_and_does_not_reclone_the_challenger(self):
        a, _ = self.build('--paused')
        a.tick(); a.tick()
        before, f_before = self.essentials(a), self.essentials(a.followers[0])
        protocol = a.followers[0].s['protocol']
        b, _ = self.build('--paused')                                         # a new process opening the same files; the old one never shut down
        self.assertEqual(self.essentials(b), before)
        self.assertEqual(self.essentials(b.followers[0]), f_before)
        self.assertEqual(b.followers[0].s['protocol'], protocol)              # frozen on its original start date, not re-cloned
        self.assertTrue(b.followers[0].s['frozen'])
        b.tick()
        self.assertIsNone(b.error)

    def test_a_yahoo_outage_changes_nothing_says_so_and_recovers(self):
        a, _ = self.build('--paused')
        a.tick()
        before = self.essentials(a)
        self.mode = 'down'
        for _ in range(3):
            a.tick()
        self.assertIn('Yahoo unreachable', a.error)
        self.assertEqual(self.essentials(a), before)                          # no trade, no phantom price, no lost position
        self.assertIn('TICK_ERROR', [r['action'] for r in self.audit()])
        self.mode = 'ok'
        a.tick()
        self.assertIsNone(a.error)

    def test_a_symbol_without_a_quote_is_not_traded_and_its_exit_waits_while_the_rest_continue(self):
        a, _ = self.build('--paused')
        a.tick()
        held = next(iter(a.s['positions']))
        self.price = {held: 10}                                               # would be a stop-out, but the quote is withheld
        self.problems = {held: 'No quote: timeout'}
        a.symbols_before = len(a.s['positions'])
        with patch.object(a, 'quotes', lambda syms, now: ({s: q for s, q in self.offline['stocks'](syms, now)[0].items() if s != held}, dict(self.problems))):
            a.tick()
        self.assertIn(held, a.s['positions'])
        self.assertIn('No quote', a.s['decisions'][held])                     # the problem is shown; the position is kept and no exit is guessed
        self.assertIn('NO_DATA', [r['action'] for r in self.audit()])

    def test_stale_quotes_are_not_traded(self):
        a, _ = self.build('--paused')
        self.at = lambda: time.time() - 3600
        a.tick()
        self.assertEqual(a.s['positions'], {})
        self.assertTrue(any('stale' in d for d in a.s['decisions'].values()))

    def test_a_locked_database_rolls_the_check_back_and_the_next_one_succeeds(self):
        a, _ = self.build('--paused')
        a.tick()
        before = self.essentials(a)
        real = a.save
        a.save = lambda: (_ for _ in ()).throw(sqlite3.OperationalError('database is locked'))
        self.day = '2026-11-05'
        a.tick()
        self.assertIn('database is locked', a.error)
        self.assertEqual(self.essentials(a), before)                          # memory matches what is on disk
        a.save = real
        a.tick()
        self.assertIsNone(a.error)
        buys = [r['symbol'] for r in self.audit() if r['action'] == 'BUY']
        self.assertEqual(sorted(buys), sorted(a.s['positions']))              # one record per position: nothing lost to the rollback, nothing doubled

    def test_a_laptop_that_slept_through_a_crash_sells_at_the_gapped_price_and_says_it_was_off(self):
        a, _ = self.build('--paused')
        a.tick()
        held = next(iter(a.s['positions']))
        entry = float(a.s['positions'][held]['entry'])
        self.day, self.price = '2026-11-09', {held: entry * .7}              # five days later the stock is down 30%
        a.tick()
        sell = [t for t in a.snapshot()['trades'] if t['symbol'] == held and t['side'] == 'SELL'][0]
        self.assertLess(float(sell['price']), entry * .75)                    # filled at the gap, far past the 10% stop: a stop is a plan, not a guarantee
        self.assertTrue(any('agent was off' in x['text'] for x in a.s['alerts']))

    def test_dashboard_reads_during_checks_never_see_a_broken_snapshot(self):
        a, _ = self.build('--paused')
        errors, stop = [], threading.Event()
        def reader():
            while not stop.is_set():
                try:
                    json.dumps(a.snapshot())
                except Exception as exc:
                    errors.append(repr(exc))
        readers = [threading.Thread(target=reader) for _ in range(4)]
        [t.start() for t in readers]
        for i in range(40):
            self.price = {s: 110 + (i % 3) for s in WATCHLIST}
            a.tick()
        stop.set(); [t.join() for t in readers]
        self.assertEqual(errors, [])
        self.assertIsNone(a.error)

    def test_a_failing_challenger_never_stops_the_baseline_and_recovers(self):
        a, _ = self.build('--paused')
        f = a.followers[0]
        with patch.object(f, 'commit', side_effect=RuntimeError('challenger boom')):
            a.tick()
        self.assertIsNone(a.error)                                            # baseline carried on
        self.assertIn('boom', f.error)
        self.assertIn('TICK_ERROR', [r['action'] for r in self.audit('decisions-stocks-e.jsonl')])
        f.error = None
        a.tick()
        self.assertIsNone(f.error)

    def test_a_baseline_edit_during_the_test_is_flagged_not_hidden(self):
        a, _ = self.build('--paused')
        a.tick()
        a.set_rules({'stop': '0.15'})
        self.assertEqual(len(a.snapshot()['challenger']['rule_changes']), 1)


if __name__ == '__main__':
    unittest.main()
