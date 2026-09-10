"""Unit tests for safety guardrail policy."""

from unittest.mock import patch
from safety.policy import SafetyPolicy, get_safety_policy


def test_safety_policy_singleton():
    s1 = get_safety_policy()
    s2 = get_safety_policy()
    assert s1 is s2


@patch("pyautogui.size")
def test_safety_policy_blocks_out_of_bounds_mouse(mock_size):
    mock_size.return_value = (1920, 1080)
    policy = SafetyPolicy()

    # Valid coordinates
    valid_dec = policy.evaluate_action("move_mouse", {"x": 500, "y": 500})
    assert valid_dec.allowed is True

    # Out of bounds X
    invalid_x = policy.evaluate_action("move_mouse", {"x": 2500, "y": 500})
    assert invalid_x.allowed is False
    assert "out of screen bounds" in invalid_x.reason

    # Negative Y
    invalid_y = policy.evaluate_action("click", {"x": 500, "y": -10})
    assert invalid_y.allowed is False
    assert "out of screen bounds" in invalid_y.reason


def test_safety_policy_blocks_closing_protected_targets():
    policy = SafetyPolicy()

    # Should block closing Antigravity IDE
    dec = policy.evaluate_action("close_window", {"window_title_or_hwnd": "Antigravity IDE"})
    assert dec.allowed is False
    assert "protected" in dec.reason.lower()


@patch("actions.applications.AppController.get_active_window")
def test_safety_policy_blocks_alt_f4_when_active_none(mock_active):
    policy = SafetyPolicy()
    mock_active.return_value = None

    dec = policy.evaluate_action("hotkey", {"keys": ["alt", "f4"]})
    assert dec.allowed is False
    assert "Alt+F4 is blocked" in dec.reason
