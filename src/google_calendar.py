"""Google Calendar read client.

Auth is a Google Cloud service account, used read-only. The chapter calendar
must be shared with the service account's address with "See all event details".
"""

from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

READONLY_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
_PAGE_SIZE = 250


class CalendarAccessError(RuntimeError):
    """A calendar is missing, or is not shared with the service account."""


def build_service(service_account_info: dict, *, readonly: bool = True):
    credentials = service_account.Credentials.from_service_account_info(
        service_account_info, scopes=READONLY_SCOPES
    )
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def service_account_email(service_account_info: dict) -> str:
    """The identity a calendar must be shared with. Safe to log -- not a secret."""
    return service_account_info.get("client_email", "(unknown)")


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


def check_access(service, *, calendar_id: str, sa_email: str, label: str,
                 need_write: bool = False) -> None:
    """Fail fast, and legibly, when a calendar is not reachable.

    The Calendar API answers 404 (not 403) for a calendar the caller cannot see,
    which reads as "wrong id" when the usual cause is "not shared yet". Say both.
    need_write is accepted for call-site clarity but this client only ever reads.
    """
    try:
        service.calendars().get(calendarId=calendar_id).execute()
    except HttpError as exc:
        if exc.resp.status not in (403, 404):
            raise
        raise CalendarAccessError(
            f"Cannot reach the {label} calendar ({calendar_id}).\n"
            f"  Google answered {exc.resp.status}, which means either the calendar ID is wrong "
            f"or it has never been shared with this service account.\n"
            f"  Fix: open that calendar in Google Calendar -> Settings -> "
            f"'Share with specific people or groups', add\n"
            f"      {sa_email}\n"
            f"  with 'See all event details'. Sharing can take a minute to take effect."
        ) from exc


def list_events(service, *, calendar_id: str, time_min: dt.datetime,
                time_max: dt.datetime) -> list[dict]:
    """List single (recurrence-expanded) events in a window, following pages."""
    items: list[dict] = []
    page_token = None
    while True:
        response = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min.isoformat(),
            timeMax=time_max.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=_PAGE_SIZE,
            pageToken=page_token,
            showDeleted=False,
        ).execute()
        items.extend(response.get("items", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return items
