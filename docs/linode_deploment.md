# Linode (Akamai Cloud) Deployment Guide

Focused setup guide for deploying the trading bot on Linode's $5/mo Nanode.

---

## 1. Prerequisites

| Tool | Version |
|------|---------|
| **Docker** | 24+ |
| **Docker Compose** | v2 |
| **Linode account** | $5/mo Nanode (1 GB RAM, 1 vCPU, 25 GB SSD) |
| **SSH key** | `ssh-keygen -t ed25519` |

---

## 2. Linode Account Setup

### 2a. Create Account

1. Go to <https://login.linode.com/signup>
2. Sign up with email, Google, or GitHub
3. Add payment method (card or PayPal)
4. Verify phone number if prompted

### 2b. Create Limited-Access User (for automation/CI)

> This follows the principle of least privilege — the bot's deployment user only needs Linode/Firewall management, not billing access.

1. **Administration → Identity & Access → Users**
2. Click **Add A User**
3. Enter username and email (Linode emails a password-setup link)
4. Click **Add User**
5. Find the user in the list → **⋮ → View Assigned Roles**
6. Assign:
   - **Linode Creator / Admin** (create/manage Linodes)
   - **Firewall Creator / Admin** (manage firewalls)
7. **Leave all billing roles unassigned**

---

## 3. Create the Linode

### 3a. Via Cloud Manager (GUI)

1. **Linodes → Create Linode**
2. **Distribution**: Ubuntu 24.04 LTS (or 22.04 LTS)
3. **Region**: Closest to your users / NYSE (e.g., **Newark, NJ** for US equities)
4. **Type**: **Nanode 1GB** ($5/mo)
   - 1 vCPU, 1 GB RAM, 25 GB SSD, 1 TB transfer
5. **Label**: `trading-bot`
6. **Root password**: Generate strong password (save it)
7. **SSH Keys**: Add your `id_ed25519.pub`
8. Click **Create Linode**

### 3b. Via CLI (optional)

```bash
linode-cli linodes create \
  --image linode/ubuntu24.04 \
  --type g6-nanode-1 \
  --region us-east \
  --label trading-bot \
  --authorized_keys "$(cat ~/.ssh/id_ed25519.pub)" \
  --root_pass "your-strong-password"
```
---

## 4. Provision & Harden

```bash
# SSH in
ssh -i ~/.ssh/id_ed25519 root@<linode-ip>

# Update & install Docker
apt-get update && apt-get upgrade -y
apt-get install -y docker.io docker-compose-plugin
systemctl enable --now docker

# Create non-root user for bot operations
adduser --disabled-password --gecos "" botuser
usermod -aG docker botuser

# Firewall (UFW) — allow only SSH + bot's outbound
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw enable

# Optional: Fail2Ban for SSH brute-force protection
apt-get install -y fail2ban
systemctl enable --now fail2ban
```

---

## 5. Deploy the Bot

```bash
# Switch to botuser
su - botuser

# Clone repo
git clone https://github.com/zoyaumar/automated-ai-trading-bot.git
cd automated-ai-trading-bot

# Configure environment
cp .env.example .env
# Edit .env with your keys (see Section 6)

# Review config.yaml (paper mode default)
cat config.yaml

# Start
docker compose up -d

# Verify
docker compose ps
docker compose logs -f --tail 30
```

---

## 6. Configuration

### 6a. `.env` (gitignored)

```ini
# Alpaca (paper keys for testing)
ALPACA_PAPER_KEY=PKxxxxxxxxxxxxxxxxxxxx
ALPACA_PAPER_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ALPACA_LIVE_KEY=
ALPACA_LIVE_SECRET=

# LLM — pick one
NVIDIA_API_KEY=nvapi-xxxxxxxxxxxxxxxxxxxxxxxx
# GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxxxxxx

# Data
FINNHUB_API_KEY=xxxxxxxxxxxxxxxxxxxx
FRED_API_KEY=xxxxxxxxxxxxxxxxxxxx

# Telegram (optional but recommended)
TELEGRAM_BOT_TOKEN=xxxxxxxxxxxxxxxxxxxx
TELEGRAM_CHAT_ID=123456789

# Heartbeat (optional — Healthchecks.io, cronitor, etc.)
# HEARTBEAT_URL=https://hc-ping.com/your-uuid
```

### 6b. `config.yaml` (key settings)

```yaml
mode: paper                 # paper | live
cycle_minutes: 30
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

llm:
  providers:
    - name: nvidia-integrate
      provider: openai_compat
      base_url: https://integrate.api.nvidia.com/v1
      model: nvidia/nemotron-3-ultra-550b-a55b
      api_key_env: NVIDIA_API_KEY
  max_calls_per_day: 50

monitoring:
  heartbeat_minutes: 5
  alert_after_minutes: 15
```

> **Changes require restart:** `docker compose restart`

---

## 7. Linode-Specific Operations

### 7a. Backups ($2/mo)

**Linodes → trading-bot → Backups → Enable Backups**
- Daily automated snapshots
- 7-day retention
- One-click restore

### 7b. Monitoring & Alerts

- **Linodes → trading-bot → Monitoring** — CPU, RAM, disk, network
- Set alerts: CPU > 80%, RAM > 85%, Disk > 90%
- Integrate with PagerDuty / Slack / email

### 7c. Scheduled Reboots (kernel updates)

```bash
# As root, schedule monthly reboot at 4 AM ET (market closed)
crontab -e
# Add:
0 4 1 * * /sbin/shutdown -r now
```

### 7d. Cost Control

- **Nanode 1GB**: $5/mo (capped, hourly billing)
- **Backups**: +$2/mo
- **Total**: $7/mo with backups
- Delete Linode anytime to stop charges

---

## 8. Key Commands

```bash
# Start
docker compose up -d

# Tail logs
docker compose logs -f --tail 100

# Restart
docker compose restart

# Stop
docker compose down

# Update & rebuild
git pull
docker compose up -d --build

# Shell access
docker compose exec bot bash

# View SQLite DB
docker compose exec bot python -c "
import sqlite3; conn = sqlite3.connect('data/bot.db');
for row in conn.execute('SELECT * FROM cycles ORDER BY id DESC LIMIT 5'): print(row)
"
```

---

## 9. Common Pitfalls

| Symptom | Cause | Fix |
|---------|-------|-----|
| Bot exits immediately | Missing `.env` or bad `config.yaml` | `cp .env.example .env`, fix `config.yaml` |
| `502` on NVIDIA API | Wrong key format | NVIDIA needs `nvapi-...`, not `sk-...` |
| Bot doesn't trade | `mode: paper` | Change to `live` after paper testing |
| Telegram messages missing | Wrong `chat_id` | Verify via Python: `bot.get_me()` |
| OOM kills container | Host RAM exhausted | Check `docker stats`; upgrade to 2 GB if needed |
| No proposals from LLM | Model returned non-JSON | Try different model / check provider status |
| SSH timeout | UFW blocking | `ufw allow ssh` |
| Clock drift | No NTP | `timedatectl set-ntp true` |

---

## 10. Why Linode over Oracle Free Tier?

| Factor | Oracle Free Tier | Linode $5/mo |
|--------|------------------|--------------|
| **Reliability** | Idle reclamation kills VMs after 7 days | No idle reclamation |
| **Support** | Community only | 24/7 ticket support |
| **Backups** | Manual | $2/mo automated |
| **Network** | Variable | Predictable, low latency to US-East |
| **Setup time** | 5–30 min provisioning | ~2 min provisioning |
| **Architecture** | ARM A1 (limited images) | x86_64 (standard) |
| **Monthly cost** | $0 (+ $200 credit optional) | $5–7 fixed |

For a production trading bot where uptime matters, Linode's predictable $5/mo is a better fit than Oracle's free-tier limitations.

---

## 11. Quick Reference

```bash
# Deploy from scratch (root)
ssh root@<ip>
apt-get update && apt-get install -y docker.io docker-compose-plugin ufw
ufw allow ssh && ufw enable
adduser --disabled-password --gecos "" botuser && usermod -aG docker botuser
su - botuser
git clone https://github.com/zoyaumar/automated-ai-trading-bot.git
cd automated-ai-trading-bot
cp .env.example .env
# nano .env  # add keys
docker compose up -d
docker compose logs -f
```

**Monthly cost: $5–7** | **Uptime: 99.9% SLA** | **Architecture: x86_64 (standard)**