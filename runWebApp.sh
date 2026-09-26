#!/usr/bin/env bash
# Launch Nomad Life locally.
# Checks requirements, frees the configured port if busy, installs dependencies and starts the app.
set -euo pipefail

cd "$(dirname "$0")"

info()  { printf '\033[1;36m[nomad-life]\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m[nomad-life]\033[0m %s\n' "$*"; }
fail()  { printf '\033[1;31m[nomad-life]\033[0m %s\n' "$*" >&2; exit 1; }

PY_CHECK='import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'

# 1. Python
PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null 2>&1 || fail "python3 not found. Please install Python 3.10 or newer."
"$PYTHON" -c "$PY_CHECK" || fail "Python 3.10 or newer is required (found $("$PYTHON" -V 2>&1))."
# Debian and Ubuntu ship venv without ensurepip, which venv needs to install pip.
"$PYTHON" -c 'import venv, ensurepip' 2>/dev/null || fail "The Python venv module is missing (on Debian/Ubuntu: apt install python3-venv)."
info "Using $("$PYTHON" -V 2>&1)"

# 2. Configuration
if [[ ! -f config.env ]]; then
  # Only you can read it: it holds SECRET_KEY and, later, the Gmail App Password.
  (umask 077; cp config.env.example config.env)
  secret="$("$PYTHON" -c 'import secrets; print(secrets.token_hex(32))')"
  sed -i.bak "s/^SECRET_KEY=.*/SECRET_KEY=${secret}/" config.env && rm -f config.env.bak
  warn "Created config.env from config.env.example with a random SECRET_KEY."
  warn "Add your Gmail credentials to config.env to enable password reset emails."
fi

# 3. Virtual environment and dependencies
VENV_PY=".venv/bin/python"
if [[ -d .venv ]] && { [[ ! -f .venv/bin/activate ]] || ! "$VENV_PY" -c "$PY_CHECK" >/dev/null 2>&1; }; then
  # e.g. the Python it was created with was upgraded or removed, or creating it was interrupted
  warn "The virtual environment in .venv is broken or too old. Recreating it."
  rm -rf .venv
fi
if [[ ! -d .venv ]]; then
  info "Creating virtual environment in .venv"
  "$PYTHON" -m venv .venv || { rm -rf .venv; fail "Could not create the virtual environment in .venv (on Debian/Ubuntu: apt install python3-venv)."; }
fi
# Always call the venv interpreter by path so we never fall back to the system Python.
# shellcheck disable=SC1091
source .venv/bin/activate

req_hash="$(sha256sum requirements.txt 2>/dev/null || shasum -a 256 requirements.txt)"
req_hash="${req_hash%% *}"
if [[ ! -f .requirements.stamp ]] || [[ "$(cat .requirements.stamp)" != "$req_hash" ]] \
   || ! "$VENV_PY" -c 'import flask, flask_login, flask_wtf, dotenv, fpdf' 2>/dev/null; then
  info "Installing requirements"
  "$VENV_PY" -m pip install --quiet --upgrade pip
  "$VENV_PY" -m pip install --quiet -r requirements.txt
  echo "$req_hash" > .requirements.stamp
else
  info "Requirements already installed"
fi

# 4. Port: read config.env with the same parser the app uses (handles export, quotes, comments)
PORT="$("$VENV_PY" -c 'from dotenv import dotenv_values; print((dotenv_values("config.env").get("APP_PORT") or "").strip())')"
PORT="${PORT:-5050}"
# Read as base 10: bash treats a leading 0 as octal (0022 would pass as 18 and then free port 22).
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] && PORT=$((10#$PORT)) && (( PORT >= 1 && PORT <= 65535 )) \
  || fail "APP_PORT in config.env must be a number between 1 and 65535 (got '$PORT')."
# config.env is the source of truth: override any APP_PORT already exported in the shell.
export APP_PORT="$PORT"

# Browsers refuse to open these ports (ERR_UNSAFE_PORT), so the app would be unreachable.
BLOCKED_PORTS=" 1 7 9 11 13 15 17 19 20 21 22 23 25 37 42 43 53 69 77 79 87 95 101 102 103 104 109 110 111 113 115 117 119 123 135 137 139 143 161 179 389 427 465 512 513 514 515 526 530 531 532 540 548 554 556 563 587 601 636 989 990 993 995 1719 1720 1723 2049 3659 4045 4190 5060 5061 6000 6566 6665 6666 6667 6668 6669 6679 6697 10080 "
[[ "$BLOCKED_PORTS" == *" $PORT "* ]] \
  && fail "Port $PORT is blocked by web browsers (ERR_UNSAFE_PORT). Choose another APP_PORT in config.env, for example 5050."

port_pids() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | sort -u || true
  elif command -v fuser >/dev/null 2>&1; then
    fuser -n tcp "$PORT" 2>/dev/null | tr -s ' ' '\n' | grep -E '^[0-9]+$' || true
  elif command -v ss >/dev/null 2>&1; then
    ss -ltnpH "sport = :$PORT" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u || true
  fi
}

port_bindable() {
  "$VENV_PY" - "$PORT" <<'PY' 2>/dev/null
import socket, sys
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(1)
finally:
    s.close()
PY
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
  [[ -z "$(port_pids)" ]] && port_bindable || fail "Could not free port $PORT."
  info "Port $PORT is now free"
elif ! port_bindable; then
  if ! command -v lsof >/dev/null 2>&1 && ! command -v fuser >/dev/null 2>&1 && ! command -v ss >/dev/null 2>&1; then
    fail "Port $PORT is in use, but no lsof, fuser or ss is available to find the process. Stop it manually or change APP_PORT in config.env."
  elif (( PORT < 1024 )); then
    fail "Port $PORT cannot be used: ports below 1024 need administrator rights. Change APP_PORT in config.env, for example to 5050."
  else
    fail "Port $PORT cannot be used: it is probably taken by another user's process, which this script cannot see or stop. Change APP_PORT in config.env."
  fi
else
  info "Port $PORT is free"
fi

# 5. Start
mkdir -p data uploads
info "Starting Nomad Life on http://localhost:$PORT (press Ctrl+C to stop)"
exec "$VENV_PY" app.py
