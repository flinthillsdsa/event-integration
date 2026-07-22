"""Component 1: aggregate national/regional feeds into the National / Regional calendar.

    python -m src.aggregate [--dry-run]

Chronicle mirrors that calendar into Discord, so anything written here reaches
Discord without this repo knowing about Discord at all.

Idempotent by construction: event ids are derived from (source name, source uid),
and each event stores a hash of the fields we write, so a second run in a row
makes zero API writes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import re
import sys
from collections import defaultdict
from zoneinfo import ZoneInfo

from . import google_calendar as gcal
from .config import Config, ConfigError, Source, load_config, load_sources
from .feeds import FeedError, NormalizedEvent, fetch_source

logger = logging.getLogger("aggregate")

# Footer we append to descriptions so a reader (and Discord) can see provenance.
FOOTER_MARKER = "— via "
_WHITESPACE_RE = re.compile(r"\s+")


def normalized_key(event: NormalizedEvent) -> str:
    """Cross-source dedup key: squashed title + start minute."""
    title = _WHITESPACE_RE.sub(" ", event.title.strip().lower())
    title = re.sub(r"[^a-z0-9 ]+", "", title)
    return f"{title}|{event.start.astimezone(dt.timezone.utc).strftime('%Y%m%dT%H%M')}"


def build_description(event: NormalizedEvent) -> str:
    parts = [event.description.strip()] if event.description.strip() else []
    if event.url:
        parts.append(f"RSVP / details: {event.url}")
    provenance = f"{FOOTER_MARKER}{event.source}"
    if event.region:
        provenance += f" ({event.region})"
    parts.append(provenance)
    return "\n\n".join(parts)


def to_calendar_body(event: NormalizedEvent, timezone: str) -> dict:
    if event.all_day:
        start_node = {"date": event.start.date().isoformat()}
        end_node = {"date": max(event.end.date(), event.start.date() + dt.timedelta(days=1)).isoformat()}
    else:
        start_node = {"dateTime": event.start.isoformat(), "timeZone": timezone}
        end_node = {"dateTime": event.end.isoformat(), "timeZone": timezone}

    return {
        "summary": event.title,
        "description": build_description(event),
        "location": event.location,
        "start": start_node,
        "end": end_node,
        "extendedProperties": {
            "private": {
                gcal.PROP_SOURCE: event.source,
                gcal.PROP_SOURCE_UID: event.uid[:1024],
            }
        },
        "transparency": "transparent",
    }


def collect(config: Config, sources: list[Source], service,
            window_start: dt.datetime, window_end: dt.datetime,
            ) -> tuple[list[NormalizedEvent], set[str], dict[str, int]]:
    """Fetch every enabled source. Returns (events, failed_source_names, skipped_counts)."""
    tzinfo = ZoneInfo(config.timezone)
    events: list[NormalizedEvent] = []
    failed: set[str] = set()
    skipped: dict[str, int] = defaultdict(int)

    seen_uids: set[tuple[str, str]] = set()
    seen_keys: set[str] = set()

    for source in sources:
        if not source.enabled:
            logger.info("[%s] disabled, skipping", source.name)
            continue
        try:
            fetched = fetch_source(
                source, window_start, window_end, tzinfo, config.default_duration_minutes, service
            )
        except FeedError as exc:
            # One bad feed must not fail the run, and must not cause its
            # previously-synced events to be reconciled away.
            logger.warning("[%s] FAILED, skipping this source: %s", source.name, exc)
            failed.add(source.name)
            continue
        except Exception as exc:  # noqa: BLE001 - a source must never abort the run
            logger.warning("[%s] FAILED (unexpected %s), skipping this source: %s",
                           source.name, type(exc).__name__, exc)
            failed.add(source.name)
            continue

        kept = 0
        for event in fetched:
            uid_key = (source.name, event.uid)
            if uid_key in seen_uids:
                skipped[source.name] += 1
                continue
            key = normalized_key(event)
            if key in seen_keys:
                logger.info("[%s] duplicate of an earlier source: %s", source.name, event.title)
                skipped[source.name] += 1
                continue
            seen_uids.add(uid_key)
            seen_keys.add(key)
            events.append(event)
            kept += 1

        logger.info("[%s] fetched %d, kept %d", source.name, len(fetched), kept)

    return events, failed, dict(skipped)


def run(config: Config, sources: list[Source], *, dry_run: bool = False) -> int:
    service = gcal.build_service(config.service_account_info)
    logger.info("Service account: %s", gcal.service_account_email(config.service_account_info))

    now = dt.datetime.now(dt.timezone.utc)
    window_start = now - dt.timedelta(days=config.past_window_days)
    window_end = now + dt.timedelta(days=config.horizon_days)
    logger.info("Window: %s .. %s", window_start.date(), window_end.date())

    events, failed_sources, skipped = collect(config, sources, service, window_start, window_end)

    desired: dict[str, tuple[NormalizedEvent, dict]] = {}
    for event in events:
        event_id = gcal.derive_event_id(event.source, event.uid)
        desired[event_id] = (event, to_calendar_body(event, config.timezone))

    existing = gcal.list_managed_events(
        service, calendar_id=config.national_calendar_id,
        time_min=window_start, time_max=window_end,
    )
    logger.info("Found %d existing aggregator-managed event(s) on the National / Regional calendar",
                len(existing))

    stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for source_name, count in skipped.items():
        stats[source_name]["skipped"] = count

    for event_id, (event, body) in desired.items():
        if dry_run:
            action = "unchanged" if event_id in existing else "added"
        else:
            action = gcal.upsert(
                service, calendar_id=config.national_calendar_id,
                event_id=event_id, body=body, existing=existing.get(event_id),
            )
        stats[event.source][action] += 1

    # Reconcile: anything we previously created that no longer appears in any
    # source goes away, so upstream cancellations propagate. Events belonging to
    # a source that failed this run are left alone.
    for event_id, event in existing.items():
        if event_id in desired:
            continue
        private = (event.get("extendedProperties") or {}).get("private") or {}
        source_name = private.get(gcal.PROP_SOURCE, "(unknown source)")
        if source_name in failed_sources:
            logger.info("Keeping %r: its source %s failed this run", event.get("summary"), source_name)
            continue
        if dry_run:
            logger.info("Would remove %r (%s)", event.get("summary"), source_name)
            stats[source_name]["removed"] += 1
            continue
        if gcal.delete_managed(service, calendar_id=config.national_calendar_id, event=event):
            logger.info("Removed %r (%s)", event.get("summary"), source_name)
            stats[source_name]["removed"] += 1

    logger.info("--- summary%s ---", " (dry run, no writes)" if dry_run else "")
    total_changes = 0
    for source_name in sorted(stats):
        counts = stats[source_name]
        logger.info(
            "  %-40s added=%-4d updated=%-4d removed=%-4d unchanged=%-4d skipped=%d",
            source_name, counts["added"], counts["updated"], counts["removed"],
            counts["unchanged"], counts["skipped"],
        )
        total_changes += counts["added"] + counts["updated"] + counts["removed"]
    logger.info("  %d write(s) this run", total_changes)

    if failed_sources:
        logger.warning("%d source(s) failed and were skipped: %s",
                       len(failed_sources), ", ".join(sorted(failed_sources)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate DSA feeds into the National / Regional calendar.")
    parser.add_argument("--dry-run", action="store_true", help="report what would change without writing")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    # googleapiclient is chatty and can echo request URLs; keep it quiet.
    logging.getLogger("googleapiclient").setLevel(logging.ERROR)

    try:
        config = load_config()
        sources = load_sources()
    except ConfigError as exc:
        logger.error("%s", exc)
        return 2

    return run(config, sources, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
