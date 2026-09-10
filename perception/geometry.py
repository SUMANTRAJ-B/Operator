"""Window and desktop geometry perception provider."""

from typing import Any, Dict, List, Optional, Tuple
import ctypes
import win32gui

from app.logging import get_logger
from perception.screenshot import get_screen_capture
from providers.vision.base import BoundingBox

logger = get_logger("perception.geometry")


class WindowGeometryProvider:
    """Provides desktop, monitor, and window geometry perception."""

    def __init__(self):
        self.screen_capture = get_screen_capture()

    def get_screen_size(self) -> Tuple[int, int]:
        """Return (width, height) of the primary desktop monitor in pixels."""
        return self.screen_capture.get_screen_size()

    def is_within_screen_bounds(self, x: int, y: int) -> bool:
        """Check whether (x, y) coordinates fall within primary screen boundaries."""
        w, h = self.get_screen_size()
        return 0 <= x < w and 0 <= y < h

    def get_window_bounds(self, hwnd: int) -> Optional[BoundingBox]:
        """Return BoundingBox for a window handle, or None if invalid/hidden."""
        if not hwnd or not win32gui.IsWindow(hwnd):
            return None
        try:
            rect = win32gui.GetWindowRect(hwnd)
            return BoundingBox(left=rect[0], top=rect[1], right=rect[2], bottom=rect[3])
        except Exception as e:
            logger.debug(f"Error getting window bounds for HWND {hwnd}: {e}")
            return None
