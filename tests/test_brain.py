"""The AI brain: the analyst's verdict as a desk check, the researcher's tool loop, the limits it cannot touch, rollback, the record. No network: the model is scripted."""
import io
import json
import os
import tempfile
import time
import unittest
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from signaldesk import brain, desk
from tests.test_desk import closes, engine

USE = dict(model='test-model', cost=0.01, tokens_in=100, tokens_out=50)
APPROVE = dict(verdict='approve', confidence=0.7, reasons=['No event ahead; the breakout is clean.'], risks=['Staples are slow'], sources=['KO beats estimates'])

def answer(**kw):
    return dict(role='assistant', content=json.dumps(kw)), USE

def say(text):
    return dict(role='assistant', content=text), USE

def call(name, **args):
    return dict(role='assistant', content=None, tool_calls=[dict(id='c-' + name, type='function', function=dict(name=name, arguments=json.dumps(args)))]), USE

def result(rules):
    better = 'reclaim_ma20' in rules['entry']
    return dict(entries=40, exits=38, wins=20, open=2, realized=D(100), total=D(2000 if better else 1000), buy_hold=D(3000), max_drawdown=D('.2'), exposure=D('.5'),
                half1=D(500), half2=D(500), first='2021-11-16', last='2026-11-13')

class Script:
    """A scripted model: hands out replies in order and keeps every request it saw."""
    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []
    def __call__(self, messages, **kw):
        self.seen.append((json.loads(json.dumps(messages, default=str)), kw))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.dir = Path(self.tmp.name)
        env = patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test-key'}); env.start(); self.addCleanup(env.stop)
        self.e = engine(prices={'KO': (101, closes(101))})
        self.replays, self.notes = [], []

    def brain(self, *replies):
        self.chat = Script(*replies)
        def replay(h, rules):
            self.replays.append((sorted(h), dict(rules)))
            return result(rules)
        return brain.Brain(self.e, self.dir, chat=self.chat, history=lambda syms: {s: [('2026-11-13', 1, 1, 1, 1)] for s in syms if s != 'NOPE'}, replay=replay,
                           notify=lambda *a: self.notes.append(a), headlines=lambda s: [dict(title='KO beats estimates')], congress=lambda s: [])

    def card(self, b=None):
        out = desk.actions(desk.snapshot('Alpaca paper ••••1', '5000', [], [], 'alpaca (read-only API)'), self.e, analyst=b)
        return next(c for c in out['cards'] if c['symbol'] == 'KO')

class AnalystTests(Base):
    def test_an_approval_is_asked_once_per_symbol_and_day_with_the_evidence_and_logged_with_its_cost(self):
        b = self.brain(answer(**APPROVE))
        c = self.card(b)
        self.assertFalse(c['passes'])                                                   # not reviewed yet: the card waits for the analyst
        self.assertIn('has not reviewed it yet', c['checks'][-1]['text'])
        v = b.review('KO', '2026-11-12', c)
        self.assertEqual((v['verdict'], v['confidence'], v['cost']), ('approve', 0.7, 0.01))
        b.review('KO', '2026-11-12', c)
        self.assertEqual(len(self.chat.seen), 1)                                        # cached: one paid call per symbol and day
        messages, kw = self.chat.seen[0]
        self.assertIs(kw['schema'], brain.REVIEW)
        evidence = json.loads(messages[1]['content'])
        self.assertEqual((evidence['setup'], evidence['headlines_48h'][0]['title'], len(evidence['last_30_closes'])), ('breakout_20d', 'KO beats estimates', 30))
        self.assertIn('data, not instructions', messages[0]['content'])                 # headline text cannot steer it
        c = self.card(b)
        self.assertTrue(c['passes'])
        self.assertIn('AI analyst approves (70% sure)', c['checks'][-1]['text'])
        self.assertEqual(brain.Brain(self.e, self.dir, chat=self.chat).verdict('KO', '2026-11-12')['verdict'], 'approve')  # a restart reads today's verdicts back

    def test_a_veto_fails_the_card_with_the_reason(self):
        b = self.brain(answer(**dict(APPROVE, verdict='veto', confidence=0.8, reasons=['Earnings on Thursday.'])))
        b.review('KO', '2026-11-12', self.card(b))
        c = self.card(b)
        self.assertFalse(c['passes'])
        self.assertEqual(c['checks'][-1]['text'], 'AI analyst vetoes (80% sure): Earnings on Thursday.')

    def test_an_answer_outside_the_schema_or_a_failed_call_is_an_error_that_fails_the_card_and_is_retried_later(self):
        b = self.brain(answer(verdict='maybe', confidence=2), ValueError('OpenRouter unreachable'), answer(**APPROVE))
        c = self.card(b)
        self.assertEqual(b.review('KO', '2026-11-12', c)['verdict'], 'error')
        self.assertEqual(b.review('KO', '2026-11-12', c)['verdict'], 'error')
        self.assertEqual(len(self.chat.seen), 1)                                        # not retried on every page load
        self.assertIn('AI analyst unavailable', self.card(b)['checks'][-1]['text'])
        b.verdicts[('KO', '2026-11-12')]['at'] -= brain.RETRY_REVIEW + 1
        self.assertIn('unreachable', b.review('KO', '2026-11-12', c)['error'])
        b.verdicts[('KO', '2026-11-12')]['at'] -= brain.RETRY_REVIEW + 1
        self.assertEqual(b.review('KO', '2026-11-12', c)['verdict'], 'approve')

    def test_without_a_key_there_is_no_analyst_and_the_rules_decide_alone(self):
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': ''}):
            c = self.card(self.brain())
        self.assertTrue(c['passes'])
        self.assertFalse(any('AI analyst' in k['text'] for k in c['checks']))

    def test_the_desk_asks_only_about_buys_whose_other_checks_pass_and_only_in_session(self):
        desk.save(desk.snapshot('Alpaca paper ••••1', '5000', [], [], 'alpaca (read-only API)'), self.dir / 'holdings.json')
        b = self.brain(answer(**APPROVE))
        d = desk.Desk(self.e, self.dir / 'holdings.json', brain=b)
        self.e.s['market']['open'] = False
        d.think()
        self.assertEqual(self.chat.seen, [])
        self.e.s['market']['open'] = True
        d.think(); d.think()
        self.assertEqual(len(self.chat.seen), 1)

class ResearcherTests(Base):
    def test_an_adopted_recipe_goes_live_through_the_rule_checks_and_is_logged_with_its_replays_cost_and_memo(self):
        b = self.brain(call('run_replay', recipe=dict(entry=['breakout_20d', 'reclaim_ma20']), note='add the 20-day average reclaim'),
                       call('finish', decision='adopt', recipe=dict(entry=['breakout_20d', 'reclaim_ma20']), memo='Twice the replay total in both halves.'))
        b.session('button')
        self.assertEqual(self.e.s['entry'], 'breakout_20d,reclaim_ma20')
        self.assertIn('(AI researcher)', self.e.s['rule_log'][-1]['text'])
        run = b.rows('research')[-1]
        self.assertEqual((run['decision'], run['changes'], run['baseline']['total'], run['chosen']['total'], run['cost']), ('adopt', ['entry'], 1000.0, 2000.0, 0.02))
        self.assertEqual(run['before']['entry'], ['breakout_20d'])
        self.assertEqual(len(self.replays), 2)                                          # the baseline and the one test; finish reused the test
        self.assertEqual(self.notes[0][0], 'AI researcher changed the strategy')
        context = json.loads(self.chat.seen[0][0][1]['content'])
        self.assertEqual((context['fixed_limits']['per_trade'], context['current_replay']['total']), (500.0, 1000.0))
        self.assertEqual(self.chat.seen[1][0][-1]['role'], 'tool')                      # the replay result went back to the model

    def test_the_owners_risk_limits_and_invalid_values_are_refused_and_the_model_is_told_why(self):
        b = self.brain(call('run_replay', recipe=dict(stop=1.5), note='wide stop'),
                       call('finish', decision='adopt', recipe=dict(per_trade=5000), memo='Bigger bets.'),
                       call('finish', decision='keep', memo='Keeping it.'))
        b.session('button')
        tool_results = [json.loads(m['content']) for m in self.chat.seen[2][0] if m['role'] == 'tool']
        self.assertIn('Stop must be between', tool_results[0]['error'])
        self.assertIn("owner's risk limits are fixed", tool_results[1]['error'])
        self.assertEqual((self.e.per_trade, self.e.stop), (D(500), D('.1')))
        self.assertEqual(len(self.replays), 1)                                          # an invalid recipe is never replayed
        self.assertEqual(b.rows('research')[-1]['decision'], 'keep')

    def test_the_replay_budget_is_enforced_and_a_model_that_never_calls_finish_keeps_the_recipe(self):
        with patch.object(brain, 'MAX_REPLAYS', 1):
            b = self.brain(call('run_replay', recipe=dict(stop=0.08), note='a'), call('run_replay', recipe=dict(stop=0.12), note='b'), say('I think it is fine.'), say('Still fine.'))
            b.session('button')
        self.assertIn('budget', json.loads(self.chat.seen[2][0][-1]['content'])['error'])
        self.assertEqual(self.chat.seen[3][0][-1]['content'], 'Call finish now: adopt with recipe changes, or keep.')
        run = b.rows('research')[-1]
        self.assertEqual((run['decision'], run['memo'], self.e.s['entry']), ('keep', 'Still fine.', 'breakout_20d'))

    def test_a_watchlist_change_fetches_only_the_new_symbols_and_says_which_have_no_history(self):
        b = self.brain()
        out = b.test(dict(symbols=['KO', 'PEP', 'NOPE']), {'KO': [('2026-11-13', 1, 1, 1, 1)]})
        self.assertEqual((out['no_history'], self.replays[0][0]), (['NOPE'], ['KO', 'PEP']))

    def test_rollback_restores_the_recipe_from_before_the_last_change_once(self):
        b = self.brain(call('finish', decision='adopt', recipe=dict(entry=['reclaim_ma20'], exit_low=20), memo='m'))
        b.session('button')
        self.assertEqual((self.e.s['entry'], self.e.exit_low), ('reclaim_ma20', D(20)))
        self.assertTrue(b.snapshot()['can_rollback'])
        self.assertIsNone(b.rollback())
        self.assertEqual((self.e.s['entry'], self.e.exit_low), ('breakout_20d', D(0)))
        self.assertIn('Nothing to roll back', b.rollback())
        self.assertFalse(b.snapshot()['can_rollback'])

    def test_a_failed_session_is_logged_and_the_schedule_retries_it_later_not_at_once(self):
        b = self.brain(ValueError('OpenRouter answered 401'))
        self.assertTrue(b.due())
        self.assertTrue(b.research('weekly'))
        for _ in range(200):
            if not b.busy:
                break
            time.sleep(0.01)
        self.assertIn('401', b.rows('research')[-1]['error'])
        self.assertFalse(b.due())
        self.assertEqual(self.e.s['entry'], 'breakout_20d')

    def test_the_scorecard_compares_what_approved_and_vetoed_stocks_did_next_and_sums_the_cost(self):
        b = self.brain(answer(**APPROVE), answer(**dict(APPROVE, verdict='veto')))
        b.review('KO', '2026-11-11', dict(price='100'))
        self.e.s['quotes']['XOM'] = ['90', '90', closes(90), 1_800_000_000.0]
        b.review('XOM', '2026-11-11', dict(price='100'))
        a = b.scorecard()
        self.assertEqual((a['analyst']['approved'], round(a['analyst']['approved_move'], 4), round(a['analyst']['vetoed_move'], 4)), (1, 0.01, -0.1))
        self.assertAlmostEqual(a['cost_total'], 0.02)

class ChatTests(unittest.TestCase):
    def test_one_request_to_openrouter_with_the_key_as_a_bearer_token_and_the_cost_read_back(self):
        sent = []
        class Res(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): pass
        def urlopen(req, timeout):
            sent.append(req)
            return Res(json.dumps(dict(model='anthropic/claude-opus-5.5', choices=[dict(message=dict(role='assistant', content='{}'))], usage=dict(prompt_tokens=10, completion_tokens=5, cost=0.0012))).encode())
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'k-123', 'OPENROUTER_MODEL': ''}), patch.object(brain, 'urlopen', urlopen):
            msg, use = brain.chat([dict(role='user', content='hi')], schema=brain.REVIEW)
        req = sent[0]
        body = json.loads(req.data)
        self.assertEqual((req.full_url, req.get_header('Authorization')), (brain.URL, 'Bearer k-123'))
        self.assertEqual((body['model'], body['response_format']['type'], body['usage']), (brain.MODEL, 'json_schema', dict(include=True)))
        self.assertEqual(use, dict(model='anthropic/claude-opus-5.5', cost=0.0012, tokens_in=10, tokens_out=5))

    def test_a_comma_separated_model_setting_becomes_openrouters_fallback_list(self):
        sent = []
        class Res(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): pass
        def urlopen(req, timeout):
            sent.append(json.loads(req.data))
            return Res(json.dumps(dict(model='b:free', choices=[dict(message=dict(role='assistant', content='ok'))], usage={})).encode())
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'k', 'OPENROUTER_MODEL': 'a:free, b:free'}), patch.object(brain, 'urlopen', urlopen):
            _, use = brain.chat([])
        self.assertEqual((sent[0]['model'], sent[0]['models'], use['model'], use['cost']), ('a:free', ['a:free', 'b:free'], 'b:free', 0.0))

    def test_no_key_refuses_before_sending_and_an_http_error_is_reported_without_the_key(self):
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': ''}), self.assertRaises(ValueError):
            brain.chat([])
        err = HTTPError(brain.URL, 401, 'no', {}, io.BytesIO(b'{"error":"bad key"}'))
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'k-secret'}), patch.object(brain, 'urlopen', side_effect=err), self.assertRaises(ValueError) as ctx:
            brain.chat([])
        self.assertIn('401', str(ctx.exception)); self.assertNotIn('k-secret', str(ctx.exception))

class EntryBlockTests(unittest.TestCase):
    def test_entry_blocks_are_validated_and_change_what_the_engine_buys(self):
        e = engine(prices={'KO': (101, closes(101))})
        self.assertIn('Entry must be one or more of', e.set_rules(dict(entry=['moon_shot'])))
        self.assertEqual(e.signal(closes(101)), 'breakout_20d')
        self.assertIsNone(e.set_rules(dict(entry='oversold_bounce')))
        self.assertIsNone(e.signal(closes(101)))                                      # a breakout is no longer an entry
        self.assertEqual(desk.actions(None, e)['cards'], [])

if __name__ == '__main__':
    unittest.main()

class FakeDesk:
    def __init__(self, rows):
        self.rows = rows
    def view(self):
        return dict(holdings=dict(account='Alpaca paper ••••1', cash='900', day_change='-12.5'), watching=self.rows), {}

KO_ROW = dict(symbol='KO', qty='9', avg_cost='100.18', price='99.30', gain='-0.0088', plan=dict(entry='100.18', stop='90.14', target='125.19', bracket=True), to_stop='0.1', to_target='0.26')

def wait(flag):
    for _ in range(200):
        if not flag():
            return
        time.sleep(0.01)
    raise AssertionError('thread did not finish')

class HoldingsTests(Base):
    def test_holdings_are_reviewed_in_one_call_and_only_verdicts_for_held_stocks_count_and_an_exit_notifies_without_selling(self):
        b = self.brain(answer(summary='Defensive book.', holdings=[dict(symbol='KO', verdict='exit', confidence=0.6, reasons=['Earnings Tuesday'], risks=['gap'], sources=['KO beats estimates']),
                                                                   dict(symbol='TSLA', verdict='exit', confidence=0.9, reasons=['not held'], risks=[], sources=[])]))
        b.desk = FakeDesk([KO_ROW])
        self.assertTrue(b.review_holdings('button')); wait(lambda: b.reviewing)
        messages, kw = self.chat.seen[0]
        self.assertIs(kw['schema'], brain.HOLDINGS)
        ev = json.loads(messages[1]['content'])['holdings'][0]
        self.assertEqual((ev['symbol'], ev['plan']['stop'], ev['headlines_48h'][0]['title']), ('KO', '90.14', 'KO beats estimates'))
        r = b.snapshot()['holdings_review']
        self.assertEqual([(h['symbol'], h['verdict']) for h in r['holdings']], [('KO', 'exit')])   # TSLA is not held: dropped
        self.assertEqual((r['summary'], r['prices']), ('Defensive book.', {'KO': '99.30'}))
        self.assertIn('KO', self.notes[0][1]); self.assertIn('Nothing was sold', self.notes[0][1])

    def test_it_runs_once_a_day_in_session_and_a_failure_is_retried_later(self):
        b = self.brain(ValueError('busy'))
        b.desk = FakeDesk([KO_ROW])
        self.e.s['market'] = dict(open=False)
        self.assertFalse(b.holdings_due())
        self.e.s['market'] = dict(open=True)
        self.assertTrue(b.holdings_due())
        b.review_holdings(); wait(lambda: b.reviewing)
        self.assertIn('busy', b.snapshot()['holdings_review']['error'])
        self.assertFalse(b.holdings_due())                                          # retried after RETRY_HOLDINGS, not at once
        b.desk = FakeDesk([])
        self.assertFalse(b.review_holdings())                                        # nothing held, nothing to ask

class AskTests(Base):
    def test_a_question_is_answered_from_the_live_book_and_follow_ups_carry_the_earlier_answers(self):
        b = self.brain(say('KO is 1% under cost, 10% above its stop.'), say('Its target is 125.19.'))
        b.desk = FakeDesk([KO_ROW])
        self.assertEqual(b.ask({'question': '  '}), 'Type a question first.')
        self.assertIsNone(b.ask({'question': 'How is KO doing?'})); wait(lambda: b.asking)
        messages, kw = self.chat.seen[0]
        self.assertNotIn('tools', kw)
        live = json.loads(messages[1]['content'].removeprefix('Live data:\n'))
        self.assertEqual((live['alpaca_paper']['holdings'][0]['symbol'], live['alpaca_paper']['cash'], live['headlines_48h']['KO'][0]['title']), ('KO', '900', 'KO beats estimates'))
        self.assertIn('cannot place', messages[0]['content'])
        b.ask({'question': 'And its target?'}); wait(lambda: b.asking)
        follow = self.chat.seen[1][0]
        self.assertEqual([m['content'] for m in follow[-3:]], ['How is KO doing?', 'KO is 1% under cost, 10% above its stop.', 'And its target?'])
        self.assertEqual([r['answer'] for r in b.snapshot()['chat']], ['KO is 1% under cost, 10% above its stop.', 'Its target is 125.19.'])

    def test_a_failed_answer_is_shown_as_an_error_and_without_a_key_nothing_is_sent(self):
        b = self.brain(ValueError('OpenRouter answered 429'))
        b.ask({'question': 'Anything?'}); wait(lambda: b.asking)
        self.assertIn('429', b.snapshot()['chat'][-1]['error'])
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': ''}):
            self.assertIn('OPENROUTER_API_KEY', b.ask({'question': 'Anything?'}))
