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

    def is_point_in_bounds(self, x: int, y: int, bbox: BoundingBox) -> bool:
        """Check whether (x, y) falls inside a BoundingBox."""
        return bbox.contains(x, y)

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

    def get_window_at_point(self, x: int, y: int) -> Optional[int]:
        """Return the window handle under the given screen coordinates."""
        try:
            hwnd = win32gui.WindowFromPoint((x, y))
            if hwnd and win32gui.IsWindow(hwnd):
                # Retrieve top-level root window (GA_ROOT = 2)
                try:
                    root = win32gui.GetAncestor(hwnd, 2)
                    return root if root else hwnd
                except Exception:
                    return hwnd
            return None
        except Exception as e:
            logger.debug(f"Error querying window at point ({x}, {y}): {e}")
            return None

    def is_point_in_protected_region(
        self,
        x: int,
        y: int,
        app_controller: Optional[Any] = None,
    ) -> Tuple[bool, str]:
        """Check whether (x, y) coordinates fall within a protected window or runner process region."""
        if not self.is_within_screen_bounds(x, y):
            w, h = self.get_screen_size()
            return True, f"Point ({x}, {y}) is out of screen bounds ({w}x{h})"

        hwnd = self.get_window_at_point(x, y)
        if not hwnd:
            return False, ""

        # Query AppController for protection status
        apps = app_controller
        if apps is None:
            try:
                from actions.applications import get_app_controller
                apps = get_app_controller()
            except Exception:
                apps = None

        if apps and hasattr(apps, "is_protected_target_with_reason"):
            try:
                title = win32gui.GetWindowText(hwnd)
                cls = win32gui.GetClassName(hwnd)
                is_prot, reason = apps.is_protected_target_with_reason(
                    hwnd=hwnd, title=title, class_name=cls
                )
                if is_prot:
                    return True, f"Target window at ({x}, {y}) '{title}' (HWND {hwnd}) is protected: {reason}"
            except Exception as e:
                logger.debug(f"Error checking protected region at ({x}, {y}): {e}")

        return False, ""
