# Flint Hills DSA — website events feed

Generates `events.json` from the chapter's Google Calendar and renders it as
event cards on [fhdsa.org](https://fhdsa.org). No Google credential ever reaches
a visitor's browser: a GitHub Action reads the calendar server-side, commits a
static JSON file, and the site fetches that file.

```
"Flint Hills Chapter of DSA"        organizers author events in Discord;
  Google Calendar  ◀───────────────  the Chronicle bot (NOT this repo) mirrors
        │                             them onto this calendar
        │  src/events_json.py  (GitHub Action, every 4h)
        ▼
   events.json  ──▶  GitHub Pages  ──▶  fhdsa.org Custom HTML block
```

**Chronicle** is a separate bot that owns Google Calendar ↔ Discord. This repo
contains no Discord code and never writes to any calendar — it only reads the
chapter calendar to build the website feed.

## Layout

| Path | Purpose |
|---|---|
| `config.yml` | chapter calendar id, window, committee tag/keyword/color map |
| `src/events_json.py` | read the chapter calendar → write `events.json` |
| `src/committees.py` | parse the `[Tag]` in a title → committee + badge color |
| `src/google_calendar.py` | read-only Google Calendar client |
| `src/config.py` | load and validate `config.yml` + the one secret |
| `events.json` | the generated feed, served from the Pages root |
| `events-embed.js`, `events-embed.css` | the card renderer, served from Pages |
| `site/paste-*.html` | the snippets you paste into WordPress |
| `.github/workflows/events.yml` | the scheduled job |

## Committees

Chapter events are authored in Discord, so the only routing signal that survives
into Google Calendar is a bracket tag at the start of the title:

```
[Housing] Tenant Union Kickoff   →  committee "Housing Justice and Tenant Organizing"
```

Resolution order, per event: the leading `[Tag]` (authoritative, case-insensitive),
then a keyword match against the title (best-effort), then the default committee
("General"). The tag is stripped from the displayed title. Tags, keywords, and
badge colors all live in the `committees:` block of `config.yml` — edit freely.

Badge text color is chosen per committee from the color's luminance (white or
near-black), so every badge clears WCAG AA contrast. A test enforces this against
the shipped colors, and another rejects the same tag being claimed twice.

## The website

Both snippets in `site/` are three lines: a stylesheet link, a `<div>`, and a
script tag. Paste one into a **Custom HTML** block.

- [`site/paste-home-page.html`](site/paste-home-page.html) — replaces the Action
  Network list in the home page's "Upcoming Events" section. Compact grid, 6 events.
- [`site/paste-chapter-calendar.html`](site/paste-chapter-calendar.html) —
  replaces the Google Calendar iframe on `/chapter-calendar/`. Month-by-month
  sections plus committee filter chips.

Container attributes, all optional:

| Attribute | Default | Meaning |
|---|---|---|
| `data-mode` | `full` | `compact` = 3-across grid; `full` = month sections + filter chips |
| `data-limit` | all | maximum number of events to render |
| `data-src` | the Pages URL | override the `events.json` location |

**Cards never navigate away.** A card shows only the committee colour bar, the
tag, the date and time, and the title. Clicking it opens a modal with the
location, the full description, and an RSVP button when the event carries an
Action Network link — that button is the only thing that leaves the page.

The modal is a native `<dialog>` opened with `showModal()`, so it gets a real
backdrop, focus trapping, and Escape-to-close from the browser. Focus moves to
the close button on open and returns to the card that opened it on close.

Both modes are a fixed three columns, so `data-limit="6"` on the home page lands
as two clean rows of three and the calendar page matches it month by month. The
three columns hold down to a 560px container, drop to two below that, and to one
below 380px. Those breakpoints are measured on the **container**, not the
viewport, because a block theme's content column is often far narrower than the
window. If the cards look cramped, set the block to *Wide width* in the editor.

Cards pull their palette from the Neve FSE theme's own CSS variables
(`--wp--preset--color--ti-*`), so a theme color change carries through.

## Running it

Locally, point `GOOGLE_SERVICE_ACCOUNT_FILE` at a service account key with read
access to the chapter calendar (the file pattern is gitignored):

```bash
pip install -r requirements.txt
GOOGLE_SERVICE_ACCOUNT_FILE=~/keys/fhdsa.json python -m src.events_json --stdout
python -m unittest discover -s tests        # credential-free
```

In CI, the workflow reads `GOOGLE_SERVICE_ACCOUNT_JSON` (the full key file
contents) from Actions secrets, regenerates `events.json`, and commits it back
only when it changed. It runs every 4 hours and on manual dispatch.

## Google setup

The chapter calendar (`flinthillsdsa@gmail.com`) must be shared with the service
account's address — Google Calendar → Settings → *Share with specific people or
groups* — with **See all event details**. That is the only calendar this repo
touches, and it is read-only. `check_access()` fails with that exact instruction
if the share is missing, rather than a raw 404.

## History

The old Action Network → Google Calendar → Discord sync, and a later
national/regional feed aggregator that wrote to a second calendar, both used to
live here and were removed. They are recoverable from git history if ever needed.
Regional calendars are now subscribed to directly in Google Calendar instead of
being aggregated.
