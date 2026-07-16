#!/bin/bash
# =============================================================================
# ORO Mining Agent — Submit Script
# =============================================================================
set -euo pipefail

AGENT_FILE="${1:-agent.py}"
ENV_FILE="${ENV_FILE:-.env}"
ORO="/Users/cvn/bittensor/venv/bin/oro"

echo "════════════════════════════════════════════"
echo "  ORO Mining Agent — Submission"
echo "════════════════════════════════════════════"

# ── Load .env ─────────────────────────────────────────────────────────────────
if [ -f "$ENV_FILE" ]; then
  set -a; source "$ENV_FILE"; set +a
  echo "✅ Loaded environment from $ENV_FILE"
else
  echo "⚠️  No .env file found — using existing environment"
fi

# ── Validate agent file ───────────────────────────────────────────────────────
if [ ! -f "$AGENT_FILE" ]; then
  echo "❌ Agent file not found: $AGENT_FILE"
  exit 1
fi

echo ""
echo "🔍 Validating Python syntax..."
python3 -m py_compile "$AGENT_FILE" && echo "✅ Syntax OK"
grep -q "def agent_main" "$AGENT_FILE" && echo "✅ agent_main found"

# ── Connect inference provider if not already ─────────────────────────────────
echo ""
echo "🔗 Checking inference provider..."
PROVIDER_STATUS=$("$ORO" inference status \
  --wallet-name "${BITTENSOR_WALLET_NAME:-default}" \
  --wallet-hotkey "${BITTENSOR_WALLET_HOTKEY:-default}" 2>&1 || true)

if echo "$PROVIDER_STATUS" | grep -qi "openrouter\|connected\|chutes"; then
  echo "✅ Inference provider already connected"
else
  echo "   Connecting OpenRouter..."
  if [ -n "${OPENROUTER_API_KEY:-}" ]; then
    "$ORO" inference connect openrouter \
      --api-key "$OPENROUTER_API_KEY" \
      --wallet-name "${BITTENSOR_WALLET_NAME:-default}" \
      --wallet-hotkey "${BITTENSOR_WALLET_HOTKEY:-default}" && \
      echo "✅ OpenRouter connected"
  else
    echo "⚠️  OPENROUTER_API_KEY not set — skipping provider setup"
  fi
fi

# ── Submit the agent ──────────────────────────────────────────────────────────
echo ""
echo "🚀 Submitting $AGENT_FILE to ORO network..."
"$ORO" submit \
  --agent-name "oro-agent" \
  --agent-file "$AGENT_FILE" \
  --wallet-name "${BITTENSOR_WALLET_NAME:-default}" \
  --wallet-hotkey "${BITTENSOR_WALLET_HOTKEY:-default}"

echo ""
echo "════════════════════════════════════════════"
echo "  ✅ Submitted!"
echo "  → Leaderboard: https://oroagents.com/leaderboard"
echo "  → Monitor    : make monitor"
echo "════════════════════════════════════════════"
