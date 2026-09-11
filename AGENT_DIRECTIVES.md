# AGENT DIRECTIVES & INVARIANTS — QUANTITATIVE TRADING ENGINE

## 1. MISSION & CONTEXT
You are an expert Quantitative Developer and Systems Architect.
Your task is to implement a modular, event-driven semi-automated trading system designed to pass and sustain a 5,000 USD Prop Firm evaluation (FTMO / Goat Funded Trader rules).

---

## 2. NON-NEGOTIABLE CORE INVARIANTS
Any code violation of these rules must cause immediate build/test failure:

- **INVARIANT 1 (Risk Determinism):** Entry, Stop Loss, Take Profit, and lot sizing are 100% rule-based and deterministic. The LLM must NEVER output execution prices or lot sizes.
- **INVARIANT 2 (Sizing Cap):** Risk per trade is strictly capped at 0.50% of the initial balance (25.00 USD max theoretical risk).
- **INVARIANT 3 (Continuous Equity Monitoring):** Daily drawdown (-5% max) is calculated tick-by-tick against *floating equity* (balance + open PnL - commissions ± swap), NOT closed balance.
- **INVARIANT 4 (Hard Kill-Switch):** If daily loss hits -4.20% (210.00 USD), all pending orders are cancelled, all open positions are flattened immediately at market, and trading is disabled until the next daily reset.
- **INVARIANT 5 (Broker Time Synchronization):** Daily reset occurs strictly at 00:00 CE(S)T (Europe/Prague). Account for daylight saving time (CET/CEST) dynamically; never hardcode a static UTC offset.
- **INVARIANT 6 (MT5 Single Process Owner):** Only `src/broker/mt5_client.py` is allowed to import and communicate with `MetaTrader5`. Any other module importing `MetaTrader5` violates architecture isolation.
- **INVARIANT 7 (Decoupled LLM):** The LLM operates off-critical path as an asynchronous context filter. It writes a `MarketContextState` JSON into Redis with a 15-minute TTL. If Redis data is stale, missing, or has `trading_allowed: false`, execution gates block all new entries.
- **INVARIANT 8 (Parity Principle):** Backtest and live execution MUST use the exact same calculation logic via `src/engine/runner.py`.

---

## 3. CODING STANDARDS
- **Language:** Python 3.11+.
- **Typing:** Strict static typing with Python `typing` annotations and Pydantic v2 schemas.
- **Testing:** 
  - All broker interactions in unit tests must use the `mock_mt5` fixture (stubbing `sys.modules["MetaTrader5"]`).
  - Unit tests must be runnable in headless environments (Linux CI/Docker) without an active Windows MT5 terminal.
- **Persistence:** SQLite for local operations with idempotent migrations; append-only audit logging.

---

## 4. IMPLEMENTATION ROADMAP (TASK EXECUTION ORDER)
1. **Phase 1 (Core & Config):** Loaders, broker clock, Pydantic schemas, and immutable prop firm rules.
2. **Phase 2 (Risk Firewall & Pre-flight):** Position sizer, equity watchdog, and the 8 pre-flight verification gates.
3. **Phase 3 (Strategy Engine):** Pure mathematical functions (FVG, Liquidity Sweep, ATR) and unified `runner.py`.
4. **Phase 4 (Broker Gateway & Mocking):** `mt5_client.py` and unit test harness with `mock_mt5`.
5. **Phase 5 (Copilot & Asynchronous Agent):** Telegram bot interactive handlers and LLM context worker.
