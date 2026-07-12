"""
Pydantic-Settings based configuration.

Values are loaded from environment variables / .env file.
All secrets have safe defaults for local development so the app boots
without a real .env file.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App runtime ──────────────────────────────────────────────────────────
    app_env: Literal["development", "staging", "production"] = "development"
    app_secret_key: str = "dev-secret-key-replace-in-production"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # ── LLM ─────────────────────────────────────────────────────────────────
    llm_api_key: str = Field(default="mock-llm-key", description="LLM provider API key")
    llm_model: str = "gemini-2.0-flash"
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    # ── Notion ───────────────────────────────────────────────────────────────
    notion_token: str = Field(default="mock-notion-token")
    notion_database_id: str = Field(default="mock-notion-db-id")
    notion_approval_desk_id: str = Field(default="mock-notion-approval-id")
    notion_audit_feed_id: str = Field(default="mock-notion-audit-id")

    # Webhook push verification: Notion sends this secret in the Authorization header.
    # Leave blank to skip signature verification (dev/test only).
    notion_webhook_secret: str = Field(default="")

    # Polling fallback: enabled automatically when push webhooks are not configured.
    # Set NOTION_POLLING_ENABLED=true in .env to force polling regardless.
    notion_polling_enabled: bool = False
    notion_polling_interval_seconds: int = Field(default=30, ge=5, le=3600)

    # ── Gmail ────────────────────────────────────────────────────────────────
    gmail_client_id: str = Field(default="mock-gmail-client-id")
    gmail_client_secret: str = Field(default="mock-gmail-client-secret")
    gmail_refresh_token: str = Field(default="mock-gmail-refresh-token")
    gmail_sender_address: str = Field(default="no-reply@mockbank.example.com")
    # In dev/staging, all outbound emails are redirected here instead of the
    # real customer address. Leave blank to use gmail_sender_address as fallback.
    gmail_sandbox_to: str = Field(default="sandbox@mockbank.example.com")

    # ── Slack ────────────────────────────────────────────────────────────────
    slack_webhook_url: str = Field(default="https://hooks.slack.com/mock/webhook")
    slack_bot_token: str = Field(default="xoxb-mock-token")
    slack_channel_id: str = Field(default="C00000000")
    # In dev/staging, all Slack messages are redirected to this test channel.
    # Leave blank to use slack_channel_id.
    slack_sandbox_channel: str = Field(default="bank-sim-test")

    # ── Mock toggles ─────────────────────────────────────────────────────────
    use_mock_notion: bool = True
    use_mock_gmail: bool = True
    use_mock_slack: bool = True
    use_mock_llm: bool = True

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings (singleton)."""
    return Settings()
