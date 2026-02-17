# Clawdbot Watchdog

Autonomous health monitoring and auto-recovery system for the OpenClaw (ClawdBot) WhatsApp bot running on GCP.

## Architecture

```
┌─────────────────────────┐       SSH / HTTP        ┌─────────────────────────┐
│  watchdog-server        │ ──────────────────────►  │  openclaw-server        │
│  (e2-micro, free tier)  │                          │  (e2-small)             │
│  IP: 34.82.115.8        │   Health checks:         │  IP: 136.117.149.33     │
│                         │   ┌──────────────────┐   │                         │
│  ┌───────────────────┐  │   │ 1. SSH reachable │   │  ┌───────────────────┐  │
│  │   watchdog.py     │──│──►│ 2. Docker status  │──│─►│  OpenClaw (Docker) │  │
│  │   (cron: */5 min) │  │   │ 3. Control UI    │   │  │  WhatsApp/Baileys │  │
│  └────────┬──────────┘  │   │ 4. WhatsApp logs │   │  │  GPT-4.1          │  │
│           │             │   │ 5. Disk space    │   │  └───────────────────┘  │
│           ▼             │   │ 6. Memory/CPU    │   │                         │
│  ┌───────────────────┐  │   └──────────────────┘   └─────────────────────────┘
│  │  Claude Advisor   │  │
│  │  (Sonnet 4.5)     │  │   Recovery actions:
│  └────────┬──────────┘  │   ┌──────────────────────┐
│           │             │   │ • restart_containers  │
│           ▼             │   │ • revert_last_commit  │
│  ┌───────────────────┐  │   │ • reboot_vm (gcloud)  │
│  │  recovery.py      │──│──►│ • escalate (log only) │
│  └───────────────────┘  │   └──────────────────────┘
│                         │
│  Logs: logs/watchdog.log│
└─────────────────────────┘
```

## How It Works

1. **Cron** triggers `watchdog.py` every 5 minutes
2. **Health checks** run in sequence — SSH, Docker, HTTP, logs, disk, memory
3. If all pass → log success, exit
4. If checks fail → increment failure counter
5. After **2 consecutive failures** → consult **Claude API** with failure context
6. Claude recommends an action via structured tool_use (deterministic output)
7. **Recovery** executes the recommended action (restart, revert, reboot, or escalate)
8. **Post-recovery checks** verify the fix worked
9. If the same action fails twice → automatic escalation

## Files

| File | Purpose |
|------|---------|
| `watchdog.py` | Main orchestrator — entry point for cron |
| `health_check.py` | 6 health checks via SSH and HTTP |
| `claude_advisor.py` | Claude API integration for failure analysis |
| `recovery.py` | Recovery actions (restart, revert, reboot) |
| `config.py` | All configuration (IPs, thresholds, paths) |
| `setup.sh` | One-shot setup script for the watchdog VM |
| `.env` | API keys (not committed) |

## Setup

```bash
# 1. SSH into watchdog-server
gcloud compute ssh watchdog-server --zone=us-west1-b

# 2. Deploy files (from your local machine)
gcloud compute scp --recurse /tmp/watchdog/* fabian@watchdog-server:/home/fabian/watchdog/ --zone=us-west1-b

# 3. Run setup
chmod +x /home/fabian/watchdog/setup.sh
/home/fabian/watchdog/setup.sh

# 4. Configure credentials
cp /home/fabian/watchdog/.env.example /home/fabian/watchdog/.env
nano /home/fabian/watchdog/.env  # Add your ANTHROPIC_API_KEY and GITHUB_PAT

# 5. Add SSH key to openclaw-server
ssh fabian@136.117.149.33 'cat >> ~/.ssh/authorized_keys' < ~/.ssh/id_ed25519.pub

# 6. Test
source /home/fabian/watchdog/venv/bin/activate
python /home/fabian/watchdog/watchdog.py
```

## Decision Flow

```
Health checks → All pass? → Log OK, exit
                   │
                   ▼ (failures)
         Consecutive failures < 2? → Wait for next run
                   │
                   ▼ (threshold hit)
         Claude Advisor analyzes context
                   │
                   ▼
         Recommends: restart | revert | reboot | escalate
                   │
                   ▼
         Execute action → Re-check → Fixed? → Reset counter
                                       │
                                       ▼ (still broken)
                                   Log, try next run
```

## Configuration

Key settings in `config.py`:

| Setting | Default | Description |
|---------|---------|-------------|
| `MAX_CONSECUTIVE_FAILURES` | 2 | Failures before recovery |
| `DISK_USAGE_THRESHOLD` | 90% | Disk alert threshold |
| `MEMORY_USAGE_THRESHOLD` | 90% | Memory alert threshold |
| `SSH_TIMEOUT` | 10s | SSH connection timeout |
| `HTTP_TIMEOUT` | 10s | HTTP request timeout |
| `CLAUDE_MODEL` | claude-sonnet-4-5 | Model for analysis |

## Logs

- `logs/watchdog.log` — Main log (5MB rotation, 5 backups)
- `logs/cron.log` — Cron stdout/stderr
- Log rotation: daily, 7 days retention, compressed

## GCP Resources

| Resource | Type | Zone | Purpose |
|----------|------|------|---------|
| `openclaw-server` | e2-small | us-west1-b | Primary bot (monitored) |
| `watchdog-server` | e2-micro | us-west1-b | Watchdog (this system) |
