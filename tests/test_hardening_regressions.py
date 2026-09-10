"""Comprehensive regression tests verifying post-benchmark engineering hardening.

Covers the 8 concrete engineering requirements:
1. Focus drift before typing.
2. Target activation immediately before input.
3. Numeric-string HWND normalization.
4. Invalid HWND handling.
5. Protected HWND safety.
6. Explorer fallback behavior.
7. Inference context compaction.
8. Timeout/retry behavior.
"""

import time
from unittest.mock import MagicMock, call, patch
import pytest
import requests

from actions.applications import AppController, WindowInfo, get_app_controller
from actions.registry import ToolRegistry, get_tool_registry
from agent.core import OperatorAgent
from agent.memory import AgentMemory
from agent.state import AgentStatus
from providers.llm.base import ChatMessage, LLMProvider, LLMResponse, ToolCall
from providers.llm.ollama import OllamaProvider
from verification.verifier import ActionVerifier


# =========================================================================
# 1. Focus Drift Before Typing
# =========================================================================

def test_focus_drift_before_typing_restores_and_verifies_target():
    """Verify that when focus drifts to another window (e.g. Chrome), target lock restores target window."""
    app_ctrl = AppController()
    target_win = WindowInfo(hwnd=1001, title="Untitled - Notepad", rect=(0, 0, 400, 300), is_visible=True, pid=5000)
    drift_win = WindowInfo(hwnd=2002, title="Operator - Google Chrome", rect=(0, 0, 800, 600), is_visible=True, pid=6000)

    current_active = drift_win

    def mock_get_active():
        return current_active

    with patch.object(app_ctrl, "resolve_window_target", return_value=target_win), \
         patch.object(app_ctrl, "get_active_window", side_effect=mock_get_active), \
         patch("win32gui.IsWindow", return_value=True), \
         patch("win32gui.ShowWindow"), \
         patch("win32gui.SetWindowPos"), \
         patch("win32gui.SetForegroundWindow") as mock_set_fg, \
         patch("ctypes.windll.user32.BringWindowToTop"):

        def side_effect_fg(hwnd):
            nonlocal current_active
            if hwnd == 1001:
                current_active = target_win

        mock_set_fg.side_effect = side_effect_fg

        res = app_ctrl.ensure_target_focused("Notepad")
        assert res is True
        assert current_active.hwnd == 1001


def test_focus_drift_fails_safely_if_target_cannot_be_focused():
    """Verify that if target window cannot be brought to foreground, typing is aborted."""
    app_ctrl = AppController()
    drift_win = WindowInfo(hwnd=2002, title="Operator - Google Chrome", rect=(0, 0, 800, 600), is_visible=True, pid=6000)
    target_win = WindowInfo(hwnd=1001, title="Untitled - Notepad", rect=(0, 0, 400, 300), is_visible=True, pid=5000)

    with patch.object(app_ctrl, "resolve_window_target", return_value=target_win), \
         patch.object(app_ctrl, "get_active_window", return_value=drift_win), \
         patch("win32gui.IsWindow", return_value=True), \
         patch("win32gui.ShowWindow"), \
         patch("win32gui.SetWindowPos"), \
         patch("win32gui.SetForegroundWindow"), \
         patch("ctypes.windll.user32.BringWindowToTop"):

        res = app_ctrl.ensure_target_focused("Notepad")
        assert res is False


# =========================================================================
# 2. Target Activation Immediately Before Input
# =========================================================================

def test_type_text_tool_locks_target_before_typing():
    """Verify registry type_text tool enforces target lock before dispatching keys."""
    registry = get_tool_registry()
    app_ctrl = get_app_controller()

    with patch.object(app_ctrl, "ensure_target_focused", return_value=True) as mock_lock, \
         patch("pyautogui.write") as mock_write:

        res = registry.execute("type_text", {"text": "hello", "window_title_or_hwnd": "Notepad"})
        assert res.success is True
        mock_lock.assert_called_once_with("Notepad")
        mock_write.assert_called_once()


def test_type_text_tool_aborts_when_target_lock_fails():
    """Verify registry type_text tool aborts with error when target lock fails."""
    registry = get_tool_registry()
    app_ctrl = get_app_controller()

    with patch.object(app_ctrl, "ensure_target_focused", return_value=False), \
         patch("pyautogui.write") as mock_write:

        res = registry.execute("type_text", {"text": "hello", "window_title_or_hwnd": "Notepad"})
        assert res.success is False
        assert "Pre-action target lock failed" in res.error
        mock_write.assert_not_called()


# =========================================================================
# 3. Numeric-String HWND Normalization
# =========================================================================

def test_numeric_string_hwnd_normalization():
    """Verify string '4200002' is normalized to integer 4200002 when window exists."""
    app_ctrl = AppController()

    with patch("win32gui.IsWindow", side_effect=lambda h: h == 4200002):
        # Numeric string for valid HWND -> integer
        assert app_ctrl.normalize_hwnd("4200002") == 4200002
        assert app_ctrl.normalize_hwnd(" 4200002 ") == 4200002

        # Integer HWND -> integer
        assert app_ctrl.normalize_hwnd(4200002) == 4200002


def test_close_window_with_numeric_string_hwnd():
    """Verify close_window handles numeric-string HWND and dispatches WM_CLOSE."""
    app_ctrl = AppController()
    target_hwnd = 4200002

    with patch("win32gui.IsWindow", return_value=True), \
         patch("win32gui.GetWindowText", return_value="Calculator"), \
         patch("win32gui.GetWindowRect", return_value=(0, 0, 100, 100)), \
         patch("win32gui.IsWindowVisible", return_value=True), \
         patch("win32gui.GetClassName", return_value="ApplicationFrameWindow"), \
         patch("win32process.GetWindowThreadProcessId", return_value=(1, 5555)), \
         patch.object(app_ctrl, "is_protected_target", return_value=False), \
         patch("win32gui.PostMessage") as mock_post:

        res = app_ctrl.close_window("4200002")
        assert res is True
        # Must send WM_CLOSE (0x0010 == 16) to HWND 4200002
        mock_post.assert_called_once_with(target_hwnd, 16, 0, 0)


# =========================================================================
# 4. Invalid HWND Handling
# =========================================================================

def test_invalid_hwnd_handling():
    """Verify non-existent HWNDs fall back safely to title query and do not crash."""
    app_ctrl = AppController()

    with patch("win32gui.IsWindow", return_value=False), \
         patch.object(app_ctrl, "find_windows", return_value=[]):

        # Non-existent HWND numeric string falls back to title query
        assert app_ctrl.normalize_hwnd("999999999") == "999999999"

        # resolve_window_target returns None without exception
        assert app_ctrl.resolve_window_target("999999999") is None

        # close_window returns True (idempotent / already closed)
        assert app_ctrl.close_window("999999999") is True

        # ensure_target_focused returns False safely
        assert app_ctrl.ensure_target_focused("999999999") is False


# =========================================================================
# 5. Protected HWND Safety
# =========================================================================

def test_protected_hwnd_safety_prevents_closing():
    """Verify that protected host IDE windows cannot be closed even when targeted by HWND."""
    app_ctrl = AppController()

    with patch("win32gui.IsWindow", return_value=True), \
         patch("win32gui.GetWindowText", return_value="Operator - Antigravity IDE"), \
         patch("win32gui.GetWindowRect", return_value=(0, 0, 1000, 800)), \
         patch("win32gui.IsWindowVisible", return_value=True), \
         patch("win32gui.GetClassName", return_value="Chrome_WidgetWin_1"), \
         patch("win32process.GetWindowThreadProcessId", return_value=(1, 9999)), \
         patch.object(app_ctrl, "is_protected_target", return_value=True), \
         patch("win32gui.PostMessage") as mock_post:

        # Attempting to close protected window via numeric string HWND
        res = app_ctrl.close_window("300001")
        assert res is False
        mock_post.assert_not_called()


# =========================================================================
# 6. Explorer Fallback Behavior (Win+E)
# =========================================================================

def test_explorer_fallback_uses_win_e_when_normal_launch_fails():
    """Verify open_application triggers Win+E shortcut if explorer window does not appear."""
    app_ctrl = AppController()
    created_win = WindowInfo(
        hwnd=8001, title="File Explorer", rect=(0, 0, 800, 600), is_visible=True, pid=1200, class_name="CabinetWClass"
    )

    with patch("subprocess.Popen"), \
         patch.object(app_ctrl, "activate_window", return_value=True) as mock_activate, \
         patch("actions.keyboard.KeyboardController.hotkey") as mock_hotkey:

        def mock_find_windows(query):
            # Before Win+E: no visible Explorer window
            if not mock_hotkey.called:
                return []
            # After Win+E: Explorer window appears
            return [created_win]

        with patch.object(app_ctrl, "find_windows", side_effect=mock_find_windows):
            app_ctrl.open_application("explorer", wait_for_window=True, timeout=1.0)

            # Win+E hotkey was triggered
            mock_hotkey.assert_called_with("win", "e")
            # Detected window was activated
            mock_activate.assert_called_with(8001)


# =========================================================================
# 7. Inference Context Compaction
# =========================================================================

def test_inference_context_compaction_prunes_stale_observations():
    """Verify AgentMemory prunes older redundant desktop observations while keeping the latest."""
    memory = AgentMemory()
    memory.set_goal("Test Goal")

    # Turn 1
    memory.add_observation("Screen: 1920x1080. Active Window: 'Chrome' (HWND: 101). Open Windows: ['Chrome'].")
    memory.add_assistant_response(content="Opening calc", tool_calls=[ToolCall(id="c1", name="open_application", arguments={"app_name": "calc"})])
    memory.add_tool_result("c1", "open_application", "Launched calc")

    # Turn 2
    memory.add_observation("Screen: 1920x1080. Active Window: 'Calculator' (HWND: 202). Open Windows: ['Calculator', 'Chrome'].")

    messages = memory.get_messages(compact=True)

    # Count how many desktop observation messages exist
    obs_msgs = [m for m in messages if m.role == "user" and m.content.startswith("[Desktop Observation]:")]
    # Exactly ONE desktop observation (the latest one) must be retained
    assert len(obs_msgs) == 1
    assert "Calculator" in obs_msgs[0].content


def test_inference_context_compaction_deep_trajectory():
    """Verify deep trajectory (> 3 assistant steps) compacts older steps into summary."""
    memory = AgentMemory()
    memory.set_goal("Multi-step task")

    for step in range(1, 6):
        memory.add_observation(f"Screen: 1920x1080. Step {step} state.")
        memory.add_assistant_response(
            content=f"Doing step {step}",
            tool_calls=[ToolCall(id=f"c{step}", name="press_key", arguments={"key": "enter"})],
        )
        memory.add_tool_result(f"c{step}", "press_key", "enter")

    messages = memory.get_messages(compact=True)

    # Check for presence of [Prior Actions Summary]
    summary_msgs = [m for m in messages if "[Prior Actions Summary]:" in m.content]
    assert len(summary_msgs) == 1
    # Only 1 latest observation
    obs_msgs = [m for m in messages if m.role == "user" and m.content.startswith("[Desktop Observation]:")]
    assert len(obs_msgs) == 1
    assert "Step 5 state" in obs_msgs[0].content


# =========================================================================
# 8. Timeout and Retry Behavior
# =========================================================================

def test_ollama_provider_options_and_retry():
    """Verify OllamaProvider sets num_predict=512 and retries on transient failure."""
    provider = OllamaProvider(timeout_seconds=5.0)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "model": "qwen3:8b",
        "message": {
            "role": "assistant",
            "content": "Done",
            "tool_calls": [],
        },
    }

    # First attempt times out, second succeeds
    with patch("requests.post", side_effect=[requests.exceptions.Timeout("Read timeout"), mock_resp]) as mock_post:
        resp = provider.chat(messages=[ChatMessage(role="user", content="hello")])
        assert resp.content == "Done"
        assert mock_post.call_count == 2

        # Verify num_predict was configured in payload options
        first_call_payload = mock_post.call_args_list[0].kwargs["json"]
        assert first_call_payload["options"]["num_predict"] == 512


# =========================================================================
# 9. Latency, State Synchronization, and Deterministic Verification Regressions
# =========================================================================

def test_verified_close_action_updates_state_to_closed():
    """1. Verified close action updates state to CLOSED."""
    from agent.state import AgentState
    state = AgentState(goal="Close Calculator")
    fact = state.record_verified_transition(
        action_fact="close_window target 'Calculator'",
        verification_fact="Target window 'Calculator' successfully closed.",
        current_state="target 'Calculator' is CLOSED",
        target="Calculator",
        state_type="CLOSED",
        verified=True,
    )
    assert fact.verified is True
    assert state.entity_states.get("calculator") == "CLOSED"
    assert state.entity_states.get("calc") == "CLOSED"


def test_agent_finishes_successfully_when_requested_state_already_verified():
    """2. Agent finishes successfully when the requested state is already verified."""
    responses = [
        LLMResponse(
            content="Closing Calculator window.",
            tool_calls=[ToolCall(id="c1", name="close_window", arguments={"window_title_or_hwnd": "Calculator"})],
        ),
        LLMResponse(
            content="Calculator is absent so cannot close.",
            tool_calls=[ToolCall(id="c2", name="finish_task", arguments={"summary": "Calculator not found", "success": False})],
        ),
    ]

    class SequenceLLM(LLMProvider):
        def __init__(self, resps):
            self.resps = list(resps)
            self.idx = 0

        def chat(self, messages, tools=None, temperature=None):
            resp = self.resps[self.idx]
            self.idx += 1
            return resp

    mock_llm = SequenceLLM(responses)
    mock_apps = MagicMock()
    mock_apps.list_windows.return_value = []
    mock_apps.get_active_window.return_value = None
    mock_apps.close_window.return_value = True

    mock_verifier = MagicMock()
    from verification.verifier import VerificationResult
    mock_verifier.verify_action.return_value = VerificationResult(
        verified=True, confidence=1.0, details="Target window 'Calculator' successfully closed."
    )

    agent = OperatorAgent(llm=mock_llm, verifier=mock_verifier, cleanup_on_finish=False)
    agent.apps = mock_apps
    state = agent.run("Close Calculator")

    assert state.status == AgentStatus.COMPLETED


def test_tool_success_plus_verifier_success_becomes_explicit_state_fact():
    """3. Tool success + verifier success becomes an explicit state fact."""
    from agent.state import AgentState
    state = AgentState(goal="Test Task")
    fact = state.record_verified_transition(
        action_fact="close_window target HWND 527542",
        verification_fact="HWND 527542 no longer exists",
        current_state="target application is closed",
        target="527542",
        state_type="CLOSED",
        verified=True,
    )
    assert len(state.verified_facts) == 1
    assert fact.action_fact == "close_window target HWND 527542"
    assert fact.verification_fact == "HWND 527542 no longer exists"
    assert fact.current_state == "target application is closed"
    assert fact.verified is True


def test_tool_success_plus_verifier_failure_does_not_produce_false_completion():
    """4. Tool success + verifier failure does NOT produce false completion."""
    from agent.state import AgentState
    state = AgentState(goal="Close Calculator")
    fact = state.record_verified_transition(
        action_fact="close_window target 'Calculator'",
        verification_fact="Target window 'Calculator' is still open.",
        current_state="target 'Calculator' is NOT closed",
        target="Calculator",
        state_type="CLOSED",
        verified=False,
    )
    assert state.entity_states.get("calculator") != "CLOSED"
    satisfied, _ = state.is_objective_satisfied()
    assert satisfied is False


def test_post_action_verified_state_included_in_next_planning_context():
    """5. Post-action verified state is included in the next planning context."""
    from agent.state import VerifiedFact
    memory = AgentMemory()
    memory.set_goal("Close Calculator")

    fact = VerifiedFact(
        action_fact="close_window target HWND 527542",
        verification_fact="HWND 527542 no longer exists",
        current_state="target 'Calculator' is CLOSED",
        target="Calculator",
        state_type="CLOSED",
        verified=True,
    )
    memory.add_verified_fact(fact)
    messages = memory.get_messages(compact=True)

    fact_msg = next((m for m in messages if "[Verified State Facts]:" in m.content), None)
    assert fact_msg is not None
    assert "ACTION FACT: close_window target HWND 527542" in fact_msg.content
    assert "VERIFICATION FACT: HWND 527542 no longer exists" in fact_msg.content
    assert "CURRENT STATE: target 'Calculator' is CLOSED" in fact_msg.content


def test_ollama_low_latency_configuration():
    """6. Ollama low-latency configuration."""
    provider = OllamaProvider(think=False)
    assert provider.think is False

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "model": "qwen3:8b",
        "message": {"role": "assistant", "content": "", "tool_calls": []},
    }

    with patch("requests.post", return_value=mock_resp) as mock_post:
        provider.chat(messages=[ChatMessage(role="user", content="Hi")])
        payload = mock_post.call_args.kwargs["json"]
        assert "think" in payload
        assert payload["think"] is False


def test_timeout_remains_configurable():
    """7. Timeout remains configurable across Settings, Provider, and Runner."""
    from app.config import Settings
    from evaluation.runner import GeneralizationBenchmark

    s = Settings(agent_timeout_seconds=45.0)
    assert s.agent_timeout_seconds == 45.0

    p = OllamaProvider(timeout_seconds=99.0)
    assert p.timeout_seconds == 99.0

    with patch("evaluation.runner.get_app_controller"):
        b = GeneralizationBenchmark(timeout_seconds=120.0)
        assert b.timeout_seconds == 120.0
        assert b.llm.timeout_seconds == 120.0


def test_task_7_deterministic_file_window_verification():
    """8. Task 7 deterministic file/window verification."""
    verifier = ActionVerifier()
    mock_apps = MagicMock()

    mock_apps.list_windows.return_value = [
        WindowInfo(hwnd=501, title="sample.txt - Notepad", rect=(0, 0, 500, 400), is_visible=True, pid=1100),
        WindowInfo(hwnd=502, title="Untitled - Notepad", rect=(0, 0, 500, 400), is_visible=True, pid=1200),
    ]
    verifier.app_controller = mock_apps

    res = verifier.verify_action(
        "open_application",
        {"app_name": "notepad", "arguments": "sample.txt"},
        {"launched": True},
    )
    assert res.verified is True
    assert "sample.txt" in res.details
    assert "501" in res.details


def test_multiple_notepad_windows_do_not_cause_false_verification():
    """9. Multiple Notepad windows do not cause false verification."""
    verifier = ActionVerifier()
    mock_apps = MagicMock()

    mock_apps.list_windows.return_value = [
        WindowInfo(hwnd=101, title="Notes - Notepad", rect=(0, 0, 500, 400), is_visible=True, pid=2000),
        WindowInfo(hwnd=102, title="Todo list - Notepad", rect=(0, 0, 500, 400), is_visible=True, pid=2001),
        WindowInfo(hwnd=103, title="Meeting minutes - Notepad", rect=(0, 0, 500, 400), is_visible=True, pid=2002),
    ]
    mock_apps.find_windows.return_value = mock_apps.list_windows.return_value
    verifier.app_controller = mock_apps

    res = verifier.verify_action(
        "open_application",
        {"app_name": "notepad", "arguments": "sample.txt"},
        {"launched": True},
    )
    assert res.verified is False
    assert "target document 'sample.txt' not found" in res.details
