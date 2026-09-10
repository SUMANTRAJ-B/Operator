"""End-to-end autonomous agent loop tests (simulated and live)."""

import time
from unittest.mock import MagicMock
import pytest

from actions.applications import get_app_controller
from agent.core import OperatorAgent
from agent.state import AgentStatus
from providers.llm.base import LLMProvider, LLMResponse, ToolCall
from providers.llm.ollama import OllamaProvider


class MockSequenceLLM(LLMProvider):
    """Deterministic LLM Provider returning an autonomous sequence of tool calls."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.call_count = 0

    def chat(self, messages, tools=None, temperature=None):
        if self.call_count < len(self.responses):
            resp = self.responses[self.call_count]
            self.call_count += 1
            return resp
        return LLMResponse(content="No more mock responses")


def test_agent_loop_simulated():
    """Verify the full Observe -> Plan -> Act -> Observe -> Verify loop works end-to-end."""
    apps = get_app_controller()

    # Sequence of autonomous decisions by the agent
    mock_responses = [
        LLMResponse(
            content="I will start by opening Notepad.",
            thinking="Step 1: Need to launch Notepad.",
            tool_calls=[ToolCall(id="c1", name="open_application", arguments={"app_name": "notepad"})],
        ),
        LLMResponse(
            content="Now I will activate the Notepad window to ensure it is focused.",
            thinking="Step 2: Activate window.",
            tool_calls=[ToolCall(id="c2", name="activate_window", arguments={"window_title_or_hwnd": "Notepad"})],
        ),
        LLMResponse(
            content="Now I will type the requested text.",
            thinking="Step 3: Type Hello Operator into focused Notepad.",
            tool_calls=[ToolCall(id="c3", name="type_text", arguments={"text": "Hello Operator"})],
        ),
        LLMResponse(
            content="The text has been entered. I will conclude the task.",
            thinking="Step 4: Conclude task.",
            tool_calls=[ToolCall(id="c4", name="finish_task", arguments={"summary": "Opened Notepad and typed Hello Operator", "success": True})],
        ),
    ]

    mock_llm = MockSequenceLLM(mock_responses)
    agent = OperatorAgent(llm=mock_llm)

    try:
        state = agent.run("Open Notepad and type Hello Operator")
        assert state.status == AgentStatus.COMPLETED
        assert state.is_terminal is True
        assert len(state.history) >= 3
        assert "Hello Operator" in (state.final_summary or "")
    finally:
        # Clean up any opened notepad window safely
        for win in apps.find_windows("notepad"):
            apps.close_window(win.hwnd, force=True)


@pytest.mark.live
def test_agent_e2e_live_ollama():
    """Live end-to-end test with local Ollama qwen3:8b deciding and executing actions."""
    apps = get_app_controller()
    llm = OllamaProvider(model="qwen3:8b", timeout_seconds=180.0)
    agent = OperatorAgent(llm=llm)

    try:
        state = agent.run("Open Notepad and type Hello Operator")
        assert state.status == AgentStatus.COMPLETED
        assert state.is_terminal is True

        # Verify that Notepad was opened
        notepad_windows = apps.find_windows("notepad")
        assert len(notepad_windows) > 0, "Expected at least one Notepad window to be open"
    finally:
        # Clean up any opened notepad window safely
        time.sleep(0.5)
        for win in apps.find_windows("notepad"):
            apps.close_window(win.hwnd, force=True)
