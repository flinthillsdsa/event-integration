"""Google Calendar client: read, and idempotent upsert/reconcile of managed events.

Auth is a Google Cloud service account. The National / Regional calendar must be
shared with the service account's address with "Make changes to events"; the
chapter calendar only needs "See all event details".

Two rules keep this safe to run unattended:
  * every event we create carries extendedProperties.private.managedBy =
    "dsa-aggregator", and
  * we only ever patch or delete events that carry that property. Human-created
    events are invisible to the reconcile pass.

Reference: https://developers.google.com/calendar/api/v3/reference/events
  - event id must be base32hex: characters a-v and 0-9, length 5..1024
  - extendedProperties.private is a string->string map, queryable on list()
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re
from zoneinfo import ZoneInfo

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]
READONLY_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

MANAGED_BY_VALUE = "dsa-aggregator"
PROP_MANAGED_BY = "managedBy"
PROP_SOURCE = "sourceName"
PROP_SOURCE_UID = "sourceUid"
PROP_CONTENT_HASH = "contentHash"

_BASE32HEX_RE = re.compile(r"^[0-9a-v]{5,1024}$")
_PAGE_SIZE = 250


def build_service(service_account_info: dict, *, readonly: bool = False):
    credentials = service_account.Credentials.from_service_account_info(
        service_account_info, scopes=READONLY_SCOPES if readonly else SCOPES
    )
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def service_account_email(service_account_info: dict) -> str:
    """The identity a calendar must be shared with. Safe to log -- not a secret."""
    return service_account_info.get("client_email", "(unknown)")


# --------------------------------------------------------------------------
# Ids and hashing
# --------------------------------------------------------------------------

def derive_event_id(source_name: str, source_uid: str) -> str:
    """Deterministic, valid Google event id for a (source, uid) pair.

    A hex digest uses only 0-9a-f, which is a subset of base32hex's 0-9a-v, so
    the result is always legal. Deterministic means a re-run updates the same
    event instead of creating a second copy.
    """
    digest = hashlib.sha256(f"{source_name}\x00{source_uid}".encode("utf-8")).hexdigest()[:40]
    event_id = f"agg{digest}"
    if not _BASE32HEX_RE.match(event_id):  # pragma: no cover - structurally impossible
        raise ValueError(f"derived id is not base32hex: {event_id!r}")
    return event_id


def content_hash(body: dict) -> str:
    """Stable hash of the fields we write, so unchanged events are not patched."""
    parts = [
        body.get("summary", ""),
        body.get("description", ""),
        body.get("location", ""),
        str((body.get("start") or {}).get("dateTime") or (body.get("start") or {}).get("date") or ""),
        str((body.get("end") or {}).get("dateTime") or (body.get("end") or {}).get("date") or ""),
    ]
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

def parse_event_time(node: dict | None, tzinfo: ZoneInfo) -> tuple[dt.datetime | None, bool]:
    """Parse a Calendar API start/end node. Returns (aware datetime, all_day)."""
    if not node:
        return None, False
    if node.get("dateTime"):
        parsed = dt.datetime.fromisoformat(node["dateTime"].replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tzinfo)
        return parsed, False
    if node.get("date"):
        day = dt.date.fromisoformat(node["date"])
        return dt.datetime(day.year, day.month, day.day, tzinfo=tzinfo), True
    return None, False


def list_events(service, *, calendar_id: str, time_min: dt.datetime, time_max: dt.datetime,
                private_extended_property: str | None = None) -> list[dict]:
    """List single (recurrence-expanded) events in a window, following pages."""
    items: list[dict] = []
    page_token = None
    while True:
        request = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min.isoformat(),
            timeMax=time_max.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=_PAGE_SIZE,
            pageToken=page_token,
            showDeleted=False,
            privateExtendedProperty=private_extended_property,
        )
        response = request.execute()
        items.extend(response.get("items", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return items


def list_managed_events(service, *, calendar_id: str, time_min: dt.datetime,
                        time_max: dt.datetime) -> dict[str, dict]:
    """Every aggregator-created event in the window, keyed by event id."""
    items = list_events(
        service,
        calendar_id=calendar_id,
        time_min=time_min,
        time_max=time_max,
        private_extended_property=f"{PROP_MANAGED_BY}={MANAGED_BY_VALUE}",
    )
    return {item["id"]: item for item in items if item.get("id")}


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

def _is_managed(event: dict) -> bool:
    private = (event.get("extendedProperties") or {}).get("private") or {}
    return private.get(PROP_MANAGED_BY) == MANAGED_BY_VALUE


def upsert(service, *, calendar_id: str, event_id: str, body: dict,
           existing: dict | None) -> str:
    """Insert or patch one managed event. Returns "added" | "updated" | "unchanged"."""
    body = dict(body)
    private = dict((body.get("extendedProperties") or {}).get("private") or {})
    private[PROP_MANAGED_BY] = MANAGED_BY_VALUE
    private[PROP_CONTENT_HASH] = content_hash(body)
    body["extendedProperties"] = {"private": private}

    if existing is None:
        body["id"] = event_id
        try:
            service.events().insert(calendarId=calendar_id, body=body, sendUpdates="none").execute()
            return "added"
        except HttpError as exc:
            if exc.resp.status != 409:
                raise
            # 409 means the id exists -- typically an event we previously created
            # and that was then cancelled/trashed. Patching revives it in place.
            service.events().patch(
                calendarId=calendar_id, eventId=event_id,
                body={**body, "status": "confirmed"}, sendUpdates="none",
            ).execute()
            return "updated"

    if not _is_managed(existing):
        # Defensive: never touch anything that is not ours, even on an id collision.
        logger.warning("Refusing to modify unmanaged event %s on %s", event_id, calendar_id)
        return "unchanged"

    existing_private = (existing.get("extendedProperties") or {}).get("private") or {}
    if existing_private.get(PROP_CONTENT_HASH) == private[PROP_CONTENT_HASH] \
            and existing.get("status") != "cancelled":
        return "unchanged"

    service.events().patch(
        calendarId=calendar_id, eventId=event_id,
        body={**body, "status": "confirmed"}, sendUpdates="none",
    ).execute()
    return "updated"


def delete_managed(service, *, calendar_id: str, event: dict) -> bool:
    """Delete an event, but only if it carries our managedBy tag."""
    if not _is_managed(event):
        logger.warning("Refusing to delete unmanaged event %s on %s", event.get("id"), calendar_id)
        return False
    try:
        service.events().delete(
            calendarId=calendar_id, eventId=event["id"], sendUpdates="none"
        ).execute()
        return True
    except HttpError as exc:
        if exc.resp.status in (404, 410):
            return False  # already gone
        raise
