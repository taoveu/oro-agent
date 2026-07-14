#!/bin/bash
# =============================================================================
# GitHub Setup — Run once to create the repo and push
# Usage: bash scripts/github_init.sh <GITHUB_TOKEN>
# =============================================================================
set -euo pipefail

GITHUB_TOKEN="${1:-${GITHUB_TOKEN:-}}"
REPO_NAME="oro-agent"
REPO_OWNER="taoveu"
REPO_DESC="World-class ORO Bittensor mining agent — LLM-driven ReAct strategy"

if [ -z "$GITHUB_TOKEN" ]; then
  echo "Usage: bash scripts/github_init.sh <GITHUB_TOKEN>"
  echo ""
  echo "Get your token at: https://github.com/settings/tokens"
  echo "Required scope: repo"
  exit 1
fi

echo "════════════════════════════════════════════"
echo "  Creating GitHub repository: $REPO_OWNER/$REPO_NAME"
echo "════════════════════════════════════════════"

# Create the repo via GitHub API
RESPONSE=$(curl -s -X POST \
  -H "Authorization: token $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  https://api.github.com/user/repos \
  -d "{
    \"name\": \"$REPO_NAME\",
    \"description\": \"$REPO_DESC\",
    \"private\": false,
    \"auto_init\": false
  }")

# Check for errors
if echo "$RESPONSE" | grep -q '"message"'; then
  MSG=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('message','Unknown error'))")
  if [ "$MSG" = "Repository creation failed." ] || echo "$MSG" | grep -q "already exists"; then
    echo "⚠️  Repository already exists — skipping creation"
  else
    echo "❌ GitHub API error: $MSG"
    exit 1
  fi
else
  echo "✅ Repository created: https://github.com/$REPO_OWNER/$REPO_NAME"
fi

# Configure remote
git remote remove origin 2>/dev/null || true
git remote add origin "https://${GITHUB_TOKEN}@github.com/$REPO_OWNER/$REPO_NAME.git"
echo "✅ Remote configured"

# Push to GitHub
echo ""
echo "📤 Pushing to GitHub..."
git push -u origin main --force
echo "✅ Pushed!"

# Print secrets instructions
echo ""
echo "════════════════════════════════════════════"
echo "  ✅ GitHub repo ready!"
echo "  URL: https://github.com/$REPO_OWNER/$REPO_NAME"
echo ""
echo "  Next: Add these secrets in GitHub Settings:"
echo "  → Settings > Secrets and variables > Actions"
echo ""
echo "  Required secrets:"
echo "    CHUTES_API_KEY         (or OPENROUTER_API_KEY)"
echo "    BITTENSOR_WALLET_NAME"
echo "    BITTENSOR_WALLET_HOTKEY"
echo "    BITTENSOR_COLDKEY_SS58"
echo ""
echo "  To auto-submit: git tag v1.0 && git push origin v1.0"
echo "════════════════════════════════════════════"
