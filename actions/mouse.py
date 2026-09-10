"""Mouse interaction controller with coordinate validation and safety controls."""

import time
from typing import Optional, Tuple
import pyautogui

from app.config import get_settings
from app.logging import get_logger
from perception.screenshot import get_screen_capture

logger = get_logger("actions.mouse")


class MouseController:
    """Controls mouse movement, clicking, dragging, and scrolling with safety checks."""

    def __init__(self):
        settings = get_settings()
        pyautogui.FAILSAFE = settings.fail_safe
        pyautogui.PAUSE = 0.0  # We handle deliberate pacing manually
        self.default_delay = settings.action_delay_seconds
        self.screen_capture = get_screen_capture()

    def get_screen_size(self) -> Tuple[int, int]:
        """Return the current screen dimensions."""
        return self.screen_capture.get_screen_size()

    def get_position(self) -> Tuple[int, int]:
        """Get current mouse cursor position (x, y)."""
        pos = pyautogui.position()
        return int(pos.x), int(pos.y)

    def validate_coordinates(self, x: int, y: int) -> None:
        """Ensure coordinates fall within the monitor bounds."""
        width, height = self.get_screen_size()
        if not (0 <= x < width and 0 <= y < height):
            raise ValueError(
                f"Coordinates ({x}, {y}) out of screen bounds: width={width}, height={height}"
            )

    def move_to(self, x: int, y: int, duration: float = 0.0) -> Tuple[int, int]:
        """Move the mouse cursor to the given coordinates.

        Args:
            x: Target X pixel coordinate.
            y: Target Y pixel coordinate.
            duration: Smooth movement duration in seconds.

        Returns:
            New cursor position.
        """
        self.validate_coordinates(x, y)
        logger.debug(f"Moving mouse to ({x}, {y}) [duration={duration}s]")
        pyautogui.moveTo(x, y, duration=duration)
        self._delay()
        return self.get_position()

    def click(
        self,
        x: Optional[int] = None,
        y: Optional[int] = None,
        button: str = "left",
        clicks: int = 1,
        interval: float = 0.1,
    ) -> Tuple[int, int]:
        """Click at the specified coordinates or current position.

        Args:
            x: Optional X coordinate.
            y: Optional Y coordinate.
            button: 'left', 'middle', or 'right'.
            clicks: Number of clicks.
            interval: Pause between clicks if multiple.

        Returns:
            Cursor position after click.
        """
        if x is not None and y is not None:
            self.validate_coordinates(x, y)
            logger.info(f"Clicking {button} at ({x}, {y}) [clicks={clicks}]")
            pyautogui.click(x=x, y=y, clicks=clicks, interval=interval, button=button)
        else:
            cur_x, cur_y = self.get_position()
            logger.info(f"Clicking {button} at current position ({cur_x}, {cur_y})")
            pyautogui.click(clicks=clicks, interval=interval, button=button)

        self._delay()
        return self.get_position()

    def double_click(
        self, x: Optional[int] = None, y: Optional[int] = None
    ) -> Tuple[int, int]:
        """Double-click at specified coordinates or current position."""
        return self.click(x=x, y=y, button="left", clicks=2, interval=0.1)

    def right_click(
        self, x: Optional[int] = None, y: Optional[int] = None
    ) -> Tuple[int, int]:
        """Right-click at specified coordinates or current position."""
        return self.click(x=x, y=y, button="right", clicks=1)

    def drag_to(
        self,
        x: int,
        y: int,
        duration: float = 0.3,
        button: str = "left",
    ) -> Tuple[int, int]:
        """Drag mouse from current position to target coordinates."""
        self.validate_coordinates(x, y)
        logger.info(f"Dragging to ({x}, {y}) [button={button}, duration={duration}s]")
        pyautogui.dragTo(x, y, duration=duration, button=button)
        self._delay()
        return self.get_position()

    def scroll(
        self,
        clicks: int,
        x: Optional[int] = None,
        y: Optional[int] = None,
    ) -> None:
        """Scroll the mouse wheel. Positive for up, negative for down."""
        if x is not None and y is not None:
            self.validate_coordinates(x, y)
            pyautogui.scroll(clicks, x=x, y=y)
        else:
            pyautogui.scroll(clicks)
        self._delay()

    def _delay(self) -> None:
        if self.default_delay > 0:
            time.sleep(self.default_delay)


_mouse_instance: Optional[MouseController] = None


def get_mouse_controller() -> MouseController:
    """Retrieve shared MouseController instance."""
    global _mouse_instance
    if _mouse_instance is None:
        _mouse_instance = MouseController()
    return _mouse_instance
