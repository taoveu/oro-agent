#!/bin/bash
# =============================================================================
# ORO Mining Agent — Monitor Script
# =============================================================================
set -euo pipefail

ENV_FILE="${ENV_FILE:-.env}"
REFRESH_SECONDS="${REFRESH:-30}"
ORO="/Users/cvn/bittensor/venv/bin/oro"

if [ -f "$ENV_FILE" ]; then
  set -a; source "$ENV_FILE"; set +a
fi

echo "════════════════════════════════════════════"
echo "  ORO Mining Agent — Monitor (UID 29)"
echo "  Refreshing every ${REFRESH_SECONDS}s"
echo "  Ctrl+C pour arrêter"
echo "════════════════════════════════════════════"

while true; do
  echo ""
  echo "[$(date '+%H:%M:%S')] Statut de l'inference provider :"
  "$ORO" inference status \
    --wallet-name "${BITTENSOR_WALLET_NAME:-default}" \
    --wallet-hotkey "${BITTENSOR_WALLET_HOTKEY:-default}" 2>&1 || true

  echo ""
  echo "📊 Leaderboard: https://oroagents.com/leaderboard"
  echo "   (refresh dans ${REFRESH_SECONDS}s — Ctrl+C pour arrêter)"
  sleep "$REFRESH_SECONDS"
done
