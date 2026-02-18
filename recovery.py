"""Recovery actions for ClawdBot on openclaw-server.

Actions are ordered from least to most disruptive.
Each action returns True if it succeeded."""

import subprocess
import logging
import time

import config

logger = logging.getLogger("watchdog.recovery")


def _ssh_cmd(command: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run a command on the target VM via SSH."""
    ssh_args = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", f"ConnectTimeout={config.SSH_TIMEOUT}",
        "-o", "BatchMode=yes",
        "-i", config.SSH_KEY_PATH,
        f"{config.SSH_USER}@{config.TARGET_IP}",
        command,
    ]
    return subprocess.run(ssh_args, capture_output=True, text=True, timeout=timeout)


def _wait_for_whatsapp(max_wait: int = 60) -> bool:
    """Wait for WhatsApp to reconnect after a restart."""
    logger.info(f"Waiting up to {max_wait}s for WhatsApp reconnection...")
    for attempt in range(max_wait // 5):
        time.sleep(5)
        try:
            result = _ssh_cmd(
                f"docker logs --tail 30 {config.GATEWAY_CONTAINER} 2>&1 | grep -c 'Listening for.*inbound messages'",
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip() != "0":
                logger.info(f"WhatsApp reconnected (attempt {attempt + 1})")
                return True
        except Exception:
            pass
    logger.warning("WhatsApp did not reconnect within timeout")
    return False


def restart_gateway() -> bool:
    """Restart only the gateway container (least disruptive)."""
    logger.info("ACTION: Restarting gateway container...")
    try:
        result = _ssh_cmd(
            f"cd {config.OPENCLAW_DIR} && docker compose restart openclaw-gateway",
            timeout=60,
        )
        if result.returncode != 0:
            logger.error(f"Gateway restart failed: {result.stderr}")
            return False

        logger.info("Gateway container restarted, waiting for boot...")
        time.sleep(config.GATEWAY_BOOT_SECONDS)
        return _wait_for_whatsapp(max_wait=60)

    except subprocess.TimeoutExpired:
        logger.error("Gateway restart timed out")
        return False
    except Exception as e:
        logger.error(f"Gateway restart error: {e}")
        return False


def restart_all() -> bool:
    """Full docker compose down/up (preserves volumes and env)."""
    logger.info("ACTION: Full restart (docker compose down/up)...")
    try:
        _ssh_cmd(f"cd {config.OPENCLAW_DIR} && docker compose down", timeout=60)
        time.sleep(5)

        result = _ssh_cmd(f"cd {config.OPENCLAW_DIR} && docker compose up -d", timeout=60)
        if result.returncode != 0:
            logger.error(f"docker compose up failed: {result.stderr}")
            return False

        logger.info("Containers started, waiting for gateway boot...")
        time.sleep(config.GATEWAY_BOOT_SECONDS)
        return _wait_for_whatsapp(max_wait=90)

    except subprocess.TimeoutExpired:
        logger.error("Full restart timed out")
        return False
    except Exception as e:
        logger.error(f"Full restart error: {e}")
        return False


def revert_workspace_commit() -> bool:
    """Revert ClawdBot's last self-modification in the workspace repo, then restart."""
    logger.info("ACTION: Reverting last workspace commit (ClawdBot self-edit)...")
    try:
        # Show what we're reverting
        log_result = _ssh_cmd(f"cd {config.WORKSPACE_DIR} && git log --oneline -5 2>&1", timeout=10)
        if log_result.returncode == 0:
            logger.info(f"Workspace recent commits:\n{log_result.stdout.strip()}")

        # Revert
        result = _ssh_cmd(f"cd {config.WORKSPACE_DIR} && git revert HEAD --no-edit 2>&1", timeout=30)
        if result.returncode != 0:
            logger.error(f"Workspace revert failed: {result.stderr}")
            _ssh_cmd(f"cd {config.WORKSPACE_DIR} && git revert --abort 2>&1")
            return False

        logger.info(f"Workspace revert successful: {result.stdout.strip()}")

        # Restart gateway to pick up changes
        return restart_gateway()

    except subprocess.TimeoutExpired:
        logger.error("Workspace revert timed out")
        return False
    except Exception as e:
        logger.error(f"Workspace revert error: {e}")
        return False


def revert_openclaw_commit() -> bool:
    """Revert the last commit in the openclaw deployment, rebuild, and restart."""
    logger.info("ACTION: Reverting last openclaw deploy commit...")
    try:
        log_result = _ssh_cmd(f"cd {config.OPENCLAW_DIR} && git log --oneline -5 2>&1", timeout=10)
        if log_result.returncode == 0:
            logger.info(f"OpenClaw recent commits:\n{log_result.stdout.strip()}")

        result = _ssh_cmd(f"cd {config.OPENCLAW_DIR} && git revert HEAD --no-edit 2>&1", timeout=30)
        if result.returncode != 0:
            logger.error(f"OpenClaw revert failed: {result.stderr}")
            _ssh_cmd(f"cd {config.OPENCLAW_DIR} && git revert --abort 2>&1")
            return False

        logger.info("OpenClaw revert successful, rebuilding...")
        return restart_all()

    except subprocess.TimeoutExpired:
        logger.error("OpenClaw revert timed out")
        return False
    except Exception as e:
        logger.error(f"OpenClaw revert error: {e}")
        return False


def reboot_vm() -> bool:
    """Reboot the target VM via GCP API — last resort."""
    logger.info("ACTION: Rebooting target VM via gcloud...")
    try:
        result = subprocess.run(
            [
                "gcloud", "compute", "instances", "reset",
                config.TARGET_INSTANCE,
                f"--zone={config.GCP_ZONE}",
                f"--project={config.GCP_PROJECT}",
            ],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            logger.error(f"gcloud reset failed: {result.stderr}")
            return False

        logger.info("VM reboot initiated, waiting 90s for boot...")
        time.sleep(90)

        # Wait for SSH
        for attempt in range(6):
            try:
                check = _ssh_cmd("echo ok", timeout=10)
                if check.returncode == 0:
                    logger.info(f"VM back online (attempt {attempt + 1})")
                    # Docker should auto-start containers (restart: unless-stopped)
                    time.sleep(config.GATEWAY_BOOT_SECONDS)
                    return _wait_for_whatsapp(max_wait=90)
            except Exception:
                pass
            time.sleep(15)

        logger.error("VM did not come back after reboot")
        return False

    except subprocess.TimeoutExpired:
        logger.error("VM reboot timed out")
        return False
    except Exception as e:
        logger.error(f"VM reboot error: {e}")
        return False


def execute_action(action: str) -> bool:
    """Execute a recovery action by name."""
    actions = {
        "restart_gateway": restart_gateway,
        "restart_all": restart_all,
        "revert_workspace_commit": revert_workspace_commit,
        "revert_openclaw_commit": revert_openclaw_commit,
        "reboot_vm": reboot_vm,
        "escalate": lambda: (logger.warning("ESCALATE: Manual intervention required"), True)[1],
        "no_action": lambda: (logger.info("No action needed"), True)[1],
    }

    handler = actions.get(action)
    if not handler:
        logger.error(f"Unknown action: {action}")
        return False

    return handler()
