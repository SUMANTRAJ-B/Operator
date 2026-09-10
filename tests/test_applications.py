"""Unit tests for application and window controller."""

from actions.applications import AppController, WindowInfo, get_app_controller


def test_app_controller_singleton():
    a1 = get_app_controller()
    a2 = get_app_controller()
    assert a1 is a2


def test_resolve_application_command():
    controller = get_app_controller()
    assert controller.resolve_application_command("notepad") == "notepad.exe"
    assert controller.resolve_application_command("calc") == "calc.exe"
    assert controller.resolve_application_command("calculator") == "calc.exe"
    assert controller.resolve_application_command("explorer") == "explorer.exe"
    assert controller.resolve_application_command("custom_tool.exe") == "custom_tool.exe"


def test_window_info_geometry():
    win = WindowInfo(
        hwnd=12345,
        title="Test Window",
        rect=(100, 200, 500, 600),
        is_visible=True,
        pid=999,
    )
    assert win.width == 400
    assert win.height == 400
    assert win.center == (300, 400)


def test_list_windows():
    controller = get_app_controller()
    windows = controller.list_windows(visible_only=True)
    assert isinstance(windows, list)
    # On a normal Windows desktop session, there are visible windows
    if windows:
        first = windows[0]
        assert isinstance(first.hwnd, int)
        assert isinstance(first.title, str)
        assert len(first.rect) == 4


def test_get_active_window():
    controller = get_app_controller()
    active = controller.get_active_window()
    if active:
        assert isinstance(active.hwnd, int)
        assert isinstance(active.title, str)


def test_is_protected_target():
    controller = get_app_controller()
    # Current PID must be protected
    import os
    assert controller.is_protected_target(pid=os.getpid()) is True
    # IDE window titles must be protected
    assert controller.is_protected_target(title="Antigravity IDE - Operator") is True
    assert controller.is_protected_target(title="Visual Studio Code") is True
    # Unrelated windows must not be protected
    assert controller.is_protected_target(title="Untitled - Notepad", pid=999999) is False


def test_close_window_blocks_protected_target():
    controller = get_app_controller()
    import os
    # Refuse to close process with current PID
    result = controller.close_window("Antigravity")
    assert result is False
