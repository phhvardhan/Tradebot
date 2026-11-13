"""The decision desk: a read-only import netted into holdings, and the Action-needed cards built from the rules you chose."""
import os
import tempfile
import time
import unittest
from unittest.mock import patch
from decimal import Decimal as D
from pathlib import Path

from signaldesk import desk
from signaldesk.swing import StockEngine
from tests.test_startup import Case

CSV = """Activity Date,Process Date,Settle Date,Instrument,Description,Trans Code,Quantity,Price,Amount
11/10/2026,11/10/2026,11/11/2026,AAPL,Apple,Sell,2,$200.00,$400.00
11/03/2026,11/03/2026,11/04/2026,AAPL,Apple,Buy,2,$120.00,($240.00)
10/30/2026,10/30/2026,10/31/2026,AAPL,Apple,Buy,4,$100.00,($400.00)
10/30/2026,10/30/2026,10/31/2026,AAPL,Apple dividend,CDIV,,,$1.00
10/29/2026,10/29/2026,10/30/2026,XOM,Exxon,Buy,1.5,$110.00,($165.00)
"""
closes = lambda last: [100.0] * 29 + [last]   # 30 daily closes: flat, then the last one

def engine(decisions=None, prices=None, market_open=True, **rules):
    e = StockEngine(':memory:', None, ['AAPL', 'XOM', 'KO'], **dict(dict(per_trade=D(500), stop=D('.1'), target=D('.25'), max_new=D(3)), **rules))
    e.s['quotes'] = {sym: [str(p), str(c[-1]), c, 1_800_000_000.0] for sym, (p, c) in (prices or {}).items()}
    e.s['decisions'] = decisions or {}
    e.s['market'] = dict(open=market_open, date='2026-11-12', reason='regular session')
    e.s['day'] = '2026-11-12'
    return e

class ImportTests(unittest.TestCase):
    def test_a_robinhood_csv_nets_fills_into_positions_with_the_brokers_average_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / 'a.csv'; f.write_text(CSV)
            fills, positions, notes = desk.read_csv(f)
        self.assertEqual([x['at'] for x in fills], ['2026-10-29', '2026-10-30', '2026-11-03', '2026-11-10'])   # oldest first, dividend skipped
        aapl = next(p for p in positions if p['symbol'] == 'AAPL')
        self.assertEqual((aapl['qty'], aapl['avg_cost']), (D('4'), D('106.6667')))   # 6 bought at 640, 2 sold: cost falls by a third, the average stays
        self.assertEqual(notes, [])

    def test_a_partial_export_that_sells_more_than_it_bought_is_flagged_not_trusted(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / 'a.csv'; f.write_text(CSV.replace('Sell,2,', 'Sell,9,'))
            _, positions, notes = desk.read_csv(f)
        self.assertEqual([p['symbol'] for p in positions], ['XOM'])
        self.assertIn('AAPL', notes[0])

    def test_tags_and_swing_plans_survive_a_re_import_and_are_validated(self):
        h = desk.snapshot('acct', '1000', [dict(symbol='AAPL', qty=4, avg_cost='106.67')], [], 'test')
        self.assertEqual(desk.tag(h, 'AAPL', 'swing', dict(entry='100', stop='110', target='120')), 'Needs stop < entry < target.')
        self.assertIsNone(desk.tag(h, 'AAPL', 'swing', dict(entry='100', stop='90', target='125', opened='2026-11-12')))
        again = desk.snapshot('acct', '900', [dict(symbol='AAPL', qty=5, avg_cost='105'), dict(symbol='KO', qty=1, avg_cost='60')], [], 'test', previous=h)
        self.assertEqual([(p['symbol'], p['purpose'], p['qty']) for p in again['positions']], [('AAPL', 'swing', '5'), ('KO', 'invest', '1')])
        self.assertEqual(again['positions'][0]['plan']['stop'], '90')
        with tempfile.TemporaryDirectory() as tmp:
            desk.save(again, Path(tmp) / 'h.json'); self.assertEqual(desk.load(Path(tmp) / 'h.json'), again)
        self.assertIsNone(desk.load(Path('/nonexistent/h.json')))

    def test_a_same_day_buy_then_sell_replays_in_that_order_in_a_newest_first_file(self):
        rows = CSV.splitlines()[0] + """
11/10/2026,11/10/2026,11/11/2026,AAPL,Apple,Sell,5,$200.00,$1000.00
11/10/2026,11/10/2026,11/11/2026,AAPL,Apple,Buy,10,$100.00,($1000.00)
"""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / 'a.csv'; f.write_text(rows)
            fills, positions, notes = desk.read_csv(f)
        self.assertEqual([x['side'] for x in fills], ['BUY', 'SELL'])
        self.assertEqual((positions[0]['qty'], positions[0]['avg_cost']), (D('5'), D('100.0000')))  # not 200: the buy came first
        self.assertEqual(notes, [])

    def test_one_symbols_note_never_hides_anothers(self):
        rows = CSV.splitlines()[0] + """
11/10/2026,11/10/2026,11/11/2026,A,Agilent,Sell,1,$100.00,$100.00
11/09/2026,11/09/2026,11/10/2026,AAPL,Apple,Sell,1,$100.00,$100.00
"""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / 'a.csv'; f.write_text(rows)
            _, _, notes = desk.read_csv(f)
        self.assertEqual([n.split(':')[0] for n in notes], ['AAPL', 'A'])

    def test_a_swing_tag_is_dropped_when_the_stock_was_sold_out_and_bought_again(self):
        h = desk.snapshot('acct', None, [dict(symbol='AAPL', qty=4, avg_cost='100', since='2026-10-01')], [], 'test')
        desk.tag(h, 'AAPL', 'swing', dict(entry='100', stop='90', target='125'))
        same = desk.snapshot('acct', None, [dict(symbol='AAPL', qty=6, avg_cost='101', since='2026-10-01')], [], 'test', previous=h)
        self.assertEqual((same['positions'][0]['purpose'], same['positions'][0]['plan']['stop']), ('swing', '90'))   # added to, still the same holding
        new = desk.snapshot('acct', None, [dict(symbol='AAPL', qty=2, avg_cost='150', since='2026-11-10')], [], 'test', previous=same)
        self.assertEqual((new['positions'][0]['purpose'], new['positions'][0]['plan']), ('invest', None))
        self.assertIn('sold out and bought again', new['notes'][0])

    def test_the_sync_time_is_when_the_csv_was_exported_not_when_it_was_imported(self):
        with tempfile.TemporaryDirectory() as tmp:
            f, out = Path(tmp) / 'a.csv', Path(tmp) / 'h.json'
            f.write_text(CSV); old = time.time() - 21 * 86400
            os.utime(f, (old, old))
            desk.main(['import', str(f), '--file', str(out)])
            self.assertAlmostEqual(desk.load(out)['synced_at'], old, delta=1)

class ActionTests(unittest.TestCase):
    def holdings(self, **kw):
        h = desk.snapshot('Robinhood ••••1234', '2000', [dict(symbol='AAPL', qty='4', avg_cost='100'), dict(symbol='XOM', qty='1.5', avg_cost='110')], [], 'test')
        desk.tag(h, 'AAPL', 'swing', dict(entry='100', stop='90', target='125', opened='2026-11-01'))
        h.update(kw); return h

    def test_a_swing_lot_under_its_stop_becomes_a_sell_card_while_an_investment_with_the_same_drop_does_not(self):
        out = desk.actions(self.holdings(), engine(prices={'AAPL': (89, closes(89)), 'XOM': (80, closes(80))}))
        self.assertEqual([(c['action'], c['symbol'], c['passes']) for c in out['cards']], [('SELL', 'AAPL', True)])
        c = out['cards'][0]
        self.assertIn('under your stop 90.00', c['trigger'])
        self.assertEqual((c['qty'], c['est_pnl'], c['quote_at']), ('4', '-44', 1_800_000_000.0))     # 4 shares, 100 -> 89, at this quote
        self.assertIn('estimate', c['est_note'])
        xom = next(w for w in out['watching'] if w['symbol'] == 'XOM')
        self.assertEqual((xom['purpose'], xom['plan']), ('invest', None))                              # down 27%, no swing rule applies

    def test_a_breakout_becomes_a_buy_card_sized_by_the_cap_with_the_loss_at_the_stop_and_every_check(self):
        out = desk.actions(self.holdings(), engine(prices={'AAPL': (100, closes(100)), 'XOM': (110, closes(100)), 'KO': (101, closes(101))}))
        buys = [c for c in out['cards'] if c['action'] == 'BUY']
        self.assertEqual([c['symbol'] for c in buys], ['KO'])                                           # only KO closed above its 20-day high
        c = buys[0]
        self.assertEqual((c['qty'], D(c['stop']), D(c['target'])), ('4.9504', D('90.9'), D('126.25')))             # $500 / 101, stop -10%, target +25%
        self.assertEqual(D(c['est_loss']), D('4.9504') * D('10.10'))
        self.assertTrue(c['passes'])
        self.assertEqual([k['text'] for k in c['checks']], ['No open swing trade on it', '0 of 3 new buys today', 'Cash $2000.00', 'Market open'])
        self.assertIn('broke the 20-day closing high 100.00', c['trigger'])

    def test_a_card_fails_and_says_why_when_a_rule_or_the_account_blocks_it(self):
        e = engine(prices={'KO': (94, closes(101))}, market_open=False)                                  # price fell back under the breakout, market closed
        out = desk.actions(self.holdings(cash='0'), e)
        c = out['cards'][0]
        self.assertFalse(c['passes'])
        fails = [k['text'] for k in c['checks'] if not k['ok']]
        self.assertTrue(any('back under the breakout level' in t for t in fails))
        self.assertIn('Cash $0.00: nothing to buy with', fails)
        self.assertIn('Market closed: the entry waits for the open', fails)
        self.assertEqual(c['qty'], '5.3191')                                                            # still sized by the cap ($500 / 94), so you see what the rule would do
        out = desk.actions(self.holdings(), engine(prices={'AAPL': (101, closes(101))}))                # already in a swing trade on it
        self.assertIn('Already in a swing trade on it', [k['text'] for k in out['cards'][0]['checks'] if not k['ok']])

    def test_an_incomplete_picture_is_said_out_loud(self):
        e = engine(prices={'AAPL': (100, closes(100))})
        out = desk.actions(dict(self.holdings(cash=None), synced_at=time.time() - 30 * 3600), e)
        kinds = [n['kind'] for n in out['notes']]
        self.assertEqual(sorted(kinds), ['cash', 'stale', 'unpriced'])                                   # old sync, no cash in the import, XOM has no quote yet
        self.assertIn('XOM', next(n['text'] for n in out['notes'] if n['kind'] == 'unpriced'))
        self.assertEqual(desk.actions(None, e)['notes'][0]['kind'], 'import')
        self.assertEqual(desk.actions(None, engine(prices={'KO': (101, closes(101))}))['cards'][0]['qty'], '4.9504')   # no account: the cap alone sizes it

    def test_in_session_a_card_on_a_quote_the_feed_stopped_updating_fails(self):
        e = engine(prices={'AAPL': (89, closes(89)), 'KO': (101, closes(101))})
        for q in e.s['quotes'].values():
            q[3] = time.time() - 3600                                                                     # an hour old while the market is open
        out = desk.actions(self.holdings(), e)
        self.assertEqual([(c['action'], c['passes']) for c in out['cards']], [('SELL', False), ('BUY', False)])
        self.assertTrue(all(any('60 min old' in k['text'] for k in c['checks'] if not k['ok']) for c in out['cards']))
        closed = engine(prices={'KO': (101, closes(101))}, market_open=False)
        closed.s['quotes']['KO'][3] = time.time() - 86400                                                # out of session yesterday's close is the price
        self.assertFalse(any('min old' in k['text'] for k in desk.actions(self.holdings(), closed)['cards'][0]['checks']))

    def test_a_zero_share_proposal_never_passes(self):
        out = desk.actions(self.holdings(cash=None), engine(prices={'KO': (101, closes(101))}, per_trade=D(0)))
        c = out['cards'][0]
        self.assertEqual((c['qty'], c['passes']), ('0', False))
        self.assertTrue(any(k['text'].startswith('Size is 0 shares') and not k['ok'] for k in c['checks']))

    def test_holdings_without_a_price_are_listed_so_the_account_value_can_say_it_is_partial(self):
        out = desk.actions(self.holdings(), engine(prices={'AAPL': (100, closes(100))}))
        self.assertEqual(out['unpriced'], ['XOM'])

def fake_alpaca(fill_pages, positions=None, cash='1234.5'):
    """A stand-in for Alpaca's GET endpoints; records what was asked."""
    calls = []
    def get(path, params=None):
        calls.append((path, dict(params or {})))
        if path == '/v2/account':
            return dict(account_number='PA3XYZ9876', cash=cash)
        if path == '/v2/positions':
            return positions if positions is not None else [dict(symbol='AAPL', qty='3', avg_entry_price='101.23456', side='long', asset_class='us_equity'),
                                                            dict(symbol='BTCUSD', qty='0.1', avg_entry_price='60000', side='long', asset_class='crypto')]
        return fill_pages[sum(c[0].endswith('FILL') for c in calls) - 1]                                   # pages in the order asked
    return get, calls

def fill(i, t, side, qty, price, sym='AAPL'):
    return dict(id=f'2026::{i}', activity_type='FILL', transaction_time=t, symbol=sym, side=side, qty=qty, price=price, type='fill')

class AlpacaTests(unittest.TestCase):
    def test_broker_positions_and_cash_are_the_truth_and_fills_say_when_each_holding_started(self):
        page = [fill(3, '2026-11-10T15:00:00.123456789Z', 'buy', '3', '101'), fill(2, '2026-10-02T15:00:00Z', 'sell', '2', '120'), fill(1, '2026-10-01T15:00:00Z', 'buy', '2', '100')]
        get, calls = fake_alpaca([page])
        label, cash, positions, fills, notes = desk.read_alpaca(get)
        self.assertTrue(label.endswith('9876')); self.assertEqual(cash, D('1234.5'))
        self.assertEqual([(p['symbol'], p['qty'], p['avg_cost'], p['since']) for p in positions], [('AAPL', D('3'), D('101.2346'), '2026-11-10')])  # flat on 10-02, bought again 11-10
        self.assertEqual([f['side'] for f in fills], ['BUY', 'SELL', 'BUY'])                                  # oldest first
        self.assertIn('BTCUSD', notes[0])
        self.assertEqual({c[0] for c in calls}, {'/v2/account', '/v2/positions', '/v2/account/activities/FILL'})

    def test_fill_history_is_read_page_by_page(self):
        first = [fill(200 - i, f'2026-11-{10 - i % 9:02d}T15:00:00Z', 'buy', '1', '100') for i in range(100)]
        get, calls = fake_alpaca([first, [fill(0, '2026-01-02T15:00:00Z', 'buy', '1', '90')]])
        _, _, _, fills, _ = desk.read_alpaca(get)
        self.assertEqual(len(fills), 101)
        self.assertEqual(calls[-1][1]['page_token'], first[-1]['id'])

    def test_a_sync_keeps_tags_on_holdings_that_stay_open(self):
        page = [fill(1, '2026-10-01T15:00:00Z', 'buy', '3', '100')]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'h.json'
            desk.sync_alpaca(path, fake_alpaca([page])[0])
            h = desk.load(path); desk.tag(h, 'AAPL', 'swing', dict(entry='100', stop='90', target='125')); desk.save(h, path)
            h = desk.sync_alpaca(path, fake_alpaca([page])[0])
        self.assertEqual((h['positions'][0]['purpose'], h['positions'][0]['plan']['stop'], h['source']), ('swing', '90', 'alpaca (read-only API)'))

    def test_the_desk_syncs_only_with_keys_and_shows_a_failure_instead_of_hiding_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            d = desk.Desk(engine(), Path(tmp) / 'h.json', get=lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(OSError('network down')))
            with patch.object(desk, 'secret', lambda name: None):
                d.sync()
            self.assertEqual(calls, [])                                                                       # no keys: no request at all
            with patch.object(desk, 'secret', lambda name: 'k'):
                d.sync()
            self.assertIn('network down', d.snapshot()['notes'][0]['text'])

    def test_the_api_client_only_ever_sends_get_requests(self):
        sent = []
        class Res:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self, *a): return b'{}'
        with patch.object(desk, 'secret', lambda name: {'ALPACA_KEY_ID': 'k', 'ALPACA_SECRET_KEY': 's'}.get(name)), \
             patch.object(desk, 'urlopen', lambda req, timeout: sent.append(req) or Res()):
            desk.alpaca_get('/v2/account')
        self.assertEqual((sent[0].get_method(), sent[0].full_url), ('GET', 'https://paper-api.alpaca.markets/v2/account'))
        self.assertIsNone(sent[0].data)

class HostTests(unittest.TestCase):
    def test_only_loopback_and_listed_private_names_are_answered(self):
        from signaldesk.core import host_ok
        with patch.dict(os.environ, {'SIGNALDESK_HOSTS': ''}):
            self.assertTrue(host_ok('127.0.0.1:8765', 8765)); self.assertFalse(host_ok('desk.tail1.ts.net', 8765))
        with patch.dict(os.environ, {'SIGNALDESK_HOSTS': 'desk.tail1.ts.net'}):
            self.assertTrue(host_ok('desk.tail1.ts.net', 8765)); self.assertTrue(host_ok('desk.tail1.ts.net:8765', 8765))
            self.assertFalse(host_ok('evil.example:8765', 8765)); self.assertFalse(host_ok(None, 8765))

class ServedTests(Case):
    def test_the_desk_is_served_and_asks_the_agent_to_quote_what_the_account_holds(self):
        stocks, _ = self.build('--paused')
        d = self.markets['desk'][0]
        desk.save(desk.snapshot('acct', '100', [dict(symbol='ZZZ', qty='1', avg_cost='5')], [], 'test'), d.path)
        self.assertIn('ZZZ', [n['text'][13:16] for n in d.snapshot()['notes'] if n['kind'] == 'unpriced'])
        stocks.tick()
        self.assertIn('ZZZ', stocks.s['quotes']); self.assertEqual(len(stocks.s['quotes']['ZZZ']), 4)    # quoted with its time, never traded
        self.assertNotIn('ZZZ', stocks.s['positions'])
        self.assertEqual([w['symbol'] for w in d.snapshot()['watching']], ['ZZZ'])
        self.assertEqual(list(self.markets)[0], 'desk')                                                   # the home page

    def test_held_symbols_are_quoted_from_startup_without_opening_the_page_and_the_desk_has_no_export(self):
        stocks, _ = self.build('--paused')
        d = self.markets['desk'][0]
        desk.save(desk.snapshot('acct', '100', [dict(symbol='ZZZ', qty='1', avg_cost='5')], [], 'test'), d.path)   # a CLI import while the server runs
        stocks.tick()
        self.assertIn('ZZZ', stocks.s['quotes'])
        self.assertFalse(hasattr(d, 'conn'))

if __name__ == '__main__':
    unittest.main()
