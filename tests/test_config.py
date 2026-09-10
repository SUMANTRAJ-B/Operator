"""Unit tests for configuration management."""

import os
from pathlib import Path
from app.config import Settings, get_settings


def test_default_settings():
    settings = Settings()
    assert settings.app_name == "Operator"
    assert settings.environment == "development"
    assert settings.fail_safe is True
    assert settings.action_delay_seconds == 0.1
    assert settings.ollama_model == "qwen3:8b"


def test_settings_custom_values():
    settings = Settings(
        app_name="TestOperator",
        environment="testing",
        fail_safe=False,
        action_delay_seconds=0.05,
        ollama_model="phi4-mini:latest",
    )
    assert settings.app_name == "TestOperator"
    assert settings.environment == "testing"
    assert settings.fail_safe is False
    assert settings.action_delay_seconds == 0.05
    assert settings.ollama_model == "phi4-mini:latest"


def test_get_settings_caching():
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2
