"""Unit tests for Ollama LLM provider and message schemas."""

from unittest.mock import MagicMock, patch
import pytest

from providers.llm.base import ChatMessage, ToolCall, LLMResponse
from providers.llm.ollama import OllamaProvider, get_llm_provider


def test_chat_message_to_dict():
    msg = ChatMessage(role="user", content="Hello")
    assert msg.to_dict() == {"role": "user", "content": "Hello"}

    tc = ToolCall(id="call_123", name="open_application", arguments={"app_name": "notepad"})
    asst_msg = ChatMessage(role="assistant", content="", tool_calls=[tc])
    d = asst_msg.to_dict()
    assert d["role"] == "assistant"
    assert len(d["tool_calls"]) == 1
    assert d["tool_calls"][0]["function"]["name"] == "open_application"


def test_ollama_provider_singleton():
    p1 = get_llm_provider()
    p2 = get_llm_provider()
    assert p1 is p2


@patch("requests.post")
def test_ollama_provider_chat_with_native_tool_call(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "model": "qwen3:8b",
        "message": {
            "role": "assistant",
            "content": "",
            "thinking": "Need to open notepad first.",
            "tool_calls": [
                {
                    "id": "call_abc123",
                    "function": {
                        "name": "open_application",
                        "arguments": {"app_name": "notepad"},
                    },
                }
            ],
        },
    }
    mock_post.return_value = mock_resp

    provider = OllamaProvider()
    res = provider.chat(
        messages=[ChatMessage(role="user", content="Open Notepad")],
        tools=[{"type": "function", "function": {"name": "open_application"}}],
    )

    assert isinstance(res, LLMResponse)
    assert res.has_tool_calls is True
    assert len(res.tool_calls) == 1
    assert res.tool_calls[0].name == "open_application"
    assert res.tool_calls[0].arguments == {"app_name": "notepad"}
    assert res.thinking == "Need to open notepad first."


@patch("requests.post")
def test_ollama_provider_chat_fallback_json_in_text(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "model": "qwen3:8b",
        "message": {
            "role": "assistant",
            "content": "```json\n{\"name\": \"type_text\", \"arguments\": {\"text\": \"Hello\"}}\n```",
            "tool_calls": [],
        },
    }
    mock_post.return_value = mock_resp

    provider = OllamaProvider()
    res = provider.chat(messages=[ChatMessage(role="user", content="Type hello")])

    assert res.has_tool_calls is True
    assert res.tool_calls[0].name == "type_text"
    assert res.tool_calls[0].arguments == {"text": "Hello"}
