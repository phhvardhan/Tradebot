import json
import plistlib
import subprocess
import tempfile
import time
import unittest
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

from signaldesk import notify, supervisor, watchdog
from tests.test_startup import Case

NOW = 1_800_000_000.0
OPEN, CLOSED = dict(open=True, calendar_known=True), dict(open=False, calendar_known=True)

def hb(age=10, positions=3, running=True, ok=True, last_ok_age=10, error=None, challenger_error=None):
    return dict(at=NOW - age, positions=positions, running=running, ok=ok, last_ok=NOW - last_ok_age, error=error, challenger_error=challenger_error)

class DecideTests(unittest.TestCase):
    def test_healthy_server_raises_nothing(self):
        self.assertEqual(watchdog.problems({'stocks': hb(), 'crypto': hb(positions=0)}, NOW, True), {})
    def test_no_heartbeat_for_minutes_while_the_market_is_open_means_positions_are_unwatched(self):
        p = watchdog.problems({'stocks': hb(age=5 * 60), 'crypto': None}, NOW, True)
        self.assertIn('NOT being watched', p['stocks:stale'])
        self.assertIn('3 open positions', p['stocks:stale'])
    def test_the_same_gap_after_the_close_is_tolerated_for_longer(self):
        self.assertEqual(watchdog.problems({'stocks': hb(age=10 * 60)}, NOW, False), {})
        self.assertIn('stocks:stale', watchdog.problems({'stocks': hb(age=30 * 60)}, NOW, False))
    def test_missing_file_for_stocks_is_a_problem_but_crypto_with_nothing_held_is_not(self):
        p = watchdog.problems({'stocks': None, 'crypto': None}, NOW, True)
        self.assertEqual(set(p), {'stocks:missing'})
    def test_checks_failing_for_ten_minutes_is_a_feed_outage_with_the_error_in_the_text(self):
        p = watchdog.problems({'stocks': hb(ok=False, last_ok_age=11 * 60, error='Yahoo unreachable')}, NOW, True)
        self.assertIn('Yahoo unreachable', p['stocks:feed'])
        self.assertEqual(watchdog.problems({'stocks': hb(ok=False, last_ok_age=5 * 60)}, NOW, True), {})  # a short blip is not paged
    def test_a_held_position_without_a_price_for_ten_minutes_alerts_even_when_checks_succeed(self):
        p = watchdog.problems({'stocks': dict(hb(), unpriced={'XOM': NOW - 11 * 60})}, NOW, True)
        self.assertIn('XOM', p['stocks:unpriced'])
        self.assertEqual(watchdog.problems({'stocks': dict(hb(), unpriced={'XOM': NOW - 60})}, NOW, True), {})      # a blip is not paged
        self.assertEqual(watchdog.problems({'stocks': dict(hb(), unpriced={'XOM': NOW - 11 * 60})}, NOW, False), {})  # closed: nothing to protect
    def test_a_failing_backup_is_reported_without_calling_the_server_down(self):
        p = watchdog.problems({'stocks': dict(hb(), backup_error='Backup of stocks.sqlite failed: OSError: full')}, NOW, True)
        self.assertEqual(set(p), {'stocks:backup'})
    def test_paused_with_positions_is_flagged_only_while_the_market_is_open(self):
        self.assertIn('stocks:paused', watchdog.problems({'stocks': hb(running=False)}, NOW, True))
        self.assertEqual(watchdog.problems({'stocks': hb(running=False)}, NOW, False), {})
        self.assertEqual(watchdog.problems({'stocks': hb(running=False, positions=0)}, NOW, True), {})
    def test_challenger_failure_is_reported_separately(self):
        self.assertIn('stocks:challenger', watchdog.problems({'stocks': hb(challenger_error='boom')}, NOW, True))

class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.dir = Path(self.tmp.name); self.addCleanup(self.tmp.cleanup)
        self.sent = []
        self.send = lambda title, text, urgent=False: self.sent.append((title, text, urgent))
    def write(self, **kw):
        (self.dir / 'heartbeat-stocks.json').write_text(json.dumps(hb(**kw)))
    def check(self, t, status=OPEN):
        return watchdog.run_check(self.dir, NOW + t, self.send, status if callable(status) else lambda now: status)

    def test_alert_once_repeat_after_thirty_minutes_and_say_when_it_recovers(self):
        self.write(age=0)
        self.assertEqual(self.check(0), [])                          # healthy: silence
        self.check(6 * 60)                                           # 6 minutes later the heartbeat is stale
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.sent[0][2])                             # urgent
        self.check(16 * 60)
        self.assertEqual(len(self.sent), 1)                          # still down, no spam
        self.check(37 * 60)
        self.assertEqual(len(self.sent), 2)                          # reminder
        (self.dir / 'heartbeat-stocks.json').write_text(json.dumps(dict(hb(age=0), at=NOW + 40 * 60)))
        self.check(40 * 60)
        self.assertIn('recovered', self.sent[-1][0])
        self.check(41 * 60)
        self.assertEqual(len(self.sent), 3)                          # and then quiet again
    def test_missing_calendar_year_is_reported_because_the_agent_would_refuse_to_trade(self):
        self.write(age=0)
        self.check(0, dict(open=False, calendar_known=False))
        self.assertIn('calendar', self.sent[0][1])
    def test_a_calendar_that_ends_within_60_days_warns_once_a_day_not_every_30_minutes(self):
        self.write(age=0)
        ends = lambda now: dict(open=False, calendar_known=now < NOW + 30 * 86400)                # the calendar runs out in 30 days
        cal = lambda: [m[1] for m in self.sent if 'NYSE calendar' in m[1]]                        # the heartbeat goes stale too; only the calendar matters here
        self.check(0, ends); self.check(31 * 60, ends)
        self.assertEqual(len(cal()), 1)
        self.check(86400, ends)
        self.assertEqual(len(cal()), 2)
        self.assertIn('stop trading on January 1', cal()[0])
    def test_a_corrupt_heartbeat_is_treated_as_missing_not_a_crash(self):
        (self.dir / 'heartbeat-stocks.json').write_text('{nope')
        self.check(0)
        self.assertIn('no heartbeat file', self.sent[0][1])

class HeartbeatTests(Case):
    def test_engine_writes_a_heartbeat_after_each_check_with_the_failure_when_there_is_one(self):
        a, _ = self.build('--paused')
        a.tick()
        h = json.loads((self.dir / 'heartbeat-stocks.json').read_text())
        self.assertEqual((h['ok'], h['positions'], h['market'], h['running']), (True, 3, 'stocks', False))
        self.mode = 'down'
        a.tick()
        h = json.loads((self.dir / 'heartbeat-stocks.json').read_text())
        self.assertFalse(h['ok']); self.assertIn('Yahoo unreachable', h['error'])
        self.assertIsNotNone(h['last_ok'])                           # the watchdog measures an outage from the last good check
    def test_a_paused_agent_still_beats_so_paused_is_distinguishable_from_dead(self):
        a, _ = self.build('--paused')
        a.beat()
        self.assertFalse(json.loads((self.dir / 'heartbeat-stocks.json').read_text())['running'])
    def test_a_dead_server_is_caught_by_the_watchdog_from_its_last_heartbeat(self):
        a, _ = self.build('--paused')
        a.tick()                                                     # the server then dies: no more heartbeats
        sent = []
        out = watchdog.run_check(self.dir, time.time() + 6 * 60, lambda *x, **k: sent.append(x), lambda now: OPEN)
        self.assertEqual([k for k, _ in out], ['stocks:stale'])

class NotifyAndSupervisorTests(unittest.TestCase):
    def test_send_logs_always_never_raises_and_only_uses_ntfy_when_configured(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {}, clear=False) as env:
            env.pop('NTFY_TOPIC', None)
            with patch('signaldesk.notify.subprocess.run', side_effect=FileNotFoundError('no osascript')), patch('signaldesk.notify.urllib.request.urlopen') as web:
                r = notify.send('t', 'text', urgent=True, log=Path(tmp) / 'a.log')
            self.assertEqual((r['log'], r['mac'].startswith('failed')), ('written', True))
            web.assert_not_called()
            self.assertIn('URGENT', (Path(tmp) / 'a.log').read_text())
            with patch('signaldesk.notify.subprocess.run'), patch('signaldesk.notify.urllib.request.urlopen') as web, patch.dict('os.environ', {'NTFY_TOPIC': 'secret-topic'}):
                self.assertEqual(notify.send('Title é', 'x', log=Path(tmp) / 'a.log')['ntfy'], 'sent')
            self.assertTrue(web.call_args[0][0].full_url.endswith('/secret-topic'))
    def test_launchd_definitions_restart_the_server_and_run_the_watchdog_every_minute(self):
        d = supervisor.plists(Path('/proj'), '/usr/bin/python3', ntfy='topic')
        server, dog = d['com.signaldesk.server'], d['com.signaldesk.watchdog']
        self.assertTrue(server['KeepAlive'] and server['RunAtLoad'])
        self.assertEqual(server['ProgramArguments'][:2], ['/usr/bin/caffeinate', '-i'])
        self.assertEqual(dog['StartInterval'], 60)
        self.assertEqual(dog['EnvironmentVariables'], {'NTFY_TOPIC': 'topic'})
        self.assertNotIn('EnvironmentVariables', server)             # the topic goes only where it is needed
        for plist in d.values():
            self.assertEqual(plistlib.loads(plistlib.dumps(plist)), plist)
            with tempfile.NamedTemporaryFile(suffix='.plist') as f:
                f.write(plistlib.dumps(plist)); f.flush()
                lint = subprocess.run(['plutil', '-lint', f.name], capture_output=True, text=True) if Path('/usr/bin/plutil').exists() else None
                self.assertTrue(lint is None or lint.returncode == 0, lint and lint.stdout)
    def test_install_refuses_to_start_a_second_server_on_a_taken_port_and_dry_run_changes_nothing(self):
        with patch('signaldesk.supervisor.loaded', return_value=False), patch('signaldesk.supervisor.port_in_use', return_value=True), patch('signaldesk.supervisor.launchctl') as lc:
            self.assertEqual(supervisor.install(), 1)
            lc.assert_not_called()
        with patch('signaldesk.supervisor.launchctl') as lc, patch('builtins.print'):
            self.assertEqual(supervisor.install(dry=True), 0)
            lc.assert_not_called()

if __name__ == '__main__':
    unittest.main()
