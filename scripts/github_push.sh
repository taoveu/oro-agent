#!/bin/bash
# =============================================================================
# GitHub Push Helper — uses stored macOS keychain credentials
# Usage: bash scripts/github_push.sh
# =============================================================================
set -euo pipefail

REPO_URL="https://github.com/taoveu/oro-agent.git"

echo "📤 Pushing to GitHub (taoveu/oro-agent)..."
git add -A
git commit -m "chore: update agent - $(date '+%Y-%m-%d %H:%M')" 2>/dev/null || echo "(nothing to commit)"
git push origin main
echo "✅ Done! View at: $REPO_URL"
