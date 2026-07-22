# DSA calendar feeds — national bodies and chapters

A compiled list of public DSA calendars the aggregator can pull from. Not every chapter is
here, and many chapters publish no public feed at all. Use it to pick sources for
[`config/feeds.yml`](../config/feeds.yml).

## Status column

Every candidate below was **fetched directly on 2026-07-22** and the response inspected.

- **WORKS** — returned a real `BEGIN:VCALENDAR` document. The `events` column is how many
  `VEVENT` blocks came back and how many start today or later.
- **BROKEN** — the URL exists in someone's notes but does not return a calendar right now.
  The reason is recorded. Worth rechecking occasionally; some are bot-blocking that may not
  apply from GitHub's runners.
- **NO FEED / NOT FOUND** — not fetched, because the chapter's platform (Squarespace,
  Solidarity.tech without an exposed token, Styled Calendar, custom apps) exposes no
  full-calendar feed, or no public calendar page was found.

Re-run the check any time with `python3 docs/check_feeds.py`.

## Deriving a feed URL yourself

For a public Google Calendar with ID `X`, the iCal feed is always:

```
https://calendar.google.com/calendar/ical/<X, with @ written as %40>/public/basic.ics
```

The aggregator can also read it by ID directly with `type: gcal`, which is preferred — it
skips a redirect and reports errors more precisely.

For WordPress chapters running **The Events Calendar**, the feed is usually
`https://<site>/events/?ical=1` or `https://<site>/?post_type=tribe_events&ical=1&eventDisplay=list`.
Note that several chapters serve HTML from these URLs anyway, so always check.

---

## A warning about volume

Chronicle mirrors the National / Regional calendar into Discord, and **Discord caps a server
at 100 upcoming events**. Several of these chapters are large: Austin returned 47 upcoming
events, Chicago 170, Seattle 102, Houston 141. Enabling a handful of big chapters will blow
past that cap and bury your own chapter's events.

Enable regionally relevant feeds, not everything that works. If you do want a big chapter,
use `include:` keyword filters in `config/feeds.yml` to take only the slice you care about,
or lower `aggregator.horizon_days` in `config.yml`.

---

## National bodies

| Body | Type | Feed URL or Calendar ID | Status | Events (total / upcoming) |
|---|---|---|---|---|
| National Political Education (NPEC) | gcal | `c_gr6govkt050e25nj4a4ldp2bu0@group.calendar.google.com` | **WORKS** | 29 / 0 |
| YDSA (Young DSA) | gcal | `c_a7c8f7ab3c2ec42eebab1d00670f2be57281dff6350c70cc1ccd6d6086733be4@group.calendar.google.com` | **WORKS** | 36 / 0 |
| Housing Justice Commission | ical | `https://housing.dsausa.org/events/?ical=1` | **BROKEN** — HTTP 403 | — |
| Growth & Development Committee | — | embed present, ID not extractable | NO FEED | — |
| NPC (National Political Committee) | — | meetings posted individually | NO FEED | — |
| Labor Commission (DSLC) | — | no calendar page | NOT FOUND | — |
| National Electoral Commission | — | no calendar page | NOT FOUND | — |
| Ecosocialist / Green New Deal | — | Action Network huddles only | NOT FOUND | — |
| International Committee | — | no calendar page | NOT FOUND | — |
| Religion & Socialism WG | — | events list, no feed | NO FEED | — |
| Medicare for All | — | no calendar page | NOT FOUND | — |
| Palestine / BDS WG | — | Action Network, no subscribe feed | NO FEED | — |
| Immigrant & Migrant Rights | — | Action Network, no subscribe feed | NO FEED | — |
| Afrosocialists & Socialists of Color | — | Action Network, no subscribe feed | NO FEED | — |

Both working national feeds returned **zero upcoming events** on the day of checking — NPEC's
last event was 2026-07-15. They are correctly configured and simply idle; the aggregator will
pick events up whenever these bodies schedule them.

National committees largely coordinate on Action Network, Slack, or Discord rather than
public Google Calendars, which is why the list is so short.

---

## Kansas area and Plains (closest to you)

| Chapter | State | Type | Feed URL or Calendar ID | Status | Events |
|---|---|---|---|---|---|
| Kansas City DSA | MO/KS | ical | `https://kcdsa.org/wp-json/r34icspro/v5/ics/9c37159db5ce114ddfce208403ae2dfd4995d529` | **EMPTY** — valid ICS, zero VEVENTs | 0 |
| Lawrence DSA | KS | gcal | `dsalawrenceks@gmail.com` | **WORKS** | 214 / 4 |
| Wichita DSA | KS | gcal | `socialists@ictdsa.org` | **WORKS** | 163 / 2 |
| Topeka DSA | KS | gcal | `topekadsa@gmail.com` | **UNUSABLE** — free/busy only, every event titled "Busy" | 192 / 177 |
| Central Iowa DSA (Des Moines) | IA | gcal | `b6f2c910b116ae2abb27d65c0c397fc301e2334367c221c8f15f13fc3bd9b87f@group.calendar.google.com` | **WORKS** | 221 / 2 |
| Iowa City DSA | IA | gcal | `exec@iowacitydsa.org` | **WORKS** | 171 / 0 |
| Northwest Arkansas DSA | AR | gcal | `nwademsoc@gmail.com` | **WORKS** | 215 / 3 |
| Omaha DSA | NE | — | `https://omahadsa.org/index.xml` is RSS, not iCal | NO iCal | — |
| St. Louis DSA | MO | — | custom event app | NOT FOUND | — |
| Mid-Missouri DSA | MO | — | Action Network only | NO FEED | — |
| Oklahoma City DSA | OK | — | Action Network / Linktree | NO FEED | — |
| Green Country DSA (Tulsa) | OK | — | social media only | NOT FOUND | — |

Two traps in this region. **Topeka** returns 192 events that are all titled "Busy" — the
calendar is shared free/busy-only, so no titles, locations, or descriptions come through.
**Kansas City** returns a well-formed calendar containing no events at all; its only
`DTSTART` lines belong to timezone definitions. Neither is worth enabling as-is. Kansas City
is worth asking about directly, since they clearly intend to publish a feed.

Measured inside the aggregator's own 180-day window: Lawrence 41 events, Wichita 32,
Central Iowa 33, Iowa City 18, Northwest Arkansas 127.

---

## West + Mountain

| Chapter | State | Type | Feed URL or Calendar ID | Status | Events |
|---|---|---|---|---|---|
| Silicon Valley DSA | CA | ical | `https://siliconvalleydsa.org/events/list/?ical=1` | **WORKS** | 33 upcoming |
| Sacramento DSA | CA | gcal | `dsasacramento@gmail.com` | **WORKS** | 1347 / 2 |
| Portland DSA | OR | gcal | `c_fc5ef259ff0af2321fb99468e483ae6e1b68a880b3d8d88bf9390c184e8e9051@group.calendar.google.com` | **WORKS** | 906 / 51 |
| Seattle DSA | WA | ical | `https://seattledsa.org/events/list/?ical=1` | **WORKS** | 102 upcoming |
| Denver DSA | CO | gcal | `pp80f5omkpu3pkbum1lkb2ifhs@group.calendar.google.com` | **WORKS** | 1835 / 10 |
| Salt Lake DSA | UT | ical | `https://www.solidarity.tech/calendar/o/IOCXPSlxxGA58jxLWB3O5gIKCrD3Mch4goaLH54TlWY.ics` | **WORKS** | 54 / 27 |
| Tucson DSA | AZ | gcal | `c_7c1102a005448020e90177aecb7d904c26beeab2fa84e3031d2d49685888c2c9@group.calendar.google.com` | **WORKS** | 61 / 14 |
| Twin Cities DSA | MN | gcal | `3r812u8t6nf3203lqd0i3n1i20@group.calendar.google.com` | **WORKS** | 3327 / 30 |
| DSA Los Angeles | CA | ical | `https://dsa-la.org/?post_type=tribe_events&ical=1&eventDisplay=list` | **BROKEN** — HTTP 403 | — |
| East Bay DSA | CA | ical | `https://www.eastbaydsa.org/calendar.ics` | **BROKEN** — returns HTML | — |
| San Francisco DSA | CA | gcal | ID not extracted | NEEDS ID | — |
| San Diego DSA | CA | gcal | ID not extracted | NEEDS ID | — |
| DSA Long Beach | CA | ical | Solidarity.tech, token not extracted | NEEDS TOKEN | — |
| California DSA (statewide) | CA | — | Squarespace | NO FEED | — |
| Phoenix-Metro DSA | AZ | — | Squarespace | NO FEED | — |
| DSA Las Vegas | NV | — | JS site | NOT FOUND | — |

---

## South + Eastern Midwest

| Chapter | State | Type | Feed URL or Calendar ID | Status | Events |
|---|---|---|---|---|---|
| Houston DSA | TX | ical | `https://houstondsa.org/?post_type=tribe_events&ical=1&eventDisplay=list` | **WORKS** | 141 upcoming |
| Austin DSA | TX | gcal | `austindsatech@gmail.com` | **WORKS** | 2761 / 47 |
| Atlanta DSA | GA | gcal | `c_f0m59gh4vu9qrumbo1bngnmrbs@group.calendar.google.com` | **WORKS** | 1300 / 14 |
| Pinellas DSA | FL | gcal | `gqol8ujletle8qvphl2a7dvgjs@group.calendar.google.com` | **WORKS** | 820 / 20 |
| Miami DSA | FL | gcal | `eghihfpsiegodnqnibsa4npls8@group.calendar.google.com` | **WORKS** | 717 / 0 |
| Birmingham DSA | AL | gcal | `gvgr7124viedgkvkhig65ul6qo@group.calendar.google.com` | **WORKS** | 1137 / 18 |
| Memphis-Midsouth DSA | TN | ical | `https://home.memphisdsa.org/?post_type=tribe_events&ical=1&eventDisplay=list` | **WORKS** | 25 upcoming |
| New Orleans DSA | LA | gcal | `vv0uj9uhqrl6j6m0pugu90uo6c@group.calendar.google.com` | **WORKS** | 1649 / 56 |
| Cincinnati DSA | OH | gcal | `1qhqelc0ls471iqrvros78otmo@group.calendar.google.com` | **WORKS** | 196 / 0 |
| Cleveland DSA | OH | gcal | `dsacleinternal@gmail.com` | **WORKS** | 2274 / 62 |
| Central Indiana DSA | IN | ical | `https://www.centralindsa.org/?post_type=tribe_events&ical=1&eventDisplay=list` | **WORKS** | 33 upcoming |
| Metro Detroit DSA | MI | gcal | `tsg8ho66ggb7ptsljkcu9iss9c@group.calendar.google.com` | **WORKS** | 1614 / 34 |
| Huron Valley DSA (Ann Arbor) | MI | gcal | `i1oimnmtdmv22kfj8akot1r0sk@group.calendar.google.com` | **WORKS** | 1058 / 5 |
| Grand Rapids DSA | MI | gcal | `b9k6h5d5vuakq4g1irff1li0dc@group.calendar.google.com` | **WORKS** | 789 / 4 |
| Chicago DSA | IL | ical | `https://ics.teamup.com/feed/ksc3uaa38o41o2vz8o/0.ics` | **WORKS** | 170 upcoming |
| Tampa DSA | FL | gcal | `934c3e386e2460454561433e2e14fb11ef6d81de2ca6870b3c36eae3cecf909ad@group.calendar.google.com` | **BROKEN** — HTTP 404 | — |
| Columbus DSA | OH | ical | `https://www.columbusdsa.org/calendar/list/?ical=1` | **BROKEN** — returns HTML | — |
| Milwaukee DSA | WI | ical | `https://milwaukee.dsawi.org/calendar/events/?ical=1` | **BROKEN** — returns HTML | — |
| Madison Area DSA | WI | ical | `https://dsamadison.org/?post_type=tribe_events&ical=1&eventDisplay=list` | **BROKEN** — returns HTML | — |
| Knoxville DSA | TN | gcal | ID needs verifying via Subscribe | NEEDS ID | — |
| Middle Tennessee DSA | TN | gcal | ID not captured | NEEDS ID | — |
| Charlotte Metro DSA | NC | gcal | ID uncertain | NEEDS ID | — |
| North Texas DSA | TX | — | Solidarity.tech, no public ICS | NO FEED | — |
| San Antonio DSA | TX | — | Squarespace | NO FEED | — |
| Triangle DSA (Raleigh/Durham) | NC | — | Squarespace | NO FEED | — |
| Louisville DSA | KY | — | Squarespace | NO FEED | — |

---

## Northeast + Mid-Atlantic

| Chapter | State | Type | Feed URL or Calendar ID | Status | Events |
|---|---|---|---|---|---|
| Boston DSA | MA | gcal | `894a2c6b665db098754774cbd42c1c71d42427830b6b4da4696cff9ba2906c4f@group.calendar.google.com` | **WORKS** | 3308 / 0 |
| Buffalo DSA | NY | gcal | `buffalodsa@gmail.com` | **WORKS** | 659 / 16 |
| Pittsburgh DSA | PA | gcal | `6ds34c93vn8vgiuq74nejp5qnk@group.calendar.google.com` | **WORKS** | 2456 / 17 |
| Rhode Island DSA | RI | gcal | `c_0euk643ooni0ggdnaaje5gjr10@group.calendar.google.com` | **WORKS** | 555 / 3 |
| Mid-Hudson Valley DSA | NY | gcal | `fljnp087nbv41649uiibvamork@group.calendar.google.com` | **WORKS** | 725 / 13 |
| Lehigh Valley DSA | PA | gcal | `dsalehigh@gmail.com` | **WORKS** | 332 / 6 |
| Southern Maine DSA | ME | gcal | `dsa.southernmaine@gmail.com` | **WORKS** | 2005 / 8 |
| North NJ DSA | NJ | ical | `https://north.dsanj.org/events/list/?ical=1` | **BROKEN** — empty body | — |
| Metro DC DSA | DC | ical | `https://mdcdsa.org/events/list/?ical=1` | **BROKEN** — HTTP 404 | — |
| Southern NH DSA | NH | ical | `https://snh.dsachapters.org/events/list/?ical=1` | **BROKEN** — TLS cert failure | — |
| Philadelphia DSA | PA | — | Squarespace | NO FEED | — |
| Central NJ DSA | NJ | — | Styled Calendar | NO FEED | — |
| River Valley DSA (W. Mass) | MA | — | Solidarity.tech | NO FEED | — |
| Long Island DSA | NY | — | Squarespace | NO FEED | — |
| Lower Hudson Valley DSA | NY | — | Squarespace | NO FEED | — |
| NYC DSA | NY | — | custom app | NOT FOUND | — |
| Connecticut DSA | CT | — | JS-rendered | NOT FOUND | — |
| Capital District DSA (Albany) | NY | — | Tribe pattern did not resolve | NOT FOUND | — |

---

## Gaps

Feeds marked NEEDS ID / NEEDS TOKEN (SF, San Diego, Long Beach, Knoxville, Middle TN,
Charlotte) almost certainly exist — the ID just was not exposed. Open the chapter's calendar
page and use its own Subscribe button to get the URL, then add it here and re-run the checker.

The three HTTP 403s (Housing Justice, DSA LA) may be bot protection reacting to a scripted
request. They could behave differently from GitHub's runners; if you want one of them, add it
with `enabled: true` and watch one run's log before trusting it.

Not individually researched: Santa Cruz, Ventura, San Fernando Valley, Inland Empire, Fresno,
Orange County, Tacoma, Spokane, Salem, Eugene, Reno, Boulder, Fort Collins, Colorado Springs,
Albuquerque, Las Cruces, Duluth, and many smaller chapters. The derivation method above
applies to all of them.
