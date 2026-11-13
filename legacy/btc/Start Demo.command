#!/bin/zsh
cd -- "$(dirname -- "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  print 'Python 3 is required but was not found in your Terminal PATH.'
  read '?Press Return to close…'
  exit 1
fi
print 'Starting Signal Desk. Keep this Terminal window open.'
print 'Once the server reports it is ready, open http://127.0.0.1:8765'
PYTHONPATH="$PWD/../.." python3 -m legacy.btc.bot --demo
launch_status=$?
if (( launch_status != 0 )); then
  print '\nThe server could not start. The error is shown above.'
  read '?Press Return to close…'
fi
exit $launch_status
