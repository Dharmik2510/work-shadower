"""Application settings, loaded from environment variables (and an optional .env file)."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Core
    database_url: str = "postgresql://postgres@localhost:5432/workshadower"
    db_pool_min: int = 1
    db_pool_max: int = 10
    public_base_url: str = ""  # e.g. https://shadower.corp.example; derived from request if empty
    cors_origins: str = ""  # comma-separated, or "*"
    web_dist_dir: str = ""
    log_level: str = "INFO"
    secret_key: str = "dev-insecure-change-me"  # signs local-storage upload URLs
    disable_pgvector: bool = False  # force the full-text-only path even if pgvector exists

    # Auth
    auth_mode: Literal["dev", "oidc"] = "dev"
    admin_emails: str = ""  # comma-separated; these users get role=admin on login
    token_ttl_hours: int = 24 * 30
    oidc_issuer: str = ""
    oidc_client_id: str = ""  # expected JWT audience
    oidc_groups_claim: str = "groups"
    oidc_admin_group: str = ""
    oidc_team_group_prefix: str = ""  # only groups with this prefix become teams (prefix stripped)

    # Storage
    storage_driver: Literal["local", "s3"] = "local"
    local_storage_dir: str = "./data/assets"
    s3_bucket: str = ""
    s3_region: str = "us-east-1"
    s3_endpoint_url: str = ""  # MinIO / gateway; empty = AWS
    s3_public_endpoint_url: str = ""  # host clients use for presigned URLs, if different
    s3_prefix: str = "assets/"
    presign_ttl_seconds: int = 900
    max_asset_bytes: int = 2 * 1024 * 1024

    # LLM
    llm_provider: Literal["anthropic", "openai", "none"] = "none"
    llm_model: str = "claude-haiku-4-5"
    llm_api_key: str = ""
    llm_base_url: str = ""  # anthropic default https://api.anthropic.com ; openai default https://api.openai.com/v1
    llm_timeout_seconds: float = 60.0
    llm_max_output_tokens: int = 4096
    llm_input_cost_per_mtok: float = 1.0
    llm_output_cost_per_mtok: float = 5.0
    llm_daily_calls_per_user: int = 50
    embed_provider: Literal["openai", "none"] = "none"
    embed_model: str = "text-embedding-3-small"
    embed_api_key: str = ""  # falls back to llm_api_key
    embed_base_url: str = ""  # falls back to https://api.openai.com/v1
    llm_retry_base_seconds: float = 1.0
    llm_max_retries: int = 3  # retries on 429 / 5xx / transport errors, exponential backoff + jitter
    llm_prompt_caching: bool = True  # Anthropic: cache the (large, static) system prompt + tool schema

    # Relevance filter (keep / review / drop each recorded event)
    # FILTER_PROVIDER: jev (TypeSafe System One model, with local fallback) | local (rules only)
    filter_provider: Literal["jev", "local"] = "local"
    jev_api_key: str = ""
    jev_base_url: str = "https://api.typesafe.ai"
    jev_model: str = "jev-latest"
    jev_timeout_seconds: float = 5.0
    jev_max_retries: int = 2
    jev_retry_base_seconds: float = 0.25
    jev_concurrency: int = 8  # parallel Jev calls per recording
    jev_max_events: int = 600  # events beyond this use the local filter only
    jev_context_events: int = 4  # neighbours on each side sent with every event
    jev_input_cost_per_mtok: float = 0.042
    jev_breaker_failures: int = 5  # consecutive failures that open the circuit breaker
    jev_breaker_cooldown_seconds: float = 60.0
    jev_task_types: str = ""  # optional comma list, e.g. "claims,underwriting,billing" -> tags
    filter_split_threshold: float = 0.85  # P(new task starts here) needed to split a recording
    filter_min_segment_steps: int = 3

    # Anthropic Message Batches: ~50% cheaper skill generation, results usually within minutes
    # (up to 24h). Drafts appear later; the heuristic draft is used if the batch fails or expires.
    llm_batch_mode: bool = False
    llm_batch_poll_seconds: float = 60.0
    llm_batch_max_hours: float = 24.0

    # Jobs / worker
    job_max_attempts: int = 5
    job_backoff_base_seconds: float = 10.0
    job_backoff_max_seconds: float = 3600.0
    job_visibility_timeout_seconds: int = 600
    worker_poll_seconds: float = 2.0
    run_worker_in_api: bool = False

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        return v.replace("postgresql+psycopg://", "postgresql://")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def admin_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
