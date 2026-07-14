# =============================================================================
# ORO Mining Agent — Makefile
# One-command automation for all operations.
# =============================================================================

.PHONY: help setup test test-product test-shop test-voucher submit monitor \
        lint check git-push all

# Default target
help:
	@echo ""
	@echo "ORO Mining Agent — Commands"
	@echo "═══════════════════════════════════════"
	@echo "  make setup        Install all dependencies"
	@echo "  make test         Run all local tests"
	@echo "  make test-product Test product strategy"
	@echo "  make test-shop    Test shop strategy"
	@echo "  make test-voucher Test voucher strategy"
	@echo "  make lint         Check code quality"
	@echo "  make submit       Submit agent to ORO network"
	@echo "  make monitor      Monitor submission status"
	@echo "  make git-push     Commit & push to GitHub"
	@echo "  make all          test + submit + git-push"
	@echo "═══════════════════════════════════════"
	@echo ""

# Setup: install all dependencies
setup:
	@bash scripts/setup.sh

# Run all local tests
test:
	@echo "🧪 Running all tests..."
	@python3 test_agent.py --problem all

# Run specific problem type tests
test-product:
	@python3 test_agent.py --problem product

test-shop:
	@python3 test_agent.py --problem shop

test-voucher:
	@python3 test_agent.py --problem voucher

# Lint and syntax check
lint:
	@echo "🔍 Checking code..."
	@python3 -m py_compile agent.py && echo "✅ agent.py syntax OK"
	@python3 -m py_compile test_agent.py && echo "✅ test_agent.py syntax OK"
	@grep -q "def agent_main" agent.py && echo "✅ agent_main found"
	@echo "✅ All checks passed"

# Submit to ORO network
submit: lint
	@bash scripts/submit.sh

# Monitor submission status
monitor:
	@bash scripts/monitor.sh

# Commit and push to GitHub
git-push:
	@echo "📤 Pushing to GitHub..."
	@git add -A
	@git commit -m "chore: update agent - $$(date '+%Y-%m-%d %H:%M')" || \
		echo "  (nothing to commit)"
	@git push origin main
	@echo "✅ Pushed to GitHub"

# Full pipeline: test → submit → push
all: test submit git-push

# Docker test (using ORO's official test environment)
docker-test:
	@echo "🐳 Running Docker test..."
	@if [ -d "../.." ] && [ -f "../../docker-compose.yml" ]; then \
		cd ../.. && docker compose run test; \
	else \
		echo "⚠️  ORO repo not found. Clone https://github.com/ORO-AI/oro first."; \
	fi
