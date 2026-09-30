#!/usr/bin/env bash
# Festive Sale Gadget Advisor - run locally and publish a public URL (macOS / Linux).
#
#   ./run-local.sh
#
# Starts the app on http://localhost:7860 and opens a free Cloudflare quick tunnel so it is reachable from the
# internet. No Cloudflare account, no card, no domain. Ctrl+C stops both.
#
# Running here rather than on a cloud host is not a compromise: Amazon and Flipkart challenge datacenter IPs far
# more than residential ones, so price lookups actually succeed more often from your own connection.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$ROOT/app"
VENV="$ROOT/.venv"
PORT="${PORT:-7860}"

say() { printf '\n\033[36m>> %s\033[0m\n' "$1"; }
warn() { printf '\033[33m   %s\033[0m\n' "$1"; }
die() { printf '\033[31m%s\033[0m\n' "$1"; exit 1; }

# ------------------------------------------------------------------ python ----
say 'Checking Python'
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1; then
    v=$("$c" -c 'import sys;print("%d%02d"%sys.version_info[:2])' 2>/dev/null || echo 0)
    if [ "$v" -ge 311 ]; then PY="$c"; break; fi
  fi
done
[ -n "$PY" ] || die "Python 3.11+ required (browser-use needs it). Install it and re-run."
echo "   using: $PY ($("$PY" --version))"

# ------------------------------------------------------------------ chrome ----
say 'Checking Chrome'
CHROME=""
for c in "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
         "/Applications/Chromium.app/Contents/MacOS/Chromium" \
         /usr/bin/google-chrome-stable /usr/bin/chromium /usr/bin/chromium-browser /snap/bin/chromium; do
  [ -x "$c" ] && { CHROME="$c"; break; }
done
if [ -n "$CHROME" ]; then
  echo "   found: $CHROME"; export PRICE_CHROME_PATH="$CHROME"
else
  warn "Chrome/Chromium not found - install it, or live price checks will fail."
fi

# On a headless Linux box Chrome needs a display; the app starts Xvfb itself when it is installed.
if [ "$(uname)" = "Linux" ] && [ -z "${DISPLAY:-}" ] && ! command -v Xvfb >/dev/null 2>&1; then
  warn "No DISPLAY and no Xvfb - Chrome will run headless (more captchas). apt install xvfb to improve this."
fi

command -v node >/dev/null 2>&1 || warn "Node.js not found - the Tavily and Memory agents need it (https://nodejs.org)."

# -------------------------------------------------------------------- venv ----
[ -d "$VENV" ] || { say 'Creating virtual environment (one time)'; "$PY" -m venv "$VENV"; }
VPY="$VENV/bin/python"

say 'Installing dependencies (a few minutes the first time)'
"$VPY" -m pip install --upgrade pip --quiet
"$VPY" -m pip install -r "$APP/requirements.txt" --quiet || die 'Dependency install failed.'

# -------------------------------------------------------------------- keys ----
if [ ! -f "$ROOT/.env" ]; then
  say 'Setting up your API keys (stored in .env, never committed)'
  read -rp 'OPENAI_API_KEY: ' k1
  read -rp 'TAVILY_API_KEY: ' k2
  printf 'OPENAI_API_KEY=%s\nTAVILY_API_KEY=%s\n' "$k1" "$k2" > "$ROOT/.env"
  echo '   saved to .env'
fi
set -a; . "$ROOT/.env"; set +a

# ------------------------------------------------------------- cloudflared ----
CF="$(command -v cloudflared || true)"
if [ -z "$CF" ]; then
  CF="$ROOT/cloudflared"
  if [ ! -x "$CF" ]; then
    say 'Downloading cloudflared (one time)'
    os=$(uname | tr '[:upper:]' '[:lower:]')
    arch=$(uname -m); case "$arch" in x86_64) arch=amd64;; aarch64|arm64) arch=arm64;; esac
    curl -fsSL -o "$CF" \
      "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-${os}-${arch}" \
      || die 'Could not download cloudflared. Get it from https://github.com/cloudflare/cloudflared/releases'
    chmod +x "$CF"
  fi
fi

# ------------------------------------------------------------------ run it ----
export PORT
say "Starting the app on http://localhost:$PORT"
( cd "$APP" && "$VPY" app.py ) & APP_PID=$!

LOG="$(mktemp)"
cleanup() {
  say 'Shutting down'
  kill "$APP_PID" "${TUN_PID:-}" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo '   waiting for it to come up...'
ready=0
for _ in $(seq 1 60); do
  sleep 2
  if curl -fsS "http://localhost:$PORT/healthz" >/dev/null 2>&1; then ready=1; break; fi
  kill -0 "$APP_PID" 2>/dev/null || die 'The app exited during start-up - see the errors above.'
done
[ "$ready" = 1 ] && printf '\033[32m   app is up\033[0m\n' || warn 'App did not respond in time; continuing anyway.'

say 'Opening the public tunnel'
"$CF" tunnel --no-autoupdate --url "http://localhost:$PORT" >"$LOG" 2>&1 & TUN_PID=$!

URL=""
for _ in $(seq 1 45); do
  sleep 2
  URL=$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -1 || true)
  [ -n "$URL" ] && break
done

echo
printf '\033[32m%s\033[0m\n' "===================================================================="
if [ -n "$URL" ]; then
  printf '\033[32m YOUR PUBLIC URL\033[0m\n   %s\n\n' "$URL"
  printf '\033[32m Paste it into the front end to connect:\033[0m\n'
  echo   '   https://festive-sale-gadget-advisor.vercel.app'
  echo   '   -> scroll to "Run it" and paste it into the "Connect a backend" box'
else
  printf '\033[33m Tunnel did not report a URL. Log: %s\033[0m\n' "$LOG"
fi
printf '\033[32m Local:  http://localhost:%s\033[0m\n' "$PORT"
printf '\033[32m Ctrl+C stops both the app and the tunnel.\033[0m\n'
printf '\033[32m%s\033[0m\n' "===================================================================="

wait "$APP_PID"
