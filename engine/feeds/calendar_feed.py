"""Parse scheduled economic releases from a JSON calendar.

A malformed document aborts ingestion. Invalid individual rows are returned as
rejections so a changed feed format remains visible to the operator.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from engine.feeds.items import CalendarEvent, Impact, parse_reading
from engine.feeds.transport import FetchError

CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


class CalendarParseError(FetchError):
    """The calendar body could not be understood.

    A subclass of `FetchError` so a caller aborting the pass on "the calendar did not
    arrive" also aborts on "the calendar arrived and was gibberish". From the pass's point
    of view those are the same event: there is no usable calendar.
    """


class MalformedEventError(ValueError):
    """One event could not be read. Reported, never coerced to a default."""


def parse_calendar(body: str) -> tuple[list[CalendarEvent], list[str]]:
    """Read the feed into events, returning the events and the rejections.

    Rejections are returned rather than raised. A single event with an unrecognised impact
    should not discard a week of correctly-formed ones - but it must not vanish either, so
    it comes back as a named problem the caller records.
    """
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        raise CalendarParseError(f"calendar body is not JSON: {error}") from error

    if not isinstance(payload, list):
        raise CalendarParseError(
            f"calendar body is {type(payload).__name__}, expected a list of events"
        )

    events: list[CalendarEvent] = []
    rejected: list[str] = []
    for index, row in enumerate(payload):
        try:
            events.append(_event(row))
        except (MalformedEventError, UnicodeDecodeError) as error:
            rejected.append(f"row {index}: {error}")

    return events, rejected


def _event(row: object) -> CalendarEvent:
    if not isinstance(row, dict):
        raise MalformedEventError(f"expected an object, got {type(row).__name__}")

    title = str(row.get("title", "")).strip()
    currency = str(row.get("country", "")).strip().upper()
    if not title or not currency:
        raise MalformedEventError("missing title or country")

    scheduled_at = _timestamp(row.get("date"))

    raw_impact = str(row.get("impact", "")).strip()
    try:
        impact = Impact(raw_impact)
    except ValueError as error:
        raise MalformedEventError(f"unknown impact {raw_impact!r}") from error

    return CalendarEvent(
        currency=currency,
        title=title,
        scheduled_at=scheduled_at,
        impact=impact,
        actual=parse_reading(_text(row.get("actual"))),
        actual_text=_text(row.get("actual")),
        forecast=parse_reading(_text(row.get("forecast"))),
        forecast_text=_text(row.get("forecast")),
        previous=parse_reading(_text(row.get("previous"))),
        previous_text=_text(row.get("previous")),
    )


def _text(value: object) -> str | None:
    """Empty strings become absent; the feed uses "" for "no reading"."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _timestamp(value: object) -> datetime:
    """The feed quotes an offset (`-04:00`); it is converted, never assumed."""
    if not isinstance(value, str) or not value.strip():
        raise MalformedEventError("missing date")
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as error:
        raise MalformedEventError(f"unreadable date {value!r}") from error
    if parsed.tzinfo is None:
        raise MalformedEventError(f"date {value!r} carries no timezone")
    return parsed.astimezone(UTC)
