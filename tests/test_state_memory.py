"""Unit tests for agent state and working memory."""

from agent.memory import AgentMemory
from agent.state import AgentState, AgentStatus, StepExecution
from providers.llm.base import ToolCall


def test_agent_state_lifecycle():
    state = AgentState(goal="Test Task")
    assert state.status == AgentStatus.IDLE
    assert state.is_terminal is False

    state.add_execution(
        StepExecution(
            step_number=1,
            phase="ACT",
            action={"tool": "open_application", "arguments": {"app_name": "notepad"}},
        )
    )
    assert len(state.history) == 1

    state.mark_completed(summary="Task finished cleanly")
    assert state.status == AgentStatus.COMPLETED
    assert state.is_terminal is True
    assert state.final_summary == "Task finished cleanly"
    assert state.elapsed_seconds >= 0.0


def test_agent_memory_trajectory():
    memory = AgentMemory()
    assert len(memory.get_messages()) == 1  # System prompt

    memory.set_goal("Open Notepad")
    assert len(memory.get_messages()) == 2
    assert memory.get_messages()[1].role == "user"

    memory.add_observation("Screen size is 1920x1080")
    assert len(memory.get_messages()) == 3

    tc = ToolCall(id="c1", name="open_application", arguments={"app_name": "notepad"})
    memory.add_assistant_response(content="", tool_calls=[tc])
    assert len(memory.get_messages()) == 4

    memory.add_tool_result(tool_call_id="c1", tool_name="open_application", result_output="Launched notepad")
    assert len(memory.get_messages()) == 5
    assert memory.get_messages()[4].role == "tool"
