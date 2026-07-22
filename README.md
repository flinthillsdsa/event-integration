# Flint Hills DSA — event integration

Two small Python jobs and a browser snippet that move events between Google
Calendar, national/regional feeds, and [fhdsa.org](https://fhdsa.org).

## How the whole flow fits together

```
organizers create events in Discord
        │
        ▼  (Chronicle bot — NOT this repo)
"Flint Hills Chapter of DSA" Google Calendar ─────────┐
                                                      │
external iCal / public Google Calendar feeds          │  read
  (config/feeds.yml)                                  │
        │                                             │
        ▼  src/aggregate.py  (this repo)              │
"National / Regional" Google Calendar ────────────────┤
        │                                             │
        ▼  Chronicle bot                              ▼
     Discord events                          src/events_json.py
                                                      │
                                                      ▼
                                     events.json  (committed, served by Pages)
                                                      │
                                                      ▼
                                    events-embed.js on fhdsa.org
```

**Chronicle** is a separate bot and owns the whole Google Calendar ↔ Discord
relationship in both directions. This repo contains no Discord code. Anything
the aggregator writes to the National / Regional calendar reaches Discord for
free, because Chronicle mirrors that calendar.

**RSVPs stay in Action Network.** We only surface the RSVP link on the card and
in the calendar event description. Nothing here captures a signup.

### What is in this repo

| Path | What it does |
|---|---|
| `config.yml` | calendar IDs, committee tag/keyword/color map, time windows |
| `config/feeds.yml` | the list of national/regional sources to aggregate |
| `src/aggregate.py` | fetch feeds → upsert into the National / Regional calendar |
| `src/events_json.py` | read both calendars → write `events.json` |
| `src/committees.py` | bracket-tag parsing, keyword fallback, RSVP-link extraction |
| `src/google_calendar.py` | Calendar API: deterministic ids, managed-event guards |
| `src/feeds.py` | iCal and Google Calendar fetching/normalization |
| `events.json` | generated. Do not hand-edit; the workflow overwrites it |
| `events-embed.js` / `events-embed.css` | the website cards |
| `site/paste-*.html` | the snippets to paste into WordPress |
| `.github/workflows/events.yml` | the schedule |

## Committees: how an event gets its badge

Chapter events are authored in Discord, so the only routing signal that survives
into Google Calendar is a bracket tag at the front of the event name:

```
[Housing] Tenant Union Kickoff
```

Resolution order:

1. **Bracket tag** — authoritative. Case-insensitive, matched against `tags` in
   `config.yml`. The tag is stripped from the title the website displays.
2. **Keyword match** — if there is no tag, the title is matched against each
   committee's `keywords` list, top to bottom, first hit wins. Best-effort guess.
3. **`General`** — everything else.

Events on the National / Regional calendar skip all of that: committee
`National`, `source: "national"`.

### Editing the map

Everything lives in the `committees:` block of `config.yml`. To rename a
committee, change a badge color, add a tag alias, or teach the keyword fallback a
new word, edit that block and commit — no code changes.

```yaml
  - name: "Housing Justice and Tenant Organizing"
    tags: ["Housing", "HJTO"]        # accepted bracket tags; first is canonical
    color: "#0b8043"                 # badge hex on the website
    keywords: ["tenant", "housing"]  # fallback when the title has no tag
```

Order matters for the keyword fallback only. `default_committee` and
`national_committee` at the bottom of the file control the two special cases.

**Tell organizers the tags.** The current set is `[CIVIC]`, `[Housing]`,
`[Meeting]`, `[Outreach]`, `[PolAction]`, `[PolEd]`, `[Social]`. Untagged events
still work — they just get a guess or land in General.

## Adding a national/regional feed

Add an entry to `config/feeds.yml` and commit:

```yaml
  - name: "Kansas City DSA"        # must be unique and stable — see the warning
    type: "ical"                   # "ical" or "gcal"
    url: "https://kcdsa.org/events/?ical=1"
    enabled: true
    region: "Missouri / Kansas"    # optional, shown in the description footer
    include: ["socialist", "dsa"]  # optional: keep only events matching one
    exclude: ["members only"]      # optional: drop events matching any
```

[`docs/dsa-calendar-feeds.md`](docs/dsa-calendar-feeds.md) is a surveyed list of DSA national
bodies and chapters with a working (or broken) public feed, each one actually fetched and
checked. Re-run that check any time with `python3 docs/check_feeds.py`.

Where to find feed URLs:

- Most DSA chapter sites run WordPress with **The Events Calendar**, which
  publishes an iCal feed at `https://<their-site>/events/?ical=1`. Use
  `type: ical`.
- National committee and working-group calendars are usually **public Google
  Calendars**. Take the calendar ID out of their embed code (`src=` parameter,
  base64-decoded) or from "Integrate calendar" in calendar settings, and use
  `type: gcal`.

Two things to know:

- **`name` is an identity, not a label.** Event ids are derived from
  `(name, source uid)`. Renaming a source orphans its events: the old ones are
  deleted and recreated. Harmless but noisy in Discord — rename sparingly.
- **`enabled: false` removes that source's events** from the calendar on the next
  run. That is the intended way to retire a feed.

## Running locally

You need a service account key file. Never commit it — `*service-account*.json`
and `secrets/` are gitignored.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export GOOGLE_SERVICE_ACCOUNT_FILE=~/secrets/fhdsa-service-account.json

python -m src.aggregate --dry-run     # report what would change; writes nothing
python -m src.aggregate               # actually reconcile the National calendar
python -m src.events_json --stdout    # print the feed instead of writing it
python -m src.events_json             # write events.json

python -m unittest discover -s tests  # no credentials needed
```

In GitHub Actions the same code reads `GOOGLE_SERVICE_ACCOUNT_JSON` (the secret's
value is the whole key file). The key is never printed or logged.

### Testing the website cards without deploying

```bash
python -m src.events_json
python3 -m http.server 8000     # then open site/paste-*.html against localhost
```

Point a card container at a local file with `data-src="/events.json"`.

## Google setup

The service account is `action-network-sync@strategic-crow-466420-a9.iam.gserviceaccount.com`
(the `client_email` in the key file). It needs:

| Calendar | Access |
|---|---|
| Flint Hills Chapter of DSA | See all event details |
| National / Regional | **Make changes to events** |

Share each calendar with that address in Google Calendar → Settings → *Share with
specific people or groups*. Public feeds listed in `feeds.yml` need no sharing.

Both jobs check this before doing any work and stop with the exact address and
access level to grant. Note that the Calendar API answers **404, not 403**, for a
calendar the service account cannot see, so "Not Found" almost always means "not
shared yet" rather than "wrong ID".

`events.json` is published by **GitHub Pages** (Settings → Pages → Deploy from a
branch → `main` / root) at
`https://flinthillsdsa.github.io/event-integration/events.json`.

## The website

Both snippets in `site/` are three lines: a stylesheet link, a `<div>`, and a
script tag. Paste one into a **Custom HTML** block.

- [`site/paste-home-page.html`](site/paste-home-page.html) — replaces the Action
  Network list in the home page's "Upcoming Events" section. Compact grid, 6
  events, links to the full calendar.
- [`site/paste-chapter-calendar.html`](site/paste-chapter-calendar.html) —
  replaces (or sits above) the Google Calendar iframe on `/chapter-calendar/`.
  Month-by-month sections plus committee filter chips.

Container attributes, all optional:

| Attribute | Default | Meaning |
|---|---|---|
| `data-mode` | `full` | `compact` = flat grid; `full` = month sections + filter chips |
| `data-limit` | all | maximum number of events to render |
| `data-source` | `all` | `chapter` or `national` to show only one |
| `data-src` | the Pages URL | override the `events.json` location |
| `data-more` | — | URL for a trailing "See all events" link |

Cards pull their palette from the Neve FSE theme's own CSS variables
(`--wp--preset--color--ti-*`), so a theme color change carries through. Badge and
left-border colors come from the committee map. The browser only ever fetches the
committed static JSON — no Google credential is exposed.

## Safety properties

These are the guarantees the code is built around; they are covered by
`tests/test_logic.py`.

- **Idempotent.** Event ids are `sha256(source name + source uid)` rendered as
  base32hex, and each event stores a hash of the fields we write. A second run in
  a row makes zero API writes.
- **Human events are untouchable.** Everything the aggregator creates carries
  `extendedProperties.private.managedBy = "dsa-aggregator"`. Reconcile only ever
  lists, patches, or deletes events carrying that tag, and both the patch and
  delete paths re-check it before acting.
- **Cancellations propagate.** A managed event that no longer appears in any
  source is deleted on the next run — unless its source failed that run, in which
  case it is deliberately left alone.
- **One bad feed cannot break the run.** Fetch and parse failures are caught per
  source, logged as a warning, and skipped. Every run prints an added / updated /
  removed / unchanged / skipped summary per source.
- **The reconcile window is bounded** by `aggregator.horizon_days` and
  `past_window_days`; events outside it are never considered.

## Migration status

Action Network is being retired as the events hub in favor of Google Calendar.
The old Action Network → Google Calendar → Discord sync used to live in this repo
and was deleted; it is recoverable from git history at commit `2108a75` if
needed. Its secrets (`ACTION_NETWORK_API_KEY`, `DISCORD_BOT_TOKEN`) are still
configured on the repo and can be removed once the migration is verified.
