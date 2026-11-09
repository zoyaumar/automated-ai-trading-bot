# Q&A — Automated AI Trading Bot
# Quick reference for the project structure, key files, and how it works.

## Project Overview

**What is this?**
A long-only, paper-trading AI trading bot. The AI (LLM) proposes trades;
deterministic code decides. The risk firewall validates, sizes, and approves or
rejects every proposal before any order is placed with Alpaca.

**Architecture in one paragraph**
`main.py` runs a loop: reconcile → protect → fetch → LLM → validate → execute
## Quick Start

**How do I run the bot?**
```powershell
# Windows
python -m bot.main
# or with the console script
python -m scripts.backtest  # for backtests
```

**What does config.yaml contain?**
- `mode: paper | live` — paper for practice, live for real money
- `watchlist` — tickers to monitor
- `risk.*` — per-trade risk % / position cap / stop / reward-to-risk rules
- `timing.*` — market hours, data age, earnings gap
- `news.*` — headline toggles
- `llm.*` — provider config, rate limits, cost caps
### `bot/indicators.py`
Pure RSI, MACD, ATR. No network, no broker dependency.

### `bot/store.py`
SQLite (SQLAlchemy) log and cache. Tables: `cycles`, `prompts`, `responses`,
`proposals`, `decisions`, `orders`, `equity_snapshots`, `alerts`.

### `bot/main.py`
The trading loop. `__init__` sets up the broker, data client, WebSocket,
LLM, news, notify. `_run_cycle()` executes one cycle. `run()` drives the loop.

### `bot/notify.py` — Telegram + heartbeat
- `TelegramClient.send_message()`, `send_trade_notification()`, `send_alert()`
- `poll_commands()` for `/stop`, `/flatten`, etc.
- `ping_heartbeat()`, `heartbeat_stale()` — dead-man's-switch

### `bot/prompt.py`
Prompt construction and JSON parsing. `ProposalSchema` (Pydantic) and
`parse_proposals()` validate LLM output.

### `bot/news.py` — Finnhub news
- `NewsClient.get_headlines()`, `get_earnings_within_days()`
- Headlines are treated as untrusted data; earnings filter is toggleable

### `bot/llm/` — LLM provider layer
### `bot/news.py` — Finnhub news
- `NewsClient.get_headlines()`, `get_earnings_within_days()`
- Headlines are treated as untrusted data; earnings filter is toggleable

### `bot/llm/` — LLM provider layer
- `base.py`: `LLMInterface`, `LLMResult`
- `openai_compat.py`: `OpenAICompatLLM` — the ADK-compatible OpenAI client.
  Supports retry/backoff, new `max_calls_per_day`, `input_cost_per_mtok`,
  `output_cost_per_mtok`, `monthly_budget_usd`.
- `__init__.py`: `get_llm()` factory

---

## Supporting Files (`scripts/`, `tests/`, `docs/`)

### `scripts/` — Operational scripts
- `backtest.py` — Parameter sweep, naive rules backtest
- `reset_drawdown.py` — Reset HWM in SQLite
- `score_proposals.py` — Score AI proposals
- `smoke_test_week1.py`, `smoke_test_week2.py` — AI-assisted e2e smoke tests
- `audit.py`, `test_alpaca_connection.py` — Audit & connectivity tests
- `reset_drawdown.py` — Reset daily HWM

### `tests/` — Unit & integration tests
- `test_config.py`, `test_indicators.py`, `test_risk.py`, `test_prompt.py`,
  `test_injection.py`, `conftest.py`
- `fixtures/` — Sample test data (e.g., injected headlines)

### `docs/` — Process documentation
- `trading_bot_checklist.md` — Week-by-week completion tracker
- `code_guidelines.md` — Error handling, docstrings, config validation rules
- `trading_bot_design_decisions.md` — Architectural decisions
- `trading_bot_timeline.md` — Day-by-day plan
## Key Environment Files

### `.env`
Real secrets. Not committed to git. Contains:
- `ALPACA_PAPER_KEY`, `ALPACA_PAPER_SECRET`
- `NVIDIA_API_KEY` (or any OpenAI-compatible key)
- `FINNHUB_API_KEY`, `FRED_API_KEY`
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- `HEARTBEAT_URL` — dead-man's-switch ping URL

### `.env.example`
Template with the same variable names but no values. Commit this.

### `.gitignore`
Covers `.env`, `data/bot.db`, `__pycache__/`, `.pytest_cache/`.

### `config.yaml`
Runtime configuration (not secrets). `mode`, `watchlist`, `risk.*`,
`timing.*`, `news.*`, `llm.*` (provider, model, rate limits, budget),
`monitoring.*`.

---

## Answer: "How do the pieces fit together?"

```
main.py ──► Broker ──► Alpaca (trade)
        ├► DataClient ──► Alpaca (data)
        ├► WebSocketStream ──► Market quotes
        ├► NewsClient ──► Finnhub headlines
        ├► Store ───► SQLite audit log
        ├► notify.py ──► Telegram + heartbeat
        └─► LLM (openai_compat) ──► Groq / NVIDIA / others
                ↓
        Prompt ├─► proposals ──► risk.py validate()
                                   ↓
                          approved? ──yes──► Broker submit_bracket_order()
                                      no ──► store reject reason
```

**Cycles:**
1. `reconcile()` — check broker state vs. Alpaca (detect drift, stale state)
2. `protect()` — risk firewall (`risk.py`) runs on all proposals
3. `fetch()` — get indicators, VIX, news, quotes
4. `LLM proxy` — ask AI for proposals
5. `validate()` — `risk.py` checks schema, whitelist, timing, sizing,
   portfolio limits, cash, drawdown
6. `execute()` — approved proposals → `broker.submit_bracket_order()`
7. `log()` — all decisions, orders, and results go to SQLite

---

## Answer: "What does the daily call count and cost cap do?"

- `max_calls_per_day: 50` — blocks AI calls after 50/day (configurable)
- `monthly_budget_usd: 0` — 0 = no dollar cap
- `input_cost_per_mtok: 0`, `output_cost_per_mtok: 0` — per-million prices
- The LLM client tracks `total_cost_usd` and blocks when the budget is
  exceeded (e.g., `10000 / 1000000 * 0.002` = $0.02 per 1M tokens for
  NVIDIA's free tier = $0).

---

## Answer: "How does the risk firewall work?"

`risk.py::validate()` is the single gate. It runs checks in order and stops
at the first failure:

1. Action must be BUY/CLOSE/HOLD (schema)
2. Ticker must be on watchlist
3. Stop distance >= `min_stop_atr_multiple` x ATR (min stop)
4. Stop distance <= `max_stop_pct` of entry (max stop)
5. Take profit >= `min_reward_to_risk` x stop distance (min R:R)
6. Shares = `floor((equity * max_risk_per_trade_pct / 100) / stop_distance)`
7. Shares <= `floor(max_position_pct * equity / entry)` (position cap)
8. Shares > 0 (else reject)
9. Portfolio: not already long, max open positions
10. Cash: `shares * entry < equity * min_cash_pct` (cash floor)
11. Buying power: `shares * entry <= buying_power` (Alpaca limit)
12. Timing: market open, data fresh, no earnings tomorrow
13. Drawdown: HWM - equity < `max_drawdown_pct`

If a check fails, `validate()` returns `Decision(approved=False)`.

---

## Answer: "What does the risk firewall NOT check?"

- **Buying power vs equity**: sizing counts against equity, not buying power.
  A position that fits in buying power but breaks the equity cap is rejected.
- **Open orders**: existing bracket orders are not considered (only positions).
- **Fees/commissions**: not modeled.
- **Slippage**: not modeled.
- **Strategy**: only long-only, no shorting or options.

---

## Answer: "How does the config file validate?"

`config.py` uses Pydantic. `BotConfig` enforces:
- `mode` must be `paper` or `live`
- Watchlist must not be empty
- `risk.max_position_pct + risk.min_cash_pct <= 100`
- `risk.max_risk_per_trade_pct <= risk.max_total_open_risk_pct`
- Range checks on all percent fields (0 to 100)

---

## Answer: "What does the SQLite log contain?"

Tables: `cycles`, `prompts`, `responses`, `proposals`, `decisions`, `orders`,
`equity_snapshots`, `alerts`. Every cycle writes:
- cycle metadata
- AI prompt and response (with token counts & cost)
- parsed proposals
- validator decisions (approved/rejected with reasons)
- order submissions and fills
- daily equity snapshots for drawdown tracking

---

## Answer: "How do I add a new watchlist symbol?"

1. Add it to `watchlist` in `config.yaml`
2. Restart the bot (config reloads automatically)
3. Add a paper account to `data.py` if it doesn't exist yet

---

## Answer: "How do I pause trading?"

- Send `/STOP` via Telegram — new entries stop, existing stops stay on.
- Send `/FLATTEN` — closes all positions and cancels open orders.
- Edit `config.yaml` and set `mode: live` → run on live account.

---

## Quick command reference

```powershell
# Run the bot (paper)
python -m scripts.backtest                    # backtest only
python -m bot.main                            # run bot (paper mode)

# Run tests
python -m pytest tests/ -q

# Audit or reset drawdown
python -m scripts.audit
python -m scripts.reset_drawdown

# View config
python -c "from bot.config import Settings; print(Settings())"
```
- `completion_log.md` — Historical milestones

- `base.py`: `LLMInterface`, `LLMResult`
- `openai_compat.py`: `OpenAICompatLLM` — the ADK-compatible OpenAI client.
  Supports retry/backoff, new `max_calls_per_day`, `input_cost_per_mtok`,
  `output_cost_per_mtok`, `monthly_budget_usd`.
- `__init__.py`: `get_llm()` factory

- `monitoring.*` — heartbeat/Telegram settings

**Where are the API keys?**
In `.env` (never commit this file; it's already in `.gitignore`).

---

## Bot Packages (`bot/`)

### `bot/__init__.py`
Package marker. Nothing to import here.

### `bot/broker.py` — Alpaca connection
- `Broker` class: `get_account()`, `get_clock()`, `get_market_calendar()`,
  `is_market_open()`, `submit_bracket_order()`, `submit_market_order()`,
  `reconcile()`, `cancel_order()`, `close_position()`, etc.
- Uses `alpaca-py` (0.44.x) — `get_positions()` not `get_all_positions()`.
- `BrokerAccount` dataclass: equity, cash, buying_power, positions, open_orders,
  daily_start_equity, high_water_mark.

### `bot/risk.py` — Risk firewall
Pure functions. `validate()` runs multiple checks in order:
- `check_schema_and_whitelist()`
- `check_stop_and_target_bounds()`
- `size_position()` (shares = floor((equity × risk%) / stop distance))
- `check_portfolio_limits()`
- `check_cash_floor()`
- `check_buying_power()`
- `check_timing_and_data()`
- `check_drawdown()`
Only the first failure is returned; the rest of the pipeline stops.
- `Proposal` and `Decision` dataclasses.

### `bot/config.py` — Configuration loading
- `BotConfig` Pydantic model with field validators.
- Cross-checks: `max_position_pct + min_cash_pct <= 100`,
  `max_risk_per_trade_pct <= max_total_open_risk_pct`.
- `Settings` class: loads YAML, validates, computes a SHA-256 config hash.

### `bot/data.py` — Market data
- `DataClient.get_daily_bars()`, `get_indicators()`
- `WebSocketStream` for last-quote tracking
- `FredClient.get_vix()` — FRED VIX series
- `get_vix_level()` — convenience helper

→ log. The LLM proposes trades; `bot/risk.py` validates and sizes them;
`bot/broker.py` submits orders to Alpaca. `bot/store.py` logs everything to
SQLite. `bot/notify.py` sends Telegram alerts and pings a heartbeat URL.
