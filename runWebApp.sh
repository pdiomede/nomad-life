#!/usr/bin/env bash
# Launch Nomad Life locally.
# Checks requirements, frees the configured port if busy, installs dependencies and starts the app.
set -euo pipefail

cd "$(dirname "$0")"

info()  { printf '\033[1;36m[nomad-life]\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m[nomad-life]\033[0m %s\n' "$*"; }
fail()  { printf '\033[1;31m[nomad-life]\033[0m %s\n' "$*" >&2; exit 1; }

# 1. Python
PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null 2>&1 || fail "python3 not found. Please install Python 3.10 or newer."
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || fail "Python 3.10 or newer is required (found $("$PYTHON" -V 2>&1))."
"$PYTHON" -c 'import venv' 2>/dev/null || fail "The Python venv module is missing (on Debian/Ubuntu: apt install python3-venv)."
info "Using $("$PYTHON" -V 2>&1)"

# 2. Configuration
if [[ ! -f config.env ]]; then
  cp config.env.example config.env
  secret="$("$PYTHON" -c 'import secrets; print(secrets.token_hex(32))')"
  sed -i.bak "s/^SECRET_KEY=.*/SECRET_KEY=${secret}/" config.env && rm -f config.env.bak
  warn "Created config.env from config.env.example with a random SECRET_KEY."
  warn "Add your Gmail credentials to config.env to enable password reset emails."
fi

PORT="$(grep -E '^[[:space:]]*APP_PORT=' config.env | tail -n1 | cut -d= -f2- | tr -d '[:space:]"'"'"'')"
PORT="${PORT:-5050}"
[[ "$PORT" =~ ^[0-9]+$ ]] || fail "APP_PORT in config.env must be a number (got '$PORT')."

# 3. Virtual environment and dependencies
if [[ ! -d .venv ]]; then
  info "Creating virtual environment in .venv"
  "$PYTHON" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

req_hash="$(sha256sum requirements.txt 2>/dev/null || shasum -a 256 requirements.txt)"
req_hash="${req_hash%% *}"
if [[ ! -f .requirements.stamp ]] || [[ "$(cat .requirements.stamp)" != "$req_hash" ]] \
   || ! python -c 'import flask, flask_login, flask_wtf, dotenv' 2>/dev/null; then
  info "Installing requirements"
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -r requirements.txt
  echo "$req_hash" > .requirements.stamp
else
  info "Requirements already installed"
fi

# 4. Free the port if something is listening on it
port_pids() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true
  elif command -v fuser >/dev/null 2>&1; then
    fuser -n tcp "$PORT" 2>/dev/null | tr -s ' ' '\n' | grep -E '^[0-9]+$' || true
  elif command -v ss >/dev/null 2>&1; then
    ss -ltnpH "sport = :$PORT" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u || true
  fi
}

pids="$(port_pids)"
if [[ -n "$pids" ]]; then
  warn "Port $PORT is in use by PID(s): $(echo "$pids" | tr '\n' ' ')"
  warn "Stopping them to free the port"
  kill $pids 2>/dev/null || true
  for _ in 1 2 3 4 5; do
    [[ -z "$(port_pids)" ]] && break
    sleep 1
  done
  pids="$(port_pids)"
  if [[ -n "$pids" ]]; then
    kill -9 $pids 2>/dev/null || true
    sleep 1
  fi
  [[ -z "$(port_pids)" ]] || fail "Could not free port $PORT."
  info "Port $PORT is now free"
else
  info "Port $PORT is free"
fi

# 5. Start
mkdir -p data uploads
info "Starting Nomad Life on http://localhost:$PORT (press Ctrl+C to stop)"
exec python app.py
