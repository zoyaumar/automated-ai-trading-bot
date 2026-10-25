# Architectural & Tooling Design Decisions

*24/7 Automated AI Trading Bot — Technical Blueprint & Tool Selection Matrix*

## 1. Executive Summary & Core Philosophy

This document defines the architecture, tooling, risk rules, and rollout plan for a 24/7 automated stock trading bot for a private client. The system uses a **Pragmatic 2-Stage Hybrid Agentic-Deterministic Architecture**: the AI proposes, and deterministic code decides. Phase 1 is **long-only** (no shorting, no margin leverage) on a fixed six-symbol watchlist, evaluated on a schedule (about every 30 minutes during market hours), with positions held for hours to days.

- **Stage 1 (AI Analysis Layer):** Anthropic Claude Sonnet 5.5 (Haiku 5.5 as a cheaper option) is called once per cycle with a structured prompt covering the whole watchlist. It has no tools and no credentials. It returns typed JSON (Pydantic) with action, ticker, confidence, `stop_loss_pct`, `take_profit_pct`, and a short reasoning. It does not choose position size.

- **Stage 2 (Deterministic Risk Layer):** A hard-coded Python validator treats the AI output as untrusted input, sizes the trade, enforces every rule in Section 3, and only then submits a bracket order to Alpaca. Any error, bad output, or missing data means no trade (fail closed).

## 2. Technology Selection Matrix

| **System Module**              | **Selected Tool / Tech**                                     | **Primary Rationale & Benefit**                                                                                                                             | **Cost / Rate Limit**                                |
|--------------------------------|--------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------|
| **Broker Engine**              | Alpaca API (`alpaca-py`)                                       | Commission-free, server-side bracket orders, same API for paper and live                                                                                    | Free (200 REST req/min)                              |
| **Real-Time Data**             | Alpaca WebSockets (IEX feed)                                 | Live quotes, minute bars, and order fill events without polling                                                                                             | Free; 30-symbol limit (we use 6)                     |
| **Price History & Indicators** | Alpaca historical bars + pandas                              | RSI, MACD, and ATR computed in our own code from daily bars. Replaces yfinance, an unofficial scraper that can break                                        | Free; latest 15 min of history excluded on free plan |
| **News**                       | Finnhub API (headlines, earnings calendar)                   | Headlines for sentiment and earnings dates for the skip filter. Open web search (Tavily) is left out of Phase 1 to limit cost and prompt-injection exposure | Free tier (60 req/min)                               |
| **Macro**                      | FRED API (VIX series)                                        | Official, free daily VIX data for market context                                                                                                            | Free                                                 |
| **AI Prompt Engine**           | Claude Sonnet 5.5 + Pydantic                                 | One structured call per cycle returns typed JSON trade proposals                                                                                            | ~\$3–\$10/month, with a monthly spend cap            |
| **Output Schema**              | Pydantic / instructor                                        | Schema-enforced JSON with retries; invalid output is skipped, never guessed                                                                                 | Free open-source library                             |
| **24/7 Hosting**               | Docker on Linux VPS                                          | Reproducible container with auto-restart; SSH keys only, firewall, non-root container, automatic security updates                                           | \$5–\$10/month                                       |
| **Emergency Control**          | Telegram bot (long polling)                                  | No public web endpoint to defend; commands accepted only from the client's chat ID                                                                          | Free                                                 |
| **Monitoring**                 | Dead-man's-switch heartbeat service + daily Telegram summary | Alerts you if the bot itself stops, which Telegram alone can't do                                                                                           | Free tier                                            |
| **Backtesting**                | pandas (vectorbt optional) on Alpaca daily bars              | Replays the live validator code on historical data to test the rules. The AI layer can't be backtested cleanly                                              | Free                                                 |
| **State**                      | SQLite (log and cache only)                                  | Audit log of prompts, proposals, and decisions. Alpaca is the source of truth                                                                               | Free                                                 |

## 3. Pre-Trade Risk Firewall Controls

Every AI proposal must pass all checks below. A single failure rejects the proposal and logs the reason.

- **1. Schema & Whitelist:** Output must match the Pydantic schema. The ticker must be on the watchlist (AAPL, MSFT, NVDA, AMZN, SPY, QQQ). Actions are BUY, CLOSE, or HOLD only. BUY opens a long and CLOSE exits an existing long. No shorting.

- **2. Stop & Target Bounds:** A stop-loss and take-profit are required. Stop distance must be at least 1.5× daily ATR and no more than 8% from entry. Take-profit must be at least 1.5× the stop distance.

- **3. Risk Cap & ATR Sizing:** Shares = (2% of equity) ÷ stop distance. Position value is then capped at 15% of equity, which is usually the binding limit.

- **4. Portfolio Limits:** Max 5 open positions. Total open risk (entry-to-stop distance × shares, summed across positions) must stay under 4% of equity.

- **5. 15% Cash Floor:** Rejects any trade that would leave less than 15% of equity in cash.

- **6. Intraday Margin & Buying Power:** Reads Alpaca's account object before each order and rejects anything the account can't support. No margin leverage: total long exposure stays within equity. FINRA retired the PDT rule and its \$25,000 minimum on June 4, 2026, and Alpaca has removed it from its platform, so there is no day-trade counter. Alpaca replaced day-trade buying power with intraday buying power, so check field names against current docs in the sandbox. An optional daily trade cap can be configured.

- **7. Timing & Data Guards:** Uses Alpaca's market clock. No new entries in the first 15 minutes after the open or the last 30 minutes before the close. Skip any ticker with earnings within 1 trading day. Reject everything if the latest quote or bar is older than 2 minutes or the WebSocket is down.

- **8. Drawdown Kill Switch:** Compares equity to the high-water mark. A 5% daily loss halts new entries for the day. A 15% drawdown from peak suspends trading until the client reviews. If the local database is lost, the peak is rebuilt from Alpaca's portfolio history rather than reset.

## 4. Order Handling & Resilience

- **Bracket Orders, Good-Till-Cancelled:** Entries are limit orders priced near the ask to cap slippage, submitted as brackets with time-in-force GTC so the stop and target survive overnight. Because a GTC entry could fill days later, the bot cancels any entry still unfilled after 10 minutes.

- **Unique Order IDs:** Every proposal gets a UUID `client_order_id`. Alpaca rejects duplicate IDs, so retries and reconnects can't double-order.

- **Alpaca Is the Source of Truth:** On startup and every cycle, the bot reconciles positions, open orders, and equity against Alpaca. SQLite is a log and cache only.

- **Protection Check:** Every cycle, each open position must have a live stop order. If one is missing, the bot re-submits it. If that fails, it closes the position and alerts the client.

- **Fail Closed:** A Claude API error, invalid JSON, validator exception, stale data, or WebSocket outage means no new trades. Existing stops stay on Alpaca's servers.

- **Reconnects & Calendar:** WebSocket reconnects use backoff. Holidays and half-days come from Alpaca's calendar.

## 5. AI Layer & Prompt-Injection Defense

Headlines are text the system doesn't control, so the design assumes the AI can be fooled and limits what a fooled AI can do.

- **No Tools, No Credentials:** The Claude call can't place orders or read account data. Its only output is a typed JSON object.

- **Code Decides:** Position size, stop bounds, the watchlist, and every limit are enforced by the validator, not requested from the AI. The AI can't remove a stop, pick a size, or add a symbol.

- **Untrusted Data Block:** Headlines go inside a clearly delimited data section, and the system prompt says to treat them as data only. This helps but doesn't prevent injection, so the validator is the real defense.

- **Small Input Surface:** Finnhub headlines only, with no open web search in Phase 1. Reasoning text is shown in Telegram truncated and escaped, and never executed.

- **Audit & Cost Controls:** Every prompt and response is logged. A daily call cap and a monthly spend cap prevent runaway cost. The week-1 test set includes proposals with injected instructions and oversized or missing stops, to confirm the validator rejects them.

## 6. Infrastructure & Monitoring

- **Hosting:** Docker container on a Linux VPS with `restart: always`, supervised by systemd.

- **Security Basics:** SSH key login only, firewall, automatic security updates, and a non-root container. API keys live in a `.env` file with locked permissions, never in the image or the code repository. Alpaca keys are trading-only.

- **Telegram:** Long polling, so there is no public endpoint to defend. Commands are ignored unless they come from the client's chat ID. `/STOP` stops new trades and cancels unfilled entries but keeps live stops. `/FLATTEN` closes all positions and cancels all orders.

- **Heartbeat:** The bot pings a dead-man's-switch service every 5 minutes during market hours and sends a Telegram summary each day after the close. No ping for 15 minutes triggers an email or SMS alert to the developer and client.

- **Data Notes:** Free real-time data is IEX only (a slice of market volume), which is fine for liquid names. Orders still execute on the real market. Decisions use daily bars plus the live stream. A paid full-market data plan exists (listed at \$99/month) but isn't needed for Phase 1.

## 7. Validation & Go-Live Plan

The AI layer is not backtested: models tend to have memorized how past markets moved, so a test on historical dates looks better than reality, and no edge is claimed. Validation is layered instead. A backtest checks the rules. Paper trading proves the plumbing, because Alpaca's simulator matches orders against the current best quote and doesn't model market impact, latency slippage, or dividends. Small real-money trading then measures real fills.

- **Failure Drills (Week 4):** Kill the server mid-position, cut the network, drop the WebSocket, return malformed AI output, feed injected headlines, delete the SQLite file, submit a duplicate order, and run `/STOP` and `/FLATTEN`. All must pass before the paper trial starts.

- **Rules Backtest & Benchmarks (Weeks 5–6, built while the paper trial runs):** Call the same validator and sizing functions the live bot uses (no duplicate logic) on Alpaca daily bars for the six symbols, with a simple stand-in signal such as a 50/200-day trend rule, since the AI isn't available historically. Entries fill at the next open with 5 bps assumed slippage. Stops and targets are checked against daily highs and lows, gaps fill at the open, and if both are hit in one bar the stop is assumed first. Run the full history plus stress windows (March 2020, 2022, August 2024). Report max daily loss against the 5% limit, circuit-breaker trips, max drawdown, exposure, whether the 4% open-risk cap held, and how often a gap went through a stop. Compare with buy-and-hold SPY. Any rule change it triggers is re-tested on paper before go-live. Daily bars can't show the order of intraday moves, so this tests the rules, not the AI.

- **AI Proposal Log:** Every AI proposal is logged, traded or not, and scored forward over 1, 5, and 10 days against buying SPY. The go/no-go review includes this comparison as a reference, not a pass check, since a few weeks of data is too noisy to judge.

- **Stage 1, Paper (4 weeks):** Pass checks: zero orders that break a rule (audited from logs); every position had a live stop within 1 minute of its fill; zero unresolved reconciliation mismatches; bot up for at least 99% of market hours with every restart self-recovered; heartbeat and daily summary delivered every trading day; AI cost within budget; `/STOP` and `/FLATTEN` working. Profit is not a pass check.

- **Stage 2, Live at 25% (minimum 4 weeks):** Compare each live fill with the price at proposal time, and compare behavior with paper.

- **Stage 3, Scale:** 50% for at least 2 weeks, then 100%. Each step needs a clean review and the client's approval.

- **Pause Rule:** Any rule violation, unprotected position, or unexplained reconciliation mismatch in live trading pauses the bot until it is fixed.

## 8. Client Decision Checklist (Final Parameters)

- **Account Size & Margin:** Confirmed account balance under \$25k. The PDT rule was retired on June 4, 2026, so the bot checks Alpaca intraday margin and buying power before every order instead of counting day trades.

- **Autonomy Level:** Confirmed 100% autonomous cloud execution without manual trade approval gates.

- **Data Feed Tier:** Free IEX real-time stream from Alpaca, Alpaca historical bars, Finnhub, and FRED.

- **Approved Watchlist:** AAPL, MSFT, NVDA, AMZN, SPY, QQQ. Long-only.

- **Go-Live Capital:** Client confirms the total capital to commit. Stage 2 uses 25% of it.

- **Emergency Commands:** `/STOP` and `/FLATTEN` as described in Section 6.

## 9. Known Limits

The system may lose money, lag the market, or sit in cash for long stretches. Stop-loss orders can fill at worse prices on overnight gaps and in fast markets, and conditional orders that trigger near the close may not execute that day. The earnings filter reduces gap risk but doesn't remove it. Free IEX data is a partial view of the market. Paper results do not predict live results.

## Folder structure
trading-bot/
├── .env                    # secrets
├── .env.example            # same keys, no values
├── .gitignore              # .env, data/, .venv/, __pycache__/
├── config.yaml             # all percentages, limits, toggles
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── README.md               # setup + runbook
│
├── bot/
│   ├── main.py             # the loop, nothing else (python -m bot.main)
│   ├── config.py           # loads + validates config.yaml and .env
│   ├── broker.py           # Alpaca: account, orders, clock, reconcile
│   ├── data.py             # bars, indicators, WebSocket, FRED, earnings
│   ├── news.py             # Finnhub headlines (toggle in config)
│   ├── prompt.py           # builds prompt, parses + validates reply
│   ├── risk.py             # pure functions, no network, no DB
│   ├── notify.py           # Telegram + heartbeat
│   ├── store.py            # SQLite log
│   └── llm/
│       ├── __init__.py     # get_llm(config) factory
│       ├── base.py         # interface: propose(system, user)
│       └── openai_compat.py
│
├── scripts/
│   ├── backtest.py         # reuses bot/risk.py
│   ├── audit.py            # pass-check audit from logs
│   ├── score_proposals.py  # AI proposals vs SPY at 1/5/10 days
│   └── reset_drawdown.py   # clears a drawdown halt after client review
│
├── tests/
│   ├── test_risk.py
│   ├── test_config.py
│   ├── test_prompt.py
│   ├── test_indicators.py
│   ├── test_injection.py
│   └── fixtures/
│       └── injected_headlines.json
│
└── data/                   # Docker volume, not committed
    ├── bot.db              # SQLite
    └── history/            # cached daily bars for the backtest