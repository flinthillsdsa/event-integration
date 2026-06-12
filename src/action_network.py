"""Action Network API v2 client and event normalization.

Docs consulted:
  https://actionnetwork.org/docs/v2/events   (fields, location sub-structure)
  https://actionnetwork.org/docs/v2/          (OData ?filter= syntax)

Routing is NOT done via OSDI taggings (those are person-centric in Action
Network). Instead organizers put routing hashtags (e.g. "#civic") directly in
the event description; we scan for them here.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://actionnetwork.org/api/v2"
PAGE_LIMIT = 100
MAX_RETRIES = 4

_HASHTAG_RE = re.compile(r"#(\w+)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


@dataclass
class NormalizedEvent:
    """An Action Network event reduced to what the calendar / Discord need."""

    uuid: str
    title: str
    description_html: str
    description_text: str          # hashtags + HTML stripped, whitespace collapsed
    browser_url: str
    status: str                    # 'confirmed' | 'tentative' | 'cancelled'
    location_str: str              # formatted "venue, line1, city, region, zip"
    tags: list[str]                # routing hashtags found (lowercase, no '#')
    # Wall-clock times in the configured default timezone (naive datetimes):
    start_wall: datetime
    end_wall: datetime
    # True UTC instants (timezone-aware) for Discord / comparisons:
    start_utc: datetime
    end_utc: datetime

    @property
    def is_cancelled(self) -> bool:
        return self.status == "cancelled"


def _strip_hashtags(text: str) -> str:
    return _HASHTAG_RE.sub("", text)


def _to_plain_text(html: str) -> str:
    text = _HTML_TAG_RE.sub("", html or "")
    return _WS_RE.sub(" ", text).strip()


def _resolve_time(value: str | None, tz: ZoneInfo) -> tuple[datetime | None, datetime | None]:
    """Return (wall_clock_naive, utc_aware) for an Action Network timestamp.

    Action Network stamps local times with a bogus trailing 'Z' that does NOT
    mean UTC. We treat a 'Z' value (or any value with no offset) as wall-clock
    in `tz`. A value carrying a real numeric offset is honored as a true instant.
    """
    if not value:
        return None, None
    try:
        if value.endswith("Z"):
            naive = datetime.fromisoformat(value[:-1])
            local = naive.replace(tzinfo=tz)
            return naive, local.astimezone(timezone.utc)
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            local = dt.replace(tzinfo=tz)
            return dt, local.astimezone(timezone.utc)
        # Has a real offset: derive wall-clock in tz, keep the true instant.
        local = dt.astimezone(tz)
        return local.replace(tzinfo=None), dt.astimezone(timezone.utc)
    except ValueError:
        logger.warning("Could not parse Action Network timestamp: %r", value)
        return None, None


def format_location(location) -> str:
    """Format the Action Network location sub-structure into one line."""
    if isinstance(location, str):
        return location.strip()
    if not isinstance(location, dict):
        return ""
    parts: list[str] = []
    if location.get("venue"):
        parts.append(str(location["venue"]).strip())
    parts.extend(str(line).strip() for line in location.get("address_lines", []) if line)
    for key in ("locality", "region", "postal_code"):
        if location.get(key):
            parts.append(str(location[key]).strip())
    return ", ".join(p for p in parts if p)


def extract_uuid(event: dict) -> str | None:
    """Pull the Action Network event UUID from the identifiers array.

    Identifiers look like 'action_network:fbce520b-12fa-437e-bd8c-f89310fdc005'.
    """
    for identifier in event.get("identifiers", []):
        if isinstance(identifier, str) and identifier.startswith("action_network:"):
            return identifier.split(":", 1)[1]
    # Fallbacks (older payloads / safety):
    for identifier in event.get("identifiers", []):
        if isinstance(identifier, str) and ":" in identifier:
            return identifier.split(":", 1)[1]
    browser_url = event.get("browser_url", "")
    if browser_url:
        return browser_url.rstrip("/").split("/")[-1]
    return None


class ActionNetworkClient:
    def __init__(self, api_key: str, default_timezone: str, default_duration_minutes: int):
        self.session = requests.Session()
        self.session.headers.update(
            {"OSDI-API-Token": api_key, "Content-Type": "application/json"}
        )
        self.tz = ZoneInfo(default_timezone)
        self.default_duration = timedelta(minutes=default_duration_minutes)

    # ---- HTTP with rate-limit handling -------------------------------------

    def _get(self, url: str, params: dict | None = None) -> dict:
        for attempt in range(MAX_RETRIES):
            resp = self.session.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", 2 ** attempt))
                logger.warning("Action Network rate limited; sleeping %.1fs", retry_after)
                time.sleep(retry_after)
                continue
            if resp.status_code in (401, 403):
                # Fail loudly on auth errors (Section 7).
                raise RuntimeError(
                    f"Action Network auth failed ({resp.status_code}). "
                    f"Check ACTION_NETWORK_API_KEY. Body: {resp.text[:200]}"
                )
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError("Action Network: exhausted retries due to rate limiting")

    # ---- Public API ---------------------------------------------------------

    def fetch_events(self, past_window_days: int) -> list[dict]:
        """Fetch raw events, server-filtered to the sync window, all pages."""
        cutoff = (datetime.now(self.tz) - timedelta(days=past_window_days)).date()
        # OData on the events endpoint supports start_date with eq/gt/lt.
        odata = f"start_date gt '{cutoff.isoformat()}'"
        params = {"filter": odata, "limit": PAGE_LIMIT}

        events: list[dict] = []
        url: str | None = f"{BASE_URL}/events"
        while url:
            logger.info("Fetching Action Network events: %s params=%s", url, params or "")
            data = self._get(url, params=params)
            params = None  # 'next' href already carries query state
            page = data.get("_embedded", {}).get("osdi:events", [])
            events.extend(page)
            url = data.get("_links", {}).get("next", {}).get("href")
        logger.info("Fetched %d raw events from Action Network", len(events))
        return events

    def normalize(self, event: dict, routing_tags: set[str]) -> NormalizedEvent | None:
        """Normalize a raw event. Returns None if it should be skipped."""
        uuid = extract_uuid(event)
        if not uuid:
            logger.warning("Skipping event with no resolvable UUID: %r", event.get("title"))
            return None

        description_html = event.get("description", "") or ""
        found = {t.lower() for t in _HASHTAG_RE.findall(description_html)}
        tags = sorted(found & routing_tags)

        start_wall, start_utc = _resolve_time(event.get("start_date"), self.tz)
        if start_wall is None or start_utc is None:
            logger.warning("Skipping event with unparseable start_date: %s", uuid)
            return None

        end_wall, end_utc = _resolve_time(event.get("end_date"), self.tz)
        if end_wall is None or end_utc is None:
            end_wall = start_wall + self.default_duration
            end_utc = start_utc + self.default_duration

        description_text = _to_plain_text(_strip_hashtags(description_html))

        return NormalizedEvent(
            uuid=uuid,
            title=event.get("title", "Untitled Event") or "Untitled Event",
            description_html=description_html,
            description_text=description_text,
            browser_url=event.get("browser_url", "") or "",
            status=(event.get("status", "confirmed") or "confirmed").lower(),
            location_str=format_location(event.get("location", {})),
            tags=tags,
            start_wall=start_wall,
            end_wall=end_wall,
            start_utc=start_utc,
            end_utc=end_utc,
        )
