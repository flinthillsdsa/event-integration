"""Credential-free tests for the pure logic: tag parsing, ids, dedup, entries.

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
from src.aggregate import normalized_key, to_calendar_body  # noqa: E402
from src.committees import extract_rsvp_url, resolve, strip_tag  # noqa: E402
from src.config import REPO_ROOT, Committee, Config, load_config, load_sources  # noqa: E402
from src.events_json import build_entry  # noqa: E402
from src.feeds import NormalizedEvent  # noqa: E402

CHICAGO = ZoneInfo("America/Chicago")


def make_config() -> Config:
    return Config(
        chapter_calendar_id="chapter@example.com",
        national_calendar_id="national@example.com",
        timezone="America/Chicago",
        horizon_days=180,
        past_window_days=1,
        default_duration_minutes=120,
        window_days=60,
        output_path=Path("events.json"),
        max_description_chars=600,
        committees=[
            Committee("Housing Justice and Tenant Organizing", ("Housing", "HJTO"), "#0b8043", ("tenant", "housing")),
            Committee("Political Education", ("PolEd",), "#8e24aa", ("reading", "education")),
            Committee("Meetings", ("Meeting",), "#616161", ("general body",)),
        ],
        default_committee=Committee("General", (), "#546e7a", ()),
        national_committee=Committee("National", (), "#ec1f27", ()),
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

    def test_national_overrides_everything(self):
        resolved = resolve("[Housing] National Call", self.config, national=True)
        self.assertEqual(resolved.committee, "National")
        self.assertEqual(resolved.title, "National Call")


class TestRsvpExtraction(unittest.TestCase):
    def test_prefers_action_network(self):
        text = "Details at https://example.org/info and RSVP https://actionnetwork.org/events/foo now."
        self.assertEqual(extract_rsvp_url(text), "https://actionnetwork.org/events/foo")

    def test_strips_trailing_punctuation(self):
        self.assertEqual(
            extract_rsvp_url("RSVP: https://actionnetwork.org/events/bar."),
            "https://actionnetwork.org/events/bar",
        )

    def test_falls_back(self):
        self.assertEqual(extract_rsvp_url("", fallback="https://calendar.google.com/x"),
                         "https://calendar.google.com/x")
        self.assertIsNone(extract_rsvp_url(None))


class TestEventIds(unittest.TestCase):
    def test_deterministic_and_valid(self):
        first = gcal.derive_event_id("NPEC", "abc-123")
        second = gcal.derive_event_id("NPEC", "abc-123")
        self.assertEqual(first, second)
        self.assertTrue(gcal._BASE32HEX_RE.match(first), first)

    def test_distinct_per_source(self):
        self.assertNotEqual(gcal.derive_event_id("NPEC", "abc"), gcal.derive_event_id("KCDSA", "abc"))

    def test_weird_uids_still_produce_valid_ids(self):
        for uid in ["", "UID with spaces!", "ünïcode✨", "x" * 5000]:
            self.assertTrue(gcal._BASE32HEX_RE.match(gcal.derive_event_id("s", uid)))


class TestDedup(unittest.TestCase):
    def _event(self, title, start, source="A"):
        return NormalizedEvent(
            uid="u", title=title, description="", location="",
            start=start, end=start + dt.timedelta(hours=1), url=None, source=source,
        )

    def test_same_title_and_start_collide_across_sources(self):
        start = dt.datetime(2026, 8, 1, 18, 0, tzinfo=CHICAGO)
        a = self._event("DSA National Call", start, "A")
        b = self._event("  dsa   national call!  ", start, "B")
        self.assertEqual(normalized_key(a), normalized_key(b))

    def test_different_start_does_not_collide(self):
        start = dt.datetime(2026, 8, 1, 18, 0, tzinfo=CHICAGO)
        self.assertNotEqual(
            normalized_key(self._event("X", start)),
            normalized_key(self._event("X", start + dt.timedelta(hours=1))),
        )

    def test_equivalent_instants_in_different_zones_collide(self):
        chicago = dt.datetime(2026, 8, 1, 18, 0, tzinfo=CHICAGO)
        utc = chicago.astimezone(dt.timezone.utc)
        self.assertEqual(normalized_key(self._event("X", chicago)), normalized_key(self._event("X", utc)))


class TestIdempotency(unittest.TestCase):
    def test_content_hash_is_stable_across_rebuilds(self):
        event = NormalizedEvent(
            uid="u", title="[PolEd] Reading", description="d", location="Library",
            start=dt.datetime(2026, 8, 1, 18, 0, tzinfo=CHICAGO),
            end=dt.datetime(2026, 8, 1, 20, 0, tzinfo=CHICAGO),
            url="https://actionnetwork.org/events/x", source="NPEC", region="National",
        )
        first = gcal.content_hash(to_calendar_body(event, "America/Chicago"))
        second = gcal.content_hash(to_calendar_body(event, "America/Chicago"))
        self.assertEqual(first, second)

    def test_hash_changes_when_a_written_field_changes(self):
        base = NormalizedEvent(
            uid="u", title="A", description="", location="",
            start=dt.datetime(2026, 8, 1, 18, 0, tzinfo=CHICAGO),
            end=dt.datetime(2026, 8, 1, 20, 0, tzinfo=CHICAGO),
            url=None, source="NPEC",
        )
        moved = NormalizedEvent(**{**base.__dict__, "start": base.start + dt.timedelta(hours=1)})
        self.assertNotEqual(
            gcal.content_hash(to_calendar_body(base, "America/Chicago")),
            gcal.content_hash(to_calendar_body(moved, "America/Chicago")),
        )

    def test_all_day_events_get_date_nodes_with_an_exclusive_end(self):
        event = NormalizedEvent(
            uid="u", title="A", description="", location="",
            start=dt.datetime(2026, 8, 1, tzinfo=CHICAGO),
            end=dt.datetime(2026, 8, 1, tzinfo=CHICAGO),
            url=None, source="NPEC", all_day=True,
        )
        body = to_calendar_body(event, "America/Chicago")
        self.assertEqual(body["start"], {"date": "2026-08-01"})
        self.assertEqual(body["end"], {"date": "2026-08-02"})


class TestManagedGuards(unittest.TestCase):
    def test_unmanaged_events_are_not_recognised(self):
        self.assertFalse(gcal._is_managed({"id": "x"}))
        self.assertFalse(gcal._is_managed({"extendedProperties": {"private": {"managedBy": "someone-else"}}}))

    def test_managed_events_are_recognised(self):
        self.assertTrue(gcal._is_managed(
            {"extendedProperties": {"private": {"managedBy": gcal.MANAGED_BY_VALUE}}}
        ))

    def test_delete_refuses_unmanaged(self):
        class Boom:
            def events(self):  # pragma: no cover - must never be reached
                raise AssertionError("delete_managed touched the API for an unmanaged event")

        self.assertFalse(gcal.delete_managed(Boom(), calendar_id="c", event={"id": "x"}))


class TestEventsJsonEntries(unittest.TestCase):
    def setUp(self):
        self.config = make_config()

    def test_missing_end_time_gets_a_default_duration(self):
        entry = build_entry(
            {"id": "1", "summary": "[Meeting] General Body",
             "start": {"dateTime": "2026-08-10T18:00:00-05:00"}},
            self.config, source="chapter", tzinfo=CHICAGO,
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
            self.config, source="chapter", tzinfo=CHICAGO,
        )
        self.assertTrue(entry["allDay"])

    def test_national_source_labels_and_extracts_rsvp(self):
        entry = build_entry(
            {"id": "3", "summary": "[PolEd] Cadre School",
             "description": "Sign up: https://actionnetwork.org/events/cadre",
             "start": {"dateTime": "2026-09-01T19:00:00-05:00"},
             "end": {"dateTime": "2026-09-01T21:00:00-05:00"}},
            self.config, source="national", tzinfo=CHICAGO,
        )
        self.assertEqual(entry["committee"], "National")
        self.assertEqual(entry["source"], "national")
        self.assertEqual(entry["url"], "https://actionnetwork.org/events/cadre")

    def test_undated_event_is_dropped(self):
        self.assertIsNone(build_entry({"id": "4", "summary": "x"}, self.config,
                                      source="chapter", tzinfo=CHICAGO))


class TestShippedConfig(unittest.TestCase):
    """The committed YAML must stay loadable and internally consistent."""

    def test_feeds_yml_parses(self):
        sources = load_sources()
        self.assertTrue(sources, "feeds.yml should seed at least one source")
        for source in sources:
            self.assertIn(source.type, {"ical", "gcal"})

    def test_config_yml_parses_without_credentials(self):
        # Placeholder calendar ids are expected until the real ones are filled in.
        try:
            config = load_config(require_credentials=False)
        except Exception as exc:
            self.assertIn("placeholder", str(exc).lower())
            return
        self.assertTrue(config.committees)
        colors = [c.color for c in config.committees]
        self.assertEqual(len(colors), len(set(colors)), "committee badge colors should be distinct")

    def test_embed_assets_exist_at_the_pages_root(self):
        for name in ("events-embed.js", "events-embed.css"):
            self.assertTrue((REPO_ROOT / name).exists(), f"{name} must sit at the repo root for Pages")


if __name__ == "__main__":
    unittest.main()
