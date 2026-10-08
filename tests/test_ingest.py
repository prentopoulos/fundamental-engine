"""Ingestion: de-duplication, the calendar's refusal to degrade, and the empty week."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from engine.feeds.calendar_feed import CalendarParseError
from engine.feeds.ingest import EMPTY_CALENDAR_WARNING, ingest, untriaged
from engine.feeds.news_feed import FeedParseError
from engine.feeds.transport import FetchedBody
from engine.store.schema import connect

# The fixtures are scheduled in the week beginning 2026-09-01.
NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
HORIZON = timedelta(days=7)


@pytest.fixture
def db(tmp_path):
    connection = connect(tmp_path / "engine.db")
    yield connection
    connection.close()


def stub_fetcher(fixture_body, news="news_good.xml", calendar="calendar_good.json"):
    """A fetcher that answers from recorded bodies. Nothing here reaches the network."""

    def _fetch(url: str) -> FetchedBody:
        name = calendar if "calendar" in url else news
        resolved = url.replace("forexlive.com", "investinglive.com")
        return FetchedBody(text=fixture_body(name), resolved_url=resolved)

    return _fetch


def test_a_first_pass_stores_every_complete_headline(db, fixture_body):
    result = ingest(db, horizon=HORIZON, now=NOW, fetcher=stub_fetcher(fixture_body))

    assert len(result.new_headlines) == 2
    assert result.duplicate_headlines == 0
    assert db.execute("SELECT count(*) FROM headlines").fetchone()[0] == 2


def test_the_resolved_url_is_recorded_not_the_requested_one(db, fixture_body):
    """The feed rebranded: forexlive 301s to investinglive, and it is the second that answered."""
    ingest(db, horizon=HORIZON, now=NOW, fetcher=stub_fetcher(fixture_body))

    sources = {row[0] for row in db.execute("SELECT DISTINCT source_url FROM headlines")}
    assert sources == {"https://www.investinglive.com/feed/news"}


def test_a_repeated_poll_creates_no_duplicates_and_re_triggers_no_scoring(db, fixture_body):
    """The whole point of de-duplicating by link: an unchanged feed costs nothing."""
    ingest(db, horizon=HORIZON, now=NOW, fetcher=stub_fetcher(fixture_body))
    second = ingest(db, horizon=HORIZON, now=NOW, fetcher=stub_fetcher(fixture_body))

    assert second.new_headlines == []
    assert second.duplicate_headlines == 2
    assert db.execute("SELECT count(*) FROM headlines").fetchone()[0] == 2


def test_only_genuinely_new_items_are_passed_on(db, fixture_body, tmp_path):
    ingest(db, horizon=HORIZON, now=NOW, fetcher=stub_fetcher(fixture_body))

    extended = tmp_path / "news_more.xml"
    original = fixture_body("news_good.xml")
    extended_body = original.replace(
        "</channel>",
        "<item><title>BoJ leaves policy unchanged</title>"
        "<description>As expected.</description>"
        "<link>https://www.investinglive.com/centralbank/boj-20260831</link>"
        "<pubDate>Mon, 31 Aug 2026 15:00:00 GMT</pubDate></item></channel>",
    )
    extended.write_text(extended_body, encoding="utf-8")

    def fetcher(url: str) -> FetchedBody:
        if "calendar" in url:
            return FetchedBody(text=fixture_body("calendar_good.json"), resolved_url=url)
        return FetchedBody(text=extended_body, resolved_url=url)

    result = ingest(db, horizon=HORIZON, now=NOW, fetcher=fetcher)

    assert [h.title for h in result.new_headlines] == ["BoJ leaves policy unchanged"]
    assert result.duplicate_headlines == 2


def test_only_untriaged_headlines_come_back_for_triage(db, fixture_body):
    ingest(db, horizon=HORIZON, now=NOW, fetcher=stub_fetcher(fixture_body))
    db.execute("UPDATE headlines SET triaged = 1 WHERE title LIKE 'ECB%'")

    pending = untriaged(db)

    assert [row["title"] for row in pending] == ["US ISM manufacturing 47.8 vs 48.4 expected"]


# --- The calendar --------------------------------------------------------------------


def test_the_week_is_stored_with_utc_timestamps(db, fixture_body):
    result = ingest(db, horizon=HORIZON, now=NOW, fetcher=stub_fetcher(fixture_body))

    assert len(result.events) == 4
    stored = dict(db.execute("SELECT currency, scheduled_at FROM calendar_events"))
    assert stored["EUR"] == "2026-09-03T12:15:00+00:00"


def test_a_re_ingested_event_updates_its_forecast_rather_than_duplicating(db, fixture_body):
    """Forecast and previous move as a release approaches; identity deliberately excludes them."""
    ingest(db, horizon=HORIZON, now=NOW, fetcher=stub_fetcher(fixture_body))
    revised = fixture_body("calendar_good.json").replace('"forecast": "78K"', '"forecast": "62K"')

    def fetcher(url: str) -> FetchedBody:
        body = revised if "calendar" in url else fixture_body("news_good.xml")
        return FetchedBody(text=body, resolved_url=url)

    ingest(db, horizon=HORIZON, now=NOW, fetcher=fetcher)

    sql = "SELECT forecast_text FROM calendar_events WHERE currency = 'USD'"
    rows = db.execute(sql).fetchall()
    assert len(rows) == 1
    assert rows[0]["forecast_text"] == "62K"


def test_rejected_rows_are_recorded_against_the_pass_rather_than_discarded(db, fixture_body):
    """The day the feed changes shape must not pass unnoticed."""
    result = ingest(
        db,
        horizon=HORIZON,
        now=NOW,
        fetcher=stub_fetcher(fixture_body, calendar="calendar_one_bad_row.json"),
    )

    assert len(result.rejections) == 3
    assert len(result.events) == 2
    assert db.execute("SELECT count(*) FROM calendar_events").fetchone()[0] == 2


def test_an_unparseable_calendar_body_aborts_and_stores_nothing(db, fixture_body):
    """A partial calendar is indistinguishable from a quiet week, so there is no partial."""
    with pytest.raises(CalendarParseError):
        ingest(
            db,
            horizon=HORIZON,
            now=NOW,
            fetcher=stub_fetcher(fixture_body, calendar="calendar_not_json.html"),
        )

    assert db.execute("SELECT count(*) FROM calendar_events").fetchone()[0] == 0


def test_a_hostile_headline_body_aborts_before_anything_is_stored(db, fixture_body):
    with pytest.raises(FeedParseError):
        ingest(
            db,
            horizon=HORIZON,
            now=NOW,
            fetcher=stub_fetcher(fixture_body, news="news_hostile.xml"),
        )

    assert db.execute("SELECT count(*) FROM headlines").fetchone()[0] == 0


def test_zero_in_window_events_records_a_warning(db, fixture_body):
    """An empty calendar and a quiet week must not look alike."""
    long_past = datetime(2026, 12, 1, tzinfo=UTC)

    result = ingest(db, horizon=HORIZON, now=long_past, fetcher=stub_fetcher(fixture_body))

    assert result.in_window_events == []
    assert EMPTY_CALENDAR_WARNING in result.warnings


def test_a_week_with_events_records_no_warning(db, fixture_body):
    result = ingest(db, horizon=HORIZON, now=NOW, fetcher=stub_fetcher(fixture_body))

    assert result.warnings == []
    assert len(result.in_window_events) == 4


def test_the_cheap_poll_skips_the_calendar_entirely(db, fixture_body):
    """A transient 502 on the calendar must not kill a poll that only wanted headlines."""

    def fetcher(url: str) -> FetchedBody:
        assert "calendar" not in url, "the headline poll fetched the calendar"
        return FetchedBody(text=fixture_body("news_good.xml"), resolved_url=url)

    result = ingest(db, horizon=HORIZON, now=NOW, fetcher=fetcher, with_calendar=False)

    assert len(result.new_headlines) == 2
    assert result.events == []
    assert result.warnings == []
