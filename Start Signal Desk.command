#!/bin/zsh
cd -- "$(dirname -- "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  print 'Python 3 is required but was not found in your Terminal PATH.'
  read '?Press Return to close…'
  exit 1
fi
if lsof -ti tcp:8765 -sTCP:LISTEN >/dev/null 2>&1; then
  print 'Signal Desk is already running on http://127.0.0.1:8765 (another window or launchd started it).'
  print 'Opening it in your browser. To restart it instead, stop the other copy first: kill $(lsof -ti tcp:8765 -sTCP:LISTEN)'
  open 'http://127.0.0.1:8765'
  read '?Press Return to close…'
  exit 0
fi
print 'Starting Signal Desk. Keep this Terminal window open.'
print 'Opening http://127.0.0.1:8765 in your browser once the server is up.'
(sleep 3; open 'http://127.0.0.1:8765') &
python3 run.py
launch_status=$?
if (( launch_status != 0 )); then
  print '\nThe server could not start. The error is shown above.'
  read '?Press Return to close…'
fi
exit $launch_status
