"""Application and window controller for Windows using pywin32 and subprocess."""

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import win32con
import win32gui
import win32process

from app.logging import get_logger

logger = get_logger("actions.applications")

# Known application name aliases for Windows
APP_ALIASES: Dict[str, str] = {
    "notepad": "notepad.exe",
    "calc": "calc.exe",
    "calculator": "calc.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "cmd": "cmd.exe",
    "powershell": "powershell.exe",
    "chrome": "chrome.exe",
    "edge": "msedge.exe",
    "vscode": "code.cmd",
    "code": "code.cmd",
}

# Protected keywords: windows and processes that must never be closed/killed by the agent
PROTECTED_WINDOW_KEYWORDS: List[str] = [
    "antigravity",
    "visual studio code",
    "vscode",
    "cursor",
    "windsurf",
    "pycharm",
    "ide",
    "powershell",
    "pwsh",
    "windows powershell",
    "windows terminal",
    "command prompt",
    "cmd.exe",
]

# Protected processes that must never be closed/killed
PROTECTED_PROCESS_NAMES: List[str] = [
    "antigravity",
    "code",
    "cursor",
    "windsurf",
    "pycharm",
    "language_server",
    "powershell",
    "pwsh",
    "cmd",
    "conhost",
    "openconsole",
    "windowsterminal",
    "wt",
    "bash",
    "wsl",
]



def ensure_desktop_access() -> bool:
    """Ensure the current thread is attached to the interactive user desktop."""
    try:
        import ctypes
        u32 = ctypes.windll.user32
        hdesk = u32.OpenInputDesktop(0, False, 0x01FF)
        if not hdesk:
            hdesk = u32.OpenDesktopW("default", 0, False, 0x01FF)
        if hdesk:
            return bool(u32.SetThreadDesktop(hdesk))
    except Exception:
        pass
    return False


@dataclass
class WindowInfo:
    """Represents state and geometry of a Windows application window."""

    hwnd: int
    title: str
    rect: Tuple[int, int, int, int]  # (left, top, right, bottom)
    is_visible: bool
    pid: int
    class_name: str = ""

    @property
    def width(self) -> int:
        return max(0, self.rect[2] - self.rect[0])

    @property
    def height(self) -> int:
        return max(0, self.rect[3] - self.rect[1])

    @property
    def center(self) -> Tuple[int, int]:
        return (self.rect[0] + self.width // 2, self.rect[1] + self.height // 2)


def resolve_file_argument(arg: Optional[str]) -> Optional[str]:
    """If argument represents a file path or filename, resolve it to an existing absolute path if possible."""
    if not arg or not isinstance(arg, str):
        return arg
    cleaned = arg.strip().strip('"').strip("'")
    if os.path.exists(cleaned):
        return os.path.abspath(cleaned)
    # Search common candidate locations: Desktop, Desktop/OperatorTestData, Desktop subdirs, cwd
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    candidates = [
        os.path.join(os.getcwd(), cleaned),
        os.path.join(desktop, cleaned),
        os.path.join(desktop, "OperatorTestData", cleaned),
    ]
    for cand in candidates:
        if os.path.exists(cand):
            return os.path.abspath(cand)
    # Also search subfolders of desktop if simple filename
    if not os.path.isabs(cleaned) and not ("/" in cleaned or "\\" in cleaned):
        try:
            for root, _, files in os.walk(desktop):
                if cleaned in files:
                    return os.path.abspath(os.path.join(root, cleaned))
        except Exception:
            pass
    return arg


class AppController:
    """Controls application launching, process tracking, and window management on Windows."""

    def __init__(self):
        ensure_desktop_access()
        self._active_state = None
        self._owned_hwnds: Set[int] = set()
        self._pre_existing_hwnds: Set[int] = set()
        self._safety_gate = None

    @property
    def safety_gate(self):
        """Retrieve CentralSafetyGate bound to this AppController instance."""
        if self._safety_gate is None:
            from safety.policy import CentralSafetyGate
            self._safety_gate = CentralSafetyGate(self)
        return self._safety_gate

    def set_active_state(self, state: Any) -> None:
        """Associate the current agent state with this AppController."""
        self._active_state = state

    def get_active_state(self) -> Optional[Any]:
        """Return the current agent state if one is active."""
        return self._active_state


    def register_owned_hwnd(self, hwnd: int) -> None:
        """Record an HWND as Operator-owned."""
        if hwnd:
            self._owned_hwnds.add(hwnd)

    def is_owned_hwnd(self, hwnd: int) -> bool:
        """Check if an HWND is registered as Operator-owned."""
        if hwnd in self._owned_hwnds:
            return True
        if self._active_state and hasattr(self._active_state, "is_resource_owned"):
            return self._active_state.is_resource_owned(hwnd)
        return False

    def get_owned_hwnds(self) -> Set[int]:
        """Return the set of Operator-owned HWNDs."""
        return set(self._owned_hwnds)

    def record_pre_existing_hwnds(self, hwnds: Set[int]) -> None:
        """Record set of HWNDs existing before current task."""
        self._pre_existing_hwnds = set(hwnds)

    def is_pre_existing_hwnd(self, hwnd: int) -> bool:
        """Check if an HWND is recorded as pre-existing."""
        if hwnd in self._pre_existing_hwnds:
            return True
        if self._active_state and hasattr(self._active_state, "pre_existing_hwnds"):
            return hwnd in self._active_state.pre_existing_hwnds
        return False

    def get_protected_pids(self) -> set:
        """Return the set of process IDs for the agent and its ancestor processes.

        Traversal terminates before reaching explorer.exe (the Windows desktop shell),
        so normal File Explorer windows are not treated as protected host processes.
        """
        pids = {os.getpid()}
        try:
            import psutil
            curr = psutil.Process(os.getpid())
            for parent in curr.parents():
                try:
                    if parent.name().lower() == "explorer.exe":
                        break
                except Exception:
                    pass
                pids.add(parent.pid)
        except Exception:
            if hasattr(os, "getppid"):
                pids.add(os.getppid())
        return pids

    def resolve_application_command(self, app_name_or_path: str) -> str:
        """Resolve common shorthand names into executable paths or commands."""
        cleaned = app_name_or_path.strip().lower()
        if cleaned in APP_ALIASES:
            return APP_ALIASES[cleaned]
        return app_name_or_path

    def open_application(
        self,
        app_name_or_path: str,
        arguments: Optional[str] = None,
        wait_for_window: bool = True,
        timeout: float = 8.0,
        activate: bool = True,
    ) -> subprocess.Popen:
        """Launch an application and optionally wait for its main window to appear.

        Args:
            app_name_or_path: Application executable, alias (e.g. 'notepad'), or path.
            arguments: Optional command-line arguments (e.g. file path to open).
            wait_for_window: Whether to wait until a matching window is detected.
            timeout: Maximum seconds to wait for window emergence.
            activate: Whether to bring the detected window to foreground immediately.

        Returns:
            The spawned subprocess.Popen object with detected_window metadata attached.
        """
        cmd = self.resolve_application_command(app_name_or_path)
        logger.info(
            f"Opening application: {cmd!r} (requested: {app_name_or_path!r}, arguments: {arguments!r})"
        )

        resolved_args = resolve_file_argument(arguments) if arguments else arguments
        cleaned_cmd = cmd.lower()
        launch_cmd = cmd

        # On Windows 11, modern Notepad is a packaged app requiring shell AUMID invocation
        if cleaned_cmd in ("notepad", "notepad.exe"):
            if resolved_args:
                launch_cmd = f'notepad.exe "{resolved_args}"' if " " in resolved_args and not resolved_args.startswith('"') else f"notepad.exe {resolved_args}"
            else:
                launch_cmd = "explorer.exe shell:AppsFolder\\Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"
        elif cleaned_cmd in ("calc", "calc.exe", "calculator"):
            launch_cmd = "calc.exe"
        elif cleaned_cmd in ("explorer", "explorer.exe", "file explorer"):
            if resolved_args:
                launch_cmd = f'explorer.exe "{resolved_args}"' if " " in resolved_args and not resolved_args.startswith('"') else f"explorer.exe {resolved_args}"
            else:
                launch_cmd = "explorer.exe shell:MyComputerFolder"
        elif resolved_args:
            launch_cmd = f'{cmd} "{resolved_args}"' if " " in resolved_args and not resolved_args.startswith('"') else f"{cmd} {resolved_args}"

        # Snapshot existing windows before launch to track newly created window
        pre_hwnds = {w.hwnd for w in self.list_windows(visible_only=True)}

        # Start process with shell=True if it contains shell arguments or spaces
        if "shell:" in launch_cmd or " " in launch_cmd:
            process = subprocess.Popen(
                launch_cmd,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            try:
                import shlex
                args = shlex.split(launch_cmd, posix=False) if isinstance(launch_cmd, str) else [launch_cmd]
                process = subprocess.Popen(
                    args,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                process = subprocess.Popen(
                    launch_cmd,
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

        detected_win: Optional[WindowInfo] = None

        if wait_for_window:
            start_time = time.time()
            query = app_name_or_path.lower().replace(".exe", "")
            is_explorer = cleaned_cmd in ("explorer", "explorer.exe", "file explorer")
            found = False

            target_filename = ""
            if arguments:
                cleaned_target = arguments.strip().strip('"').strip("'")
                target_filename = os.path.basename(cleaned_target).lower()

            # Initial observation loop
            first_phase_timeout = min(timeout, 2.0) if is_explorer else timeout
            while time.time() - start_time < first_phase_timeout:
                candidate_windows = self.find_windows(query)

                # If target filename given, prioritize window with that filename in title
                if target_filename:
                    matching_target = [w for w in candidate_windows if target_filename in w.title.lower()]
                    if matching_target:
                        detected_win = matching_target[0]
                        found = True
                        break

                # Otherwise prioritize newly created window (not in pre_hwnds)
                new_wins = [w for w in candidate_windows if w.hwnd not in pre_hwnds]
                if new_wins:
                    detected_win = new_wins[0]
                    found = True
                    break

                # Fallback: if pre_hwnds was empty, pick first candidate
                if candidate_windows and not pre_hwnds:
                    detected_win = candidate_windows[0]
                    found = True
                    break

                time.sleep(0.3)

            # Win+E GUI shortcut fallback for File Explorer when normal launch produces no visible window
            if is_explorer and not found:
                logger.info("File Explorer launch did not yield a visible window. Attempting Win+E GUI shortcut fallback.")
                try:
                    from actions.keyboard import get_keyboard_controller
                    kb = get_keyboard_controller()
                    kb.hotkey("win", "e")
                except Exception as err:
                    logger.warning(f"Win+E shortcut fallback encountered error: {err}")

                # Second observation loop after Win+E
                second_start = time.time()
                second_timeout = max(timeout - (second_start - start_time), 2.0)
                while time.time() - second_start < second_timeout:
                    windows = self.find_windows("explorer")
                    new_wins = [w for w in windows if w.hwnd not in pre_hwnds]
                    if new_wins:
                        detected_win = new_wins[0]
                        found = True
                        break
                    elif windows:
                        detected_win = windows[0]
                        found = True
                        break
                    time.sleep(0.3)

            if detected_win:
                self.register_owned_hwnd(detected_win.hwnd)
                logger.info(f"Detected window for {app_name_or_path}: {detected_win.title!r} (HWND: {detected_win.hwnd})")
                if activate:
                    self.activate_window(detected_win.hwnd)
            elif not found:
                logger.warning(
                    f"Application {app_name_or_path!r} launched, but no window detected within {timeout}s"
                )

        setattr(process, "detected_window", detected_win)
        setattr(process, "pre_hwnds", pre_hwnds)
        return process

    def list_windows(self, visible_only: bool = True) -> List[WindowInfo]:
        """Enumerate all current top-level desktop windows."""
        ensure_desktop_access()
        windows: List[WindowInfo] = []

        def enum_callback(hwnd: int, extra: Any) -> bool:
            title = win32gui.GetWindowText(hwnd)
            is_visible = bool(win32gui.IsWindowVisible(hwnd))
            class_name = win32gui.GetClassName(hwnd)

            if visible_only and (not is_visible or (not title.strip() and not class_name.strip())):
                return True

            rect = win32gui.GetWindowRect(hwnd)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            windows.append(
                WindowInfo(
                    hwnd=hwnd,
                    title=title,
                    rect=rect,
                    is_visible=is_visible,
                    pid=pid,
                    class_name=class_name,
                )
            )
            return True

        try:
            win32gui.EnumWindows(enum_callback, None)
        except Exception:
            time.sleep(0.1)
            try:
                windows.clear()
                win32gui.EnumWindows(enum_callback, None)
            except Exception as err:
                logger.warning(f"Transient error in EnumWindows: {err}")

        return windows

    def find_windows(self, title_query: str, exact: bool = False) -> List[WindowInfo]:
        """Search for visible windows whose title or class matches the query."""
        all_windows = self.list_windows(visible_only=True)
        query = title_query.lower().strip()
        matched: List[WindowInfo] = []

        for win in all_windows:
            title = win.title.lower()
            cls_name = win.class_name.lower()
            if exact:
                if title == query or cls_name == query:
                    matched.append(win)
            else:
                if query in title or query in cls_name:
                    matched.append(win)
                elif query in ("explorer", "file explorer") and "cabinetwclass" in cls_name:
                    matched.append(win)
                elif query in ("calc", "calculator") and (("calculator" in title or "calc" in title) or cls_name == "calcframe"):
                    matched.append(win)


        return matched

    def get_active_window(self) -> Optional[WindowInfo]:
        """Return information about the currently focused/active window."""
        ensure_desktop_access()
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd or not win32gui.IsWindow(hwnd):
            return None

        title = win32gui.GetWindowText(hwnd)
        rect = win32gui.GetWindowRect(hwnd)
        is_visible = bool(win32gui.IsWindowVisible(hwnd))
        class_name = win32gui.GetClassName(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)

        return WindowInfo(
            hwnd=hwnd,
            title=title,
            rect=rect,
            is_visible=is_visible,
            pid=pid,
            class_name=class_name,
        )

    def normalize_hwnd(self, hwnd_or_title: Union[int, str]) -> Union[int, str]:
        """Normalize numeric window handles while preserving normal title substring queries.

        If supplied value is an integer or numeric string that corresponds to an existing HWND
        via win32gui.IsWindow(), return the integer HWND. Otherwise return the original string.
        """
        if isinstance(hwnd_or_title, int):
            return hwnd_or_title

        if isinstance(hwnd_or_title, str):
            cleaned = hwnd_or_title.strip()
            is_numeric = (
                cleaned.isdigit()
                or (cleaned.startswith("-") and cleaned[1:].isdigit())
                or (cleaned.lower().startswith("0x") and len(cleaned) > 2)
            )
            if is_numeric:
                try:
                    cand_hwnd = int(cleaned, 0)
                    if win32gui.IsWindow(cand_hwnd):
                        return cand_hwnd
                except (ValueError, OverflowError):
                    pass
            return cleaned

        return hwnd_or_title

    def resolve_window_target(self, hwnd_or_title: Union[int, str]) -> Optional[WindowInfo]:
        """Resolve target window from an integer HWND, numeric HWND string, or title query."""
        ensure_desktop_access()
        normalized = self.normalize_hwnd(hwnd_or_title)
        if isinstance(normalized, int):
            if win32gui.IsWindow(normalized):
                title = win32gui.GetWindowText(normalized)
                rect = win32gui.GetWindowRect(normalized)
                is_visible = bool(win32gui.IsWindowVisible(normalized))
                class_name = win32gui.GetClassName(normalized)
                _, pid = win32process.GetWindowThreadProcessId(normalized)
                return WindowInfo(
                    hwnd=normalized,
                    title=title,
                    rect=rect,
                    is_visible=is_visible,
                    pid=pid,
                    class_name=class_name,
                )
            return None

        candidates = self.find_windows(str(normalized))
        if candidates:
            return candidates[0]

        # Dynamic title fallback for Notepad: if query contained "notepad" or "untitled",
        # look for any active or open Notepad window whose title may have dynamically updated
        query_str = str(normalized).lower().strip()
        if "notepad" in query_str or "untitled" in query_str:
            notepad_wins = self.find_windows("notepad")
            if notepad_wins:
                if self._active_state and hasattr(self._active_state, "get_owned_resources"):
                    for res in self._active_state.get_owned_resources():
                        if "notepad" in res.app_identity.lower() or "notepad" in res.title.lower():
                            for nw in notepad_wins:
                                if nw.hwnd == res.hwnd:
                                    return nw
                return notepad_wins[0]

        return None

    def ensure_target_focused(self, hwnd_or_title: Union[int, str]) -> bool:
        """Pre-action target locking: resolve, validate, activate, and verify foreground focus.

        Follows a strict multi-step protocol:
        1. Resolve target window.
        2. Validate HWND with IsWindow().
        3. Activate using the Windows API.
        4. Verify target is actually foreground.
        5. Retry API activation if necessary.
        6. Only use keyboard fallback if absolutely necessary and permitted by Safety Policy.
        7. Never blindly send Alt or global keystrokes.
        8. If foreground verification still fails, abort and return False.
        """
        ensure_desktop_access()
        target_win = self.resolve_window_target(hwnd_or_title)
        if not target_win:
            logger.warning(f"ensure_target_focused: Target window could not be resolved for {hwnd_or_title!r}")
            return False

        target_hwnd = target_win.hwnd
        if not win32gui.IsWindow(target_hwnd):
            logger.warning(f"ensure_target_focused: HWND {target_hwnd} is not a valid window")
            return False

        def _api_activate(hwnd: int) -> None:
            import ctypes
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetWindowPos(
                hwnd,
                win32con.HWND_TOP,
                0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
            )
            ctypes.windll.user32.BringWindowToTop(hwnd)
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                pass

        # Step 3: Activate using the Windows API
        try:
            _api_activate(target_hwnd)
        except Exception as err:
            logger.warning(f"Primary API activation error for HWND {target_hwnd}: {err}")

        time.sleep(0.05)

        # Step 4: Verify target is actually foreground
        active = self.get_active_window()
        if active and active.hwnd == target_hwnd:
            return True

        # Step 5: Retry API activation if necessary
        try:
            _api_activate(target_hwnd)
        except Exception:
            pass
        time.sleep(0.05)

        active = self.get_active_window()
        if active and active.hwnd == target_hwnd:
            return True

        # Step 6 & 7: Only use keyboard fallback if permitted by Safety Policy
        try:
            from safety.policy import get_safety_policy
            policy = get_safety_policy()
            decision = policy.evaluate_action("press_key", {"key": "alt"})
            if decision.allowed:
                import win32api
                win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_EXTENDEDKEY, 0)
                win32api.keybd_event(
                    win32con.VK_MENU, 0, win32con.KEYEVENTF_EXTENDEDKEY | win32con.KEYEVENTF_KEYUP, 0
                )
                _api_activate(target_hwnd)
        except Exception as err:
            logger.debug(f"Fallback focus unlock attempt error: {err}")

        time.sleep(0.05)

        # Final verification
        active = self.get_active_window()
        if active and active.hwnd == target_hwnd:
            return True

        # Step 8: Verification failed; abort action safely
        logger.warning(
            f"ensure_target_focused: Failed to establish foreground lock on {hwnd_or_title!r} (HWND {target_hwnd}). "
            f"Active window remains: {active.title!r} (HWND {active.hwnd if active else 'None'})"
        )
        return False

    def activate_window(self, hwnd_or_title: Union[int, str]) -> bool:
        """Bring target window to the foreground and set input focus."""
        return self.ensure_target_focused(hwnd_or_title)

    def is_protected_target_with_reason(
        self,
        hwnd: Optional[int] = None,
        pid: Optional[int] = None,
        title: str = "",
        class_name: str = "",
    ) -> Tuple[bool, str]:
        """Check if target process or window belongs to protected host, terminal, shell, or IDE."""
        target_pid = pid
        if hwnd and not target_pid and win32gui.IsWindow(hwnd):
            try:
                _, target_pid = win32process.GetWindowThreadProcessId(hwnd)
            except Exception:
                pass

        # 1. Protected runner and ancestor process IDs
        if target_pid:
            protected_pids = self.get_protected_pids()
            if target_pid in protected_pids:
                return True, f"Process PID {target_pid} belongs to the protected host/runner process tree"

        # 2. Protected process executable names
        if target_pid:
            try:
                import psutil
                proc_name = psutil.Process(target_pid).name().lower()
                for p_name in PROTECTED_PROCESS_NAMES:
                    if p_name in proc_name:
                        return True, f"Process executable {proc_name!r} is a protected environment"
            except Exception:
                pass

        # 3. Window title and class inspection
        win_title = ""
        win_cls = ""
        if hwnd and win32gui.IsWindow(hwnd):
            try:
                win_title = win32gui.GetWindowText(hwnd).lower()
                win_cls = win32gui.GetClassName(hwnd).lower()
            except Exception:
                pass

        cls_check = f"{class_name.lower()} {win_cls}".strip()
        if any(c in cls_check for c in ("consolewindowclass", "cascadia_hosting_window_class")):
            return True, f"Window class {cls_check!r} is a protected console or terminal"

        combined = f"{title.lower()} {win_title} {cls_check}".strip()
        for kw in PROTECTED_WINDOW_KEYWORDS:
            if kw in combined:
                return True, f"Matched protected keyword {kw!r}"

        # Standalone word boundary check for terminal / cmd / powershell
        if re.search(r"\b(cmd|powershell|pwsh|terminal)\b", combined):
            return True, "Matched protected terminal/shell identifier"

        return False, ""

    def is_protected_target(
        self, hwnd: Optional[int] = None, pid: Optional[int] = None, title: str = ""
    ) -> bool:
        """Check if target process or window belongs to the IDE/agent host and should be protected."""
        return self.is_protected_target_with_reason(hwnd=hwnd, pid=pid, title=title)[0]

    def close_window(self, hwnd_or_title: Union[int, str], force: bool = False) -> bool:
        """Close a window by sending WM_CLOSE or terminating its process after safety gate validation."""
        target_win = self.resolve_window_target(hwnd_or_title)

        # Central safety gate check (enforces protected, pre-existing, ownership invariants)
        gate_decision = self.safety_gate.evaluate_close_target(
            hwnd_or_title=hwnd_or_title,
            force=force,
            state=self._active_state,
            target_win=target_win,
        )

        if not gate_decision.allowed:
            logger.warning(
                f"Central safety gate BLOCKED close_window: {gate_decision.reason} "
                f"(target={hwnd_or_title!r}, force={force})"
            )
            return False

        if not target_win or not win32gui.IsWindow(target_win.hwnd):
            logger.info(f"Window {hwnd_or_title!r} is already closed or invalid.")
            return True

        target_hwnd = target_win.hwnd
        target_pid = target_win.pid
        target_title = target_win.title

        if force and target_pid:
            # Never force-kill the explorer.exe desktop shell
            try:
                import psutil
                if psutil.Process(target_pid).name().lower() == "explorer.exe":
                    logger.warning("Safety guard: Blocked force termination of explorer.exe shell process.")
                    force = False
            except Exception:
                pass

        if force and target_pid:
            logger.info(f"Force-terminating process pid={target_pid}")
            os.system(f"taskkill /F /PID {target_pid} >nul 2>&1")
            return True

        logger.info(f"Sending WM_CLOSE to window hwnd={target_hwnd}")
        try:
            win32gui.PostMessage(target_hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception as err:
            logger.warning(f"Error posting WM_CLOSE to hwnd={target_hwnd}: {err}")
        time.sleep(0.3)
        return True



_app_instance: Optional[AppController] = None


def get_app_controller() -> AppController:
    """Retrieve shared AppController instance."""
    global _app_instance
    if _app_instance is None:
        _app_instance = AppController()
    return _app_instance
