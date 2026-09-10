"""Ollama local LLM provider with native tool calling and thinking support."""

import json
import uuid
from typing import Any, Dict, List, Optional
import requests

from app.config import get_settings
from app.logging import get_logger
from providers.llm.base import ChatMessage, LLMProvider, LLMResponse, ToolCall

logger = get_logger("providers.llm.ollama")


class OllamaProvider(LLMProvider):
    """Client for local Ollama server running models like qwen3:8b."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        think: Optional[bool] = None,
    ):
        settings = get_settings()
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.ollama_model
        self.timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else settings.agent_timeout_seconds
        )
        self.think = think if think is not None else settings.ollama_think
        self.endpoint = f"{self.base_url}/api/chat"

    def chat(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
    ) -> LLMResponse:
        """Send a chat completion request to the local Ollama instance."""
        settings = get_settings()
        temp = temperature if temperature is not None else settings.llm_temperature
        options: Dict[str, Any] = {
            "temperature": temp,
            "num_predict": settings.ollama_num_predict,
        }

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_dict() for m in messages],
            "options": options,
            "stream": False,
        }

        if self.think is not None:
            payload["think"] = self.think

        if tools:
            payload["tools"] = tools

        logger.debug(
            f"Sending request to Ollama [{self.model}] with {len(messages)} messages "
            f"and {len(tools or [])} tools"
        )

        data = None
        for attempt in range(2):
            try:
                response = requests.post(
                    self.endpoint,
                    json=payload,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                data = response.json()
                break
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as err:
                logger.warning(f"Ollama request attempt {attempt + 1} failed with transient error: {err}")
                if attempt == 0:
                    import time
                    time.sleep(0.5)
                    continue
                err_msg = f"Failed to connect to Ollama at {self.endpoint}: {err}"
                logger.error(err_msg)
                raise RuntimeError(err_msg) from err
            except requests.exceptions.RequestException as err:
                err_msg = f"Failed to connect to Ollama at {self.endpoint}: {err}"
                logger.error(err_msg)
                raise RuntimeError(err_msg) from err

        message_data = data.get("message", {})
        content = message_data.get("content", "")
        thinking = message_data.get("thinking", None)
        raw_tool_calls = message_data.get("tool_calls", [])

        parsed_tool_calls: List[ToolCall] = []
        for tc in raw_tool_calls:
            call_id = tc.get("id") or f"call_{uuid.uuid4().hex[:8]}"
            func = tc.get("function", {})
            func_name = func.get("name", "")
            raw_args = func.get("arguments", {})

            if isinstance(raw_args, str):
                try:
                    args_dict = json.loads(raw_args)
                except Exception:
                    args_dict = {"raw_arguments": raw_args}
            elif isinstance(raw_args, dict):
                args_dict = raw_args
            else:
                args_dict = {}

            parsed_tool_calls.append(
                ToolCall(id=call_id, name=func_name, arguments=args_dict)
            )

        # Fallback check: if model output raw tool call format in content or thinking
        if not parsed_tool_calls and content:
            fallback = self._extract_json_tool_call(content)
            if fallback:
                parsed_tool_calls.append(fallback)

        logger.debug(
            f"Ollama response received: {len(parsed_tool_calls)} tool calls, "
            f"content_length={len(content)}"
        )
        return LLMResponse(
            content=content,
            tool_calls=parsed_tool_calls,
            thinking=thinking,
            raw=data,
        )

    def _extract_json_tool_call(self, text: str) -> Optional[ToolCall]:
        """Attempt to parse embedded JSON tool calling if output in text."""
        cleaned = text.strip()
        if "```json" in cleaned:
            parts = cleaned.split("```json")
            for part in parts[1:]:
                json_text = part.split("```")[0].strip()
                try:
                    parsed = json.loads(json_text)
                    if isinstance(parsed, dict) and "name" in parsed:
                        return ToolCall(
                            id=f"call_{uuid.uuid4().hex[:8]}",
                            name=parsed["name"],
                            arguments=parsed.get("arguments", parsed.get("parameters", {})),
                        )
                except Exception:
                    continue
        return None


_llm_provider_instance: Optional[OllamaProvider] = None


def get_llm_provider(
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    timeout_seconds: Optional[float] = None,
    think: Optional[bool] = None,
) -> OllamaProvider:
    """Retrieve shared or configured OllamaProvider singleton."""
    global _llm_provider_instance
    if (
        _llm_provider_instance is None
        or base_url is not None
        or model is not None
        or timeout_seconds is not None
        or think is not None
    ):
        _llm_provider_instance = OllamaProvider(
            base_url=base_url,
            model=model,
            timeout_seconds=timeout_seconds,
            think=think,
        )
    return _llm_provider_instance
