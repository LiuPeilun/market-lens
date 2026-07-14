#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

BACKEND_HOST="${MARKET_LENS_HOST:-127.0.0.1}"
BACKEND_PORT="${MARKET_LENS_PORT:-8001}"
FRONTEND_HOST="${MARKET_LENS_FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${MARKET_LENS_FRONTEND_PORT:-5173}"

if [[ -z "${UV_CACHE_DIR:-}" ]]; then
  export UV_CACHE_DIR="$ROOT_DIR/.uv-cache"
fi

if [[ -z "${VITE_API_PROXY_TARGET:-}" ]]; then
  export VITE_API_PROXY_TARGET="http://$BACKEND_HOST:$BACKEND_PORT"
fi

BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
  if [[ -n "$FRONTEND_PID" ]] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
    kill "$FRONTEND_PID" 2>/dev/null || true
  fi
  if [[ -n "$BACKEND_PID" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    kill "$BACKEND_PID" 2>/dev/null || true
  fi
}

trap cleanup EXIT INT TERM

echo "Starting Market Lens backend: http://$BACKEND_HOST:$BACKEND_PORT"
(
  cd "$ROOT_DIR"
  uv run --no-sync uvicorn market_lens.api.app:app \
    --host "$BACKEND_HOST" \
    --port "$BACKEND_PORT"
) &
BACKEND_PID=$!

echo "Starting Market Lens frontend: http://$FRONTEND_HOST:$FRONTEND_PORT"
(
  cd "$ROOT_DIR/frontend"
  ./node_modules/.bin/vite \
    --host "$FRONTEND_HOST" \
    --port "$FRONTEND_PORT"
) &
FRONTEND_PID=$!

echo
echo "Frontend: http://$FRONTEND_HOST:$FRONTEND_PORT"
echo "Backend:  http://$BACKEND_HOST:$BACKEND_PORT"
echo "Press Ctrl+C to stop both services."
echo

wait -n "$BACKEND_PID" "$FRONTEND_PID"
