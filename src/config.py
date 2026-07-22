"""Configuration loading: config.yml, config/feeds.yml, and the one secret.

The only secret this repo touches is the Google service account JSON. It is
read from the environment and never logged, printed, or written to disk.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.yml"
FEEDS_PATH = REPO_ROOT / "config" / "feeds.yml"


class ConfigError(RuntimeError):
    """Raised when configuration or the required secret is missing/invalid."""


@dataclass(frozen=True)
class Committee:
    name: str
    tags: tuple[str, ...]
    color: str
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class Source:
    name: str
    type: str            # "ical" | "gcal"
    url: str
    enabled: bool
    region: str | None
    include: tuple[str, ...]
    exclude: tuple[str, ...]


@dataclass
class Config:
    chapter_calendar_id: str
    national_calendar_id: str
    timezone: str

    horizon_days: int
    past_window_days: int
    default_duration_minutes: int

    window_days: int
    output_path: Path
    max_description_chars: int

    committees: list[Committee]
    default_committee: Committee
    national_committee: Committee

    service_account_info: dict = field(repr=False, default_factory=dict)


def _committee(raw: dict) -> Committee:
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ConfigError("A committee entry is missing 'name'.")
    color = str(raw.get("color") or "#546e7a").strip()
    return Committee(
        name=name,
        tags=tuple(str(t).strip() for t in (raw.get("tags") or []) if str(t).strip()),
        color=color,
        keywords=tuple(str(k).strip().lower() for k in (raw.get("keywords") or []) if str(k).strip()),
    )


def _load_service_account_info(required: bool) -> dict:
    """Read the service account JSON from env, or from a local file path.

    GOOGLE_SERVICE_ACCOUNT_JSON -- the key file's full contents (Actions secret).
    GOOGLE_SERVICE_ACCOUNT_FILE -- path to a local key file, for local runs only.
                                   The path pattern is gitignored.
    """
    raw = (os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or "").strip()

    if not raw:
        path = (os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE") or "").strip()
        if path:
            key_path = Path(path).expanduser()
            if not key_path.exists():
                raise ConfigError(f"GOOGLE_SERVICE_ACCOUNT_FILE points at a missing file: {key_path}")
            raw = key_path.read_text(encoding="utf-8").strip()

    if not raw:
        if not required:
            return {}
        raise ConfigError(
            "No Google credentials. Set GOOGLE_SERVICE_ACCOUNT_JSON (the Actions "
            "secret) or, for local runs, GOOGLE_SERVICE_ACCOUNT_FILE to a key path."
        )

    try:
        info = json.loads(raw)
    except json.JSONDecodeError as exc:
        # Deliberately does not echo the value.
        raise ConfigError(
            "The service account credential is not valid JSON. The secret value "
            "must be the entire key file contents."
        ) from exc

    if info.get("type") != "service_account":
        raise ConfigError("The supplied Google credential is not a service account key.")
    return info


def load_config(config_path: Path | None = None, *, require_credentials: bool = True) -> Config:
    path = config_path or CONFIG_PATH
    if not path.exists():
        raise ConfigError(f"config.yml not found at {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    calendars = raw.get("calendars") or {}
    chapter = str(calendars.get("chapter") or "").strip()
    national = str(calendars.get("national") or "").strip()
    if not chapter or not national:
        raise ConfigError("config.yml must set both calendars.chapter and calendars.national.")
    for label, cal_id in (("chapter", chapter), ("national", national)):
        if "REPLACE" in cal_id.upper():
            raise ConfigError(
                f"calendars.{label} is still a placeholder ({cal_id!r}). Put the real "
                f"Google Calendar ID in config.yml."
            )

    agg = raw.get("aggregator") or {}
    ejs = raw.get("events_json") or {}

    committees = [_committee(c) for c in (raw.get("committees") or [])]
    if not committees:
        raise ConfigError("config.yml has no 'committees'; the tag map cannot be empty.")

    return Config(
        chapter_calendar_id=chapter,
        national_calendar_id=national,
        timezone=str(raw.get("timezone") or "America/Chicago"),
        horizon_days=int(agg.get("horizon_days", 180)),
        past_window_days=int(agg.get("past_window_days", 1)),
        default_duration_minutes=int(agg.get("default_duration_minutes", 120)),
        window_days=int(ejs.get("window_days", 60)),
        output_path=REPO_ROOT / str(ejs.get("output_path", "events.json")),
        max_description_chars=int(ejs.get("max_description_chars", 600)),
        committees=committees,
        default_committee=_committee(raw.get("default_committee") or {"name": "General", "color": "#546e7a"}),
        national_committee=_committee(raw.get("national_committee") or {"name": "National", "color": "#ec1f27"}),
        service_account_info=_load_service_account_info(require_credentials),
    )


def load_sources(feeds_path: Path | None = None) -> list[Source]:
    path = feeds_path or FEEDS_PATH
    if not path.exists():
        raise ConfigError(f"feeds config not found at {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    sources: list[Source] = []
    seen: set[str] = set()

    for entry in raw.get("sources") or []:
        name = str(entry.get("name") or "").strip()
        url = str(entry.get("url") or "").strip()
        stype = str(entry.get("type") or "").strip().lower()
        if not name or not url:
            raise ConfigError(f"feeds.yml source is missing 'name' or 'url': {entry!r}")
        if stype not in {"ical", "gcal"}:
            raise ConfigError(f"feeds.yml source {name!r} has type {stype!r}; expected 'ical' or 'gcal'.")
        if name.lower() in seen:
            raise ConfigError(f"feeds.yml has two sources named {name!r}; names must be unique.")
        seen.add(name.lower())

        sources.append(
            Source(
                name=name,
                type=stype,
                url=url,
                enabled=bool(entry.get("enabled", True)),
                region=(str(entry["region"]).strip() if entry.get("region") else None),
                include=tuple(str(k).strip().lower() for k in (entry.get("include") or []) if str(k).strip()),
                exclude=tuple(str(k).strip().lower() for k in (entry.get("exclude") or []) if str(k).strip()),
            )
        )

    return sources
