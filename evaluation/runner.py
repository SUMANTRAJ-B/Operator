"""Standalone CLI Benchmark Runner for the Operator Generalization Test Suite.

Evaluates autonomous computer use across 8 independent benchmark tasks:
1. Open Calculator
2. Open Notepad and type a different sentence
3. Open Calculator and calculate 25 + 17
4. Open File Explorer
5. Switch between two applications
6. Create a new folder on the Desktop
7. Open an existing text file
8. Safely close a target application
"""

import argparse
from datetime import datetime
import os
from pathlib import Path
import shutil
import sys
import time
from typing import Any, Callable, Dict, List, Optional

# Ensure project root is at head of sys.path
_curr_dir = str(Path(__file__).resolve().parent)
while _curr_dir in sys.path:
    sys.path.remove(_curr_dir)
_root_dir = str(Path(__file__).resolve().parent.parent)
if _root_dir not in sys.path:
    sys.path.insert(0, _root_dir)

from rich.console import Console

from actions.applications import get_app_controller
from agent.core import OperatorAgent
from agent.state import AgentStatus
from app.config import get_settings
from app.logging import get_logger, setup_logger
from evaluation.report import BenchmarkReport, TaskEvaluationResult
from providers.llm.base import LLMProvider
from providers.llm.ollama import OllamaProvider
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

logger = get_logger("evaluation.runner")
console = Console()


class GeneralizationBenchmark:
    """Benchmark orchestrator executing and measuring the 8 generalization tasks."""

    def __init__(
        self,
        model_name: Optional[str] = None,
        llm: Optional[LLMProvider] = None,
        timeout_seconds: Optional[float] = None,
    ):
        self.settings = get_settings()
        self.model_name = model_name or self.settings.ollama_model
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else self.settings.agent_timeout_seconds
        )
        self.llm = llm or OllamaProvider(model=self.model_name, timeout_seconds=self.timeout_seconds)
        self.apps = get_app_controller()
        self.report = BenchmarkReport(model=self.model_name)

    # ---------------- Fixtures and Cleanup Helpers ----------------

    def _cleanup_applications(self, app_names: List[str]) -> None:
        """Safely close target test application instances."""
        for name in app_names:
            windows = self.apps.find_windows(name)
            for win in windows:
                if win.title.strip():
                    self.apps.close_window(win.hwnd, force=False)
        time.sleep(0.5)

    def _setup_task_6_desktop_folder(self) -> str:
        """Ensure test folder on Desktop does not exist before Task 6."""
        folder_path = os.path.join(os.path.expanduser("~"), "Desktop", "OperatorTestFolder")
        if os.path.exists(folder_path):
            try:
                shutil.rmtree(folder_path)
            except Exception:
                pass
        return folder_path

    def _cleanup_task_6_desktop_folder(self) -> None:
        """Clean up test folder on Desktop after Task 6."""
        folder_path = os.path.join(os.path.expanduser("~"), "Desktop", "OperatorTestFolder")
        if os.path.exists(folder_path):
            try:
                shutil.rmtree(folder_path)
            except Exception:
                pass

    def _setup_task_7_fixture(self) -> str:
        """Create deterministic text file fixture on Desktop for Task 7."""
        data_dir = os.path.join(os.path.expanduser("~"), "Desktop", "OperatorTestData")
        os.makedirs(data_dir, exist_ok=True)
        file_path = os.path.join(data_dir, "sample.txt")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("Operator generalization test file.\n")
        return file_path

    def _cleanup_task_7_fixture(self) -> None:
        """Clean up deterministic text file fixture after Task 7."""
        data_dir = os.path.join(os.path.expanduser("~"), "Desktop", "OperatorTestData")
        if os.path.exists(data_dir):
            try:
                shutil.rmtree(data_dir)
            except Exception:
                pass

    # ---------------- Task Definitions ----------------

    def get_task_definition(self, task_num: int) -> Dict[str, Any]:
        """Return goal, setup, and programmatic verifier for the task number."""
        tasks = {
            1: {
                "name": "Open Calculator",
                "goal": "Open Calculator",
                "setup": lambda: self._cleanup_applications(["calc", "calculator"]),
                "verifier": lambda: verify_calculator_window(self.apps),
                "teardown": lambda: self._cleanup_applications(["calc", "calculator"]),
            },
            2: {
                "name": "Open Notepad and type sentence",
                "goal": "Open Notepad and type The quick brown fox jumps over the lazy dog",
                "setup": lambda: self._cleanup_applications(["notepad"]),
                "verifier": lambda: verify_notepad_content(
                    "The quick brown fox jumps over the lazy dog", self.apps
                ),
                "teardown": lambda: self._cleanup_applications(["notepad"]),
            },
            3: {
                "name": "Calculator: Calculate 25 + 17",
                "goal": "Open Calculator and calculate 25 + 17",
                "setup": lambda: self._cleanup_applications(["calc", "calculator"]),
                "verifier": lambda: verify_calculator_result("42", self.apps),
                "teardown": lambda: self._cleanup_applications(["calc", "calculator"]),
            },
            4: {
                "name": "Open File Explorer",
                "goal": "Open File Explorer",
                "setup": lambda: None,
                "verifier": lambda: verify_explorer_window(self.apps),
                "teardown": lambda: None,
            },
            5: {
                "name": "Switch between two applications",
                "goal": "Open Notepad, open Calculator, then switch back to Notepad",
                "setup": lambda: self._cleanup_applications(["notepad", "calc", "calculator"]),
                "verifier": lambda: (
                    verify_calculator_window(self.apps)
                    and verify_foreground_window("Notepad", self.apps)
                ),
                "teardown": lambda: self._cleanup_applications(["notepad", "calc", "calculator"]),
            },
            6: {
                "name": "Create a new folder on Desktop",
                "goal": "Create a new folder named 'OperatorTestFolder' on the Desktop",
                "setup": lambda: self._setup_task_6_desktop_folder(),
                "verifier": lambda: verify_desktop_folder("OperatorTestFolder"),
                "teardown": lambda: self._cleanup_task_6_desktop_folder(),
            },
            7: {
                "name": "Open existing text file",
                "goal": "Open the existing sample.txt file.",
                "setup": lambda: self._setup_task_7_fixture(),
                "verifier": lambda: verify_file_opened(
                    "sample.txt", "Operator generalization test file.", self.apps
                ),
                "teardown": lambda: (
                    self._cleanup_applications(["notepad"]),
                    self._cleanup_task_7_fixture(),
                ),
            },
            8: {
                "name": "Safely close a target application",
                "goal": "Close Calculator",
                "setup": lambda: (
                    self._cleanup_applications(["calc", "calculator"]),
                    self.apps.open_application("calc", wait_for_window=True),
                    time.sleep(1.0),
                ),
                "verifier": lambda: (
                    verify_window_closed("Calculator", self.apps)
                    and any("antigravity" in w.title.lower() for w in self.apps.list_windows(visible_only=True))
                ),
                "teardown": lambda: self._cleanup_applications(["calc", "calculator"]),
            },
        }

        if task_num not in tasks:
            raise ValueError(f"Invalid task number {task_num}. Valid tasks are 1 to 8.")
        return tasks[task_num]

    # ---------------- Execution Engine ----------------

    def run_task(self, task_num: int) -> TaskEvaluationResult:
        """Execute a single benchmark task autonomously and collect metrics."""
        task_info = self.get_task_definition(task_num)
        name = task_info["name"]
        goal = task_info["goal"]

        console.print(f"\n[bold yellow]>>> Starting Task {task_num}: {name}[/bold yellow]")
        console.print(f"[dim]Goal: {goal}[/dim]\n")

        # Run task setup
        if task_info.get("setup"):
            try:
                task_info["setup"]()
            except Exception as err:
                logger.warning(f"Task {task_num} setup error: {err}")

        start_dt = datetime.now()
        start_time = time.time()

        verifier_fn = task_info.get("verifier")
        agent = OperatorAgent(llm=self.llm, timeout_seconds=self.timeout_seconds)
        try:
            agent_state = agent.run(goal, pre_cleanup_verifier=verifier_fn)
        except Exception as err:
            logger.exception(f"Exception during task {task_num} execution: {err}")
            from agent.state import AgentState
            agent_state = AgentState(goal=goal)
            agent_state.mark_failed(str(err))

        duration = time.time() - start_time
        end_dt = datetime.now()

        # Run programmatic verification against live OS state
        time.sleep(0.3)
        prog_verified = agent_state.objective_verified
        if not prog_verified and verifier_fn:
            try:
                prog_verified = bool(verifier_fn())
            except Exception as err:
                logger.error(f"Programmatic verifier failed with exception: {err}")
                prog_verified = False

        # Extract tool calls and trajectory from execution history
        tool_calls = []
        observations = []
        verifications = []
        trajectory = []

        for step in agent_state.history:
            if step.action:
                tool_calls.append(step.action)
            if step.observation:
                observations.append(step.observation)
            if step.verification:
                verifications.append(step.verification)
            trajectory.append(
                {
                    "step": step.step_number,
                    "thought": step.thought,
                    "action": step.action,
                    "result": step.result,
                    "verified": step.verification.get("verified", False) if step.verification else False,
                }
            )

        success = (agent_state.status == AgentStatus.COMPLETED) and prog_verified

        result = TaskEvaluationResult(
            task_id=task_num,
            task_name=name,
            goal=goal,
            success=success,
            programmatic_verification=prog_verified,
            duration_seconds=round(duration, 2),
            start_time=start_dt.isoformat(),
            end_time=end_dt.isoformat(),
            model=self.model_name,
            step_count=agent_state.current_step,
            tool_calls=tool_calls,
            observations=observations,
            verification_results=verifications,
            safety_interventions=0,
            recovery_attempts=0,
            final_status=agent_state.status.value,
            failure_reason=agent_state.error if not success else None,
            trajectory=trajectory,
        )

        self.report.results.append(result)

        # Run task teardown
        if task_info.get("teardown"):
            try:
                task_info["teardown"]()
            except Exception as err:
                logger.warning(f"Task {task_num} teardown error: {err}")

        console.print(
            f"[bold {'green' if success else 'red'}]>>> Task {task_num} finished: "
            f"{'SUCCESS' if success else 'FAILED'} (Time: {duration:.1f}s, Programmatic Verif: {prog_verified})[/bold {'green' if success else 'red'}]\n"
        )
        return result

    def run_all(self) -> BenchmarkReport:
        """Execute all 8 generalization tasks sequentially."""
        for t in range(1, 9):
            self.run_task(t)
        return self.report


def main():
    parser = argparse.ArgumentParser(description="Operator Generalization Benchmark Runner")
    parser.add_argument("--task", type=int, choices=range(1, 9), help="Run a specific task (1-8)")
    parser.add_argument("--all", action="store_true", help="Run all 8 benchmark tasks")
    parser.add_argument("--model", type=str, help="Override Ollama model (default: from config)")
    parser.add_argument("--timeout", type=float, help="Override agent task timeout in seconds")
    parser.add_argument(
        "--output",
        type=str,
        default="reports/generalization_results.json",
        help="Path to output results JSON",
    )

    args = parser.parse_args()
    setup_logger()

    if not args.task and not args.all:
        console.print("[yellow]Please specify --task [1-8] or --all to run the benchmark.[/yellow]")
        sys.exit(1)

    runner = GeneralizationBenchmark(model_name=args.model, timeout_seconds=args.timeout)

    if args.task:
        runner.run_task(args.task)
    elif args.all:
        runner.run_all()

    runner.report.print_summary()
    saved_path = runner.report.save_json(args.output)
    console.print(f"[bold cyan]Structured metrics saved to: {saved_path}[/bold cyan]\n")


if __name__ == "__main__":
    main()
