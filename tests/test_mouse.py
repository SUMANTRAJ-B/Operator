"""Unit tests for mouse controller."""

from unittest.mock import MagicMock, patch
import pytest
from actions.mouse import MouseController, get_mouse_controller


def test_mouse_controller_singleton():
    m1 = get_mouse_controller()
    m2 = get_mouse_controller()
    assert m1 is m2


def test_get_screen_size():
    controller = get_mouse_controller()
    width, height = controller.get_screen_size()
    assert width > 0
    assert height > 0


def test_get_position():
    controller = get_mouse_controller()
    x, y = controller.get_position()
    assert isinstance(x, int)
    assert isinstance(y, int)


def test_validate_coordinates():
    controller = get_mouse_controller()
    w, h = controller.get_screen_size()

    # Valid coordinates
    controller.validate_coordinates(0, 0)
    controller.validate_coordinates(w - 1, h - 1)
    controller.validate_coordinates(w // 2, h // 2)

    # Invalid coordinates
    with pytest.raises(ValueError):
        controller.validate_coordinates(-1, 50)
    with pytest.raises(ValueError):
        controller.validate_coordinates(50, -1)
    with pytest.raises(ValueError):
        controller.validate_coordinates(w + 100, 50)
    with pytest.raises(ValueError):
        controller.validate_coordinates(50, h + 100)


@patch("pyautogui.moveTo")
def test_move_to_mocked(mock_move):
    controller = MouseController()
    w, h = controller.get_screen_size()
    target_x, target_y = w // 2, h // 2

    controller.move_to(target_x, target_y, duration=0.1)
    mock_move.assert_called_once_with(target_x, target_y, duration=0.1)


@patch("pyautogui.click")
def test_click_mocked(mock_click):
    controller = MouseController()
    controller.click(x=100, y=100, button="left", clicks=1)
    mock_click.assert_called_once_with(x=100, y=100, clicks=1, interval=0.1, button="left")


@patch("pyautogui.scroll")
def test_scroll_mocked(mock_scroll):
    controller = MouseController()
    controller.scroll(clicks=5, x=100, y=100)
    mock_scroll.assert_called_once_with(5, x=100, y=100)
