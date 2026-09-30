#!/usr/bin/env bash
# Report accounts that look fake (confirmed but never signed in, holding no data) and offer to
# delete them, after two confirmations. Uses config.env like the app, so on the server it works
# on the live database: take a backup first.
#   ./checkFakeUsers.sh                      report, then ask what to delete
#   ./checkFakeUsers.sh --dry-run            report only
#   ./checkFakeUsers.sh --min-age-hours 72   only accounts older than 3 days (default 24)
#
# Python: $PYTHON if set, else the active virtual environment, else the app's .venv, else
# python3 (or python) from PATH, as in GitHub Actions.
set -euo pipefail
cd "$(dirname "$0")"
if [[ -n "${PYTHON:-}" ]]; then
  py="$PYTHON"
elif [[ -n "${VIRTUAL_ENV:-}" && -x "$VIRTUAL_ENV/bin/python" ]]; then
  py="$VIRTUAL_ENV/bin/python"
elif [[ -x .venv/bin/python ]]; then
  py=.venv/bin/python
else
  py="$(command -v python3 || command -v python || true)"
fi
if [[ -z "$py" ]] || ! "$py" -c "import flask" 2>/dev/null; then
  echo "checkFakeUsers: no Python with Flask found${py:+ ($py)}. Install the app first (./runWebApp.sh, or setup_vps.md section 4), or activate its environment." >&2
  exit 1
fi
exec "$py" -m flask --app app check-fake-users "$@"
