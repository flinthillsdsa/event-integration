"""Orchestrate one full, stateless, idempotent sync pass.

Action Network (tagged events)  ->  Google Calendar (per-hashtag routing)
                                ->  Discord scheduled events (optional)

Run locally:  python -m src.main
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

from .action_network import ActionNetworkClient, NormalizedEvent
from .config import Config, ConfigError, load_config
from .discord import DiscordClient
from .google_calendar import GoogleCalendarClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("sync")


class Counters:
    def __init__(self) -> None:
        self.created = self.updated = self.unchanged = 0
        self.cancelled = self.deleted = self.skipped = self.errors = 0
        self.discord_created = self.discord_updated = self.discord_deleted = 0

    def summary(self) -> str:
        return (
            f"calendar: {self.created} created, {self.updated} updated, "
            f"{self.unchanged} unchanged, {self.cancelled} cancelled, "
            f"{self.deleted} deleted | discord: {self.discord_created} created, "
            f"{self.discord_updated} updated, {self.discord_deleted} deleted | "
            f"{self.skipped} skipped, {self.errors} errors"
        )


def _select_calendars(event: NormalizedEvent, cfg: Config) -> list[str]:
    cal_ids: list[str] = []
    for tag in event.tags:
        cal_id = cfg.routing.get(tag)
        if cal_id and cal_id not in cal_ids:
            cal_ids.append(cal_id)
    return cal_ids


def run() -> int:
    try:
        cfg = load_config()
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        return 2

    routing_tags = set(cfg.routing.keys())
    s = cfg.settings
    now_utc = datetime.now(timezone.utc)

    an = ActionNetworkClient(
        cfg.action_network_api_key, s.default_timezone, s.default_duration_minutes
    )
    gcal = GoogleCalendarClient(cfg.google_service_account_info, s.default_timezone)
    logger.info("Google service account: %s", gcal.service_account_email)

    discord: DiscordClient | None = None
    discord_existing: dict[str, str] = {}
    if cfg.discord.enabled:
        discord = DiscordClient(cfg.discord.bot_token, cfg.discord.guild_id)
        discord_existing = discord.list_events()
        logger.info("Discord enabled; %d existing scheduled events", len(discord_existing))

    raw_events = an.fetch_events(s.past_window_days)

    counters = Counters()
    # Track what we synced this pass, for disappearance detection.
    synced_by_calendar: dict[str, set[str]] = {cal: set() for cal in cfg.routing.values()}
    present_active_uuids: set[str] = set()   # active+tagged, seen this run (any time)
    discord_synced: set[str] = set()

    for raw in raw_events:
        try:
            event = an.normalize(raw, routing_tags)
            if event is None:
                counters.skipped += 1
                continue

            if s.only_tagged and not event.tags:
                logger.debug("Skipping untagged event: %s", event.title)
                counters.skipped += 1
                continue

            calendars = _select_calendars(event, cfg)
            if not calendars:
                counters.skipped += 1
                continue

            # ---- Cancelled upstream ----
            if event.is_cancelled:
                _handle_cancelled(event, calendars, cfg, gcal, discord, discord_existing, counters)
                continue

            present_active_uuids.add(event.uuid)

            # ---- Google Calendar upsert (fan out to each routed calendar) ----
            for cal_id in calendars:
                try:
                    action = gcal.upsert(cal_id, event)
                    synced_by_calendar[cal_id].add(event.uuid)
                    _tally_upsert(counters, action)
                    logger.info(
                        "[%s] %s -> %s", action, event.title,
                        ",".join(cfg.routing_by_calendar.get(cal_id, [cal_id])),
                    )
                except Exception:  # one bad calendar write must not abort the run
                    logger.exception("Calendar upsert failed for %s on %s", event.uuid, cal_id)
                    counters.errors += 1

            # ---- Discord (future events only) ----
            if discord is not None:
                if event.start_utc <= now_utc:
                    logger.debug("Discord: skipping past event %s", event.title)
                else:
                    _sync_discord(event, discord, discord_existing, discord_synced, counters)

        except Exception:
            logger.exception("Unhandled error processing an event; skipping it")
            counters.errors += 1

    _handle_disappearances(
        cfg, gcal, discord, synced_by_calendar, present_active_uuids,
        discord_synced, discord_existing, now_utc, counters,
    )

    logger.info("Sync complete — %s", counters.summary())
    return 0


def _tally_upsert(counters: Counters, action: str) -> None:
    if action == "created":
        counters.created += 1
    elif action == "updated":
        counters.updated += 1
    elif action == "unchanged":
        counters.unchanged += 1


def _handle_cancelled(event, calendars, cfg, gcal, discord, discord_existing, counters) -> None:
    for cal_id in calendars:
        try:
            if cfg.settings.on_cancelled == "delete":
                if gcal.delete(cal_id, event.uuid):
                    counters.deleted += 1
            else:  # mark cancelled
                if gcal.cancel(cal_id, event.uuid):
                    counters.cancelled += 1
        except Exception:
            logger.exception("Cancel handling failed for %s on %s", event.uuid, cal_id)
            counters.errors += 1
    # A cancelled event should not linger in Discord either. Pop it from the
    # existing map so the disappearance pass does not try to delete it again.
    if discord is not None and event.uuid in discord_existing:
        if discord.delete(discord_existing.pop(event.uuid)):
            counters.discord_deleted += 1


def _sync_discord(event, discord, discord_existing, discord_synced, counters) -> None:
    try:
        existing_id = discord_existing.get(event.uuid)
        if existing_id is None:
            new_id = discord.create(event)
            if new_id:
                counters.discord_created += 1
                discord_synced.add(event.uuid)
        else:
            if discord.update(existing_id, event):
                counters.discord_updated += 1
            discord_synced.add(event.uuid)
    except Exception:
        logger.exception("Discord sync failed for %s", event.uuid)
        counters.errors += 1


def _handle_disappearances(
    cfg, gcal, discord, synced_by_calendar, present_active_uuids,
    discord_synced, discord_existing, now_utc, counters,
) -> None:
    if cfg.settings.on_disappeared != "delete":
        return

    time_min_iso = now_utc.isoformat()
    for cal_id in cfg.routing.values():
        try:
            managed = gcal.list_managed_future_uuids(cal_id, time_min_iso)
        except Exception:
            logger.exception("Could not list managed events on %s", cal_id)
            counters.errors += 1
            continue
        gone = set(managed.keys()) - synced_by_calendar.get(cal_id, set())
        for uuid in gone:
            try:
                if gcal.delete(cal_id, uuid):
                    counters.deleted += 1
                    logger.info("Deleted disappeared event %s from %s", uuid, cal_id)
            except Exception:
                logger.exception("Failed to delete disappeared %s on %s", uuid, cal_id)
                counters.errors += 1

    if discord is not None:
        # Delete Discord events whose AN event is no longer present (active) at all.
        gone = set(discord_existing.keys()) - present_active_uuids
        for uuid in gone:
            if discord.delete(discord_existing[uuid]):
                counters.discord_deleted += 1
                logger.info("Deleted disappeared Discord event for %s", uuid)


if __name__ == "__main__":
    sys.exit(run())
