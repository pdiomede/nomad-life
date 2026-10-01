#!/usr/bin/env bash
# Report accounts that look fake (confirmed but never signed in, holding no data) and offer to
# delete them, after two confirmations. Uses config.env like the app, so on the server it works
# on the live database: take a backup first.
#   ./checkFakeUsers.sh                      report, then ask what to delete
#   ./checkFakeUsers.sh --dry-run            report only
#   ./checkFakeUsers.sh --min-age-hours 72   only accounts older than 3 days (default 24)
#
# Python: $PYTHON if set (used as given), else the first that can load the app among the active
# virtual environment, the app's .venv, and python3 or python from PATH (as in GitHub Actions).
# Another project's environment, or a Python with Flask alone, is skipped: it would fail with
# "No such command 'check-fake-users'".
set -euo pipefail
cd "$(dirname "$0")"
probe="import flask, flask_login, flask_wtf, dotenv, segno"
if [[ -n "${PYTHON:-}" ]]; then
  candidates=("$PYTHON")
else
  candidates=()
  if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    candidates+=("$VIRTUAL_ENV/bin/python")
  fi
  candidates+=(.venv/bin/python "$(command -v python3 || true)" "$(command -v python || true)")
fi
py=""
for candidate in "${candidates[@]}"; do
  if [[ -n "$candidate" ]] && "$candidate" -c "$probe" 2>/dev/null; then
    py="$candidate"
    break
  fi
done
if [[ -z "$py" ]]; then
  echo "checkFakeUsers: no Python with the app's packages found${PYTHON:+ (\$PYTHON is $PYTHON)}. Install the app first (./runWebApp.sh, or setup_vps.md section 4), or activate its environment." >&2
  exit 1
fi
exec "$py" -m flask --app app check-fake-users "$@"
