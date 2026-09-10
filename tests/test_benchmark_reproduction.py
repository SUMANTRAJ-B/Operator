"""Comprehensive regression test suite reproducing the live benchmark failure path and verifying Central Safety Gate protections.

Verifies:
1. Benchmark reproduction:
   pre-existing PowerShell
   -> close_window("Windows PowerShell")
   -> centralized safety gate
   -> BLOCK
   -> WM_CLOSE NOT dispatched
   -> PowerShell remains open
2. Pre-existing PowerShell cannot be closed.
3. Pre-existing Windows Terminal cannot be closed.
4. Pre-existing CMD cannot be closed.
5. force=True cannot bypass protection.
6. Benchmark runner/host process remains protected.
7. Antigravity remains protected.
8. Unowned windows cannot be closed.
9. Operator-owned Calculator can still be closed.
10. Operator-owned Notepad can still be closed.
11. Cleanup can close only Operator-owned resources.
12. Task 2 verification without dependence on static 'Untitled - Notepad' title.
"""

import os
from unittest.mock import MagicMock, call, patch
import pytest
import win32con

from actions.applications import AppController, WindowInfo
from actions.registry import ToolRegistry
from agent.core import OperatorAgent
from agent.state import AgentState, AgentStatus
from safety.policy import CentralSafetyGate, SafetyPolicy
from verification.verifier import verify_notepad_content


# =========================================================================
# 1. Exact Live Benchmark Failure Path Reproduction
# =========================================================================

def test_benchmark_reproduction_preexisting_powershell_close_blocked_no_wm_close():
    """Reproduce exact live benchmark Task 7 failure path:
    pre-existing PowerShell
    -> close_window("Windows PowerShell")
    -> centralized safety gate
    -> BLOCK
    -> WM_CLOSE NOT dispatched
    -> PowerShell remains open
    """
    ps_hwnd = 9911
    ps_pid = 4455

    # Simulate pre-existing environment: PowerShell was already open before the task
    state = AgentState(goal="Open the existing sample.txt file.")
    state.record_initial_environment({ps_hwnd})

    app_controller = AppController()
    app_controller.set_active_state(state)

    # Mock window resolution for Windows PowerShell
    ps_win = WindowInfo(
        hwnd=ps_hwnd,
        title="Windows PowerShell",
        rect=(0, 0, 800, 600),
        is_visible=True,
        pid=ps_pid,
        class_name="ConsoleWindowClass",
    )

    with patch.object(app_controller, "resolve_window_target", return_value=ps_win), \
         patch("win32gui.IsWindow", return_value=True), \
         patch("win32gui.PostMessage") as mock_post_message, \
         patch("win32process.GetWindowThreadProcessId", return_value=(0, ps_pid)), \
         patch("psutil.Process") as mock_psutil_proc:

        proc_mock = MagicMock()
        proc_mock.name.return_value = "powershell.exe"
        proc_mock.pid = ps_pid
        mock_psutil_proc.return_value = proc_mock

        # Action: close_window("Windows PowerShell") as called in live benchmark
        success = app_controller.close_window("Windows PowerShell", force=False)

        # 1. Verify action returned False (BLOCK)
        assert success is False, "Central safety gate must block closing Windows PowerShell"

        # 2. Verify WM_CLOSE was NEVER dispatched
        # WM_CLOSE is win32con.WM_CLOSE (0x0010)
        wm_close_calls = [
            c for c in mock_post_message.call_args_list
            if len(c.args) >= 2 and c.args[1] == win32con.WM_CLOSE
        ]
        assert len(wm_close_calls) == 0, "WM_CLOSE must NOT be dispatched to Windows PowerShell!"
        mock_post_message.assert_not_called()

        # 3. Verify process termination was never attempted
        proc_mock.terminate.assert_not_called()
        proc_mock.kill.assert_not_called()


# =========================================================================
# 2. Pre-existing PowerShell Cannot Be Closed Under Any Formulation
# =========================================================================

@pytest.mark.parametrize("target", ["Windows PowerShell", "powershell", "powershell.exe", 9911])
def test_preexisting_powershell_cannot_be_closed(target):
    """Verify PowerShell cannot be closed whether targeted by name, title, or HWND."""
    ps_hwnd = 9911
    ps_pid = 4455

    state = AgentState(goal="Some task")
    state.record_initial_environment({ps_hwnd})

    app_controller = AppController()
    app_controller.set_active_state(state)

    ps_win = WindowInfo(
        hwnd=ps_hwnd,
        title="Windows PowerShell",
        rect=(0, 0, 800, 600),
        is_visible=True,
        pid=ps_pid,
        class_name="ConsoleWindowClass",
    )

    with patch.object(app_controller, "resolve_window_target", return_value=ps_win), \
         patch("win32gui.IsWindow", return_value=True), \
         patch("win32gui.PostMessage") as mock_post_message, \
         patch("win32process.GetWindowThreadProcessId", return_value=(0, ps_pid)), \
         patch("psutil.Process") as mock_psutil_proc:

        proc_mock = MagicMock()
        proc_mock.name.return_value = "powershell.exe"
        mock_psutil_proc.return_value = proc_mock

        res = app_controller.close_window(target, force=False)
        assert res is False
        mock_post_message.assert_not_called()


# =========================================================================
# 3. Pre-existing Windows Terminal Cannot Be Closed
# =========================================================================

@pytest.mark.parametrize("target", ["Windows Terminal", "wt", "windowsterminal", 8822])
def test_preexisting_windows_terminal_cannot_be_closed(target):
    """Verify Windows Terminal cannot be closed."""
    wt_hwnd = 8822
    wt_pid = 5566

    state = AgentState(goal="Benchmark run")
    state.record_initial_environment({wt_hwnd})

    app_controller = AppController()
    app_controller.set_active_state(state)

    wt_win = WindowInfo(
        hwnd=wt_hwnd,
        title="Windows Terminal",
        rect=(0, 0, 1024, 768),
        is_visible=True,
        pid=wt_pid,
        class_name="CASCADIA_HOSTING_WINDOW_CLASS",
    )

    with patch.object(app_controller, "resolve_window_target", return_value=wt_win), \
         patch("win32gui.IsWindow", return_value=True), \
         patch("win32gui.PostMessage") as mock_post_message, \
         patch("win32process.GetWindowThreadProcessId", return_value=(0, wt_pid)), \
         patch("psutil.Process") as mock_psutil_proc:

        proc_mock = MagicMock()
        proc_mock.name.return_value = "windowsterminal.exe"
        mock_psutil_proc.return_value = proc_mock

        res = app_controller.close_window(target, force=False)
        assert res is False
        mock_post_message.assert_not_called()


# =========================================================================
# 4. Pre-existing CMD Cannot Be Closed
# =========================================================================

@pytest.mark.parametrize("target", ["Command Prompt", "cmd", "cmd.exe", 7733])
def test_preexisting_cmd_cannot_be_closed(target):
    """Verify Command Prompt / cmd cannot be closed."""
    cmd_hwnd = 7733
    cmd_pid = 6677

    state = AgentState(goal="Benchmark run")
    state.record_initial_environment({cmd_hwnd})

    app_controller = AppController()
    app_controller.set_active_state(state)

    cmd_win = WindowInfo(
        hwnd=cmd_hwnd,
        title="Command Prompt",
        rect=(0, 0, 800, 600),
        is_visible=True,
        pid=cmd_pid,
        class_name="ConsoleWindowClass",
    )

    with patch.object(app_controller, "resolve_window_target", return_value=cmd_win), \
         patch("win32gui.IsWindow", return_value=True), \
         patch("win32gui.PostMessage") as mock_post_message, \
         patch("win32process.GetWindowThreadProcessId", return_value=(0, cmd_pid)), \
         patch("psutil.Process") as mock_psutil_proc:

        proc_mock = MagicMock()
        proc_mock.name.return_value = "cmd.exe"
        mock_psutil_proc.return_value = proc_mock

        res = app_controller.close_window(target, force=False)
        assert res is False
        mock_post_message.assert_not_called()


# =========================================================================
# 5. force=True Cannot Bypass Protection
# =========================================================================

@pytest.mark.parametrize("target,proc_name", [
    ("Windows PowerShell", "powershell.exe"),
    ("Windows Terminal", "windowsterminal.exe"),
    ("Command Prompt", "cmd.exe"),
    ("Antigravity", "antigravity.exe"),
])
def test_force_true_cannot_bypass_protection(target, proc_name):
    """Verify force=True is strictly prevented from bypassing the central safety gate."""
    target_hwnd = 6644
    target_pid = 7788

    app_controller = AppController()
    target_win = WindowInfo(
        hwnd=target_hwnd,
        title=target,
        rect=(0, 0, 800, 600),
        is_visible=True,
        pid=target_pid,
        class_name="WindowCls",
    )

    with patch.object(app_controller, "resolve_window_target", return_value=target_win), \
         patch("win32gui.IsWindow", return_value=True), \
         patch("win32gui.PostMessage") as mock_post_message, \
         patch("psutil.Process") as mock_psutil_proc:

        proc_mock = MagicMock()
        proc_mock.name.return_value = proc_name
        mock_psutil_proc.return_value = proc_mock

        res = app_controller.close_window(target, force=True)
        assert res is False, f"force=True must NOT allow closing {target}!"
        mock_post_message.assert_not_called()
        proc_mock.terminate.assert_not_called()
        proc_mock.kill.assert_not_called()


# =========================================================================
# 6. Benchmark Runner and Host Process Tree Remains Protected
# =========================================================================

def test_benchmark_runner_and_ancestors_remain_protected():
    """Verify any window belonging to the runner PID or its ancestor processes is blocked."""
    runner_pid = os.getpid()
    runner_hwnd = 5511

    app_controller = AppController()
    runner_win = WindowInfo(
        hwnd=runner_hwnd,
        title="Evaluation Runner Console",
        rect=(0, 0, 800, 600),
        is_visible=True,
        pid=runner_pid,
        class_name="ConsoleWindowClass",
    )

    with patch.object(app_controller, "resolve_window_target", return_value=runner_win), \
         patch("win32gui.IsWindow", return_value=True), \
         patch("win32gui.PostMessage") as mock_post_message:

        res = app_controller.close_window(runner_hwnd, force=True)
        assert res is False, "Runner process tree must be protected from closure!"
        mock_post_message.assert_not_called()


# =========================================================================
# 7. Antigravity IDE Remains Protected
# =========================================================================

def test_antigravity_remains_protected():
    """Verify Antigravity IDE window and process are strictly protected."""
    ag_hwnd = 4411
    ag_pid = 3322

    app_controller = AppController()
    ag_win = WindowInfo(
        hwnd=ag_hwnd,
        title="Operator - Antigravity",
        rect=(0, 0, 1920, 1080),
        is_visible=True,
        pid=ag_pid,
        class_name="Chrome_WidgetWin_1",
    )

    with patch.object(app_controller, "resolve_window_target", return_value=ag_win), \
         patch("win32gui.IsWindow", return_value=True), \
         patch("win32gui.PostMessage") as mock_post_message:

        res = app_controller.close_window("Antigravity", force=False)
        assert res is False
        mock_post_message.assert_not_called()

        res_force = app_controller.close_window(ag_hwnd, force=True)
        assert res_force is False
        mock_post_message.assert_not_called()


# =========================================================================
# 8. Unowned Windows Cannot Be Closed
# =========================================================================

def test_unowned_windows_cannot_be_closed():
    """Verify any window not tracked as an Operator-owned resource cannot be closed."""
    unowned_hwnd = 3311
    unowned_pid = 2211

    state = AgentState(goal="Do some calculations")
    # Not registered in owned_resources

    app_controller = AppController()
    app_controller.set_active_state(state)

    unowned_win = WindowInfo(
        hwnd=unowned_hwnd,
        title="Random Unrelated App",
        rect=(0, 0, 800, 600),
        is_visible=True,
        pid=unowned_pid,
        class_name="RandomClass",
    )

    with patch.object(app_controller, "resolve_window_target", return_value=unowned_win), \
         patch("win32gui.IsWindow", return_value=True), \
         patch("win32gui.PostMessage") as mock_post_message:

        decision = app_controller.safety_gate.evaluate_close_target(unowned_hwnd, state=state)
        assert decision.allowed is False
        assert "not registered as an Operator-owned resource" in decision.reason

        res = app_controller.close_window(unowned_hwnd)
        assert res is False
        mock_post_message.assert_not_called()


# =========================================================================
# 9. Operator-Owned Calculator CAN Be Closed
# =========================================================================

def test_operator_owned_calculator_can_be_closed():
    """Verify Calculator opened by Operator is permitted to close and dispatches WM_CLOSE."""
    calc_hwnd = 2211
    calc_pid = 1122

    state = AgentState(goal="Open Calculator")
    state.register_owned_resource(
        hwnd=calc_hwnd, pid=calc_pid, title="Calculator", app_identity="calc"
    )

    app_controller = AppController()
    app_controller.set_active_state(state)

    calc_win = WindowInfo(
        hwnd=calc_hwnd,
        title="Calculator",
        rect=(0, 0, 500, 600),
        is_visible=True,
        pid=calc_pid,
        class_name="ApplicationFrameWindow",
    )

    with patch.object(app_controller, "resolve_window_target", return_value=calc_win), \
         patch("win32gui.IsWindow", return_value=True), \
         patch("win32gui.PostMessage") as mock_post_message:

        decision = app_controller.safety_gate.evaluate_close_target(calc_hwnd, state=state)
        assert decision.allowed is True
        assert "Operator-owned and permitted to close" in decision.reason

        res = app_controller.close_window(calc_hwnd, force=False)
        assert res is True
        mock_post_message.assert_called_once_with(calc_hwnd, win32con.WM_CLOSE, 0, 0)


# =========================================================================
# 10. Operator-Owned Notepad CAN Be Closed
# =========================================================================

def test_operator_owned_notepad_can_be_closed():
    """Verify Notepad opened by Operator is permitted to close and dispatches WM_CLOSE."""
    np_hwnd = 4422
    np_pid = 3344

    state = AgentState(goal="Open Notepad and type")
    state.register_owned_resource(
        hwnd=np_hwnd, pid=np_pid, title="*Test Note - Notepad", app_identity="notepad"
    )

    app_controller = AppController()
    app_controller.set_active_state(state)

    np_win = WindowInfo(
        hwnd=np_hwnd,
        title="*Test Note - Notepad",
        rect=(0, 0, 800, 600),
        is_visible=True,
        pid=np_pid,
        class_name="Notepad",
    )

    with patch.object(app_controller, "resolve_window_target", return_value=np_win), \
         patch("win32gui.IsWindow", return_value=True), \
         patch("win32gui.PostMessage") as mock_post_message:

        decision = app_controller.safety_gate.evaluate_close_target(np_hwnd, state=state)
        assert decision.allowed is True
        assert "Operator-owned and permitted to close" in decision.reason

        res = app_controller.close_window(np_hwnd, force=False)
        assert res is True
        mock_post_message.assert_called_once_with(np_hwnd, win32con.WM_CLOSE, 0, 0)


# =========================================================================
# 11. Cleanup Can Close ONLY Operator-Owned Resources
# =========================================================================

def test_cleanup_closes_only_operator_owned_resources():
    """Verify agent.cleanup_resources strictly closes only Operator-owned resources,
    ignoring pre-existing, protected, and unowned windows.
    """
    state = AgentState(goal="Task with mixed resources")
    # Pre-existing HWNDs
    state.record_initial_environment({1001, 1002})

    # Only 2001 is Operator-owned
    res_owned = state.register_owned_resource(
        hwnd=2001, pid=2200, title="Calculator", app_identity="calc"
    )
    assert res_owned is not None

    mock_apps = MagicMock(spec=AppController)
    mock_apps.is_protected_target.return_value = False

    closed_hwnds = []
    def mock_close(hwnd, force=False):
        closed_hwnds.append(hwnd)
        return True
    mock_apps.close_window.side_effect = mock_close

    agent = OperatorAgent(cleanup_on_finish=True)
    agent.apps = mock_apps

    with patch("win32gui.IsWindow", side_effect=lambda h: h not in closed_hwnds):
        ok, notes = agent.cleanup_resources(state)
        assert ok is True
        # Only HWND 2001 was closed
        assert closed_hwnds == [2001]
        assert 1001 not in closed_hwnds
        assert 1002 not in closed_hwnds
        assert res_owned.current_state == "CLOSED"
        assert res_owned.cleanup_verified is True


# =========================================================================
# 12. Task 2: Dynamic Notepad Title and Content Verification
# =========================================================================

def test_task_2_dynamic_notepad_verification_without_static_title():
    """Verify Task 2 does not require static 'Untitled - Notepad' title and succeeds
    with dynamic Windows 11 title and actual document content.
    """
    np_hwnd = 5555
    sentence = "The quick brown fox jumps over the lazy dog"

    state = AgentState(goal="Open Notepad and type sentence")
    state.register_owned_resource(
        hwnd=np_hwnd, pid=6666, title=f"*{sentence[:20]}... - Notepad", app_identity="notepad"
    )

    mock_apps = MagicMock(spec=AppController)
    mock_apps._active_state = state
    mock_apps.find_windows.return_value = [
        WindowInfo(
            hwnd=np_hwnd,
            title=f"*{sentence[:30]} - Notepad",  # Dynamic Windows 11 title
            rect=(100, 100, 900, 700),
            is_visible=True,
            pid=6666,
            class_name="Notepad",
        )
    ]

    with patch("verification.verifier.get_notepad_text", return_value=sentence), \
         patch("win32gui.IsWindow", return_value=True), \
         patch("win32gui.GetWindowText", return_value=f"*{sentence[:30]} - Notepad"):

        verified = verify_notepad_content(sentence, app_controller=mock_apps, target_hwnd=np_hwnd)
        assert verified is True, "Task 2 must verify content in dynamic Notepad window"
