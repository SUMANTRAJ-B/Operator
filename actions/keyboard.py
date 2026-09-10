"""Keyboard interaction controller with key mapping, text entry, and hotkeys."""

import time
from typing import Dict, List, Optional
import pyautogui

from app.config import get_settings
from app.logging import get_logger

logger = get_logger("actions.keyboard")

# Key name aliases for consistency and Windows compatibility
KEY_ALIASES: Dict[str, str] = {
    "win": "winleft",
    "windows": "winleft",
    "cmd": "winleft",
    "super": "winleft",
    "ctrl": "ctrlleft",
    "control": "ctrlleft",
    "alt": "altleft",
    "shift": "shiftleft",
    "esc": "escape",
    "return": "enter",
    "del": "delete",
    "ins": "insert",
    "pageup": "pgup",
    "pagedown": "pgdn",
    "arrowup": "up",
    "arrowdown": "down",
    "arrowleft": "left",
    "arrowright": "right",
}


class KeyboardController:
    """Controls text entry, special key presses, and modifier hotkeys."""

    def __init__(self):
        settings = get_settings()
        pyautogui.FAILSAFE = False
        self.default_delay = settings.action_delay_seconds

    def normalize_key(self, key: str) -> str:
        """Map key name aliases to standard PyAutoGUI keys."""
        cleaned = key.strip().lower()
        return KEY_ALIASES.get(cleaned, cleaned)

    def type_text(self, text: str, interval: float = 0.02) -> int:
        """Type a string of text with optional character interval delay.

        Args:
            text: The string to type.
            interval: Delay in seconds between each keystroke.

        Returns:
            Number of characters typed.
        """
        logger.info(f"Typing text (length={len(text)}): {text!r}")
        pyautogui.write(text, interval=interval)
        self._delay()
        return len(text)

    def press_key(self, key: str, presses: int = 1, interval: float = 0.05) -> str:
        """Press a specific key (e.g. 'enter', 'tab', 'escape', 'f5').

        Args:
            key: Name of the key to press.
            presses: Number of times to press.
            interval: Delay between presses.

        Returns:
            Normalized key name that was pressed.
        """
        normalized = self.normalize_key(key)
        logger.info(f"Pressing key {normalized!r} [presses={presses}]")
        pyautogui.press(normalized, presses=presses, interval=interval)
        self._delay()
        return normalized

    def hotkey(self, *keys: str) -> List[str]:
        """Press a combination of keys simultaneously (e.g. 'ctrl', 'c' or 'win', 'r').

        Args:
            *keys: Sequence of keys to press down and release in reverse order.

        Returns:
            List of normalized keys pressed.
        """
        if not keys:
            raise ValueError("Hotkey combination requires at least one key")

        normalized_keys = [self.normalize_key(k) for k in keys]

        # Safeguard: prevent accidental Alt+F4 on IDE or host windows
        if any(k in ("alt", "altleft", "altright") for k in normalized_keys) and "f4" in normalized_keys:
            try:
                from actions.applications import get_app_controller
                app_ctrl = get_app_controller()
                active = app_ctrl.get_active_window()
                if active is None or app_ctrl.is_protected_target(hwnd=active.hwnd, pid=active.pid, title=active.title):
                    target_name = active.title if active else "unknown/unfocused"
                    logger.warning(
                        f"Safety guard: Blocked Alt+F4 hotkey. Active window is protected or unverified: {target_name!r}"
                    )
                    return []
            except Exception as e:
                logger.debug(f"Safety check error: {e}")

        logger.info(f"Triggering hotkey: {' + '.join(normalized_keys)}")
        pyautogui.hotkey(*normalized_keys)
        self._delay()
        return normalized_keys

    def key_down(self, key: str) -> str:
        """Hold down a key without releasing."""
        normalized = self.normalize_key(key)
        logger.debug(f"Holding key down: {normalized!r}")
        pyautogui.keyDown(normalized)
        return normalized

    def key_up(self, key: str) -> str:
        """Release a held key."""
        normalized = self.normalize_key(key)
        logger.debug(f"Releasing key: {normalized!r}")
        pyautogui.keyUp(normalized)
        return normalized

    def _delay(self) -> None:
        if self.default_delay > 0:
            time.sleep(self.default_delay)


_keyboard_instance: Optional[KeyboardController] = None


def get_keyboard_controller() -> KeyboardController:
    """Retrieve shared KeyboardController instance."""
    global _keyboard_instance
    if _keyboard_instance is None:
        _keyboard_instance = KeyboardController()
    return _keyboard_instance
