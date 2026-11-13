import unittest
from datetime import date, datetime, time
from decimal import Decimal as D
from time import time as time_now
from signaldesk.day import DayEngine, scan

DAY = date(2026, 11, 12)  # a Thursday, regular session
EPOCH = datetime(1970, 1, 1)

def series(bars):
    """bars: [(hh, mm, high, low, close)] -> a Yahoo-shaped 5-minute series for DAY."""
    t = [int((scan.eastern_to_utc(datetime.combine(DAY, time(h, m))) - EPOCH).total_seconds()) for h, m, *_ in bars]
    return dict(t=t, c=[b[4] for b in bars], h=[b[2] for b in bars], l=[b[3] for b in bars], v=[1000] * len(bars), meta={})

RANGE = [(9, 30, 102, 100, 101), (9, 35, 101.8, 100.4, 101.2), (9, 40, 102, 100.2, 101.5)]   # 15-minute range 100-102, middle 101
BREAK = RANGE + [(9, 45, 102.6, 101.9, 102.5)]                                              # the 9:45 bar closes above 102

def status(hm, open=True, day=DAY):
    return dict(open=open, date=str(day), calendar_known=True, now_new_york=f'{day} {hm}', closes='16:00')

def data(price, bars=BREAK, sym='AAA'):
    return {sym: dict(series=series(bars), price=D(str(price)), at=0)}

class DayTests(unittest.TestCase):
    def setUp(self):
        self.alerts = []
        self.e = DayEngine(':memory:', ['AAA', 'BBB'], notify=self.alerts.append)
    def sides(self):
        return [(t['symbol'], t['side']) for t in reversed(self.e.snapshot()['trades'])]
    def buy(self, price='102.6'):
        self.e.apply(status('09:50'), data(price))
        return self.e.s['positions']['AAA']

    def test_breakout_buys_with_the_stop_at_the_range_middle_and_the_size_capped_by_risk_and_per_trade(self):
        p = self.buy()
        self.assertEqual(self.sides(), [('AAA', 'BUY')])
        self.assertEqual(D(p['stop']), D('101.00'))
        self.assertAlmostEqual(float(p['entry']), 102.6 * 1.0005, 3)
        self.assertLessEqual(D(p['cost']), D(1000)); self.assertGreater(D(p['cost']), D(999))   # $1000 cap binds before the $50 risk
        self.assertAlmostEqual(float(p['target']), float(p['entry']) + 2 * (float(p['entry']) - 101), 3)
        self.assertEqual(len(self.alerts), 1)
        e = DayEngine(':memory:', ['AAA']); e.set_rules(dict(per_trade='0'))                   # no cap: the $50 max loss sizes it
        e.apply(status('09:50'), data('102.6')); p = e.s['positions']['AAA']
        self.assertAlmostEqual(float(D(p['qty']) * (D(p['entry']) - D(p['stop']))), 50, 1)
    def test_opening_range_still_forming_and_no_breakout_do_not_buy(self):
        self.e.apply(status('09:40'), data(101, RANGE))
        self.assertIn('forming: 2 of 3', self.e.s['decisions']['AAA'])
        self.e.apply(status('09:50'), data(101.9, RANGE + [(9, 45, 101.9, 101, 101.8)]))
        self.assertIn('No breakout', self.e.s['decisions']['AAA'])
        self.assertEqual(self.e.s['ranges']['AAA'], dict(high=102, low=100, bars=4, hit=None))
        self.assertEqual(self.sides(), [])
    def test_stop_then_one_trade_per_symbol_a_day(self):
        self.buy()
        self.e.apply(status('10:30'), data(100.95))
        self.assertEqual(self.sides(), [('AAA', 'BUY'), ('AAA', 'SELL')])
        self.assertIn('Stop', self.e.snapshot()['trades'][0]['reason']); self.assertLess(D(self.e.snapshot()['trades'][0]['pnl']), 0)
        self.e.apply(status('10:31'), data(103))
        self.assertIn('Done for today', self.e.s['decisions']['AAA'])
        self.assertEqual(len(self.sides()), 2)
    def test_target_and_break_even_stop(self):
        p = self.buy(); entry, risk = D(p['entry']), D(p['entry']) - D(p['stop'])
        self.e.apply(status('10:00'), data(entry + risk * D('1.05')))   # up 1R: the stop moves to the entry, nothing is sold
        self.assertEqual(D(self.e.s['positions']['AAA']['stop']), entry); self.assertEqual(len(self.sides()), 1)
        self.e.apply(status('10:05'), data(entry - D('0.01')))
        self.assertIn('Break-even stop', self.e.snapshot()['trades'][0]['reason'])
        self.e.s['positions'] = {}; self.e.s['traded'] = {}
        p = self.buy(); self.e.apply(status('10:10'), data(D(p['target']) + 1))
        self.assertIn('Target', self.e.snapshot()['trades'][0]['reason']); self.assertGreater(D(self.e.snapshot()['trades'][0]['pnl']), 0)
    def test_early_close_moves_flat_by_and_blocks_entries_after_it(self):
        self.buy()
        early = lambda hm: dict(status(hm), closes='13:00')
        self.e.apply(early('12:54'), data(103)); self.assertIn('AAA', self.e.s['positions'])
        self.e.apply(early('12:55'), data(103)); self.assertNotIn('AAA', self.e.s['positions'])   # 15:55 on a 16:00 close is 12:55 on a 13:00 close
        self.e.s['traded'] = {}
        self.e.apply(early('12:56'), data('102.6')); self.assertNotIn('AAA', self.e.s['positions'])
        self.assertIn('past the flat-by', self.e.s['decisions']['AAA'])
    def test_held_position_without_a_price_is_reported_in_the_heartbeat(self):
        import json, tempfile, pathlib
        from signaldesk import watchdog
        self.buy()
        d = pathlib.Path(tempfile.mkdtemp()); self.e.heartbeat_path = d / 'heartbeat-day.json'; self.e.running = True
        self.e.status = lambda now: status('10:00')
        self.e.fetch = lambda syms, now: ({}, {'AAA': 'No quote: timeout'})
        self.e.tick()
        hb = json.loads(self.e.heartbeat_path.read_text())
        self.assertTrue(hb['ok']); self.assertIn('AAA', hb['unpriced'])        # the loop is healthy, the position is blind
        later = hb['unpriced']['AAA'] + 11 * 60   # still unpriced 11 minutes on, with checks still arriving every minute
        self.assertIn('day:unpriced', watchdog.problems({'day': dict(hb, at=later, last_ok=later)}, later, True))
        self.e.fetch = lambda syms, now: ({'AAA': dict(data(103)['AAA'], at=time_now())}, {}); self.e.tick()   # a stale quote would still count as no price
        self.assertEqual(json.loads(self.e.heartbeat_path.read_text())['unpriced'], {})
    def test_everything_is_sold_at_flat_by_and_a_leftover_sells_at_the_next_sessions_first_check(self):
        self.buy()
        self.e.apply(status('15:54'), data(103)); self.assertIn('AAA', self.e.s['positions'])
        self.e.apply(status('15:55'), data(103)); self.assertNotIn('AAA', self.e.s['positions'])
        self.assertIn('Flat by 15:55', self.e.snapshot()['trades'][0]['reason'])
        self.e.s['positions'] = {}; self.e.s['traded'] = {}
        self.buy()
        self.e.apply(status('16:30', open=False), data(103)); self.assertIn('overnight', self.e.s['decisions']['AAA'])
        self.e.apply(status('09:31', day=date(2026, 11, 13)), data(104, RANGE[:1]))
        self.assertIn('Left over from 2026-11-12', self.e.snapshot()['trades'][0]['reason'])
        self.assertEqual(self.e.s['ranges'], {})  # a new day starts with fresh ranges
    def test_late_breakout_faded_price_and_chased_price_are_skipped(self):
        late = RANGE + [(h, m, 101.9, 101, 101.5) for h in (9, 10, 11) for m in range(45 if h == 9 else 0, 60, 5) if h > 9 or m >= 45] + [(11, 30, 102.6, 101.9, 102.5)]
        self.e.apply(status('11:40'), data(102.6, late))
        self.assertIn('after 11:30', self.e.s['decisions']['AAA'])
        self.e.apply(status('10:30'), data(102.6)); self.assertIn('missed', self.e.s['decisions']['AAA'])   # the 9:45 breakout seen only at 10:30
        self.e.apply(status('09:50'), data(101.9)); self.assertIn('back under', self.e.s['decisions']['AAA'])
        e = DayEngine(':memory:', ['AAA']); e.apply(status('09:55'), data(102.6)); self.assertIn('AAA', e.s['positions'])   # one bar of lag is fine
        self.e.apply(status('09:50'), data(103.2)); self.assertIn('not chasing', self.e.s['decisions']['AAA'])
        self.assertEqual(self.sides(), [])
    def test_trades_per_day_and_daily_loss_block_entries_not_exits(self):
        self.assertIsNone(self.e.set_rules(dict(max_trades='1')))
        both = {**data(102.6), **data(102.6, sym='BBB')}
        self.e.apply(status('09:50'), both)
        self.assertEqual(len(self.e.s['positions']), 1); self.assertIn('1 trades today', self.e.s['decisions']['BBB'])
        self.e.s['day_start'] = str(D(self.e.s['equity']) + 500)   # as if $500 was lost today
        self.e.apply(status('09:51'), {**data(100.9), **data(102.6, sym='BBB')})
        self.assertEqual(self.sides(), [('AAA', 'BUY'), ('AAA', 'SELL')]); self.assertIn('down $', self.e.s['decisions']['BBB'])
    def test_only_an_empty_account_skips(self):
        self.e.s['cash'] = '300'; p = self.buy()
        self.assertGreater(D(p['cost']), D('299.8'))
        self.e.apply(status('09:50'), data(102.6, sym='BBB'))
        self.assertIn('no cash left', self.e.s['decisions']['BBB'])
    def test_rules_are_validated_logged_and_kept(self):
        self.assertIn('multiple of 5', self.e.set_rules(dict(or_minutes='7')))
        self.assertIn('before the flat-by', self.e.set_rules(dict(entries_until='15:56')))
        self.assertIn('Unknown', self.e.set_rules(dict(stop='1')))
        self.assertIn('Watchlist', self.e.set_rules(dict(symbols=['a;b'])))
        self.assertIsNone(self.e.set_rules(dict(or_minutes='30', reward_risk='3', symbols=['nvda'])))
        self.assertEqual(self.e.s['symbols'], ['NVDA']); self.assertEqual(len(self.e.s['rule_log']), 3)
        self.e.apply(status('09:50'), data(102.6, sym='NVDA'))
        self.assertIn('forming: 4 of 6', self.e.s['decisions']['NVDA'])
    def test_tick_fetches_only_in_session_or_while_holding_and_a_failed_check_changes_nothing(self):
        calls = []
        self.e.fetch = lambda syms, now: (calls.append(list(syms)), ({k: dict(v, at=time_now()) for k, v in data(102.6).items()}, {}))[1]
        self.e.status = lambda now: status('16:30', open=False)
        self.e.tick(); self.assertEqual(calls, []); self.assertIn('Market closed', self.e.s['decisions']['AAA'])
        self.e.status = lambda now: status('09:50')
        self.e.tick(); self.assertEqual(calls, [['AAA', 'BBB']]); self.assertIn('AAA', self.e.s['positions'])
        self.e.status = lambda now: status('16:30', open=False)
        self.e.tick(); self.assertEqual(len(calls), 2)   # still held: fetched to show the price
        self.e.fetch = lambda syms, now: (_ for _ in ()).throw(OSError('down'))
        before = dict(self.e.s); self.e.status = lambda now: status('10:00'); self.e.tick()
        self.assertIn('down', self.e.error); self.assertEqual(self.e.s, before)

if __name__ == '__main__':
    unittest.main()

class DayBackupTest(unittest.TestCase):
    def test_day_database_is_backed_up(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as tmp:
            e = DayEngine(pathlib.Path(tmp) / 'day.sqlite', ['AAA'])
            e.backup('2026-11-12')
            self.assertTrue((pathlib.Path(tmp) / 'backups' / 'day-2026-11-12.sqlite').is_file())
            e.conn.close()
