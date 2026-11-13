#!/usr/bin/env python3
"""Independent watchdog. Run it every minute from outside the server (signaldesk.supervisor installs it for launchd); it reads the heartbeat files the
server rewrites after every check and alerts when the server cannot be trusted to watch your positions. It works when the server is dead because it does not use the server.

Alerts: no heartbeat (server down or hung), checks failing for a long time while the market is open (feed down: exits are not evaluated), agent paused with open
positions, a held position with no usable price for 10 minutes, challenger failing, missing NYSE calendar (and, once a day, a calendar that ends within 60 days). Each problem alerts once, repeats every 30 minutes while it lasts, and says when it clears.

  python3 -m signaldesk.watchdog            one check (what launchd runs)
  python3 -m signaldesk.watchdog --test     send a test alert to every channel, to see that you get it
"""
import argparse
import importlib.util
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from . import notify
from .core import ROOT, SKILLS

STALE_OPEN, STALE_CLOSED = 4 * 60, 20 * 60   # seconds with no heartbeat before it counts as down (a check every minute, plus slow feeds)
FEED_DOWN = 10 * 60                          # seconds without a successful check, market open, before it counts as a feed outage
REPEAT = 30 * 60                             # re-alert this often while a problem lasts
CALENDAR_AHEAD = 60 * 86400                  # warn this long before the NYSE calendar runs out
REPEAT_FOR = {'calendar_soon': 86400}        # a warning, not an outage: once a day

def market_status(now):
    spec = importlib.util.spec_from_file_location('skill_alerts', SKILLS / 'trader-alerts' / 'alerts.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.market_status(datetime.fromtimestamp(now, timezone.utc).replace(tzinfo=None))

def problems(beats, now, market_open):
    """beats: {market: heartbeat dict, or None if the file is missing}. Returns {problem key: text} for everything wrong right now."""
    out = {}
    for name, hb in beats.items():
        if hb is None:
            if name == 'stocks':
                out['stocks:missing'] = 'stocks: no heartbeat file. The server has not run, or its data folder changed.'
            continue
        held, live = hb.get('positions', 0), market_open or name == 'crypto'
        if name == 'crypto' and not held:
            continue  # nothing to protect there
        age = now - hb['at']
        if age > (STALE_OPEN if live else STALE_CLOSED):
            out[f'{name}:stale'] = f'{name}: no check for {age / 60:.0f} min. Stops and targets are NOT being watched ({held} open position{"s" * (held != 1)}). The server is down or stuck.'
            continue
        if not hb.get('running') and held and live:
            out[f'{name}:paused'] = f'{name}: the agent is paused with {held} open position{"s" * (held != 1)}. Stops and targets are NOT being checked. Press Start.'
        elif hb.get('running') and not hb.get('ok') and live and hb.get('last_ok') and now - hb['last_ok'] > FEED_DOWN:
            out[f'{name}:feed'] = f'{name}: checks have failed for {(now - hb["last_ok"]) / 60:.0f} min ({hb.get("error")}). Exits are not being evaluated for {held} open position{"s" * (held != 1)}.'
        lost = sorted(s for s, t in (hb.get('unpriced') or {}).items() if now - t > FEED_DOWN)
        if lost and live and hb.get('running'):  # the check loop can be healthy while a held position has no price, so its exits are blind
            out[f'{name}:unpriced'] = f'{name}: no usable price for {", ".join(lost)} for over {FEED_DOWN // 60} min. Stops and targets for {"it" if len(lost) == 1 else "them"} are NOT being checked.'
        if hb.get('backup_error'):
            out[f'{name}:backup'] = f'{name}: {hb["backup_error"]}. Trading continues; the daily copy is retried every check.'
        if hb.get('challenger_error'):
            out[f'{name}:challenger'] = f'{name}: the challenger portfolio is failing ({hb["challenger_error"]}). The baseline is unaffected, but the comparison has a gap.'
    return out

def run_check(data_dir, now, send=notify.send, status_fn=market_status):
    data = Path(data_dir)
    beats = {}
    for name in ('stocks', 'crypto', 'day'):
        try:
            beats[name] = json.loads((data / f'heartbeat-{name}.json').read_text())
        except (OSError, ValueError):
            beats[name] = None
    status = status_fn(now)
    found = problems(beats, now, bool(status.get('open')))
    if not status.get('calendar_known'):
        found['calendar'] = 'The NYSE calendar for this year is not in the trader-alerts skill, so the agent will refuse to trade. Update vendor/trader-alerts/alerts.py.'
    elif not status_fn(now + CALENDAR_AHEAD).get('calendar_known'):
        found['calendar_soon'] = (f'The NYSE calendar in vendor/trader-alerts/alerts.py ends within {CALENDAR_AHEAD // 86400} days. '
                                  'Add next year\'s holidays and early closes, or the agents stop trading on January 1.')
    state_path = data / 'watchdog.json'
    try:
        prior = json.loads(state_path.read_text())
    except (OSError, ValueError):
        prior = {}
    sent, state = [], {}
    for key, text in found.items():
        if key not in prior or now - prior[key] >= REPEAT_FOR.get(key, REPEAT):
            send('Signal Desk needs attention', text, urgent=key not in REPEAT_FOR); sent.append((key, text))
            state[key] = now
        else:
            state[key] = prior[key]
    for key in prior:
        if key not in found:
            send('Signal Desk recovered', f'{key} is back to normal.'); sent.append((key, 'recovered'))
    state_path.write_text(json.dumps(state))
    return sent

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--data-dir', default=str(ROOT / 'data'))
    p.add_argument('--test', action='store_true', help='send a test alert to every channel and exit')
    a = p.parse_args(argv)
    if a.test:
        print(notify.send('Signal Desk test', 'If you can read this, alerts reach you.', urgent=True))
        return 0
    for key, text in run_check(a.data_dir, time.time()):
        print(time.strftime('%H:%M:%S'), key, text)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
