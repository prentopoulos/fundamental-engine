"""Fetching both feeds and storing what is new.

The two feeds fail differently on purpose.

**The headline feed degrades.** A poll that returns nothing new is the normal case; a poll
that returns items already stored is de-duplicated by link and passed to nobody. Missing an
hour of headlines costs one hour of staleness, which the interface shows.

**The calendar does not degrade.** A body that will not parse aborts the pass before
a new board is published. Raw headlines already stored remain available for the next
attempt. A partial calendar could otherwise look misleadingly like a quiet week.
For the same reason a pass that ingests zero in-window
events records a warning that surfaces on every page: an empty calendar and a quiet week
must not present identically.

Rejected calendar rows are recorded against the pass rather than discarded. One row with an
unrecognised impact should not discard a good week — but it must not vanish either, or the
day the feed changes shape passes unnoticed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from sqlite3 import Connection

from engine.feeds.calendar_feed import CALENDAR_URL, parse_calendar
from engine.feeds.items import CalendarEvent, Headline
from engine.feeds.news_feed import NEWS_URL, parse_feed
from engine.feeds.news_sitemap import parse_news_sitemap
from engine.feeds.transport import Fetcher, fetch
from engine.store.schema import transaction

# Recorded on the pass and surfaced on every view. A name rather than a sentence so the
# interface can style it and a query can count it.
EMPTY_CALENDAR_WARNING = "empty_calendar_week"


@dataclass
class Ingestion:
    """What one ingestion saw. Returned rather than logged, so the pass can record it."""

    new_headlines: list[Headline] = field(default_factory=list)
    duplicate_headlines: int = 0
    events: list[CalendarEvent] = field(default_factory=list)
    in_window_events: list[CalendarEvent] = field(default_factory=list)
    rejections: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def ingest(
    connection: Connection,
    *,
    horizon: timedelta,
    now: datetime | None = None,
    fetcher: Fetcher = fetch,
    news_urls: tuple[str, ...] = (NEWS_URL,),
    sitemap_urls: tuple[str, ...] = (),
    calendar_url: str = CALENDAR_URL,
    with_calendar: bool = True,
) -> Ingestion:
    """Fetch, parse and store both feeds, returning what was new.

    `with_calendar` is false on the cheap headline poll: the week's schedule does not
    change between two hourly ticks often enough to be worth a second fetch, and the
    calendar's abort-on-bad-body rule would let a transient 502 kill a poll that only
    wanted headlines.
    """
    moment = now or datetime.now(UTC)
    result = Ingestion()

    # Several sections of the same publisher, each a separate RSS document capped at 25
    # items. They overlap heavily and de-duplicate by link, so the cost of reading all of
    # them is three fetches rather than three times the scoring.
    headlines: list[Headline] = []
    for url in news_urls:
        body = fetcher(url)
        headlines.extend(parse_feed(body.text, body.resolved_url))

    # The same publisher's news sitemap: same window, wider coverage, titles but no bodies.
    for url in sitemap_urls:
        body = fetcher(url)
        headlines.extend(parse_news_sitemap(body.text, body.resolved_url))

    result.new_headlines, result.duplicate_headlines = _store_headlines(
        connection, headlines, moment
    )

    if not with_calendar:
        return result

    calendar_body = fetcher(calendar_url)
    # Raises CalendarParseError before any calendar rows are stored. Raw headlines above
    # remain saved, but the pass cannot publish a read based on a missing calendar.
    events, rejections = parse_calendar(calendar_body.text)
    result.events = events
    result.rejections = rejections

    _store_events(connection, events, moment)

    result.in_window_events = [
        event for event in events if moment <= event.scheduled_at <= moment + horizon
    ]
    if not result.in_window_events:
        result.warnings.append(EMPTY_CALENDAR_WARNING)

    return result


def _store_headlines(
    connection: Connection, headlines: list[Headline], moment: datetime
) -> tuple[list[Headline], int]:
    """Insert new headlines, skipping ones already seen.

    De-duplication is by link, enforced by a unique index rather than by a prior SELECT:
    two polls racing would both pass a check-then-insert, and the second would create a
    duplicate that re-triggers scoring and double-counts a story.
    """
    fresh: list[Headline] = []
    duplicates = 0

    with transaction(connection):
        for headline in headlines:
            cursor = connection.execute(
                "INSERT INTO headlines (id, title, body, link, published_at, source_url,"
                " ingested_at, triaged)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 0)"
                " ON CONFLICT (link) DO NOTHING",
                (
                    headline.id,
                    headline.title,
                    headline.body,
                    headline.link,
                    headline.published_at.isoformat(),
                    headline.source_url,
                    moment.isoformat(),
                ),
            )
            if cursor.rowcount:
                fresh.append(headline)
            else:
                duplicates += 1

    return fresh, duplicates


def _store_events(connection: Connection, events: list[CalendarEvent], moment: datetime) -> None:
    """Upsert the week's schedule.

    Forecast and previous are updated in place: they move as a release approaches, and the
    event's identity deliberately excludes them so a revision is an update rather than a
    second row on the board.
    """
    with transaction(connection):
        for event in events:
            connection.execute(
                "INSERT INTO calendar_events (id, currency, title, scheduled_at, impact,"
                " forecast, forecast_text, previous, previous_text, ingested_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (id) DO UPDATE SET"
                "   forecast = excluded.forecast,"
                "   forecast_text = excluded.forecast_text,"
                "   previous = excluded.previous,"
                "   previous_text = excluded.previous_text,"
                "   ingested_at = excluded.ingested_at",
                (
                    event.id,
                    event.currency,
                    event.title,
                    event.scheduled_at.isoformat(),
                    str(event.impact),
                    event.forecast,
                    event.forecast_text,
                    event.previous,
                    event.previous_text,
                    moment.isoformat(),
                ),
            )


def untriaged(connection: Connection, limit: int | None = None) -> list[dict[str, object]]:
    """Headlines that have not been through triage yet, oldest first.

    The hourly pass is incremental: a quiet hour with three new headlines costs three
    triage calls, not a hundred. This query is what makes that true.
    """
    sql = "SELECT * FROM headlines WHERE triaged = 0 ORDER BY published_at"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return [dict(row) for row in connection.execute(sql)]


def unscored(connection: Connection, limit: int | None = None) -> list[dict[str, object]]:
    """Headlines triage kept that have not been scored yet, oldest first.

    A headline moves through three durable states — ingested, triaged, scored — and each
    is a committed fact before the next is attempted. That is what lets a pass resume: a
    run that triaged twenty-five headlines and then died at the scoring stage leaves them
    kept and unscored, and the next pass picks up exactly there rather than re-triaging
    them (wasted money) or skipping them forever (a permanently thin read).

    Headlines triage dropped are never scored, so they never appear here.
    """
    sql = "SELECT * FROM headlines WHERE triaged = 1 AND scored_at IS NULL ORDER BY published_at"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return [dict(row) for row in connection.execute(sql)]
