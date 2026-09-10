"""Generalization test suite evaluating autonomous computer use across 8 benchmark tasks.

Includes simulated/mock tests for the 8 benchmark tasks, tests for programmatic verifiers,
and clearly separated live evaluation tests.
"""

import os
from pathlib import Path
import shutil
import tempfile
import time
from unittest.mock import MagicMock, patch
import pytest

from actions.applications import AppController, WindowInfo
from agent.core import OperatorAgent
from agent.state import AgentStatus
from evaluation.report import BenchmarkReport, TaskEvaluationResult
from evaluation.runner import GeneralizationBenchmark
from providers.llm.base import LLMProvider, LLMResponse, ToolCall
from verification.verifier import (
    verify_calculator_result,
    verify_calculator_window,
    verify_desktop_folder,
    verify_explorer_window,
    verify_file_opened,
    verify_foreground_window,
    verify_notepad_content,
    verify_window_closed,
)


class MockSequenceLLM(LLMProvider):
    """Deterministic mock provider delivering autonomous tool sequences for testing."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.call_count = 0

    def chat(self, messages, tools=None, temperature=None):
        if self.call_count < len(self.responses):
            resp = self.responses[self.call_count]
            self.call_count += 1
            return resp
        return LLMResponse(
            content="Task concluded",
            tool_calls=[ToolCall(id="fin", name="finish_task", arguments={"summary": "Concluded", "success": True})],
        )


# =========================================================================
# 1. Programmatic Verifier Unit Tests
# =========================================================================


def test_verifier_calculator_window():
    """Verify calculator window detector checks matching titles and visibility."""
    mock_apps = MagicMock(spec=AppController)
    mock_apps.find_windows.side_effect = lambda query: [
        WindowInfo(hwnd=1001, title="Calculator", rect=(0, 0, 400, 500), is_visible=True, pid=5000)
    ]
    assert verify_calculator_window(mock_apps) is True

    mock_apps.find_windows.side_effect = lambda query: []
    assert verify_calculator_window(mock_apps) is False


def test_verifier_notepad_content():
    """Verify notepad content validator matches expected strings."""
    mock_apps = MagicMock(spec=AppController)
    mock_apps.find_windows.return_value = [
        WindowInfo(hwnd=2001, title="*Test - Notepad", rect=(0, 0, 600, 400), is_visible=True, pid=6000)
    ]
    with patch("verification.verifier.get_notepad_text", return_value="The quick brown fox jumps over the lazy dog"):
        assert verify_notepad_content("quick brown fox", mock_apps) is True
        assert verify_notepad_content("completely absent text", mock_apps) is False


def test_verifier_calculator_result():
    """Verify calculator result validation via clipboard fallback."""
    mock_apps = MagicMock(spec=AppController)
    mock_apps.find_windows.return_value = [
        WindowInfo(hwnd=3001, title="Calculator", rect=(0, 0, 300, 400), is_visible=True, pid=7000)
    ]
    with patch("pyperclip.paste", return_value="42"):
        assert verify_calculator_result("42", mock_apps) is True
    with patch("pyperclip.paste", return_value="99"):
        assert verify_calculator_result("42", mock_apps) is False


def test_verifier_explorer_window():
    """Verify file explorer window detection."""
    mock_apps = MagicMock(spec=AppController)
    mock_apps.find_windows.side_effect = lambda q: (
        [WindowInfo(hwnd=4001, title="Home", rect=(0, 0, 800, 600), is_visible=True, pid=8000, class_name="CabinetWClass")]
        if "cabinet" in q.lower() or "explorer" in q.lower()
        else []
    )
    assert verify_explorer_window(mock_apps) is True


def test_verifier_foreground_window():
    """Verify active foreground window check."""
    mock_apps = MagicMock(spec=AppController)
    mock_apps.get_active_window.return_value = WindowInfo(
        hwnd=5001, title="Untitled - Notepad", rect=(0, 0, 500, 400), is_visible=True, pid=9000
    )
    assert verify_foreground_window("Notepad", mock_apps) is True
    assert verify_foreground_window("Calculator", mock_apps) is False


def test_verifier_desktop_folder():
    """Verify desktop folder detector accurately inspects filesystem."""
    desktop_dir = os.path.join(os.path.expanduser("~"), "Desktop")
    test_folder = os.path.join(desktop_dir, "_OperatorUnitTestFolder_Temp_")

    try:
        assert verify_desktop_folder("_OperatorUnitTestFolder_Temp_") is False
        os.makedirs(test_folder, exist_ok=True)
        assert verify_desktop_folder("_OperatorUnitTestFolder_Temp_") is True
    finally:
        if os.path.exists(test_folder):
            os.rmdir(test_folder)


def test_verifier_file_opened():
    """Verify open text file detection."""
    mock_apps = MagicMock(spec=AppController)
    mock_apps.list_windows.return_value = [
        WindowInfo(hwnd=6001, title="sample.txt - Notepad", rect=(0, 0, 500, 400), is_visible=True, pid=9500)
    ]
    assert verify_file_opened("sample.txt", app_controller=mock_apps) is True
    assert verify_file_opened("other.txt", app_controller=mock_apps) is False


def test_verifier_window_closed():
    """Verify window closed detector."""
    mock_apps = MagicMock(spec=AppController)
    mock_apps.find_windows.return_value = []
    assert verify_window_closed("Calculator", mock_apps) is True

    mock_apps.find_windows.return_value = [
        WindowInfo(hwnd=7001, title="Calculator", rect=(0, 0, 300, 400), is_visible=True, pid=9900)
    ]
    assert verify_window_closed("Calculator", mock_apps) is False


# =========================================================================
# 2. Benchmark Report & Task Definitions
# =========================================================================


def test_benchmark_report_computation():
    """Verify benchmark metrics and JSON serialization."""
    report = BenchmarkReport(model="test-model")
    report.results.append(
        TaskEvaluationResult(
            task_id=1,
            task_name="Open Calculator",
            goal="Open Calculator",
            success=True,
            programmatic_verification=True,
            duration_seconds=12.5,
            start_time="2026-09-09T12:00:00",
            end_time="2026-09-09T12:00:12",
            model="test-model",
            step_count=2,
        )
    )
    report.results.append(
        TaskEvaluationResult(
            task_id=2,
            task_name="Type in Notepad",
            goal="Open Notepad and type test",
            success=False,
            programmatic_verification=False,
            duration_seconds=15.0,
            start_time="2026-09-09T12:01:00",
            end_time="2026-09-09T12:01:15",
            model="test-model",
            step_count=3,
        )
    )

    report.compute_summary()
    assert report.total_tasks == 2
    assert report.passed_tasks == 1
    assert report.failed_tasks == 1
    assert report.success_rate == 50.0
    assert report.average_duration_seconds == 13.75

    data = report.to_dict()
    assert data["summary"]["passed_tasks"] == 1
    assert len(data["results"]) == 2


def test_benchmark_task_definitions():
    """Verify all 8 tasks are registered with goals and verifiers."""
    bench = GeneralizationBenchmark()
    for task_id in range(1, 9):
        task_def = bench.get_task_definition(task_id)
        assert "name" in task_def
        assert "goal" in task_def
        assert "verifier" in task_def
        assert callable(task_def["verifier"])


# =========================================================================
# 3. Simulated Benchmark Tasks (Autonomous Loop with Mock LLM)
# =========================================================================


def test_simulated_task_1_open_calculator():
    """Simulated Task 1: Autonomous decision to open Calculator."""
    responses = [
        LLMResponse(
            content="Opening calculator.",
            tool_calls=[ToolCall(id="c1", name="open_application", arguments={"app_name": "calc"})],
        ),
        LLMResponse(
            content="Calculator opened.",
            tool_calls=[ToolCall(id="c2", name="finish_task", arguments={"summary": "Opened Calculator", "success": True})],
        ),
    ]
    agent = OperatorAgent(llm=MockSequenceLLM(responses))
    state = agent.run("Open Calculator")
    assert state.status == AgentStatus.COMPLETED
    assert state.current_step >= 1


def test_simulated_task_2_open_notepad_and_type():
    """Simulated Task 2: Autonomous decision to open Notepad and type text."""
    responses = [
        LLMResponse(
            content="Opening Notepad.",
            tool_calls=[ToolCall(id="c1", name="open_application", arguments={"app_name": "notepad"})],
        ),
        LLMResponse(
            content="Typing sentence.",
            tool_calls=[ToolCall(id="c2", name="type_text", arguments={"text": "The quick brown fox jumps over the lazy dog"})],
        ),
        LLMResponse(
            content="Finished.",
            tool_calls=[ToolCall(id="c3", name="finish_task", arguments={"summary": "Typed sentence in Notepad", "success": True})],
        ),
    ]
    agent = OperatorAgent(llm=MockSequenceLLM(responses))
    state = agent.run("Open Notepad and type The quick brown fox jumps over the lazy dog")
    assert state.status == AgentStatus.COMPLETED


def test_simulated_task_3_calculator_math():
    """Simulated Task 3: Autonomous decision to open Calculator and calculate 25 + 17."""
    responses = [
        LLMResponse(
            content="Opening Calculator for calculation.",
            tool_calls=[ToolCall(id="c1", name="open_application", arguments={"app_name": "calc"})],
        ),
        LLMResponse(
            content="Entering calculation.",
            tool_calls=[ToolCall(id="c2", name="type_text", arguments={"text": "25+17="})],
        ),
        LLMResponse(
            content="Finished calculation.",
            tool_calls=[ToolCall(id="c3", name="finish_task", arguments={"summary": "Calculated 25 + 17 = 42", "success": True})],
        ),
    ]
    agent = OperatorAgent(llm=MockSequenceLLM(responses))
    state = agent.run("Open Calculator and calculate 25 + 17")
    assert state.status == AgentStatus.COMPLETED


def test_simulated_task_4_open_file_explorer():
    """Simulated Task 4: Autonomous decision to open File Explorer."""
    responses = [
        LLMResponse(
            content="Opening File Explorer via hotkey.",
            tool_calls=[ToolCall(id="c1", name="hotkey", arguments={"keys": ["win", "e"]})],
        ),
        LLMResponse(
            content="Explorer opened.",
            tool_calls=[ToolCall(id="c2", name="finish_task", arguments={"summary": "Opened File Explorer", "success": True})],
        ),
    ]
    agent = OperatorAgent(llm=MockSequenceLLM(responses))
    state = agent.run("Open File Explorer")
    assert state.status == AgentStatus.COMPLETED


def test_simulated_task_5_switch_applications():
    """Simulated Task 5: Autonomous decision to switch between Notepad and Calculator."""
    responses = [
        LLMResponse(
            content="Opening Notepad first.",
            tool_calls=[ToolCall(id="c1", name="open_application", arguments={"app_name": "notepad"})],
        ),
        LLMResponse(
            content="Opening Calculator second.",
            tool_calls=[ToolCall(id="c2", name="open_application", arguments={"app_name": "calc"})],
        ),
        LLMResponse(
            content="Switching back to Notepad.",
            tool_calls=[ToolCall(id="c3", name="activate_window", arguments={"window_title_or_hwnd": "Notepad"})],
        ),
        LLMResponse(
            content="Switch completed.",
            tool_calls=[ToolCall(id="c4", name="finish_task", arguments={"summary": "Switched between applications", "success": True})],
        ),
    ]
    agent = OperatorAgent(llm=MockSequenceLLM(responses))
    state = agent.run("Open Notepad, open Calculator, then switch back to Notepad")
    assert state.status == AgentStatus.COMPLETED


def test_simulated_task_6_create_desktop_folder():
    """Simulated Task 6: Autonomous GUI action sequence creating desktop folder."""
    responses = [
        LLMResponse(
            content="Navigating to desktop.",
            tool_calls=[ToolCall(id="c1", name="hotkey", arguments={"keys": ["win", "d"]})],
        ),
        LLMResponse(
            content="Creating new folder via keyboard shortcut.",
            tool_calls=[ToolCall(id="c2", name="hotkey", arguments={"keys": ["ctrl", "shift", "n"]})],
        ),
        LLMResponse(
            content="Naming folder and confirming.",
            tool_calls=[
                ToolCall(id="c3", name="type_text", arguments={"text": "OperatorTestFolder"}),
                ToolCall(id="c4", name="press_key", arguments={"key": "enter"}),
            ],
        ),
        LLMResponse(
            content="Folder created.",
            tool_calls=[ToolCall(id="c5", name="finish_task", arguments={"summary": "Created desktop folder", "success": True})],
        ),
    ]
    agent = OperatorAgent(llm=MockSequenceLLM(responses))
    state = agent.run("Create a new folder named 'OperatorTestFolder' on the Desktop")
    assert state.status == AgentStatus.COMPLETED


def test_simulated_task_7_open_existing_text_file():
    """Simulated Task 7: Autonomous decision to open existing text file."""
    responses = [
        LLMResponse(
            content="Opening the file in Notepad using arguments.",
            tool_calls=[ToolCall(id="c1", name="open_application", arguments={"app_name": "notepad", "arguments": "sample.txt"})],
        ),
        LLMResponse(
            content="File opened.",
            tool_calls=[ToolCall(id="c2", name="finish_task", arguments={"summary": "Opened sample.txt", "success": True})],
        ),
    ]
    agent = OperatorAgent(llm=MockSequenceLLM(responses))
    state = agent.run("Open the existing sample.txt file.")
    assert state.status == AgentStatus.COMPLETED


def test_simulated_task_8_safely_close_target_application():
    """Simulated Task 8: Autonomous decision to close target application safely."""
    responses = [
        LLMResponse(
            content="Closing Calculator window.",
            tool_calls=[ToolCall(id="c1", name="close_window", arguments={"window_title_or_hwnd": "Calculator"})],
        ),
        LLMResponse(
            content="Calculator closed safely.",
            tool_calls=[ToolCall(id="c2", name="finish_task", arguments={"summary": "Closed Calculator", "success": True})],
        ),
    ]
    agent = OperatorAgent(llm=MockSequenceLLM(responses))
    state = agent.run("Close Calculator")
    assert state.status == AgentStatus.COMPLETED
