"""Health checks for the openclaw-server VM — ClawdBot specific."""

import subprocess
import re
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

import config

logger = logging.getLogger("watchdog.health")


@dataclass
class CheckResult:
    name: str
    passed: bool
    details: str
    severity: str = "info"
    error: Optional[str] = None


def _ssh_cmd(command: str, timeout: int = config.SSH_TIMEOUT) -> subprocess.CompletedProcess:
    """Run a command on the target VM via SSH."""
    ssh_args = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", f"ConnectTimeout={timeout}",
        "-o", "BatchMode=yes",
        "-i", config.SSH_KEY_PATH,
        f"{config.SSH_USER}@{config.TARGET_IP}",
        command,
    ]
    return subprocess.run(ssh_args, capture_output=True, text=True, timeout=timeout + 5)


def _severity(name: str) -> str:
    return config.CHECK_SEVERITY.get(name, "info")


def check_ssh_reachable() -> CheckResult:
    """Check if we can SSH into the target VM."""
    name = "ssh_reachable"
    try:
        result = _ssh_cmd("echo ok")
        if result.returncode == 0 and "ok" in result.stdout:
            return CheckResult(name, True, "SSH connection successful", _severity(name))
        return CheckResult(name, False, "SSH failed", _severity(name), result.stderr.strip())
    except subprocess.TimeoutExpired:
        return CheckResult(name, False, "SSH timed out", _severity(name), "Connection timed out")
    except Exception as e:
        return CheckResult(name, False, "SSH error", _severity(name), str(e))


def check_gateway_running() -> CheckResult:
    """Check if the OpenClaw gateway container is running (the critical one)."""
    name = "gateway_running"
    try:
        result = _ssh_cmd(
            f"docker inspect --format '{{{{.State.Status}}}} {{{{.State.StartedAt}}}}' {config.GATEWAY_CONTAINER} 2>&1",
            timeout=15,
        )
        if result.returncode != 0:
            return CheckResult(name, False, "Gateway container not found", _severity(name), result.stderr.strip())
        output = result.stdout.strip()
        if output.startswith("running"):
            return CheckResult(name, True, f"Gateway container: {output}", _severity(name))
        return CheckResult(name, False, f"Gateway not running: {output}", _severity(name), output)
    except subprocess.TimeoutExpired:
        return CheckResult(name, False, "Docker check timed out", _severity(name), "Timeout")
    except Exception as e:
        return CheckResult(name, False, "Docker check error", _severity(name), str(e))


def check_whatsapp_connected() -> CheckResult:
    """Check WhatsApp connection via gateway logs — look for Baileys connection status."""
    name = "whatsapp_connected"
    try:
        result = _ssh_cmd(
            f"docker logs --tail 200 {config.GATEWAY_CONTAINER} 2>&1 | grep -i '\\[whatsapp\\]' | tail -15",
            timeout=15,
        )
        if result.returncode != 0:
            return CheckResult(name, False, "Could not read gateway logs", _severity(name), result.stderr.strip())
        output = result.stdout.strip()
        if not output:
            return CheckResult(name, False, "No WhatsApp log entries found", _severity(name), "No [whatsapp] lines in last 200 log lines")

        lower = output.lower()
        # Check for healthy signals
        if "listening for" in lower and "inbound messages" in lower:
            # Check for subsequent disconnects
            lines = output.splitlines()
            last_connect_idx = -1
            last_disconnect_idx = -1
            for i, line in enumerate(lines):
                ll = line.lower()
                if "listening for" in ll or "starting provider" in ll:
                    last_connect_idx = i
                if "disconnect" in ll or "connection closed" in ll or "logout" in ll:
                    last_disconnect_idx = i
            if last_connect_idx > last_disconnect_idx:
                return CheckResult(name, True, f"WhatsApp connected: {lines[-1].strip()}", _severity(name))
            else:
                return CheckResult(name, False, "WhatsApp disconnected after connecting", _severity(name), lines[-1].strip())

        if "disconnect" in lower or "connection closed" in lower or "logout" in lower or "qr code" in lower:
            return CheckResult(name, False, "WhatsApp connection issue", _severity(name), output[-500:])

        return CheckResult(name, True, f"WhatsApp logs look OK", _severity(name))
    except subprocess.TimeoutExpired:
        return CheckResult(name, False, "WhatsApp check timed out", _severity(name), "Timeout")
    except Exception as e:
        return CheckResult(name, False, "WhatsApp check error", _severity(name), str(e))


def check_control_ui() -> CheckResult:
    """Check if the OpenClaw control UI is responding (via SSH since it listens on localhost)."""
    name = "control_ui"
    try:
        result = _ssh_cmd(
            f"curl -s -o /dev/null -w '%{{http_code}}' http://localhost:{config.CONTROL_UI_PORT}/ 2>&1",
            timeout=15,
        )
        if result.returncode != 0:
            return CheckResult(name, False, "Control UI check failed", _severity(name), result.stderr.strip())
        status_code = result.stdout.strip()
        if status_code == "200":
            return CheckResult(name, True, f"Control UI responding (HTTP {status_code})", _severity(name))
        return CheckResult(name, False, f"Control UI returned HTTP {status_code}", _severity(name), f"HTTP {status_code}")
    except subprocess.TimeoutExpired:
        return CheckResult(name, False, "Control UI check timed out", _severity(name), "Timeout")
    except Exception as e:
        return CheckResult(name, False, "Control UI check error", _severity(name), str(e))


def check_website_serving() -> CheckResult:
    """Check if nginx is serving the Axiom website on port 80."""
    name = "website_serving"
    try:
        result = _ssh_cmd(
            "curl -s -o /dev/null -w '%{http_code}' http://localhost:80/ 2>&1",
            timeout=15,
        )
        if result.returncode != 0:
            return CheckResult(name, False, "Website check failed", _severity(name), result.stderr.strip())
        status_code = result.stdout.strip()
        if status_code in ("200", "304"):
            return CheckResult(name, True, f"Website serving (HTTP {status_code})", _severity(name))
        return CheckResult(name, False, f"Website returned HTTP {status_code}", _severity(name))
    except subprocess.TimeoutExpired:
        return CheckResult(name, False, "Website check timed out", _severity(name), "Timeout")
    except Exception as e:
        return CheckResult(name, False, "Website check error", _severity(name), str(e))


def check_heartbeat_fresh() -> CheckResult:
    """Check if ClawdBot's internal heartbeat is still firing (every 30 min)."""
    name = "heartbeat_fresh"
    try:
        result = _ssh_cmd(
            f"docker logs --tail 500 {config.GATEWAY_CONTAINER} 2>&1 | grep '\\[heartbeat\\]' | tail -1",
            timeout=15,
        )
        if result.returncode != 0:
            return CheckResult(name, False, "Could not read heartbeat logs", _severity(name), result.stderr.strip())
        output = result.stdout.strip()
        if not output:
            return CheckResult(name, False, "No heartbeat entries found in recent logs", _severity(name))

        # Try to extract timestamp from log line (format: 2026-02-17T23:48:07.029Z)
        ts_match = re.search(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})', output)
        if ts_match:
            last_beat = datetime.fromisoformat(ts_match.group(1)).replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - last_beat
            if age < timedelta(minutes=config.HEARTBEAT_STALE_MINUTES):
                return CheckResult(name, True, f"Heartbeat {int(age.total_seconds() / 60)}m ago", _severity(name))
            return CheckResult(name, False, f"Heartbeat stale ({int(age.total_seconds() / 60)}m ago)", _severity(name))

        return CheckResult(name, True, f"Heartbeat found: {output[-100:]}", _severity(name))
    except subprocess.TimeoutExpired:
        return CheckResult(name, False, "Heartbeat check timed out", _severity(name), "Timeout")
    except Exception as e:
        return CheckResult(name, False, "Heartbeat check error", _severity(name), str(e))


def check_dashboard_fresh() -> CheckResult:
    """Check if the dashboard collector is still updating data.json."""
    name = "dashboard_fresh"
    try:
        result = _ssh_cmd(
            f"stat -c '%Y' {config.DASHBOARD_DATA} 2>/dev/null && date +%s",
            timeout=10,
        )
        if result.returncode != 0:
            return CheckResult(name, False, "Dashboard data file not found", _severity(name))
        lines = result.stdout.strip().splitlines()
        if len(lines) >= 2:
            file_ts = int(lines[0])
            now_ts = int(lines[1])
            age_min = (now_ts - file_ts) / 60
            if age_min < config.DASHBOARD_STALE_MINUTES:
                return CheckResult(name, True, f"Dashboard updated {age_min:.0f}m ago", _severity(name))
            return CheckResult(name, False, f"Dashboard stale ({age_min:.0f}m ago)", _severity(name))
        return CheckResult(name, False, "Could not parse dashboard timestamps", _severity(name))
    except subprocess.TimeoutExpired:
        return CheckResult(name, False, "Dashboard check timed out", _severity(name), "Timeout")
    except Exception as e:
        return CheckResult(name, False, "Dashboard check error", _severity(name), str(e))


def check_disk_space() -> CheckResult:
    """Check disk usage on the target VM."""
    name = "disk_space"
    try:
        result = _ssh_cmd("df -h / | tail -1 | awk '{print $5}'")
        if result.returncode != 0:
            return CheckResult(name, False, "df command failed", _severity(name), result.stderr.strip())
        usage_str = result.stdout.strip().replace("%", "")
        try:
            usage = int(usage_str)
        except ValueError:
            return CheckResult(name, False, f"Could not parse disk usage: {result.stdout.strip()}", _severity(name))
        if usage >= config.DISK_USAGE_THRESHOLD:
            return CheckResult(name, False, f"Disk usage critical: {usage}%", _severity(name), f"{usage}% used")
        return CheckResult(name, True, f"Disk usage OK: {usage}%", _severity(name))
    except subprocess.TimeoutExpired:
        return CheckResult(name, False, "Disk check timed out", _severity(name), "Timeout")
    except Exception as e:
        return CheckResult(name, False, "Disk check error", _severity(name), str(e))


def check_resources() -> CheckResult:
    """Check memory and CPU on the target VM."""
    name = "resources"
    try:
        result = _ssh_cmd("free -m | grep Mem | awk '{printf \"%.0f\", $3/$2*100}' && echo '' && uptime | awk -F'load average:' '{print $2}'")
        if result.returncode != 0:
            return CheckResult(name, False, "Resource check failed", _severity(name), result.stderr.strip())
        lines = result.stdout.strip().splitlines()
        mem_pct = int(lines[0]) if lines else 0
        load = lines[1].strip() if len(lines) > 1 else "unknown"
        if mem_pct >= config.MEMORY_USAGE_THRESHOLD:
            return CheckResult(name, False, f"Memory critical: {mem_pct}%, load: {load}", _severity(name), f"Memory {mem_pct}%")
        return CheckResult(name, True, f"Memory: {mem_pct}%, load: {load}", _severity(name))
    except subprocess.TimeoutExpired:
        return CheckResult(name, False, "Resource check timed out", _severity(name), "Timeout")
    except Exception as e:
        return CheckResult(name, False, "Resource check error", _severity(name), str(e))


def get_diagnostic_context() -> str:
    """Gather rich diagnostic info for Claude when analysis is needed.
    Only called on failures — this is the expensive data gathering step."""
    parts = []

    # Recent gateway logs
    try:
        result = _ssh_cmd(f"docker logs --tail 80 {config.GATEWAY_CONTAINER} 2>&1", timeout=20)
        if result.returncode == 0:
            parts.append(f"=== GATEWAY LOGS (last 80 lines) ===\n{result.stdout[-3000:]}")
    except Exception:
        parts.append("=== GATEWAY LOGS: unavailable ===")

    # Recent git commits in the workspace (ClawdBot's self-modifications)
    try:
        result = _ssh_cmd(f"cd {config.WORKSPACE_DIR} && git log --oneline -10 2>&1", timeout=10)
        if result.returncode == 0:
            parts.append(f"=== WORKSPACE GIT LOG (last 10 commits) ===\n{result.stdout}")
    except Exception:
        pass

    # Recent git commits in the openclaw deployment
    try:
        result = _ssh_cmd(f"cd {config.OPENCLAW_DIR} && git log --oneline -5 2>&1", timeout=10)
        if result.returncode == 0:
            parts.append(f"=== OPENCLAW DEPLOY GIT LOG ===\n{result.stdout}")
    except Exception:
        pass

    # Docker container states
    try:
        result = _ssh_cmd("docker ps -a --format 'table {{.Names}}\\t{{.Status}}\\t{{.Ports}}' 2>&1", timeout=10)
        if result.returncode == 0:
            parts.append(f"=== ALL CONTAINERS ===\n{result.stdout}")
    except Exception:
        pass

    # Recent restart count
    try:
        result = _ssh_cmd(f"docker inspect --format '{{{{.RestartCount}}}}' {config.GATEWAY_CONTAINER} 2>&1", timeout=10)
        if result.returncode == 0:
            parts.append(f"=== RESTART COUNT: {result.stdout.strip()} ===")
    except Exception:
        pass

    return "\n\n".join(parts)


def run_all_checks() -> list[CheckResult]:
    """Run all health checks and return results."""
    results = []

    # SSH first — if it fails, skip SSH-dependent checks
    ssh_result = check_ssh_reachable()
    results.append(ssh_result)

    if not ssh_result.passed:
        logger.warning("SSH unreachable — skipping SSH-dependent checks")
        for name in ["gateway_running", "whatsapp_connected", "control_ui",
                      "website_serving", "heartbeat_fresh", "dashboard_fresh",
                      "disk_space", "resources"]:
            results.append(CheckResult(name, False, "Skipped (SSH unreachable)", _severity(name)))
        return results

    # Run remaining checks
    checks = [
        check_gateway_running,
        check_whatsapp_connected,
        check_control_ui,
        check_website_serving,
        check_heartbeat_fresh,
        check_dashboard_fresh,
        check_disk_space,
        check_resources,
    ]
    for check_fn in checks:
        result = check_fn()
        logger.info(f"  {result.name}: {'PASS' if result.passed else 'FAIL'} [{result.severity}] — {result.details}")
        results.append(result)

    return results
