"""
Checkpoints — Configurable human-in-the-loop pause points.
Agents check actions against rules before executing, pausing when a match is found.
"""

import re
import uuid
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Default checkpoint rules — dangerous operations that should always prompt
DEFAULT_RULES = [
    {"id": "default-rm", "trigger": "command", "pattern": r"rm\s+-rf", "action": "confirm", "label": "Destructive delete"},
    {"id": "default-docker", "trigger": "command", "pattern": r"docker\s+(rm|rmi|system\s+prune)", "action": "confirm", "label": "Docker cleanup"},
    {"id": "default-drop", "trigger": "command", "pattern": r"DROP\s+(TABLE|DATABASE)", "action": "confirm", "label": "Database drop"},
    {"id": "default-deploy", "trigger": "command", "pattern": r"(deploy|push.*production|kubectl\s+apply)", "action": "pause", "label": "Production deploy"},
]


class CheckpointManager:
    """Manages checkpoint rules for human-in-the-loop gates."""

    def __init__(self):
        self._rules: list[dict] = list(DEFAULT_RULES)

    def get_rules(self) -> list[dict]:
        """Get all checkpoint rules."""
        return self._rules

    def add_rule(self, trigger: str, pattern: str, action: str = "pause", label: str = "") -> dict:
        """Add a custom checkpoint rule."""
        rule = {
            "id": f"custom-{uuid.uuid4().hex[:8]}",
            "trigger": trigger,
            "pattern": pattern,
            "action": action,
            "label": label or f"Custom: {pattern[:30]}",
        }
        self._rules.append(rule)
        logger.info(f"Checkpoint added: {rule['label']}")
        return rule

    def remove_rule(self, rule_id: str):
        """Remove a checkpoint rule by ID."""
        self._rules = [r for r in self._rules if r["id"] != rule_id]

    def check_action(self, action_type: str, action_data: dict) -> Optional[dict]:
        """
        Check if an action matches any checkpoint rule.
        Returns the matched rule or None.
        """
        # Determine what to check based on action type
        if action_type == "run_command":
            check_text = action_data.get("command", "")
            trigger_type = "command"
        elif action_type == "write_file":
            check_text = action_data.get("path", "")
            trigger_type = "file_write"
        elif action_type == "delete_file":
            check_text = action_data.get("path", "")
            trigger_type = "file_delete"
        else:
            check_text = str(action_data)
            trigger_type = action_type

        for rule in self._rules:
            # Match on trigger type or "custom" (matches everything)
            if rule["trigger"] not in (trigger_type, "command", "custom"):
                continue

            try:
                if re.search(rule["pattern"], check_text, re.IGNORECASE):
                    logger.info(f"Checkpoint triggered: {rule['label']} on '{check_text[:60]}'")
                    return rule
            except re.error:
                continue

        return None
