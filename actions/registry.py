"""Structured tool registry and execution engine for computer capabilities."""

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union

from app.logging import get_logger
from actions.applications import get_app_controller
from actions.keyboard import get_keyboard_controller
from actions.mouse import get_mouse_controller
from perception.screenshot import get_screen_capture

logger = get_logger("actions.registry")


@dataclass
class ToolResult:
    """Standard output envelope for tool execution."""

    tool_name: str
    success: bool
    output: Any = None
    error: Optional[str] = None
    execution_time_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool_name,
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "execution_time_seconds": round(self.execution_time_seconds, 4),
        }


@dataclass
class Tool:
    """Defines a callable computer capability with strict schema."""

    name: str
    description: str
    parameters: Dict[str, Any]  # JSON Schema specification
    handler: Callable[..., Any]

    def to_schema(self) -> Dict[str, Any]:
        """Format as standard tool definition for LLM function calling."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """Central registry enforcing structured computer control tools."""

    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a new tool instance."""
        if tool.name in self._tools:
            logger.warning(f"Overwriting existing tool registration: {tool.name}")
        self._tools[tool.name] = tool
        logger.debug(f"Registered tool: {tool.name}")

    def get_tool(self, name: str) -> Optional[Tool]:
        """Lookup tool by name."""
        return self._tools.get(name)

    def list_tool_names(self) -> List[str]:
        """Return names of all registered tools."""
        return list(self._tools.keys())

    def get_all_schemas(self) -> List[Dict[str, Any]]:
        """Return schema list for LLM function calling."""
        return [tool.to_schema() for tool in self._tools.values()]

    def execute(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> ToolResult:
        """Execute a tool with provided arguments safely."""
        tool = self._tools.get(name)
        if not tool:
            err = f"Tool {name!r} is not registered. Available tools: {self.list_tool_names()}"
            logger.error(err)
            return ToolResult(tool_name=name, success=False, error=err)

        args = arguments or {}
        start_time = time.time()
        logger.info(f"Executing tool: {name} with args={args}")

        try:
            output = tool.handler(**args)
            elapsed = time.time() - start_time
            logger.info(f"Tool {name} succeeded in {elapsed:.3f}s")
            return ToolResult(
                tool_name=name,
                success=True,
                output=output,
                execution_time_seconds=elapsed,
            )
        except TypeError as err:
            elapsed = time.time() - start_time
            err_msg = f"Invalid arguments for tool {name!r}: {err}"
            logger.error(err_msg)
            return ToolResult(
                tool_name=name,
                success=False,
                error=err_msg,
                execution_time_seconds=elapsed,
            )
        except Exception as err:
            elapsed = time.time() - start_time
            err_msg = f"Error during execution of tool {name!r}: {err}"
            logger.exception(err_msg)
            return ToolResult(
                tool_name=name,
                success=False,
                error=err_msg,
                execution_time_seconds=elapsed,
            )


# Module-level shared registry
_registry_instance: Optional[ToolRegistry] = None


def create_default_registry() -> ToolRegistry:
    """Initialize registry with all standard computer-use tools."""
    registry = ToolRegistry()
    mouse = get_mouse_controller()
    keyboard = get_keyboard_controller()
    apps = get_app_controller()
    screen = get_screen_capture()

    # 1. get_screen_size
    registry.register(
        Tool(
            name="get_screen_size",
            description="Get the primary monitor screen dimensions (width, height) in pixels.",
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
            handler=lambda: mouse.get_screen_size(),
        )
    )

    # 2. screenshot
    def _take_screenshot(save_path: Optional[str] = None, region: Optional[List[int]] = None) -> Dict[str, Any]:
        if region and len(region) == 4:
            x, y, w, h = region
            img = screen.capture_region(x=x, y=y, width=w, height=h, save_path=save_path)
        else:
            path = save_path or str(screen.generate_screenshot_filename())
            img = screen.capture_full_screen(save_path=path)
            return {"path": path, "width": img.width, "height": img.height}
        return {"path": save_path, "width": img.width, "height": img.height}

    registry.register(
        Tool(
            name="screenshot",
            description="Take a screenshot of the whole screen or a specific region.",
            parameters={
                "type": "object",
                "properties": {
                    "save_path": {
                        "type": "string",
                        "description": "Optional file path to save the screenshot.",
                    },
                    "region": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Optional [x, y, width, height] region to crop.",
                    },
                },
                "required": [],
            },
            handler=_take_screenshot,
        )
    )

    # 3. move_mouse
    registry.register(
        Tool(
            name="move_mouse",
            description="Move the mouse cursor to the specified screen coordinates.",
            parameters={
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "Target X coordinate in pixels."},
                    "y": {"type": "integer", "description": "Target Y coordinate in pixels."},
                    "duration": {
                        "type": "number",
                        "description": "Movement animation duration in seconds.",
                        "default": 0.0,
                    },
                },
                "required": ["x", "y"],
            },
            handler=lambda x, y, duration=0.0: mouse.move_to(x=x, y=y, duration=duration),
        )
    )

    # 4. click
    registry.register(
        Tool(
            name="click",
            description="Click at screen coordinates or at the current mouse position.",
            parameters={
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "Optional X coordinate in pixels."},
                    "y": {"type": "integer", "description": "Optional Y coordinate in pixels."},
                    "button": {
                        "type": "string",
                        "enum": ["left", "middle", "right"],
                        "default": "left",
                        "description": "Mouse button to click.",
                    },
                    "clicks": {
                        "type": "integer",
                        "default": 1,
                        "description": "Number of clicks.",
                    },
                },
                "required": [],
            },
            handler=lambda x=None, y=None, button="left", clicks=1: mouse.click(
                x=x, y=y, button=button, clicks=clicks
            ),
        )
    )

    # 5. double_click
    registry.register(
        Tool(
            name="double_click",
            description="Double-click at screen coordinates or at current position.",
            parameters={
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "Optional X coordinate."},
                    "y": {"type": "integer", "description": "Optional Y coordinate."},
                },
                "required": [],
            },
            handler=lambda x=None, y=None: mouse.double_click(x=x, y=y),
        )
    )

    # 6. right_click
    registry.register(
        Tool(
            name="right_click",
            description="Right-click at screen coordinates or at current position.",
            parameters={
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "Optional X coordinate."},
                    "y": {"type": "integer", "description": "Optional Y coordinate."},
                },
                "required": [],
            },
            handler=lambda x=None, y=None: mouse.right_click(x=x, y=y),
        )
    )

    # 7. scroll
    registry.register(
        Tool(
            name="scroll",
            description="Scroll the mouse wheel. Positive value scrolls up, negative scrolls down.",
            parameters={
                "type": "object",
                "properties": {
                    "clicks": {
                        "type": "integer",
                        "description": "Number of scroll clicks (+up, -down).",
                    },
                    "x": {"type": "integer", "description": "Optional X coordinate."},
                    "y": {"type": "integer", "description": "Optional Y coordinate."},
                },
                "required": ["clicks"],
            },
            handler=lambda clicks, x=None, y=None: mouse.scroll(clicks=clicks, x=x, y=y),
        )
    )

    # 8. type_text
    def _type_text_tool(
        text: str,
        interval: float = 0.02,
        window_title_or_hwnd: Optional[Union[str, int]] = None,
    ) -> Dict[str, Any]:
        if window_title_or_hwnd:
            focused = apps.ensure_target_focused(window_title_or_hwnd)
            if not focused:
                raise RuntimeError(
                    f"Pre-action target lock failed: Target window {window_title_or_hwnd!r} could not be verified in foreground."
                )
        else:
            active = apps.get_active_window()
            if not active:
                raise RuntimeError("Cannot type: No active foreground window detected.")
            if apps.is_protected_target(hwnd=active.hwnd, pid=active.pid, title=active.title):
                raise RuntimeError(
                    f"Safety guard: Keystrokes blocked from protected host window {active.title!r}."
                )

        typed_len = keyboard.type_text(text=text, interval=interval)
        return {"typed": text, "length": typed_len}

    registry.register(
        Tool(
            name="type_text",
            description="Type a string of text into the active input element or specified target window.",
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The exact text to type."},
                    "window_title_or_hwnd": {
                        "type": "string",
                        "description": "Optional window title or handle to focus before typing.",
                    },
                    "interval": {
                        "type": "number",
                        "default": 0.02,
                        "description": "Delay in seconds between keystrokes.",
                    },
                },
                "required": ["text"],
            },
            handler=_type_text_tool,
        )
    )

    # 9. press_key
    registry.register(
        Tool(
            name="press_key",
            description="Press a specific keyboard key (e.g. 'enter', 'tab', 'escape', 'backspace', 'win').",
            parameters={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Key name to press."},
                    "presses": {
                        "type": "integer",
                        "default": 1,
                        "description": "Number of times to press the key.",
                    },
                },
                "required": ["key"],
            },
            handler=lambda key, presses=1: keyboard.press_key(key=key, presses=presses),
        )
    )

    # 10. hotkey
    def _execute_hotkey(keys: Union[List[str], str]) -> List[str]:
        if isinstance(keys, str):
            key_list = [k.strip() for k in keys.replace("+", " ").split()]
        else:
            key_list = keys
        return keyboard.hotkey(*key_list)

    registry.register(
        Tool(
            name="hotkey",
            description="Press a combination of keys simultaneously (e.g. ['ctrl', 'c'] or 'ctrl+s').",
            parameters={
                "type": "object",
                "properties": {
                    "keys": {
                        "description": "List of key names or a plus-separated string (e.g. 'ctrl+a').",
                    }
                },
                "required": ["keys"],
            },
            handler=_execute_hotkey,
        )
    )

    # 11. wait
    def _wait_tool(seconds: float) -> float:
        duration = max(0.0, float(seconds))
        time.sleep(duration)
        return duration

    registry.register(
        Tool(
            name="wait",
            description="Pause execution for a given duration in seconds.",
            parameters={
                "type": "object",
                "properties": {
                    "seconds": {
                        "type": "number",
                        "description": "Number of seconds to pause.",
                    }
                },
                "required": ["seconds"],
            },
            handler=_wait_tool,
        )
    )

    # 12. open_application
    def _open_app_tool(
        app_name: str,
        arguments: Optional[str] = None,
        wait_for_window: bool = True,
    ) -> Dict[str, Any]:
        proc = apps.open_application(app_name, arguments=arguments, wait_for_window=wait_for_window)
        win = getattr(proc, "detected_window", None)
        detail = f" with arguments {arguments!r}" if arguments else ""
        return {
            "launched": True,
            "app_name": app_name,
            "arguments": arguments,
            "hwnd": win.hwnd if win else None,
            "pid": win.pid if win else (getattr(proc, "pid", None)),
            "title": win.title if win else "",
            "message": f"Launched {app_name}{detail}" + (f" (window: '{win.title}', HWND: {win.hwnd})" if win else ""),
        }

    registry.register(
        Tool(
            name="open_application",
            description="Launch a desktop application or file by name/path (e.g. 'notepad', 'calc', 'explorer') with optional arguments.",
            parameters={
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "Application executable name or alias (e.g. 'calc', 'notepad', 'explorer').",
                    },
                    "arguments": {
                        "type": "string",
                        "description": "Optional file path or arguments to pass to the application.",
                    },
                    "wait_for_window": {
                        "type": "boolean",
                        "default": True,
                        "description": "Whether to wait for the application window to appear.",
                    },
                },
                "required": ["app_name"],
            },
            handler=_open_app_tool,
        )
    )

    # 13. get_active_window
    def _get_active_window_info() -> Optional[Dict[str, Any]]:
        win = apps.get_active_window()
        if not win:
            return None
        return {
            "hwnd": win.hwnd,
            "title": win.title,
            "rect": win.rect,
            "center": win.center,
            "pid": win.pid,
        }

    registry.register(
        Tool(
            name="get_active_window",
            description="Get information about the currently focused active window.",
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
            handler=_get_active_window_info,
        )
    )

    # 14. list_windows
    def _list_windows(visible_only: bool = True) -> List[Dict[str, Any]]:
        windows = apps.list_windows(visible_only=visible_only)
        return [
            {"hwnd": w.hwnd, "title": w.title, "pid": w.pid, "rect": w.rect}
            for w in windows
            if w.title.strip()
        ]

    registry.register(
        Tool(
            name="list_windows",
            description="List open visible application windows on the desktop.",
            parameters={
                "type": "object",
                "properties": {
                    "visible_only": {
                        "type": "boolean",
                        "default": True,
                        "description": "Whether to return only visible windows.",
                    }
                },
                "required": [],
            },
            handler=_list_windows,
        )
    )

    # 15. activate_window
    def _activate_window(window_title_or_hwnd: Union[str, int]) -> Dict[str, Any]:
        success = apps.activate_window(window_title_or_hwnd)
        return {"success": success, "target": str(window_title_or_hwnd)}

    registry.register(
        Tool(
            name="activate_window",
            description="Bring a specific window to the foreground and focus it by title or handle.",
            parameters={
                "type": "object",
                "properties": {
                    "window_title_or_hwnd": {
                        "description": "Window title substring or integer window handle (HWND).",
                    }
                },
                "required": ["window_title_or_hwnd"],
            },
            handler=_activate_window,
        )
    )

    # 16. close_window
    def _close_window(window_title_or_hwnd: Union[str, int], force: bool = False) -> Dict[str, Any]:
        success = apps.close_window(window_title_or_hwnd, force=force)
        return {"success": success, "target": str(window_title_or_hwnd)}

    registry.register(
        Tool(
            name="close_window",
            description="Close a window safely by window title or handle. Protected windows cannot be closed.",
            parameters={
                "type": "object",
                "properties": {
                    "window_title_or_hwnd": {
                        "description": "Window title substring or integer handle.",
                    },
                    "force": {
                        "type": "boolean",
                        "default": False,
                        "description": "Whether to force terminate the process.",
                    },
                },
                "required": ["window_title_or_hwnd"],
            },
            handler=_close_window,
        )
    )

    # 17. finish_task
    def _finish_task(summary: str, success: bool = True) -> Dict[str, Any]:
        return {"finished": True, "success": success, "summary": summary}

    registry.register(
        Tool(
            name="finish_task",
            description="Call this tool when the user's objective has been fully accomplished to complete the task.",
            parameters={
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Summary of actions taken and verified results.",
                    },
                    "success": {
                        "type": "boolean",
                        "default": True,
                        "description": "Whether the goal was achieved successfully.",
                    },
                },
                "required": ["summary"],
            },
            handler=_finish_task,
        )
    )

    return registry


def get_tool_registry() -> ToolRegistry:
    """Retrieve shared default ToolRegistry instance."""
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = create_default_registry()
    return _registry_instance
