"""Unit tests for tool registry and execution safety."""

from actions.registry import Tool, ToolRegistry, ToolResult, get_tool_registry


def test_registry_registration_and_lookup():
    reg = ToolRegistry()
    test_tool = Tool(
        name="add_numbers",
        description="Add two numbers together",
        parameters={
            "type": "object",
            "properties": {
                "a": {"type": "integer"},
                "b": {"type": "integer"},
            },
            "required": ["a", "b"],
        },
        handler=lambda a, b: a + b,
    )
    reg.register(test_tool)

    assert "add_numbers" in reg.list_tool_names()
    assert reg.get_tool("add_numbers") is test_tool


def test_registry_schema_export():
    reg = ToolRegistry()
    reg.register(
        Tool(
            name="test_fn",
            description="A test function",
            parameters={"type": "object", "properties": {"x": {"type": "string"}}},
            handler=lambda x: x,
        )
    )
    schemas = reg.get_all_schemas()
    assert len(schemas) == 1
    assert schemas[0]["type"] == "function"
    assert schemas[0]["function"]["name"] == "test_fn"


def test_registry_execution_success():
    reg = ToolRegistry()
    reg.register(
        Tool(
            name="multiply",
            description="Multiply two integers",
            parameters={"type": "object", "properties": {"x": {}, "y": {}}},
            handler=lambda x, y: x * y,
        )
    )
    result = reg.execute("multiply", {"x": 6, "y": 7})
    assert isinstance(result, ToolResult)
    assert result.success is True
    assert result.output == 42
    assert result.error is None
    assert result.execution_time_seconds >= 0.0


def test_registry_execution_invalid_args():
    reg = ToolRegistry()
    reg.register(
        Tool(
            name="greet",
            description="Greet someone",
            parameters={"type": "object", "properties": {"name": {}}},
            handler=lambda name: f"Hello, {name}",
        )
    )
    result = reg.execute("greet", {"unknown_param": 123})
    assert result.success is False
    assert "Invalid arguments" in result.error


def test_registry_execution_unknown_tool():
    reg = ToolRegistry()
    result = reg.execute("non_existent_tool", {})
    assert result.success is False
    assert "not registered" in result.error


def test_default_tool_registry_contains_standard_tools():
    reg = get_tool_registry()
    tools = set(reg.list_tool_names())
    expected = {
        "get_screen_size",
        "screenshot",
        "move_mouse",
        "click",
        "double_click",
        "right_click",
        "scroll",
        "type_text",
        "press_key",
        "hotkey",
        "wait",
        "open_application",
        "get_active_window",
    }
    assert expected.issubset(tools)
