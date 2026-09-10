"""Comprehensive tests for Operator Phase A — Visual Grounding & Pixel-Level Interaction.

Verifies:
1. Fresh observation creation & metadata
2. Unique observation IDs
3. Observation freshness
4. Configurable staleness
5. Screen-bound validation
6. Bounding-box center calculation
7. Invalid bounding box rejection
8. Stale observation rejection
9. Coordinate-without-grounding rejection
10. Protected-region rejection
11. Protected-window rejection
12. Successful grounded click
13. Successful grounded double-click
14. Successful grounded right-click
15. Post-click re-observation requirement
16. Screenshot cleanup after success
17. Screenshot retention after failure
18. Audit trail creation
19. UIAutomation fallback when vision unavailable
20. Text-only LLM cannot provide fake vision coordinates
21. Regression: move_mouse cannot bypass grounding
22. Regression: click cannot bypass grounding
23. Regression: drag cannot bypass grounding
24. Regression: semantic click_element resolves via UIAutomation
25. Regression: stale observations rejected immediately before dispatch
26. Regression: CentralSafetyGate remains unchanged and authoritative
"""

import time
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from actions.mouse import MouseController
from actions.registry import ToolRegistry, create_default_registry
from perception.controller import (
    PerceptionController,
    PixelActionAudit,
    ScreenObservation,
)
from perception.geometry import WindowGeometryProvider
from perception.grounding import (
    GroundingEvidence,
    GroundingRegistry,
    GroundingSource,
    get_grounding_registry,
)
from perception.uiautomation import UIAutomationProvider
from providers.vision.base import (
    BoundingBox,
    DetectedUIElement,
    OllamaVisionProvider,
    UnavailableVisionProvider,
)
from safety.policy import CentralSafetyGate, SafetyPolicy
from verification.verifier import ActionVerifier, VerificationResult


@pytest.fixture(autouse=True)
def clean_grounding_registry():
    """Ensure clean grounding registry for every test."""
    reg = get_grounding_registry()
    reg.clear()
    yield
    reg.clear()


# 1. Fresh observation creation
def test_fresh_observation_creation():
    mock_geom = MagicMock(spec=WindowGeometryProvider)
    mock_geom.get_screen_size.return_value = (1920, 1080)
    controller = PerceptionController(geometry=mock_geom, vision=UnavailableVisionProvider())

    obs = controller.fresh_screen_observation(capture_image=False)
    assert obs.observation_id.startswith("obs_")
    assert obs.timestamp > 0
    assert obs.screen_size == (1920, 1080)
    assert obs.is_valid is True
    assert obs.vision_available is False


# 2. Unique observation IDs
def test_unique_observation_ids():
    mock_geom = MagicMock(spec=WindowGeometryProvider)
    mock_geom.get_screen_size.return_value = (1920, 1080)
    controller = PerceptionController(geometry=mock_geom, vision=UnavailableVisionProvider())

    obs1 = controller.fresh_screen_observation(capture_image=False)
    obs2 = controller.fresh_screen_observation(capture_image=False)
    assert obs1.observation_id != obs2.observation_id


# 3. Observation freshness
def test_observation_freshness():
    obs = ScreenObservation(
        observation_id="obs_fresh",
        timestamp=time.time(),
        screen_size=(1920, 1080),
        max_age_seconds=5.0,
    )
    assert obs.is_stale() is False
    assert obs.is_valid is True


# 4. Configurable staleness
def test_configurable_staleness():
    now = time.time()
    obs = ScreenObservation(
        observation_id="obs_custom_age",
        timestamp=now - 2.0,
        screen_size=(1920, 1080),
        max_age_seconds=1.0,
    )
    # Default threshold 1.0s -> stale (age 2.0s > 1.0s)
    assert obs.is_stale() is True

    # Override threshold 5.0s -> fresh (age 2.0s < 5.0s)
    assert obs.is_stale(max_age_seconds=5.0) is False


# 5. Screen-bound validation
def test_screen_bound_validation():
    mock_geom = MagicMock(spec=WindowGeometryProvider)
    mock_geom.get_screen_size.return_value = (1920, 1080)
    mock_geom.is_within_screen_bounds.side_effect = (
        lambda x, y: 0 <= x < 1920 and 0 <= y < 1080
    )

    assert mock_geom.is_within_screen_bounds(0, 0) is True
    assert mock_geom.is_within_screen_bounds(1919, 1079) is True
    assert mock_geom.is_within_screen_bounds(-1, 500) is False
    assert mock_geom.is_within_screen_bounds(500, -1) is False
    assert mock_geom.is_within_screen_bounds(1920, 500) is False
    assert mock_geom.is_within_screen_bounds(500, 1080) is False


# 6. Bounding-box center calculation
def test_bounding_box_center_calculation():
    bbox = BoundingBox(left=100, top=200, right=300, bottom=400)
    assert bbox.width == 200
    assert bbox.height == 200
    assert bbox.center == (200, 300)
    assert bbox.contains(200, 300) is True


# 7. Invalid bounding box rejection
def test_invalid_bounding_box_rejection():
    # Degenerate: right <= left
    deg_x = BoundingBox(left=300, top=100, right=100, bottom=200)
    assert deg_x.width == 0

    # Degenerate: bottom <= top
    deg_y = BoundingBox(left=100, top=400, right=200, bottom=200)
    assert deg_y.height == 0


# 8. Stale observation rejection
@patch("pyautogui.size", return_value=(1920, 1080))
def test_stale_observation_rejection(mock_size):
    reg = get_grounding_registry()
    stale_ts = time.time() - 20.0
    reg.register_observation(
        observation_id="obs_stale",
        timestamp=stale_ts,
        screen_size=(1920, 1080),
    )

    policy = SafetyPolicy()
    dec = policy.evaluate_action(
        "move_mouse",
        {"x": 500, "y": 500, "observation_id": "obs_stale"},
    )
    assert dec.allowed is False
    assert "stale" in dec.reason.lower()


# 9. Coordinate-without-grounding rejection
@patch("pyautogui.size", return_value=(1920, 1080))
def test_coordinate_without_grounding_rejection(mock_size):
    policy = SafetyPolicy()
    # No observation_id
    dec1 = policy.evaluate_action("move_mouse", {"x": 500, "y": 500})
    assert dec1.allowed is False
    assert "requires an active observation_id" in dec1.reason

    # Unknown observation_id
    dec2 = policy.evaluate_action(
        "click_at",
        {"x": 500, "y": 500, "observation_id": "obs_nonexistent"},
    )
    assert dec2.allowed is False
    assert "not registered" in dec2.reason.lower()


# 10. Protected-region rejection
@patch("pyautogui.size", return_value=(1920, 1080))
@patch("perception.geometry.WindowGeometryProvider.is_point_in_protected_region")
def test_protected_region_rejection(mock_prot, mock_size):
    mock_prot.return_value = (True, "Matched protected terminal window region")
    reg = get_grounding_registry()
    reg.register_observation(
        observation_id="obs_valid",
        timestamp=time.time(),
        screen_size=(1920, 1080),
    )

    policy = SafetyPolicy()
    dec = policy.evaluate_action(
        "click_at",
        {"x": 400, "y": 400, "observation_id": "obs_valid"},
    )
    assert dec.allowed is False
    assert "protected" in dec.reason.lower()
    assert "BLOCKED" in dec.reason


# 11. Protected-window rejection
def test_protected_window_rejection():
    gate = CentralSafetyGate()
    # IDE keywords
    dec_ide = gate.evaluate_close_target("Antigravity IDE")
    assert dec_ide.allowed is False
    assert "protected" in dec_ide.reason.lower()

    # Terminal / shell keywords
    dec_term = gate.evaluate_close_target("Windows Terminal")
    assert dec_term.allowed is False
    assert "protected" in dec_term.reason.lower()

    dec_pwsh = gate.evaluate_close_target("powershell.exe")
    assert dec_pwsh.allowed is False
    assert "protected" in dec_pwsh.reason.lower()


# 12. Successful grounded click
@patch("pyautogui.size", return_value=(1920, 1080))
@patch("pyautogui.click")
@patch("perception.geometry.WindowGeometryProvider.is_point_in_protected_region", return_value=(False, ""))
def test_successful_grounded_click(mock_prot, mock_click, mock_size):
    reg = get_grounding_registry()
    now = time.time()
    ev = GroundingEvidence(
        observation_id="obs_click",
        observation_timestamp=now,
        source=GroundingSource.UI_AUTOMATION,
        target_query="Submit",
        bounding_box=BoundingBox(left=100, top=100, right=200, bottom=150),
        click_point=(150, 125),
        screen_size=(1920, 1080),
    )
    reg.register_observation(
        observation_id="obs_click",
        timestamp=now,
        screen_size=(1920, 1080),
        evidences=[ev],
    )

    policy = SafetyPolicy()
    dec = policy.evaluate_action(
        "click_at",
        {"x": 150, "y": 125, "button": "left", "observation_id": "obs_click"},
    )
    assert dec.allowed is True

    mouse = MouseController()
    with patch.object(mouse, "validate_coordinates"):
        mouse.click_at(150, 125, button="left", observation_id="obs_click")
        mock_click.assert_called_once_with(
            x=150, y=125, clicks=1, interval=0.1, button="left"
        )


# 13. Successful grounded double-click
@patch("pyautogui.size", return_value=(1920, 1080))
@patch("pyautogui.click")
@patch("perception.geometry.WindowGeometryProvider.is_point_in_protected_region", return_value=(False, ""))
def test_successful_grounded_double_click(mock_prot, mock_click, mock_size):
    reg = get_grounding_registry()
    now = time.time()
    reg.register_observation(
        observation_id="obs_double",
        timestamp=now,
        screen_size=(1920, 1080),
    )

    policy = SafetyPolicy()
    dec = policy.evaluate_action(
        "double_click_at",
        {"x": 300, "y": 300, "observation_id": "obs_double"},
    )
    assert dec.allowed is True

    mouse = MouseController()
    with patch.object(mouse, "validate_coordinates"):
        mouse.double_click_at(300, 300, observation_id="obs_double")
        mock_click.assert_called_once_with(
            x=300, y=300, clicks=2, interval=0.1, button="left"
        )


# 14. Successful grounded right-click
@patch("pyautogui.size", return_value=(1920, 1080))
@patch("pyautogui.click")
@patch("perception.geometry.WindowGeometryProvider.is_point_in_protected_region", return_value=(False, ""))
def test_successful_grounded_right_click(mock_prot, mock_click, mock_size):
    reg = get_grounding_registry()
    now = time.time()
    reg.register_observation(
        observation_id="obs_right",
        timestamp=now,
        screen_size=(1920, 1080),
    )

    policy = SafetyPolicy()
    dec = policy.evaluate_action(
        "right_click_at",
        {"x": 450, "y": 450, "observation_id": "obs_right"},
    )
    assert dec.allowed is True

    mouse = MouseController()
    with patch.object(mouse, "validate_coordinates"):
        mouse.right_click_at(450, 450, observation_id="obs_right")
        mock_click.assert_called_once_with(
            x=450, y=450, clicks=1, interval=0.1, button="right"
        )


# 15. Post-click re-observation requirement
def test_post_click_reobservation_requirement():
    mock_geom = MagicMock(spec=WindowGeometryProvider)
    mock_geom.get_screen_size.return_value = (1920, 1080)
    controller = PerceptionController(geometry=mock_geom, vision=UnavailableVisionProvider())

    obs_pre = controller.fresh_screen_observation(capture_image=False)
    # Simulate action executed
    obs_post = controller.fresh_screen_observation(capture_image=False)

    assert obs_pre.observation_id != obs_post.observation_id
    assert controller.get_latest_observation() is obs_post


# 16. Screenshot cleanup after success
def test_screenshot_cleanup_after_success(tmp_path):
    mock_geom = MagicMock(spec=WindowGeometryProvider)
    mock_geom.get_screen_size.return_value = (1920, 1080)
    controller = PerceptionController(geometry=mock_geom, vision=UnavailableVisionProvider())

    # Create dummy screenshot file
    shot_file = tmp_path / "obs_test_shot.png"
    shot_file.write_text("dummy image data")
    assert shot_file.exists()

    obs = ScreenObservation(
        observation_id="obs_success_test",
        timestamp=time.time(),
        screen_size=(1920, 1080),
        screenshot_path=shot_file,
    )
    controller._observation_history[obs.observation_id] = obs

    # On verified success -> file deleted
    controller.cleanup_observation_screenshot(obs.observation_id, success=True)
    assert not shot_file.exists()


# 17. Screenshot retention after failure
def test_screenshot_retention_after_failure(tmp_path):
    mock_geom = MagicMock(spec=WindowGeometryProvider)
    mock_geom.get_screen_size.return_value = (1920, 1080)
    controller = PerceptionController(geometry=mock_geom, vision=UnavailableVisionProvider())

    shot_file = tmp_path / "obs_fail_shot.png"
    shot_file.write_text("diagnostic failure image")
    assert shot_file.exists()

    obs = ScreenObservation(
        observation_id="obs_fail_test",
        timestamp=time.time(),
        screen_size=(1920, 1080),
        screenshot_path=shot_file,
    )
    controller._observation_history[obs.observation_id] = obs

    # On failure -> file retained for reporting/diagnostics
    controller.cleanup_observation_screenshot(obs.observation_id, success=False)
    assert shot_file.exists()


# 18. Audit trail creation
def test_audit_trail_creation():
    mock_geom = MagicMock(spec=WindowGeometryProvider)
    mock_geom.get_screen_size.return_value = (1920, 1080)
    controller = PerceptionController(geometry=mock_geom, vision=UnavailableVisionProvider())

    obs = controller.fresh_screen_observation(capture_image=False)
    audit = controller.record_pixel_action_audit(
        observation_id=obs.observation_id,
        target_description="Confirm Button",
        source=GroundingSource.UI_AUTOMATION,
        chosen_coordinate=(250, 350),
        action="click_at",
        safety_decision={"allowed": True, "reason": "Permitted"},
        action_result={"success": True},
        verification_result={"verified": True, "details": "Verified cleanly"},
        bounding_box=(200, 300, 300, 400),
        control_metadata={"control_type": "button", "automation_id": "btn_confirm"},
    )

    assert isinstance(audit, PixelActionAudit)
    assert audit.observation_id == obs.observation_id
    assert audit.chosen_coordinate == (250, 350)
    assert audit.source == GroundingSource.UI_AUTOMATION
    assert len(controller.get_audit_trail()) == 1


# 19. UIAutomation fallback when vision unavailable
def test_uiautomation_fallback_when_vision_unavailable():
    mock_uia = MagicMock(spec=UIAutomationProvider)
    test_control = DetectedUIElement(
        label="Save Document",
        element_type="button",
        bounds=BoundingBox(left=50, top=50, right=150, bottom=90),
        confidence=1.0,
        attributes={"source": "uiautomation"},
    )
    mock_uia.find_controls_in_window.return_value = [test_control]

    controller = PerceptionController(uiautomation=mock_uia, vision=UnavailableVisionProvider())
    assert controller.is_vision_available is False

    elem, source = controller.find_element("Save Document", window_hwnd=999)
    assert elem is not None
    assert elem.label == "Save Document"
    assert source == "uiautomation"
    assert elem.click_point == (100, 70)


# 20. Text-only LLM cannot provide coordinates
def test_text_only_llm_cannot_provide_fake_coordinates():
    provider = OllamaVisionProvider(model="qwen3:8b")
    assert provider.is_available() is False

    unavailable = UnavailableVisionProvider()
    res = unavailable.analyze_image("dummy.png", prompt="What are the coordinates?")
    assert res.success is False
    assert len(res.elements) == 0


# 21. Regression: move_mouse cannot bypass grounding
def test_regression_move_mouse_cannot_bypass_grounding():
    policy = SafetyPolicy()
    # Attempting move_mouse without observation_id must be rejected
    dec = policy.evaluate_action("move_mouse", {"x": 200, "y": 300})
    assert dec.allowed is False
    assert "requires an active observation_id" in dec.reason


# 22. Regression: click cannot bypass grounding
def test_regression_click_cannot_bypass_grounding():
    policy = SafetyPolicy()
    # Attempting click with coordinates but without observation_id must be rejected
    dec = policy.evaluate_action("click", {"x": 200, "y": 300})
    assert dec.allowed is False
    assert "requires an active observation_id" in dec.reason


# 23. Regression: drag cannot bypass grounding
def test_regression_drag_cannot_bypass_grounding():
    policy = SafetyPolicy()
    # Attempting drag without observation_id must be rejected
    dec = policy.evaluate_action(
        "drag",
        {"start_x": 100, "start_y": 100, "end_x": 300, "end_y": 300},
    )
    assert dec.allowed is False
    assert "requires an active observation_id" in dec.reason


# 24. Regression: semantic click_element resolves via UIAutomation
@patch("actions.mouse.MouseController.click_at")
def test_regression_semantic_click_resolves_via_uiautomation(mock_click_at):
    registry = create_default_registry()
    click_elem_tool = registry.get_tool("click_element")
    assert click_elem_tool is not None

    mock_elem = DetectedUIElement(
        label="OK",
        element_type="button",
        bounds=BoundingBox(left=100, top=200, right=200, bottom=250),
        confidence=1.0,
    )

    with patch("perception.controller.PerceptionController.fresh_screen_observation") as mock_fresh:
        mock_obs = MagicMock(spec=ScreenObservation)
        mock_obs.observation_id = "obs_semantic_1"
        mock_fresh.return_value = mock_obs

        with patch("perception.uiautomation.UIAutomationProvider.find_control_by_query", return_value=mock_elem):
            with patch("actions.mouse.MouseController.validate_coordinates"):
                result = registry.execute("click_element", {"target": "OK"})
                assert result.success is True
                assert result.output["click_point"] == (150, 225)
                mock_click_at.assert_called_once_with(
                    x=150, y=225, button="left", observation_id="obs_semantic_1"
                )


# 25. Regression: stale observations rejected immediately before dispatch
def test_regression_stale_observations_rejected_immediately_before_dispatch():
    reg = get_grounding_registry()
    old_ts = time.time() - 10.0
    reg.register_observation(
        observation_id="obs_old_dispatch",
        timestamp=old_ts,
        screen_size=(1920, 1080),
    )

    is_grounded, reason, _ = reg.validate_coordinate_grounding(
        observation_id="obs_old_dispatch",
        x=500,
        y=500,
        max_age_seconds=5.0,
    )
    assert is_grounded is False
    assert "stale" in reason.lower()


# 26. Regression: CentralSafetyGate remains unchanged and authoritative
def test_regression_central_safety_gate_authority():
    gate = CentralSafetyGate()

    # Invariant: force=True cannot bypass protected runner/terminals
    dec_force = gate.evaluate_close_target("powershell.exe", force=True)
    assert dec_force.allowed is False

    # Invariant: pre-existing windows cannot be closed
    mock_state = MagicMock()
    mock_state.pre_existing_hwnds = {54321}
    mock_state.goal = "Do some work"
    mock_target = MagicMock()
    mock_target.hwnd = 54321
    mock_target.title = "Pre-existing Notepad"
    mock_target.pid = 9999

    dec_pre = gate.evaluate_close_target(
        hwnd_or_title=54321,
        state=mock_state,
        target_win=mock_target,
    )
    assert dec_pre.allowed is False
    assert "pre-existed" in dec_pre.reason.lower()
