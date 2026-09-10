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

    def move_to(
        self,
        x: int,
        y: int,
        duration: float = 0.0,
        observation_id: Optional[str] = None,
    ) -> Tuple[int, int]:
        """Move the mouse cursor to the given coordinates."""
        self.validate_coordinates(x, y)
        logger.debug(f"Moving mouse to ({x}, {y}) [duration={duration}s, obs={observation_id}]")
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
        observation_id: Optional[str] = None,
    ) -> Tuple[int, int]:
        """Click at the specified coordinates or current position."""
        if x is not None and y is not None:
            self.validate_coordinates(x, y)
            logger.info(f"Clicking {button} at ({x}, {y}) [clicks={clicks}, obs={observation_id}]")
            pyautogui.click(x=x, y=y, clicks=clicks, interval=interval, button=button)
        else:
            cur_x, cur_y = self.get_position()
            logger.info(f"Clicking {button} at current position ({cur_x}, {cur_y})")
            pyautogui.click(clicks=clicks, interval=interval, button=button)

        self._delay()
        return self.get_position()

    def click_at(
        self,
        x: int,
        y: int,
        button: str = "left",
        clicks: int = 1,
        interval: float = 0.1,
        observation_id: Optional[str] = None,
    ) -> Tuple[int, int]:
        """Click at specific coordinates grounded in an observation."""
        return self.click(
            x=x,
            y=y,
            button=button,
            clicks=clicks,
            interval=interval,
            observation_id=observation_id,
        )

    def double_click(
        self,
        x: Optional[int] = None,
        y: Optional[int] = None,
        observation_id: Optional[str] = None,
    ) -> Tuple[int, int]:
        """Double-click at specified coordinates or current position."""
        return self.click(x=x, y=y, button="left", clicks=2, interval=0.1, observation_id=observation_id)

    def double_click_at(
        self,
        x: int,
        y: int,
        observation_id: Optional[str] = None,
    ) -> Tuple[int, int]:
        """Double-click at specific coordinates grounded in an observation."""
        return self.double_click(x=x, y=y, observation_id=observation_id)

    def right_click(
        self,
        x: Optional[int] = None,
        y: Optional[int] = None,
        observation_id: Optional[str] = None,
    ) -> Tuple[int, int]:
        """Right-click at specified coordinates or current position."""
        return self.click(x=x, y=y, button="right", clicks=1, observation_id=observation_id)

    def right_click_at(
        self,
        x: int,
        y: int,
        observation_id: Optional[str] = None,
    ) -> Tuple[int, int]:
        """Right-click at specific coordinates grounded in an observation."""
        return self.right_click(x=x, y=y, observation_id=observation_id)

    def drag_to(
        self,
        x: int,
        y: int,
        duration: float = 0.3,
        button: str = "left",
        observation_id: Optional[str] = None,
    ) -> Tuple[int, int]:
        """Drag mouse from current position to target coordinates."""
        self.validate_coordinates(x, y)
        logger.info(f"Dragging to ({x}, {y}) [button={button}, duration={duration}s, obs={observation_id}]")
        pyautogui.dragTo(x, y, duration=duration, button=button)
        self._delay()
        return self.get_position()

    def drag(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration: float = 0.3,
        button: str = "left",
        observation_id: Optional[str] = None,
    ) -> Tuple[int, int]:
        """Move to start coordinates, press mouse, drag to end coordinates, and release."""
        self.validate_coordinates(start_x, start_y)
        self.validate_coordinates(end_x, end_y)
        logger.info(
            f"Dragging from ({start_x}, {start_y}) to ({end_x}, {end_y}) [duration={duration}s, obs={observation_id}]"
        )
        self.move_to(start_x, start_y, duration=0.0, observation_id=observation_id)
        return self.drag_to(end_x, end_y, duration=duration, button=button, observation_id=observation_id)

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
