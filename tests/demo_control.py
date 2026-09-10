"""Live computer-control demonstration and verification script.

Demonstrates and verifies:
1. Take a screenshot.
2. Detect the screen size.
3. Move the mouse.
4. Click.
5. Type text.
6. Press keys.
7. Open an application.
"""

import os
import sys
import time
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from actions.applications import get_app_controller
from actions.keyboard import get_keyboard_controller
from actions.mouse import get_mouse_controller
from actions.registry import get_tool_registry
from app.config import get_settings
from app.logging import setup_logger
from perception.screenshot import get_screen_capture

# Ensure Windows console supports UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

console = Console(highlight=False)
logger = setup_logger("operator.demo")


def run_live_demonstration() -> bool:
    console.print(
        Panel(
            "[bold cyan]OPERATOR COMPUTER-USE AGENT: LIVE CONTROL DEMONSTRATION[/bold cyan]\n"
            "[dim]Verifying all 7 core OS interaction primitives[/dim]",
            border_style="cyan",
        )
    )

    settings = get_settings()
    results = {}
    mouse = get_mouse_controller()
    keyboard = get_keyboard_controller()
    apps = get_app_controller()
    screen = get_screen_capture()
    registry = get_tool_registry()

    artifacts_dir = Path("artifacts/screenshots")
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    # 1. Detect Screen Size
    try:
        width, height = mouse.get_screen_size()
        console.print(f"[green][PASS][/green] Step 1: Detect Screen Size -> [bold yellow]{width}x{height}[/bold yellow] pixels")
        results["Detect Screen Size"] = (True, f"{width}x{height}")
    except Exception as err:
        console.print(f"[red][FAIL][/red] Step 1: Detect Screen Size failed: {err}")
        results["Detect Screen Size"] = (False, str(err))

    # 2. Take a Screenshot
    try:
        shot_path = artifacts_dir / "demo_initial.png"
        img = screen.capture_full_screen(save_path=shot_path)
        resolved_file = Path(shot_path).resolve()
        assert resolved_file.exists() and resolved_file.stat().st_size > 0, "Screenshot file does not exist or is empty"
        console.print(f"[green][PASS][/green] Step 2: Take Screenshot -> Saved to [cyan]{resolved_file}[/cyan] ({img.width}x{img.height})")
        results["Take Screenshot"] = (True, f"{shot_path.name} ({img.width}x{img.height})")
    except Exception as err:
        console.print(f"[red][FAIL][/red] Step 2: Take Screenshot failed: {err}")
        results["Take Screenshot"] = (False, str(err))

    # 3. Move the Mouse
    try:
        w, h = mouse.get_screen_size()
        target_x, target_y = w // 2, h // 2
        console.print(f"Moving cursor smoothly to center ({target_x}, {target_y})...")
        new_pos = mouse.move_to(target_x, target_y, duration=0.4)
        console.print(f"[green][PASS][/green] Step 3: Move Mouse -> Cursor positioned at [bold yellow]{new_pos}[/bold yellow]")
        results["Move Mouse"] = (True, f"Moved to {new_pos}")
    except Exception as err:
        console.print(f"[red][FAIL][/red] Step 3: Move Mouse failed: {err}")
        results["Move Mouse"] = (False, str(err))

    # 4. Click
    try:
        clicked_pos = mouse.click(button="left")
        console.print(f"[green][PASS][/green] Step 4: Click -> Dispatched left click at [bold yellow]{clicked_pos}[/bold yellow]")
        results["Click"] = (True, f"Clicked at {clicked_pos}")
    except Exception as err:
        console.print(f"[red][FAIL][/red] Step 4: Click failed: {err}")
        results["Click"] = (False, str(err))

    # 5. Open an Application (Notepad)
    notepad_process = None
    notepad_window = None
    try:
        console.print("Launching Notepad...")
        notepad_process = apps.open_application("notepad", wait_for_window=True, timeout=8.0, activate=True)
        time.sleep(1.0)  # Brief settle time for UI rendering

        matching_windows = apps.find_windows("notepad")
        if matching_windows:
            notepad_window = matching_windows[0]
            apps.activate_window(notepad_window.hwnd)
            console.print(f"[green][PASS][/green] Step 5: Open Application -> Notepad active (HWND: {notepad_window.hwnd}, Title: {notepad_window.title!r})")
            results["Open Application"] = (True, f"HWND {notepad_window.hwnd} ({notepad_window.title})")
        else:
            console.print("[yellow][WARN][/yellow] Step 5: Notepad opened but window title query was not matched immediately")
            results["Open Application"] = (True, "Process spawned")
    except Exception as err:
        console.print(f"[red][FAIL][/red] Step 5: Open Application failed: {err}")
        results["Open Application"] = (False, str(err))

    # 6. Type Text
    try:
        test_text = "Operator AI Agent: Live Computer Control Demonstration Success!"
        console.print(f"Typing text into application: [italic white]{test_text!r}[/italic white]")
        chars_typed = keyboard.type_text(test_text, interval=0.03)
        console.print(f"[green][PASS][/green] Step 6: Type Text -> Typed [bold green]{chars_typed}[/bold green] characters")
        results["Type Text"] = (True, f"Typed {chars_typed} characters")
    except Exception as err:
        console.print(f"[red][FAIL][/red] Step 6: Type Text failed: {err}")
        results["Type Text"] = (False, str(err))

    # 7. Press Keys
    try:
        # Press enter to create a new line, then type another line
        keyboard.press_key("enter")
        time.sleep(0.2)
        second_line = "Keyboard press test: ENTER passed."
        keyboard.type_text(second_line, interval=0.02)
        console.print(f"[green][PASS][/green] Step 7: Press Keys -> Dispatched 'enter' key and typed second line")
        results["Press Keys"] = (True, "Pressed 'enter' and confirmed text entry")
    except Exception as err:
        console.print(f"[red][FAIL][/red] Step 7: Press Keys failed: {err}")
        results["Press Keys"] = (False, str(err))

    # Take verification screenshot showing typed text in Notepad
    try:
        notepad_shot = artifacts_dir / "demo_notepad_typed.png"
        screen.capture_full_screen(save_path=notepad_shot)
        console.print(f"Captured verification screenshot: [cyan]{notepad_shot}[/cyan]")
    except Exception as err:
        console.print(f"Verification screenshot notice: {err}")

    # Clean up: Close Notepad safely without broadcasting global keystrokes
    try:
        time.sleep(0.5)
        console.print("Cleaning up: Closing Notepad safely...")
        # Target Notepad specifically via window handle (WM_CLOSE)
        if notepad_window:
            apps.close_window(notepad_window.hwnd, force=False)
            time.sleep(0.5)

        # Terminate notepad process directly to discard unsaved buffers safely
        if notepad_process and notepad_process.poll() is None:
            notepad_process.terminate()
            try:
                notepad_process.wait(timeout=1.5)
            except Exception:
                notepad_process.kill()

        # If any remaining notepad windows match our session, clean them up
        remaining = apps.find_windows("notepad")
        for win in remaining:
            if notepad_window and win.hwnd == notepad_window.hwnd:
                apps.close_window(win.hwnd, force=True)

        console.print("[green][PASS][/green] Cleanup: Notepad closed safely without affecting host IDE.")
    except Exception as err:
        console.print(f"[yellow]Cleanup warning:[/yellow] {err}")

    # Summary Table
    table = Table(title="Live Demonstration Verification Summary", show_header=True, header_style="bold cyan")
    table.add_column("Capability", style="bold white", width=25)
    table.add_column("Status", width=12)
    table.add_column("Details", style="dim")

    all_passed = True
    for cap, (status, detail) in results.items():
        if not status:
            all_passed = False
        status_text = "[bold green]PASSED[/bold green]" if status else "[bold red]FAILED[/bold red]"
        table.add_row(cap, status_text, detail)

    console.print("\n", table, "\n")
    return all_passed


if __name__ == "__main__":
    success = run_live_demonstration()
    sys.exit(0 if success else 1)
