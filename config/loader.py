"""
config/loader.py
================
Deep-merge YAML configuration loader with environment variable injection.

Load order (later overrides earlier):
  1. config/settings.yaml          — base defaults
  2. config/settings.local.yaml    — local overrides (gitignored)
  3. Environment variables         — ENV_VAR_NAME maps to nested YAML keys
                                     via double-underscore separator
                                     e.g. BROKER__LOGIN=12345 → broker.login

Usage
-----
    from config.loader import get_settings
    cfg = get_settings()
    print(cfg.broker.server)
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CONFIG_DIR = Path(__file__).parent


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file and return its contents as a dict (empty if missing)."""
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into *base* (mutates and returns base)."""
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


# ---------------------------------------------------------------------------
# Pydantic sub-models
# ---------------------------------------------------------------------------

class RedisConfig(BaseModel):
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    market_context_key: str = "market_context_state"
    context_ttl_seconds: int = 900


class BrokerConfig(BaseModel):
    platform: str = "MT5"
    symbol: str = "EURUSD"
    magic_number: int = 202401
    deviation: int = 10
    connection_timeout_s: int = 10
    retry_attempts: int = 3
    retry_delay_s: float = 2.0
    login: int = 0
    password: str = ""
    server: str = ""


class EngineConfig(BaseModel):
    tick_interval_ms: int = 250
    bar_timeframe: str = "M15"
    warmup_bars: int = 200


class TelegramConfig(BaseModel):
    parse_mode: str = "HTML"
    alert_timeout_s: int = 120
    bot_token: str = ""
    chat_id: str = ""


class NewsConfig(BaseModel):
    provider: str = "finnhub"
    calendar_refresh_min: int = 60
    finnhub_api_key: str = ""


class DatabaseConfig(BaseModel):
    url: str = "sqlite:///data/trading.db"
    echo: bool = False
    pool_pre_ping: bool = True


class SystemConfig(BaseModel):
    environment: str = "development"
    log_level: str = "INFO"
    log_file: str = "logs/trading_engine.log"
    log_rotation: str = "1 day"
    log_retention: str = "14 days"


class SupervisorConfig(BaseModel):
    heartbeat_interval_s: int = 30
    deadman_ping_interval_s: int = 300
    max_missed_heartbeats: int = 3


class BackupConfig(BaseModel):
    retention_days: int = 14
    output_dir: str = "data/backups"


# ---------------------------------------------------------------------------
# Root settings model
# ---------------------------------------------------------------------------

class Settings(BaseModel):
    system: SystemConfig = Field(default_factory=SystemConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    broker: BrokerConfig = Field(default_factory=BrokerConfig)
    engine: EngineConfig = Field(default_factory=EngineConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    news: NewsConfig = Field(default_factory=NewsConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    supervisor: SupervisorConfig = Field(default_factory=SupervisorConfig)
    backup: BackupConfig = Field(default_factory=BackupConfig)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Build the merged Settings object (cached after first call).

    Environment variables override YAML using double-underscore nesting:
        BROKER__PASSWORD=secret  →  settings.broker.password = "secret"
        TELEGRAM__BOT_TOKEN=xyz  →  settings.telegram.bot_token = "xyz"
    """
    base = _load_yaml(_CONFIG_DIR / "settings.yaml")
    local = _load_yaml(_CONFIG_DIR / "settings.local.yaml")
    merged = _deep_merge(base, local)

    # Inject environment variables (SECTION__KEY format)
    for env_key, env_val in os.environ.items():
        parts = env_key.lower().split("__", maxsplit=1)
        if len(parts) == 2:
            section, key = parts
            if section in merged and isinstance(merged[section], dict):
                merged[section][key] = env_val

    return Settings.model_validate(merged)
