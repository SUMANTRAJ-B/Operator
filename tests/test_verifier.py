"""Unit tests for action verifier."""

from unittest.mock import MagicMock, patch
from actions.applications import WindowInfo
from verification.verifier import ActionVerifier, get_action_verifier


def test_action_verifier_singleton():
    v1 = get_action_verifier()
    v2 = get_action_verifier()
    assert v1 is v2


@patch("actions.applications.AppController.find_windows")
def test_verify_open_application_success(mock_find):
    mock_find.return_value = [
        WindowInfo(hwnd=999, title="Untitled - Notepad", rect=(0, 0, 500, 500), is_visible=True, pid=123)
    ]
    verifier = ActionVerifier()
    res = verifier.verify_action("open_application", {"app_name": "notepad"}, "Launched notepad")
    assert res.verified is True
    assert "Untitled - Notepad" in res.details


@patch("actions.applications.AppController.find_windows")
def test_verify_open_application_failure(mock_find):
    mock_find.return_value = []
    verifier = ActionVerifier()
    res = verifier.verify_action("open_application", {"app_name": "unknown_tool"}, None)
    assert res.verified is False


def test_verify_finish_task():
    verifier = ActionVerifier()
    res = verifier.verify_action("finish_task", {"summary": "Completed successfully", "success": True}, None)
    assert res.verified is True
