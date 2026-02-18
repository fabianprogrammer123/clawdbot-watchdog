#!/usr/bin/env python3
"""
Watchdog — ClawdBot health monitor and auto-recovery agent.

This is the EXPENSIVE path — only called by check.sh when a simple restart
has already failed. This script gathers diagnostics, consults Claude API,
and executes intelligent recovery.

Cost model:
  - check.sh (cron every 5 min): pure bash, zero API cost
  - check.sh auto-restart: bash docker restart, zero API cost
  - THIS SCRIPT: only on repeated failures, ~$0.001-0.005 per Claude call (Haiku)
"""

import json
import logging
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import config
from health_check import run_all_checks, get_diagnostic_context, CheckResult
from claude_advisor import get_recommendation
from recovery import execute_action

# Set up logging
logger = logging.getLogger("watchdog")
logger.setLevel(logging.INFO)

file_handler = RotatingFileHandler(
    config.LOG_FILE,
    maxBytes=5 * 1024 * 1024,
    backupCount=5,
)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))
logger.addHandler(file_handler)

console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
))
logger.addHandler(console_handler)


def load_state() -> dict:
    if config.STATE_FILE.exists():
        try:
            return json.loads(config.STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"consecutive_failures": 0, "last_action": None, "last_action_time": None, "actions_tried": []}


def save_state(state: dict):
    config.STATE_FILE.write_text(json.dumps(state, indent=2))


def run():
    """Full diagnostic run — gather context, consult Claude, execute recovery."""
    start_time = datetime.now(timezone.utc)
    logger.info("=" * 60)
    logger.info(f"WATCHDOG FULL ANALYSIS at {start_time.isoformat()}")
    logger.info("(check.sh escalated — simple restart didn't help)")

    state = load_state()
    state["consecutive_failures"] += 1

    # Run all health checks
    logger.info("Running full health checks...")
    results = run_all_checks()
    passed = [r for r in results if r.passed]
    failed = [r for r in results if not r.passed]

    logger.info(f"Results: {len(passed)} passed, {len(failed)} failed")

    if not failed:
        logger.info("All checks passed — system recovered on its own!")
        state["consecutive_failures"] = 0
        state["actions_tried"] = []
        save_state(state)
        return

    # Separate by severity
    critical_fails = [r for r in failed if r.severity == "critical"]
    warning_fails = [r for r in failed if r.severity == "warning"]
    info_fails = [r for r in failed if r.severity == "info"]

    logger.warning(f"Critical: {len(critical_fails)}, Warning: {len(warning_fails)}, Info: {len(info_fails)}")
    for r in failed:
        logger.warning(f"  [{r.severity}] {r.name}: {r.details}" + (f" ({r.error})" if r.error else ""))

    # Only consult Claude for critical/warning failures
    actionable_fails = critical_fails + warning_fails
    if not actionable_fails:
        logger.info("Only info-level failures — logging and moving on")
        save_state(state)
        return

    # Gather diagnostic context (logs, git history)
    logger.info("Gathering diagnostic context...")
    diagnostic_context = get_diagnostic_context()

    # Add action history so Claude knows what's been tried
    if state["actions_tried"]:
        diagnostic_context += f"\n\n=== PREVIOUSLY TRIED ACTIONS (all failed) ===\n"
        diagnostic_context += "\n".join(f"- {a}" for a in state["actions_tried"])

    # Consult Claude
    logger.info("Consulting Claude advisor...")
    recommendation = get_recommendation(actionable_fails, diagnostic_context)

    if not recommendation:
        logger.error("No recommendation — defaulting to restart_all")
        recommendation = {"action": "restart_all", "reasoning": "Fallback", "confidence": 0.3}

    action = recommendation["action"]

    # Don't repeat an action that already failed
    if action in state["actions_tried"] and action not in ("escalate", "no_action"):
        logger.warning(f"Action '{action}' already tried — Claude should have picked something else")
        # Escalate the action
        action_order = ["restart_gateway", "restart_all", "revert_workspace_commit", "revert_openclaw_commit", "reboot_vm", "escalate"]
        for next_action in action_order:
            if next_action not in state["actions_tried"]:
                logger.info(f"Escalating to: {next_action}")
                action = next_action
                break
        else:
            action = "escalate"

    logger.info(f"Executing: {action} (confidence: {recommendation.get('confidence', '?')})")
    logger.info(f"Reasoning: {recommendation.get('reasoning', 'N/A')}")

    success = execute_action(action)
    state["actions_tried"].append(action)
    state["last_action"] = action
    state["last_action_time"] = datetime.now(timezone.utc).isoformat()

    if success:
        logger.info(f"Recovery '{action}' succeeded!")
        # Verify
        time.sleep(10)
        verify = run_all_checks()
        verify_failed = [r for r in verify if not r.passed and r.severity in ("critical", "warning")]
        if not verify_failed:
            logger.info("Post-recovery: all critical/warning checks passing!")
            state["consecutive_failures"] = 0
            state["actions_tried"] = []
        else:
            logger.warning(f"Post-recovery: {len(verify_failed)} critical/warning checks still failing")
    else:
        logger.error(f"Recovery '{action}' FAILED")

    save_state(state)

    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
    logger.info(f"Watchdog analysis completed in {elapsed:.1f}s")


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        logger.exception(f"Watchdog crashed: {e}")
        sys.exit(1)
