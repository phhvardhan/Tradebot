import unittest
from datetime import date, timedelta
from decimal import Decimal as D
from unittest.mock import patch
import time
from signaldesk.quotes import crypto_status
from signaldesk.swing import StockEngine, replay

DAY0 = date(2026, 9, 1)

def hist(last=110, n=40):  # flat at 100, then the last close breaks the 20-day high
    return [(DAY0 + timedelta(i), 100.0) for i in range(n - 1)] + [(DAY0 + timedelta(n - 1), float(last))]

def q(price, closes=None):
    return dict(closes=closes or hist(), price=D(str(price)), at=0)

class StockTests(unittest.TestCase):
    def setUp(self):
        self.alerts = []
        self.e = StockEngine(':memory:', None, ['AAA', 'BBB'], notify=self.alerts.append)
    def sides(self):
        return [(t['symbol'], t['side']) for t in reversed(self.e.snapshot()['trades'])]
    def buy(self, day='2026-10-26', price=110):
        self.e.apply(day, {'AAA': q(price)}, True)
        return self.e.s['positions']['AAA']

    def test_a_reported_split_adjusts_the_position_instead_of_reading_as_a_crash(self):
        self.e.quotes = lambda *a: ({}, {})                                                    # a live engine
        p = self.buy('2026-10-26', 110); qty, cost = D(p['qty']), D(p['cost'])
        self.e.apply('2026-10-28', {'AAA': dict(q(28), splits=[('2026-10-28', D(4))])}, True)  # 4-for-1: 110 becomes 27.5, then up a little
        p = self.e.s['positions']['AAA']
        self.assertEqual((D(p['qty']), D(p['cost'])), (qty * 4, cost))
        self.assertAlmostEqual(float(p['entry']), 110 * 1.0005 / 4, places=4)
        self.assertEqual(self.sides(), [('AAA', 'BUY')])                                      # no false stop
        self.e.apply('2026-10-29', {'AAA': dict(q(28), splits=[('2026-10-28', D(4))])}, True)
        self.assertEqual(D(self.e.s['positions']['AAA']['qty']), qty * 4)                     # applied once
        self.assertTrue(any('split 4-for-1' in a['text'] for a in self.e.s['alerts']))
    def test_an_unexplained_jump_holds_exits_for_a_session_then_a_real_move_is_acted_on(self):
        self.e.quotes = lambda *a: ({}, {})
        self.buy('2026-10-26', 110)
        self.e.apply('2026-10-27', {'AAA': q(11)}, True)                                       # a bad print, or a split Yahoo did not report
        self.assertIn('AAA', self.e.s['positions'])
        self.assertIn('exits wait a session', self.e.s['decisions']['AAA'])
        self.assertEqual(sum('no split is reported' in a['text'] for a in self.e.s['alerts']), 1)
        self.e.apply('2026-10-27', {'AAA': q(109)}, True)                                      # the quote comes back the same day: nothing happened
        self.assertNotIn('suspect', self.e.s['positions']['AAA'])
        self.e.apply('2026-10-27', {'AAA': q(50)}, True)                                       # a real collapse
        self.assertIn('AAA', self.e.s['positions'])
        self.e.apply('2026-10-28', {'AAA': q(50)}, True)                                       # still there next session, no split: the stop fires
        self.assertNotIn('AAA', self.e.s['positions'])
        self.assertIn('Stop', self.e.snapshot()['trades'][0]['reason'])
    def test_the_replay_never_holds_exits_for_a_jump(self):
        self.buy('2026-10-26', 110)                                                            # quotes=None: a replay
        self.e.apply('2026-10-27', {'AAA': q(50)}, True)
        self.assertNotIn('AAA', self.e.s['positions'])
    def test_breakout_buys_at_most_500_dollars(self):
        p = self.buy()
        self.assertLessEqual(D(p['cost']), D(500))
        self.assertGreater(D(p['cost']), D(499))
        self.assertEqual(self.sides(), [('AAA', 'BUY')])
    def test_daily_pnl_tracks_account_value_change_including_open_positions(self):
        self.buy('2026-10-26', 110)
        self.e.apply('2026-10-27', {'AAA': q(121)}, True)    # held, price up ~10%: open gain counts before any sale
        self.assertEqual(set(self.e.snapshot()['daily']), {'2026-10-26', '2026-10-27'})
        self.assertGreater(D(self.e.snapshot()['daily']['2026-10-27']), 0)
        self.e.apply('2026-10-28', {'AAA': q(121)}, False)   # a closed day that was never traded is not recorded
        self.assertNotIn('2026-10-28', self.e.snapshot()['daily'])
    def test_max_open_caps_positions_and_zero_means_no_limit(self):
        self.assertIsNone(self.e.set_rules({'max_open': 1}))
        self.e.apply('2026-10-26', {'AAA': q(110), 'BBB': q(110)}, True)
        self.assertEqual(list(self.e.s['positions']), ['AAA'])
        self.assertIn('1 positions open (limit 1)', self.e.s['decisions']['BBB'])
        self.assertIn('whole number', self.e.set_rules({'max_open': 1.5}))
        self.assertIsNone(self.e.set_rules({'max_open': 0}))
        self.e.apply('2026-10-27', {'BBB': q(110)}, True)
        self.assertEqual(set(self.e.s['positions']), {'AAA', 'BBB'})
    def test_trailing_stop_sells_after_a_pullback_from_the_high_and_is_off_by_default(self):
        self.buy('2026-10-26', 110)
        self.e.apply('2026-10-27', {'AAA': q(150)}, True); self.e.apply('2026-10-28', {'AAA': q(125)}, True)
        self.assertIn('AAA', self.e.s['positions'])  # off by default: -17% from the high is not a sale
        self.assertIsNone(self.e.set_rules({'trail': 0.15}))
        self.e.apply('2026-10-29', {'AAA': q(125)}, True)
        self.assertNotIn('AAA', self.e.s['positions'])
        self.assertIn('Trailing stop', self.e.snapshot()['trades'][0]['reason'])
        self.assertIn('between 0% and 100%', self.e.set_rules({'trail': 1.2}))
    def test_breakeven_sells_at_entry_only_after_the_trigger_was_reached_and_is_off_by_default(self):
        self.assertIsNone(self.e.set_rules({'breakeven': 0.05}))
        self.buy('2026-10-26', 110)
        self.e.apply('2026-10-27', {'AAA': q(109)}, True)
        self.assertIn('AAA', self.e.s['positions'])      # trigger never reached: a small dip is not a sale
        self.e.apply('2026-10-28', {'AAA': q(118)}, True); self.e.apply('2026-10-29', {'AAA': q(110)}, True)
        self.assertNotIn('AAA', self.e.s['positions'])
        self.assertIn('Break-even stop', self.e.snapshot()['trades'][0]['reason'])
        self.assertIn('between 0% and 100%', self.e.set_rules({'breakeven': 1.2}))
    def test_max_sector_blocks_a_second_buy_in_the_same_sector(self):
        e = StockEngine(':memory:', None, ['AAPL', 'MSFT', 'JPM'])
        self.assertIsNone(e.set_rules({'max_sector': 1}))
        e.apply('2026-10-26', {s: q(110) for s in ('AAPL', 'MSFT', 'JPM')}, True)
        self.assertEqual(set(e.s['positions']), {'AAPL', 'JPM'})   # MSFT is tech like AAPL; JPM is finance
        self.assertIn('tech positions already open', e.s['decisions']['MSFT'])
    def test_channel_exit_sells_under_the_n_day_closing_low_and_is_off_by_default(self):
        self.buy('2026-10-26', 110)
        dip = lambda p: q(p, closes=[(DAY0 + timedelta(i), 100.0 if i < 39 else 110.0) for i in range(40)])
        self.e.apply('2026-10-27', {'AAA': dip(105)}, True)
        self.assertIn('AAA', self.e.s['positions'])                   # off: a 105 price is not a sale
        self.assertIsNone(self.e.set_rules({'exit_low': 20, 'target': 100}))
        self.e.apply('2026-10-28', {'AAA': dip(105)}, True)
        self.assertIn('AAA', self.e.s['positions'])                   # above the 20-day closing low of 100
        self.e.apply('2026-10-29', {'AAA': dip(99.5)}, True)
        self.assertNotIn('AAA', self.e.s['positions'])
        self.assertIn('Channel exit', self.e.snapshot()['trades'][0]['reason'])
        self.assertIn('whole number', self.e.set_rules({'exit_low': 2.5}))
    def test_news_note_is_added_to_the_buy_reason_and_never_blocks(self):
        self.e.news = lambda sym: 'News: no headlines in 48h.'
        self.buy()
        self.assertTrue(self.e.snapshot()['trades'][0]['reason'].endswith('News: no headlines in 48h.'))
        self.e.s['positions'].clear(); self.e.s['last_entry'].clear()
        self.e.news = lambda sym: ''
        self.assertEqual(self.buy('2026-10-27')['opened'], '2026-10-27')
    def test_congress_rule_blocks_breakouts_without_a_recent_purchase_and_is_off_by_default(self):
        seen = []
        self.e.congress = lambda sym, day, days: seen.append((sym, day, days)) or False
        self.e.apply('2026-10-26', {'AAA': q(110)}, True)
        self.assertIn('AAA', self.e.s['positions'])  # off by default: bought, filter never asked
        self.assertEqual(seen, [])
        self.e.s['positions'].clear(); self.e.s['last_entry'].clear()
        self.assertIsNone(self.e.set_rules({'congress_days': 45}))
        self.e.apply('2026-10-27', {'AAA': q(110)}, True)
        self.assertNotIn('AAA', self.e.s['positions'])
        self.assertEqual(seen, [('AAA', '2026-10-27', 45)])
        self.assertIn('no congressional purchase', self.e.s['decisions']['AAA'])
        self.e.congress = lambda sym, day, days: True
        self.e.apply('2026-10-28', {'AAA': q(110)}, True)
        self.assertIn('AAA', self.e.s['positions'])
    def test_congress_days_must_be_a_whole_number_in_range(self):
        for bad in ('-1', '2.5', '400', 'x'):
            self.assertIsNotNone(self.e.set_rules({'congress_days': bad}), bad)
    def test_quotes_kept_for_cards(self):
        self.e.apply('2026-10-26', {'BBB': q(105, hist(last=100))}, True)
        price, prev, closes, at = self.e.s['quotes']['BBB']  # price, last daily close, last 30 closes for the card chart, quote time
        self.assertEqual((price, prev, len(closes), closes[-1], at), ('105', '100.0', 30, 100.0, 0))
    def test_no_signal_no_buy(self):
        self.e.apply('2026-10-26', {'AAA': q(100, hist(last=100))}, True)
        self.assertEqual(self.sides(), [])
        self.assertIn('No signal', self.e.s['decisions']['AAA'])
    def test_stop_at_minus_20_percent_not_before(self):
        entry = D(self.buy()['entry'])
        self.e.apply('2026-10-27', {'AAA': q(entry * D('.81'))}, True)
        self.assertIn('AAA', self.e.s['positions'])
        self.e.apply('2026-10-27', {'AAA': q(entry * D('.80'))}, True)
        self.assertEqual(self.sides(), [('AAA', 'BUY'), ('AAA', 'SELL')])
        self.assertIn('Stop', self.e.s['decisions']['AAA'])
    def test_target_at_plus_100_percent(self):
        entry = D(self.buy()['entry'])
        self.e.apply('2026-10-27', {'AAA': q(entry * 2)}, True)
        self.assertEqual(self.sides()[-1], ('AAA', 'SELL'))
        self.assertGreater(D(self.e.s['realized']), D(490))
    def test_alert_once_when_position_up_80_percent(self):
        entry = D(self.buy()['entry'])
        self.e.apply('2026-10-27', {'AAA': q(entry * D('1.79'))}, True)
        self.assertEqual(self.alerts, [])
        self.e.apply('2026-10-27', {'AAA': q(entry * D('1.8'))}, True)
        self.e.apply('2026-10-28', {'AAA': q(entry * D('1.85'))}, True)
        self.assertEqual(len(self.alerts), 1)
        self.assertIn('AAA', self.alerts[0])
    def test_alert_when_whole_account_up_80_percent(self):
        self.e.s['cash'] = '9000'
        self.e.apply('2026-10-26', {}, True)
        self.assertTrue(any('account' in a for a in self.alerts))
    def test_daily_loss_limit_blocks_entries_not_exits(self):
        entry = D(self.buy()['entry'])
        self.e.s['cash'] = str(D(self.e.s['cash']) - 400)  # lose $400 today
        self.e.apply('2026-10-26', {'BBB': q(110)}, True)
        self.assertNotIn('BBB', self.e.s['positions'])
        self.assertIn('$400', self.e.s['decisions']['BBB'])
        self.e.apply('2026-10-26', {'AAA': q(entry * D('.8'))}, True)
        self.assertNotIn('AAA', self.e.s['positions'])
        self.e.apply('2026-10-27', {'BBB': q(110, hist(last=110, n=41))}, True)  # new day resets the limit
        self.assertIn('BBB', self.e.s['positions'])
    def test_market_closed_no_trades_or_exits(self):
        self.e.apply('2026-10-25', {'AAA': q(110)}, False)
        self.assertEqual(self.sides(), [])
        entry = D(self.buy()['entry'])
        self.e.apply('2026-10-27', {'AAA': q(entry * D('.5'))}, False)
        self.assertIn('AAA', self.e.s['positions'])
    def test_signal_not_reused_after_exit(self):
        entry = D(self.buy()['entry'])
        self.e.apply('2026-10-26', {'AAA': q(entry * D('.8'))}, True)
        self.e.apply('2026-10-26', {'AAA': q(110)}, True)
        self.assertEqual(len(self.sides()), 2)
    def test_less_cash_than_the_cap_buys_a_smaller_position_and_only_an_empty_account_skips(self):
        self.e.s['cash'] = '300'
        self.e.apply('2026-10-26', {'AAA': q(110)}, True)
        self.assertEqual(self.sides(), [('AAA', 'BUY')])
        self.assertLess(D(self.e.s['cash']), D('0.2'))
        self.assertGreater(D(self.e.s['positions']['AAA']['cost']), D('299.8'))
        self.e.apply('2026-10-26', {'BBB': q(110)}, True)
        self.assertEqual(len(self.sides()), 1)
        self.assertIn('no cash left', self.e.s['decisions']['BBB'])
    def test_losing_replay_blocks_entries(self):
        e = StockEngine(':memory:', None, ['AAA'], edge_check=lambda: dict(entries=40, total=D(-120)))
        e.refresh_edge('2026-10-26', wait=True)
        e.apply('2026-10-26', {'AAA': q(110)}, True)
        self.assertEqual(e.s['positions'], {})
        self.assertIn('replay', e.s['decisions']['AAA'])
    def test_gate_off_trades_despite_losing_replay(self):
        e = StockEngine(':memory:', None, ['AAA'], edge_check=lambda: dict(entries=40, total=D(-120)), gate=False)
        e.refresh_edge('2026-10-26', wait=True)
        e.apply('2026-10-26', {'AAA': q(110)}, True)
        self.assertIn('AAA', e.s['positions'])
        self.assertFalse(e.snapshot()['gate'])
    def test_no_limit_spends_all_cash_once(self):
        self.assertIsNone(self.e.set_rules(dict(per_trade='0')))
        self.e.apply('2026-10-26', {'AAA': q(110), 'BBB': q(110)}, True)
        self.assertEqual(len(self.e.s['positions']), 1)
        self.assertGreater(D(next(iter(self.e.s['positions'].values()))['cost']), D(4999))
        self.assertIn('cash', list(self.e.s['decisions'].values())[-1])
    def test_rules_edit_validates_and_persists(self):
        self.assertIn('Stop', self.e.set_rules(dict(stop='1.5')))
        self.assertIn('symbol', self.e.set_rules(dict(symbols=['A;B'])))
        self.assertEqual(self.e.s['stop'], '0.20')  # library default; the dashboard builds start from DEFAULT_RULES
        self.assertIsNone(self.e.set_rules(dict(stop='.1', target='.3', daily_loss='0', symbols=['ccc', 'AAA', 'CCC'], gate=False)))
        self.assertEqual((self.e.stop, self.e.target, self.e.symbols, self.e.gate), (D('.1'), D('.3'), ['CCC', 'AAA'], False))
        entry = D(self.buy()['entry'])
        self.e.s['cash'] = str(D(self.e.s['cash']) - 1000)  # daily loss limit 0 = off
        self.e.apply('2026-10-26', {'AAA': q(entry * D('.9'))}, True)
        self.assertNotIn('AAA', self.e.s['positions'])  # new 10% stop
    def test_removed_symbol_still_gets_exit_checks(self):
        self.buy()
        seen = []
        self.e.quotes = lambda syms, now: (seen.append(syms), ({}, {}))[1]
        self.e.set_rules(dict(symbols=['BBB']))
        self.e.tick()
        self.assertIn('AAA', seen[0])
    def test_failed_quote_feed_changes_nothing(self):
        e = StockEngine(':memory:', lambda syms, now: 1 / 0, ['AAA'])
        e.tick()
        self.assertIn('ZeroDivisionError', e.error)
        self.assertEqual(e.s['cash'], '5000')
    def test_stale_quote_during_session_is_not_traded(self):
        open_now = lambda now: dict(open=True, date='2026-10-26', calendar_known=True)  # not the real clock: the test must not depend on market hours
        e = StockEngine(':memory:', lambda syms, now: ({'AAA': q(110)}, {}), ['AAA'], status=open_now)
        e.tick()
        self.assertEqual(e.s['positions'], {})
        self.assertIn('stale', e.s['decisions']['AAA'])

    def test_entry_needs_the_live_price_to_still_agree_with_the_signal(self):
        self.e.apply('2026-10-26', {'AAA': q(95)}, True)    # breakout level is 100; the stock reversed under it
        self.assertEqual(self.e.s['positions'], {})
        self.assertIn('back under the breakout level', self.e.s['decisions']['AAA'])
        self.e.apply('2026-10-26', {'AAA': q(130)}, True)   # 18% over the 110 close: a chase
        self.assertEqual(self.e.s['positions'], {})
        self.assertIn('not chasing', self.e.s['decisions']['AAA'])
        self.e.apply('2026-10-26', {'AAA': q(112)}, True)
        self.assertIn('AAA', self.e.s['positions'])
    def test_failed_replay_is_retried_after_an_hour_not_blocked_all_day(self):
        calls = []
        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError('429')
            return dict(entries=50, total=D(100))
        e = StockEngine(':memory:', None, ['AAA'], edge_check=flaky)
        e.refresh_edge('2026-10-26', wait=True); e.refresh_edge('2026-10-26', wait=True)
        self.assertEqual(len(calls), 1)                      # inside the hour: no hammering
        e.edge['at'] -= 3601
        e.refresh_edge('2026-10-26', wait=True)
        self.assertIsNone(e.edge_block())
    def test_replay_started_before_a_rule_change_is_dropped(self):
        e = StockEngine(':memory:', None, ['AAA'], edge_check=lambda: dict(entries=50, total=D(100)))
        e.edge_gen += 1
        gen = e.edge_gen
        e.set_rules({'stop': '0.2'})
        self.assertEqual(e.edge_gen, gen + 1)
        self.assertIsNone(e.edge)
    def test_watchlist_must_be_a_list_and_rule_changes_are_logged(self):
        self.assertIn('list', self.e.set_rules({'symbols': 'TSLA'}))
        self.assertEqual(self.e.symbols, ['AAA', 'BBB'])
        self.assertIsNone(self.e.set_rules({'stop': '0.15'}))
        self.assertIn('stop: 0.20 → 0.15', self.e.s['rule_log'][-1]['text'])
    def test_new_buys_per_day_spreads_entries(self):
        self.assertIsNone(self.e.set_rules({'max_new': 1}))
        self.e.apply('2026-10-26', {'AAA': q(110), 'BBB': q(110)}, True)
        self.assertEqual(list(self.e.s['positions']), ['AAA'])
        self.assertIn('bought today', self.e.s['decisions']['BBB'])
        self.e.apply('2026-10-27', {'BBB': q(110)}, True)
        self.assertEqual(set(self.e.s['positions']), {'AAA', 'BBB'})
    def test_notifications_wait_for_the_save_and_a_rolled_back_check_sends_none(self):
        self.buy('2026-10-26', 110)
        self.e.apply('2026-10-27', {'AAA': q(200)}, True)    # target hit and above the 80% alert
        self.assertEqual(len(self.alerts), 1)
        def boom(): raise RuntimeError('x')
        e = StockEngine(':memory:', lambda syms, now: ({'AAA': q(110)}, {}), ['AAA'], notify=self.alerts.append,
                        status=lambda now: dict(open=True, date='2026-10-26', calendar_known=True))
        e.s['positions']['AAA'] = dict(qty='1', entry='100', cost='100', last='100', opened='x', alerted=False)
        e.save = boom
        before = len(self.alerts)
        e.tick()
        self.assertEqual(len(self.alerts), before)
        self.assertEqual(e.pending, [])
    def test_history_is_thinned_and_the_database_is_backed_up_daily(self):
        for d in range(5):
            self.e.apply(f'2026-10-{26 + d}', {'AAA': q(110)}, True)
        self.assertEqual(len(self.e.s['history']), 1)        # five checks inside ten minutes: one point
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as tmp:
            e = StockEngine(pathlib.Path(tmp) / 'x.sqlite', None, ['AAA'])
            e.backup('2026-10-26'); e.backup('2026-10-26')
            self.assertEqual([p.name for p in (pathlib.Path(tmp) / 'backups').glob('*')], ['x-2026-10-26.sqlite'])
            f = StockEngine(pathlib.Path(tmp) / 'x-e.sqlite', None, ['AAA'])   # a challenger next to it: pruning one must never delete the other's copies
            f.backup('2026-10-26')
            for d in range(15):
                e.backup(f'2026-11-{d + 1:02}')
            names = sorted(p.name for p in (pathlib.Path(tmp) / 'backups').glob('*'))
            self.assertIn('x-e-2026-10-26.sqlite', names); self.assertEqual(len(names), 15)   # 14 of x, 1 of x-e
            e.conn.close(); f.conn.close()
    def test_agent_gap_is_reported(self):
        e = StockEngine(':memory:', lambda s, n: ({}, {}), ['AAA'])
        e.apply('2026-10-26', {'AAA': q(110)}, True)         # Monday
        e.apply('2026-10-30', {'AAA': q(110)}, True)         # Friday: Tue to Thu untracked
        self.assertIn('3 weekday', e.s['alerts'][-1]['text'])

    def test_crypto_trades_on_a_weekend(self):
        e = StockEngine(':memory:', lambda syms, now: ({'BTC': dict(q(110), at=time.time())}, {}), ['BTC'], status=crypto_status, source='coinbase')
        e.tick()  # crypto_status is always open, whatever the day
        self.assertIn('BTC', e.s['positions'])
        self.assertTrue(e.s['market']['open'])

class ReplayTests(unittest.TestCase):
    def test_replay_trades_rising_series(self):
        up = [(DAY0 + timedelta(i), 100 * 1.01 ** i) for i in range(120)]
        r = replay({'AAA': up})
        self.assertGreaterEqual(r['entries'], 1)
        self.assertGreater(r['total'], 0)

if __name__ == '__main__':
    unittest.main()

class ResearchTests(unittest.TestCase):
    def series(self):
        return [(DAY0 + timedelta(i), 100.0) for i in range(60)] + [(DAY0 + timedelta(60 + i), 110.0) for i in range(12)]
    def test_replay_applies_the_congress_filter_the_live_agent_uses(self):
        blocked = replay({'AAA': self.series()}, congress=lambda s, d, n: False, congress_days=D(30))
        allowed = replay({'AAA': self.series()}, congress=lambda s, d, n: True, congress_days=D(30))
        self.assertEqual(blocked['entries'], 0)
        self.assertGreaterEqual(allowed['entries'], 1)
        self.assertEqual(StockEngine(':memory:', None, ['AAA'], congress_days=D(30)).rules()['congress_days'], 30)
    def test_replay_can_return_every_trade_with_its_day(self):
        r = replay({'AAA': self.series()}, record=True)
        self.assertEqual([j['side'] for j in r['journal']][:1], ['BUY'])
        self.assertTrue(all(j['day'] for j in r['journal']))
        self.assertNotIn('journal', replay({'AAA': self.series()}))
    def test_replay_reports_what_a_profit_does_not_show(self):
        r = replay({'AAA': self.series()})
        self.assertEqual(set(['max_drawdown', 'exposure', 'half1', 'half2', 'buy_hold']) - set(r), set())
        self.assertAlmostEqual(float(r['buy_hold']), 5000 * .10, places=2)      # 100 -> 110 held the whole time
        self.assertGreaterEqual(r['max_drawdown'], 0)
    def test_every_replay_is_written_to_the_experiment_log(self):
        import json, pathlib, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            e = StockEngine(pathlib.Path(tmp) / 'x.sqlite', None, ['AAA'], edge_check=lambda: dict(entries=9, total=D(5)))
            e.refresh_edge('2026-10-26', wait=True)
            row = json.loads((pathlib.Path(tmp) / 'experiments.jsonl').read_text())
            self.assertEqual((row['symbols'], row['rules']['stop'], row['result']['entries']), (['AAA'], '0.20', 9))
            e.conn.close()
    def test_a_replay_can_be_reproduced_from_its_saved_prices_and_code_hashes(self):
        import gzip, json, pathlib, tempfile
        from signaldesk.swing import data_hash, save_history, CODE
        a, b = {'AAA': self.series()}, {'AAA': self.series()[:-1] + [(DAY0 + timedelta(71), 111.0)]}
        self.assertEqual(data_hash(a), data_hash({'AAA': self.series()}))
        self.assertNotEqual(data_hash(a), data_hash(b))
        self.assertEqual(replay(a)['data_sha256'], data_hash(a))
        with tempfile.TemporaryDirectory() as tmp:
            p = save_history(a, tmp)
            self.assertEqual(save_history(a, tmp), p)
            self.assertEqual(json.load(gzip.open(p, 'rt'))['AAA'][0], [str(DAY0), 100.0])
        self.assertEqual(set(CODE), {'swing_py', 'scan_py', 'alerts_py'})
    def test_cli_replay_reads_the_rules_saved_from_the_dashboard(self):
        import pathlib, tempfile
        from signaldesk.swing import saved_rules
        with tempfile.TemporaryDirectory() as tmp:
            db = pathlib.Path(tmp) / 's.sqlite'
            e = StockEngine(db, None, ['AAA']); e.set_rules({'stop': '0.12', 'congress_days': '30'}); e.conn.close()
            r = saved_rules(db)
            self.assertEqual((r['stop'], r['congress_days']), (D('0.12'), D(30)))
        self.assertEqual(saved_rules('/nonexistent/x.sqlite'), {})
    def test_startup_rules_layer_defaults_then_saved_then_flags(self):
        import pathlib, tempfile
        from signaldesk.app import startup_rules
        with tempfile.TemporaryDirectory() as tmp:
            db = pathlib.Path(tmp) / 's.sqlite'
            self.assertEqual(startup_rules(db, D(5000))['target'], D('0.25'))            # nothing saved: the dashboard defaults
            e = StockEngine(db, None, ['AAA']); e.set_rules({'target': '0.3', 'per_trade': '250'}); e.conn.close()
            r = startup_rules(db, D(5000))
            self.assertEqual((r['target'], r['per_trade']), (D('0.3'), D(250)))           # saved wins over defaults
            self.assertEqual(startup_rules(db, D(5000), per_trade=D(100))['per_trade'], D(100))  # flag wins over saved
    def test_skills_are_bundled_with_the_project(self):
        from signaldesk.core import ROOT
        for f in ('trader-scanner/scan.py', 'trader-alerts/alerts.py', 'trader-news/news.py', 'trader-pelosi/ptr.py'):
            self.assertTrue((ROOT / 'vendor' / f).is_file(), f)

class ChallengerTests(unittest.TestCase):
    def setUp(self):
        import tempfile, pathlib
        self.tmp = tempfile.TemporaryDirectory(); self.dir = pathlib.Path(self.tmp.name); self.addCleanup(self.tmp.cleanup)
        self.ny = '2026-11-04 15:55'
        self.calls = []
        def quotes(syms, now):
            self.calls.append(list(syms))
            return {s: dict(q(self.price.get(s, 110)), at=time.time()) for s in syms}, self.problems
        self.price, self.problems = {}, {}
        self.status = lambda now: dict(open=True, date='2026-11-04', calendar_known=True, closes='16:00', now_new_york=self.ny)
        self.leader = StockEngine(':memory:', quotes, ['AAA'], status=self.status, gate=False)
        self.leader.s['positions']['ZZZ'] = dict(qty='1', entry='100', cost='100', last='100', opened='2026-10-30', alerted=False)
        self.leader.s['cash'] = '4900'
        self.f = None
    def challenger(self):
        from signaldesk.swing import build_challenger
        self.f = build_challenger(self.leader, self.dir / 'e.sqlite', self.dir)
        return self.f
    def lines(self, name):
        import json
        p = self.dir / name
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def test_minutes_to_close_handles_early_closes_crypto_and_bad_clocks(self):
        from signaldesk.swing import minutes_to_close
        self.assertEqual(minutes_to_close(dict(closes='16:00', now_new_york='2026-11-04 15:50')), 10)
        self.assertEqual(minutes_to_close(dict(closes='13:00', now_new_york='2026-11-27 12:55')), 5)   # an early close moves the window with it
        self.assertEqual(minutes_to_close(dict(name='Crypto')), 0)
        self.assertEqual(minutes_to_close(dict(closes='16:00', now_new_york='nonsense')), 999)
    def test_channel_exit_waits_for_the_closing_window_and_excludes_today(self):
        e = StockEngine(':memory:', None, ['ZZZ'], target=D(100), exit_low=D(20))
        e.s['positions']['ZZZ'] = dict(qty='1', entry='100', cost='100', last='100', opened='x', alerted=False)
        e.near_close = False
        e.apply('2026-11-04', {'ZZZ': q(95)}, True)
        self.assertIn('ZZZ', e.s['positions'])                       # under the low at 11:00 is not a sale: the researched rule decides at the close
        e.near_close = True
        e.apply('2026-11-04', {'ZZZ': q(95)}, True)
        self.assertNotIn('ZZZ', e.s['positions'])
    def test_held_symbol_missing_from_the_quotes_is_tracked_until_it_returns(self):
        self.challenger()
        self.problems = {'ZZZ': 'No quote: timeout'}
        real = self.leader.quotes
        self.leader.quotes = lambda syms, now: ({k: v for k, v in real(syms, now)[0].items() if k != 'ZZZ'}, self.problems)
        self.leader.tick(); since = self.leader.unpriced['ZZZ']
        self.leader.tick(); self.assertEqual(self.leader.unpriced, {'ZZZ': since})   # the clock starts at the first miss
        self.leader.quotes, self.problems = real, {}
        self.leader.tick(); self.assertEqual(self.leader.unpriced, {})
    def test_challenger_takes_entry_permission_from_the_baseline_gate(self):
        f = self.challenger()
        self.leader.edge_block = lambda: 'Signal, but entries blocked: strategy replay unavailable (replay running).'
        self.assertEqual(f.edge_block(), self.leader.edge_block())
        self.assertEqual([r['change'] for r in f.s['protocol']['revisions']], ['gate'])
    def test_a_replay_finishing_mid_check_gives_both_portfolios_the_same_gate_verdict(self):
        f = self.challenger()
        answers = iter(['Signal, but entries blocked: strategy replay unavailable (replay running).'])
        self.leader.edge_block = lambda: next(answers, None)   # pending at the first read, done at every read after it
        self.leader.tick()
        self.assertNotIn('AAA', self.leader.s['positions']); self.assertNotIn('AAA', f.s['positions'])
        self.assertIn('replay running', f.s['decisions']['AAA'])
        self.assertIsNone(f.gate_now)                                  # the frozen verdict lasts one check only
        self.leader.tick()
        self.assertIn('AAA', self.leader.s['positions']); self.assertIn('AAA', f.s['positions'])
    def test_both_portfolios_share_one_quote_fetch_including_each_others_holdings(self):
        f = self.challenger()
        f.s['positions']['YYY'] = dict(qty='1', entry='100', cost='100', last='100', opened='x', alerted=False)
        self.leader.tick()
        self.assertEqual(len(self.calls), 1)                          # one stream, not one per portfolio
        self.assertTrue({'AAA', 'ZZZ', 'YYY'} <= set(self.calls[0]))
        self.assertEqual(f.s['symbols'], ['AAA'])                     # same watchlist
        self.assertEqual(f.s['market']['date'], self.leader.s['market']['date'])
    def test_challenger_is_a_frozen_clone_with_exactly_two_differences(self):
        f = self.challenger()
        self.assertEqual(f.s['positions'], self.leader.s['positions'])
        self.assertEqual((f.s['cash'], f.s['capital']), (self.leader.s['cash'], self.leader.s['capital']))
        self.assertEqual((f.target, f.exit_low, f.stop, f.max_new), (D(100), D(20), self.leader.stop, self.leader.max_new))
        self.assertIn('frozen', f.set_rules({'stop': '0.05'}))
        self.assertEqual(f.s['stop'], self.leader.s['stop'])
        f2 = StockEngine(self.dir / 'e.sqlite', None, ['AAA'])        # a restart reads the saved, still frozen rules
        self.assertTrue(f2.s['frozen'])
    def test_exit_rule_difference_changes_only_the_exit(self):
        f = self.challenger(); self.price = {'ZZZ': 95}
        self.leader.tick()
        self.assertIn('ZZZ', self.leader.s['positions'])              # baseline: -5% is neither stop nor +25% target
        self.assertNotIn('ZZZ', f.s['positions'])                     # challenger: under the channel low in the closing window
        self.ny = '2026-11-04 11:00'; self.price = {'ZZZ': 95}
    def test_scoreboard_reports_both_portfolios_from_the_same_start(self):
        f = self.challenger(); self.price = {'ZZZ': 95}
        self.leader.tick()
        c = self.leader.snapshot()['challenger']
        self.assertEqual(c['baseline']['since'], c['challenger']['since'])
        self.assertEqual((c['challenger']['exits'], c['baseline']['exits']), (1, 0))
        self.assertEqual(set(['ret', 'max_drawdown', 'deployed', 'days_with_position', 'avg_positions', 'largest_share', 'entries', 'exits']) - set(c['challenger']), set())
        self.assertGreater(c['baseline']['days_with_position'], 0)
        self.assertEqual(c['rule_changes'], [])
        self.leader.set_rules({'stop': '0.15'})
        self.assertEqual(len(self.leader.snapshot()['challenger']['rule_changes']), 1)   # a baseline edit makes the comparison unfair, and says so
    def test_decisions_are_recorded_with_price_threshold_reason_and_data_failures(self):
        f = self.challenger(); self.price = {'ZZZ': 95}; self.problems = {'AAA': 'No quote: boom'}
        self.leader.tick()
        sell = [r for r in self.lines('decisions-stocks-e.jsonl') if r['action'] == 'SELL'][0]
        self.assertEqual((sell['symbol'], sell['price'], sell['channel_low'], sell['portfolio']), ('ZZZ', '95', '100.0', 'challenger'))
        self.assertIn('Channel exit', sell['reason'])
        self.assertTrue(sell['ts'] and sell['day'] == '2026-11-04')
        self.assertIn('NO_DATA', [r['action'] for r in self.lines('decisions-stocks.jsonl')])
        self.leader.quotes = lambda s, n: (_ for _ in ()).throw(RuntimeError('Yahoo down'))
        self.leader.tick()
        self.assertIn('TICK_ERROR', [r['action'] for r in self.lines('decisions-stocks.jsonl')])
    def test_every_open_position_gets_one_end_of_day_record(self):
        self.challenger(); self.price = {'ZZZ': 105}
        self.leader.tick(); self.leader.tick()
        holds = [r for r in self.lines('decisions-stocks.jsonl') if r['action'] == 'HOLD' and r['symbol'] == 'ZZZ']
        self.assertEqual(len(holds), 1)
        self.assertEqual(holds[0]['price'], '105')

class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import threading
        from signaldesk import server as app
        cls.e = StockEngine(':memory:', None, ['AAA'])
        threading.Thread(target=app.serve, args=({'stocks': (cls.e, 'stocks.html', 'stocks.js')}, 8798, None), daemon=True).start()
        time.sleep(.4)
    def get(self, path):
        import urllib.request, urllib.error
        try:
            return urllib.request.urlopen(f'http://127.0.0.1:8798{path}', timeout=3).status
        except urllib.error.HTTPError as err:
            return err.code
    def test_trailing_slash_and_query_string_still_find_the_page(self):
        for path in ('/stocks', '/stocks/', '/stocks/activity/', '/stocks?x=1', '/api/stocks/state?x=1'):
            self.assertEqual(self.get(path), 200, path)
        self.assertEqual(self.get('/stocks/bogus'), 404)
    def test_negative_content_length_is_rejected_not_hung(self):
        import json, socket, urllib.request
        token = json.load(urllib.request.urlopen('http://127.0.0.1:8798/api/stocks/state'))['token']
        s = socket.create_connection(('127.0.0.1', 8798), timeout=3)
        s.sendall(f'POST /api/stocks/rules HTTP/1.1\r\nHost: 127.0.0.1:8798\r\nX-App-Token: {token}\r\nContent-Length: -1\r\n\r\n'.encode())
        self.assertIn(b'400', s.recv(100))
        s.close()
