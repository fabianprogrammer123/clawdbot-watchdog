"""Watchdog configuration."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the watchdog directory
load_dotenv(Path(__file__).parent / ".env")

# Target VM
TARGET_IP = "136.117.149.33"
TARGET_INTERNAL_IP = "10.138.0.2"
SSH_USER = "fabian"
SSH_KEY_PATH = os.path.expanduser("~/.ssh/id_ed25519")
SSH_TIMEOUT = 10  # seconds

# Ports
CONTROL_UI_PORT = 18789
BRIDGE_PORT = 18790
WEBSITE_PORT = 80
HTTP_TIMEOUT = 10  # seconds

# Paths on the target VM
OPENCLAW_DIR = "/home/fabian/openclaw"
OPENCLAW_CONFIG_DIR = "/home/fabian/.openclaw"
WORKSPACE_DIR = "/home/fabian/.openclaw/workspace-reception"
WEBSITE_DIR = "/home/fabian/.openclaw/website"
DASHBOARD_DATA = "/home/fabian/.openclaw/website/dashboard/data.json"

# Docker container names
GATEWAY_CONTAINER = "openclaw-openclaw-gateway-1"
WEBSITE_CONTAINER = "openclaw-website-1"
# CLI containers are ephemeral — don't monitor them

# Gateway boot time (apt-get, npm install, playwright install on startup)
GATEWAY_BOOT_SECONDS = 30

# Health check thresholds — per-check severity
# "critical" = act after 1 failure, "warning" = act after 2, "info" = log only
CHECK_SEVERITY = {
    "ssh_reachable": "critical",
    "gateway_running": "critical",
    "whatsapp_connected": "warning",
    "control_ui": "warning",
    "website_serving": "info",
    "heartbeat_fresh": "info",
    "dashboard_fresh": "info",
    "disk_space": "warning",
    "resources": "info",
}

DISK_USAGE_THRESHOLD = 90  # percent
MEMORY_USAGE_THRESHOLD = 90  # percent
HEARTBEAT_STALE_MINUTES = 45  # heartbeat fires every 30 min
DASHBOARD_STALE_MINUTES = 5   # collector updates every 60s

# State file to track consecutive failures
STATE_FILE = Path(__file__).parent / "state.json"

# Logging
LOG_DIR = Path(__file__).parent / "logs"
LOG_FILE = LOG_DIR / "watchdog.log"
LOG_DIR.mkdir(exist_ok=True)

# API keys
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GITHUB_PAT = os.getenv("GITHUB_PAT", "")

# Claude model — Haiku for cost efficiency (only called on actual failures)
CLAUDE_MODEL = "claude-haiku-4-5-20251001"

# Git repos the bot uses (for monitoring divergence)
BOT_WORKSPACE_REPO = "fabianprogrammer123/clawdbot-workspace"
BOT_WEBSITE_REPO = "fabianprogrammer123/axiom-website"

# GCP
GCP_PROJECT = "luminous-return-468119-i1"
GCP_ZONE = "us-west1-b"
TARGET_INSTANCE = "openclaw-server"
