#!/usr/bin/env python3
"""Keep Signal Desk running and watched on macOS, using launchd (built in, no extra software).

  python3 -m signaldesk.supervisor install [--ntfy-topic TOPIC]   start the server at login, restart it whenever it exits, and run the watchdog every minute
  python3 -m signaldesk.supervisor status
  python3 -m signaldesk.supervisor uninstall
  python3 -m signaldesk.supervisor install --dry-run              print what it would write and run, change nothing

This edits your user LaunchAgents (starts programs at login), so run it yourself. Stop any server you started by hand first (it holds port 8765).
`caffeinate -i` keeps the Mac from idle-sleeping while the server runs. Closing the lid or a battery sleep still stops it; the watchdog tells you when that happens."""
import argparse
import os
import plistlib
import socket
import subprocess
import sys
from pathlib import Path

from .core import ROOT
LABELS = ('com.signaldesk.server', 'com.signaldesk.watchdog')
AGENTS = Path.home() / 'Library' / 'LaunchAgents'

def plists(project=ROOT, python=sys.executable, ntfy=None):
    logs = project / 'data' / 'logs'
    common = dict(WorkingDirectory=str(project), RunAtLoad=True)
    server = dict(Label=LABELS[0], ProgramArguments=['/usr/bin/caffeinate', '-i', python, str(project / 'run.py')], KeepAlive=True, ThrottleInterval=10,
                  StandardOutPath=str(logs / 'server.log'), StandardErrorPath=str(logs / 'server.log'), **common)
    watchdog = dict(Label=LABELS[1], ProgramArguments=[python, '-m', 'signaldesk.watchdog'], StartInterval=60,
                    StandardOutPath=str(logs / 'watchdog.log'), StandardErrorPath=str(logs / 'watchdog.log'), **common)
    if ntfy:
        watchdog['EnvironmentVariables'] = {'NTFY_TOPIC': ntfy}  # only the watchdog needs it; the topic acts like a password
    return {LABELS[0]: server, LABELS[1]: watchdog}

def port_in_use(port=8765):
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(('127.0.0.1', port)) == 0

def launchctl(*args):
    return subprocess.run(['launchctl', *args], capture_output=True, text=True)

def loaded(label):
    return launchctl('print', f'gui/{os.getuid()}/{label}').returncode == 0

def install(ntfy=None, dry=False):
    defs = plists(ntfy=ntfy)
    if dry:
        for label, d in defs.items():
            print(f'--- would write {AGENTS / (label + ".plist")}\n{plistlib.dumps(d).decode()}')
        print('then: launchctl bootstrap gui/%d <each plist>' % os.getuid())
        return 0
    if not loaded(LABELS[0]) and port_in_use():
        print('Port 8765 is already in use, probably a server you started by hand. Stop it first:\n  kill $(lsof -ti tcp:8765 -sTCP:LISTEN)\nthen run install again.')
        return 1
    (ROOT / 'data' / 'logs').mkdir(parents=True, exist_ok=True)
    AGENTS.mkdir(parents=True, exist_ok=True)
    for label, d in defs.items():
        path = AGENTS / f'{label}.plist'
        if loaded(label):
            launchctl('bootout', f'gui/{os.getuid()}/{label}')
        path.write_bytes(plistlib.dumps(d))
        r = launchctl('bootstrap', f'gui/{os.getuid()}', str(path))
        print(f'{label}: {"started" if r.returncode == 0 else "FAILED: " + r.stderr.strip()}')
    print('Logs: data/logs/. Send yourself a test alert with: python3 -m signaldesk.watchdog --test')
    return 0

def uninstall():
    for label in LABELS:
        if loaded(label):
            launchctl('bootout', f'gui/{os.getuid()}/{label}')
        (AGENTS / f'{label}.plist').unlink(missing_ok=True)
        print(f'{label}: removed')
    return 0

def status():
    for label in LABELS:
        out = launchctl('print', f'gui/{os.getuid()}/{label}')
        state = next((l.strip() for l in out.stdout.splitlines() if l.strip().startswith('state =')), 'not loaded' if out.returncode else 'loaded')
        print(f'{label}: {state}')
    return 0

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('cmd', choices=['install', 'status', 'uninstall'])
    p.add_argument('--ntfy-topic'); p.add_argument('--dry-run', action='store_true')
    a = p.parse_args(argv)
    return install(a.ntfy_topic, a.dry_run) if a.cmd == 'install' else uninstall() if a.cmd == 'uninstall' else status()

if __name__ == '__main__':
    raise SystemExit(main())
