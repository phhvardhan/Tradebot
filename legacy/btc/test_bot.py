import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import threading, time
from signaldesk.core import D, host_ok, positive
from legacy.btc.bot import Engine, Market, validate_market


def candles(up=True, end=10000):
    return [(end - (20-i)*300, D(100+i if up else 120-i)) for i in range(21)]

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / 'test.sqlite')
        self.e = Engine(self.db, Market(True))
    def tearDown(self):
        self.e.conn.close()
        self.tmp.cleanup()
    def buy(self):
        self.e.apply(candles(), D('119.9'), D('120'), 10001)
    def test_round_trip_accounts_for_both_fees_and_slippage(self):
        self.buy()
        self.assertGreater(D(self.e.s['qty']), 0)
        self.assertGreaterEqual(D(self.e.s['cash']), D(3200))
        self.e.apply(candles(False, 10300), D(118), D('118.1'), 10301)
        s = self.e.snapshot()
        self.assertEqual(D(s['qty']), 0)
        self.assertEqual(D(s['cash']) - D(s['capital']), D(s['realized']))
        self.assertEqual(D(s['fees']), sum(D(t['fee']) for t in s['trades']))
        self.assertEqual([t['side'] for t in s['trades']], ['SELL', 'BUY'])
    def test_repeated_check_does_not_duplicate_entry(self):
        self.buy(); self.buy()
        self.assertEqual(len(self.e.snapshot()['trades']), 1)
    def test_stop_sells_at_gap_price_not_promised_stop(self):
        self.buy()
        self.e.apply(candles(), D(100), D('100.1'), 10002)
        trade = self.e.snapshot()['trades'][0]
        self.assertEqual(trade['side'], 'SELL')
        self.assertEqual(D(trade['price']), D('99.900'))
        self.buy()
        self.assertEqual(len(self.e.snapshot()['trades']), 2)
    def test_drawdown_blocks_new_buys(self):
        self.e.s['cash'] = '3790'
        self.buy()
        self.assertEqual(len(self.e.snapshot()['trades']), 0)
        self.assertIn('5%', self.e.s['decision'])
    def test_wide_spread_blocks_buy(self):
        self.e.apply(candles(), D(110), D(120), 10001)
        self.assertEqual(len(self.e.snapshot()['trades']), 0)
    def test_restart_preserves_position_and_resumes_paused(self):
        self.buy()
        other = Engine(self.db, Market(True))
        try:
            self.assertEqual(other.s['qty'], self.e.s['qty'])
            self.assertEqual(len(other.snapshot()['trades']), 1)
            self.assertFalse(other.running)
        finally:
            other.conn.close()
    def test_fetch_failure_changes_no_balance_or_trades(self):
        with patch.object(self.e.market, 'get', side_effect=OSError('offline')):
            self.e.tick()
        self.assertEqual(self.e.s['cash'], '4000')
        self.assertEqual(self.e.snapshot()['trades'], [])
        self.assertIn('offline', self.e.error)
    def test_database_failure_rolls_back_trade_and_memory(self):
        data = (candles(), D('119.9'), D(120), 10001)
        with patch.object(self.e.market, 'get', return_value=data), patch.object(self.e, 'save', side_effect=OSError('disk full')):
            self.e.tick()
        self.assertEqual(self.e.s['cash'], '4000')
        self.assertEqual(self.e.snapshot()['trades'], [])
    def test_synthetic_scenario_produces_entries_and_exits(self):
        for _ in range(100): self.e.tick()
        sides = {t['side'] for t in self.e.snapshot()['trades']}
        self.assertEqual(sides, {'BUY', 'SELL'})
        self.assertGreaterEqual(D(self.e.s['cash']), 0)

    def test_no_reentry_on_the_candle_that_just_exited(self):
        self.buy()
        hi = D(self.e.s['entry']) * D('1.05')
        later = [(t + 300, v + 1) for t, v in candles()]
        self.e.apply(later, hi, hi + D('.05'), 10301)
        self.e.apply(later, hi, hi + D('.05'), 10361)
        self.assertEqual([t['side'] for t in self.e.snapshot()['trades']], ['SELL', 'BUY'])
        self.assertIn('exited on this', self.e.s['decision'])
    def test_start_wakes_worker_immediately(self):
        self.e.market.demo = False  # 60s interval
        ticked = threading.Event()
        with patch.object(self.e, 'tick', side_effect=ticked.set):
            threading.Thread(target=self.e.worker, daemon=True).start()
            time.sleep(.1)
            self.e.control('/api/start')
            self.assertTrue(ticked.wait(2), 'worker did not tick promptly after start')
    def test_pause_and_start_clear_stale_error(self):
        self.e.error = 'Market update blocked: timeout'
        self.assertTrue(self.e.control('/api/pause'))
        self.assertIsNone(self.e.error)
    def test_step_refused_while_running(self):
        self.e.control('/api/start')
        self.assertFalse(self.e.control('/api/step'))
    def test_equal_settings_written_differently_reopen(self):
        Engine(self.db, Market(True), capital=D('4000.00'), fee=D('0.0040')).conn.close()

    def gated(self, check):
        e = Engine(':memory:', Market(True), edge_check=check)
        e.refresh_edge(D('.001'))
        e.apply(candles(), D('119.9'), D('120'), 10001)
        return e
    def test_losing_replay_blocks_new_entries(self):
        e = self.gated(lambda spread: dict(round_trips=19, realized=D(-200)))
        self.assertEqual(e.snapshot()['trades'], [])
        self.assertIn('replay', e.s['decision'])
    def test_profitable_replay_allows_entry(self):
        e = self.gated(lambda spread: dict(round_trips=8, realized=D(25)))
        self.assertEqual([t['side'] for t in e.snapshot()['trades']], ['BUY'])
    def test_too_few_replay_trades_blocks_entry(self):
        e = self.gated(lambda spread: dict(round_trips=2, realized=D(25)))
        self.assertEqual(e.snapshot()['trades'], [])
    def test_failed_edge_check_blocks_entry_but_not_exit(self):
        e = self.gated(lambda spread: dict(round_trips=8, realized=D(25)))
        e.edge_check = lambda spread: 1 / 0
        e.edge['at'] = 0
        e.refresh_edge(D('.001'))
        e.apply(candles(False, 10300), D(118), D('118.1'), 10301)
        self.assertEqual([t['side'] for t in e.snapshot()['trades']], ['SELL', 'BUY'])
        e.apply(candles(True, 10600), D('119.9'), D('120'), 10601)
        self.assertEqual(len(e.snapshot()['trades']), 2)
        self.assertIn('unavailable', e.s['decision'])
    def test_snapshot_flags_more_than_100_trades(self):
        for _ in range(100): self.e.trade('BUY', D(1), D(1), D(0), D(0), 'x')
        self.assertFalse(self.e.snapshot()['more'])
        self.e.trade('BUY', D(1), D(1), D(0), D(0), 'x')
        s = self.e.snapshot()
        self.assertEqual((len(s['trades']), s['more']), (100, True))

class ServerTests(unittest.TestCase):
    def test_bad_number_is_a_value_error_for_argparse(self):
        with self.assertRaises(ValueError): positive('4k')
    def test_localhost_and_ip_accepted_other_hosts_rejected(self):
        self.assertTrue(host_ok('127.0.0.1:8765', 8765))
        self.assertTrue(host_ok('localhost:8765', 8765))
        self.assertFalse(host_ok('evil.test:8765', 8765))
        self.assertFalse(host_ok('localhost:9999', 8765))
    def test_buttons_disabled_until_token_loaded(self):
        html = (Path(__file__).parent / 'static' / 'index.html').read_text()
        for b in ('start', 'pause', 'step'):
            self.assertRegex(html, r'<button[^>]*id="%s"[^>]*disabled' % b)

class DataTests(unittest.TestCase):
    def setUp(self):
        self.now=100000
        self.rows=[[self.now-(22-i)*300,0,0,0,100+i,1] for i in range(22)]
        self.quote={'at':self.now,'bidPrice':'120','askPrice':'120.1'}
    def test_incomplete_candle_is_excluded(self):
        self.rows.append([self.now,0,0,0,999999,1])
        result=validate_market(self.rows,self.quote,self.now)
        self.assertEqual(result[0][-1][1],D(121))
    def test_stale_quote_rejected(self):
        self.quote['at']-=121
        with self.assertRaises(ValueError): validate_market(self.rows,self.quote,self.now)
    def test_gap_rejected(self):
        self.rows[-2][0]-=10
        with self.assertRaises(ValueError): validate_market(self.rows,self.quote,self.now)
    def test_nan_price_rejected(self):
        self.quote['askPrice']='NaN'
        with self.assertRaises(ValueError): validate_market(self.rows,self.quote,self.now)
    def test_stale_candles_rejected(self):
        for row in self.rows: row[0]-=900
        with self.assertRaises(ValueError): validate_market(self.rows,self.quote,self.now)

if __name__=='__main__': unittest.main()
