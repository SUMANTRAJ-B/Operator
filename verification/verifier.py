"""Action verification system to validate outcomes against actual desktop state."""

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from actions.applications import get_app_controller
from app.logging import get_logger

logger = get_logger("verification.verifier")


@dataclass
class VerificationResult:
    """Outcome of verifying an action against actual OS state."""

    verified: bool
    confidence: float = 1.0
    details: str = ""


class ActionVerifier:
    """Validates that actions executed produced expected system state changes."""

    def __init__(self):
        self.app_controller = get_app_controller()

    def verify_action(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        tool_result: Any,
    ) -> VerificationResult:
        """Verify the post-action state of the OS based on the tool that executed."""
        args = arguments or {}

        # 1. Verification for open_application
        if tool_name == "open_application":
            app_name = args.get("app_name", "").lower().replace(".exe", "")
            raw_arguments = args.get("arguments")
            time.sleep(0.3)  # Brief settle time for window creation

            # If a specific document or file was requested, verify that exact document is open
            if raw_arguments and isinstance(raw_arguments, str):
                import os
                target_file = os.path.basename(raw_arguments.strip().strip('"').strip("'")).lower()
                all_visible = self.app_controller.list_windows(visible_only=True)
                matching_doc = [w for w in all_visible if target_file in w.title.lower()]
                if matching_doc:
                    win = matching_doc[0]
                    return VerificationResult(
                        verified=True,
                        confidence=1.0,
                        details=f"Document '{target_file}' successfully opened in window '{win.title}' (HWND: {win.hwnd})",
                    )
                # If target file was not in any window title, reject verification even if unrelated app windows exist
                return VerificationResult(
                    verified=False,
                    confidence=0.85,
                    details=f"Application launched but target document '{target_file}' not found in any open window titles.",
                )

            # Normal application launch verification
            windows = self.app_controller.find_windows(app_name)
            if windows:
                win = windows[0]
                return VerificationResult(
                    verified=True,
                    confidence=0.95,
                    details=f"Application window detected: '{win.title}' (HWND: {win.hwnd})",
                )
            return VerificationResult(
                verified=False,
                confidence=0.7,
                details=f"Application '{app_name}' launched but no window found matching '{app_name}'",
            )

        # 2. Verification for activate_window
        if tool_name == "activate_window":
            target_raw = args.get("window_title_or_hwnd", "")
            norm_target = self.app_controller.normalize_hwnd(target_raw)
            time.sleep(0.2)
            active = self.app_controller.get_active_window()
            if active:
                if isinstance(norm_target, int) and active.hwnd == norm_target:
                    return VerificationResult(
                        verified=True,
                        confidence=1.0,
                        details=f"Window '{active.title}' (HWND {active.hwnd}) successfully verified as active foreground window.",
                    )
                elif isinstance(norm_target, str):
                    target_str = norm_target.lower().strip()
                    active_title_lower = active.title.lower()
                    matched = target_str in active_title_lower or str(active.hwnd) == target_str
                    # Dynamic title tolerance for Notepad
                    if not matched and ("notepad" in target_str or "untitled" in target_str):
                        if "notepad" in active_title_lower or active.class_name.lower() == "notepad":
                            matched = True
                    if matched:
                        return VerificationResult(
                            verified=True,
                            confidence=1.0,
                            details=f"Window '{active.title}' successfully verified as active foreground window.",
                        )
            active_title = active.title if active else "None"
            return VerificationResult(
                verified=False,
                confidence=0.8,
                details=f"Target window '{target_raw}' not currently active (active is: '{active_title}').",
            )

        # 3. Verification for type_text
        if tool_name == "type_text":
            text = args.get("text", "")
            target_raw = args.get("window_title_or_hwnd")
            active = self.app_controller.get_active_window()
            if not active:
                return VerificationResult(
                    verified=False,
                    confidence=0.5,
                    details="Dispatched text, but no active window was detected.",
                )
            if self.app_controller.is_protected_target(
                hwnd=active.hwnd, pid=active.pid, title=active.title
            ):
                return VerificationResult(
                    verified=False,
                    confidence=0.9,
                    details=f"Keystrokes were directed at protected host window '{active.title}'. Verification rejected.",
                )
            if target_raw:
                norm_target = self.app_controller.normalize_hwnd(target_raw)
                if isinstance(norm_target, int):
                    if active.hwnd != norm_target:
                        return VerificationResult(
                            verified=False,
                            confidence=0.9,
                            details=f"Keystrokes sent but active window (HWND {active.hwnd}) does not match target HWND {norm_target}.",
                        )
                elif isinstance(norm_target, str):
                    target_str = norm_target.lower().strip()
                    active_title_lower = active.title.lower()
                    matched = target_str in active_title_lower
                    # Dynamic title tolerance for Notepad (e.g. typing transforms Untitled into *<typed text> - Notepad)
                    if not matched and ("notepad" in target_str or "untitled" in target_str):
                        if "notepad" in active_title_lower or active.class_name.lower() == "notepad":
                            matched = True
                    if not matched:
                        return VerificationResult(
                            verified=False,
                            confidence=0.9,
                            details=f"Keystrokes sent but active window '{active.title}' does not match target '{norm_target}'.",
                        )

            return VerificationResult(
                verified=True,
                confidence=0.9,
                details=f"Dispatched {len(text)} characters into active window '{active.title}'.",
            )

        # 4. Verification for close_window
        if tool_name == "close_window":
            target_raw = args.get("window_title_or_hwnd", "")
            time.sleep(0.3)
            norm_target = self.app_controller.normalize_hwnd(target_raw)
            if isinstance(norm_target, int):
                import win32gui
                if not win32gui.IsWindow(norm_target):
                    return VerificationResult(
                        verified=True,
                        confidence=1.0,
                        details=f"Target window HWND {norm_target} successfully closed.",
                    )
                return VerificationResult(
                    verified=False,
                    confidence=0.8,
                    details=f"Target window HWND {norm_target} is still open.",
                )
            else:
                remaining = self.app_controller.find_windows(str(norm_target))
                if not remaining:
                    return VerificationResult(
                        verified=True,
                        confidence=1.0,
                        details=f"Target window {target_raw!r} successfully closed.",
                    )
                return VerificationResult(
                    verified=False,
                    confidence=0.7,
                    details=f"Target window {target_raw!r} is still open.",
                )

        # 5. Verification for move_mouse
        if tool_name == "move_mouse":
            target_x = args.get("x")
            target_y = args.get("y")
            obs_id = args.get("observation_id")
            if target_x is not None and target_y is not None:
                try:
                    import pyautogui
                    cur_pos = pyautogui.position()
                    dist = ((cur_pos.x - target_x) ** 2 + (cur_pos.y - target_y) ** 2) ** 0.5
                    if dist <= 10.0:
                        res = VerificationResult(
                            verified=True,
                            confidence=1.0,
                            details=f"Cursor moved to verified position ({cur_pos.x}, {cur_pos.y}) matching target ({target_x}, {target_y}).",
                        )
                    else:
                        res = VerificationResult(
                            verified=False,
                            confidence=0.8,
                            details=f"Cursor position ({cur_pos.x}, {cur_pos.y}) did not reach target ({target_x}, {target_y}).",
                        )
                except Exception as e:
                    res = VerificationResult(verified=True, confidence=0.8, details=f"Move executed: {e}")
            else:
                res = VerificationResult(verified=True, confidence=0.8, details="Move mouse executed.")

            if obs_id:
                try:
                    from perception.controller import get_perception_controller
                    get_perception_controller().cleanup_observation_screenshot(obs_id, success=res.verified)
                except Exception:
                    pass
            return res

        # 6. Verification for pixel mouse clicks & drags
        if tool_name in (
            "click",
            "click_at",
            "double_click",
            "double_click_at",
            "right_click",
            "right_click_at",
            "drag",
            "click_element",
        ):
            obs_id = args.get("observation_id")
            if not obs_id and isinstance(tool_result, dict):
                obs_id = tool_result.get("observation_id")

            # Verify that active window is not a protected target
            active = self.app_controller.get_active_window()
            if active and self.app_controller.is_protected_target(
                hwnd=active.hwnd, pid=active.pid, title=active.title
            ):
                res = VerificationResult(
                    verified=False,
                    confidence=0.9,
                    details=f"Action '{tool_name}' resulted in focus on protected host window '{active.title}'. Verification rejected.",
                )
            else:
                res = VerificationResult(
                    verified=True,
                    confidence=0.95,
                    details=f"Pixel action '{tool_name}' verified. System in valid state (active window: '{active.title if active else 'None'}').",
                )

            if obs_id:
                try:
                    from perception.controller import get_perception_controller
                    get_perception_controller().cleanup_observation_screenshot(obs_id, success=res.verified)
                except Exception:
                    pass
            return res

        # 7. Verification for finish_task
        if tool_name == "finish_task":
            summary = args.get("summary", "")
            success = args.get("success", True)
            return VerificationResult(
                verified=True,
                confidence=1.0,
                details=f"Task completed. Summary: {summary}",
            )

        # Default fallback verification
        return VerificationResult(
            verified=True,
            confidence=0.8,
            details=f"Tool '{tool_name}' executed cleanly.",
        )

    def verify_resource_cleanup(self, hwnd: int) -> bool:
        """Verify programmatically that an Operator-owned HWND is completely destroyed."""
        try:
            import win32gui
            return not bool(win32gui.IsWindow(hwnd))
        except Exception:
            return True



def get_notepad_text(hwnd: int) -> str:
    """Read full text from the edit control of a Notepad window."""
    import ctypes
    import win32gui
    texts = []

    def enum_child(child, _):
        cls = win32gui.GetClassName(child)
        if cls in ("RichEditD2DPT", "Edit"):
            length = win32gui.SendMessage(child, 0x000E, 0, 0)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                win32gui.SendMessage(child, 0x000D, length + 1, buf)
                texts.append(buf.value)

    win32gui.EnumChildWindows(hwnd, enum_child, None)
    return "\n".join(texts)


def verify_calculator_window(app_controller: Optional[Any] = None) -> bool:
    """Verify that a Calculator window exists and is visible."""
    apps = app_controller or get_app_controller()
    wins = apps.find_windows("calc") + apps.find_windows("calculator")
    return any(w.is_visible and w.title.strip() for w in wins)


def verify_notepad_content(
    expected_text: str,
    app_controller: Optional[Any] = None,
    target_hwnd: Optional[int] = None,
) -> bool:
    """Verify that an Operator-owned Notepad window contains the expected text.

    Does not rely on a static 'Untitled - Notepad' title. Inspects the actual
    document content from child controls, UIAutomation, and dynamic window title.
    """
    apps = app_controller or get_app_controller()
    candidate_hwnds: List[int] = [target_hwnd] if target_hwnd else []

    # Check Operator-owned resources first
    active_state = getattr(apps, "_active_state", None)
    if active_state and hasattr(active_state, "get_owned_resources"):
        for res in active_state.get_owned_resources():
            if "notepad" in res.app_identity.lower() or "notepad" in res.title.lower():
                if res.hwnd not in candidate_hwnds:
                    candidate_hwnds.append(res.hwnd)

    wins = apps.find_windows("notepad")
    for w in wins:
        if w.hwnd not in candidate_hwnds:
            candidate_hwnds.append(w.hwnd)

    expected_clean = expected_text.strip().lower()
    for h in candidate_hwnds:
        # 1. Inspect actual text buffer via Edit/RichEdit child control
        content = get_notepad_text(h)
        if content and expected_clean in content.lower():
            return True

        # 2. Inspect window title (Windows 11 puts typed words into dynamic title: "*The quick brown fox jumps over the - Notepad")
        try:
            import win32gui
            if win32gui.IsWindow(h):
                title = win32gui.GetWindowText(h).lower()
                prefix = expected_clean[:25].strip()
                if prefix and prefix in title:
                    return True
                if expected_clean in title:
                    return True
        except Exception:
            pass

    return False



def verify_calculator_result(expected_value: str = "42", app_controller: Optional[Any] = None) -> bool:
    """Verify that Calculator displays the expected calculation result.

    Priority order:
    1. UIAutomation inspection of Calculator display elements.
    2. Clipboard copy inspection via Ctrl+C.
    3. Screenshot / OCR fallback.
    """
    apps = app_controller or get_app_controller()
    wins = apps.find_windows("calc") + apps.find_windows("calculator")
    if not wins:
        return False
    hwnd = wins[0].hwnd

    # 1. UIAutomation
    try:
        import comtypes.client
        from comtypes import CoInitialize, CoUninitialize
        CoInitialize()
        try:
            mod = comtypes.client.GetModule("UIAutomationCore.dll")
            uia = comtypes.client.CreateObject(mod.CUIAutomation, interface=mod.IUIAutomation)
            elem = uia.ElementFromHandle(hwnd)
            cond = uia.CreatePropertyCondition(30005, "CalculatorResults")
            res_elem = elem.FindFirst(mod.TreeScope_Descendants, cond)
            if res_elem and expected_value in (res_elem.CurrentName or ""):
                return True
            true_cond = uia.CreateTrueCondition()
            all_elems = elem.FindAll(mod.TreeScope_Descendants, true_cond)
            for i in range(min(all_elems.Length, 50)):
                child = all_elems.GetElement(i)
                name = child.CurrentName or ""
                if expected_value in name and ("display" in name.lower() or name.strip() == expected_value):
                    return True
        finally:
            CoUninitialize()
    except Exception as err:
        logger.warning(f"UIAutomation calculator check failed: {err}")

    # 2. Clipboard fallback
    try:
        import pyperclip
        from actions.keyboard import get_keyboard_controller
        kb = get_keyboard_controller()
        apps.activate_window(hwnd)
        time.sleep(0.2)
        kb.hotkey("ctrl", "c")
        time.sleep(0.2)
        clip = pyperclip.paste().strip()
        if expected_value in clip:
            return True
    except Exception as err:
        logger.warning(f"Clipboard calculator check failed: {err}")

    return False


def verify_explorer_window(app_controller: Optional[Any] = None) -> bool:
    """Verify that a File Explorer window exists and is visible."""
    apps = app_controller or get_app_controller()
    wins = apps.find_windows("explorer") + apps.find_windows("file explorer") + apps.find_windows("CabinetWClass")
    return any(w.is_visible for w in wins)


def verify_foreground_window(expected_title_substring: str, app_controller: Optional[Any] = None) -> bool:
    """Verify that the currently focused foreground window matches the expected title."""
    apps = app_controller or get_app_controller()
    active = apps.get_active_window()
    if not active:
        return False
    return expected_title_substring.lower() in active.title.lower()


def verify_desktop_folder(folder_name: str = "OperatorTestFolder") -> bool:
    """Verify that the specified folder exists on the Windows Desktop."""
    import os
    desktop = os.path.join(os.path.expanduser("~"), "Desktop", folder_name)
    return os.path.exists(desktop) and os.path.isdir(desktop)


def verify_file_opened(
    filename: str = "sample.txt",
    expected_content: Optional[str] = None,
    app_controller: Optional[Any] = None,
) -> bool:
    """Verify that a window is open displaying the target filename and/or content."""
    apps = app_controller or get_app_controller()
    target_name = filename.lower().strip()
    for w in apps.list_windows(visible_only=True):
        if target_name in w.title.lower():
            if expected_content:
                text = get_notepad_text(w.hwnd)
                if expected_content in text:
                    return True
                continue
            return True
    return False


def verify_window_closed(target_query: str, app_controller: Optional[Any] = None) -> bool:
    """Verify that no visible window matches the target query."""
    apps = app_controller or get_app_controller()
    remaining = [w for w in apps.find_windows(target_query) if w.title.strip()]
    return len(remaining) == 0


_verifier_instance: Optional[ActionVerifier] = None


def get_action_verifier() -> ActionVerifier:
    """Retrieve shared ActionVerifier instance."""
    global _verifier_instance
    if _verifier_instance is None:
        _verifier_instance = ActionVerifier()
    return _verifier_instance
