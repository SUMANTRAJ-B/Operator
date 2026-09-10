"""Recovery and retry management for handling execution and verification failures."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional

from app.config import get_settings
from app.logging import get_logger

logger = get_logger("agent.recovery")


class ErrorCategory(str, Enum):
    """Classification of errors encountered during agent loop."""

    SAFETY_BLOCKED = "safety_blocked"
    TOOL_ERROR = "tool_error"
    VERIFICATION_FAILED = "verification_failed"
    WINDOW_NOT_FOUND = "window_not_found"
    TIMEOUT = "timeout"
    MODAL_BLOCKED = "modal_blocked"
    MODAL_RECOVERY_FAILED = "modal_recovery_failed"
    UNKNOWN = "unknown"


@dataclass
class RecoveryDecision:
    """Actionable decision on how to handle an encountered error."""

    can_retry: bool
    category: ErrorCategory
    feedback_for_agent: str
    retry_count: int


class RecoveryManager:
    """Classifies failures and generates guidance messages for agent self-correction."""

    def __init__(self):
        settings = get_settings()
        self.max_retries = settings.max_step_retries
        self._step_retries: Dict[str, int] = {}

    def handle_failure(
        self,
        tool_name: str,
        error_str: str,
        category: ErrorCategory = ErrorCategory.TOOL_ERROR,
    ) -> RecoveryDecision:
        """Record failure and provide guidance for agent recovery."""
        key = f"{tool_name}:{category.value}"
        current = self._step_retries.get(key, 0) + 1
        self._step_retries[key] = current

        can_retry = current <= self.max_retries
        logger.warning(
            f"Recovery trigger for [{tool_name}] (attempt {current}/{self.max_retries}, category={category.value}): {error_str}"
        )

        if category == ErrorCategory.SAFETY_BLOCKED:
            feedback = (
                f"Action '{tool_name}' was blocked by safety policy: {error_str}. "
                "Choose an alternative safe action or verify you are targeting the user's application, not the IDE."
            )
        elif category == ErrorCategory.VERIFICATION_FAILED:
            feedback = (
                f"Action '{tool_name}' executed, but post-action verification failed: {error_str}. "
                "Observe the current desktop state (e.g. check open windows or active window) and retry with the correct handle or focus."
            )
        elif category == ErrorCategory.MODAL_BLOCKED:
            feedback = (
                f"Action '{tool_name}' encountered a blocking dialog or modal: {error_str}. "
                "Visual recovery was engaged to resolve the modal state."
            )
        elif category == ErrorCategory.MODAL_RECOVERY_FAILED:
            feedback = (
                f"Modal recovery for '{tool_name}' could not safely resolve dialog: {error_str}. "
                "Action was halted safely to prevent unverified UI modifications."
            )
        else:
            feedback = (
                f"Tool '{tool_name}' encountered an error: {error_str}. "
                "Inspect the error and adjust your parameters or call an alternate tool."
            )

        return RecoveryDecision(
            can_retry=can_retry,
            category=category,
            feedback_for_agent=feedback,
            retry_count=current,
        )

    def reset(self) -> None:
        """Reset retry counters."""
        self._step_retries.clear()
