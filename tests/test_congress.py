import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from signaldesk import congress

TODAY = date.today().isoformat()
OLD = (date.today() - timedelta(days=200)).isoformat()

class PtrError(Exception):
    pass

def stub(report):
    filings = [dict(doc_id='1', year=date.today().year, filed=TODAY, name='Jane Doe'), dict(doc_id='2', year=date.today().year, filed=OLD, name='Old Timer')]
    return SimpleNamespace(load_index=lambda y: filings, select=lambda rows, kind, since=None: [f for f in rows if f['filed'] >= (since or '')], PtrError=PtrError,
                           _fetch_and_read=lambda doc, year, pdfs, filed: ('p', report))

class CongressTest(unittest.TestCase):
    def setUp(self):
        congress._rows.clear(); congress._done.clear(); congress._buys.clear(); congress._state.update(busy=False, error=None, checked=0.0)
        self.tmp = tempfile.TemporaryDirectory()
        patches = [mock.patch.object(congress, 'STORE', Path(self.tmp.name) / 'congress.json'), mock.patch.object(congress, '_loaded', True), mock.patch.object(congress.time, 'sleep')]
        for p in patches:
            p.start(); self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_keeps_watchlist_tickers_and_skips_old_filings_and_untickered_rows(self):
        report = {'rows': [dict(ticker='NVDA', type_name='purchase', amount='$1,001 - $15,000', owner='SP', owner_name='spouse', date='2026-01-02', amount_low=1001),
                           dict(ticker='ZZZZ', type_name='sale', amount='$1,001 - $15,000', date='2026-01-02'), dict(ticker=None, type_name='sale', amount='x', date='2026-01-02')]}
        with mock.patch.object(congress, '_ptr', return_value=stub(report)):
            congress._refresh()
        with mock.patch.object(congress, 'SKILL', Path(__file__)):
            out = congress.snapshot({'NVDA'})
        self.assertEqual([(t['symbol'], t['who'], t['side']) for t in out['trades']], [('NVDA', 'Jane Doe (spouse)', 'Purchase')])
        self.assertEqual(congress._done, {'1'})  # the 200-day-old filing was never fetched
        self.assertEqual((out['days'], out['filings'], 'checked' in out), (60, 1, True))  # what the page needs to explain itself
        self.assertEqual(out['trades'][0]['traded'], '2026-01-02')  # trade date, so the page can show how late it was reported

    def test_missing_pdftotext_gives_install_hint(self):
        with mock.patch.object(congress, '_ptr', return_value=stub(None)):
            congress._refresh()
        self.assertIn('brew install poppler', congress._state['error'])

    def test_skill_missing_means_not_configured(self):
        with mock.patch.object(congress, 'SKILL', Path('/nonexistent/ptr.py')):
            self.assertEqual(congress.snapshot({'NVDA'}), {'configured': False})

    def test_bought_uses_filing_date_window_without_lookahead(self):
        congress._merge(dict(rows=[dict(doc='1', date='2026-03-10', symbol='NVDA', side='Purchase'), dict(doc='2', date='2026-03-10', symbol='AAPL', side='Sale')], done=['1', '2']))
        self.assertFalse(congress.bought('NVDA', '2026-03-09', 45))  # not filed yet on that day
        self.assertTrue(congress.bought('NVDA', '2026-03-10', 45))
        self.assertTrue(congress.bought('NVDA', '2026-04-24', 45))   # 45 days later is still inside the window
        self.assertFalse(congress.bought('NVDA', '2026-04-25', 45))  # 46 days later is not
        self.assertFalse(congress.bought('AAPL', '2026-03-10', 45))  # a sale is not a purchase

    def test_store_round_trips_and_merges_another_writers_rows(self):
        congress._merge(dict(rows=[dict(doc='1', date='2026-03-10', symbol='NVDA', side='Purchase')], done=['1']))
        congress._save()
        congress._rows.clear(); congress._done.clear()
        congress._rows.append(dict(doc='2', date='2026-03-11', symbol='MSFT', side='Purchase')); congress._done.add('2')
        congress._save()  # must keep doc 1 from the file while adding doc 2
        self.assertEqual({r['doc'] for r in congress._rows}, {'1', '2'})
        self.assertEqual(congress._done, {'1', '2'})

if __name__ == '__main__':
    unittest.main()
