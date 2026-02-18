# Clawdbot Watchdog

Autonomous health monitoring and auto-recovery for ClawdBot — the AI business agent running on GCP.

## Cost Model

```
99% of runs:  check.sh (pure bash)     → $0.00
 ~1% of runs: watchdog.py + Claude API → ~$0.001-0.005 (Haiku)
Monthly cost estimate: < $0.50 API + $0 hosting (e2-micro free tier)
```

## Architecture

```
  Cron (*/5 min)
       │
       ▼
  ┌─────────────────────────────────────────────────┐
  │  check.sh (pure bash — ZERO API cost)           │
  │                                                 │
  │  1. SSH → is gateway container running?         │
  │     YES → exit 0 (done, free)                   │
  │     NO  ↓                                       │
  │  2. Try: docker compose up -d                   │
  │     Fixed? → exit 0 (done, free)                │
  │     Still broken? ↓                             │
  │  3. Escalate to watchdog.py                     │
  └───────────────┬─────────────────────────────────┘
                  ▼
  ┌─────────────────────────────────────────────────┐
  │  watchdog.py (Python — rare, targeted)          │
  │                                                 │
  │  1. Run 9 health checks (SSH-based)             │
  │  2. Gather diagnostics:                         │
  │     - Gateway logs (last 80 lines)              │
  │     - Git history (workspace + openclaw)         │
  │     - Container states & restart counts         │
  │  3. Send to Claude API (Haiku — cheapest)       │
  │  4. Claude analyzes and recommends action       │
  │  5. Execute recovery, verify, log               │
  └───────────────┬─────────────────────────────────┘
                  ▼
  ┌─────────────────────────────────────────────────┐
  │  Recovery Actions (least → most disruptive)     │
  │                                                 │
  │  restart_gateway    → restart just the bot      │
  │  restart_all        → full docker compose cycle │
  │  revert_workspace   → undo ClawdBot self-edit   │
  │  revert_openclaw    → undo deploy change        │
  │  reboot_vm          → full VM reboot via GCP    │
  │  escalate           → log for manual review     │
  └─────────────────────────────────────────────────┘
```

## How ClawdBot Breaks (And How We Fix It)

ClawdBot is **self-modifying** — it edits its own AGENTS.md, GOALS.md, and LEARNINGS.md, and can run arbitrary code. This means:

1. **Code self-edit breaks the bot** → watchdog sees gateway crash, checks git log, Claude recommends `revert_workspace_commit`
2. **Container hung/OOM** → simple restart via `docker compose up -d` (handled by check.sh, no API cost)
3. **WhatsApp disconnected** → gateway restart reconnects Baileys automatically
4. **VM-level issue** → `gcloud compute instances reset` as last resort

## Health Checks (9 total)

| Check | Severity | What It Monitors |
|-------|----------|-----------------|
| `ssh_reachable` | critical | Can we reach the VM at all? |
| `gateway_running` | critical | Is the main bot container alive? |
| `whatsapp_connected` | warning | Is Baileys connected to WhatsApp? |
| `control_ui` | warning | Is the OpenClaw control UI (port 18789) responding? |
| `website_serving` | info | Is nginx serving the Axiom website (port 80)? |
| `heartbeat_fresh` | info | Has the bot's internal heartbeat fired recently? |
| `dashboard_fresh` | info | Is the dashboard collector updating? |
| `disk_space` | warning | Is disk usage below 90%? |
| `resources` | info | Memory and CPU load |

**Severity levels:**
- `critical` → act after 1 failure
- `warning` → act after 2 failures
- `info` → log only (no recovery action)

## Files

| File | Purpose |
|------|---------|
| `check.sh` | Cron entry point — lightweight bash, zero API cost |
| `watchdog.py` | Full analysis — only on repeated failures |
| `health_check.py` | 9 health checks + diagnostic context gathering |
| `claude_advisor.py` | Claude API integration (Haiku, ~$0.001/call) |
| `recovery.py` | 6 recovery actions with WhatsApp reconnect verification |
| `config.py` | All configuration, thresholds, severity levels |

## Repo Organization

| Repo | Purpose | Location on VM |
|------|---------|---------------|
| `clawdbot-watchdog` | This — infrastructure/monitoring | watchdog-server:/home/fabian/watchdog/ |
| `clawdbot-workspace` | Bot's brain (AGENTS.md, GOALS.md, etc.) | openclaw-server:/home/fabian/.openclaw/workspace-reception/ |
| `axiom-website` | Website files served by nginx | openclaw-server:/home/fabian/.openclaw/website/ |
| `openclaw` | OpenClaw framework (upstream) | openclaw-server:/home/fabian/openclaw/ |

**Why separate?** The watchdog is infrastructure that monitors the bot. The workspace is the bot's own "brain" that it self-modifies. Mixing them would create circular dependencies. The watchdog needs to be able to revert workspace commits — it can't do that if it lives in the same repo.

## Setup

```bash
# Already deployed. To redeploy after changes:
gcloud compute scp --recurse /tmp/watchdog/* fabian@watchdog-server:/home/fabian/watchdog/ --zone=us-west1-b

# SSH in to verify:
gcloud compute ssh watchdog-server --zone=us-west1-b
crontab -l  # should show */5 check.sh
tail -f /home/fabian/watchdog/logs/watchdog.log
```

## GCP Resources

| Resource | Type | Zone | IP | Purpose |
|----------|------|------|----|---------|
| `openclaw-server` | e2-small | us-west1-b | 136.117.149.33 | ClawdBot (monitored) |
| `watchdog-server` | e2-micro | us-west1-b | 34.82.115.8 | Watchdog (free tier) |
