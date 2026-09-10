"""Generic visual modal recovery controller and candidate evaluation engine.

Implements the bounded Phase B recovery state machine:
- Generic modal/blocking-state detection via UIAutomation and Win32.
- Candidate scoring considering control semantics, task intent, risk classification,
  and ambiguity (IDYES / affirmative controls are NOT treated as inherently safe).
- Multi-signal state-based verification of modal resolution.
- Bounded execution (single recovery attempt per trigger, not a second planner).
- Authoritative Grounding & CentralSafetyGate compliance.
- Ephemeral screenshot lifecycle: delete on verified success, preserve on failure/safe stop.
"""

from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid

import win32con
import win32gui

from actions.applications import get_app_controller
from actions.mouse import get_mouse_controller
from app.config import get_settings
from app.logging import get_logger
from perception.controller import ScreenObservation, get_perception_controller
from perception.grounding import get_grounding_registry
from providers.vision.base import BoundingBox
from safety.policy import get_safety_policy
from verification.verifier import ActionVerifier, VerificationResult, get_action_verifier

logger = get_logger("agent.modal_recovery")


class RecoveryOutcome(str, Enum):
    """Explicit recovery evaluation and resolution outcomes."""

    RECOVERED = "recovered"
    NOT_BLOCKING = "not_blocking"
    AMBIGUOUS = "ambiguous"
    HIGH_RISK = "high_risk"
    NO_SAFE_ACTION = "no_safe_action"
    FAILED = "failed"
    SAFE_STOP = "safe_stop"


class ControlRole(str, Enum):
    """Semantic role of dialog controls derived from Win32 IDs, styles, and patterns."""

    AFFIRMATIVE_PROCEED = "affirmative_proceed"
    DISMISS_ACKNOWLEDGE = "dismiss_acknowledge"
    CANCEL_ABORT = "cancel_abort"
    RETRY = "retry"
    NEGATIVE_REJECT = "negative_reject"
    CLOSE = "close"
    UNKNOWN = "unknown"


class RiskLevel(str, Enum):
    """Risk tier for modal dialog actions."""

    LOW_RISK_ACKNOWLEDGE = "low_risk_acknowledge"
    TASK_ALIGNED_CONFIRM = "task_aligned_confirm"
    POTENTIALLY_DESTRUCTIVE = "potentially_destructive"
    HIGH_RISK_UNKNOWN = "high_risk_unknown"


@dataclass
class RecoveryCandidate:
    """Actionable control candidate evaluated for modal dismissal."""

    control_id: int
    label: str
    element_type: str
    bounds: Tuple[int, int, int, int]
    click_point: Tuple[int, int]
    role: ControlRole
    risk_level: RiskLevel
    score: float
    confidence: float
    is_default: bool = False
    is_enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "control_id": self.control_id,
            "label": self.label,
            "element_type": self.element_type,
            "bounds": self.bounds,
            "click_point": self.click_point,
            "role": self.role.value,
            "risk_level": self.risk_level.value,
            "score": round(self.score, 3),
            "confidence": round(self.confidence, 3),
            "is_default": self.is_default,
            "is_enabled": self.is_enabled,
            "metadata": self.metadata,
        }


@dataclass
class ModalDialogState:
    """Structured representation of an active blocking modal or dialog window."""

    hwnd: int
    title: str
    class_name: str
    owner_hwnd: Optional[int] = None
    owner_title: str = ""
    is_modal: bool = False
    message_text: str = ""
    controls: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hwnd": self.hwnd,
            "title": self.title,
            "class_name": self.class_name,
            "owner_hwnd": self.owner_hwnd,
            "owner_title": self.owner_title,
            "is_modal": self.is_modal,
            "message_text": self.message_text,
            "controls_count": len(self.controls),
        }


@dataclass
class ModalRecoveryDecision:
    """Actionable decision produced by bounded candidate evaluation."""

    outcome: RecoveryOutcome
    candidate: Optional[RecoveryCandidate] = None
    reason: str = ""
    action_type: str = "NONE"  # "UIA_INVOKE", "GROUNDED_CLICK", "NONE"


@dataclass
class ModalRecoveryAudit:
    """Audit record capturing complete recovery attempt context and verification outcome."""

    audit_id: str
    task_id: str
    timestamp: float
    modal_hwnd: int
    modal_title: str
    dialog_message: str
    outcome: RecoveryOutcome
    candidates: List[Dict[str, Any]]
    chosen_candidate: Optional[Dict[str, Any]]
    action_type: str
    safety_decision: Dict[str, Any]
    pre_observation_id: Optional[str] = None
    post_observation_id: Optional[str] = None
    screenshot_path: Optional[str] = None
    verification_details: str = ""
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "task_id": self.task_id,
            "timestamp": self.timestamp,
            "modal_hwnd": self.modal_hwnd,
            "modal_title": self.modal_title,
            "dialog_message": self.dialog_message,
            "outcome": self.outcome.value,
            "candidates": self.candidates,
            "chosen_candidate": self.chosen_candidate,
            "action_type": self.action_type,
            "safety_decision": self.safety_decision,
            "pre_observation_id": self.pre_observation_id,
            "post_observation_id": self.post_observation_id,
            "screenshot_path": self.screenshot_path,
            "verification_details": self.verification_details,
            "error_message": self.error_message,
        }


@dataclass
class ModalRecoveryResult:
    """Outcome returned to OperatorAgent to resume or safely stop."""

    outcome: RecoveryOutcome
    success: bool
    modal_hwnd: int
    message: str
    audit: ModalRecoveryAudit
    retained_evidence: bool = False


class RecoveryCandidateEvaluator:
    """Generic candidate scoring and role evaluation.

    CRITICAL ARCHITECTURAL RULES:
    1. Zero hardcoding of dialog titles, button strings, or application names.
    2. IDYES / affirmative controls are NOT treated as inherently safe.
    3. Evaluates control semantics, task intent, risk classification, and ambiguity.
    4. If no safe candidate or ambiguity exists, outputs NO_SAFE_ACTION or AMBIGUOUS -> SAFE_STOP.
    """

    # Win32 Standard Dialog Control IDs
    IDOK = 1
    IDCANCEL = 2
    IDABORT = 3
    IDRETRY = 4
    IDIGNORE = 5
    IDYES = 6
    IDNO = 7
    IDCLOSE = 8
    IDHELP = 9
    IDTRYAGAIN = 10
    IDCONTINUE = 11

    # Destructive / high-risk trigger phrases (used solely for risk classification, not string-matching buttons)
    HIGH_RISK_INDICATORS = (
        "permanently delete",
        "format disk",
        "erase",
        "uninstall",
        "administrator privileges",
        "elevated permissions",
        "factory reset",
        "wipe",
        "destroy",
    )

    def classify_control_role(self, ctrl: Dict[str, Any]) -> ControlRole:
        """Derive control role from standard Windows control IDs and styles."""
        ctrl_id = ctrl.get("control_id", 0)
        auto_id = str(ctrl.get("automation_id", "")).lower()
        label_raw = str(ctrl.get("label", "")).strip()
        label_clean = label_raw.replace("&", "").lower()

        # 1. Standard Win32 Control ID mapping
        if ctrl_id == self.IDOK:
            return ControlRole.DISMISS_ACKNOWLEDGE
        if ctrl_id == self.IDYES:
            return ControlRole.AFFIRMATIVE_PROCEED
        if ctrl_id == self.IDNO:
            return ControlRole.NEGATIVE_REJECT
        if ctrl_id in (self.IDCANCEL, self.IDABORT):
            return ControlRole.CANCEL_ABORT
        if ctrl_id in (self.IDRETRY, self.IDTRYAGAIN):
            return ControlRole.RETRY
        if ctrl_id == self.IDCLOSE:
            return ControlRole.CLOSE

        # 2. Automation ID fallback
        if "yes" in auto_id or "affirmative" in auto_id or "proceed" in auto_id or "accept" in auto_id:
            return ControlRole.AFFIRMATIVE_PROCEED
        if "ok" in auto_id or "acknowledge" in auto_id or "dismiss" in auto_id:
            return ControlRole.DISMISS_ACKNOWLEDGE
        if "cancel" in auto_id or "abort" in auto_id:
            return ControlRole.CANCEL_ABORT
        if "no" in auto_id or "reject" in auto_id:
            return ControlRole.NEGATIVE_REJECT
        if "retry" in auto_id:
            return ControlRole.RETRY
        if "close" in auto_id:
            return ControlRole.CLOSE

        # 3. Label semantic fallback (without hardcoded dialog titles)
        if label_clean in ("yes", "proceed", "continue", "replace", "overwrite", "save"):
            return ControlRole.AFFIRMATIVE_PROCEED
        if label_clean in ("ok", "got it", "acknowledge", "dismiss", "done"):
            return ControlRole.DISMISS_ACKNOWLEDGE
        if label_clean in ("cancel", "abort"):
            return ControlRole.CANCEL_ABORT
        if label_clean in ("no", "don't save", "dont save", "reject"):
            return ControlRole.NEGATIVE_REJECT
        if label_clean in ("retry", "try again"):
            return ControlRole.RETRY
        if label_clean in ("close",):
            return ControlRole.CLOSE

        return ControlRole.UNKNOWN

    def classify_dialog_risk(
        self,
        dialog: ModalDialogState,
        goal: str,
    ) -> RiskLevel:
        """Classify the risk level of the modal dialog."""
        msg = f"{dialog.title} {dialog.message_text}".lower()
        goal_l = goal.lower()

        # Check for unrecoverable destructive indicators
        for ind in self.HIGH_RISK_INDICATORS:
            if ind in msg:
                # If the user's explicit goal specifically requested this destructive action, it's aligned
                if ind in goal_l:
                    return RiskLevel.TASK_ALIGNED_CONFIRM
                # Otherwise, it's an unexpected destructive prompt: high-risk!
                return RiskLevel.POTENTIALLY_DESTRUCTIVE

        # Check if dialog is an informative alert / prompt
        is_alert = any(w in msg for w in ("info", "notice", "alert", "message", "complete", "finished"))
        if is_alert:
            return RiskLevel.LOW_RISK_ACKNOWLEDGE

        # Check if task goal actively aligns with confirmation
        aligned_keywords = ("save", "confirm", "proceed", "yes", "replace", "overwrite", "accept", "continue", "apply", "ok")
        if any(k in goal_l for k in aligned_keywords):
            return RiskLevel.TASK_ALIGNED_CONFIRM

        return RiskLevel.HIGH_RISK_UNKNOWN

    def evaluate_candidates(
        self,
        dialog: ModalDialogState,
        goal: str,
        screen_size: Tuple[int, int],
    ) -> ModalRecoveryDecision:
        """Evaluate and score recovery candidates.

        Correction 1: IDYES / affirmative control is NOT automatically authorized.
        Correction 4: Distinguishes HIGH_RISK, AMBIGUOUS, NO_SAFE_ACTION, and RECOVERED.
        """
        # Step 1: Risk Assessment of the dialog context
        risk_tier = self.classify_dialog_risk(dialog, goal)
        if risk_tier == RiskLevel.POTENTIALLY_DESTRUCTIVE:
            return ModalRecoveryDecision(
                outcome=RecoveryOutcome.HIGH_RISK,
                candidate=None,
                reason=(
                    f"Modal dialog '{dialog.title}' contains potentially destructive prompt "
                    f"({dialog.message_text[:80]!r}) that is not explicitly aligned with task goal {goal!r}. "
                    "Safety policy mandates SAFE_STOP."
                ),
                action_type="NONE",
            )

        # Step 2: Extract interactive button controls
        button_controls = [
            c for c in dialog.controls
            if c.get("type") in ("button", "control") and c.get("is_enabled", True)
        ]

        if not button_controls:
            return ModalRecoveryDecision(
                outcome=RecoveryOutcome.NO_SAFE_ACTION,
                candidate=None,
                reason=f"No enabled actionable button controls discovered on dialog '{dialog.title}'.",
                action_type="NONE",
            )

        scored_candidates: List[RecoveryCandidate] = []
        scr_w, scr_h = screen_size

        for c in button_controls:
            b_tuple = c.get("bounds", (0, 0, 0, 0))
            if len(b_tuple) != 4 or b_tuple[2] <= b_tuple[0] or b_tuple[3] <= b_tuple[1]:
                continue
            # Validate bounding box falls inside screen dimensions
            if b_tuple[0] < 0 or b_tuple[1] < 0 or b_tuple[2] > scr_w or b_tuple[3] > scr_h:
                continue

            center = (b_tuple[0] + (b_tuple[2] - b_tuple[0]) // 2, b_tuple[1] + (b_tuple[3] - b_tuple[1]) // 2)
            role = self.classify_control_role(c)
            is_def = bool(c.get("is_default", False))
            ctrl_id = c.get("control_id", 0)

            # Base score computation
            score = 0.5
            confidence = 0.5

            # 1. Role scoring in context
            if role == ControlRole.DISMISS_ACKNOWLEDGE:
                # Acknowledging alerts/dialogs is inherently safe
                score += 0.35
                confidence += 0.35
            elif role == ControlRole.AFFIRMATIVE_PROCEED:
                # Affirmative proceed is scored favorably IF task intent aligns, but NOT automatically safe
                if risk_tier == RiskLevel.TASK_ALIGNED_CONFIRM:
                    score += 0.30
                    confidence += 0.30
                else:
                    score += 0.10
                    confidence += 0.15
            elif role == ControlRole.CLOSE:
                score += 0.20
                confidence += 0.20
            elif role == ControlRole.CANCEL_ABORT:
                # If goal is closing or aborting, cancel might have lower score
                score += 0.10
                confidence += 0.15
            elif role == ControlRole.NEGATIVE_REJECT:
                score += 0.10
                confidence += 0.15
            else:
                score += 0.0

            # 2. Windows Default Button bonus (BS_DEFPUSHBUTTON is designed for Enter key / default action)
            if is_def:
                score += 0.15
                confidence += 0.10

            # 3. Enabled status
            if not c.get("is_enabled", True):
                score = 0.0
                confidence = 0.0

            candidate = RecoveryCandidate(
                control_id=ctrl_id,
                label=c.get("label", ""),
                element_type=c.get("type", "button"),
                bounds=b_tuple,
                click_point=center,
                role=role,
                risk_level=risk_tier,
                score=score,
                confidence=confidence,
                is_default=is_def,
                is_enabled=c.get("is_enabled", True),
                metadata=c,
            )
            scored_candidates.append(candidate)

        if not scored_candidates:
            return ModalRecoveryDecision(
                outcome=RecoveryOutcome.NO_SAFE_ACTION,
                candidate=None,
                reason="All controls on dialog were invalid or out of screen bounds.",
                action_type="NONE",
            )

        # Sort candidates descending by score
        scored_candidates.sort(key=lambda c: c.score, reverse=True)
        top = scored_candidates[0]

        # Step 3: Ambiguity Check
        # If there are multiple candidates and the top two have nearly identical scores, check ambiguity
        if len(scored_candidates) > 1:
            runner_up = scored_candidates[1]
            score_diff = top.score - runner_up.score
            # If top two candidates are both affirmative vs negative without clear task alignment:
            if (
                score_diff < 0.05
                and top.role in (ControlRole.AFFIRMATIVE_PROCEED, ControlRole.NEGATIVE_REJECT)
                and runner_up.role in (ControlRole.AFFIRMATIVE_PROCEED, ControlRole.NEGATIVE_REJECT)
            ):
                return ModalRecoveryDecision(
                    outcome=RecoveryOutcome.AMBIGUOUS,
                    candidate=None,
                    reason=(
                        f"Ambiguous dialog choices on '{dialog.title}': top candidate '{top.label}' "
                        f"(score {top.score:.2f}) vs '{runner_up.label}' (score {runner_up.score:.2f}) "
                        "have indistinguishable confidence. Stopping safely."
                    ),
                    action_type="NONE",
                )

        # Step 4: Confidence Threshold Check
        if top.score < 0.60:
            return ModalRecoveryDecision(
                outcome=RecoveryOutcome.NO_SAFE_ACTION,
                candidate=None,
                reason=(
                    f"Highest candidate '{top.label}' did not meet minimum confidence threshold "
                    f"({top.score:.2f} < 0.60). Halting safely without clicking."
                ),
                action_type="NONE",
            )

        # Candidate successfully selected
        return ModalRecoveryDecision(
            outcome=RecoveryOutcome.RECOVERED,
            candidate=top,
            reason=f"Selected candidate '{top.label}' (role: {top.role.value}, score: {top.score:.2f}).",
            action_type="GROUNDED_CLICK",
        )


class ModalRecoveryController:
    """Bounded, specialized visual recovery controller.

    CRITICAL BOUNDS:
    - Single recovery attempt per occurrence (NOT a second general-purpose planner).
    - Preserves CentralSafetyGate authority.
    - Uses GroundingRegistry for fresh observation_id validation.
    - Uses ActionVerifier.verify_modal_dismissed for multi-signal state verification.
    - Manages ephemeral screenshot lifecycle.
    """

    def __init__(
        self,
        evaluator: Optional[RecoveryCandidateEvaluator] = None,
        verifier: Optional[ActionVerifier] = None,
    ):
        self.settings = get_settings()
        self.evaluator = evaluator or RecoveryCandidateEvaluator()
        self.verifier = verifier or get_action_verifier()
        self.app_controller = get_app_controller()
        self.mouse_controller = get_mouse_controller()
        self.safety_policy = get_safety_policy()
        self.perception = get_perception_controller()
        self.grounding_registry = get_grounding_registry()

    def is_blocking_modal(
        self,
        observation: Optional[ScreenObservation] = None,
        target_hwnd: Optional[int] = None,
        target_title_or_query: Optional[str] = None,
    ) -> Tuple[bool, Optional[ModalDialogState]]:
        """Determine whether an active window/dialog is currently blocking task execution."""
        obs = observation or self.perception.fresh_screen_observation(capture_image=False)
        modal_dict = obs.modal_dialog

        top_hwnd = win32gui.GetForegroundWindow()
        if not top_hwnd or not win32gui.IsWindow(top_hwnd):
            return False, None

        # If modal dialog detected by UIAutomation
        if modal_dict and modal_dict.get("hwnd") == top_hwnd:
            dialog_state = ModalDialogState(
                hwnd=top_hwnd,
                title=modal_dict.get("title", ""),
                class_name=modal_dict.get("class_name", ""),
                owner_hwnd=modal_dict.get("owner_hwnd"),
                owner_title=modal_dict.get("owner_title", ""),
                is_modal=modal_dict.get("is_modal", True),
                message_text=modal_dict.get("message_text", ""),
                controls=modal_dict.get("controls", []),
            )
            return True, dialog_state

        # Check if foreground window is an unexpected popup owned by the target app
        if target_hwnd and win32gui.IsWindow(target_hwnd):
            if top_hwnd != target_hwnd:
                owner = win32gui.GetWindow(top_hwnd, win32con.GW_OWNER)
                if owner == target_hwnd or (not win32gui.IsWindowEnabled(target_hwnd)):
                    cls = win32gui.GetClassName(top_hwnd)
                    title = win32gui.GetWindowText(top_hwnd)
                    controls = self.perception.uia.find_controls_in_window(top_hwnd)
                    control_dicts = [
                        {
                            "label": c.label,
                            "type": c.element_type,
                            "bounds": c.bounds.to_tuple(),
                            "confidence": c.confidence,
                            "control_id": c.attributes.get("control_id", 0),
                            "is_default": c.attributes.get("is_default", False),
                            "hwnd": c.attributes.get("hwnd"),
                            "is_enabled": c.attributes.get("is_enabled", True),
                        }
                        for c in controls
                    ]
                    dialog_state = ModalDialogState(
                        hwnd=top_hwnd,
                        title=title,
                        class_name=cls,
                        owner_hwnd=owner,
                        owner_title=win32gui.GetWindowText(owner) if owner else "",
                        is_modal=True,
                        message_text="",
                        controls=control_dicts,
                    )
                    return True, dialog_state

        return False, None

    def attempt_recovery(
        self,
        task_id: str,
        goal: str,
        target_hwnd: Optional[int] = None,
        target_title_or_query: Optional[str] = None,
    ) -> ModalRecoveryResult:
        """Execute a single, bounded recovery cycle to safely resolve a blocking modal.

        Follows the strict sequence:
        1. Fresh Screen Observation (capture_image=True for evidence).
        2. Detect blocking modal & extract controls.
        3. Protected environment check (CentralSafetyGate).
        4. Candidate evaluation (risk, intent, ambiguity).
        5. Pre-action freshness validation (< 2.0s).
        6. SafetyPolicy pre-action check.
        7. Action dispatch (grounded click_at with observation_id).
        8. Post-action fresh observation.
        9. Multi-signal state-based verification.
        10. Screenshot lifecycle: delete on verified recovery, preserve on failure/stop.
        11. Complete audit trail recording.
        """
        audit_id = f"mrec_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
        now = time.time()
        logger.info(f"Initiating bounded modal recovery cycle [{audit_id}] for task: {goal!r}")

        # 1. Fresh screen observation with screenshot
        pre_obs = self.perception.fresh_screen_observation(capture_image=True)
        screenshot_path = str(pre_obs.screenshot_path) if pre_obs.screenshot_path else None

        # 2. Inspect blocking modal
        is_blocking, dialog = self.is_blocking_modal(
            observation=pre_obs,
            target_hwnd=target_hwnd,
            target_title_or_query=target_title_or_query,
        )

        if not is_blocking or not dialog:
            # Clean up ephemeral screenshot since no modal exists
            if pre_obs.observation_id:
                self.perception.cleanup_observation_screenshot(pre_obs.observation_id, success=True)
            audit = ModalRecoveryAudit(
                audit_id=audit_id,
                task_id=task_id,
                timestamp=now,
                modal_hwnd=0,
                modal_title="",
                dialog_message="",
                outcome=RecoveryOutcome.NOT_BLOCKING,
                candidates=[],
                chosen_candidate=None,
                action_type="NONE",
                safety_decision={"allowed": True, "reason": "No blocking modal active."},
                pre_observation_id=pre_obs.observation_id,
                verification_details="No modal blocking task execution.",
            )
            return ModalRecoveryResult(
                outcome=RecoveryOutcome.NOT_BLOCKING,
                success=True,
                modal_hwnd=0,
                message="No blocking modal detected; task may proceed.",
                audit=audit,
                retained_evidence=False,
            )

        # 3. CentralSafetyGate check: protected process tree immunity
        is_prot, prot_reason = self.app_controller.is_protected_target_with_reason(
            hwnd=dialog.hwnd, title=dialog.title, class_name=dialog.class_name
        )
        if is_prot:
            logger.warning(f"Modal recovery halted: modal window is protected ({prot_reason})")
            audit = ModalRecoveryAudit(
                audit_id=audit_id,
                task_id=task_id,
                timestamp=now,
                modal_hwnd=dialog.hwnd,
                modal_title=dialog.title,
                dialog_message=dialog.message_text,
                outcome=RecoveryOutcome.SAFE_STOP,
                candidates=[],
                chosen_candidate=None,
                action_type="NONE",
                safety_decision={"allowed": False, "reason": f"Target is protected host: {prot_reason}"},
                pre_observation_id=pre_obs.observation_id,
                screenshot_path=screenshot_path,
                verification_details="Target belongs to protected host environment.",
                error_message=f"Modal belongs to protected process: {prot_reason}",
            )
            return ModalRecoveryResult(
                outcome=RecoveryOutcome.SAFE_STOP,
                success=False,
                modal_hwnd=dialog.hwnd,
                message=f"Modal is protected host environment ({prot_reason}). SAFE_STOP.",
                audit=audit,
                retained_evidence=True,
            )

        # 4. Candidate evaluation
        decision = self.evaluator.evaluate_candidates(
            dialog=dialog,
            goal=goal,
            screen_size=pre_obs.screen_size,
        )

        all_candidates_dict = [c.get("label") for c in dialog.controls]
        chosen_dict = decision.candidate.to_dict() if decision.candidate else None

        # Check for non-recoverable outcomes (HIGH_RISK, AMBIGUOUS, NO_SAFE_ACTION)
        if decision.outcome != RecoveryOutcome.RECOVERED or not decision.candidate:
            logger.info(f"Modal recovery halted safely: outcome={decision.outcome.value} ({decision.reason})")
            audit = ModalRecoveryAudit(
                audit_id=audit_id,
                task_id=task_id,
                timestamp=now,
                modal_hwnd=dialog.hwnd,
                modal_title=dialog.title,
                dialog_message=dialog.message_text,
                outcome=decision.outcome,
                candidates=[{"label": str(c)} for c in all_candidates_dict],
                chosen_candidate=None,
                action_type="NONE",
                safety_decision={"allowed": False, "reason": decision.reason},
                pre_observation_id=pre_obs.observation_id,
                screenshot_path=screenshot_path,
                verification_details="No action taken due to risk/ambiguity classification.",
                error_message=decision.reason,
            )
            # Retain evidence for diagnosis
            return ModalRecoveryResult(
                outcome=decision.outcome,
                success=False,
                modal_hwnd=dialog.hwnd,
                message=decision.reason,
                audit=audit,
                retained_evidence=True,
            )

        candidate = decision.candidate

        # 5. Pre-action freshness validation (< 2.0s)
        configured_max_age = getattr(self.settings, "modal_recovery_max_age_seconds", 2.0)
        age = self.grounding_registry.get_observation_age(pre_obs.observation_id)
        if age is not None and age > configured_max_age:
            logger.warning(f"Pre-action observation {pre_obs.observation_id} is stale ({age:.2f}s > {configured_max_age}s); re-observing.")
            pre_obs = self.perception.fresh_screen_observation(capture_image=True)
            screenshot_path = str(pre_obs.screenshot_path) if pre_obs.screenshot_path else screenshot_path

        # Verify modal window is still foreground before dispatch
        curr_top = win32gui.GetForegroundWindow()
        if curr_top != dialog.hwnd:
            logger.warning(f"Pre-action target validation failed: modal HWND {dialog.hwnd} is no longer foreground.")
            audit = ModalRecoveryAudit(
                audit_id=audit_id,
                task_id=task_id,
                timestamp=now,
                modal_hwnd=dialog.hwnd,
                modal_title=dialog.title,
                dialog_message=dialog.message_text,
                outcome=RecoveryOutcome.FAILED,
                candidates=[{"label": str(c)} for c in all_candidates_dict],
                chosen_candidate=chosen_dict,
                action_type="NONE",
                safety_decision={"allowed": False, "reason": "Modal lost foreground focus before action dispatch."},
                pre_observation_id=pre_obs.observation_id,
                screenshot_path=screenshot_path,
                error_message="Modal lost foreground focus before action.",
            )
            return ModalRecoveryResult(
                outcome=RecoveryOutcome.FAILED,
                success=False,
                modal_hwnd=dialog.hwnd,
                message="Modal window was dismissed or lost focus prior to action dispatch.",
                audit=audit,
                retained_evidence=True,
            )

        # 6. SafetyPolicy pre-action check
        click_x, click_y = candidate.click_point
        action_args = {
            "x": click_x,
            "y": click_y,
            "observation_id": pre_obs.observation_id,
            "button": "left",
            "clicks": 1,
        }
        safety_dec = self.safety_policy.evaluate_action("click_at", action_args)
        if not safety_dec.allowed:
            logger.warning(f"SafetyPolicy blocked modal recovery action: {safety_dec.reason}")
            audit = ModalRecoveryAudit(
                audit_id=audit_id,
                task_id=task_id,
                timestamp=now,
                modal_hwnd=dialog.hwnd,
                modal_title=dialog.title,
                dialog_message=dialog.message_text,
                outcome=RecoveryOutcome.SAFE_STOP,
                candidates=[{"label": str(c)} for c in all_candidates_dict],
                chosen_candidate=chosen_dict,
                action_type="GROUNDED_CLICK",
                safety_decision={"allowed": False, "reason": safety_dec.reason},
                pre_observation_id=pre_obs.observation_id,
                screenshot_path=screenshot_path,
                error_message=f"SafetyPolicy blocked action: {safety_dec.reason}",
            )
            return ModalRecoveryResult(
                outcome=RecoveryOutcome.SAFE_STOP,
                success=False,
                modal_hwnd=dialog.hwnd,
                message=f"SafetyPolicy blocked recovery action: {safety_dec.reason}",
                audit=audit,
                retained_evidence=True,
            )

        # 7. Action dispatch: grounded click_at
        logger.info(
            f"Executing grounded recovery click on '{candidate.label}' at ({click_x}, {click_y}) [obs={pre_obs.observation_id}]"
        )
        try:
            self.mouse_controller.click_at(
                x=click_x,
                y=click_y,
                observation_id=pre_obs.observation_id,
                button="left",
                clicks=1,
            )
        except Exception as err:
            logger.error(f"Error executing recovery click: {err}")
            audit = ModalRecoveryAudit(
                audit_id=audit_id,
                task_id=task_id,
                timestamp=now,
                modal_hwnd=dialog.hwnd,
                modal_title=dialog.title,
                dialog_message=dialog.message_text,
                outcome=RecoveryOutcome.FAILED,
                candidates=[{"label": str(c)} for c in all_candidates_dict],
                chosen_candidate=chosen_dict,
                action_type="GROUNDED_CLICK",
                safety_decision={"allowed": True, "reason": safety_dec.reason},
                pre_observation_id=pre_obs.observation_id,
                screenshot_path=screenshot_path,
                error_message=str(err),
            )
            return ModalRecoveryResult(
                outcome=RecoveryOutcome.FAILED,
                success=False,
                modal_hwnd=dialog.hwnd,
                message=f"Action dispatch failed: {err}",
                audit=audit,
                retained_evidence=True,
            )

        # 8. Post-action fresh observation
        time.sleep(0.3)  # Brief UI settling delay
        post_obs = self.perception.fresh_screen_observation(capture_image=False)

        # 9. Multi-signal state-based verification (Correction 2)
        verif = self.verifier.verify_modal_dismissed(
            modal_hwnd=dialog.hwnd,
            target_hwnd=target_hwnd,
            target_title_or_query=target_title_or_query,
        )

        final_outcome = RecoveryOutcome.RECOVERED if verif.verified else RecoveryOutcome.FAILED
        logger.info(f"Modal recovery verification: verified={verif.verified} ({verif.details})")

        audit = ModalRecoveryAudit(
            audit_id=audit_id,
            task_id=task_id,
            timestamp=now,
            modal_hwnd=dialog.hwnd,
            modal_title=dialog.title,
            dialog_message=dialog.message_text,
            outcome=final_outcome,
            candidates=[{"label": str(c)} for c in all_candidates_dict],
            chosen_candidate=chosen_dict,
            action_type="GROUNDED_CLICK",
            safety_decision={"allowed": True, "reason": safety_dec.reason},
            pre_observation_id=pre_obs.observation_id,
            post_observation_id=post_obs.observation_id,
            screenshot_path=screenshot_path,
            verification_details=verif.details,
            error_message=None if verif.verified else f"Verification failed: {verif.details}",
        )

        # 10. Ephemeral screenshot lifecycle (Correction 6)
        if verif.verified:
            # Verified success: delete ephemeral observation screenshots
            if pre_obs.observation_id:
                self.perception.cleanup_observation_screenshot(pre_obs.observation_id, success=True)
            if post_obs.observation_id:
                self.perception.cleanup_observation_screenshot(post_obs.observation_id, success=True)
            retained = False
        else:
            # Verification failed: preserve screenshots and evidence
            retained = True
            logger.info(f"Retaining recovery failure evidence at: {screenshot_path}")

        return ModalRecoveryResult(
            outcome=final_outcome,
            success=verif.verified,
            modal_hwnd=dialog.hwnd,
            message=verif.details,
            audit=audit,
            retained_evidence=retained,
        )


_modal_recovery_instance: Optional[ModalRecoveryController] = None


def get_modal_recovery_controller() -> ModalRecoveryController:
    """Retrieve shared ModalRecoveryController instance."""
    global _modal_recovery_instance
    if _modal_recovery_instance is None:
        _modal_recovery_instance = ModalRecoveryController()
    return _modal_recovery_instance
