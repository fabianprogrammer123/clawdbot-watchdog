"""Claude API integration for analyzing failures and recommending recovery actions."""

import json
import logging
from typing import Optional

import anthropic

import config
from health_check import CheckResult

logger = logging.getLogger("watchdog.advisor")

VALID_ACTIONS = ["restart_containers", "revert_last_commit", "reboot_vm", "escalate", "no_action"]

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
                    "description": "Brief explanation of why this action was chosen.",
                },
                "confidence": {
                    "type": "number",
                    "description": "Confidence level 0-1 that this action will resolve the issue.",
                },
            },
            "required": ["action", "reasoning", "confidence"],
        },
    }
]

SYSTEM_PROMPT = """You are a DevOps watchdog advisor for an OpenClaw (WhatsApp bot) deployment.
You analyze health check results and recommend the most appropriate recovery action.

The system runs on a GCP VM (e2-small) with Docker Compose. The bot connects to WhatsApp via Baileys.

Available actions:
- restart_containers: Stop and restart Docker containers. Best for hung processes, memory leaks, or connection issues.
- revert_last_commit: Git revert the last commit on the VM. Best when a recent code change likely caused the issue.
- reboot_vm: Full VM reboot via GCP API. Last resort for system-level issues.
- escalate: Log for manual intervention. Use when the issue is unclear or risky to auto-fix.
- no_action: No recovery needed. Use when failures are transient or non-critical.

Guidelines:
- Prefer the least disruptive action that will fix the issue.
- restart_containers is usually the right first step for most issues.
- Only recommend revert_last_commit if there's evidence a code change caused the problem.
- reboot_vm only if containers restart didn't help or system resources are severely degraded.
- escalate if you're unsure or the situation is complex.

Use the recommend_action tool to provide your recommendation."""


def get_recommendation(failed_checks: list[CheckResult], context: str = "") -> Optional[dict]:
    """Ask Claude to analyze failures and recommend an action.

    Returns dict with keys: action, reasoning, confidence — or None on error.
    """
    if not config.ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY not set — cannot consult Claude")
        return {"action": "restart_containers", "reasoning": "Fallback: no API key configured", "confidence": 0.5}

    # Build the user message with check details
    check_summary = "\n".join(
        f"- {c.name}: FAIL — {c.details}" + (f" (error: {c.error})" if c.error else "")
        for c in failed_checks
    )
    user_message = f"""The following health checks have FAILED on the openclaw-server:

{check_summary}

{f"Additional context: {context}" if context else ""}

Analyze these failures and recommend the best recovery action using the recommend_action tool."""

    try:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            tool_choice={"type": "tool", "name": "recommend_action"},
            messages=[{"role": "user", "content": user_message}],
        )

        # Extract tool use from response
        for block in response.content:
            if block.type == "tool_use" and block.name == "recommend_action":
                recommendation = block.input
                if recommendation.get("action") in VALID_ACTIONS:
                    logger.info(f"Claude recommends: {recommendation['action']} (confidence: {recommendation['confidence']})")
                    logger.info(f"Reasoning: {recommendation['reasoning']}")
                    return recommendation
                else:
                    logger.warning(f"Claude returned invalid action: {recommendation}")

        logger.warning("Claude did not return a tool_use response")
        return {"action": "escalate", "reasoning": "Claude did not return a valid recommendation", "confidence": 0.3}

    except anthropic.APIError as e:
        logger.error(f"Claude API error: {e}")
        return {"action": "restart_containers", "reasoning": f"API error fallback: {e}", "confidence": 0.4}
    except Exception as e:
        logger.error(f"Unexpected error consulting Claude: {e}")
        return {"action": "restart_containers", "reasoning": f"Error fallback: {e}", "confidence": 0.3}
