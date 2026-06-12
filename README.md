# event-integration

Syncs [Action Network](https://actionnetwork.org/) events for the **Flint Hills
Chapter of DSA** into the chapter's Google Calendars, and (optionally) mirrors
them into Discord as native scheduled events.

It runs every 30 minutes inside GitHub Actions. There is no server and no
database — each run is **stateless and idempotent**, so re-running never creates
duplicates and edits in Action Network propagate on the next run.

## How routing works

Organizers add a **hashtag** to the event description in Action Network. The
hashtag decides which Google Calendar the event lands on. An event may carry
more than one hashtag and is written to each matching calendar.

| Hashtag      | Calendar                                |
| ------------ | --------------------------------------- |
| `#civic`     | Civic                                   |
| `#housing`   | Housing Justice and Tenant Organizing   |
| `#meeting`   | Meetings                                |
| `#outreach`  | Outreach                                |
| `#action`    | Political Action                        |
| `#education` | Political Education                     |
| `#social`    | Social                                  |

Events with **no** matching hashtag are ignored. The hashtags are stripped from
the description before it is shown on the calendar or in Discord.

The full hashtag→calendar map and all sync options live in
[`config.yml`](config.yml).

---

## One-time setup for a successor

You only do this once. Everything sensitive goes into **GitHub Actions Secrets**,
never into the code.

### 1. Google Cloud service account (for Google Calendar)

A service account already exists for this project — you only need a JSON **key**
for it and to share the calendars with it:

> **Service account:** `action-network-sync@strategic-crow-466420-a9.iam.gserviceaccount.com`
> (Google Cloud project `strategic-crow-466420-a9`)

1. Go to <https://console.cloud.google.com/> and select project
   **`strategic-crow-466420-a9`**.
2. In **APIs & Services → Library**, confirm **Google Calendar API** shows
   **Enabled** (enable it if not).
3. In **APIs & Services → Credentials**, open the **action-network-sync**
   service account → **Keys → Add key → Create new key → JSON**. A `.json` file
   downloads. **This is a secret** — keep it safe, never commit it. (If a key was
   downloaded previously and you still have it, reuse that instead of making a
   new one.)
4. **Share each calendar with the service account email above.** In Google
   Calendar, for every calendar in the table above: **Settings → Share with
   specific people → Add people → paste
   `action-network-sync@strategic-crow-466420-a9.iam.gserviceaccount.com` →**
   permission **"Make changes to events" → Send.** Without this the sync cannot
   write to the calendar.

> Setting up from scratch in a different project instead? Create a service
> account (no roles needed), enable the Google Calendar API, download a JSON key,
> and share each calendar with the new account's email the same way.

> To find a Calendar ID: Google Calendar → Settings → pick the calendar →
> *Integrate calendar* → *Calendar ID*. These are already filled into
> `config.yml`.

### 2. Action Network API key

Action Network API access is a paying-partner feature. In Action Network:
**Start Organizing → Details → API & Sync**, then generate/copy the **API key**.
Create the routing tags by simply typing the hashtags (e.g. `#civic`) into event
descriptions — no special tag setup is required.

### 3. Discord bot (optional — already enabled in `config.yml`)

1. <https://discord.com/developers/applications> → **New Application**.
2. **Bot → Add Bot.** Under **Token**, click **Reset Token** and copy it. **This
   is a secret.**
3. Invite the bot to the server with the **Manage Events** permission. Build an
   invite URL under **OAuth2 → URL Generator**: scope `bot`, permission
   **Manage Events**, open the URL, pick the server, authorize.
4. The server (guild) ID is already in `config.yml`
   (`1100839736991555665`). To get a guild ID yourself: enable **Developer
   Mode** in Discord (User Settings → Advanced), right-click the server →
   **Copy Server ID**.

To turn Discord off, set `discord.enabled: false` in `config.yml`.

### 4. Add the secrets to GitHub

In this repo: **Settings → Secrets and variables → Actions → New repository
secret.** Create:

| Secret name                   | Value                                                        |
| ----------------------------- | ------------------------------------------------------------ |
| `ACTION_NETWORK_API_KEY`      | the Action Network API key                                   |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | the **entire contents** of the downloaded service-account JSON |
| `DISCORD_BOT_TOKEN`           | the Discord bot token (only if Discord is enabled)           |

### 5. Run it once to verify

**Actions → Sync → Run workflow** (the `workflow_dispatch` button). Watch the
log. You should see events being created on the calendars. Run it a **second**
time: it should report events as *unchanged* (no duplicates).

---

## Running locally for testing

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export ACTION_NETWORK_API_KEY='...'
export GOOGLE_SERVICE_ACCOUNT_JSON="$(cat /path/to/service-account.json)"
export DISCORD_BOT_TOKEN='...'        # only if Discord is enabled

python -m src.main
```

The run logs a summary like `calendar: 3 created, 0 updated, 12 unchanged …`.

---

## Keeping the schedule alive

GitHub disables scheduled workflows after 60 days of repository inactivity, and
this repo commits nothing on its own. The `sync.yml` workflow therefore includes
a small `keepalive` job ([`liskin/gh-workflow-keepalive`](https://github.com/liskin/gh-workflow-keepalive))
that re-enables the workflow on every scheduled run via the GitHub API — no junk
commits. (The action originally specified for this, `gautamkrishnar/keepalive-workflow`,
was blocked by GitHub for a Terms-of-Service violation and can no longer be used.)

If the Sync workflow ever shows as disabled in the Actions tab, click
**Enable workflow** once and the keepalive will hold it open from then on.

## Acceptance behavior

- A manual run creates matching events on the correct calendars.
- A second run makes no duplicates and patches only changed events.
- A cancelled Action Network event is marked **cancelled** on the calendar
  (configurable in `config.yml`).
- Each future event appears in the Discord server's **Events** tab; a second run
  edits rather than duplicates it; no channel messages are posted.
- Secrets never appear in committed code or in logs.
