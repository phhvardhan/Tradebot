"""Alerts that do not depend on the dashboard: a line in data/alerts.log, a macOS notification, and (only if you set NTFY_TOPIC) a phone push through ntfy.sh.
The ntfy topic works like a password: anyone who knows it can read your alerts. Pick a long random one, e.g. `python3 -c "import secrets;print(secrets.token_urlsafe(16))"`."""
import json
import subprocess
import time
import urllib.request
from pathlib import Path

from .core import ROOT, secret

LOG = ROOT / 'data' / 'alerts.log'

def send(title, text, urgent=False, log=None):
    """Send everywhere it can; returns what happened per channel and never raises, since an alert path that crashes is worse than none."""
    results = {}
    try:
        path = Path(log or LOG)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'a') as f:
            f.write(f'{time.strftime("%Y-%m-%d %H:%M:%S")} {"URGENT " if urgent else ""}{title}: {text}\n')
        results['log'] = 'written'
    except OSError as exc:
        results['log'] = f'failed: {exc}'
    try:
        subprocess.run(['osascript', '-e', f'display notification {json.dumps(text, ensure_ascii=False)} with title {json.dumps(title, ensure_ascii=False)}' + (' sound name "Basso"' if urgent else '')],
                       timeout=5, check=False, capture_output=True)
        results['mac'] = 'sent'
    except Exception as exc:
        results['mac'] = f'failed: {exc}'
    topic = secret('NTFY_TOPIC')  # environment, else .env
    if topic:
        try:
            req = urllib.request.Request(f'{secret("NTFY_SERVER") or "https://ntfy.sh"}/{topic}', data=text.encode(), method='POST',
                                         headers={'Title': title.encode('ascii', 'ignore').decode(), 'Priority': 'urgent' if urgent else 'default', 'Tags': 'warning' if urgent else 'white_check_mark'})
            urllib.request.urlopen(req, timeout=8).read()
            results['ntfy'] = 'sent'
        except Exception as exc:
            results['ntfy'] = f'failed: {exc}'
    return results
