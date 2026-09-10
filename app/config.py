"""Configuration settings for Operator agent."""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application and environment configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application settings
    app_name: str = Field(default="Operator", description="Application name")
    environment: Literal["development", "testing", "production"] = Field(
        default="development", description="Execution environment"
    )
    log_level: str = Field(default="INFO", description="Logging level")
    log_to_file: bool = Field(default=True, description="Whether to write logs to disk")
    log_dir: Path = Field(default=Path("logs"), description="Log file directory")

    # Safety & Control pacing
    fail_safe: bool = Field(
        default=True,
        description="Enable emergency fail-safe (e.g., cursor to corner aborts)",
    )
    action_delay_seconds: float = Field(
        default=0.1, ge=0.0, description="Pacing pause in seconds after actions"
    )
    confirmation_required: bool = Field(
        default=False, description="Require confirmation for dangerous actions"
    )

    # Perception artifacts
    screenshot_dir: Path = Field(
        default=Path("artifacts/screenshots"),
        description="Directory for saved screenshots",
    )

    # Local LLM Provider (Ollama)
    ollama_base_url: str = Field(
        default="http://localhost:11434", description="Ollama API base endpoint"
    )
    ollama_model: str = Field(
        default="qwen3:8b", description="Default model name for Ollama inference"
    )
    llm_temperature: float = Field(
        default=0.1, ge=0.0, le=2.0, description="Inference sampling temperature"
    )
    ollama_think: Optional[bool] = Field(
        default=False,
        description="Enable or disable model thinking mode in Ollama API (False for low-latency tool calling)",
    )
    ollama_num_predict: int = Field(
        default=512, ge=1, le=4096, description="Maximum tokens to generate per inference call"
    )

    # Agent Loop settings
    max_agent_steps: int = Field(
        default=25, ge=1, le=100, description="Maximum iterations in agent loop"
    )
    agent_timeout_seconds: float = Field(
        default=180.0, ge=10.0, description="Maximum timeout for entire task in seconds"
    )
    max_step_retries: int = Field(
        default=3, ge=1, le=10, description="Max retries for a failed step before aborting"
    )
    cleanup_owned_resources_on_finish: bool = Field(
        default=True,
        description="Enforce runtime session cleanup of Operator-owned windows upon task completion",
    )


@lru_cache()
def get_settings() -> Settings:
    """Retrieve cached application settings instance."""
    return Settings()
