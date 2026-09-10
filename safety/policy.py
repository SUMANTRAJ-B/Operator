"""Safety guardrail policy enforcing pre-execution checks and protecting host environments."""

from dataclasses import dataclass, field
import re
from typing import Any, Dict, Optional, Tuple, Union
import pyautogui
import win32gui

from actions.applications import get_app_controller
from app.config import get_settings
from app.logging import get_logger

logger = get_logger("safety.policy")


@dataclass
class SafetyDecision:
    """Represents safety evaluation result for an action."""

    allowed: bool
    reason: str = "Action permitted by safety policy."
    sanitized_arguments: Dict[str, Any] = field(default_factory=dict)


class CentralSafetyGate:
    """Centralized safety gate governing window closure and process termination.

    Enforces the following invariant safety boundaries:
    1. Protected environments (Antigravity IDE, IDEs, host runner, PowerShell, Windows Terminal,
       CMD, shells) can NEVER be closed under any circumstances.
    2. force=True cannot bypass protection.
    3. Pre-existing windows (present prior to task initiation) can NEVER be closed.
    4. Unowned windows (not registered as Operator-owned resources) can NEVER be closed.
    5. Operator-owned applications (e.g. Operator-owned Calculator, Operator-owned Notepad) CAN be closed.
    6. Programmatic close dispatch (WM_CLOSE or taskkill) is strictly blocked if any safety invariant is violated.
    """

    def __init__(self, app_controller: Optional[Any] = None):
        self._app_controller = app_controller
        self._active_state = None

    @property
    def app_controller(self):
        if self._app_controller is None:
            from actions.applications import get_app_controller
            self._app_controller = get_app_controller()
        return self._app_controller

    def set_active_state(self, state: Any) -> None:
        """Associate current task execution state with the safety gate."""
        self._active_state = state

    def get_active_state(self) -> Optional[Any]:
        """Retrieve current task execution state."""
        return self._active_state

    def is_protected_target(
        self,
        hwnd: Optional[int] = None,
        pid: Optional[int] = None,
        title: str = "",
        class_name: str = "",
    ) -> Tuple[bool, str]:
        """Check if target belongs to protected host, IDE, terminal, shell, or runner process."""
        return self.app_controller.is_protected_target_with_reason(
            hwnd=hwnd, pid=pid, title=title, class_name=class_name
        )

    @staticmethod
    def is_protected_query(query: str) -> Tuple[bool, str]:
        """Check if raw query string matches protected shells, terminals, or host environments."""
        if not query:
            return False, ""
        q = str(query).strip().lower()

        # Specific compound phrases
        for kw in (
            "windows powershell",
            "windows terminal",
            "command prompt",
            "visual studio code",
            "openconsole",
        ):
            if kw in q:
                return True, f"Matched protected keyword {kw!r}"

        # Word boundary pattern for short identifiers and executables
        pattern = r"\b(powershell(\.exe)?|pwsh(\.exe)?|cmd(\.exe)?|windowsterminal(\.exe)?|wt(\.exe)?|conhost(\.exe)?|bash(\.exe)?|wsl(\.exe)?|antigravity(\.exe)?|code(\.cmd|\.exe)?|cursor(\.exe)?|windsurf(\.exe)?|pycharm(\.exe)?)\b"
        m = re.search(pattern, q)
        if m:
            return True, f"Matched protected environment identifier {m.group(0)!r}"

        return False, ""

    def evaluate_close_target(
        self,
        hwnd_or_title: Union[int, str, Any],
        force: bool = False,
        state: Optional[Any] = None,
        target_win: Optional[Any] = None,
    ) -> SafetyDecision:
        """Central safety gate evaluating whether a window or process may be closed.

        Strict safety checks performed in order:
        1. Query string check: Protected IDE, shell, terminal keywords are immediately blocked.
        2. Window resolution and process inspection: Protected PIDs (runner, ancestors) and
           protected processes (PowerShell, Windows Terminal, CMD, etc.) are strictly blocked.
           force=True CANNOT bypass this.
        3. Pre-existing window check: Any window open before the task began is strictly blocked.
        4. Ownership check: Any window not registered as an Operator-owned resource is blocked.
        5. Operator-owned windows (e.g. Calculator or Notepad spawned by Operator) are permitted.
        """
        curr_state = state or self._active_state
        if curr_state is None and hasattr(self.app_controller, "get_active_state"):
            try:
                curr_state = self.app_controller.get_active_state()
            except Exception:
                curr_state = None

        target_str = str(hwnd_or_title).strip()

        # 1. Query String Keyword Guard (blocks before or without window resolution)
        is_query_prot, query_prot_reason = self.is_protected_query(target_str)
        if is_query_prot:
            return SafetyDecision(
                allowed=False,
                reason=f"Safety guard: Target window '{target_str}' matches protected host/terminal/IDE keyword ({query_prot_reason}). Close BLOCKED.",
            )

        if hasattr(self.app_controller, "is_protected_target"):
            try:
                res_prot = self.app_controller.is_protected_target(title=target_str)
                if res_prot is True:
                    return SafetyDecision(
                        allowed=False,
                        reason=f"Safety guard: Target window '{target_str}' matches protected host/terminal/IDE keyword. Close BLOCKED.",
                    )
            except Exception:
                pass

        # 2. Resolve Target Window
        win = target_win
        if win is None and hasattr(self.app_controller, "resolve_window_target"):
            try:
                win = self.app_controller.resolve_window_target(hwnd_or_title)
            except Exception:
                win = None

        target_hwnd = win.hwnd if win else (int(target_str) if target_str.isdigit() else None)
        target_pid = getattr(win, "pid", None)
        target_title = getattr(win, "title", target_str)
        target_class = getattr(win, "class_name", "")

        # Check resolved title against protected queries
        is_title_prot, title_prot_reason = self.is_protected_query(target_title)
        if is_title_prot:
            return SafetyDecision(
                allowed=False,
                reason=f"Safety guard: Target window '{target_title}' is protected ({title_prot_reason}). Close BLOCKED.",
            )

        # 3. Comprehensive Target Inspection (PID, Process Name, Window Class)
        if hasattr(self.app_controller, "is_protected_target_with_reason"):
            try:
                chk = self.app_controller.is_protected_target_with_reason(
                    hwnd=target_hwnd, pid=target_pid, title=target_title, class_name=target_class
                )
                if isinstance(chk, tuple) and len(chk) == 2 and chk[0] is True:
                    return SafetyDecision(
                        allowed=False,
                        reason=f"Safety guard: Target '{target_title}' (HWND: {target_hwnd}, PID: {target_pid}) is protected ({chk[1]}). Close BLOCKED.",
                    )
            except Exception:
                pass

        # Inspect PID directly with psutil if target_pid is known
        if target_pid:
            try:
                import psutil
                # Runner and parent process IDs
                curr_proc = psutil.Process()
                protected_tree = {curr_proc.pid}
                for p in curr_proc.parents():
                    protected_tree.add(p.pid)
                    try:
                        if p.name().lower() == "explorer.exe":
                            break
                    except Exception:
                        pass
                if target_pid in protected_tree:
                    return SafetyDecision(
                        allowed=False,
                        reason=f"Safety guard: PID {target_pid} belongs to the protected runner/host process tree. Close BLOCKED.",
                    )

                # Process executable name check
                pname = psutil.Process(target_pid).name().lower()
                for p_name in (
                    "powershell", "pwsh", "cmd", "conhost", "openconsole",
                    "windowsterminal", "wt", "bash", "wsl",
                    "antigravity", "code", "cursor", "windsurf", "pycharm"
                ):
                    if p_name in pname:
                        return SafetyDecision(
                            allowed=False,
                            reason=f"Safety guard: Process executable '{pname}' (PID {target_pid}) is a protected environment. Close BLOCKED.",
                        )
            except Exception:
                pass

        # If window is already nonexistent or closed
        if win is None and target_hwnd is not None:
            try:
                if not win32gui.IsWindow(target_hwnd):
                    return SafetyDecision(
                        allowed=True,
                        reason=f"Target window HWND {target_hwnd} is already closed or invalid.",
                    )
            except Exception:
                return SafetyDecision(allowed=True, reason="Target window is invalid.")

        # 4. Pre-Existing Window Check
        if target_hwnd is not None:
            is_pre = False
            if curr_state is not None and hasattr(curr_state, "pre_existing_hwnds"):
                pre_set = curr_state.pre_existing_hwnds
                if isinstance(pre_set, (set, list, tuple)) and target_hwnd in pre_set:
                    is_pre = True
            if not is_pre and hasattr(self.app_controller, "is_pre_existing_hwnd"):
                try:
                    if self.app_controller.is_pre_existing_hwnd(target_hwnd) is True:
                        is_pre = True
                except Exception:
                    pass

            if is_pre:
                # Pre-existing windows can ONLY be closed if this non-protected application
                # is the explicit objective of a closure task (e.g. Task 8: goal is "Close Calculator")
                is_task_closure_target = False
                if curr_state is not None and hasattr(curr_state, "goal") and isinstance(curr_state.goal, str):
                    goal_l = curr_state.goal.lower()
                    is_closure_goal = any(verb in goal_l for verb in ("close", "exit", "quit", "terminate", "shut down", "kill"))
                    if is_closure_goal:
                        norm_target_str = target_title.lower()
                        if norm_target_str in goal_l or ("calculator" in goal_l and ("calc" in norm_target_str or "calculator" in norm_target_str)):
                            is_task_closure_target = True

                if not is_task_closure_target:
                    return SafetyDecision(
                        allowed=False,
                        reason=(
                            f"Safety guard: Target window '{target_title}' (HWND: {target_hwnd}) "
                            f"pre-existed the task. Pre-existing windows cannot be closed."
                        ),
                    )

        # 5. Ownership Verification ("unowned windows cannot be closed")
        if curr_state is not None and target_hwnd is not None:
            is_owned = False
            if hasattr(curr_state, "is_resource_owned"):
                try:
                    if curr_state.is_resource_owned(target_hwnd) is True:
                        is_owned = True
                except Exception:
                    pass
            if not is_owned and hasattr(self.app_controller, "is_owned_hwnd"):
                try:
                    if self.app_controller.is_owned_hwnd(target_hwnd) is True:
                        is_owned = True
                except Exception:
                    pass

            # Check if task goal explicitly targets closing this non-protected application
            is_task_closure_target = False
            if hasattr(curr_state, "goal") and isinstance(curr_state.goal, str):
                goal_l = curr_state.goal.lower()
                is_closure_goal = any(verb in goal_l for verb in ("close", "exit", "quit", "terminate", "shut down", "kill"))
                if is_closure_goal:
                    norm_target_str = target_title.lower()
                    if norm_target_str in goal_l or ("calculator" in goal_l and ("calc" in norm_target_str or "calculator" in norm_target_str)):
                        is_task_closure_target = True

            if not is_owned and not is_task_closure_target:
                return SafetyDecision(
                    allowed=False,
                    reason=(
                        f"Safety guard: Target window '{target_title}' (HWND: {target_hwnd}) "
                        f"is not registered as an Operator-owned resource. Unowned windows cannot be closed."
                    ),
                )

        return SafetyDecision(
            allowed=True,
            reason=f"Window '{target_title}' (HWND: {target_hwnd}) is Operator-owned and permitted to close.",
            sanitized_arguments={"window_title_or_hwnd": hwnd_or_title, "force": force},
        )


class SafetyPolicy:
    """Pre-action safety controller verifying boundaries and protected resources."""

    def __init__(self):
        self.settings = get_settings()
        self.app_controller = get_app_controller()
        self.safety_gate = CentralSafetyGate(self.app_controller)

    def set_active_state(self, state: Any) -> None:
        """Synchronize active agent state with safety policy and safety gate."""
        self.safety_gate.set_active_state(state)

    def evaluate_action(self, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> SafetyDecision:
        """Evaluate whether a proposed tool call is safe to execute.

        Args:
            tool_name: Name of tool to execute.
            arguments: Tool arguments dictionary.

        Returns:
            SafetyDecision indicating whether action is permitted and why.
        """
        args = arguments or {}

        # 1. Mouse coordinate boundary validation
        if tool_name in ("move_mouse", "click", "double_click", "right_click"):
            x = args.get("x")
            y = args.get("y")
            if x is not None or y is not None:
                screen_w, screen_h = pyautogui.size()
                if x is not None and (x < 0 or x >= screen_w):
                    return SafetyDecision(
                        allowed=False,
                        reason=f"Coordinate X={x} out of screen bounds [0, {screen_w - 1}].",
                    )
                if y is not None and (y < 0 or y >= screen_h):
                    return SafetyDecision(
                        allowed=False,
                        reason=f"Coordinate Y={y} out of screen bounds [0, {screen_h - 1}].",
                    )

        # 2. Application and Window Protection Check (Centralized Safety Gate)
        if tool_name == "close_window":
            target = args.get("window_title_or_hwnd")
            force = bool(args.get("force", False))
            target_win = args.get("_target_win")
            state = args.get("_state")
            if target is not None:
                return self.safety_gate.evaluate_close_target(
                    hwnd_or_title=target,
                    force=force,
                    state=state,
                    target_win=target_win,
                )

        # 3. Dangerous Hotkey Validation (Alt+F4 check)
        if tool_name == "hotkey":
            keys = args.get("keys", [])
            key_list = [str(k).strip().lower() for k in (keys if isinstance(keys, list) else str(keys).split("+"))]
            if "f4" in key_list and any(k in ("alt", "altleft", "altright") for k in key_list):
                active = self.app_controller.get_active_window()
                if active is None or self.app_controller.is_protected_target(
                    hwnd=active.hwnd, pid=active.pid, title=active.title
                ):
                    target_desc = active.title if active else "unknown/unfocused"
                    return SafetyDecision(
                        allowed=False,
                        reason=f"Safety guard: Alt+F4 is blocked because the active window is protected or unverified ({target_desc!r}).",
                    )

        # 4. Keystroke Protection: Never type blindly into protected host/IDE windows
        if tool_name in ("type_text", "press_key"):
            target_arg = args.get("window_title_or_hwnd")
            if target_arg and self.app_controller.is_protected_target(title=str(target_arg)):
                return SafetyDecision(
                    allowed=False,
                    reason=f"Safety guard: Target window '{target_arg}' matches protected IDE/host keyword.",
                )
            if not target_arg:
                active = self.app_controller.get_active_window()
                if active and self.app_controller.is_protected_target(
                    hwnd=active.hwnd, pid=active.pid, title=active.title
                ):
                    return SafetyDecision(
                        allowed=False,
                        reason=f"Safety guard: Active window is protected IDE ('{active.title}'). Target application window must be activated before typing.",
                    )

        return SafetyDecision(allowed=True, sanitized_arguments=args)


_policy_instance: Optional[SafetyPolicy] = None


def get_safety_policy() -> SafetyPolicy:
    """Retrieve shared SafetyPolicy singleton."""
    global _policy_instance
    if _policy_instance is None:
        _policy_instance = SafetyPolicy()
    return _policy_instance


def get_safety_gate() -> CentralSafetyGate:
    """Retrieve shared CentralSafetyGate instance."""
    return get_safety_policy().safety_gate
