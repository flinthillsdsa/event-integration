"""Credential-free tests for the website feed logic: tag parsing, committees,
RSVP extraction, access preflight, events.json entries, and the shipped config.

    python -m unittest discover -s tests
"""

from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import google_calendar as gcal  # noqa: E402
from src.committees import extract_rsvp_url, resolve, strip_tag  # noqa: E402
from src.config import REPO_ROOT, Committee, Config, load_config  # noqa: E402
from src.events_json import build_entry  # noqa: E402

CHICAGO = ZoneInfo("America/Chicago")


def make_config() -> Config:
    return Config(
        chapter_calendar_id="chapter@example.com",
        timezone="America/Chicago",
        window_days=120,
        output_path=Path("events.json"),
        max_description_chars=600,
        default_duration_minutes=120,
        committees=[
            Committee("Housing Justice and Tenant Organizing", ("Housing", "HJTO"), "#0b8043", ("tenant", "housing")),
            Committee("Political Education", ("PolEd",), "#8e24aa", ("reading", "education")),
            Committee("Meetings", ("Meeting",), "#616161", ("general body",)),
        ],
        default_committee=Committee("General", (), "#546e7a", ()),
    )


class TestTagParsing(unittest.TestCase):
    def test_strips_leading_tag(self):
        self.assertEqual(strip_tag("[Housing] Tenant Union Kickoff"), ("Tenant Union Kickoff", "Housing"))

    def test_tolerates_separators_and_spacing(self):
        self.assertEqual(strip_tag("[PolEd] - Reading Group")[0], "Reading Group")
        self.assertEqual(strip_tag("[ Social ]: Picnic"), ("Picnic", "Social"))

    def test_no_tag_is_left_alone(self):
        self.assertEqual(strip_tag("Comrade Club"), ("Comrade Club", None))

    def test_tag_only_title_is_not_emptied(self):
        self.assertEqual(strip_tag("[Meeting]"), ("[Meeting]", None))


class TestCommitteeResolution(unittest.TestCase):
    def setUp(self):
        self.config = make_config()

    def test_tag_wins_and_is_case_insensitive(self):
        resolved = resolve("[hOuSiNg] Kickoff", self.config)
        self.assertEqual(resolved.committee, "Housing Justice and Tenant Organizing")
        self.assertEqual(resolved.color, "#0b8043")
        self.assertEqual(resolved.matched_by, "tag")
        self.assertEqual(resolved.title, "Kickoff")

    def test_alias_tag(self):
        self.assertEqual(resolve("[HJTO] Court Watch", self.config).matched_by, "tag")

    def test_keyword_fallback(self):
        resolved = resolve("Tenant Power Night", self.config)
        self.assertEqual(resolved.committee, "Housing Justice and Tenant Organizing")
        self.assertEqual(resolved.matched_by, "keyword")

    def test_default_fallback(self):
        resolved = resolve("Comrade Club", self.config)
        self.assertEqual(resolved.committee, "General")
        self.assertEqual(resolved.matched_by, "default")

    def test_unknown_tag_falls_through_to_keywords(self):
        resolved = resolve("[Bogus] Reading Group", self.config)
        self.assertEqual(resolved.committee, "Political Education")
        self.assertEqual(resolved.matched_by, "keyword")


class TestRsvpExtraction(unittest.TestCase):
    def test_prefers_action_network(self):
        text = "Details at https://example.org/info and RSVP https://actionnetwork.org/events/foo now."
        self.assertEqual(extract_rsvp_url(text), "https://actionnetwork.org/events/foo")

    def test_strips_trailing_punctuation(self):
        self.assertEqual(
            extract_rsvp_url("RSVP: https://actionnetwork.org/events/bar."),
            "https://actionnetwork.org/events/bar",
        )

    def test_no_link_returns_none(self):
        self.assertIsNone(extract_rsvp_url("No link here."))
        self.assertIsNone(extract_rsvp_url(None))


class TestAccessPreflight(unittest.TestCase):
    """A calendar that is not shared must fail with instructions, not a traceback."""

    @staticmethod
    def _service(status=None):
        from googleapiclient.errors import HttpError

        class Resp:
            def __init__(self, code):
                self.status = code
                self.reason = "Not Found"

        class Request:
            def __init__(self, error=None, result=None):
                self._error, self._result = error, result

            def execute(self):
                if self._error:
                    raise self._error
                return self._result

        class Service:
            def calendars(self):
                error = HttpError(Resp(status), b"{}") if status else None
                return type("C", (), {"get": lambda _s, **kw: Request(error, {})})()

        return Service()

    def test_404_explains_sharing(self):
        with self.assertRaises(gcal.CalendarAccessError) as ctx:
            gcal.check_access(self._service(status=404), calendar_id="cal@example.com",
                              sa_email="bot@project.iam.gserviceaccount.com",
                              label="Flint Hills Chapter of DSA")
        message = str(ctx.exception)
        self.assertIn("bot@project.iam.gserviceaccount.com", message)
        self.assertIn("See all event details", message)
        self.assertIn("cal@example.com", message)

    def test_403_is_treated_the_same(self):
        with self.assertRaises(gcal.CalendarAccessError):
            gcal.check_access(self._service(status=403), calendar_id="c", sa_email="b",
                              label="chapter")

    def test_reachable_calendar_passes(self):
        gcal.check_access(self._service(status=None), calendar_id="c", sa_email="b",
                          label="chapter")


class TestEventsJsonEntries(unittest.TestCase):
    def setUp(self):
        self.config = make_config()

    def test_missing_end_time_gets_a_default_duration(self):
        entry = build_entry(
            {"id": "1", "summary": "[Meeting] General Body",
             "start": {"dateTime": "2026-08-10T18:00:00-05:00"}},
            self.config, tzinfo=CHICAGO,
        )
        self.assertEqual(entry["title"], "General Body")
        self.assertEqual(entry["committee"], "Meetings")
        self.assertEqual(entry["color"], "#616161")
        self.assertFalse(entry["allDay"])
        delta = dt.datetime.fromisoformat(entry["end"]) - dt.datetime.fromisoformat(entry["start"])
        self.assertEqual(delta, dt.timedelta(minutes=120))

    def test_all_day_event(self):
        entry = build_entry(
            {"id": "2", "summary": "May Day", "start": {"date": "2026-05-01"}, "end": {"date": "2026-05-02"}},
            self.config, tzinfo=CHICAGO,
        )
        self.assertTrue(entry["allDay"])

    def test_rsvp_link_is_extracted_from_the_description(self):
        entry = build_entry(
            {"id": "3", "summary": "[PolEd] Cadre School",
             "description": "Sign up: https://actionnetwork.org/events/cadre",
             "start": {"dateTime": "2026-09-01T19:00:00-05:00"},
             "end": {"dateTime": "2026-09-01T21:00:00-05:00"}},
            self.config, tzinfo=CHICAGO,
        )
        self.assertEqual(entry["committee"], "Political Education")
        self.assertEqual(entry["source"], "chapter")
        self.assertEqual(entry["url"], "https://actionnetwork.org/events/cadre")

    def test_event_without_an_rsvp_link_gets_no_url(self):
        entry = build_entry(
            {"id": "5", "summary": "[Social] Test Event", "description": "Test<br>",
             "htmlLink": "https://www.google.com/calendar/event?eid=abc",
             "start": {"dateTime": "2026-07-23T16:00:00-05:00"},
             "end": {"dateTime": "2026-07-23T17:00:00-05:00"}},
            self.config, tzinfo=CHICAGO,
        )
        self.assertIsNone(entry["url"], "must not fall back to the Google Calendar UI link")
        self.assertEqual(entry["title"], "Test Event")

    def test_undated_event_is_dropped(self):
        self.assertIsNone(build_entry({"id": "4", "summary": "x"}, self.config, tzinfo=CHICAGO))


class TestShippedConfig(unittest.TestCase):
    """The committed YAML must stay loadable and internally consistent."""

    def test_config_yml_parses_without_credentials(self):
        try:
            config = load_config(require_credentials=False)
        except Exception as exc:
            self.assertIn("placeholder", str(exc).lower())
            return
        self.assertTrue(config.committees)
        colors = [c.color for c in config.committees]
        self.assertEqual(len(colors), len(set(colors)), "committee badge colors should be distinct")

    def test_every_badge_colour_can_carry_readable_text(self):
        """Mirrors readableTextOn() in events-embed.js.

        The script picks white or near-black per badge, so a colour only needs
        to clear 4.5:1 against ONE of them. This catches a new committee colour
        that is unreadable either way.
        """
        def luminance(hex_colour: str) -> float:
            raw = hex_colour.lstrip("#")
            channels = []
            for i in (0, 2, 4):
                c = int(raw[i:i + 2], 16) / 255
                channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
            return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

        config = load_config(require_credentials=False)
        for committee in list(config.committees) + [config.default_committee]:
            lum = luminance(committee.color)
            best = max(1.05 / (lum + 0.05), (lum + 0.05) / 0.05)
            self.assertGreaterEqual(
                best, 4.5,
                f"{committee.name} ({committee.color}) fails 4.5:1 against both white and black",
            )

    def test_committee_tags_are_unique_across_committees(self):
        config = load_config(require_credentials=False)
        seen: dict[str, str] = {}
        for committee in config.committees:
            for tag in committee.tags:
                key = tag.lower()
                self.assertNotIn(
                    key, seen,
                    f"tag [{tag}] is claimed by both {seen.get(key)} and {committee.name}",
                )
                seen[key] = committee.name

    def test_embed_assets_exist_at_the_pages_root(self):
        for name in ("events-embed.js", "events-embed.css"):
            self.assertTrue((REPO_ROOT / name).exists(), f"{name} must sit at the repo root for Pages")


if __name__ == "__main__":
    unittest.main()
