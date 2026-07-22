"""Fetch and normalize national/regional event sources (iCal and Google Calendar).

Every source is reduced to the same NormalizedEvent shape so the aggregator does
not care where an event came from. A source that fails -- unreachable host, bad
TLS, malformed ICS -- raises FeedError; the caller logs it and moves on. One bad
feed never aborts a run.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import requests

from .config import Source

logger = logging.getLogger(__name__)

USER_AGENT = "flinthillsdsa-event-integration/1.0 (+https://github.com/flinthillsdsa/event-integration)"
FETCH_TIMEOUT_SECONDS = 30
MAX_FEED_BYTES = 20 * 1024 * 1024


class FeedError(RuntimeError):
    """A single source could not be fetched or parsed."""


@dataclass
class NormalizedEvent:
    uid: str
    title: str
    description: str
    location: str
    start: dt.datetime
    end: dt.datetime
    url: str | None
    source: str
    all_day: bool = False
    region: str | None = None

    def matches_filters(self, source: Source) -> bool:
        haystack = f"{self.title}\n{self.description}".lower()
        if source.include and not any(k in haystack for k in source.include):
            return False
        if source.exclude and any(k in haystack for k in source.exclude):
            return False
        return True


def _as_aware(value, tzinfo: ZoneInfo) -> tuple[dt.datetime, bool]:
    """Coerce an ICS date/datetime to an aware datetime. Returns (dt, all_day)."""
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=tzinfo), False
        return value, False
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day, tzinfo=tzinfo), True
    raise FeedError(f"Unsupported date value: {value!r}")


def _text(component, key: str) -> str:
    value = component.get(key)
    if value is None:
        return ""
    return str(value).strip()


# --------------------------------------------------------------------------
# iCal
# --------------------------------------------------------------------------

def fetch_ical(source: Source, window_start: dt.datetime, window_end: dt.datetime,
               tzinfo: ZoneInfo, default_duration_minutes: int) -> list[NormalizedEvent]:
    import icalendar
    import recurring_ical_events

    url = source.url
    # webcal:// is just https:// with a calendar-app handler.
    if url.lower().startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]

    try:
        response = requests.get(url, timeout=FETCH_TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
    except requests.RequestException as exc:
        raise FeedError(f"could not fetch {url}: {exc}") from exc

    if len(response.content) > MAX_FEED_BYTES:
        raise FeedError(f"{url} returned {len(response.content)} bytes, over the {MAX_FEED_BYTES} limit")

    try:
        calendar = icalendar.Calendar.from_ical(response.content)
        # Expands RRULE/RDATE and honours EXDATE, so weekly chapter meetings
        # arrive as individual dated occurrences.
        occurrences = recurring_ical_events.of(calendar).between(window_start, window_end)
    except Exception as exc:  # icalendar raises a wide variety of parse errors
        raise FeedError(f"could not parse iCal from {url}: {exc}") from exc

    events: list[NormalizedEvent] = []
    for component in occurrences:
        if component.name != "VEVENT":
            continue
        if str(component.get("STATUS", "")).upper() == "CANCELLED":
            continue

        dtstart = component.get("DTSTART")
        if dtstart is None:
            continue
        start, all_day = _as_aware(dtstart.dt, tzinfo)

        dtend = component.get("DTEND")
        if dtend is not None:
            end, _ = _as_aware(dtend.dt, tzinfo)
        elif component.get("DURATION") is not None:
            end = start + component["DURATION"].dt
        else:
            end = start + dt.timedelta(minutes=default_duration_minutes)
        if end <= start:
            end = start + dt.timedelta(minutes=default_duration_minutes)

        base_uid = _text(component, "UID") or f"{_text(component, 'SUMMARY')}-{start.isoformat()}"
        # Recurrence expansion reuses the master UID, so qualify it by start.
        uid = f"{base_uid}@{start.strftime('%Y%m%dT%H%M%S')}"

        events.append(
            NormalizedEvent(
                uid=uid,
                title=_text(component, "SUMMARY") or "(untitled)",
                description=_text(component, "DESCRIPTION"),
                location=_text(component, "LOCATION"),
                start=start,
                end=end,
                url=_text(component, "URL") or None,
                source=source.name,
                all_day=all_day,
                region=source.region,
            )
        )

    return events


# --------------------------------------------------------------------------
# Public Google Calendar
# --------------------------------------------------------------------------

def fetch_gcal(source: Source, window_start: dt.datetime, window_end: dt.datetime,
               tzinfo: ZoneInfo, default_duration_minutes: int, service) -> list[NormalizedEvent]:
    from googleapiclient.errors import HttpError

    from .google_calendar import list_events, parse_event_time

    try:
        raw_events = list_events(
            service,
            calendar_id=source.url,
            time_min=window_start,
            time_max=window_end,
        )
    except HttpError as exc:
        raise FeedError(f"Calendar API rejected {source.url}: {exc}") from exc

    events: list[NormalizedEvent] = []
    for item in raw_events:
        if item.get("status") == "cancelled":
            continue
        start, all_day = parse_event_time(item.get("start"), tzinfo)
        if start is None:
            continue
        end, _ = parse_event_time(item.get("end"), tzinfo)
        if end is None or end <= start:
            end = start + dt.timedelta(minutes=default_duration_minutes)

        uid = item.get("iCalUID") or item.get("id") or ""
        if not uid:
            continue

        events.append(
            NormalizedEvent(
                uid=uid,
                title=(item.get("summary") or "(untitled)").strip(),
                description=(item.get("description") or "").strip(),
                location=(item.get("location") or "").strip(),
                start=start,
                end=end,
                url=item.get("htmlLink"),
                source=source.name,
                all_day=all_day,
                region=source.region,
            )
        )

    return events


def fetch_source(source: Source, window_start: dt.datetime, window_end: dt.datetime,
                 tzinfo: ZoneInfo, default_duration_minutes: int, service) -> list[NormalizedEvent]:
    """Fetch one source and apply its include/exclude filters."""
    if source.type == "ical":
        events = fetch_ical(source, window_start, window_end, tzinfo, default_duration_minutes)
    elif source.type == "gcal":
        events = fetch_gcal(source, window_start, window_end, tzinfo, default_duration_minutes, service)
    else:
        raise FeedError(f"unknown source type {source.type!r}")

    kept = [e for e in events if e.matches_filters(source)]
    dropped = len(events) - len(kept)
    if dropped:
        logger.info("  %s: %d event(s) dropped by include/exclude filters", source.name, dropped)
    return kept
