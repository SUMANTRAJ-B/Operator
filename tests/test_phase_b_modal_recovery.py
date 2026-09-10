"""Comprehensive test suite for Phase B Generic Visual Recovery / Modal Handling.

Validates the Phase B architectural requirements and safety invariants:
1. IDYES / affirmative control is NOT treated as inherently safe.
2. Destructive / high-risk modal produces HIGH_RISK -> SAFE_STOP.
3. Multi-signal state verification verifies recovery even if modal HWND lingers but is no longer blocking.
4. NO_SAFE_ACTION causes safe stop without dispatching mouse clicks.
5. AMBIGUOUS dialog candidate evaluation stops safely.
6. CentralSafetyGate strictly forbids targeting or clicking protected host dialogs.
7. Pre-action freshness validation and GroundingRegistry compliance.
8. Ephemeral screenshot lifecycle: delete on verified recovery, retain on failure/safe stop.
9. AgentCore integration with modal recovery state machine.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from actions.applications import AppController
from actions.mouse import MouseController
from agent.core import OperatorAgent
from agent.modal_recovery import (
    ControlRole,
    ModalDialogState,
    ModalRecoveryAudit,
    ModalRecoveryController,
    RecoveryCandidate,
    RecoveryCandidateEvaluator,
    RecoveryOutcome,
    RiskLevel,
)
from agent.state import AgentState, AgentStatus
from perception.controller import PerceptionController, ScreenObservation
from perception.grounding import GroundingEvidence, GroundingSource, get_grounding_registry
from providers.llm.base import LLMResponse, ToolCall
from providers.vision.base import BoundingBox, DetectedUIElement
from safety.policy import CentralSafetyGate, SafetyPolicy
from verification.verifier import ActionVerifier, VerificationResult


@pytest.fixture
def mock_screen_size():
    return (1920, 1080)


# ---------------------------------------------------------------------------
# Correction 1: IDYES / affirmative control is NOT inherently safe
# ---------------------------------------------------------------------------


def test_idyes_not_automatically_authorized_on_destructive_prompt(mock_screen_size):
    """Test that IDYES / affirmative controls are NOT treated as inherently safe.

    A destructive prompt ('Permanently delete and erase all items') with an IDYES
    control must be classified as POTENTIALLY_DESTRUCTIVE and produce HIGH_RISK -> SAFE_STOP,
    not an automatic click.
    """
    evaluator = RecoveryCandidateEvaluator()
    dialog = ModalDialogState(
        hwnd=12345,
        title="Confirm Deletion",
        class_name="#32770",
        message_text="Warning: This action will permanently delete and erase all files.",
        controls=[
            {
                "label": "Yes",
                "control_id": 6,  # IDYES
                "type": "button",
                "bounds": (500, 400, 600, 450),
                "is_default": True,
                "is_enabled": True,
            },
            {
                "label": "Cancel",
                "control_id": 2,  # IDCANCEL
                "type": "button",
                "bounds": (620, 400, 720, 450),
                "is_default": False,
                "is_enabled": True,
            },
        ],
    )

    # Goal is standard, not asking for permanent deletion
    decision = evaluator.evaluate_candidates(
        dialog=dialog,
        goal="Open sample.txt and read content",
        screen_size=mock_screen_size,
    )

    assert decision.outcome == RecoveryOutcome.HIGH_RISK
    assert decision.candidate is None
    assert decision.action_type == "NONE"
    assert "destructive prompt" in decision.reason.lower()


def test_affirmative_control_permitted_only_when_task_aligned(mock_screen_size):
    """Test that affirmative controls receive safe authorization ONLY when aligned with task goal."""
    evaluator = RecoveryCandidateEvaluator()
    dialog = ModalDialogState(
        hwnd=12346,
        title="Save Changes",
        class_name="#32770",
        message_text="Do you want to save changes to Document?",
        controls=[
            {
                "label": "&Save",
                "control_id": 1,  # IDOK
                "type": "button",
                "bounds": (500, 400, 600, 450),
                "is_default": True,
                "is_enabled": True,
            },
            {
                "label": "Cancel",
                "control_id": 2,  # IDCANCEL
                "type": "button",
                "bounds": (620, 400, 720, 450),
                "is_default": False,
                "is_enabled": True,
            },
        ],
    )

    decision = evaluator.evaluate_candidates(
        dialog=dialog,
        goal="Save the document and close",
        screen_size=mock_screen_size,
    )

    assert decision.outcome == RecoveryOutcome.RECOVERED
    assert decision.candidate is not None
    assert decision.candidate.control_id == 1
    assert decision.action_type == "GROUNDED_CLICK"


# ---------------------------------------------------------------------------
# Correction 2 & 3: Multi-signal state verification (modal lingering but not blocking)
# ---------------------------------------------------------------------------


def test_modal_lingering_but_hidden_and_focus_released_verifies():
    """Test that modal lingering in memory (IsWindow=True) but hidden (IsWindowVisible=False)

    and with focus safely returned to target window verifies successfully via multi-signal check.
    """
    mock_apps = MagicMock(spec=AppController)
    mock_active = MagicMock()
    mock_active.hwnd = 99999
    mock_active.title = "Untitled - Notepad"
    mock_active.pid = 4321
    mock_apps.get_active_window.return_value = mock_active
    mock_apps.is_protected_target.return_value = False

    verifier = ActionVerifier()
    verifier.app_controller = mock_apps

    # Mock win32gui to simulate lingering HWND that is no longer visible
    with patch("win32gui.IsWindow", return_value=True), \
         patch("win32gui.IsWindowVisible", return_value=False), \
         patch("win32gui.IsWindowEnabled", return_value=True):
        res = verifier.verify_modal_dismissed(
            modal_hwnd=88888,
            target_hwnd=99999,
            target_title_or_query="Notepad",
        )

    assert res.verified is True
    assert "is_visible=False" in res.details
    assert "focus_released=True" in res.details


def test_modal_alive_and_visible_but_target_restored_and_enabled_verifies():
    """Test Combination 3: modal remains alive and visible, but is no longer blocking

    because target interaction is restored, expected target window is foreground,
    target is enabled, and focus safely released from modal.
    """
    mock_apps = MagicMock(spec=AppController)
    mock_active = MagicMock()
    mock_active.hwnd = 99999
    mock_active.title = "Untitled - Notepad"
    mock_active.pid = 4321
    mock_apps.get_active_window.return_value = mock_active
    mock_apps.is_protected_target.return_value = False

    verifier = ActionVerifier()
    verifier.app_controller = mock_apps

    # Modal HWND 88888 exists and is visible, but focus has shifted to target 99999 which is enabled
    with patch("win32gui.IsWindow", return_value=True), \
         patch("win32gui.IsWindowVisible", return_value=True), \
         patch("win32gui.IsWindowEnabled", return_value=True):
        res = verifier.verify_modal_dismissed(
            modal_hwnd=88888,
            target_hwnd=99999,
            target_title_or_query="Notepad",
        )

    assert res.verified is True
    assert "no longer captures task focus" in res.details
    assert "foreground and enabled" in res.details


def test_modal_focus_shifted_to_protected_fails_verification():
    """Test that if modal dismissal results in focus shifting to a protected IDE/runner,

    verification is strictly rejected to prevent typing/clicking into host.
    """
    mock_apps = MagicMock(spec=AppController)
    mock_active = MagicMock()
    mock_active.hwnd = 11111
    mock_active.title = "Antigravity IDE - Operator"
    mock_active.pid = 9999
    mock_apps.get_active_window.return_value = mock_active
    mock_apps.is_protected_target.return_value = True

    verifier = ActionVerifier()
    verifier.app_controller = mock_apps

    with patch("win32gui.IsWindow", return_value=False):
        res = verifier.verify_modal_dismissed(modal_hwnd=88888)

    assert res.verified is False
    assert "protected host window" in res.details


# ---------------------------------------------------------------------------
# Correction 4: NO_SAFE_ACTION causes safe stop without clicking
# ---------------------------------------------------------------------------


def test_no_safe_action_halts_safely_without_clicking(mock_screen_size):
    """Test that a dialog with no enabled controls produces NO_SAFE_ACTION and halts without mouse actions."""
    mock_mouse = MagicMock(spec=MouseController)
    mock_perception = MagicMock(spec=PerceptionController)
    mock_verifier = MagicMock(spec=ActionVerifier)
    mock_safety = MagicMock(spec=SafetyPolicy)

    obs = ScreenObservation(
        observation_id="obs_no_safe_action_123",
        timestamp=1000.0,
        screen_size=mock_screen_size,
        modal_dialog={
            "hwnd": 55555,
            "title": "Unresponsive Dialog",
            "class_name": "#32770",
            "controls": [
                {"label": "Disabled Button", "type": "button", "is_enabled": False, "bounds": (100, 100, 200, 150)}
            ],
        },
    )
    mock_perception.fresh_screen_observation.return_value = obs

    controller = ModalRecoveryController(verifier=mock_verifier)
    controller.perception = mock_perception
    controller.mouse_controller = mock_mouse
    controller.safety_policy = mock_safety

    with patch("win32gui.GetForegroundWindow", return_value=55555), \
         patch("win32gui.IsWindow", return_value=True), \
         patch("win32gui.GetWindowText", return_value="Unresponsive Dialog"):
        res = controller.attempt_recovery(
            task_id="task_test_no_action",
            goal="Test goal",
        )

    assert res.outcome == RecoveryOutcome.NO_SAFE_ACTION
    assert res.success is False
    assert res.retained_evidence is True
    # Verify mouse was NEVER clicked
    mock_mouse.click_at.assert_not_called()


def test_ambiguous_dialog_candidates_trigger_safe_stop(mock_screen_size):
    """Test that ambiguous dialogs with conflicting equal-score choices halt safely."""
    evaluator = RecoveryCandidateEvaluator()
    dialog = ModalDialogState(
        hwnd=77777,
        title="Ambiguous Option",
        class_name="#32770",
        message_text="Choose one of two conflicting options.",
        controls=[
            {
                "label": "Option A (Proceed)",
                "control_id": 6,
                "type": "button",
                "bounds": (500, 400, 600, 450),
                "is_default": False,
                "is_enabled": True,
            },
            {
                "label": "Option B (Reject)",
                "control_id": 7,
                "type": "button",
                "bounds": (620, 400, 720, 450),
                "is_default": False,
                "is_enabled": True,
            },
        ],
    )

    decision = evaluator.evaluate_candidates(
        dialog=dialog,
        goal="Perform some neutral work",
        screen_size=mock_screen_size,
    )

    # Both choices are conflicting without clear task guidance
    assert decision.outcome in (RecoveryOutcome.AMBIGUOUS, RecoveryOutcome.NO_SAFE_ACTION)
    assert decision.candidate is None
    assert decision.action_type == "NONE"


# ---------------------------------------------------------------------------
# CentralSafetyGate Protection
# ---------------------------------------------------------------------------


def test_modal_on_protected_process_tree_strictly_immune():
    """Test that an unexpected modal dialog owned by PowerShell, Windows Terminal, or IDE

    is strictly barred from recovery action.
    """
    mock_apps = MagicMock(spec=AppController)
    mock_apps.is_protected_target_with_reason.return_value = (True, "Matched protected terminal")

    mock_mouse = MagicMock(spec=MouseController)
    mock_perception = MagicMock(spec=PerceptionController)

    obs = ScreenObservation(
        observation_id="obs_prot_tree_123",
        timestamp=1000.0,
        screen_size=(1920, 1080),
        modal_dialog={
            "hwnd": 99999,
            "title": "Windows PowerShell Confirmation",
            "class_name": "#32770",
            "controls": [{"label": "OK", "type": "button", "bounds": (100, 100, 200, 150)}],
        },
    )
    mock_perception.fresh_screen_observation.return_value = obs

    controller = ModalRecoveryController()
    controller.app_controller = mock_apps
    controller.mouse_controller = mock_mouse
    controller.perception = mock_perception

    with patch("win32gui.GetForegroundWindow", return_value=99999), \
         patch("win32gui.IsWindow", return_value=True), \
         patch("win32gui.GetWindowText", return_value="Windows PowerShell Confirmation"):
        res = controller.attempt_recovery(
            task_id="task_prot_123",
            goal="Do something",
        )

    assert res.outcome == RecoveryOutcome.SAFE_STOP
    assert res.success is False
    assert res.retained_evidence is True
    mock_mouse.click_at.assert_not_called()


# ---------------------------------------------------------------------------
# Screenshot Lifecycle
# ---------------------------------------------------------------------------


def test_screenshot_lifecycle_deleted_on_verified_recovery(tmp_path):
    """Test that ephemeral screenshot is unlinked upon verified successful recovery."""
    shot_path = tmp_path / "ephemeral_recovery.png"
    shot_path.write_bytes(b"fake image data")
    assert shot_path.exists()

    mock_perception = MagicMock(spec=PerceptionController)
    obs = ScreenObservation(
        observation_id="obs_success_shot",
        timestamp=1000.0,
        screen_size=(1920, 1080),
        screenshot_path=shot_path,
        modal_dialog={
            "hwnd": 44444,
            "title": "Notice",
            "class_name": "#32770",
            "controls": [{"label": "OK", "control_id": 1, "type": "button", "bounds": (100, 100, 200, 150), "is_enabled": True}],
        },
    )
    mock_perception.fresh_screen_observation.return_value = obs

    mock_verifier = MagicMock(spec=ActionVerifier)
    mock_verifier.verify_modal_dismissed.return_value = VerificationResult(
        verified=True, details="Modal destroyed."
    )

    mock_safety = MagicMock(spec=SafetyPolicy)
    mock_safety.evaluate_action.return_value = MagicMock(allowed=True, reason="OK")

    mock_mouse = MagicMock(spec=MouseController)

    controller = ModalRecoveryController(verifier=mock_verifier)
    controller.perception = mock_perception
    controller.mouse_controller = mock_mouse
    controller.safety_policy = mock_safety
    controller.grounding_registry = MagicMock()
    controller.grounding_registry.get_observation_age.return_value = 0.1

    with patch("win32gui.GetForegroundWindow", return_value=44444), \
         patch("win32gui.IsWindow", return_value=True), \
         patch("win32gui.GetWindowText", return_value="Notice"):
        res = controller.attempt_recovery(
            task_id="task_shot_success",
            goal="Acknowledge notice",
        )

    assert res.outcome == RecoveryOutcome.RECOVERED
    assert res.success is True
    assert res.retained_evidence is False
    # Verified that cleanup_observation_screenshot was called
    mock_perception.cleanup_observation_screenshot.assert_called()


def test_screenshot_lifecycle_retained_on_failed_recovery(tmp_path):
    """Test that diagnostic screenshot is preserved on failure."""
    shot_path = tmp_path / "failure_recovery.png"
    shot_path.write_bytes(b"fake failure image")

    mock_perception = MagicMock(spec=PerceptionController)
    obs = ScreenObservation(
        observation_id="obs_fail_shot",
        timestamp=1000.0,
        screen_size=(1920, 1080),
        screenshot_path=shot_path,
        modal_dialog={
            "hwnd": 33333,
            "title": "Persistent Modal",
            "class_name": "#32770",
            "controls": [{"label": "OK", "control_id": 1, "type": "button", "bounds": (100, 100, 200, 150), "is_enabled": True}],
        },
    )
    mock_perception.fresh_screen_observation.return_value = obs

    mock_verifier = MagicMock(spec=ActionVerifier)
    mock_verifier.verify_modal_dismissed.return_value = VerificationResult(
        verified=False, details="Modal still visible."
    )

    mock_safety = MagicMock(spec=SafetyPolicy)
    mock_safety.evaluate_action.return_value = MagicMock(allowed=True, reason="OK")

    mock_mouse = MagicMock(spec=MouseController)

    controller = ModalRecoveryController(verifier=mock_verifier)
    controller.perception = mock_perception
    controller.mouse_controller = mock_mouse
    controller.safety_policy = mock_safety
    controller.grounding_registry = MagicMock()
    controller.grounding_registry.get_observation_age.return_value = 0.1

    with patch("win32gui.GetForegroundWindow", return_value=33333), \
         patch("win32gui.IsWindow", return_value=True), \
         patch("win32gui.GetWindowText", return_value="Persistent Modal"):
        res = controller.attempt_recovery(
            task_id="task_shot_fail",
            goal="Acknowledge notice",
        )

    assert res.outcome == RecoveryOutcome.FAILED
    assert res.success is False
    assert res.retained_evidence is True
    # Ensure file was not deleted
    assert shot_path.exists()


# ---------------------------------------------------------------------------
# OperatorAgent Integration
# ---------------------------------------------------------------------------


def test_agent_core_recovers_when_target_lock_blocked():
    """Test that when OperatorAgent encounters focus blocked by a modal during target locking,

    it engages ModalRecoveryController, verifies dismissal, and resumes the task cleanly.
    """
    mock_llm = MagicMock()
    mock_llm.chat.side_effect = [
        LLMResponse(
            content="Typing text into Notepad",
            tool_calls=[ToolCall(id="tc1", name="type_text", arguments={"text": "Hello", "window_title_or_hwnd": "Notepad"})],
        ),
        LLMResponse(content="Task finished successfully", tool_calls=[]),
    ]

    mock_apps = MagicMock(spec=AppController)
    # First ensure_target_focused fails (blocked), second succeeds (recovered)
    mock_apps.ensure_target_focused.side_effect = [False, True]
    mock_apps.get_active_window.return_value = MagicMock(hwnd=10101, title="Notepad", pid=1234)
    mock_apps.list_windows.return_value = []

    mock_modal_rec = MagicMock(spec=ModalRecoveryController)
    mock_modal_rec.is_blocking_modal.return_value = (True, ModalDialogState(hwnd=20202, title="Prompt", class_name="#32770"))
    mock_audit = ModalRecoveryAudit(
        audit_id="audit_core_123",
        task_id="test_task",
        timestamp=1000.0,
        modal_hwnd=20202,
        modal_title="Prompt",
        dialog_message="Confirm prompt",
        outcome=RecoveryOutcome.RECOVERED,
        candidates=[],
        chosen_candidate={"label": "OK"},
        action_type="GROUNDED_CLICK",
        safety_decision={"allowed": True},
        verification_details="Modal dismissed.",
    )
    mock_modal_rec.attempt_recovery.return_value = MagicMock(
        success=True,
        outcome=RecoveryOutcome.RECOVERED,
        message="Modal dismissed.",
        audit=mock_audit,
    )

    agent = OperatorAgent(
        llm=mock_llm,
        cleanup_on_finish=False,
    )
    agent.apps = mock_apps
    agent.modal_recovery = mock_modal_rec

    state = agent.run(goal="Type into Notepad")

    # Assert modal recovery was engaged and audit recorded into state
    mock_modal_rec.attempt_recovery.assert_called_once()
    assert len(state.modal_recovery_audits) == 1
    assert state.modal_recovery_audits[0]["audit_id"] == "audit_core_123"


def test_pre_action_freshness_reobserves_when_stale():
    """Test that pre-action freshness validation re-observes if observation is older than 2.0s."""
    mock_perception = MagicMock(spec=PerceptionController)
    stale_obs = ScreenObservation(
        observation_id="obs_stale_1",
        timestamp=100.0,
        screen_size=(1920, 1080),
        modal_dialog={
            "hwnd": 11112,
            "title": "Stale Dialog",
            "class_name": "#32770",
            "controls": [{"label": "OK", "control_id": 1, "type": "button", "bounds": (100, 100, 200, 150), "is_enabled": True}],
        },
    )
    fresh_obs = ScreenObservation(
        observation_id="obs_fresh_2",
        timestamp=105.0,
        screen_size=(1920, 1080),
        modal_dialog={
            "hwnd": 11112,
            "title": "Stale Dialog",
            "class_name": "#32770",
            "controls": [{"label": "OK", "control_id": 1, "type": "button", "bounds": (100, 100, 200, 150), "is_enabled": True}],
        },
    )
    mock_perception.fresh_screen_observation.side_effect = [stale_obs, fresh_obs, fresh_obs]

    mock_verifier = MagicMock(spec=ActionVerifier)
    mock_verifier.verify_modal_dismissed.return_value = VerificationResult(verified=True, details="Dismissed.")

    mock_safety = MagicMock(spec=SafetyPolicy)
    mock_safety.evaluate_action.return_value = MagicMock(allowed=True, reason="OK")

    mock_mouse = MagicMock(spec=MouseController)

    controller = ModalRecoveryController(verifier=mock_verifier)
    controller.perception = mock_perception
    controller.mouse_controller = mock_mouse
    controller.safety_policy = mock_safety
    controller.grounding_registry = MagicMock()
    # First check reports 3.5s age (> 2.0s threshold), triggering re-observation
    controller.grounding_registry.get_observation_age.side_effect = [3.5, 0.1]

    with patch("win32gui.GetForegroundWindow", return_value=11112), \
         patch("win32gui.IsWindow", return_value=True), \
         patch("win32gui.GetWindowText", return_value="Stale Dialog"):
        res = controller.attempt_recovery(
            task_id="task_freshness",
            goal="Dismiss dialog",
        )

    assert res.outcome == RecoveryOutcome.RECOVERED
    assert res.success is True
    # Verify fresh observation was called multiple times due to re-observation
    assert mock_perception.fresh_screen_observation.call_count >= 2


def test_modal_dismissed_via_destruction_verifies():
    """Test that verify_modal_dismissed succeeds when window destruction signal is received."""
    mock_apps = MagicMock(spec=AppController)
    mock_apps.get_active_window.return_value = MagicMock(hwnd=54321, title="Safe App", pid=100)
    mock_apps.is_protected_target.return_value = False

    verifier = ActionVerifier()
    verifier.app_controller = mock_apps

    with patch("win32gui.IsWindow", return_value=False):
        res = verifier.verify_modal_dismissed(modal_hwnd=77777)

    assert res.verified is True
    assert "IsWindow == False" in res.details


def test_high_risk_modal_causes_agent_safe_stop():
    """Test that when an unexpected high-risk modal appears, OperatorAgent stops safely."""
    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(
        content="Type into editor",
        tool_calls=[ToolCall(id="tc1", name="type_text", arguments={"text": "data", "window_title_or_hwnd": "Editor"})],
    )

    mock_apps = MagicMock(spec=AppController)
    mock_apps.ensure_target_focused.return_value = False
    mock_apps.get_active_window.return_value = MagicMock(hwnd=88888, title="Format Confirmation", pid=123)
    mock_apps.list_windows.return_value = []

    mock_modal_rec = MagicMock(spec=ModalRecoveryController)
    mock_modal_rec.is_blocking_modal.return_value = (True, ModalDialogState(hwnd=88888, title="Format Confirmation", class_name="#32770"))
    mock_audit = ModalRecoveryAudit(
        audit_id="audit_high_risk",
        task_id="test_task",
        timestamp=1000.0,
        modal_hwnd=88888,
        modal_title="Format Confirmation",
        dialog_message="Warning: This will format disk.",
        outcome=RecoveryOutcome.HIGH_RISK,
        candidates=[],
        chosen_candidate=None,
        action_type="NONE",
        safety_decision={"allowed": False, "reason": "High risk destructive dialog."},
        error_message="High risk prompt",
    )
    mock_modal_rec.attempt_recovery.return_value = MagicMock(
        success=False,
        outcome=RecoveryOutcome.HIGH_RISK,
        message="Destructive prompt outside task scope.",
        audit=mock_audit,
    )

    agent = OperatorAgent(
        llm=mock_llm,
        cleanup_on_finish=False,
    )
    agent.apps = mock_apps
    agent.modal_recovery = mock_modal_rec

    state = agent.run(goal="Edit file")

    assert state.status == AgentStatus.FAILED
    assert "safe stop" in state.error.lower()
    assert len(state.modal_recovery_audits) == 1
    assert state.modal_recovery_audits[0]["outcome"] == "high_risk"
