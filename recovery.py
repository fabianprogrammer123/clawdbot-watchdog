"""Recovery actions for the openclaw-server VM."""

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
        "-o", "ConnectTimeout={}".format(config.SSH_TIMEOUT),
        "-o", "BatchMode=yes",
        "-i", config.SSH_KEY_PATH,
        f"{config.SSH_USER}@{config.TARGET_IP}",
        command,
    ]
    return subprocess.run(ssh_args, capture_output=True, text=True, timeout=timeout)


def restart_containers() -> bool:
    """Restart Docker containers on the target VM."""
    logger.info("ACTION: Restarting Docker containers...")
    try:
        # Stop containers
        result = _ssh_cmd(f"cd {config.OPENCLAW_DIR} && docker compose down", timeout=60)
        if result.returncode != 0:
            logger.warning(f"docker compose down stderr: {result.stderr}")

        # Wait a moment
        time.sleep(5)

        # Start containers
        result = _ssh_cmd(f"cd {config.OPENCLAW_DIR} && docker compose up -d", timeout=60)
        if result.returncode != 0:
            logger.error(f"docker compose up failed: {result.stderr}")
            return False

        logger.info(f"Containers restarted. stdout: {result.stdout.strip()}")

        # Wait for containers to stabilize
        time.sleep(10)

        # Verify containers are running
        verify = _ssh_cmd("docker ps --filter name=openclaw --format '{{.Names}} {{.Status}}'")
        if verify.returncode == 0 and "Up" in verify.stdout:
            logger.info(f"Containers verified running: {verify.stdout.strip()}")
            return True
        else:
            logger.error(f"Containers not running after restart: {verify.stdout}")
            return False

    except subprocess.TimeoutExpired:
        logger.error("Container restart timed out")
        return False
    except Exception as e:
        logger.error(f"Container restart error: {e}")
        return False


def revert_last_commit() -> bool:
    """Revert the last git commit on the target VM and restart."""
    logger.info("ACTION: Reverting last commit on target VM...")
    try:
        # Show what we're reverting
        log_result = _ssh_cmd(f"cd {config.OPENCLAW_DIR} && git log --oneline -3")
        if log_result.returncode == 0:
            logger.info(f"Recent commits:\n{log_result.stdout.strip()}")

        # Revert HEAD
        result = _ssh_cmd(f"cd {config.OPENCLAW_DIR} && git revert HEAD --no-edit", timeout=30)
        if result.returncode != 0:
            logger.error(f"Git revert failed: {result.stderr}")
            # Try to abort if revert left a bad state
            _ssh_cmd(f"cd {config.OPENCLAW_DIR} && git revert --abort")
            return False

        logger.info(f"Git revert successful: {result.stdout.strip()}")

        # Rebuild and restart containers
        rebuild = _ssh_cmd(f"cd {config.OPENCLAW_DIR} && docker compose down && docker compose build && docker compose up -d", timeout=120)
        if rebuild.returncode != 0:
            logger.error(f"Rebuild after revert failed: {rebuild.stderr}")
            return False

        logger.info("Revert + rebuild completed")
        return True

    except subprocess.TimeoutExpired:
        logger.error("Revert operation timed out")
        return False
    except Exception as e:
        logger.error(f"Revert error: {e}")
        return False


def reboot_vm() -> bool:
    """Reboot the target VM via GCP API."""
    logger.info("ACTION: Rebooting target VM via gcloud...")
    try:
        result = subprocess.run(
            [
                "gcloud", "compute", "instances", "reset",
                config.TARGET_INSTANCE,
                f"--zone={config.GCP_ZONE}",
                f"--project={config.GCP_PROJECT}",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            logger.error(f"gcloud reset failed: {result.stderr}")
            return False

        logger.info(f"VM reboot initiated: {result.stdout.strip()}")

        # Wait for VM to come back up
        logger.info("Waiting 60s for VM to boot...")
        time.sleep(60)

        # Check if SSH is back
        for attempt in range(6):
            try:
                check = _ssh_cmd("echo ok", timeout=10)
                if check.returncode == 0:
                    logger.info(f"VM back online after reboot (attempt {attempt + 1})")
                    return True
            except Exception:
                pass
            time.sleep(10)

        logger.error("VM did not come back after reboot within timeout")
        return False

    except subprocess.TimeoutExpired:
        logger.error("VM reboot timed out")
        return False
    except Exception as e:
        logger.error(f"VM reboot error: {e}")
        return False


def execute_action(action: str) -> bool:
    """Execute a recovery action by name. Returns True if successful."""
    actions = {
        "restart_containers": restart_containers,
        "revert_last_commit": revert_last_commit,
        "reboot_vm": reboot_vm,
        "escalate": lambda: (logger.warning("ESCALATE: Manual intervention required"), True)[1],
        "no_action": lambda: (logger.info("No action needed"), True)[1],
    }

    handler = actions.get(action)
    if not handler:
        logger.error(f"Unknown action: {action}")
        return False

    return handler()
