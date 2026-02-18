#!/bin/bash
# Lightweight health check — runs every 5 min via cron.
# ZERO API cost. Pure bash + SSH.
#
# Flow:
#   1. SSH to openclaw-server, check if gateway container is running
#   2. If running → exit (free, fast, done)
#   3. If not running → try simple restart (docker compose up -d)
#   4. If restart fails or this is a repeated failure → escalate to watchdog.py (Claude API)
#
# Cost model: this script is free. watchdog.py costs ~$0.001-0.005 per call (Haiku).

set -euo pipefail

WATCHDOG_DIR="$(cd "$(dirname "$0")" && pwd)"
STATE_FILE="$WATCHDOG_DIR/state.json"
LOG_FILE="$WATCHDOG_DIR/logs/watchdog.log"
PYTHON="$WATCHDOG_DIR/venv/bin/python"

TARGET="fabian@136.117.149.33"
SSH_KEY="$HOME/.ssh/id_ed25519"
SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=10 -o BatchMode=yes -i $SSH_KEY"
GATEWAY="openclaw-openclaw-gateway-1"
OPENCLAW_DIR="/home/fabian/openclaw"

DEV_MODE_FILE="$WATCHDOG_DIR/DEV_MODE"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [check.sh] $1" >> "$LOG_FILE"
}

# === DEV MODE: skip all checks and recovery ===
if [ -f "$DEV_MODE_FILE" ]; then
    exit 0
fi

# Read bash_failures counter from state file (simple integer file)
BASH_STATE="$WATCHDOG_DIR/bash_state"
get_fail_count() {
    if [ -f "$BASH_STATE" ]; then
        cat "$BASH_STATE" 2>/dev/null || echo "0"
    else
        echo "0"
    fi
}
set_fail_count() {
    echo "$1" > "$BASH_STATE"
}

# === CHECK 1: Can we SSH? ===
if ! ssh $SSH_OPTS "$TARGET" "echo ok" &>/dev/null; then
    FAILS=$(get_fail_count)
    FAILS=$((FAILS + 1))
    set_fail_count "$FAILS"
    log "FAIL: SSH unreachable (consecutive: $FAILS)"

    if [ "$FAILS" -ge 2 ]; then
        log "SSH unreachable twice — attempting VM check via gcloud"
        # Check if the VM itself is running
        VM_STATUS=$(gcloud compute instances describe openclaw-server \
            --zone=us-west1-b --project=luminous-return-468119-i1 \
            --format='get(status)' 2>/dev/null || echo "UNKNOWN")

        if [ "$VM_STATUS" != "RUNNING" ]; then
            log "VM is $VM_STATUS — starting it"
            gcloud compute instances start openclaw-server \
                --zone=us-west1-b --project=luminous-return-468119-i1 2>&1 | \
                while read -r line; do log "gcloud: $line"; done
            sleep 30
        fi

        if [ "$FAILS" -ge 3 ]; then
            log "SSH unreachable 3+ times — escalating to watchdog.py"
            "$PYTHON" "$WATCHDOG_DIR/watchdog.py" >> "$LOG_FILE" 2>&1 || true
        fi
    fi
    exit 1
fi

# === CHECK 2: Is the gateway container running? ===
CONTAINER_STATUS=$(ssh $SSH_OPTS "$TARGET" \
    "docker inspect --format '{{.State.Status}}' $GATEWAY 2>/dev/null || echo 'not_found'")

if [ "$CONTAINER_STATUS" = "running" ]; then
    # All good — reset counters and exit
    if [ "$(get_fail_count)" != "0" ]; then
        log "OK: Gateway running (recovered from previous failure)"
        set_fail_count 0
        # Reset Python state too
        echo '{"consecutive_failures": 0, "last_action": null, "last_action_time": null, "actions_tried": []}' > "$STATE_FILE"
    fi
    exit 0
fi

# === Gateway is NOT running ===
FAILS=$(get_fail_count)
FAILS=$((FAILS + 1))
set_fail_count "$FAILS"
log "FAIL: Gateway status='$CONTAINER_STATUS' (consecutive: $FAILS)"

# First failure: try simple restart (zero API cost)
if [ "$FAILS" -le 2 ]; then
    log "Attempting simple restart (docker compose up -d)..."
    RESTART_OUT=$(ssh $SSH_OPTS "$TARGET" \
        "cd $OPENCLAW_DIR && docker compose up -d 2>&1" || echo "RESTART_FAILED")
    log "Restart output: $RESTART_OUT"

    # Wait for gateway to boot (apt-get + npm + playwright = ~30s)
    sleep 35

    # Check if it came back
    NEW_STATUS=$(ssh $SSH_OPTS "$TARGET" \
        "docker inspect --format '{{.State.Status}}' $GATEWAY 2>/dev/null || echo 'not_found'")

    if [ "$NEW_STATUS" = "running" ]; then
        log "OK: Simple restart worked — gateway is running"
        set_fail_count 0
        echo '{"consecutive_failures": 0, "last_action": "restart_all", "last_action_time": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'", "actions_tried": []}' > "$STATE_FILE"
        exit 0
    else
        log "Simple restart didn't help — gateway status: $NEW_STATUS"
    fi
fi

# Repeated failures: escalate to Python watchdog (Claude API analysis)
if [ "$FAILS" -ge 2 ]; then
    log "Escalating to watchdog.py for Claude analysis (failure #$FAILS)..."
    "$PYTHON" "$WATCHDOG_DIR/watchdog.py" >> "$LOG_FILE" 2>&1 || true
fi

exit 1
