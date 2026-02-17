"""Health checks for the openclaw-server VM."""

import subprocess
import requests
import logging
from dataclasses import dataclass, field
from typing import Optional

import config

logger = logging.getLogger("watchdog.health")


@dataclass
class CheckResult:
    name: str
    passed: bool
    details: str
    error: Optional[str] = None


def _ssh_cmd(command: str, timeout: int = config.SSH_TIMEOUT) -> subprocess.CompletedProcess:
    """Run a command on the target VM via SSH."""
    ssh_args = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout={}".format(timeout),
        "-o", "BatchMode=yes",
        "-i", config.SSH_KEY_PATH,
        f"{config.SSH_USER}@{config.TARGET_IP}",
        command,
    ]
    return subprocess.run(ssh_args, capture_output=True, text=True, timeout=timeout + 5)


def check_ssh_reachable() -> CheckResult:
    """Check if we can SSH into the target VM."""
    try:
        result = _ssh_cmd("echo ok")
        if result.returncode == 0 and "ok" in result.stdout:
            return CheckResult("ssh_reachable", True, "SSH connection successful")
        return CheckResult("ssh_reachable", False, "SSH failed", result.stderr.strip())
    except subprocess.TimeoutExpired:
        return CheckResult("ssh_reachable", False, "SSH timed out", "Connection timed out")
    except Exception as e:
        return CheckResult("ssh_reachable", False, "SSH error", str(e))


def check_docker_running() -> CheckResult:
    """Check if OpenClaw Docker containers are running."""
    try:
        result = _ssh_cmd("docker ps --filter name=openclaw --format '{{.Names}} {{.Status}}'", timeout=15)
        if result.returncode != 0:
            return CheckResult("docker_running", False, "docker ps failed", result.stderr.strip())
        output = result.stdout.strip()
        if not output:
            return CheckResult("docker_running", False, "No openclaw containers found", "No containers matching 'openclaw'")
        running = all("Up" in line for line in output.splitlines() if line.strip())
        if running:
            return CheckResult("docker_running", True, f"Containers running: {output}")
        return CheckResult("docker_running", False, f"Containers not healthy: {output}", output)
    except subprocess.TimeoutExpired:
        return CheckResult("docker_running", False, "Docker check timed out", "Timeout")
    except Exception as e:
        return CheckResult("docker_running", False, "Docker check error", str(e))


def check_control_ui() -> CheckResult:
    """Check if the OpenClaw control UI is responding (via SSH since it listens on localhost)."""
    try:
        result = _ssh_cmd(
            f"curl -s -o /dev/null -w '%{{http_code}}' http://localhost:{config.CONTROL_UI_PORT}/ 2>&1",
            timeout=15,
        )
        if result.returncode != 0:
            return CheckResult("control_ui", False, "Control UI check failed", result.stderr.strip())
        status_code = result.stdout.strip()
        if status_code == "200":
            return CheckResult("control_ui", True, f"Control UI responding (HTTP {status_code})")
        return CheckResult("control_ui", False, f"Control UI returned HTTP {status_code}", f"HTTP {status_code}")
    except subprocess.TimeoutExpired:
        return CheckResult("control_ui", False, "Control UI check timed out", "Timeout")
    except Exception as e:
        return CheckResult("control_ui", False, "Control UI check error", str(e))


def check_whatsapp_connection() -> CheckResult:
    """Check OpenClaw logs for WhatsApp connection status."""
    try:
        result = _ssh_cmd(
            f"docker logs --tail 50 $(docker ps -q --filter name=openclaw) 2>&1 | grep -iE '(whatsapp|baileys|connection|disconnect|error)' | tail -10",
            timeout=15,
        )
        if result.returncode != 0:
            return CheckResult("whatsapp", False, "Could not read container logs", result.stderr.strip())
        output = result.stdout.strip()
        if not output:
            return CheckResult("whatsapp", True, "No connection issues in recent logs")
        lower = output.lower()
        if "disconnect" in lower or "error" in lower or "fatal" in lower:
            return CheckResult("whatsapp", False, f"WhatsApp issues detected", output[-500:])
        return CheckResult("whatsapp", True, f"WhatsApp logs look OK: {output[-200:]}")
    except subprocess.TimeoutExpired:
        return CheckResult("whatsapp", False, "WhatsApp check timed out", "Timeout")
    except Exception as e:
        return CheckResult("whatsapp", False, "WhatsApp check error", str(e))


def check_disk_space() -> CheckResult:
    """Check disk usage on the target VM."""
    try:
        result = _ssh_cmd("df -h / | tail -1 | awk '{print $5}'")
        if result.returncode != 0:
            return CheckResult("disk_space", False, "df command failed", result.stderr.strip())
        usage_str = result.stdout.strip().replace("%", "")
        try:
            usage = int(usage_str)
        except ValueError:
            return CheckResult("disk_space", False, f"Could not parse disk usage: {result.stdout.strip()}")
        if usage >= config.DISK_USAGE_THRESHOLD:
            return CheckResult("disk_space", False, f"Disk usage critical: {usage}%", f"{usage}% used")
        return CheckResult("disk_space", True, f"Disk usage OK: {usage}%")
    except subprocess.TimeoutExpired:
        return CheckResult("disk_space", False, "Disk check timed out", "Timeout")
    except Exception as e:
        return CheckResult("disk_space", False, "Disk check error", str(e))


def check_resources() -> CheckResult:
    """Check memory and CPU on the target VM."""
    try:
        result = _ssh_cmd("free -m | grep Mem | awk '{printf \"%.0f\", $3/$2*100}' && echo '' && uptime | awk -F'load average:' '{print $2}'")
        if result.returncode != 0:
            return CheckResult("resources", False, "Resource check failed", result.stderr.strip())
        lines = result.stdout.strip().splitlines()
        mem_pct = int(lines[0]) if lines else 0
        load = lines[1].strip() if len(lines) > 1 else "unknown"
        if mem_pct >= config.MEMORY_USAGE_THRESHOLD:
            return CheckResult("resources", False, f"Memory critical: {mem_pct}%, load: {load}", f"Memory {mem_pct}%")
        return CheckResult("resources", True, f"Memory: {mem_pct}%, load: {load}")
    except subprocess.TimeoutExpired:
        return CheckResult("resources", False, "Resource check timed out", "Timeout")
    except Exception as e:
        return CheckResult("resources", False, "Resource check error", str(e))


def run_all_checks() -> list[CheckResult]:
    """Run all health checks and return results."""
    checks = [
        check_ssh_reachable,
        check_docker_running,
        check_control_ui,
        check_whatsapp_connection,
        check_disk_space,
        check_resources,
    ]
    results = []
    for check_fn in checks:
        logger.info(f"Running check: {check_fn.__name__}")
        result = check_fn()
        logger.info(f"  {result.name}: {'PASS' if result.passed else 'FAIL'} — {result.details}")
        results.append(result)
        # If SSH is unreachable, skip remaining SSH-dependent checks
        if result.name == "ssh_reachable" and not result.passed:
            logger.warning("SSH unreachable — skipping SSH-dependent checks")
            for remaining in checks[checks.index(check_fn) + 1:]:
                if remaining != check_control_ui:
                    results.append(CheckResult(remaining.__name__.replace("check_", ""), False, "Skipped (SSH unreachable)"))
            # Still try control UI since it's HTTP-based
            results.append(check_control_ui())
            break
    return results
