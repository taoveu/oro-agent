# =============================================================================
# ORO Mining Agent — Makefile
# One-command automation for all operations.
# =============================================================================

.PHONY: help setup test test-product test-shop test-voucher submit monitor \
        lint audit validate validate-product validate-shop validate-voucher \
        check git-push all

# Default target
help:
	@echo ""
	@echo "ORO Mining Agent — Commands"
	@echo "═══════════════════════════════════════"
	@echo "  make test             Run all local mock tests (42 scénarios)"
	@echo "  make audit            Audit statique : endpoint, modèle, Gate 1..."
	@echo "  make validate         Validation réelle contre l'API ORO (30 problems)"
	@echo "  make validate-product Validation Product uniquement (API réelle)"
	@echo "  make lint             Syntaxe + audit (alias de make audit)"
	@echo "  make submit           Soumettre (nécessite audit OK)"
	@echo "  make monitor          Suivre le statut de soumission"
	@echo "  make git-push         Commit & push vers GitHub"
	@echo "═══════════════════════════════════════"
	@echo ""

# Setup: install all dependencies
setup:
	@bash scripts/setup.sh

# ── Tests locaux (mocks) ──────────────────────────────────────────────────────

# Run all local tests (validateur renforcé V3 — 42 scénarios)
test:
	@echo "🧪 Running full test harness (42 scenarios)..."
	@python3 test_harness.py

# Run specific problem type tests
test-product:
	@python3 test_harness.py --category product

test-shop:
	@python3 test_harness.py --category shop

test-voucher:
	@python3 test_harness.py --category voucher

# Show only failures
test-failures:
	@python3 test_harness.py --fail-only

# ── Audit statique (Gate 1 + conformité ORO) ──────────────────────────────────

# Audit statique complet — vérifie endpoint, modèle, LLM, imports dangereux
audit:
	@echo "🔍 Audit statique agent.py..."
	@python3 validate_real_api.py --audit-only

# Alias pour compatibilité
lint: audit

# ── Validation réelle contre l'API ORO ───────────────────────────────────────

# Validation complète (30 vrais problèmes, API réelle)
validate:
	@echo "🌐 Validation réelle — API ORO (suite 3, 30 problèmes)..."
	@python3 validate_real_api.py

# Validation par catégorie
validate-product:
	@python3 validate_real_api.py --cat product

validate-shop:
	@python3 validate_real_api.py --cat shop

validate-voucher:
	@python3 validate_real_api.py --cat voucher

# Validation rapide (5 premiers problèmes)
validate-quick:
	@python3 validate_real_api.py --n 5

# ── Soumission ────────────────────────────────────────────────────────────────

# Submit to ORO network (nécessite audit OK)
submit: audit
	@bash scripts/submit.sh

# Monitor submission status
monitor:
	@bash scripts/monitor.sh

# ── Git ───────────────────────────────────────────────────────────────────────

# Commit and push to GitHub
git-push:
	@echo "📤 Pushing to GitHub..."
	@git add -A
	@git commit -m "chore: update agent - $$(date '+%Y-%m-%d %H:%M')" || \
		echo "  (nothing to commit)"
	@git push origin main
	@echo "✅ Pushed to GitHub"

# ── Docker (ORO officiel) ─────────────────────────────────────────────────────

# Docker test (using ORO's official test environment)
docker-test:
	@echo "🐳 Running Docker test (environnement officiel ORO)..."
	@if [ -d "../.." ] && [ -f "../../docker-compose.yml" ]; then \
		cd ../.. && docker compose run test; \
	else \
		echo "⚠️  ORO repo not found. Clone https://github.com/ORO-AI/oro first."; \
		echo "   cd .. && git clone https://github.com/ORO-AI/oro && cd oro/agent"; \
	fi

# ── Pipeline complet ──────────────────────────────────────────────────────────

# Pipeline recommandé avant soumission : mock tests + audit + validation réelle
pre-submit: test audit validate
	@echo "✅ Pipeline complet réussi — prêt pour make submit"

# Full pipeline: test → submit → push
all: test submit git-push
