"""What an ingested item is, and how it keeps the same identity across passes.

Passes run over overlapping windows, so the same headline arrives repeatedly.
Identity is therefore derived from stable fields of the item itself - never from ingestion
order or fetch time - so the second sighting is recognisably the same row.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict

# Multipliers the calendar feed uses inline: "2.50T", "-1.2K".
_SUFFIXES: Final = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}
_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")


class Impact(StrEnum):
    """The supported calendar impact categories."""

    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"
    HOLIDAY = "Holiday"


class UnknownImpactError(ValueError):
    """An impact level outside the four the feed publishes."""


def parse_reading(raw: str | None) -> float | None:
    """Turn a feed reading into a number, or into nothing.

    Returns `None` rather than `0.0` when there is no reading. The calendar quotes an
    absent value as an empty string and a genuine zero as "0", so they must not
    collapse into the same value.
    """
    if raw is None:
        return None
    text = raw.strip().replace(",", "").replace("%", "")
    if not text:
        return None

    multiplier = 1.0
    if text[-1].upper() in _SUFFIXES:
        multiplier = _SUFFIXES[text[-1].upper()]
        text = text[:-1]

    if not _NUMBER.match(text):
        # Readings like "<0.1" or "Tentative" keep their text and lose their number rather
        # than being coerced into a wrong one.
        return None
    return float(text) * multiplier


def _identity(*parts: str) -> str:
    joined = "\x1f".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


class CalendarEvent(BaseModel):
    """One scheduled release. Optional actual fields are retained for compatible feeds;
    the forward-reading pipeline uses forecast, previous, and schedule only.
    """

    model_config = ConfigDict(frozen=True)

    currency: str
    title: str
    scheduled_at: datetime
    impact: Impact
    actual: float | None = None
    actual_text: str | None = None
    forecast: float | None = None
    forecast_text: str | None = None
    previous: float | None = None
    previous_text: str | None = None

    @property
    def id(self) -> str:
        """Identity is what the event *is*, not what it currently reads.

        Forecast, previous and actual all change as a release approaches and lands. If any
        of them entered the identity, a revision would arrive as a second event rather than
        as an update to the first.
        """
        return _identity("calendar", self.currency, self.title, self.scheduled_at.isoformat())

    @property
    def has_actual(self) -> bool:
        return self.actual is not None or bool(self.actual_text)


class Headline(BaseModel):
    """One news item, with its publication time and source link."""

    model_config = ConfigDict(frozen=True)

    title: str
    body: str
    link: str
    published_at: datetime
    source_url: str

    @property
    def id(self) -> str:
        """The canonical link, which the feed guarantees per item and reuses on re-publish."""
        return _identity("headline", self.link)
