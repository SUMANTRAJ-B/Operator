"""Screen capture module supporting full screen, monitor, and region snapshots."""

import ctypes
import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import mss
from PIL import Image, ImageGrab

from app.config import get_settings
from app.logging import get_logger

logger = get_logger("perception.screenshot")


def _ensure_desktop_access() -> None:
    """Ensure the current thread is attached to the active user desktop.

    In Windows, background processes, services, or subshells may lack desktop
    access unless explicitly attached via OpenInputDesktop and SetThreadDesktop.
    """
    try:
        u32 = ctypes.windll.user32
        h_desk = u32.OpenInputDesktop(0, False, 0x01FF)
        if h_desk:
            u32.SetThreadDesktop(h_desk)
    except Exception as err:
        logger.debug(f"Desktop attachment notice (non-fatal): {err}")


def _get_mss_instance() -> mss.base.MSSBase:
    """Instantiate MSS, handling both modern MSS class and legacy mss() factory."""
    cls = getattr(mss, "MSS", None)
    if cls is not None:
        return cls()
    return mss.mss()


class ScreenCapture:
    """High-performance screen perception using MSS with Pillow fallback."""

    def __init__(self, output_dir: Optional[Union[str, Path]] = None):
        settings = get_settings()
        self.output_dir = Path(output_dir or settings.screenshot_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def get_screen_size(self) -> Tuple[int, int]:
        """Return (width, height) of the primary monitor in pixels."""
        _ensure_desktop_access()
        try:
            with _get_mss_instance() as sct:
                primary = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                return primary["width"], primary["height"]
        except Exception:
            # Fallback to win32 / system metrics
            u32 = ctypes.windll.user32
            return u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)

    def get_all_monitors(self) -> List[Dict[str, Any]]:
        """Return information about all detected monitors."""
        _ensure_desktop_access()
        try:
            with _get_mss_instance() as sct:
                return list(sct.monitors)
        except Exception:
            w, h = self.get_screen_size()
            return [{"left": 0, "top": 0, "width": w, "height": h}]

    def capture_full_screen(
        self,
        save_path: Optional[Union[str, Path]] = None,
        monitor_index: int = 1,
    ) -> Image.Image:
        """Capture the full screen of the specified monitor.

        Args:
            save_path: Optional file path to save the captured image.
            monitor_index: Monitor index (1 = primary, 0 = all monitors combined).

        Returns:
            PIL Image in RGB format.
        """
        _ensure_desktop_access()

        image: Optional[Image.Image] = None
        try:
            with _get_mss_instance() as sct:
                if monitor_index >= len(sct.monitors):
                    monitor_index = 1 if len(sct.monitors) > 1 else 0

                monitor = sct.monitors[monitor_index]
                sct_img = sct.grab(monitor)
                image = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        except Exception as mss_err:
            logger.warning(f"MSS capture encountered error ({mss_err}), using ImageGrab fallback")
            image = ImageGrab.grab(all_screens=(monitor_index == 0))

        if image is None:
            raise RuntimeError("Failed to capture screen image via MSS and ImageGrab")

        if save_path:
            resolved_path = self._resolve_path(save_path)
            image.save(resolved_path)
            logger.debug(f"Saved full-screen capture to {resolved_path}")

        return image

    def capture_region(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        save_path: Optional[Union[str, Path]] = None,
    ) -> Image.Image:
        """Capture a defined rectangular region of the screen.

        Args:
            x: Top-left X coordinate.
            y: Top-left Y coordinate.
            width: Region width in pixels.
            height: Region height in pixels.
            save_path: Optional file path to save the captured image.

        Returns:
            PIL Image in RGB format.
        """
        if width <= 0 or height <= 0:
            raise ValueError(f"Invalid region dimensions: width={width}, height={height}")

        _ensure_desktop_access()

        image: Optional[Image.Image] = None
        try:
            region = {"top": y, "left": x, "width": width, "height": height}
            with _get_mss_instance() as sct:
                sct_img = sct.grab(region)
                image = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        except Exception as mss_err:
            logger.warning(f"MSS region capture encountered error ({mss_err}), using ImageGrab fallback")
            bbox = (x, y, x + width, y + height)
            image = ImageGrab.grab(bbox=bbox)

        if image is None:
            raise RuntimeError(f"Failed to capture region ({x}, {y}, {width}, {height})")

        if save_path:
            resolved_path = self._resolve_path(save_path)
            image.save(resolved_path)
            logger.debug(f"Saved region capture to {resolved_path}")

        return image

    def generate_screenshot_filename(self, prefix: str = "shot") -> Path:
        """Generate a timestamped file path in the configured screenshot directory."""
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:19]
        return self.output_dir / f"{prefix}_{timestamp}.png"

    def _resolve_path(self, path: Union[str, Path]) -> Path:
        target = Path(path)
        if target.is_absolute():
            resolved = target
        else:
            # Avoid duplicate nesting if path is already relative to or starts with output_dir
            try:
                if target.is_relative_to(self.output_dir):
                    resolved = target.resolve()
                else:
                    resolved = (self.output_dir / target).resolve()
            except AttributeError:
                resolved = (self.output_dir / target).resolve()

        resolved.parent.mkdir(parents=True, exist_ok=True)
        return resolved


_capture_instance: Optional[ScreenCapture] = None


def get_screen_capture() -> ScreenCapture:
    """Retrieve shared ScreenCapture instance."""
    global _capture_instance
    if _capture_instance is None:
        _capture_instance = ScreenCapture()
    return _capture_instance
