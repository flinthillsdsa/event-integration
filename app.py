#!/usr/bin/env python3
"""
Action Network → TeamUp + Discord Integration Service
Three-way sync: Action Network → TeamUp (hashtag subcalendar routing) + Discord
Version 5.0 - Restored hashtag routing, Railway fixes, code cleanup
"""

import re
import threading
import time
import logging
import os
import json
from datetime import datetime, timezone, timedelta

from flask import Flask, request, jsonify
import requests
import pytz

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Configuration  (set all of these as Railway environment variables)
# ---------------------------------------------------------------------------

TEAMUP_API_KEY         = os.environ.get('TEAMUP_API_KEY')
TEAMUP_CALENDAR_KEY    = os.environ.get('TEAMUP_CALENDAR_KEY')
ACTION_NETWORK_API_KEY = os.environ.get('ACTION_NETWORK_API_KEY')
DISCORD_BOT_TOKEN      = os.environ.get('DISCORD_BOT_TOKEN')
DISCORD_GUILD_ID       = (
    int(os.environ.get('DISCORD_GUILD_ID'))
    if os.environ.get('DISCORD_GUILD_ID') else None
)

# Optional: set SYNC_SECRET in Railway to protect write endpoints.
# Callers must then include the header:  X-Sync-Secret: <your secret>
SYNC_SECRET = os.environ.get('SYNC_SECRET')

# ---------------------------------------------------------------------------
# Subcalendar routing
# ---------------------------------------------------------------------------
# Map hashtag → TeamUp subcalendar ID.
# Add a hashtag anywhere in an Action Network event description to route it.
# Events with no matching hashtag fall back to DEFAULT_SUBCALENDAR_ID.

SUBCALENDAR_IDS: dict[str, int] = {
    '#community':  14816009,   # Community Involvement & Volunteer Initiatives Committee
    '#meeting':    14502151,   # Meetings
    '#outreach':   14816011,   # Outreach / Canvassing / Tabling
    '#education':  14815998,   # Political Education
    '#social':     14816002,   # Socials
}

DEFAULT_SUBCALENDAR_ID = 14502151  # Meetings (fallback)

# Human-readable names for logging
SUBCALENDAR_NAMES: dict[int, str] = {
    14816009: 'Community Involvement & Volunteer Initiatives',
    14502151: 'Meetings',
    14816011: 'Outreach/Canvassing/Tabling',
    14815998: 'Political Education',
    14816002: 'Socials',
}

# ---------------------------------------------------------------------------
# Thread-safe event mappings
# ---------------------------------------------------------------------------
# Stores the relationship between Action Network IDs and TeamUp/Discord IDs.
#
# ⚠️  This is in-memory only — mappings are lost on restart, which causes
#     duplicate events.  For a production fix, replace with a small Postgres
#     table (Railway has a free Postgres add-on) or a JSON file on a
#     persistent volume.
#
# Format: {action_network_id: {teamup_id, discord_id, last_modified, status, title, action_network_url}}

event_mappings: dict = {}
mappings_lock  = threading.Lock()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clean_description_for_display(description: str) -> str:
    """Strip routing hashtags from a description (they're internal-only)."""
    if not description:
        return description
    cleaned = re.sub(r'#\w+', '', description)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def build_discord_description(an_event: dict) -> str:
    """
    Build a plain-text description for Discord (max 1000 chars).
    Strips HTML tags and routing hashtags, appends RSVP link.
    """
    description = clean_description_for_display(an_event.get('description', ''))
    if description:
        description = re.sub(r'<[^>]+>', '', description)
        description = re.sub(r'\s+', ' ', description).strip()

    registration_url = an_event.get('browser_url', '')
    if registration_url:
        suffix = f"\n\nRSVP: {registration_url}"
        description = (description + suffix) if description else suffix.strip()

    if len(description) > 1000:
        description = description[:997] + '...'
    return description


def get_subcalendar_id(an_event: dict) -> int:
    """
    Return the TeamUp subcalendar ID by scanning the event description for
    a known routing hashtag.  Falls back to DEFAULT_SUBCALENDAR_ID.
    """
    description = an_event.get('description', '').lower()
    for hashtag, subcal_id in SUBCALENDAR_IDS.items():
        if hashtag in description:
            return subcal_id
    return DEFAULT_SUBCALENDAR_ID


def convert_to_utc(time_str: str) -> str | None:
    """
    Convert an Action Network timestamp to UTC ISO 8601.

    Action Network stores times in US/Central but appends 'Z' (incorrectly
    implying UTC).  We strip the Z, treat the value as Central Time, and
    convert to real UTC.
    """
    if not time_str:
        return None
    try:
        central = pytz.timezone('US/Central')

        if time_str.endswith('Z'):
            dt = datetime.fromisoformat(time_str[:-1])
            return central.localize(dt).astimezone(pytz.UTC).isoformat().replace('+00:00', 'Z')

        # Already has an explicit tz offset — leave as-is
        if '+' in time_str or '-' in time_str.split('T')[-1]:
            return time_str

        # No tz info — assume Central
        dt = datetime.fromisoformat(time_str)
        return central.localize(dt).astimezone(pytz.UTC).isoformat().replace('+00:00', 'Z')

    except Exception as e:
        logger.warning(f"⚠️  Error converting time '{time_str}': {e}")
        return time_str


def check_sync_secret():
    """
    If SYNC_SECRET is configured, verify the X-Sync-Secret request header.
    Returns a 401 response tuple on failure, or None on success.
    """
    if not SYNC_SECRET:
        return None  # No protection configured
    if request.headers.get('X-Sync-Secret', '') != SYNC_SECRET:
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401
    return None


# ---------------------------------------------------------------------------
# Sync Service
# ---------------------------------------------------------------------------

class ActionNetworkTeamUpDiscordSync:

    def __init__(self):
        self.teamup_base_url = f"https://api.teamup.com/{TEAMUP_CALENDAR_KEY}"
        self.teamup_headers = {
            'Teamup-Token': TEAMUP_API_KEY,
            'Content-Type': 'application/json',
        }
        self.action_network_headers = {
            'OSDI-API-Token': ACTION_NETWORK_API_KEY,
            'Content-Type': 'application/json',
        }
        self.action_network_base_url = 'https://actionnetwork.org/api/v2'

    # ------------------------------------------------------------------ #
    # Action Network                                                       #
    # ------------------------------------------------------------------ #

    def fetch_action_network_events(self, limit: int = 100) -> list:
        """Fetch all events from Action Network, following pagination links."""
        all_events: list = []
        url: str | None = f"{self.action_network_base_url}/events"
        params = {'limit': limit}

        try:
            while url:
                logger.info(f"Fetching Action Network events: {url}")
                response = requests.get(url, headers=self.action_network_headers, params=params)
                params = {}  # only send on first request

                if response.status_code != 200:
                    logger.error(f"❌ Action Network fetch failed: {response.status_code} — {response.text}")
                    break

                data = response.json()
                page_events = data.get('_embedded', {}).get('osdi:events', [])
                all_events.extend(page_events)

                # Follow next-page link if present
                url = data.get('_links', {}).get('next', {}).get('href')

            logger.info(f"✅ Fetched {len(all_events)} events from Action Network")
            return all_events

        except Exception as e:
            logger.error(f"❌ Error fetching Action Network events: {e}")
            return []

    @staticmethod
    def extract_event_id(an_event: dict) -> str | None:
        """Extract a stable unique ID from an Action Network event dict."""
        for identifier in an_event.get('identifiers', []):
            if isinstance(identifier, str):
                return identifier.split(':')[-1]
        event_id = an_event.get('id')
        if event_id:
            return str(event_id)
        browser_url = an_event.get('browser_url', '')
        if browser_url:
            return browser_url.rstrip('/').split('/')[-1]
        return None

    # ------------------------------------------------------------------ #
    # Transform                                                            #
    # ------------------------------------------------------------------ #

    def transform_action_network_event(self, an_event: dict) -> dict | None:
        """Convert an Action Network event dict to TeamUp API format."""
        try:
            title = an_event.get('title', 'Untitled Event')

            # Build display description (hashtags removed, RSVP link added)
            display_description = clean_description_for_display(an_event.get('description', ''))
            registration_url = an_event.get('browser_url', '')

            if registration_url:
                rsvp_html = f'<a href="{registration_url}" target="_blank">RSVP</a>'
                enhanced_description = (
                    f"{display_description}\n\n{rsvp_html}" if display_description else rsvp_html
                )
            else:
                enhanced_description = display_description

            # Times
            start_date = an_event.get('start_date') or an_event.get('start_time')
            end_date   = an_event.get('end_date')   or an_event.get('end_time')
            start_date = convert_to_utc(start_date)
            end_date   = convert_to_utc(end_date)

            if start_date and not end_date:
                try:
                    start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                    end_date = (start_dt + timedelta(hours=2)).isoformat().replace('+00:00', 'Z')
                except Exception:
                    end_date = start_date

            # Location
            location = an_event.get('location', {})
            location_str = ''
            if isinstance(location, dict):
                parts: list[str] = []
                if location.get('venue'):
                    parts.append(location['venue'])
                parts.extend(location.get('address_lines', []))
                for key in ('locality', 'region', 'postal_code'):
                    if location.get(key):
                        parts.append(location[key])
                location_str = ', '.join(parts)
            elif isinstance(location, str):
                location_str = location

            # Subcalendar routing via hashtag
            subcalendar_id = get_subcalendar_id(an_event)

            teamup_event = {
                'subcalendar_ids': [subcalendar_id],
                'title':           title,
                'notes':           enhanced_description,
                'start_dt':        start_date,
                'end_dt':          end_date,
                'all_day':         False,
            }
            if location_str:
                teamup_event['location'] = location_str

            return teamup_event

        except Exception as e:
            logger.error(f"❌ Error transforming event: {e}")
            return None

    # ------------------------------------------------------------------ #
    # TeamUp CRUD                                                          #
    # ------------------------------------------------------------------ #

    def create_teamup_event(self, event_data: dict) -> dict | None:
        try:
            response = requests.post(
                f"{self.teamup_base_url}/events",
                headers=self.teamup_headers,
                json=event_data,
            )
            if response.status_code == 201:
                logger.info(f"✅ Created TeamUp event: {event_data.get('title')}")
                return response.json()
            logger.error(f"❌ TeamUp create failed: {response.status_code} — {response.text}")
            return None
        except Exception as e:
            logger.error(f"❌ Error creating TeamUp event: {e}")
            return None

    def update_teamup_event(self, teamup_event_id: str, event_data: dict) -> dict | None:
        try:
            payload = {**event_data, 'id': teamup_event_id}
            response = requests.put(
                f"{self.teamup_base_url}/events/{teamup_event_id}",
                headers=self.teamup_headers,
                json=payload,
            )
            if response.status_code == 200:
                logger.info(f"✅ Updated TeamUp event: {event_data.get('title')}")
                return response.json()
            logger.error(f"❌ TeamUp update failed: {response.status_code} — {response.text}")
            return None
        except Exception as e:
            logger.error(f"❌ Error updating TeamUp event: {e}")
            return None

    def delete_teamup_event(self, teamup_event_id: str) -> bool:
        try:
            response = requests.delete(
                f"{self.teamup_base_url}/events/{teamup_event_id}",
                headers=self.teamup_headers,
            )
            if response.status_code == 204:
                logger.info(f"✅ Deleted TeamUp event: {teamup_event_id}")
                return True
            logger.error(f"❌ TeamUp delete failed: {response.status_code} — {response.text}")
            return False
        except Exception as e:
            logger.error(f"❌ Error deleting TeamUp event: {e}")
            return False

    # ------------------------------------------------------------------ #
    # Discord CRUD                                                         #
    # ------------------------------------------------------------------ #

    def _discord_headers(self) -> dict:
        return {
            'Authorization': f'Bot {DISCORD_BOT_TOKEN}',
            'Content-Type': 'application/json',
        }

    def _discord_payload(self, an_event: dict, teamup_data: dict) -> dict:
        return {
            'name':                  teamup_data['title'],
            'description':           build_discord_description(an_event),
            'scheduled_start_time':  teamup_data['start_dt'],
            'scheduled_end_time':    teamup_data.get('end_dt', teamup_data['start_dt']),
            'privacy_level':         2,   # GUILD_ONLY
            'entity_type':           3,   # EXTERNAL
            'entity_metadata': {
                'location': teamup_data.get('location') or 'TBD',
            },
        }

    def create_discord_event(self, an_event: dict, teamup_data: dict) -> str | None:
        if not DISCORD_BOT_TOKEN or not DISCORD_GUILD_ID:
            logger.warning('⚠️  Discord not configured — skipping')
            return None
        try:
            response = requests.post(
                f"https://discord.com/api/v10/guilds/{DISCORD_GUILD_ID}/scheduled-events",
                headers=self._discord_headers(),
                json=self._discord_payload(an_event, teamup_data),
            )
            if response.status_code == 200:
                discord_id = response.json()['id']
                logger.info(f"🎮 Created Discord event: {teamup_data['title']} ({discord_id})")
                return discord_id
            logger.error(f"❌ Discord create failed: {response.status_code} — {response.text}")
            return None
        except Exception as e:
            logger.error(f"❌ Error creating Discord event: {e}")
            return None

    def update_discord_event(self, discord_event_id: str, an_event: dict, teamup_data: dict) -> str | None:
        if not DISCORD_BOT_TOKEN or not DISCORD_GUILD_ID:
            return None
        try:
            payload = self._discord_payload(an_event, teamup_data)
            # privacy_level and entity_type are not patchable
            payload.pop('privacy_level', None)
            payload.pop('entity_type', None)
            response = requests.patch(
                f"https://discord.com/api/v10/guilds/{DISCORD_GUILD_ID}/scheduled-events/{discord_event_id}",
                headers=self._discord_headers(),
                json=payload,
            )
            if response.status_code == 200:
                logger.info(f"🎮 Updated Discord event: {teamup_data['title']}")
                return discord_event_id
            logger.error(f"❌ Discord update failed: {response.status_code} — {response.text}")
            return None
        except Exception as e:
            logger.error(f"❌ Error updating Discord event: {e}")
            return None

    def delete_discord_event(self, discord_event_id: str) -> bool:
        if not DISCORD_BOT_TOKEN or not DISCORD_GUILD_ID:
            return False
        try:
            response = requests.delete(
                f"https://discord.com/api/v10/guilds/{DISCORD_GUILD_ID}/scheduled-events/{discord_event_id}",
                headers=self._discord_headers(),
            )
            if response.status_code == 204:
                logger.info(f"🎮 Deleted Discord event: {discord_event_id}")
                return True
            logger.error(f"❌ Discord delete failed: {response.status_code} — {response.text}")
            return False
        except Exception as e:
            logger.error(f"❌ Error deleting Discord event: {e}")
            return False

    # ------------------------------------------------------------------ #
    # Full sync                                                            #
    # ------------------------------------------------------------------ #

    def sync_events(self) -> dict:
        """Three-way sync: Action Network → TeamUp + Discord."""
        logger.info('🔄 Starting sync (Action Network → TeamUp + Discord)...')
        an_events = self.fetch_action_network_events()
        new_count = updated_count = deleted_count = 0

        for an_event in an_events:
            event_id = self.extract_event_id(an_event)
            if not event_id:
                logger.warning(f"⚠️  No ID for event: {an_event.get('title', 'No title')}")
                continue

            title         = an_event.get('title', 'No title')
            status        = an_event.get('status', 'confirmed')
            modified_date = an_event.get('modified_date', '')

            # --- Cancelled ---
            if status == 'cancelled':
                with mappings_lock:
                    stored = event_mappings.pop(event_id, None)
                if stored:
                    if stored.get('teamup_id') and self.delete_teamup_event(stored['teamup_id']):
                        deleted_count += 1
                    if stored.get('discord_id'):
                        self.delete_discord_event(stored['discord_id'])
                    logger.info(f"🗑️  Removed cancelled event: {title}")
                continue

            with mappings_lock:
                stored = event_mappings.get(event_id)

            # --- New event ---
            if stored is None:
                teamup_data = self.transform_action_network_event(an_event)
                if not teamup_data:
                    continue

                result = self.create_teamup_event(teamup_data)
                if not result:
                    logger.error(f"❌ Failed to create TeamUp event: {title}")
                    continue

                teamup_id  = result.get('event', {}).get('id')
                discord_id = self.create_discord_event(an_event, teamup_data)

                subcal_id   = teamup_data.get('subcalendar_ids', [DEFAULT_SUBCALENDAR_ID])[0]
                subcal_name = SUBCALENDAR_NAMES.get(subcal_id, 'Unknown')
                discord_label = ' + Discord' if discord_id else ''
                logger.info(f"📅 NEW: '{title}' → TeamUp/{subcal_name}{discord_label}")

                with mappings_lock:
                    event_mappings[event_id] = {
                        'teamup_id':          teamup_id,
                        'discord_id':         discord_id,
                        'last_modified':      modified_date,
                        'status':             status,
                        'title':              title,
                        'action_network_url': an_event.get('browser_url', ''),
                    }
                new_count += 1

            # --- Existing event ---
            else:
                needs_update = (
                    modified_date != stored.get('last_modified')
                    or status != stored.get('status')
                )
                if not needs_update:
                    logger.debug(f"✅ No changes: {title}")
                    continue

                teamup_data = self.transform_action_network_event(an_event)
                if not teamup_data:
                    continue

                tu_result = self.update_teamup_event(stored['teamup_id'], teamup_data)
                if stored.get('discord_id'):
                    self.update_discord_event(stored['discord_id'], an_event, teamup_data)

                if tu_result:
                    with mappings_lock:
                        event_mappings[event_id].update({
                            'last_modified': modified_date,
                            'status':        status,
                            'title':         title,
                        })
                    updated_count += 1
                    logger.info(f"✅ UPDATED: '{title}'")
                else:
                    logger.error(f"❌ Failed to update event: {title}")

        logger.info(
            f"🔄 Sync complete — {new_count} new, {updated_count} updated, {deleted_count} deleted"
        )
        return {'new_events': new_count, 'updated_events': updated_count, 'deleted_events': deleted_count}

    # ------------------------------------------------------------------ #
    # Connection tests                                                     #
    # ------------------------------------------------------------------ #

    def test_action_network_connection(self) -> bool:
        try:
            r = requests.get(
                f"{self.action_network_base_url}/events",
                headers=self.action_network_headers,
                params={'limit': 1},
            )
            return r.status_code == 200
        except Exception:
            return False

    def test_teamup_connection(self) -> bool:
        try:
            r = requests.get(
                f"{self.teamup_base_url}/events",
                headers=self.teamup_headers,
                params={'limit': 1},
            )
            return r.status_code == 200
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Initialize & background sync
# ---------------------------------------------------------------------------

sync_service = ActionNetworkTeamUpDiscordSync()


def background_sync():
    while True:
        try:
            time.sleep(30 * 60)  # 30 minutes
            sync_service.sync_events()
        except Exception as e:
            logger.error(f"❌ Background sync error: {e}")


if ACTION_NETWORK_API_KEY:
    t = threading.Thread(target=background_sync, daemon=True)
    t.start()
    logger.info('🔄 Background sync thread started (every 30 minutes)')


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        'service':  'Action Network → TeamUp + Discord Sync',
        'version':  '5.0',
        'status':   'running',
        'sync_interval': '30 minutes',
        'subcalendar_routing': {
            hashtag: SUBCALENDAR_NAMES.get(sid, str(sid))
            for hashtag, sid in SUBCALENDAR_IDS.items()
        },
        'default_subcalendar': 'Meetings (no hashtag)',
        'platforms': {
            'action_network': 'source',
            'teamup':         'configured' if (TEAMUP_API_KEY and TEAMUP_CALENDAR_KEY) else 'not configured',
            'discord':        'configured' if (DISCORD_BOT_TOKEN and DISCORD_GUILD_ID) else 'not configured',
        },
        'endpoints': {
            'GET  /':                      'this page',
            'GET  /health':                'connection health check',
            'GET  /status':                'sync status',
            'GET  /mappings':              'event mappings',
            'GET  /debug/mappings':        'detailed mappings',
            'GET  /debug/action-network':  'Action Network API debug',
            'POST /sync':                  'trigger manual sync',
            'POST /force-update/<id>':     'force-update one event',
            'POST /clear-mappings':        'clear all mappings',
        },
    })


@app.route('/health', methods=['GET'])
def health_check():
    teamup_ok  = bool(TEAMUP_API_KEY and TEAMUP_CALENDAR_KEY)
    an_ok      = bool(ACTION_NETWORK_API_KEY)
    discord_ok = bool(DISCORD_BOT_TOKEN and DISCORD_GUILD_ID)

    an_conn     = sync_service.test_action_network_connection() if an_ok     else False
    teamup_conn = sync_service.test_teamup_connection()         if teamup_ok else False
    discord_conn = False
    if discord_ok:
        try:
            r = requests.get(
                f"https://discord.com/api/v10/guilds/{DISCORD_GUILD_ID}",
                headers={'Authorization': f'Bot {DISCORD_BOT_TOKEN}'},
            )
            discord_conn = r.status_code == 200
        except Exception:
            pass

    return jsonify({
        'status':    'healthy',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'config': {
            'teamup_configured':         teamup_ok,
            'action_network_configured': an_ok,
            'discord_configured':        discord_ok,
        },
        'connections': {
            'teamup':         teamup_conn,
            'action_network': an_conn,
            'discord':        discord_conn,
        },
        'mapped_events': len(event_mappings),
    })


@app.route('/sync', methods=['POST'])
def manual_sync():
    err = check_sync_secret()
    if err:
        return err
    try:
        result = sync_service.sync_events()
        if 'error' in result:
            return jsonify({'status': 'error', 'message': result['error']}), 500
        return jsonify({
            'status': 'success',
            'result': result,
            'total_mapped_events': len(event_mappings),
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/status', methods=['GET'])
def sync_status():
    return jsonify({
        'total_mapped_events': len(event_mappings),
        'sync_interval':       '30 minutes',
        'teamup_configured':   bool(TEAMUP_API_KEY and TEAMUP_CALENDAR_KEY),
        'discord_configured':  bool(DISCORD_BOT_TOKEN and DISCORD_GUILD_ID),
        'subcalendar_routing': {
            hashtag: SUBCALENDAR_NAMES.get(sid, str(sid))
            for hashtag, sid in SUBCALENDAR_IDS.items()
        },
    })


@app.route('/mappings', methods=['GET'])
def event_mappings_route():
    with mappings_lock:
        return jsonify({'total_events': len(event_mappings), 'mappings': dict(event_mappings)})


@app.route('/debug/mappings', methods=['GET'])
def debug_mappings():
    with mappings_lock:
        data = {
            an_id: {
                'teamup_id':          m.get('teamup_id'),
                'discord_id':         m.get('discord_id'),
                'last_modified':      m.get('last_modified'),
                'status':             m.get('status'),
                'title':              m.get('title', 'Unknown'),
                'action_network_url': m.get('action_network_url', ''),
            }
            for an_id, m in event_mappings.items()
        }
    return jsonify({'total_mappings': len(data), 'detailed_mappings': data})


@app.route('/debug/action-network', methods=['GET'])
def debug_action_network():
    results: dict = {}

    try:
        r = requests.get(
            f"{sync_service.action_network_base_url}/events",
            headers=sync_service.action_network_headers,
        )
        results['events_endpoint'] = {
            'status_code': r.status_code,
            'response':    r.json() if r.status_code == 200 else r.text[:500],
        }
    except Exception as e:
        results['events_endpoint'] = {'error': str(e)}

    # NOTE: API key value is intentionally not included here
    results['config'] = {
        'action_network_configured': bool(ACTION_NETWORK_API_KEY),
        'teamup_configured':         bool(TEAMUP_API_KEY and TEAMUP_CALENDAR_KEY),
        'discord_configured':        bool(DISCORD_BOT_TOKEN and DISCORD_GUILD_ID),
    }

    return jsonify(results)


@app.route('/force-update/<event_id>', methods=['POST'])
def force_update_event(event_id):
    err = check_sync_secret()
    if err:
        return err

    with mappings_lock:
        if event_id not in event_mappings:
            return jsonify({'status': 'error', 'message': f'Event {event_id} not in mappings'}), 404

    an_events = sync_service.fetch_action_network_events(limit=100)
    target = next(
        (e for e in an_events if sync_service.extract_event_id(e) == event_id),
        None,
    )
    if not target:
        return jsonify({'status': 'error', 'message': f'Event {event_id} not found in Action Network'}), 404

    teamup_data = sync_service.transform_action_network_event(target)
    if not teamup_data:
        return jsonify({'status': 'error', 'message': 'Failed to transform event'}), 500

    with mappings_lock:
        stored = event_mappings[event_id]

    updated = []
    if stored.get('teamup_id') and sync_service.update_teamup_event(stored['teamup_id'], teamup_data):
        updated.append('TeamUp')
    if stored.get('discord_id') and sync_service.update_discord_event(stored['discord_id'], target, teamup_data):
        updated.append('Discord')

    if updated:
        with mappings_lock:
            event_mappings[event_id].update({
                'last_modified': target.get('modified_date', ''),
                'status':        target.get('status', 'confirmed'),
                'title':         target.get('title', 'No title'),
            })
        return jsonify({'status': 'success', 'platforms_updated': updated})

    return jsonify({'status': 'error', 'message': 'No platforms updated'}), 500


@app.route('/clear-mappings', methods=['POST'])
def clear_mappings():
    err = check_sync_secret()
    if err:
        return err
    with mappings_lock:
        count = len(event_mappings)
        event_mappings.clear()
    logger.info(f"🗑️  Cleared {count} event mappings")
    return jsonify({
        'status':  'success',
        'message': f'Cleared {count} mappings. Next sync will treat all events as new.',
        'warning': 'This may create duplicates if events already exist in TeamUp or Discord.',
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))

    for name, val in [
        ('TEAMUP_API_KEY',         TEAMUP_API_KEY),
        ('TEAMUP_CALENDAR_KEY',    TEAMUP_CALENDAR_KEY),
        ('ACTION_NETWORK_API_KEY', ACTION_NETWORK_API_KEY),
        ('DISCORD_BOT_TOKEN',      DISCORD_BOT_TOKEN),
        ('DISCORD_GUILD_ID',       DISCORD_GUILD_ID),
    ]:
        if not val:
            logger.warning(f"⚠️   {name} not configured")

    logger.info(f"🚀 Starting Action Network → TeamUp + Discord Sync on port {port}")
    logger.info(f"📡 Sync endpoint:    POST /sync")
    logger.info(f"❤️   Health check:    GET  /health")
    logger.info(f"📊 Status:           GET  /status")
    logger.info(f"🔗 Mappings:         GET  /mappings")
    logger.info(f"🎮 Discord:          {'enabled' if DISCORD_BOT_TOKEN and DISCORD_GUILD_ID else 'disabled'}")
    logger.info(f"🗓️  TeamUp:           {'enabled' if TEAMUP_API_KEY and TEAMUP_CALENDAR_KEY else 'disabled'}")

    app.run(host='0.0.0.0', port=port, debug=False)
