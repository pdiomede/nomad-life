#!/usr/bin/env bash
# Report accounts that look fake (confirmed but never signed in, holding no data) and offer to
# delete them, after two confirmations. Uses config.env like the app, so on the server it works
# on the live database: take a backup first.
#   ./checkFakeUsers.sh                      report, then ask what to delete
#   ./checkFakeUsers.sh --dry-run            report only
#   ./checkFakeUsers.sh --min-age-hours 72   only accounts older than 3 days (default 24)
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/flask ]]; then
  echo "checkFakeUsers: .venv/bin/flask not found. Install the app first (./runWebApp.sh, or setup_vps.md section 4)." >&2
  exit 1
fi
exec .venv/bin/flask --app app check-fake-users "$@"
