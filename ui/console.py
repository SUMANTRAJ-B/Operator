"""Developer UI dashboard showing live agent state, plan, actions, and verifications."""

from typing import Any, Dict, Optional
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agent.planner import StepStatus, TaskPlan
from agent.state import AgentStatus

console = Console(highlight=False)


def _clean_str(text: Any) -> str:
    """Sanitize strings by replacing characters unencodable in the Windows console."""
    if not isinstance(text, str):
        text = str(text)
    return text.encode("ascii", errors="replace").decode("ascii")


class DeveloperUI:
    """Renders structured status displays for the Operator execution loop."""

    def __init__(self):
        self.console = console

    def display_header(self, goal: str, model: str, task_id: str) -> None:
        """Display startup banner and task goal."""
        content = (
            f"[bold cyan]OPERATOR AUTONOMOUS COMPUTER-USE AGENT[/bold cyan]\n"
            f"[dim]Task ID:[/dim] [white]{task_id}[/white] | [dim]Model:[/dim] [yellow]{model}[/yellow]\n"
            f"[bold]Goal:[/bold] [italic white]{_clean_str(goal)}[/italic white]"
        )
        self.console.print(Panel(content, border_style="cyan"))

    def display_plan(self, plan: TaskPlan) -> None:
        """Display the structured milestone plan table."""
        table = Table(title="Execution Plan", show_header=True, header_style="bold magenta")
        table.add_column("#", width=3, justify="right")
        table.add_column("Milestone Description", style="white")
        table.add_column("Status", width=14)

        status_styles = {
            StepStatus.PENDING: "[dim]PENDING[/dim]",
            StepStatus.IN_PROGRESS: "[bold yellow]IN PROGRESS[/bold yellow]",
            StepStatus.COMPLETED: "[bold green]COMPLETED[/bold green]",
            StepStatus.FAILED: "[bold red]FAILED[/bold red]",
            StepStatus.SKIPPED: "[dim yellow]SKIPPED[/dim yellow]",
        }

        for step in plan.steps:
            status_text = status_styles.get(step.status, str(step.status))
            table.add_row(str(step.step_id), _clean_str(step.description), status_text)

        self.console.print(table)

    def display_phase(self, phase: str, details: str = "") -> None:
        """Display loop phase transition (OBSERVE, PLAN, ACT, VERIFY)."""
        phase_colors = {
            "OBSERVE": "blue",
            "PLAN": "magenta",
            "ACT": "yellow",
            "VERIFY": "cyan",
            "RECOVER": "red",
        }
        color = phase_colors.get(phase.upper(), "white")
        prefix = f"[bold {color}][{phase.upper()}][/bold {color}]"
        self.console.print(f"{prefix} {_clean_str(details)}")

    def display_action(self, tool_name: str, arguments: Dict[str, Any], thought: Optional[str] = None) -> None:
        """Display tool dispatch with arguments and model rationale."""
        if thought:
            self.console.print(f"  [dim italic]Thought: {thought.strip()}[/dim italic]")
        arg_str = ", ".join(f"{k}={v!r}" for k, v in arguments.items()) if arguments else "none"
        self.console.print(f"  [bold yellow]-> Calling tool:[/bold yellow] [bold green]{tool_name}[/bold green]({arg_str})")

    def display_observation(self, observation: str) -> None:
        """Display desktop perception outcome."""
        self.console.print(f"  [dim]Perception:[/dim] {observation}")

    def display_verification(self, verified: bool, details: str) -> None:
        """Display result of post-action verification."""
        if verified:
            self.console.print(f"  [bold green][VERIFIED][/bold green] {details}")
        else:
            self.console.print(f"  [bold red][UNVERIFIED][/bold red] {details}")

    def display_completion(self, summary: str, elapsed_seconds: float) -> None:
        """Display final task success summary."""
        content = (
            f"[bold green]TASK COMPLETED SUCCESSFULLY[/bold green]\n"
            f"[bold]Summary:[/bold] {summary}\n"
            f"[dim]Total Duration: {elapsed_seconds}s[/dim]"
        )
        self.console.print("\n", Panel(content, border_style="green"), "\n")

    def display_failure(self, error: str, elapsed_seconds: float) -> None:
        """Display final task failure report."""
        content = (
            f"[bold red]TASK FAILED[/bold red]\n"
            f"[bold]Error:[/bold] {error}\n"
            f"[dim]Total Duration: {elapsed_seconds}s[/dim]"
        )
        self.console.print("\n", Panel(content, border_style="red"), "\n")


_ui_instance: Optional[DeveloperUI] = None


def get_developer_ui() -> DeveloperUI:
    """Retrieve shared DeveloperUI singleton."""
    global _ui_instance
    if _ui_instance is None:
        _ui_instance = DeveloperUI()
    return _ui_instance
