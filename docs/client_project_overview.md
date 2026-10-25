# 24/7 Automated AI Trading Bot

*Client Project Summary & Strategic Overview*

## Executive Summary

This document gives a concise, non-technical overview of the 24/7 Automated AI Trading Bot being built for your account. The goal is a hands-off trading system that runs continuously on secure cloud infrastructure, so no computer of yours has to stay on.

The bot checks a short list of large, liquid stocks and ETFs on a schedule during market hours and holds positions for hours to days. It only buys; it never shorts or borrows to trade. An AI suggests trades, and a set of fixed safety rules decides whether each one is allowed. It will not make money every week, and sitting in cash is a normal result when nothing looks good.

## Key Strategic Decisions Made

### 1. Two-Stage Safety Architecture (AI Analysis + Python Risk Firewall)

**The AI's Role:** Anthropic's Claude (or a free alternative LLM) acts only as a research analyst. It reads news headlines and price signals and proposes trade ideas in a fixed format. It picks the stock, the stop-loss, and the profit target. It never picks the trade size.

**The Risk Firewall:** The AI cannot place trades or see your brokerage login. Every idea goes through a strict, hard-coded Python validator that sizes the trade and checks it against every safety rule. If anything fails, or anything unexpected happens, the trade is rejected and the bot does nothing.

**Protection Against Bad Inputs:** News text is treated as untrusted. Even if a headline tried to trick the AI, the validator's limits still apply, so the worst case is a rejected or small trade.

### 2. Phased Rollout (Stocks First, Options Later)

**Phase 1 (Active Focus):** Long-only trading in six approved symbols: Apple, Microsoft, Nvidia, Amazon, SPY, and QQQ. Any other symbol is rejected automatically.

**Phase 2 (Future Extension):** Once Phase 1 is proven stable in live execution, options income strategies (such as Cash-Secured Puts and Covered Calls) will be introduced.

### 3. Condensed 8-Week Development Timeline

**Weeks 1–4 (Build & Integration):** Risk firewall, data feeds, AI integration, cloud server, Telegram controls, and deliberate failure tests (server crash, network drop, bad AI output).

**Weeks 5–8 (4-Week Paper Trading Trial):** A mandatory 28-day simulation using live market data and virtual funds. It checks that the system runs reliably and follows its rules. It cannot predict profits, because paper trading doesn't model real-world slippage. While it runs, we also replay the safety rules on years of historical price data, including market crashes, and compare the results with simply holding SPY.

### 4. Staged Go-Live (Small Money First)

**Stage 1, Paper Trial:** Must pass the checks below before any real money is used.

**Stage 2, Small Live:** Real money at 25% of the capital you choose to commit, for at least 4 weeks. We compare real fills with paper results.

**Stage 3, Scale Up:** 50% for at least 2 weeks, then 100%. Each step needs a clean review and your OK.

**Paper-Trial Pass Checks:** No rule violations, every position protected by a stop-loss, the bot's records match Alpaca's, it stays up through market hours and restarts itself after failures, alerts arrive every trading day, and the emergency commands work. Paper profit is not a pass check.

**Benchmark Check:** At each review we compare results with simply holding SPY, so you can see whether the AI is adding anything. It is a reference point, not a pass check, because a few weeks of results are too noisy to judge.

**Pause Rule:** Any rule violation or unprotected position during live trading pauses the bot until it's fixed.

### 5. 100% Autonomous 24/7 Cloud Execution

The bot runs on its own inside a Docker container on a secure cloud server (Linux VPS, about $5–$10/month). It restarts itself after a reboot or network drop and, on every restart, re-checks your Alpaca account so it never relies on stale memory. A separate monitoring service messages you if the bot stops checking in.

### 6. Built-In Account Protection Rules

**Max 2% Risk per Trade:** If a trade's stop-loss triggers, the loss is capped at 2% of your account value.

**Max Position Size (15%):** No single stock can be more than 15% of your account, even when its stop-loss is very tight.

**Max 5 Open Positions, 4% Total Open Risk:** Limits how much can be lost if several stops trigger at once.

**15% Uninvested Cash Reserve:** At least 15% of your portfolio stays in cash at all times.

**Volatility-Adjusted Sizing:** Uses ATR to buy fewer shares of volatile stocks and more of stable ones, keeping dollar risk consistent.

**Stop-Loss Required:** Every trade needs a stop-loss that is not too tight (at least 1.5 times the stock's normal daily range) and not too wide (no more than 8%), plus a profit target.

**Timing and Event Filters:** No new trades in the first 15 minutes after the open or the last 30 before the close, none on a stock reporting earnings within a day, and none if price data looks stale.

**Intraday Margin Check:** Before every order, the bot checks Alpaca's real-time buying power and intraday margin. FINRA retired the old $25,000 pattern day trader rule on June 4, 2026, so the bot no longer counts day trades. An optional daily trade cap is available.

### 7. System Drawdown Limits & Emergency Controls

**Daily Loss Limit (5%):** If losses reach 5% in a trading day, new entries stop for the rest of the day.

**Maximum Drawdown (15%):** If the account falls 15% from its peak, all trading is suspended pending your review.

**Telegram `/STOP`:** Immediately stops new trading and cancels entry orders that haven't filled. Stop-losses on positions you hold stay active.

**Telegram `/FLATTEN`:** Closes all open positions and cancels all orders if you want out completely.

**Locked to You:** Only your Telegram account can send commands.

### 8. Fail-Safe Server-Side Orders (Bracket Orders)

Every order includes a linked stop-loss and profit target stored on Alpaca's servers, so your stop-loss stays active even if the cloud server or internet connection drops. They are set to stay active across days. Every cycle, the bot also checks that each position still has its stop. If one is missing, it replaces it, or closes the position and alerts you.

### 9. Low Operating Costs

Free data (Alpaca real-time feed, Alpaca price history, Finnhub news, FRED) keeps costs down. Estimated total: about $10–$20 per month (server about $5–$10, AI usage a few dollars, with a monthly spending cap on the AI).

## Known Limits

**The AI Can't Be Backtested:** The AI reads current news, and AI models tend to have memorized how past markets moved, so a test on old data would look better than reality. The safety rules are plain code, so they are backtested on years of data instead. Starting with small real money is the real test of the AI.

**Paper Trading Is Limited:** It doesn't model slippage, market impact, or dividends, and real fills are usually a little worse.

**Results:** The bot may lose money, trail the market, or sit in cash for long periods. Stop-losses can fill at worse prices when a stock gaps overnight or the market moves fast.

## Summary of Project Parameters

The following matrix summarizes the finalized parameters, settings, and rationales for Phase 1:

| **Parameter**          | **Decision / Setting**                               | **Client Rationale**                                     |
|------------------------|------------------------------------------------------|----------------------------------------------------------|
| **Primary Focus**      | Long-only US stocks & ETFs (Phase 1)                 | Keeps risk simple before adding options                  |
| **Approved Watchlist** | AAPL, MSFT, NVDA, AMZN, SPY, QQQ                     | Large, liquid names only                                 |
| **Execution Mode**     | 100% Autonomous                                      | Runs without manual trade approvals                      |
| **Per-Trade Risk**     | Max 2% risk, max 15% position size                   | Limits the damage from any single trade                  |
| **Portfolio Limits**   | 5 positions, 4% total open risk, 15% cash            | Limits damage when several trades go wrong together      |
| **Drawdown Ceilings**  | 5% daily halt / 15% total drawdown                   | Circuit breakers that stop trading when losses mount     |
| **Emergency Control**  | Telegram `/STOP` and `/FLATTEN`                          | Pause new trades with stops kept, or exit everything     |
| **Account Rules**      | Real-time buying power checks                        | Matches FINRA's 2026 rules; optional daily trade cap     |
| **Testing Approach**   | Rules backtest, 4-week paper trial, small live start | Each stage checks something the others can't             |
| **Go-Live Plan**       | Paper 4 weeks, then 25% live (4+ weeks), 50%, 100%   | Real money scales up only after the system proves itself |
| **Monthly Cost**       | About $10–$20                                      | Server, AI usage, and free data feeds                    |

## Important Risk Notes

Trading involves risk, and the account can lose money. Stop-loss orders can fill at a worse price than the one set, especially in fast or gapping markets. The paper-trading trial shows how the system behaves, but it does not predict live results. This document describes how the system is designed and is not financial advice.
