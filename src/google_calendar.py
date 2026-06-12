"""Google Calendar client: deterministic, idempotent upsert / cancel / delete.

Auth: Google Cloud service account (server-to-server). The target calendars must
be shared with the service account's email with "Make changes to events".

Docs consulted:
  https://developers.google.com/calendar/api/v3/reference/events/insert
    - event id: base32hex chars (a-v, 0-9), length 5..1024, unique per calendar
    - extendedProperties.private: string key/value map
"""

from __future__ import annotations

import hashlib
import logging
import re

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]
_BASE32HEX_RE = re.compile(r"^[0-9a-v]{5,1024}$")

# extendedProperties.private keys we own.
PROP_MANAGED = "anManaged"
PROP_UUID = "anUuid"
PROP_HASH = "anContentHash"


def calendar_event_id(uuid: str) -> str:
    """Derive a deterministic, valid Google Calendar event id from an AN UUID.

    AN UUIDs are hyphenated hex; stripping hyphens yields base32hex-valid chars.
    Prefix with 'an' so the id never starts in a way Google dislikes.
    """
    candidate = "an" + uuid.replace("-", "").lower()
    if not _BASE32HEX_RE.match(candidate):
        raise ValueError(f"Derived calendar id is not valid base32hex: {candidate!r}")
    return candidate


def content_hash(event, location_str: str, description: str) -> str:
    """Stable hash over the fields we write, to skip no-op patches."""
    payload = "".join(
        [
            event.title,
            description,
            location_str,
            event.start_wall.isoformat(),
            event.end_wall.isoformat(),
            event.status,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class GoogleCalendarClient:
    def __init__(self, service_account_info: dict, default_timezone: str):
        try:
            creds = service_account.Credentials.from_service_account_info(
                service_account_info, scopes=SCOPES
            )
        except Exception as exc:  # malformed key -> fail loudly
            raise RuntimeError(f"Invalid Google service account key: {exc}") from exc
        self.service_account_email = service_account_info.get("client_email", "<unknown>")
        self.default_timezone = default_timezone
        # cache_discovery=False avoids noisy warnings in ephemeral CI runs.
        self.service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    # ---- body construction --------------------------------------------------

    def _build_body(self, event, description: str, chash: str) -> dict:
        body = {
            "summary": event.title,
            "description": description,
            "start": {"dateTime": event.start_wall.isoformat(), "timeZone": self.default_timezone},
            "end": {"dateTime": event.end_wall.isoformat(), "timeZone": self.default_timezone},
            "status": "cancelled" if event.is_cancelled else "confirmed",
            "extendedProperties": {
                "private": {
                    PROP_MANAGED: "true",
                    PROP_UUID: event.uuid,
                    PROP_HASH: chash,
                }
            },
        }
        if event.location_str:
            body["location"] = event.location_str
        return body

    @staticmethod
    def _build_description(event) -> str:
        """Plain-ish description with an appended RSVP link (Section 4)."""
        body = event.description_text
        if event.browser_url:
            rsvp = f"RSVP: {event.browser_url}"
            body = f"{body}\n\n{rsvp}" if body else rsvp
        return body

    # ---- upsert -------------------------------------------------------------

    def upsert(self, calendar_id: str, event) -> str:
        """Insert or patch the event on one calendar. Returns an action label."""
        gcal_id = calendar_event_id(event.uuid)
        description = self._build_description(event)
        chash = content_hash(event, event.location_str, description)
        body = self._build_body(event, description, chash)

        try:
            existing = (
                self.service.events()
                .get(calendarId=calendar_id, eventId=gcal_id)
                .execute()
            )
        except HttpError as exc:
            if exc.resp.status == 404:
                body["id"] = gcal_id
                self.service.events().insert(calendarId=calendar_id, body=body).execute()
                return "created"
            raise

        existing_hash = (
            existing.get("extendedProperties", {}).get("private", {}).get(PROP_HASH)
        )
        existing_status = existing.get("status")
        target_status = body["status"]
        if existing_hash == chash and existing_status == target_status:
            return "unchanged"

        self.service.events().patch(
            calendarId=calendar_id, eventId=gcal_id, body=body
        ).execute()
        return "updated"

    def cancel(self, calendar_id: str, uuid: str) -> bool:
        """Mark an existing event cancelled. Returns False if it does not exist."""
        gcal_id = calendar_event_id(uuid)
        try:
            self.service.events().patch(
                calendarId=calendar_id, eventId=gcal_id, body={"status": "cancelled"}
            ).execute()
            return True
        except HttpError as exc:
            if exc.resp.status == 404:
                return False
            raise

    def delete(self, calendar_id: str, uuid: str) -> bool:
        gcal_id = calendar_event_id(uuid)
        try:
            self.service.events().delete(calendarId=calendar_id, eventId=gcal_id).execute()
            return True
        except HttpError as exc:
            if exc.resp.status in (404, 410):
                return False
            raise

    def list_managed_future_uuids(self, calendar_id: str, time_min_iso: str) -> dict[str, str]:
        """Map anUuid -> gcal event id for our managed events starting >= now.

        Used to detect events that disappeared upstream. Only future events are
        considered so naturally-aged past events are never deleted.
        """
        result: dict[str, str] = {}
        page_token = None
        while True:
            resp = (
                self.service.events()
                .list(
                    calendarId=calendar_id,
                    privateExtendedProperty=f"{PROP_MANAGED}=true",
                    timeMin=time_min_iso,
                    singleEvents=True,
                    showDeleted=False,
                    maxResults=2500,
                    pageToken=page_token,
                )
                .execute()
            )
            for item in resp.get("items", []):
                if item.get("status") == "cancelled":
                    continue
                uuid = item.get("extendedProperties", {}).get("private", {}).get(PROP_UUID)
                if uuid:
                    result[uuid] = item["id"]
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return result
