"""Discord native Guild Scheduled Events client (entity_type EXTERNAL).

This creates/edits entries in the server's Events tab. It posts NO channel
messages. Webhooks cannot do this; a bot with the Manage Events permission is
required.

Because GitHub Actions is stateless, the Action Network UUID is embedded as a
discreet trailing marker line in each event description ("ref: an-<uuid>") and
parsed back on the next run to map AN events to Discord event ids.

Docs consulted:
  https://docs.discord.com/developers/resources/guild-scheduled-event
    entity_type EXTERNAL = 3, privacy_level GUILD_ONLY = 2, status CANCELED = 4
    entity_metadata.location required for EXTERNAL, 1..100 chars
    create returns HTTP 200 with the event object
"""

from __future__ import annotations

import logging
import re
import time

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://discord.com/api/v10"
ENTITY_TYPE_EXTERNAL = 3
PRIVACY_GUILD_ONLY = 2
STATUS_CANCELED = 4

MAX_DESCRIPTION = 1000
MAX_LOCATION = 100
MAX_RETRIES = 5

_MARKER_RE = re.compile(r"ref:\s*an-([0-9a-fA-F-]{8,})")


def marker_for(uuid: str) -> str:
    return f"ref: an-{uuid}"


def build_description(event) -> str:
    """Plain-text body + 'Register' link + UUID marker, <= 1000 chars."""
    marker = marker_for(event.uuid)
    body = event.description_text  # already HTML- and hashtag-stripped
    if event.browser_url:
        suffix = f"Register: {event.browser_url}"
        body = f"{body}\n\n{suffix}" if body else suffix

    marker_block = f"\n\nref: an-{event.uuid}"
    room = MAX_DESCRIPTION - len(marker_block)
    if len(body) > room:
        body = body[: max(0, room - 3)] + "..."
    return f"{body}{marker_block}" if body else marker.strip()


def build_location(event) -> str:
    loc = event.location_str or "TBD"
    return loc[:MAX_LOCATION]


class DiscordClient:
    def __init__(self, bot_token: str, guild_id: str):
        self.guild_id = guild_id
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": f"Bot {bot_token}", "Content-Type": "application/json"}
        )

    # ---- HTTP with 429 handling --------------------------------------------

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{API_BASE}{path}"
        for _ in range(MAX_RETRIES):
            resp = self.session.request(method, url, timeout=30, **kwargs)
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", 1))
                logger.warning("Discord rate limited; sleeping %.2fs", retry_after)
                time.sleep(retry_after)
                continue
            if resp.status_code in (401, 403):
                raise RuntimeError(
                    f"Discord auth/permission error ({resp.status_code}) on {method} {path}. "
                    f"Check DISCORD_BOT_TOKEN and the bot's Manage Events permission. "
                    f"Body: {resp.text[:200]}"
                )
            return resp
        raise RuntimeError(f"Discord: exhausted retries on {method} {path}")

    # ---- payload ------------------------------------------------------------

    def _payload(self, event) -> dict:
        return {
            "name": event.title[:100],
            "description": build_description(event),
            "scheduled_start_time": event.start_utc.isoformat(),
            "scheduled_end_time": event.end_utc.isoformat(),
            "privacy_level": PRIVACY_GUILD_ONLY,
            "entity_type": ENTITY_TYPE_EXTERNAL,
            "entity_metadata": {"location": build_location(event)},
        }

    # ---- CRUD ---------------------------------------------------------------

    def list_events(self) -> dict[str, str]:
        """Return a map of AN uuid -> Discord scheduled-event id (from markers)."""
        resp = self._request("GET", f"/guilds/{self.guild_id}/scheduled-events")
        resp.raise_for_status()
        mapping: dict[str, str] = {}
        for ev in resp.json():
            description = ev.get("description") or ""
            match = _MARKER_RE.search(description)
            if match:
                mapping[match.group(1)] = ev["id"]
        return mapping

    def create(self, event) -> str | None:
        resp = self._request(
            "POST", f"/guilds/{self.guild_id}/scheduled-events", json=self._payload(event)
        )
        if 200 <= resp.status_code < 300:
            return resp.json()["id"]
        logger.error("Discord create failed (%s): %s", resp.status_code, resp.text[:300])
        return None

    def update(self, event_id: str, event) -> bool:
        payload = self._payload(event)
        # entity_type and privacy_level are not patchable.
        payload.pop("entity_type", None)
        payload.pop("privacy_level", None)
        resp = self._request(
            "PATCH",
            f"/guilds/{self.guild_id}/scheduled-events/{event_id}",
            json=payload,
        )
        if 200 <= resp.status_code < 300:
            return True
        logger.error("Discord update failed (%s): %s", resp.status_code, resp.text[:300])
        return False

    def delete(self, event_id: str) -> bool:
        resp = self._request(
            "DELETE", f"/guilds/{self.guild_id}/scheduled-events/{event_id}"
        )
        if resp.status_code in (200, 204):
            return True
        if resp.status_code == 404:
            return False
        logger.error("Discord delete failed (%s): %s", resp.status_code, resp.text[:300])
        return False
