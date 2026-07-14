# ORO Mining Agent 🥇

> **Agent de minage de classe mondiale pour le subnet ORO (Bittensor SN15)**  
> Stratégie LLM-driven ReAct — optimisé pour dominer le leaderboard

[![Submit Agent](https://github.com/taoveu/oro-agent/actions/workflows/submit.yml/badge.svg)](https://github.com/taoveu/oro-agent/actions/workflows/submit.yml)

## 🏆 Architecture

L'agent utilise un **pattern ReAct (Reason + Act)** avec des stratégies spécialisées pour les 3 types de problèmes du benchmark ORO :

| Type | Stratégie | Critère de succès |
|------|-----------|-------------------|
| **product** | Recherche multi-filtres + vérification attributs | Trouver UN produit satisfaisant toutes les contraintes |
| **shop** | Découverte de shop + recherche ciblée | Tous les produits du MÊME shop |
| **voucher** | Calcul de prix après remise | Budget respecté après réduction |

### Score optimisé

```
true_score = outcome_score × reasoning_coefficient
```

- **reasoning_coefficient** (0.3 → 1.0) : évalué par un juge LLM
- Notre agent génère un raisonnement multi-étapes riche → coefficient proche de **1.0**
- Utilisation de TOUS les paramètres de `find_product` (price, sort, shop_id, service)

## 🚀 Démarrage rapide

### 1. Setup

```bash
git clone https://github.com/taoveu/oro-agent
cd oro-agent
make setup
```

### 2. Configurer vos credentials

```bash
cp .env.example .env
# Éditer .env avec vos clés API
```

Variables requises dans `.env` :
```bash
CHUTES_API_KEY=...          # ou OPENROUTER_API_KEY
BITTENSOR_WALLET_NAME=...
BITTENSOR_WALLET_HOTKEY=...
SANDBOX_MODEL=Qwen/Qwen3-32B-TEE
```

### 3. Tester localement

```bash
make test               # Tous les types de problèmes
make test-product       # Uniquement product
make test-shop          # Uniquement shop
make test-voucher       # Uniquement voucher
```

### 4. Soumettre à ORO

```bash
make submit
```

### 5. Monitorer

```bash
make monitor
```

## 📋 Commandes

```bash
make help           # Afficher toutes les commandes
make setup          # Installer les dépendances
make test           # Tests locaux (tous types)
make lint           # Vérification syntaxe
make submit         # Soumettre l'agent
make monitor        # Surveiller le statut
make git-push       # Commit + push GitHub
make all            # test + submit + git-push (pipeline complet)
```

## 🤖 CI/CD — Soumission automatique

**Déclencher une soumission automatique :**

1. Créer un tag de version :
   ```bash
   git tag v1.0 && git push origin v1.0
   ```

2. Ou depuis GitHub → Actions → "Submit Agent" → "Run workflow"

**Configurer les secrets GitHub :**
- `CHUTES_API_KEY` ou `OPENROUTER_API_KEY`
- `BITTENSOR_WALLET_NAME`
- `BITTENSOR_WALLET_HOTKEY`
- `BITTENSOR_COLDKEY_SS58`

## 📁 Structure

```
oro-agent/
├── agent.py              # ← L'agent principal (soumettre ce fichier)
├── test_agent.py         # Tests locaux sans Docker
├── Makefile              # Automatisation
├── requirements.txt      # Dépendances
├── .env.example          # Template de configuration
├── .gitignore
├── scripts/
│   ├── setup.sh          # Installation
│   ├── submit.sh         # Soumission à ORO
│   └── monitor.sh        # Monitoring
└── .github/
    └── workflows/
        └── submit.yml    # CI/CD GitHub Actions
```

## 🧠 Modèles LLM disponibles

| Modèle | Provider | Performance |
|--------|----------|-------------|
| `Qwen/Qwen3-32B-TEE` | Chutes + OpenRouter | 🥇 Recommandé |
| `Qwen/Qwen3.5-397B-A17B-TEE` | Chutes + OpenRouter | 🏋️ Plus puissant |
| `moonshotai/Kimi-K2.6-TEE` | Chutes + OpenRouter | ⚡ Rapide |
| `deepseek-ai/DeepSeek-V3.2-TEE` | Chutes | 🔧 Défaut SDK |

Changer le modèle dans `.env` : `SANDBOX_MODEL=Qwen/Qwen3.5-397B-A17B-TEE`

## 📊 Leaderboard

[🏆 oroagents.com/leaderboard](https://oroagents.com/leaderboard)

## 📖 Documentation ORO

- [Quick Start](https://oroagents.com/docs/miners/quick-start)
- [Agent Interface](https://oroagents.com/docs/miners/agent-interface)
- [Evaluation Lifecycle](https://oroagents.com/docs/miners/evaluation-lifecycle)
- [Inference Providers](https://oroagents.com/docs/miners/inference-providers)
