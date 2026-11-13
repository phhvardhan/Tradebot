#!/usr/bin/env bash
# Run ON your Mac, from the project folder:  bash deploy/push.sh user@server            code only (the server's data stays)
#                                            bash deploy/push.sh user@server --with-data  first time: also copies data/ and .env
# Stop the Mac's own agents before the first --with-data copy (python3 -m signaldesk.supervisor uninstall), or two copies trade on.
set -euo pipefail
HOST="${1:?usage: deploy/push.sh user@server [--with-data]}"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
EXCLUDE=(--exclude __pycache__ --exclude .DS_Store --exclude '*.tmp')
[ "${2:-}" = "--with-data" ] || EXCLUDE+=(--exclude data/ --exclude .env)
rsync -az "${EXCLUDE[@]}" "$DIR/" "$HOST:signaldesk/"
[ "${2:-}" = "--with-data" ] || ssh "$HOST" 'sudo systemctl restart signaldesk 2>/dev/null || true'
echo "Copied to $HOST:~/signaldesk"
