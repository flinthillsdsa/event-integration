"""Configuration loading: config.yml (routing + settings) and secrets (env)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.yml"


class ConfigError(RuntimeError):
    """Raised when configuration or required secrets are missing/invalid."""


@dataclass
class Settings:
    default_timezone: str
    default_duration_minutes: int
    past_window_days: int
    only_tagged: bool
    multi_tag_fan_out: bool
    on_cancelled: str
    on_disappeared: str


@dataclass
class DiscordConfig:
    enabled: bool
    guild_id: str | None
    bot_token: str | None


@dataclass
class Config:
    group_id: str
    settings: Settings
    routing: dict[str, str]            # hashtag-name (no '#') -> calendar id
    action_network_api_key: str
    google_service_account_info: dict
    discord: DiscordConfig
    routing_by_calendar: dict[str, list[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Reverse map so we can name which tags route to a calendar (logging).
        by_cal: dict[str, list[str]] = {}
        for tag, cal_id in self.routing.items():
            by_cal.setdefault(cal_id, []).append(tag)
        self.routing_by_calendar = by_cal


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(
            f"Required secret '{name}' is missing. Set it as an environment "
            f"variable / GitHub Actions Secret."
        )
    return value


def load_config(config_path: Path | None = None) -> Config:
    path = config_path or CONFIG_PATH
    if not path.exists():
        raise ConfigError(f"config.yml not found at {path}")

    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    sync_raw = raw.get("sync", {})
    settings = Settings(
        default_timezone=sync_raw.get("default_timezone", "America/Chicago"),
        default_duration_minutes=int(sync_raw.get("default_duration_minutes", 120)),
        past_window_days=int(sync_raw.get("past_window_days", 1)),
        only_tagged=bool(sync_raw.get("only_tagged", True)),
        multi_tag_fan_out=bool(sync_raw.get("multi_tag_fan_out", True)),
        on_cancelled=str(sync_raw.get("on_cancelled", "cancel")).lower(),
        on_disappeared=str(sync_raw.get("on_disappeared", "delete")).lower(),
    )

    routing = {str(k).lower().lstrip("#"): str(v) for k, v in (raw.get("routing") or {}).items()}
    if not routing:
        raise ConfigError("config.yml has no 'routing' map; nothing to sync.")

    # --- Secrets ---
    an_key = _require_env("ACTION_NETWORK_API_KEY")

    sa_json = _require_env("GOOGLE_SERVICE_ACCOUNT_JSON")
    try:
        sa_info = json.loads(sa_json)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON. Paste the full "
            "service-account key file contents as the secret value."
        ) from exc

    discord_raw = raw.get("discord", {}) or {}
    discord_enabled = bool(discord_raw.get("enabled", False))
    discord = DiscordConfig(
        enabled=discord_enabled,
        guild_id=str(discord_raw["guild_id"]) if discord_raw.get("guild_id") else None,
        bot_token=os.environ.get("DISCORD_BOT_TOKEN"),
    )
    if discord.enabled:
        if not discord.guild_id:
            raise ConfigError("discord.enabled is true but discord.guild_id is missing in config.yml")
        if not discord.bot_token:
            raise ConfigError("discord.enabled is true but DISCORD_BOT_TOKEN secret is missing")

    return Config(
        group_id=str((raw.get("action_network", {}) or {}).get("group_id", "")),
        settings=settings,
        routing=routing,
        action_network_api_key=an_key,
        google_service_account_info=sa_info,
        discord=discord,
    )
