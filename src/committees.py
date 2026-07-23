"""Committee resolution from an event title, plus title cleanup.

Chapter events originate in Discord, so the only routing signal that survives
into Google Calendar is a bracket tag at the front of the event name:

    "[Housing] Tenant Union Kickoff"  ->  Housing Justice and Tenant Organizing

Resolution order, per the migration brief:
  1. leading bracket tag  (authoritative)
  2. keyword match against the title  (best-effort guess)
  3. the configured default committee  ("General")
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Committee, Config

# A leading "[Tag]" -- tolerant of surrounding whitespace and a trailing
# separator such as "[PolEd] - Reading Group" or "[Social]: Picnic".
_LEADING_TAG_RE = re.compile(r"^\s*[\[\(]\s*([^\]\)]{1,40})\s*[\]\)]\s*[-–—:]?\s*")

# Action Network RSVP links, as pasted into a Discord/Calendar description.
_RSVP_RE = re.compile(r"https?://(?:www\.)?actionnetwork\.org/\S+", re.IGNORECASE)
# Any http(s) URL, used only as a last-resort fallback.
_ANY_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
# Trailing punctuation that commonly rides along when a URL ends a sentence.
_URL_TRAILING = ".,;:!?)\"'>]}"


@dataclass(frozen=True)
class Resolved:
    title: str          # display title, bracket tag stripped
    committee: str
    color: str
    matched_by: str     # "tag" | "keyword" | "default"


def strip_tag(title: str) -> tuple[str, str | None]:
    """Split a leading bracket tag off a title.

    Returns (clean_title, tag_or_None). The tag is returned verbatim; callers
    lowercase it for matching.
    """
    raw = (title or "").strip()
    match = _LEADING_TAG_RE.match(raw)
    if not match:
        return raw, None
    remainder = raw[match.end():].strip()
    # A title that is nothing but a tag keeps its original text rather than
    # collapsing to an empty string.
    if not remainder:
        return raw, None
    return remainder, match.group(1).strip()


def resolve(title: str, config: Config) -> Resolved:
    """Resolve a raw event title to a display title plus committee + color."""
    clean, tag = strip_tag(title)

    if tag:
        needle = tag.lower()
        for committee in config.committees:
            if any(needle == t.lower() for t in committee.tags):
                return Resolved(clean, committee.name, committee.color, "tag")

    haystack = clean.lower()
    for committee in config.committees:
        if any(keyword in haystack for keyword in committee.keywords):
            return Resolved(clean, committee.name, committee.color, "keyword")

    fallback = config.default_committee
    return Resolved(clean, fallback.name, fallback.color, "default")


def committee_color(name: str, config: Config) -> str:
    """Badge color for a committee name, for callers that already have a name."""
    for committee in config.committees:
        if committee.name == name:
            return committee.color
    return config.default_committee.color


def _clean_url(url: str) -> str:
    return url.rstrip(_URL_TRAILING)


def extract_rsvp_url(description: str | None, *, fallback: str | None = None) -> str | None:
    """Pull the RSVP link out of a description.

    Prefers an Action Network URL -- RSVP capture stays in Action Network and we
    only surface the link. Falls back to the first URL of any kind, then to the
    caller-supplied fallback (typically the source event's own page).
    """
    text = description or ""
    match = _RSVP_RE.search(text)
    if match:
        return _clean_url(match.group(0))
    match = _ANY_URL_RE.search(text)
    if match:
        return _clean_url(match.group(0))
    return fallback
