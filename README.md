# 🛡️ PropSentry — Quantitative Trading Engine

> **Moteur de trading algorithmique événementiel et semi-automatisé calibré pour réussir et maintenir les évaluations Prop Firm 5,000 USD (FTMO / Goat Funded Trader).**

---

## 📑 Table des Matières

1. [Vue d'ensemble](#-vue-densemble)
2. [Les 8 Invariants Non-Négociables](#-les-8-invariants-non-négociables)
3. [Architecture Système](#-architecture-système)
4. [Structure du Répertoire](#-structure-du-répertoire)
5. [Prérequis](#-prérequis)
6. [Installation & Configuration Rapide](#-installation--configuration-rapide)
7. [Démarrage pas-à-pas](#-démarrage-pas-à-pas)
8. [Mode Backtest & Monte Carlo](#-mode-backtest--monte-carlo)
9. [Suites de Tests (91 tests unitaires)](#-suites-de-tests-91-tests-unitaires)
10. [Sécurité & Supervision](#-sécurité--supervision)

---

## 🎯 Vue d'ensemble

**PropSentry** est conçu pour opérer selon des règles de gestion des risques déterministes strictes :
- **Capital initial :** 5,000.00 USD
- **Risque max par trade :** 0.50% de la balance initiale (= 25.00 USD max)
- **Ratio Risque / Récompense minimum :** 1:2 (2.0R)
- **Seuil Kill-Switch quotidien :** -4.20% (= 210.00 USD de perte flottante)
- **Seuil de perte max globale :** -10.00% (= solde plancher à 4,500.00 USD)
- **Gestion des positions :** 1 position ouverte à la fois, Breakeven automatique à +1R
- **Validation humaine :** Copilot Telegram avec timeout d'auto-rejet de 2 minutes

---

## 🔒 Les 8 Invariants Non-Négociables

| Invariant | Règle Métier | Implémentation dans le Code |
|---|---|---|
| **INV 1 (Risk Determinism)** | Prix d'entrée, SL, TP et lot size 100% déterministes. Le LLM ne produit jamais de prix ni de volumes. | [`src/core/schemas/signals.py`](file:///c:/Users/LENOVO/Documents/Github/PropSentry/src/core/schemas/signals.py) & [`src/risk/position_sizer.py`](file:///c:/Users/LENOVO/Documents/Github/PropSentry/src/risk/position_sizer.py) |
| **INV 2 (Sizing Cap)** | Risque strictement bridé à 0.5% (≤ 25 USD). Toute violation lève `SizingCapBreached`. | [`src/risk/position_sizer.py`](file:///c:/Users/LENOVO/Documents/Github/PropSentry/src/risk/position_sizer.py) (`validate_risk_usd`) |
| **INV 3 (Continuous Equity)** | Drawdown calculé tick-par-tick contre l'*equity flottante*, pas le solde fermé. | [`src/risk/equity_watchdog.py`](file:///c:/Users/LENOVO/Documents/Github/PropSentry/src/risk/equity_watchdog.py) (`EquityWatchdog`) |
| **INV 4 (Kill-Switch)** | À -4.20% : annulation immédiate de tous les ordres en attente + clôture de toutes les positions au marché. | [`src/risk/kill_switch.py`](file:///c:/Users/LENOVO/Documents/Github/PropSentry/src/risk/kill_switch.py) (`KillSwitch.execute`) |
| **INV 5 (Daily Reset)** | Reset à 00:00 CE(S)T (`Europe/Prague`). Gestion dynamique DST (CET UTC+1 / CEST UTC+2). | [`src/core/broker_clock.py`](file:///c:/Users/LENOVO/Documents/Github/PropSentry/src/core/broker_clock.py) (`to_broker_time`, `get_daily_reset_utc`) |
| **INV 6 (MT5 Isolation)** | Seul `src/broker/mt5_client.py` a le droit d'importer `MetaTrader5`. Le conteneur Linux Docker communique en HTTP. | [`src/broker/mt5_bridge.py`](file:///c:/Users/LENOVO/Documents/Github/PropSentry/src/broker/mt5_bridge.py) & [`src/broker/bridge_client.py`](file:///c:/Users/LENOVO/Documents/Github/PropSentry/src/broker/bridge_client.py) |
| **INV 7 (LLM Context TTL)** | L'analyse LLM est mise en cache dans Redis avec un TTL de 15 minutes. Si périmée ou `trading_allowed=false`, tout nouvel ordre est bloqué. | [`src/agent/worker.py`](file:///c:/Users/LENOVO/Documents/Github/PropSentry/src/agent/worker.py) & Gate 8 dans [`src/engine/preflight_gates.py`](file:///c:/Users/LENOVO/Documents/Github/PropSentry/src/engine/preflight_gates.py) |
| **INV 8 (Parity Gate)** | Le live et le backtest partagent la même logique bar-par-bar et doivent produire des résultats numériquement identiques. | [`src/engine/runner.py`](file:///c:/Users/LENOVO/Documents/Github/PropSentry/src/engine/runner.py) & [`src/engine/parity_gate.py`](file:///c:/Users/LENOVO/Documents/Github/PropSentry/src/engine/parity_gate.py) |

---

## 🏗️ Architecture Système

La librairie `MetaTrader5` nécessite l'accès natif au terminal MT5 sous Windows (IPC Windows). Pour permettre une conteneurisation robuste sous Docker (Linux) sans compromettre MT5, le système utilise une **architecture hybride** :

```
┌─────────────────────────── WINDOWS HOST ────────────────────────────┐
│                                                                     │
│   ┌─────────────────────┐               ┌───────────────────────┐   │
│   │ Terminal MT5        │ ◄── IPC Win ──┤ src/broker/           │   │
│   │ (FTMO / GFT Demo)   │               │   mt5_bridge.py       │   │
│   └─────────────────────┘               │   (FastAPI :8765)     │   │
│                                         └───────────▲───────────┘   │
└─────────────────────────────────────────────────────┼───────────────┘
                                                      │ HTTP REST
┌─────────────────────── DOCKER ENGINE (Linux) ───────┼───────────────┐
│                                                     │               │
│   ┌────────────────────────┐            ┌───────────▼───────────┐   │
│   │ Redis (TTL 15 min)     │ ◄───────── │ src/engine/           │   │
│   │ - MarketContextState   │            │   live_engine.py      │   │
│   └────────────────────────┘            │   - Runner (INV 8)    │   │
│                                         │   - Watchdog (INV 3)  │   │
│   ┌────────────────────────┐            │   - 8 Gates (INV 1-8) │   │
│   │ SQLite (trading.db)    │            └───────────┬───────────┘   │
│   │ - Orders / Snapshots   │ ◄──────────────────────┤               │
│   └────────────────────────┘                        ▼               │
│                                         ┌───────────────────────┐   │
│                                         │ src/copilot/          │   │
│                                         │   telegram_bot.py     │   │
│                                         │   (Validation 2 min)  │   │
│                                         └───────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 📁 Structure du Répertoire

```text
PropSentry/
├── .github/workflows/ci.yml       # CI : lint, tests headless, build Docker
├── config/
│   ├── prop_firm_rules.yaml       # Contrat de risque chiffré (FTMO 5K)
│   ├── settings.yaml              # Configuration générale par défaut
│   ├── settings.local.yaml        # Surcharges locales gitignorées
│   └── loader.py                  # Pydantic Settings & deep-merge YAML
├── data/
│   ├── historical/                # Données Parquet / CSV pour le backtest
│   ├── backups/                   # Sauvegardes SQLite quotidiennes (rétention 14j)
│   └── cache/                     # Fichiers de cache indicateurs
├── src/
│   ├── core/                      # Invariants de domaine et horloges
│   │   ├── broker_clock.py        # Fuseau Europe/Prague DST-aware
│   │   ├── time_guards.py         # Fenêtres de sessions (Londres/NY), vendredi 20h
│   │   ├── exceptions.py          # Hiérarchie InvariantBreachError
│   │   └── schemas/               # Candle, CompositeSignal, Context, OrderIntent
│   ├── engine/                    # Exécution et logique de marché
│   │   ├── indicators.py          # ATR, EMA, FVG, Liquidity Sweep
│   │   ├── preflight_gates.py     # Les 8 portes de vérification
│   │   ├── parity_gate.py         # Assert de parité live/backtest
│   │   ├── runner.py              # Runner bar-par-bar unifié
│   │   ├── live_engine.py         # Orchestrateur async complet
│   │   └── backtest_engine.py     # Moteur de replay historique
│   ├── risk/                      # Gestion du risque et sizing
│   │   ├── position_sizer.py      # Calcul du lot size (cap 0.5%)
│   │   ├── equity_watchdog.py     # Moniteur tick-par-tick
│   │   ├── kill_switch.py         # Exécution immédiate du kill-switch
│   │   └── exposure_guard.py      # Gardien du nombre de positions ouvertes
│   ├── broker/                    # Communication broker
│   │   ├── mt5_client.py          # Client natif MT5 (Windows Host)
│   │   ├── mt5_bridge.py          # Pont HTTP FastAPI (Windows Host)
│   │   ├── bridge_client.py       # Client HTTP Bridge (Docker)
│   │   ├── order_dispatcher.py    # Envoi d'ordres & retry idempotent
│   │   ├── position_manager.py    # Déplacement au Breakeven à +1R
│   │   └── reconciler.py          # Détection positions fantômes
│   ├── agent/                     # Pipeline LLM Context
│   │   ├── news_fetcher.py        # Fetch calendrier Finnhub
│   │   ├── news_gate.py           # Blackout ±30 min
│   │   ├── regime_classifier.py   # Classification tendance / volatilité
│   │   ├── llm_auditor.py         # Prompt système LLM avec timeout 5s
│   │   └── worker.py              # Worker async d'actualisation Redis
│   ├── copilot/                   # Interface opérateur Telegram
│   │   ├── telegram_bot.py        # Bot Telegram async
│   │   ├── message_formatter.py   # Formatage des messages d'alerte HTML
│   │   └── callback_handler.py    # Validation / Rejet opérateur
│   ├── storage/                   # Persistance des données
│   │   ├── models.py              # Tables SQLAlchemy (orders, daily_snaps, audits)
│   │   ├── db.py                  # Session factory SQLite
│   │   └── repository.py          # Écritures append-only
│   └── supervisor/                # Santé et résilience
│       ├── heartbeat.py           # Détection de boucle bloquée
│       ├── deadman_switch.py      # Ping HTTP de présence (5 min)
│       └── backup_service.py      # Snapshot quotidien SQLite
├── scripts/
│   ├── run_backtest.py            # Script CLI de backtest
│   ├── run_live_copilot.py        # Point d'entrée de production live
│   ├── run_paper.py               # Point d'entrée paper trading
│   ├── run_monte_carlo.py         # Simulateur Monte Carlo de drawdown
│   └── take_daily_snapshot.py     # Snapshot manuel de fin de journée
├── tests/                         # 9 suites de tests unitaires (91 tests)
├── Dockerfile                     # Image de production Linux
├── Dockerfile.dev                 # Image de développement & tests
├── docker-compose.yml             # Orchestration Docker Engine + Redis
├── start_bridge.bat               # Démarrage automatique du pont MT5
├── requirements-bridge.txt        # Dépendances spécifiques Windows Host
└── pyproject.toml                 # Dépendances Python du projet
```

---

## 💻 Prérequis

1. **Python 3.11 ou 3.12** installé sur votre machine.
2. **MetaTrader 5** installé sur Windows et connecté à votre compte d'évaluation (Demo ou Live).
3. **Docker Desktop** (si vous souhaitez lancer le moteur en conteneur).
4. Un bot Telegram créé via [@BotFather](https://t.me/BotFather) (pour les alertes).
5. Une clé API gratuite [Finnhub](https://finnhub.io/) (pour le calendrier économique).

---

## ⚙️ Installation & Configuration Rapide

### 1. Cloner et configurer l'environnement
```bash
# Copier le template d'environnement
copy .env.example .env
```

Éditer le fichier `.env` avec vos accès :
```ini
# MetaTrader 5 (Compte Démo / Évaluation)
MT5_LOGIN=12345678
MT5_PASSWORD=votre_mot_de_passe
MT5_SERVER=FTMO-Demo
MT5_BRIDGE_PORT=8765
MT5_BRIDGE_API_KEY=votre-cle-secrete-bridge

# Telegram Copilot
TELEGRAM__BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM__CHAT_ID=12345678

# Calendrier Économique
NEWS__FINNHUB_API_KEY=votre_cle_finnhub
```

---

## 🚀 Démarrage pas-à-pas

### Étape 1 : Démarrer le pont MT5 sur Windows Host
Le pont FastAPI doit tourner sur la machine où le terminal MT5 est actif :
```bat
.\start_bridge.bat
```
Vérifier l'état du pont dans un navigateur ou terminal :
```bash
curl http://localhost:8765/health
# Réponse attendue : {"bridge":"online","mt5_connected":true,...}
```

### Étape 2 : Lancer le moteur

#### Option A : Via Docker Compose (Recommandé en production)
```bash
docker compose up --build -d
# Suivre les logs en temps réel
docker compose logs -f engine
```

#### Option B : En local natif (sans Docker)
```powershell
# Démarrer Redis (ex: redis-server local)
# Puis lancer le copilot live :
python scripts/run_live_copilot.py

# Ou lancer en mode Paper (auto-approbation sans confirmation Telegram) :
python scripts/run_paper.py
```

---

## 📊 Mode Backtest & Monte Carlo

### 1. Exécuter un Backtest historique
Placez un fichier Parquet ou CSV de données historiques dans `data/historical/EURUSD_M15.parquet` (ou `.csv`), contenant les colonnes `timestamp`, `open`, `high`, `low`, `close`, `volume`.

```powershell
python scripts/run_backtest.py --symbol EURUSD --timeframe M15 --from 2024-01-01 --to 2024-06-30 --export results/bt_2024.csv
```

**Sortie console détaillée :**
- Solde initial vs final
- Total P&L net et Win Rate
- Ratio de Sharpe & Profit Factor
- Drawdown Maximum (%)
- Bilan des rejets par porte pré-trade (Gates 1 à 8)

### 2. Simulation de Risque Monte Carlo
Permute les séquences de gains/pertes pour tester la robustesse face aux limites Prop Firm :
```powershell
python scripts/run_monte_carlo.py --trades results/bt_2024.csv --simulations 10000
```
Calcule précisément la probabilité d'atteindre la limite journalière de -5% ou globale de -10%.

---

## 🧪 Suites de Tests (91 tests unitaires)

Les tests s'exécutent de façon entièrement **headless** (sans terminal MT5 requis) grâce à la fixture [`mock_mt5`](file:///c:/Users/LENOVO/Documents/Github/PropSentry/tests/conftest.py).

Lancer tous les tests :
```powershell
python -m pytest tests/ -v --tb=short
```

Résultats :
```text
tests/test_broker_bridge.py     .... [ 8%]  # Test du pont HTTP & dispatch
tests/test_broker_clock.py      ....... [19%]  # Test DST, fuseau Europe/Prague & reset
tests/test_indicators.py        ............... [36%]  # Test ATR, EMA, FVG & Liquidity Sweep
tests/test_kill_switch.py       ........... [49%]  # Test déclenchement -4.20% & reset
tests/test_parity.py            ........ [58%]  # Test stricte parité live/backtest
tests/test_preflight_gates.py   .................. [85%]  # Test unitaire des 8 portes pré-flight
tests/test_reconciliation.py    ...... [92%]  # Test positions fantômes & écart de solde
tests/test_risk_sizing.py       ....... [100%] # Test cap 0.5% (25 USD)

============================= 91 passed in 1.26s =============================
```

---

## 🛡️ Sécurité & Supervision

1. **Heartbeat Interne ([`src/supervisor/heartbeat.py`](file:///c:/Users/LENOVO/Documents/Github/PropSentry/src/supervisor/heartbeat.py)) :**
   Surveille que la boucle événementielle tourne sans blocage. Alerte si 3 battements consécutifs sont manqués.
2. **Dead-man's Switch ([`src/supervisor/deadman_switch.py`](file:///c:/Users/LENOVO/Documents/Github/PropSentry/src/supervisor/deadman_switch.py)) :**
   Envoie un ping HTTP toutes les 5 minutes vers un service externe (Healthchecks.io, UptimeRobot). Si le processus crash, une alerte externe est déclenchée.
3. **Sauvegardes Automatiques ([`src/supervisor/backup_service.py`](file:///c:/Users/LENOVO/Documents/Github/PropSentry/src/supervisor/backup_service.py)) :**
   Copie automatique de la base SQLite `trading.db` vers `data/backups/` après chaque reset journalier à 00:05 CE(S)T, avec purge automatique après 14 jours.
