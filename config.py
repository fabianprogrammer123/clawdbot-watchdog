"""Watchdog configuration."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the watchdog directory
load_dotenv(Path(__file__).parent / ".env")

# Target VM
TARGET_IP = "136.117.149.33"
SSH_USER = "fabian"
SSH_KEY_PATH = os.path.expanduser("~/.ssh/id_ed25519")
SSH_TIMEOUT = 10  # seconds

# Control UI
CONTROL_UI_PORT = 18789
CONTROL_UI_URL = f"http://{TARGET_IP}:{CONTROL_UI_PORT}"
HTTP_TIMEOUT = 10  # seconds

# Paths on the target VM
OPENCLAW_DIR = "/home/fabian/openclaw"
OPENCLAW_CONFIG_DIR = "/home/fabian/.openclaw"

# Health check thresholds
MAX_CONSECUTIVE_FAILURES = 2
DISK_USAGE_THRESHOLD = 90  # percent
MEMORY_USAGE_THRESHOLD = 90  # percent

# State file to track consecutive failures
STATE_FILE = Path(__file__).parent / "state.json"

# Logging
LOG_DIR = Path(__file__).parent / "logs"
LOG_FILE = LOG_DIR / "watchdog.log"
LOG_DIR.mkdir(exist_ok=True)

# API keys
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GITHUB_PAT = os.getenv("GITHUB_PAT", "")

# Claude model (Sonnet for cost efficiency)
CLAUDE_MODEL = "claude-sonnet-4-5-20250929"

# GCP
GCP_PROJECT = "luminous-return-468119-i1"
GCP_ZONE = "us-west1-b"
TARGET_INSTANCE = "openclaw-server"
