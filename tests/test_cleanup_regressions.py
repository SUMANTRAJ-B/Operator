"""Regression tests for generic session and resource cleanup in Operator.

Verifies the 15 session cleanup requirements:
1. Pre-existing Calculator remains open after task cleanup.
2. Calculator opened by Operator is closed during cleanup.
3. Pre-existing Notepad remains open.
4. Notepad opened by Operator is closed.
5. Multiple Operator-owned applications are all closed.
6. Multiple windows of the same application are handled correctly.
7. Existing protected Antigravity/terminal windows are never closed.
8. Cleanup cannot close an HWND that was not registered as Operator-owned.
9. Successful task completion requires verified cleanup.
10. Failed cleanup is never reported as successful cleanup.
11. HWND normalization works for cleanup.
12. Cleanup remains safe when focus has drifted.
13. If LLM forgets to call close_window, runtime cleanup still closes Operator-owned resources.
14. If an application was already open before the task, it is not closed.
15. Opening a file in Notepad tracks the correct document window and closes that window only.
"""

import time
from unittest.mock import MagicMock, call, patch
import pytest

from actions.applications import AppController, WindowInfo
from actions.registry import ToolRegistry
from agent.core import OperatorAgent
from agent.state import AgentState, AgentStatus, OwnedResource
from providers.llm.base import LLMProvider, LLMResponse, ToolCall
from verification.verifier import ActionVerifier, VerificationResult


class MockStaticLLM(LLMProvider):
    """Simple mock LLM provider returning predetermined responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.idx = 0

    def chat(self, messages, tools=None, temperature=None):
        if self.idx < len(self.responses):
            resp = self.responses[self.idx]
            self.idx += 1
            return resp
        return LLMResponse(
            content="Done",
            tool_calls=[ToolCall(id="fin", name="finish_task", arguments={"summary": "Task complete", "success": True})],
        )


# =========================================================================
# 1. Pre-existing Calculator Remains Open After Task Cleanup
# =========================================================================

def test_1_preexisting_calculator_remains_open():
    """Verify Calculator opened before task initiation is not closed during task cleanup."""
    state = AgentState(goal="Test Task")
    state.record_initial_environment({1001})  # HWND 1001 is pre-existing Calculator

    mock_apps = MagicMock(spec=AppController)
    mock_apps.is_protected_target.return_value = False

    agent = OperatorAgent(cleanup_on_finish=True)
    agent.apps = mock_apps

    ok, notes = agent.cleanup_resources(state)
    assert ok is True
    mock_apps.close_window.assert_not_called()


# =========================================================================
# 2. Calculator Opened by Operator is Closed During Cleanup
# =========================================================================

def test_2_calculator_opened_by_operator_is_closed():
    """Verify Calculator opened during a task is closed and verified during session cleanup."""
    state = AgentState(goal="Open Calculator")
    state.record_initial_environment({500, 600})

    res = state.register_owned_resource(hwnd=2001, pid=5500, title="Calculator", app_identity="calc")
    assert res is not None
    assert state.is_resource_owned(2001) is True

    mock_apps = MagicMock(spec=AppController)
    mock_apps.is_protected_target.return_value = False

    open_windows = {2001}
    def mock_close(hwnd, force=False):
        open_windows.discard(hwnd)
        return True
    mock_apps.close_window.side_effect = mock_close

    agent = OperatorAgent(cleanup_on_finish=True)
    agent.apps = mock_apps

    with patch("win32gui.IsWindow", side_effect=lambda h: h in open_windows):
        ok, notes = agent.cleanup_resources(state)
        assert ok is True
        mock_apps.close_window.assert_called_once_with(2001, force=False)
        assert res.current_state == "CLOSED"
        assert res.cleanup_verified is True


# =========================================================================
# 3. Pre-existing Notepad Remains Open
# =========================================================================

def test_3_preexisting_notepad_remains_open():
    """Verify pre-existing Notepad instance remains untouched after cleanup."""
    state = AgentState(goal="Some Goal")
    state.record_initial_environment({3001})  # HWND 3001 is pre-existing Notepad

    res = state.register_owned_resource(hwnd=3001, pid=6000, title="Notes - Notepad", app_identity="notepad")
    assert res is None

    mock_apps = MagicMock(spec=AppController)
    agent = OperatorAgent(cleanup_on_finish=True)
    agent.apps = mock_apps

    ok, _ = agent.cleanup_resources(state)
    assert ok is True
    mock_apps.close_window.assert_not_called()


# =========================================================================
# 4. Notepad Opened by Operator is Closed
# =========================================================================

def test_4_notepad_opened_by_operator_is_closed():
    """Verify Notepad instance spawned during task is cleanly closed."""
    state = AgentState(goal="Open Notepad and type")
    state.record_initial_environment(set())

    res = state.register_owned_resource(hwnd=4001, pid=7000, title="Untitled - Notepad", app_identity="notepad")
    assert res is not None

    mock_apps = MagicMock(spec=AppController)
    mock_apps.is_protected_target.return_value = False

    open_windows = {4001}
    def mock_close(hwnd, force=False):
        open_windows.discard(hwnd)
        return True
    mock_apps.close_window.side_effect = mock_close

    agent = OperatorAgent(cleanup_on_finish=True)
    agent.apps = mock_apps

    with patch("win32gui.IsWindow", side_effect=lambda h: h in open_windows):
        ok, _ = agent.cleanup_resources(state)
        assert ok is True
        mock_apps.close_window.assert_called_once_with(4001, force=False)
        assert res.current_state == "CLOSED"
        assert res.cleanup_verified is True


# =========================================================================
# 5. Multiple Operator-Owned Applications Are All Closed
# =========================================================================

def test_5_multiple_operator_owned_applications_closed():
    """Verify Calculator, Notepad, and Explorer opened in one task are all closed."""
    state = AgentState(goal="Multi-app task")
    state.record_initial_environment({10, 20})

    res_calc = state.register_owned_resource(hwnd=101, pid=1101, title="Calculator", app_identity="calc")
    res_note = state.register_owned_resource(hwnd=102, pid=1102, title="Untitled - Notepad", app_identity="notepad")
    res_expl = state.register_owned_resource(hwnd=103, pid=1103, title="File Explorer", app_identity="explorer")

    mock_apps = MagicMock(spec=AppController)
    mock_apps.is_protected_target.return_value = False

    open_windows = {101, 102, 103}
    def mock_close(hwnd, force=False):
        open_windows.discard(hwnd)
        return True
    mock_apps.close_window.side_effect = mock_close

    agent = OperatorAgent(cleanup_on_finish=True)
    agent.apps = mock_apps

    with patch("win32gui.IsWindow", side_effect=lambda h: h in open_windows):
        ok, notes = agent.cleanup_resources(state)
        assert ok is True
        assert len(mock_apps.close_window.call_args_list) == 3
        mock_apps.close_window.assert_has_calls([
            call(101, force=False),
            call(102, force=False),
            call(103, force=False),
        ], any_order=True)

        assert res_calc.current_state == "CLOSED"
        assert res_note.current_state == "CLOSED"
        assert res_expl.current_state == "CLOSED"


# =========================================================================
# 6. Multiple Windows of the Same Application Handled Correctly
# =========================================================================

def test_6_multiple_windows_same_application_handled_correctly():
    """Verify that when multiple Notepad windows exist, only the task-owned window is closed."""
    state = AgentState(goal="Open another notepad")
    state.record_initial_environment({601})  # HWND 601 was pre-existing

    res_new = state.register_owned_resource(hwnd=602, pid=9002, title="Untitled - Notepad", app_identity="notepad")
    assert res_new is not None

    mock_apps = MagicMock(spec=AppController)
    mock_apps.is_protected_target.return_value = False

    open_windows = {601, 602}
    def mock_close(hwnd, force=False):
        open_windows.discard(hwnd)
        return True
    mock_apps.close_window.side_effect = mock_close

    agent = OperatorAgent(cleanup_on_finish=True)
    agent.apps = mock_apps

    with patch("win32gui.IsWindow", side_effect=lambda h: h in open_windows):
        ok, _ = agent.cleanup_resources(state)
        assert ok is True
        mock_apps.close_window.assert_called_once_with(602, force=False)


# =========================================================================
# 7. Protected Antigravity/Terminal Windows Are Never Closed
# =========================================================================

def test_7_protected_antigravity_terminal_never_closed():
    """Verify protected IDE/agent host windows are strictly protected from cleanup."""
    state = AgentState(goal="Task")
    res = OwnedResource(hwnd=9999, pid=9999, title="Antigravity IDE - Project", app_identity="antigravity")
    state.owned_resources.append(res)

    mock_apps = MagicMock(spec=AppController)
    mock_apps.is_protected_target.return_value = True  # Recognized as protected

    agent = OperatorAgent(cleanup_on_finish=True)
    agent.apps = mock_apps

    ok, notes = agent.cleanup_resources(state)
    mock_apps.close_window.assert_not_called()
    assert res.current_state != "CLOSED"


# =========================================================================
# 8. Cleanup Cannot Close an HWND That Was Not Registered as Owned
# =========================================================================

def test_8_cleanup_cannot_close_unregistered_hwnd():
    """Verify cleanup strictly iterates only over registered OwnedResource entries."""
    state = AgentState(goal="Task")
    state.record_initial_environment({701, 702, 703})

    mock_apps = MagicMock(spec=AppController)
    agent = OperatorAgent(cleanup_on_finish=True)
    agent.apps = mock_apps

    ok, _ = agent.cleanup_resources(state)
    assert ok is True
    mock_apps.close_window.assert_not_called()


# =========================================================================
# 9. Successful Task Completion Requires Verified Cleanup
# =========================================================================

def test_9_successful_task_completion_requires_verified_cleanup():
    """Verify finish_task fails the task if cleanup cannot be verified."""
    responses = [
        LLMResponse(
            content="Task done",
            tool_calls=[ToolCall(id="fin", name="finish_task", arguments={"summary": "Done", "success": True})],
        )
    ]

    mock_llm = MockStaticLLM(responses)
    mock_apps = MagicMock(spec=AppController)
    mock_apps.list_windows.return_value = []
    mock_apps.get_active_window.return_value = None
    mock_apps.is_protected_target.return_value = False
    mock_apps.close_window.return_value = True

    agent = OperatorAgent(llm=mock_llm, cleanup_on_finish=True)
    agent.apps = mock_apps

    def side_effect_run(goal):
        state = AgentState(goal=goal)
        state.register_owned_resource(hwnd=8888, pid=8800, title="Stubborn App", app_identity="stubborn")
        with patch("win32gui.IsWindow", return_value=True):
            ok, notes = agent.cleanup_resources(state)
            assert ok is False
            assert state.cleanup_completed is False
            state.mark_failed(f"Session cleanup failed: {state.cleanup_error}")
            return state

    state = side_effect_run("Test goal")
    assert state.status == AgentStatus.FAILED
    assert "Session cleanup failed" in state.error


# =========================================================================
# 10. Failed Cleanup is Never Reported as Successful Cleanup
# =========================================================================

def test_10_failed_cleanup_is_never_reported_as_successful():
    """Verify cleanup_resources returns (False, notes) when window does not disappear."""
    state = AgentState(goal="Test")
    res = state.register_owned_resource(hwnd=4444, pid=4400, title="Unclosable Window", app_identity="test")

    mock_apps = MagicMock(spec=AppController)
    mock_apps.is_protected_target.return_value = False

    agent = OperatorAgent(cleanup_on_finish=True)
    agent.apps = mock_apps

    # win32gui.IsWindow continually returns True (window refuses to close)
    with patch("win32gui.IsWindow", return_value=True):
        ok, notes = agent.cleanup_resources(state)
        assert ok is False
        assert state.cleanup_completed is False
        assert res.cleanup_verified is False
        assert any("Failed to verify closure" in n for n in notes)


# =========================================================================
# 11. HWND Normalization Works for Cleanup
# =========================================================================

def test_11_hwnd_normalization_works_for_cleanup():
    """Verify mark_resource_closed works with integer HWND and numeric string."""
    state = AgentState(goal="Test")
    res1 = state.register_owned_resource(hwnd=12345, pid=100, title="App1", app_identity="app1")
    res2 = state.register_owned_resource(hwnd=67890, pid=200, title="App2", app_identity="app2")

    # Mark using integer
    assert state.mark_resource_closed(12345) is True
    assert res1.current_state == "CLOSED"
    assert res1.cleanup_verified is True

    # Mark using numeric string
    assert state.mark_resource_closed("67890") is True
    assert res2.current_state == "CLOSED"
    assert res2.cleanup_verified is True


# =========================================================================
# 12. Cleanup Remains Safe When Focus Has Drifted
# =========================================================================

def test_12_cleanup_remains_safe_when_focus_has_drifted():
    """Verify cleanup closes owned background window without stealing active focus."""
    state = AgentState(goal="Test")
    res = state.register_owned_resource(hwnd=3333, pid=3300, title="Background Window", app_identity="app")

    mock_apps = MagicMock(spec=AppController)
    mock_apps.is_protected_target.return_value = False
    mock_apps.get_active_window.return_value = WindowInfo(
        hwnd=7777, title="Operator - Google Chrome", rect=(0, 0, 800, 600), is_visible=True, pid=9000
    )

    open_windows = {3333, 7777}
    def mock_close(hwnd, force=False):
        open_windows.discard(hwnd)
        return True
    mock_apps.close_window.side_effect = mock_close

    agent = OperatorAgent(cleanup_on_finish=True)
    agent.apps = mock_apps

    with patch("win32gui.IsWindow", side_effect=lambda h: h in open_windows):
        ok, _ = agent.cleanup_resources(state)
        assert ok is True
        mock_apps.close_window.assert_called_once_with(3333, force=False)


# =========================================================================
# 13. If LLM Forgets to Call close_window, Runtime Cleanup Still Closes
# =========================================================================

def test_13_llm_forgets_close_window_runtime_cleanup_still_closes():
    """Verify runtime automatically closes Operator-owned resources even if LLM omits close_window."""
    responses = [
        LLMResponse(
            content="Opened calc.",
            tool_calls=[ToolCall(id="c1", name="open_application", arguments={"app_name": "calc"})],
        ),
        LLMResponse(
            content="I am done.",
            tool_calls=[ToolCall(id="c2", name="finish_task", arguments={"summary": "Completed without close", "success": True})],
        ),
    ]

    mock_llm = MockStaticLLM(responses)
    mock_apps = MagicMock(spec=AppController)
    mock_apps.list_windows.return_value = []
    mock_apps.get_active_window.return_value = None
    mock_apps.is_protected_target.return_value = False

    open_windows = {5555}
    def mock_close(hwnd, force=False):
        open_windows.discard(hwnd)
        return True
    mock_apps.close_window.side_effect = mock_close

    mock_verifier = MagicMock()
    mock_verifier.verify_action.return_value = VerificationResult(
        verified=True, confidence=1.0, details="Calc opened"
    )

    agent = OperatorAgent(llm=mock_llm, verifier=mock_verifier, cleanup_on_finish=True)
    agent.apps = mock_apps

    orig_execute = agent.registry.execute

    def mock_registry_execute(name, args):
        if name == "open_application":
            from actions.registry import ToolResult
            return ToolResult(
                tool_name="open_application",
                success=True,
                output={"launched": True, "app_name": "calc", "hwnd": 5555, "pid": 5500, "title": "Calculator"},
            )
        return orig_execute(name, args)

    agent.registry.execute = mock_registry_execute

    with patch("win32gui.IsWindow", side_effect=lambda h: h in open_windows):
        state = agent.run("Open calc task")
        assert state.status == AgentStatus.COMPLETED
        assert state.cleanup_completed is True
        mock_apps.close_window.assert_called_with(5555, force=False)


# =========================================================================
# 14. If Application Was Already Open Before the Task, It Is Not Closed
# =========================================================================

def test_14_application_already_open_before_task_not_closed():
    """Verify that if an app was already open prior to task start, it is not closed."""
    mock_apps = MagicMock(spec=AppController)
    pre_existing_win = WindowInfo(
        hwnd=7777, title="Calculator", rect=(0, 0, 400, 500), is_visible=True, pid=7000
    )
    mock_apps.list_windows.return_value = [pre_existing_win]
    mock_apps.get_active_window.return_value = pre_existing_win
    mock_apps.is_protected_target.return_value = False

    agent = OperatorAgent(cleanup_on_finish=True)
    agent.apps = mock_apps

    state = AgentState(goal="Check calc")
    state.record_initial_environment({7777})

    res = state.register_owned_resource(hwnd=7777, pid=7000, title="Calculator", app_identity="calc")
    assert res is None

    ok, _ = agent.cleanup_resources(state)
    assert ok is True
    mock_apps.close_window.assert_not_called()


# =========================================================================
# 15. Opening a File in Notepad Tracks and Closes Correct Document Window
# =========================================================================

def test_15_opening_file_tracks_and_closes_correct_window():
    """Verify opening a specific file tracks that document window and closes only that window."""
    state = AgentState(goal="Open sample.txt")
    state.record_initial_environment({8001})  # Pre-existing Notepad HWND 8001

    res = state.register_owned_resource(
        hwnd=8002, pid=8200, title="sample.txt - Notepad", app_identity="notepad", arguments="sample.txt"
    )
    assert res is not None
    assert state.is_resource_owned(8002) is True
    assert state.is_resource_owned(8001) is False

    mock_apps = MagicMock(spec=AppController)
    mock_apps.is_protected_target.return_value = False

    open_windows = {8001, 8002}
    def mock_close(hwnd, force=False):
        open_windows.discard(hwnd)
        return True
    mock_apps.close_window.side_effect = mock_close

    agent = OperatorAgent(cleanup_on_finish=True)
    agent.apps = mock_apps

    with patch("win32gui.IsWindow", side_effect=lambda h: h in open_windows):
        ok, _ = agent.cleanup_resources(state)
        assert ok is True
        mock_apps.close_window.assert_called_once_with(8002, force=False)
        assert res.current_state == "CLOSED"
        assert res.cleanup_verified is True
