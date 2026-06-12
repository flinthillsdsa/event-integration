# CLAUDE.md — context for future Claude Code sessions

## What this is

A stateless, idempotent sync that runs in GitHub Actions every 30 minutes:

```
Action Network events  ──hashtag in description──►  Google Calendar (per tag)
                                               └──►  Discord scheduled events (optional)
```

No server, no database. Each run reconstructs all state from the live APIs, so
re-running never duplicates and upstream edits propagate on the next pass.

## Architecture

| File                     | Responsibility                                                        |
| ------------------------ | --------------------------------------------------------------------- |
| `src/main.py`            | Orchestrates one pass: fetch → normalize → route → upsert → reconcile |
| `src/action_network.py`  | AN API v2 client, event normalization, time/location/hashtag handling |
| `src/google_calendar.py` | Service-account GCal client: deterministic upsert / cancel / delete    |
| `src/discord.py`         | Guild Scheduled Events client (EXTERNAL type), UUID-marker mapping      |
| `src/config.py`          | Loads `config.yml` + secrets from env                                  |
| `config.yml`             | Routing map (hashtag→calendar) and sync options                       |

## Secrets (env vars / GitHub Actions Secrets)

- `ACTION_NETWORK_API_KEY` — header `OSDI-API-Token`.
- `GOOGLE_SERVICE_ACCOUNT_JSON` — full service-account key JSON (string).
- `DISCORD_BOT_TOKEN` — header `Authorization: Bot <token>` (only if Discord on).

The Discord **guild id** is non-sensitive and lives in `config.yml`.

## `config.yml` schema

- `action_network.group_id` — informational (the API token is group-scoped).
- `sync.default_timezone` — IANA tz events are authored in (`America/Chicago`).
- `sync.default_duration_minutes` — end time fallback (mandatory for Discord).
- `sync.past_window_days` — ignore events starting more than N days ago.
- `sync.only_tagged` — skip events without a routing hashtag.
- `sync.multi_tag_fan_out` — an event with multiple hashtags goes to each calendar.
- `sync.on_cancelled` — `cancel` (mark calendar event cancelled) or `delete`.
- `sync.on_disappeared` — `delete` or `keep` for future events gone from AN.
- `routing` — map of `hashtag-name` (no `#`) → Google Calendar ID.
- `discord.enabled`, `discord.guild_id`.

## Key design decisions / gotchas (verified against live docs)

- **Routing is by hashtag in the event description**, NOT OSDI taggings. Action
  Network taggings are person-centric and never link to events. Organizers type
  `#civic`, `#housing`, etc. into the description; `action_network.normalize`
  matches them against the routing map (case-insensitive). Hashtags are stripped
  from the displayed description.
- **Action Network time quirk:** AN stamps local (Central) times with a trailing
  `Z` that does *not* mean UTC. `_resolve_time` treats a `Z`/offset-less value as
  wall-clock in `default_timezone`, producing both the wall-clock (for Google,
  via `{dateTime, timeZone}`) and the true UTC instant (for Discord). DST is
  handled by `zoneinfo`. A value with a real numeric offset is honored as-is.
- **Deterministic Google event id:** `"an" + uuid_without_hyphens`, validated
  against base32hex (`a-v`, `0-9`, length 5–1024). Lets `get`→404→`insert`,
  else `patch`. A 16-char content hash in `extendedProperties.private` skips
  no-op patches.
- **AN fetch:** `GET /api/v2/events?filter=start_date gt 'YYYY-MM-DD'`, events in
  `_embedded["osdi:events"]`, follow `_links.next.href`. AN OData supports only
  `eq`/`gt`/`lt`.
- **Discord is stateless via a marker:** each scheduled-event description ends
  with `ref: an-<uuid>`; on each run we list events, parse the marker, and map
  AN UUID → Discord event id. EXTERNAL events require `entity_type=3`,
  `privacy_level=2`, `entity_metadata.location` (≤100 chars, defaults to `TBD`),
  and `scheduled_end_time`. Create returns HTTP 200. Only future events are sent
  (Discord rejects past start times).
- **Reconciliation / disappearance:** for each calendar we list our managed
  *future* events (`privateExtendedProperty=anManaged=true`, `timeMin=now`) and
  delete any not seen this run — so naturally-aged past events are never touched,
  and an event that changes hashtags is removed from the old calendar. Cancelled
  events stay (marked cancelled) per `on_cancelled`. Discord events are deleted
  when their AN event is no longer present/active.
- **Resilience:** auth failures (AN 401/403, bad Google key, Discord 401/403)
  raise loudly; a single malformed event is logged and skipped without aborting
  the run. AN and Discord 429s honor `Retry-After`.

## Keepalive

`gautamkrishnar/keepalive-workflow` (specified in the original brief) was blocked
by GitHub for ToS. Replaced with `liskin/gh-workflow-keepalive@v1` as a
`keepalive` job *inside* `sync.yml` (it must run in the workflow it protects;
runs only on the `schedule` trigger; needs `actions: write`).

## Running locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ACTION_NETWORK_API_KEY=... GOOGLE_SERVICE_ACCOUNT_JSON="$(cat key.json)" DISCORD_BOT_TOKEN=...
python -m src.main
```

There are no third-party test deps; the pure helpers (`_resolve_time`,
`calendar_event_id`, `format_location`, `discord.build_description`) are easy to
exercise directly in a REPL.
