# Project Timeline & Milestones

*24/7 Automated AI Trading Bot — Condensed 8-Week Phase 1 Development Roadmap*

## 1. Timeline Overview & Phase Strategy

This document defines the **8-Week Phase 1 Development Roadmap**. Weeks 1–4 build and test the system. Weeks 5–8 run a mandatory **4-week (28-day) paper-trading trial**. The end of week 8 is a go/no-go review before a **small-money live launch**, and scaling up follows in steps. Go-live is decided by pass criteria, not by the calendar: if the build runs late, the paper trial starts later and the live date moves. The trial is never shortened.

To keep the four-week build realistic, Phase 1 leaves out options, shorting, rebalancing, and open web search. The rules backtest is built during weeks 5–6, while the paper trial runs on its own.

| **Week**        | **Target Focus**               | **Key Deliverable Component**                                                                        | **Target Status** |
|-----------------|--------------------------------|------------------------------------------------------------------------------------------------------|-------------------|
| **Week 1**      | Core Architecture & Safety     | Alpaca connection, risk validator (all checks) with unit tests, order reconciliation                 | Pending           |
| **Week 2**      | Data Pipelines & AI Engine     | WebSockets, Alpaca bars and indicators, FRED, Finnhub, Claude structured prompt with injection tests | Pending           |
| **Week 3**      | Cloud Hosting & Remote Control | Docker, VPS hardening, Telegram (`/STOP`, `/FLATTEN`), heartbeat monitoring                              | Pending           |
| **Week 4**      | Integration & Failure Drills   | End-to-end loop with every failure drill passed                                                      | Pending           |
| **Weeks 5–8**   | 28-Day Paper Trial             | Uninterrupted paper run against the pass checks, plus rules backtest and SPY comparison              | Pending           |
| **End of Wk 8** | Go/No-Go & Live Stage 1        | Review of paper results, backtest, and SPY comparison, then live launch at 25% of committed capital  | Target Launch     |
| **Week 9+**     | Live Scale-Up                  | 25% (4+ weeks), then 50% (2+ weeks), then 100%, each with client approval                            | Pending           |

## 2. Detailed Milestone Breakdowns

### Week 1: Core Architecture & Deterministic Risk Firewall

- **Milestone 1: Environment & API Gateway Setup (Days 1–3):** Initialize the Python 3.12 workspace, settings module, and `.env` secret handling. Connect to the Alpaca Paper Trading API using `alpaca-py`. Set up SQLite as a log and cache, and wire in Alpaca's market clock and calendar. Check the account object's current buying-power field names in the sandbox.

- **Milestone 2: Pre-Trade Risk Engine & Reconciliation (Days 4–7):** Build the validator: schema and watchlist check, stop and target bounds, 2% risk with ATR sizing and the 15% position cap, 5-position and 4% total-risk limits, 15% cash floor, intraday margin check, timing and data guards, and the 5% / 15% drawdown switch. Add reconciliation against Alpaca and the unprotected-position check. Write unit tests for every rule, including malformed output, off-watchlist tickers, missing stops, oversized orders, and injected-instruction proposals.

### Week 2: Data Pipelines & Claude Single-Prompt AI Engine

- **Milestone 3: Data Ingestion & Streaming (Days 8–10):** Set up Alpaca WebSockets for the six watchlist symbols. Pull daily bars from Alpaca and compute RSI, MACD, and ATR with pandas. Add FRED for VIX, and Finnhub for headlines and the earnings calendar. Add the stale-data guard.

- **Milestone 4: Claude Prompt & Pydantic Schemas (Days 11–14):** Build the single structured prompt on Claude Sonnet 5.5 (Haiku 5.5 as an option) with Pydantic schemas. Output: action, ticker, confidence, `stop_loss_pct`, `take_profit_pct`, reasoning. Position size is not an AI output. Headlines go in a delimited untrusted block. Add the daily call cap and monthly spend cap, and test with injected headlines.

### Week 3: Cloud Infrastructure, Docker & Remote Control

- **Milestone 5: Telegram & Monitoring (Days 15–17):** Build the Telegram bot with long polling, accepting commands only from the client's chat ID. Add trade notifications, a daily summary, `/STOP` (halts new trading, cancels unfilled entries, keeps stop-losses live), and `/FLATTEN` (closes all positions and cancels all orders). Add the heartbeat ping and the no-ping alert.

- **Milestone 6: 24/7 Cloud Docker Deployment (Days 18–21):** Create the `Dockerfile` and `docker-compose.yml` with a persistent volume. Provision a VPS (about \$5–\$10/month) with SSH-key login, firewall, automatic security updates, and a non-root container. Configure `restart: always` and reconcile-on-boot.

### Week 4: End-to-End System Integration & Failure Drills

- **Milestone 7: Integration & Failure Drills (Days 22–28):** Connect WebSockets, the Claude pipeline, the risk engine, and Alpaca execution in `main.py`. Run the drills: kill the server mid-position, cut the network, drop the WebSocket, return malformed AI output, feed injected headlines, delete the SQLite file, submit a duplicate order, and run `/STOP` and `/FLATTEN`. The paper trial starts only when every drill passes.

### Weeks 5–8: 4-Week Autonomous Paper Trading Trial

- **Milestone 8: 28-Day Uninterrupted Paper Trading (Days 29–56):** Run the bot 24/7 in Alpaca Paper Trading mode and review weekly. Pass checks: zero rule-breaking orders; every position protected by a live stop within 1 minute of its fill; no unresolved reconciliation mismatches; at least 99% uptime during market hours with every restart self-recovered; heartbeat and daily summary delivered every trading day; AI cost within budget; `/STOP` and `/FLATTEN` working. Paper profit is not a pass check, because paper trading doesn't model slippage, market impact, or dividends.

- **Milestone 9: Rules Backtest & Benchmarks (Days 29–42, in parallel with the paper trial):** The bot runs on its own during the paper trial, so this is built alongside it. Call the live validator and sizing functions on Alpaca daily bars for the six symbols, with a simple 50/200-day trend rule standing in for the AI. Use 5 bps assumed slippage, stops and targets checked on daily highs and lows, gaps filled at the open, and the stop assumed first if both are hit in one bar. Run the full history plus March 2020, 2022, and August 2024. Report max daily loss against the 5% limit, circuit-breaker trips, max drawdown, and gap-through-stop counts, and compare with buy-and-hold SPY. Also score every logged AI proposal forward against SPY. Any rule change it triggers is re-tested on paper before go-live.

### End of Week 8: Go/No-Go & Live Rollout

- **Milestone 10: Go/No-Go Review & Live Stage 1 (End of Week 8):** Review the paper trial against the pass checks, plus the rules backtest and the SPY comparison, with the client. If everything passes, switch the Alpaca keys from paper to live and start at 25% of the capital the client chooses to commit, for at least 4 weeks. Compare live fills with the price at proposal time.

- **Milestone 11: Scale-Up (Week 9 onward):** Move to 50% for at least 2 weeks, then 100%. Each step needs a clean review and the client's approval. Any rule violation, unprotected position, or unexplained mismatch pauses the bot until it is fixed.

- **Milestone 12: Phase 2 Options Architecture (After live scale-up is stable):** Design options chain streaming and Cash-Secured Put / Covered Call logic. This starts only once Phase 1 has run stably at full size.
