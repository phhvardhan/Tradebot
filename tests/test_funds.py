import unittest
from datetime import date, timedelta
from decimal import Decimal as D
from signaldesk.funds import DEFAULT_RULES, FUNDS, SipEngine, sip_plan, xirr

def flat(start=date(2025, 1, 1), days=500, nav=100):
    return [(start + timedelta(i), D(nav)) for i in range(days)]

class SipTests(unittest.TestCase):
    def plan(self, **kw):
        rules = dict(DEFAULT_RULES, **kw.pop('rules', {}))
        return sip_plan(kw.pop('series', flat()), kw.pop('cat', 'index'), date(2025, 1, 1), kw.pop('upto', date(2025, 6, 30)), kw.pop('monthly', 1000), rules, kw.pop('goal', 10))
    def test_one_buy_per_calendar_month_at_the_first_nav(self):
        p = self.plan()
        self.assertEqual([b[0] for b in p], ['2025-01', '2025-02', '2025-03', '2025-04', '2025-05', '2025-06'])
        self.assertEqual({b[3] for b in p}, {D(1000)})
        self.assertEqual(p[1][1], date(2025, 2, 1))
    def test_step_up_raises_the_amount_each_year_only(self):
        p = self.plan(upto=date(2026, 2, 28), rules=dict(step_up='.1'))
        self.assertEqual((p[11][3], p[12][3]), (D(1000), D(1100)))
    def test_months_already_done_are_skipped(self):
        p = sip_plan(flat(), 'index', date(2025, 1, 1), date(2025, 4, 30), 1000, DEFAULT_RULES, 10, after='2025-02')
        self.assertEqual([b[0] for b in p], ['2025-03', '2025-04'])
    def test_short_goal_blocks_equity_and_small_caps_but_not_bonds(self):
        self.assertIn('needs 5', self.plan(goal=3)[0][4])
        self.assertIsNone(self.plan(goal=6, cat='index')[0][4])
        self.assertIn('needs 7', self.plan(goal=6, cat='small')[0][4])
        self.assertIsNone(self.plan(goal=1, cat='bond')[0][4])
    def test_mid_and_large_caps_need_the_equity_horizon_and_liquid_funds_none(self):
        self.assertIn('needs 5', self.plan(goal=3, cat='mid')[0][4]); self.assertIsNone(self.plan(goal=1, cat='liquid')[0][4])
    def test_hot_theme_pauses_only_thematic_funds_and_is_off_by_default(self):
        s = [(date(2025, 1, 1) + timedelta(i), D(100) if i < 300 else D(150)) for i in range(500)]
        late = dict(upto=date(2026, 5, 1), series=s)
        self.assertEqual([b[4] for b in sip_plan(s, 'thematic', date(2026, 3, 1), date(2026, 3, 31), 1000, DEFAULT_RULES, 10)], [None])
        hot = dict(DEFAULT_RULES, hot_theme='.3')
        self.assertIn('up 50%', sip_plan(s, 'thematic', date(2026, 3, 1), date(2026, 3, 31), 1000, hot, 10)[0][4])
        self.assertIsNone(sip_plan(s, 'index', date(2026, 3, 1), date(2026, 3, 31), 1000, hot, 10)[0][4])
    def test_xirr_of_a_doubling_in_one_year_is_about_100_percent(self):
        self.assertAlmostEqual(xirr([(date(2025, 1, 1), D(-100)), (date(2026, 1, 1), D(200))]), 1.0, places=2)
        self.assertIsNone(xirr([(date(2025, 1, 1), D(-100))]))

class EngineTests(unittest.TestCase):
    def engine(self, fetch=None, today=date(2025, 3, 15)):
        self.e = SipEngine(':memory:', fetch or (lambda f: flat(date(2025, 1, 1), 100)), lambda: today, [f for f in FUNDS if f['id'] in ('IN120716', 'VFIAX')])
        self.e.s['started'] = '2025-01-01'
        return self.e
    def test_tick_buys_each_month_once_and_a_second_tick_adds_nothing(self):
        e = self.engine(); e.tick()
        st = e.s['funds']['IN120716']
        self.assertEqual((st['last_month'], D(st['units']), D(st['invested'])), ('2025-03', D(150), D(15000)))
        e.tick()
        self.assertEqual(D(e.s['funds']['IN120716']['units']), D(150))
    def test_snapshot_totals_are_separate_per_currency(self):
        e = self.engine(); e.tick(); t = e.snapshot()['totals']
        self.assertEqual(set(t), {'INR', 'USD'}); self.assertEqual(t['INR']['invested'], '15000.00'); self.assertEqual(t['USD']['invested'], '600.00')
    def test_a_failed_fetch_buys_nothing_and_says_why(self):
        def bad(f): raise ValueError('boom')
        e = self.engine(bad); e.tick()
        self.assertIn('boom', e.error); self.assertEqual(D(e.s['funds']['IN120716']['units']), 0)
        self.assertIn('No NAV', e.s['funds']['IN120716']['error'])
    def test_blocked_month_is_journaled_once_and_not_retried(self):
        e = self.engine(); self.assertIsNone(e.set_rules({'funds': {'IN120716': {'goal_years': 2}}})); e.tick(); e.tick()
        rows = e.conn.execute("SELECT side FROM trades WHERE fund='IN120716'").fetchall()
        self.assertEqual(rows, [('SKIP',)] * 3)
    def test_add_fund_checks_live_data_saves_it_and_rejects_bad_input(self):
        e = self.engine()
        self.assertIsNone(e.set_rules({'add_fund': dict(src='yahoo', code='vtsax', cat='index', monthly=50, name='Total Stock')}))
        self.assertIn('VTSAX', e.funds); self.assertEqual(e.snapshot()['funds']['VTSAX']['cur'], 'USD')
        self.assertIn('already', e.set_rules({'add_fund': dict(src='yahoo', code='VTSAX', cat='index')}))
        self.assertIn('Pick', e.set_rules({'add_fund': dict(src='yahoo', code='../x', cat='index')}))
        self.assertIn('Pick', e.set_rules({'add_fund': dict(src='mfapi', code='abc', cat='index')}))
        self.assertIn('Pick', e.set_rules({'add_fund': dict(src='yahoo', code='ABC', cat='crypto')}))
        e.fetch = lambda f: (_ for _ in ()).throw(ValueError('nope'))
        self.assertIn('Could not load', e.set_rules({'add_fund': dict(src='yahoo', code='ZZZ', cat='bond')}))
        self.assertNotIn('ZZZ', e.funds)
    def test_added_funds_survive_a_restart(self):
        import os, tempfile
        db = os.path.join(tempfile.mkdtemp(), 'f.sqlite'); ok = lambda f: flat(date(2025, 1, 1), 100)
        e = SipEngine(db, ok, lambda: date(2025, 3, 15), []); self.assertIsNone(e.set_rules({'add_fund': dict(src='yahoo', code='VTSAX', cat='index', name='T')}))
        self.assertIn('VTSAX', SipEngine(db, ok, lambda: date(2025, 3, 15), []).funds)
    def test_rule_validation(self):
        e = self.engine()
        self.assertIn('Step-up', e.set_rules({'step_up': 2})); self.assertIn('Unknown fund', e.set_rules({'funds': {'NOPE': {}}}))
        self.assertIn('Goal', e.set_rules({'funds': {'IN120716': {'goal_years': 0}}})); self.assertIn('numbers', e.set_rules({'step_up': 'x'}))

if __name__ == '__main__':
    unittest.main()

class WorkerTests(unittest.TestCase):
    def test_a_failing_check_or_backup_never_stops_the_worker(self):
        import pathlib, tempfile, threading, time
        e = SipEngine(':memory:', fetch=lambda f: flat())
        calls = []
        def tick():
            calls.append(1)
            if len(calls) == 1:
                raise OSError('disk gone')
        e.tick, e.interval, e.running = tick, 0.01, True
        threading.Thread(target=e.worker, daemon=True).start()
        time.sleep(0.2)
        self.assertGreater(len(calls), 1)                         # it kept checking after the failure
        self.assertIn('disk gone', e.error)
        with tempfile.TemporaryDirectory() as tmp:
            f = SipEngine(pathlib.Path(tmp) / 'funds.sqlite', fetch=lambda x: flat())
            (pathlib.Path(tmp) / 'backups').write_text('not a folder')
            f.backup('2026-11-12')                                # raises nothing
            self.assertIn('Backup of funds.sqlite failed', f.backup_error)
            self.assertIn('Backup of funds.sqlite failed', f.snapshot()['error'])
            (pathlib.Path(tmp) / 'backups').unlink(); f.backup('2026-11-12')
            self.assertIsNone(f.backup_error)                      # retried and cleared
            f.conn.close()

