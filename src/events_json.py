"""Component 2: generate events.json for the website.

    python -m src.events_json [--stdout]

Reads the chapter calendar and the National / Regional calendar and writes a
flat, sorted, forward-looking feed that the WordPress page fetches as a static
file. No Google credential ever reaches the browser.

The payload deliberately carries no generation timestamp: the workflow commits
this file only when it changes, and a timestamp would make every run a change.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from zoneinfo import ZoneInfo

from . import google_calendar as gcal
from .committees import extract_rsvp_url, resolve
from .config import Config, ConfigError, load_config

logger = logging.getLogger("events_json")


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def build_entry(item: dict, config: Config, *, source: str, tzinfo: ZoneInfo) -> dict | None:
    start, all_day = gcal.parse_event_time(item.get("start"), tzinfo)
    if start is None:
        return None

    end, _ = gcal.parse_event_time(item.get("end"), tzinfo)
    if end is None or end <= start:
        # Discord-authored events can arrive without an end time.
        end = start + dt.timedelta(minutes=config.default_duration_minutes)

    raw_title = (item.get("summary") or "").strip() or "(untitled)"
    resolved = resolve(raw_title, config, national=(source == "national"))
    description = (item.get("description") or "").strip()

    return {
        "id": item.get("id", ""),
        "title": resolved.title,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "allDay": all_day,
        "location": (item.get("location") or "").strip(),
        "description": _truncate(description, config.max_description_chars),
        # No fallback to the event's Google Calendar htmlLink: that URL drops a
        # website visitor into Google's calendar UI, which is worse than no link.
        # A card with no url simply renders without a "Details & RSVP" button.
        "url": extract_rsvp_url(description),
        "committee": resolved.committee,
        "color": resolved.color,
        "source": source,
    }


def generate(config: Config) -> dict:
    service = gcal.build_service(config.service_account_info, readonly=True)
    sa_email = gcal.service_account_email(config.service_account_info)
    tzinfo = ZoneInfo(config.timezone)

    for label, calendar_id in (
        ("Flint Hills Chapter of DSA", config.chapter_calendar_id),
        ("National / Regional", config.national_calendar_id),
    ):
        gcal.check_access(service, calendar_id=calendar_id, sa_email=sa_email,
                          label=label, need_write=False)

    now = dt.datetime.now(tzinfo)
    # "Upcoming and today" -- an event earlier today still counts as today.
    time_min = now.replace(hour=0, minute=0, second=0, microsecond=0)
    time_max = now + dt.timedelta(days=config.window_days)

    entries: list[dict] = []
    for source, calendar_id in (
        ("chapter", config.chapter_calendar_id),
        ("national", config.national_calendar_id),
    ):
        # Deliberately not caught: a half-read calendar would publish a feed
        # missing real events, and the site would quietly show a short list.
        # Better to fail and leave the last good events.json in place.
        items = gcal.list_events(
            service, calendar_id=calendar_id, time_min=time_min, time_max=time_max
        )

        count = 0
        for item in items:
            if item.get("status") == "cancelled":
                continue
            entry = build_entry(item, config, source=source, tzinfo=tzinfo)
            if entry is None:
                continue
            entries.append(entry)
            count += 1
        logger.info("%s calendar: %d event(s)", source, count)

    entries.sort(key=lambda e: (e["start"], e["title"].lower()))

    # Only committees actually present, in config order, so the site's filter
    # chips never offer an empty category.
    present = {e["committee"] for e in entries}
    ordered = [c for c in config.committees if c.name in present]
    committees = [{"name": c.name, "color": c.color} for c in ordered]
    for extra in (config.national_committee, config.default_committee):
        if extra.name in present and not any(c["name"] == extra.name for c in committees):
            committees.append({"name": extra.name, "color": extra.color})

    return {
        "version": 1,
        "timezone": config.timezone,
        "windowDays": config.window_days,
        "committees": committees,
        "events": entries,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate events.json for the website.")
    parser.add_argument("--stdout", action="store_true", help="print instead of writing the file")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    logging.getLogger("googleapiclient").setLevel(logging.ERROR)

    try:
        config = load_config()
    except ConfigError as exc:
        logger.error("%s", exc)
        return 2

    try:
        payload = generate(config)
    except gcal.CalendarAccessError as exc:
        logger.error("%s", exc)
        return 3

    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    if args.stdout:
        print(text, end="")
        return 0

    config.output_path.parent.mkdir(parents=True, exist_ok=True)
    config.output_path.write_text(text, encoding="utf-8")
    logger.info("Wrote %d event(s) to %s", len(payload["events"]), config.output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
