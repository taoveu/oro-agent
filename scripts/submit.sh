#!/bin/bash
# =============================================================================
# ORO Mining Agent — Submit Script
# Submits agent.py to the ORO network and shows the result.
# =============================================================================
set -euo pipefail

AGENT_FILE="${1:-agent.py}"
ENV_FILE="${ENV_FILE:-.env}"

echo "════════════════════════════════════════════"
echo "  ORO Mining Agent — Submission"
echo "════════════════════════════════════════════"

# ── Load .env ─────────────────────────────────────────────────────────────────
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC2046
  export $(grep -v '^#' "$ENV_FILE" | xargs)
  echo "✅ Loaded environment from $ENV_FILE"
else
  echo "⚠️  No .env file found — using existing environment"
fi

# ── Validate agent file ───────────────────────────────────────────────────────
if [ ! -f "$AGENT_FILE" ]; then
  echo "❌ Agent file not found: $AGENT_FILE"
  exit 1
fi

# Check Python syntax
echo ""
echo "🔍 Validating Python syntax..."
python3 -m py_compile "$AGENT_FILE" && echo "✅ Syntax OK" || {
  echo "❌ Syntax error in $AGENT_FILE"
  exit 1
}

# Check that agent_main is defined
if ! grep -q "def agent_main" "$AGENT_FILE"; then
  echo "❌ agent_main function not found in $AGENT_FILE"
  exit 1
fi
echo "✅ agent_main function found"

# ── Check ORO SDK login ───────────────────────────────────────────────────────
echo ""
echo "🔐 Checking ORO authentication..."
if ! oro status &>/dev/null; then
  echo "   Logging in with Bittensor wallet..."
  oro login \
    --wallet-name "${BITTENSOR_WALLET_NAME:-default}" \
    --wallet-hotkey "${BITTENSOR_WALLET_HOTKEY:-default}" \
    --network "${BITTENSOR_NETWORK:-finney}"
fi
echo "✅ Authenticated"

# ── Submit the agent ──────────────────────────────────────────────────────────
echo ""
echo "🚀 Submitting $AGENT_FILE to ORO network..."
SUBMIT_OUTPUT=$(oro submit "$AGENT_FILE" 2>&1)
echo "$SUBMIT_OUTPUT"

# Extract submission ID if present
SUBMISSION_ID=$(echo "$SUBMIT_OUTPUT" | grep -oE '[a-f0-9-]{36}' | head -1 || true)

echo ""
echo "════════════════════════════════════════════"
if [ -n "$SUBMISSION_ID" ]; then
  echo "  ✅ Submitted! ID: $SUBMISSION_ID"
  echo "  → Monitor: make monitor"
  echo "  → Leaderboard: https://oroagents.com/leaderboard"
else
  echo "  ✅ Submission complete"
  echo "  → Check status: oro status"
  echo "  → Leaderboard: https://oroagents.com/leaderboard"
fi
echo "════════════════════════════════════════════"
