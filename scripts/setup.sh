#!/bin/bash
# =============================================================================
# ORO Mining Agent — Full Automation Script
# =============================================================================
# Usage:
#   ./scripts/setup.sh       → Install everything
#   ./scripts/submit.sh      → Submit agent to ORO network
#   ./scripts/monitor.sh     → Monitor submission status
#   make test                → Run local tests
#   make submit              → One-command submit
# =============================================================================
set -euo pipefail

echo "════════════════════════════════════════════"
echo "  ORO Mining Agent — Setup"
echo "════════════════════════════════════════════"

# ── Check Python version ─────────────────────────────────────────────────────
python_version=$(python3 --version 2>&1 | cut -d' ' -f2)
major=$(echo "$python_version" | cut -d'.' -f1)
minor=$(echo "$python_version" | cut -d'.' -f2)

if [ "$major" -lt 3 ] || ([ "$major" -eq 3 ] && [ "$minor" -lt 10 ]); then
  echo "❌ Python 3.10+ required (found $python_version)"
  exit 1
fi
echo "✅ Python $python_version"

# ── Install ORO SDK ──────────────────────────────────────────────────────────
echo ""
echo "📦 Installing ORO SDK..."
pip install -U "oro-sdk[bittensor]" -q
echo "✅ ORO SDK installed"

# ── Install dev dependencies ──────────────────────────────────────────────────
echo ""
echo "📦 Installing dev dependencies..."
pip install -r requirements.txt -q
echo "✅ Dev dependencies installed"

# ── Create .env if not exists ─────────────────────────────────────────────────
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo ""
  echo "📝 Created .env from template"
  echo "   → Please edit .env with your credentials before running!"
  echo "   → Required: CHUTES_API_KEY or OPENROUTER_API_KEY"
  echo "   → Required: BITTENSOR_WALLET_NAME, BITTENSOR_WALLET_HOTKEY"
else
  echo "✅ .env already exists"
fi

echo ""
echo "════════════════════════════════════════════"
echo "  Setup complete! Next steps:"
echo "  1. Edit .env with your credentials"
echo "  2. Run: make test       (local test)"
echo "  3. Run: make submit     (submit to ORO)"
echo "════════════════════════════════════════════"
