"""Unit tests verifying the modular vision perception architecture and grounding invariants."""

import time
from unittest.mock import MagicMock, patch
import pytest

from perception.controller import PerceptionController, ScreenObservation
from perception.geometry import WindowGeometryProvider
from perception.uiautomation import UIAutomationProvider
from providers.vision.base import (
    BoundingBox,
    DetectedUIElement,
    OllamaVisionProvider,
    UnavailableVisionProvider,
    VisionAnalysisResult,
    get_vision_provider,
)


def test_currently_configured_ollama_model_has_no_vision():
    """Verify that the currently installed local Ollama models do not support vision."""
    provider = OllamaVisionProvider(model="qwen3:8b")
    # qwen3:8b is text-only; is_available must be False
    assert provider.is_available() is False


def test_unavailable_vision_provider_reports_unavailable():
    """Verify that UnavailableVisionProvider cleanly exposes unavailable state and does not fake vision."""
    provider = UnavailableVisionProvider(reason="No vision model installed.")
    assert provider.is_available() is False
    assert provider.get_model_name() == "none (unavailable)"

    result = provider.analyze_image("dummy.png", prompt="Find button")
    assert result.success is False
    assert "No vision model installed" in result.error

    elements = provider.detect_elements("dummy.png")
    assert elements == []


def test_get_vision_provider_defaults_to_unavailable_on_text_only_system():
    """Verify factory returns an UnavailableVisionProvider on the current machine without faking vision."""
    provider = get_vision_provider()
    assert isinstance(provider, UnavailableVisionProvider)
    assert provider.is_available() is False


def test_screen_observation_lifecycle_and_staleness():
    """Verify ScreenObservation generates unique IDs, timestamps, and detects staleness."""
    now = time.time()
    obs = ScreenObservation(
        observation_id="obs_test_123",
        timestamp=now,
        screen_size=(1920, 1080),
        vision_available=False,
    )
    assert obs.is_stale(max_age_seconds=10.0) is False
    # Stale observation check
    obs_old = ScreenObservation(
        observation_id="obs_old_456",
        timestamp=now - 20.0,
        screen_size=(1920, 1080),
        vision_available=False,
    )
    assert obs_old.is_stale(max_age_seconds=5.0) is True


def test_bounding_box_safe_click_point_derivation():
    """Verify safe click coordinates are strictly derived from bounding box center."""
    bbox = BoundingBox(left=860, top=600, right=950, bottom=635)
    center_x, center_y = bbox.center
    assert center_x == 905
    assert center_y == 617
    assert bbox.contains(center_x, center_y) is True
    assert bbox.contains(850, 600) is False


def test_perception_controller_falls_back_to_uiautomation_when_vision_unavailable():
    """Verify PerceptionController uses UIAutomation and does not attempt fake vision."""
    mock_uia = MagicMock(spec=UIAutomationProvider)
    test_btn = DetectedUIElement(
        label="Yes",
        element_type="button",
        bounds=BoundingBox(860, 600, 950, 635),
        confidence=1.0,
    )
    mock_uia.find_controls_in_window.return_value = [test_btn]

    unavailable_vision = UnavailableVisionProvider()
    controller = PerceptionController(uiautomation=mock_uia, vision=unavailable_vision)

    assert controller.is_vision_available is False
    elem, source = controller.find_element("Yes", window_hwnd=1234)

    assert elem is not None
    assert elem.label == "Yes"
    assert elem.click_point == (905, 617)
    assert source == "uiautomation"
