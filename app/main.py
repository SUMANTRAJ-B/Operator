"""Operator CLI entrypoint."""

import argparse
import json
from pathlib import Path
import sys

# Remove script directory from sys.path to prevent shadowing stdlib 'logging'
_curr_dir = str(Path(__file__).resolve().parent)
while _curr_dir in sys.path:
    sys.path.remove(_curr_dir)

# Ensure project root is at the head of python path
_root_dir = str(Path(__file__).resolve().parent.parent)
if _root_dir not in sys.path:
    sys.path.insert(0, _root_dir)

from rich.console import Console
from rich.table import Table

from actions.registry import get_tool_registry
from app.config import get_settings
from app.logging import setup_logger

console = Console()


def print_system_info():
    """Print Operator configuration and system status."""
    settings = get_settings()
    console.print(f"[bold cyan]=== {settings.app_name} System Status ===[/bold cyan]")
    console.print(f"Environment: [green]{settings.environment}[/green]")
    console.print(f"Log Level: [yellow]{settings.log_level}[/yellow]")
    console.print(f"Fail-Safe: [bold red]{settings.fail_safe}[/bold red]")
    console.print(f"Ollama URL: [blue]{settings.ollama_base_url}[/blue] (Model: {settings.ollama_model})")


def list_tools():
    """Display registered computer-use tools in a rich table."""
    registry = get_tool_registry()
    table = Table(title="Operator Registered Tools", show_header=True, header_style="bold magenta")
    table.add_column("Tool Name", style="bold cyan", width=20)
    table.add_column("Description", style="white")
    table.add_column("Parameters", style="dim green")

    for name in sorted(registry.list_tool_names()):
        tool = registry.get_tool(name)
        props = list(tool.parameters.get("properties", {}).keys())
        table.add_row(tool.name, tool.description, ", ".join(props) if props else "none")

    console.print(table)


def main():
    parser = argparse.ArgumentParser(description="Operator - Computer-Use AI Agent")
    parser.add_argument("--info", action="store_true", help="Display system configuration")
    parser.add_argument("--tools", action="store_true", help="List registered tools")
    parser.add_argument(
        "--task",
        type=str,
        help="Run autonomous agent loop to fulfill the specified objective",
    )
    parser.add_argument(
        "--model",
        type=str,
        help="Override Ollama model name (e.g. qwen3:8b, phi4-mini:latest)",
    )
    parser.add_argument(
        "--exec",
        nargs=2,
        metavar=("TOOL_NAME", "JSON_ARGS"),
        help="Execute a registered tool directly with JSON args",
    )

    args = parser.parse_args()
    setup_logger()

    if args.task:
        from agent.core import OperatorAgent
        from providers.llm.ollama import OllamaProvider

        llm = OllamaProvider(model=args.model) if args.model else None
        agent = OperatorAgent(llm=llm)
        state = agent.run(args.task)
        sys.exit(0 if state.status == "completed" else 1)
    elif args.info:
        print_system_info()
    elif args.tools:
        list_tools()
    elif args.exec:
        tool_name, json_str = args.exec
        try:
            parsed_args = json.loads(json_str)
        except json.JSONDecodeError as err:
            console.print(f"[bold red]Failed to parse JSON arguments: {err}[/bold red]")
            sys.exit(1)

        registry = get_tool_registry()
        res = registry.execute(tool_name, parsed_args)
        console.print(res.to_dict())
    else:
        print_system_info()
        print_tools_summary = Table(box=None)
        print_tools_summary.add_column(style="dim")
        console.print(
            "\nRun with [bold cyan]--task \"<objective>\"[/bold cyan] to start the autonomous agent.\n"
            "Use [bold]--tools[/bold] to view registered tools or [bold]--info[/bold] for status.\n"
        )


if __name__ == "__main__":
    main()
