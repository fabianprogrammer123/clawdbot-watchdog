"""Claude API integration for analyzing ClawdBot failures and recommending recovery.

Only called when simple restart has already failed — this is the expensive path.
Uses Haiku for cost efficiency."""

import json
import logging
from typing import Optional

import anthropic

import config
from health_check import CheckResult

logger = logging.getLogger("watchdog.advisor")

VALID_ACTIONS = [
    "restart_gateway",
    "restart_all",
    "revert_workspace_commit",
    "revert_openclaw_commit",
    "reboot_vm",
    "escalate",
    "no_action",
]

TOOLS = [
    {
        "name": "recommend_action",
        "description": "Recommend a recovery action based on the health check analysis.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": VALID_ACTIONS,
                    "description": "The recovery action to take.",
                },
                "reasoning": {
                    "type": "string",
                    "description": "Brief explanation (1-2 sentences) of why this action was chosen.",
                },
                "confidence": {
                    "type": "number",
                    "description": "Confidence 0-1 that this action will resolve the issue.",
                },
            },
            "required": ["action", "reasoning", "confidence"],
        },
    }
]

SYSTEM_PROMPT = """You are a DevOps watchdog advisor for ClawdBot, an autonomous AI business agent.

## Architecture
- ClawdBot runs on GCP VM (e2-small, us-west1-b) in Docker containers
- Gateway container (openclaw-openclaw-gateway-1): the main bot — runs OpenClaw with GPT-5, WhatsApp via Baileys, Claude Code, headless Chromium
- Website container (openclaw-website-1): nginx serving static files on port 80
- CLI containers (ephemeral): spawned for interactive sessions, ignore these

## Key behavior
- ClawdBot MODIFIES ITS OWN CODE: it edits AGENTS.md, GOALS.md, LEARNINGS.md, and can run arbitrary code
- Self-modifications are committed to GitHub (clawdbot-workspace repo)
- If a self-modification breaks the bot, reverting the workspace commit may fix it
- Gateway startup takes ~30s (runs apt-get, npm install, playwright install on each boot)
- WhatsApp reconnects automatically after restart — look for "Listening for personal WhatsApp inbound messages"
- Internal heartbeat fires every 30 min for autonomous self-improvement tasks
- Dashboard collector updates data.json every 60s

## Available actions (least to most disruptive)
- no_action: Issue is transient/non-critical, will resolve itself
- restart_gateway: docker compose restart openclaw-gateway (keeps volumes, reconnects WhatsApp)
- restart_all: docker compose down && docker compose up -d (full restart, preserves volumes)
- revert_workspace_commit: git revert HEAD in the workspace — use when a ClawdBot self-edit broke things
- revert_openclaw_commit: git revert HEAD in /home/fabian/openclaw — use when a deploy change broke things
- reboot_vm: full VM reboot via GCP API — last resort for kernel/system issues
- escalate: log for manual intervention — use when unsure or issue is complex

## Decision guidelines
- A simple restart already failed before you were consulted — so think deeper
- Look at git logs: did ClawdBot recently modify files? That's often the cause
- Look at Docker restart count: high count = the container keeps crashing
- If logs show Python/Node errors after a workspace commit, recommend revert_workspace_commit
- If the gateway won't start at all, check if a Docker image or compose change is the cause
- Memory/disk issues need different treatment than code bugs
- WhatsApp disconnects usually fix themselves on restart — only escalate if persistent"""


def get_recommendation(failed_checks: list[CheckResult], diagnostic_context: str) -> Optional[dict]:
    """Ask Claude to analyze failures and recommend an action.

    Returns dict with keys: action, reasoning, confidence — or None on error.
    """
    if not config.ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY not set — using fallback")
        return {"action": "restart_all", "reasoning": "No API key — fallback to full restart", "confidence": 0.4}

    check_summary = "\n".join(
        f"- {c.name} [{c.severity}]: FAIL — {c.details}" + (f" (error: {c.error})" if c.error else "")
        for c in failed_checks
    )
    user_message = f"""ClawdBot health checks FAILED (a simple restart was already attempted and didn't help):

{check_summary}

{diagnostic_context}

Analyze the logs and git history. What is likely broken and what's the best recovery action?"""

    try:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=512,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            tool_choice={"type": "tool", "name": "recommend_action"},
            messages=[{"role": "user", "content": user_message}],
        )

        for block in response.content:
            if block.type == "tool_use" and block.name == "recommend_action":
                rec = block.input
                if rec.get("action") in VALID_ACTIONS:
                    logger.info(f"Claude recommends: {rec['action']} (confidence: {rec['confidence']})")
                    logger.info(f"Reasoning: {rec['reasoning']}")
                    return rec

        logger.warning("Claude did not return a valid tool_use response")
        return {"action": "escalate", "reasoning": "No valid recommendation from Claude", "confidence": 0.3}

    except anthropic.APIError as e:
        logger.error(f"Claude API error: {e}")
        return {"action": "restart_all", "reasoning": f"API error fallback: {e}", "confidence": 0.3}
    except Exception as e:
        logger.error(f"Unexpected error consulting Claude: {e}")
        return {"action": "restart_all", "reasoning": f"Error fallback: {e}", "confidence": 0.3}
