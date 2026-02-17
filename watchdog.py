#!/usr/bin/env python3
"""
Watchdog — OpenClaw health monitor and auto-recovery agent.

Runs via cron every 5 minutes. Checks the health of the openclaw-server VM,
consults Claude API when issues are detected, and executes recovery actions.
"""

import json
import logging
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import config
from health_check import run_all_checks, CheckResult
from claude_advisor import get_recommendation
from recovery import execute_action

# Set up logging
logger = logging.getLogger("watchdog")
logger.setLevel(logging.INFO)

# File handler with rotation
file_handler = RotatingFileHandler(
    config.LOG_FILE,
    maxBytes=5 * 1024 * 1024,  # 5MB
    backupCount=5,
)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))
logger.addHandler(file_handler)

# Console handler for manual runs
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
))
logger.addHandler(console_handler)


def load_state() -> dict:
    """Load persistent state (consecutive failure count, last action, etc.)."""
    if config.STATE_FILE.exists():
        try:
            return json.loads(config.STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"consecutive_failures": 0, "last_action": None, "last_action_time": None}


def save_state(state: dict):
    """Save persistent state."""
    config.STATE_FILE.write_text(json.dumps(state, indent=2))


def run():
    """Main watchdog loop — run once per invocation."""
    start_time = datetime.now(timezone.utc)
    logger.info("=" * 60)
    logger.info(f"Watchdog run started at {start_time.isoformat()}")

    state = load_state()

    # Run all health checks
    results = run_all_checks()
    passed = [r for r in results if r.passed]
    failed = [r for r in results if not r.passed]

    logger.info(f"Health check summary: {len(passed)} passed, {len(failed)} failed")

    if not failed:
        # All checks passed
        logger.info("All checks passed — system healthy")
        state["consecutive_failures"] = 0
        state["last_action"] = None
        save_state(state)
        logger.info(f"Watchdog run completed in {(datetime.now(timezone.utc) - start_time).total_seconds():.1f}s")
        return

    # Some checks failed
    state["consecutive_failures"] += 1
    logger.warning(f"Consecutive failure count: {state['consecutive_failures']}")

    for r in failed:
        logger.warning(f"  FAILED: {r.name} — {r.details}" + (f" ({r.error})" if r.error else ""))

    # Check if we should act or wait
    if state["consecutive_failures"] < config.MAX_CONSECUTIVE_FAILURES:
        logger.info(f"Below failure threshold ({state['consecutive_failures']}/{config.MAX_CONSECUTIVE_FAILURES}) — waiting for next check")
        save_state(state)
        return

    # Threshold reached — consult Claude and take action
    logger.info("Failure threshold reached — consulting Claude advisor...")

    context_parts = []
    if state.get("last_action"):
        context_parts.append(f"Last recovery action was '{state['last_action']}' at {state.get('last_action_time', 'unknown')}")
    context = "; ".join(context_parts)

    recommendation = get_recommendation(failed, context)

    if not recommendation:
        logger.error("Could not get recommendation — defaulting to container restart")
        recommendation = {"action": "restart_containers", "reasoning": "Fallback default", "confidence": 0.3}

    action = recommendation["action"]
    logger.info(f"Recommended action: {action} (confidence: {recommendation.get('confidence', '?')})")
    logger.info(f"Reasoning: {recommendation.get('reasoning', 'N/A')}")

    # Don't repeat the same action if it just failed
    if action == state.get("last_action") and action not in ("escalate", "no_action"):
        logger.warning(f"Same action '{action}' was tried last time — escalating instead")
        action = "escalate" if action != "reboot_vm" else "reboot_vm"

    # Execute recovery
    logger.info(f"Executing recovery action: {action}")
    success = execute_action(action)

    state["last_action"] = action
    state["last_action_time"] = datetime.now(timezone.utc).isoformat()

    if success:
        logger.info(f"Recovery action '{action}' completed successfully")

        # Re-run health checks to verify
        if action not in ("escalate", "no_action"):
            logger.info("Re-running health checks to verify recovery...")
            time.sleep(5)
            verify_results = run_all_checks()
            verify_failed = [r for r in verify_results if not r.passed]

            if not verify_failed:
                logger.info("Post-recovery checks all passed — system recovered!")
                state["consecutive_failures"] = 0
            else:
                logger.warning(f"Post-recovery: {len(verify_failed)} checks still failing")
                for r in verify_failed:
                    logger.warning(f"  Still failing: {r.name} — {r.details}")
        else:
            state["consecutive_failures"] = 0
    else:
        logger.error(f"Recovery action '{action}' FAILED")

    save_state(state)

    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
    logger.info(f"Watchdog run completed in {elapsed:.1f}s")


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        logger.exception(f"Watchdog crashed: {e}")
        sys.exit(1)
