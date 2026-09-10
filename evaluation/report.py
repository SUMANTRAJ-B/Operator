"""Benchmark reporting and metrics collection for the Operator generalization test suite."""

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.table import Table

console = Console()


@dataclass
class TaskEvaluationResult:
    """Detailed metrics and execution outcome for a single generalization benchmark task."""

    task_id: int
    task_name: str
    goal: str
    success: bool
    programmatic_verification: bool
    duration_seconds: float
    start_time: str
    end_time: str
    model: str
    step_count: int = 0
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    observations: List[str] = field(default_factory=list)
    verification_results: List[Dict[str, Any]] = field(default_factory=list)
    safety_interventions: int = 0
    recovery_attempts: int = 0
    final_status: str = "unknown"
    failure_reason: Optional[str] = None
    trajectory: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BenchmarkReport:
    """Aggregated benchmark report across multiple generalization tasks."""

    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    model: str = "qwen3:8b"
    total_tasks: int = 0
    passed_tasks: int = 0
    failed_tasks: int = 0
    success_rate: float = 0.0
    average_duration_seconds: float = 0.0
    results: List[TaskEvaluationResult] = field(default_factory=list)

    def compute_summary(self) -> None:
        """Compute summary statistics across all task results."""
        self.total_tasks = len(self.results)
        self.passed_tasks = sum(1 for r in self.results if r.success and r.programmatic_verification)
        self.failed_tasks = self.total_tasks - self.passed_tasks
        self.success_rate = (self.passed_tasks / self.total_tasks * 100.0) if self.total_tasks > 0 else 0.0
        total_time = sum(r.duration_seconds for r in self.results)
        self.average_duration_seconds = (total_time / self.total_tasks) if self.total_tasks > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        self.compute_summary()
        return {
            "timestamp": self.timestamp,
            "model": self.model,
            "summary": {
                "total_tasks": self.total_tasks,
                "passed_tasks": self.passed_tasks,
                "failed_tasks": self.failed_tasks,
                "success_rate_percent": round(self.success_rate, 1),
                "average_duration_seconds": round(self.average_duration_seconds, 2),
            },
            "results": [r.to_dict() for r in self.results],
        }

    def save_json(self, output_path: str = "reports/generalization_results.json") -> str:
        """Save report to JSON file on disk."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
        return str(path.resolve())

    def print_summary(self) -> None:
        """Render a formatted Rich table summary to the console."""
        self.compute_summary()
        table = Table(
            title=f"Operator Generalization Benchmark Report ({self.model})",
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("#", style="dim", width=4)
        table.add_column("Task Name", style="bold white", width=36)
        table.add_column("Agent Result", width=14)
        table.add_column("Programmatic Verif", width=18)
        table.add_column("Steps", justify="right", width=7)
        table.add_column("Tools Called", style="dim", width=28)
        table.add_column("Time (s)", justify="right", width=9)

        for r in self.results:
            agent_status = "[green]COMPLETED[/green]" if r.success else "[red]FAILED[/red]"
            verif_status = "[green]VERIFIED[/green]" if r.programmatic_verification else "[red]REJECTED[/red]"
            tools_used = ", ".join(t.get("tool", "") for t in r.tool_calls[:3])
            if len(r.tool_calls) > 3:
                tools_used += f" (+{len(r.tool_calls) - 3})"

            table.add_row(
                str(r.task_id),
                r.task_name,
                agent_status,
                verif_status,
                str(r.step_count),
                tools_used or "none",
                f"{r.duration_seconds:.1f}",
            )

        console.print()
        console.print(table)
        console.print(
            f"Overall: [bold green]{self.passed_tasks}/{self.total_tasks} passed[/bold green] "
            f"({self.success_rate:.1f}%) | Avg Duration: {self.average_duration_seconds:.1f}s"
        )
        console.print()
