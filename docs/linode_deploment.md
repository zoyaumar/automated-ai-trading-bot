# Linode (Akamai Cloud) Deployment & 1-Month Paper Trading Guide

Production deployment guide for running the AI Trading Bot on a **Linode $5/mo Nanode** (1 GB RAM, 1 vCPU, 25 GB SSD, Newark/US-East region) in **paper-trading mode**.

---

## 1. Architecture & Security Model

The bot is designed to be **operationally zero-trust and self-contained**:
* **Zero Inbound Ports**: Operates exclusively via outbound HTTPS requests and outbound WebSocket connections to Alpaca, Telegram (long-polling), LLM endpoints, and Finnhub. No web servers or open inbound webhooks.
* **Non-Root Container**: Runs inside Docker under an unprivileged user (`botuser`), with persistent data stored in a dedicated named volume (`bot_data`).
* **Decoupled Dead-Man's Switch**: Pings an external heartbeat monitor (e.g., [Healthchecks.io](https://healthchecks.io)) every 5 minutes independently of Telegram messaging.
* **Persistent SQLite Audit Log**: All cycles, LLM prompts/responses, orders, equity snapshots, and alerts are written to `data/bot.db` with SQLite WAL mode enabled.

---

## 2. Linode Server Provisioning

### 2a. Create the Linode Instance
1. Log into [Linode Cloud Manager](https://cloud.linode.com).
2. Click **Create Linode**:
   - **Distribution**: Ubuntu 24.04 LTS (or 22.04 LTS).
   - **Region**: **Newark, NJ (us-east)** — lowest latency to NYSE/Nasdaq and Alpaca servers.
   - **Plan**: **Shared CPU -> Nanode 1GB** ($5.00/mo).
   - **Label**: `trading-bot-paper`
   - **SSH Keys**: Select or upload your `id_ed25519.pub` key.
   - **Backups**: Recommended (+$2/mo) for automated daily snapshot retention.
3. Click **Create Linode**.

### 2b. Initial Server Hardening & Clock Sync
SSH into your Linode as root:
```bash
ssh -i ~/.ssh/id_ed25519 root@<LINODE_IP>
```

Run initial setup:
```bash
# 1. Update system packages
apt-get update && apt-get upgrade -y

# 2. Install Docker, Docker Compose, and Chrony (time sync)
apt-get install -y docker.io docker-compose-plugin chrony ufw fail2ban
systemctl enable --now docker
systemctl enable --now chrony
systemctl enable --now fail2ban

# 3. Synchronize system clock (Critical for market data timestamps & order signing)
timedatectl set-timezone America/New_York
timedatectl set-ntp true
chronyc tracking

# 4. Host Firewall (UFW) - Block ALL inbound except SSH
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH'
ufw --force enable

# 5. Create unprivileged operator user
adduser --disabled-password --gecos "" botuser
usermod -aG docker botuser
```

---

## 3. Bot Installation

Switch to the `botuser` operator account:
```bash
su - botuser
```

Clone the repository and set up environment:
```bash
git clone https://github.com/zoyaumar/automated-ai-trading-bot.git
cd automated-ai-trading-bot

# Set up environment variables
cp .env.example .env
chmod 600 .env
nano .env
```

---

## 4. Environment Configuration (`.env`)

Fill in the `.env` file with your credentials:

```ini
# ==========================================
# Alpaca Trading API (Paper Trading)
# ==========================================
ALPACA_PAPER_KEY=PKXXXXXXXXXXXXXXXXXX
ALPACA_PAPER_SECRET=XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
ALPACA_LIVE_KEY=
ALPACA_LIVE_SECRET=

# ==========================================
# LLM Providers (Configure at least one)
# ==========================================
# For NVIDIA NIM:
NVIDIA_API_KEY=nvapi-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
# For Groq:
# GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
# For Anthropic Claude:
# ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxxxxx

# ==========================================
# Market News & Macro
# ==========================================
FINNHUB_API_KEY=your_finnhub_key_here
FRED_API_KEY=your_fred_key_here

# ==========================================
# Telegram Monitoring & Commands
# ==========================================
TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ
TELEGRAM_CHAT_ID=your_chat_or_group_id

# TELEGRAM_USER_ID is your numeric personal user ID (from @userinfobot).
# Restricts /stop, /resume, /flatten commands to YOU only, even in groups.
TELEGRAM_USER_ID=your_numeric_user_id

# ==========================================
# External Dead-Man's Switch (Recommended)
# ==========================================
# Create a free check at https://healthchecks.io with 5-min interval, 15-min grace:
HEARTBEAT_URL=https://hc-ping.com/your-uuid-here
```

---

## 5. Verify `config.yaml`

Ensure `config.yaml` is set for paper trading and proper risk controls:

```yaml
mode: paper                 # MUST be paper
cycle_minutes: 30           # Evaluation frequency
safety_check_minutes: 5

watchlist: [AAPL, MSFT, NVDA, AMZN, SPY, QQQ]

risk:
  max_risk_per_trade_pct: 2
  max_position_pct: 15
  max_open_positions: 5
  max_total_open_risk_pct: 4
  min_cash_pct: 15
  daily_loss_limit_pct: 5
  max_drawdown_pct: 15
  min_stop_atr_multiple: 1.5
  max_stop_pct: 8
  min_reward_to_risk: 1.5

timing:
  skip_first_minutes: 15
  skip_last_minutes: 30
  max_data_age_seconds: 120
  cancel_unfilled_entry_minutes: 10
  earnings_skip_trading_days: 1

news:
  headlines_enabled: true
  earnings_filter_enabled: true

llm:
  providers:
    - name: nvidia-integrate
      provider: openai_compat
      base_url: https://integrate.api.nvidia.com/v1
      model: nvidia/nemotron-3-ultra-550b-a55b
      api_key_env: NVIDIA_API_KEY
  timeout_seconds: 60
  max_calls_per_day: 50
  monthly_budget_usd: 0

execution:
  entry_limit_buffer_pct: 0.1

monitoring:
  heartbeat_minutes: 5
  alert_after_minutes: 15
```

---

## 6. Launch & Verification

### 6a. Start the Container
```bash
docker compose up -d --build
```

### 6b. Verify Running Container & Logs
```bash
# Check status
docker compose ps

# Tail application logs
docker compose logs -f --tail 50
```

You should see:
1. `Bot started` in logs and received as a Telegram message.
2. WebSocket subscribing to watchlist tickers.
3. Successful initial heartbeat ping.
4. Startup protection check passing.

---

## 7. The 1-Month Paper Staging Plan

Running in paper mode for 4 weeks on Linode validates all real-world edge cases before any real capital is deployed.

```mermaid
flowchart LR
    W1[Week 1: Connectivity & Signals] --> W2[Week 2: Execution & Orders]
    W2 --> W3[Week 3: Risk & Volatility Stress]
    W3 --> W4[Week 4: Audit & Review]
```

### Week 1: Infrastructure & Telemetry Health
* [ ] **Startup Notification**: Confirm you receive the "Bot started" Telegram message.
* [ ] **WebSocket Longevity**: Monitor logs over a 24-hour cycle. Confirm the quote stream doesn't disconnect or freeze during market hours.
* [ ] **Dead-Man's Switch**: Check [Healthchecks.io](https://healthchecks.io) dashboard to ensure green pings arrive every 5 minutes.
* [ ] **Telegram Command Drill**: Send `/stop` and `/resume` from your mobile phone. Verify that:
  - Bot responds promptly.
  - The pause state persists in `data/bot.db`.
  - Non-authorized users cannot execute commands.

### Week 2: Signal Quality & LLM Accuracy
* [ ] **Proposal Inspection**: Run SQLite queries to inspect proposals generated by the LLM:
  ```bash
  docker compose exec bot python -c "
  import sqlite3; conn = sqlite3.connect('data/bot.db')
  for row in conn.execute('SELECT ticker, action, confidence, stop_loss_pct, reasoning FROM proposals ORDER BY id DESC LIMIT 10'):
      print(row)
  "
  ```
* [ ] **JSON Validation**: Check if any LLM calls produced invalid JSON or triggered provider fallbacks.
* [ ] **News & Earnings Gate**: Verify that symbols with upcoming earnings announcements within 1 day are successfully blocked by the risk firewall.

### Week 3: Risk Firewall & Execution Realism
* [ ] **Order Placement**: Confirm bracket orders (`BUY` limit with associated stop-loss and take-profit legs) appear on the [Alpaca Paper Dashboard](https://app.alpaca.markets/paper/dashboard/overview).
* [ ] **Stop & Target Bounds**: Ensure no orders exceed the 2% trade risk or 15% position size caps.
* [ ] **Emergency Flatten Drill**:
  - Open a paper position.
  - Send `/flatten` via Telegram.
  - Confirm all open orders are cancelled, the position is closed via market order, and an audit alert is recorded in the database.

### Week 4: Month-End Audit & Performance Review
* [ ] **Reconciliation Check**: Run `scripts/audit.py` to compare Alpaca broker state against local SQLite records:
  ```bash
  docker compose exec bot python scripts/audit.py
  ```
* [ ] **Drawdown & High-Water Mark**: Check `equity_snapshots` in SQLite:
  ```bash
  docker compose exec bot python -c "
  import sqlite3; conn = sqlite3.connect('data/bot.db')
  for row in conn.execute('SELECT taken_at, equity, cash, high_water_mark, drawdown_pct FROM equity_snapshots ORDER BY id DESC LIMIT 5'):
      print(row)
  "
  ```
* [ ] **Resource Consumption**: Inspect Linode CPU and memory consumption:
  ```bash
  docker stats --no-stream
  ```
  (Memory should remain stable under 350 MB on the 1 GB Nanode).

---

## 8. Operational Commands Reference

| Action | Command |
|---|---|
| **View Live Logs** | `docker compose logs -f --tail 100` |
| **Restart Container** | `docker compose restart` |
| **Rebuild Container** | `git pull && docker compose up -d --build` |
| **Stop Container** | `docker compose down` |
| **Enter Container Shell** | `docker compose exec bot bash` |
| **Inspect SQLite Alerts** | `docker compose exec bot python -c "import sqlite3; c=sqlite3.connect('data/bot.db'); print(c.execute('SELECT * FROM alerts ORDER BY id DESC LIMIT 10').fetchall())"` |
| **Check Time Sync** | `chronyc tracking` |

---

## 9. Automated Database Backups (Cron)

Set up a daily automated backup of the SQLite database to prevent loss:

```bash
# As botuser on Linode host:
crontab -e
```

Add the following daily backup cron (runs at 4:30 AM ET when markets are closed):
```bash
30 4 * * * docker compose -f /home/botuser/automated-ai-trading-bot/docker-compose.yml exec -T bot sqlite3 data/bot.db ".backup data/bot_backup.db"
```

---

## 10. Troubleshooting Matrix

| Issue | Likely Cause | Solution |
|---|---|---|
| **Container exits on startup** | Missing or malformed `.env` | Run `docker compose logs` to read exact error. Verify all API keys are present. |
| **Permission Denied for `data/bot.db`** | Docker volume mounted with root ownership | Ensure latest `Dockerfile` with `mkdir -p /app/data && chown -R botuser:botuser /app` is built. |
| **Telegram commands ignored** | `TELEGRAM_USER_ID` mismatch | Send a message to `@userinfobot` to verify your numeric ID and update `.env`. |
| **`WebSocket stopped` in logs** | Network glitch or Alpaca maintenance | WebSocket auto-reconnects with backoff. Check if persistent outside market hours. |
| **Heartbeat alert received** | Container hung or VPS offline | SSH in, check `docker compose ps` and `docker compose logs --tail 100`. |
| **Quote age stale error** | VPS clock drifted | Run `chronyc tracking` and `timedatectl set-ntp true`. |