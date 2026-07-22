"""Fetch every candidate DSA feed and report what actually comes back."""
import concurrent.futures as cf
import datetime as dt
import urllib.parse
import urllib.request

GCAL = "https://calendar.google.com/calendar/ical/{}/public/basic.ics"

# (region, chapter, kind, value)   kind: "gcal" id  |  "ical" url
FEEDS = [
    ("National", "DSA Political Education (NPEC)", "gcal", "c_gr6govkt050e25nj4a4ldp2bu0@group.calendar.google.com"),
    ("National", "YDSA", "gcal", "c_a7c8f7ab3c2ec42eebab1d00670f2be57281dff6350c70cc1ccd6d6086733be4@group.calendar.google.com"),
    ("National", "Housing Justice Commission", "ical", "https://housing.dsausa.org/events/?ical=1"),

    ("Plains", "Kansas City DSA", "ical", "https://kcdsa.org/wp-json/r34icspro/v5/ics/9c37159db5ce114ddfce208403ae2dfd4995d529"),
    ("Plains", "Lawrence DSA", "gcal", "dsalawrenceks@gmail.com"),
    ("Plains", "Wichita DSA", "gcal", "socialists@ictdsa.org"),
    ("Plains", "Topeka DSA", "gcal", "topekadsa@gmail.com"),
    ("Plains", "Central Iowa DSA", "gcal", "b6f2c910b116ae2abb27d65c0c397fc301e2334367c221c8f15f13fc3bd9b87f@group.calendar.google.com"),
    ("Plains", "Iowa City DSA", "gcal", "exec@iowacitydsa.org"),
    ("Plains", "Northwest Arkansas DSA", "gcal", "nwademsoc@gmail.com"),
    ("Plains", "Omaha DSA (RSS only)", "ical", "https://omahadsa.org/index.xml"),

    ("West", "DSA Los Angeles", "ical", "https://dsa-la.org/?post_type=tribe_events&ical=1&eventDisplay=list"),
    ("West", "East Bay DSA", "ical", "https://www.eastbaydsa.org/calendar.ics"),
    ("West", "Silicon Valley DSA", "ical", "https://siliconvalleydsa.org/events/list/?ical=1"),
    ("West", "Sacramento DSA", "gcal", "dsasacramento@gmail.com"),
    ("West", "Portland DSA", "gcal", "c_fc5ef259ff0af2321fb99468e483ae6e1b68a880b3d8d88bf9390c184e8e9051@group.calendar.google.com"),
    ("West", "Seattle DSA", "ical", "https://seattledsa.org/events/list/?ical=1"),
    ("West", "Denver DSA", "gcal", "pp80f5omkpu3pkbum1lkb2ifhs@group.calendar.google.com"),
    ("West", "Salt Lake DSA", "ical", "https://www.solidarity.tech/calendar/o/IOCXPSlxxGA58jxLWB3O5gIKCrD3Mch4goaLH54TlWY.ics"),
    ("West", "Tucson DSA", "gcal", "c_7c1102a005448020e90177aecb7d904c26beeab2fa84e3031d2d49685888c2c9@group.calendar.google.com"),
    ("West", "Twin Cities DSA", "gcal", "3r812u8t6nf3203lqd0i3n1i20@group.calendar.google.com"),

    ("South", "Houston DSA", "ical", "https://houstondsa.org/?post_type=tribe_events&ical=1&eventDisplay=list"),
    ("South", "Austin DSA", "gcal", "austindsatech@gmail.com"),
    ("South", "Atlanta DSA", "gcal", "c_f0m59gh4vu9qrumbo1bngnmrbs@group.calendar.google.com"),
    ("South", "Tampa DSA", "gcal", "934c3e386e2460454561433e2e14fb11ef6d81de2ca6870b3c36eae3cecf909ad@group.calendar.google.com"),
    ("South", "Pinellas DSA", "gcal", "gqol8ujletle8qvphl2a7dvgjs@group.calendar.google.com"),
    ("South", "Miami DSA", "gcal", "eghihfpsiegodnqnibsa4npls8@group.calendar.google.com"),
    ("South", "Birmingham DSA", "gcal", "gvgr7124viedgkvkhig65ul6qo@group.calendar.google.com"),
    ("South", "Memphis-Midsouth DSA", "ical", "https://home.memphisdsa.org/?post_type=tribe_events&ical=1&eventDisplay=list"),
    ("South", "New Orleans DSA", "gcal", "vv0uj9uhqrl6j6m0pugu90uo6c@group.calendar.google.com"),

    ("Midwest", "Cincinnati DSA", "gcal", "1qhqelc0ls471iqrvros78otmo@group.calendar.google.com"),
    ("Midwest", "Cleveland DSA", "gcal", "dsacleinternal@gmail.com"),
    ("Midwest", "Columbus DSA", "ical", "https://www.columbusdsa.org/calendar/list/?ical=1"),
    ("Midwest", "Central Indiana DSA", "ical", "https://www.centralindsa.org/?post_type=tribe_events&ical=1&eventDisplay=list"),
    ("Midwest", "Metro Detroit DSA", "gcal", "tsg8ho66ggb7ptsljkcu9iss9c@group.calendar.google.com"),
    ("Midwest", "Huron Valley DSA", "gcal", "i1oimnmtdmv22kfj8akot1r0sk@group.calendar.google.com"),
    ("Midwest", "Grand Rapids DSA", "gcal", "b9k6h5d5vuakq4g1irff1li0dc@group.calendar.google.com"),
    ("Midwest", "Chicago DSA", "ical", "https://ics.teamup.com/feed/ksc3uaa38o41o2vz8o/0.ics"),
    ("Midwest", "Milwaukee DSA", "ical", "https://milwaukee.dsawi.org/calendar/events/?ical=1"),
    ("Midwest", "Madison Area DSA", "ical", "https://dsamadison.org/?post_type=tribe_events&ical=1&eventDisplay=list"),

    ("Northeast", "Boston DSA", "gcal", "894a2c6b665db098754774cbd42c1c71d42427830b6b4da4696cff9ba2906c4f@group.calendar.google.com"),
    ("Northeast", "Buffalo DSA", "gcal", "buffalodsa@gmail.com"),
    ("Northeast", "Pittsburgh DSA", "gcal", "6ds34c93vn8vgiuq74nejp5qnk@group.calendar.google.com"),
    ("Northeast", "Rhode Island DSA", "gcal", "c_0euk643ooni0ggdnaaje5gjr10@group.calendar.google.com"),
    ("Northeast", "Mid-Hudson Valley DSA", "gcal", "fljnp087nbv41649uiibvamork@group.calendar.google.com"),
    ("Northeast", "Lehigh Valley DSA", "gcal", "dsalehigh@gmail.com"),
    ("Northeast", "Southern Maine DSA", "gcal", "dsa.southernmaine@gmail.com"),
    ("Northeast", "North NJ DSA", "ical", "https://north.dsanj.org/events/list/?ical=1"),
    ("Northeast", "Metro DC DSA", "ical", "https://mdcdsa.org/events/list/?ical=1"),
    ("Northeast", "Southern NH DSA", "ical", "https://snh.dsachapters.org/events/list/?ical=1"),
]


def url_for(kind, value):
    if kind == "gcal":
        return GCAL.format(urllib.parse.quote(value, safe=""))
    return value


def check(entry):
    region, name, kind, value = entry
    url = url_for(kind, value)
    req = urllib.request.Request(url, headers={"User-Agent": "flinthillsdsa-event-integration/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = resp.read(4_000_000)
            code = resp.status
    except Exception as exc:  # noqa: BLE001
        return (region, name, kind, value, "FAIL", f"{type(exc).__name__}: {str(exc)[:60]}", 0, 0)

    text = body.decode("utf-8", "replace")
    if "BEGIN:VCALENDAR" not in text:
        return (region, name, kind, value, "NOT-ICAL", f"HTTP {code}, {len(body)}b", 0, 0)

    total = text.count("BEGIN:VEVENT")
    # Count events plausibly in the future (DTSTART year-month >= this month).
    today = dt.date.today()
    future = 0
    for line in text.splitlines():
        if not line.startswith("DTSTART"):
            continue
        digits = "".join(c for c in line.split(":")[-1] if c.isdigit())
        if len(digits) >= 8:
            try:
                d = dt.date(int(digits[0:4]), int(digits[4:6]), int(digits[6:8]))
            except ValueError:
                continue
            if d >= today:
                future += 1
    has_rrule = "RRULE" in text
    return (region, name, kind, value, "OK", f"HTTP {code}" + (" +rrule" if has_rrule else ""), total, future)


with cf.ThreadPoolExecutor(max_workers=12) as pool:
    results = list(pool.map(check, FEEDS))

print("STATUS\tREGION\tCHAPTER\tKIND\tVEVENTS\tFUTURE\tNOTE\tVALUE")
for region, name, kind, value, status, note, total, future in results:
    print(f"{status}\t{region}\t{name}\t{kind}\t{total}\t{future}\t{note}\t{value}")

ok = [r for r in results if r[4] == "OK"]
live = [r for r in ok if r[7] > 0]
print(f"\n{len(ok)}/{len(results)} returned a real calendar; {len(live)} have upcoming events.")
