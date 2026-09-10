"""Unit tests for keyboard controller."""

from unittest.mock import patch
import pytest
from actions.keyboard import KeyboardController, get_keyboard_controller


def test_keyboard_controller_singleton():
    k1 = get_keyboard_controller()
    k2 = get_keyboard_controller()
    assert k1 is k2


def test_normalize_key():
    controller = get_keyboard_controller()
    assert controller.normalize_key("win") == "winleft"
    assert controller.normalize_key("windows") == "winleft"
    assert controller.normalize_key("ctrl") == "ctrlleft"
    assert controller.normalize_key("esc") == "escape"
    assert controller.normalize_key("return") == "enter"
    assert controller.normalize_key("tab") == "tab"


@patch("pyautogui.write")
def test_type_text_mocked(mock_write):
    controller = KeyboardController()
    length = controller.type_text("Hello World", interval=0.01)
    assert length == 11
    mock_write.assert_called_once_with("Hello World", interval=0.01)


@patch("pyautogui.press")
def test_press_key_mocked(mock_press):
    controller = KeyboardController()
    key = controller.press_key("enter", presses=2)
    assert key == "enter"
    mock_press.assert_called_once_with("enter", presses=2, interval=0.05)


@patch("pyautogui.hotkey")
def test_hotkey_mocked(mock_hotkey):
    controller = KeyboardController()
    keys = controller.hotkey("ctrl", "shift", "esc")
    assert keys == ["ctrlleft", "shiftleft", "escape"]
    mock_hotkey.assert_called_once_with("ctrlleft", "shiftleft", "escape")


def test_hotkey_empty_raises():
    controller = KeyboardController()
    with pytest.raises(ValueError):
        controller.hotkey()


@patch("actions.applications.AppController.get_active_window")
@patch("actions.applications.AppController.is_protected_target")
@patch("pyautogui.hotkey")
def test_hotkey_blocks_alt_f4_on_protected(mock_hotkey, mock_is_protected, mock_active):
    from actions.applications import WindowInfo
    controller = KeyboardController()
    mock_active.return_value = WindowInfo(hwnd=1, title="Antigravity", rect=(0, 0, 100, 100), is_visible=True, pid=1234)
    mock_is_protected.return_value = True

    keys = controller.hotkey("alt", "f4")
    assert keys == []
    mock_hotkey.assert_not_called()


@patch("actions.applications.AppController.get_active_window")
@patch("pyautogui.hotkey")
def test_hotkey_blocks_alt_f4_when_active_none(mock_hotkey, mock_active):
    controller = KeyboardController()
    mock_active.return_value = None

    keys = controller.hotkey("alt", "f4")
    assert keys == []
    mock_hotkey.assert_not_called()
