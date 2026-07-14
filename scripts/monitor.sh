#!/bin/bash
# =============================================================================
# ORO Mining Agent — Monitor Script
# Continuously polls the status of the latest submission.
# =============================================================================
set -euo pipefail

ENV_FILE="${ENV_FILE:-.env}"
REFRESH_SECONDS="${REFRESH:-30}"

if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC2046
  export $(grep -v '^#' "$ENV_FILE" | xargs)
fi

echo "════════════════════════════════════════════"
echo "  ORO Mining Agent — Monitor"
echo "  Refreshing every ${REFRESH_SECONDS}s"
echo "  Press Ctrl+C to stop"
echo "════════════════════════════════════════════"
echo ""

while true; do
  TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
  echo "[$TIMESTAMP] Fetching status..."
  
  oro status 2>&1 || echo "⚠️  Could not fetch status"
  
  echo ""
  echo "📊 Leaderboard: https://oroagents.com/leaderboard"
  echo "   (refreshing in ${REFRESH_SECONDS}s...)"
  echo ""
  
  sleep "$REFRESH_SECONDS"
done
