"""Feed parsing tests for stable identity, malformed input, and hostile XML.

Fixture bodies keep the tests offline and make format changes easy to reproduce.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from engine.feeds.calendar_feed import CalendarParseError, parse_calendar
from engine.feeds.items import Impact
from engine.feeds.news_feed import FeedParseError, parse_feed

SOURCE = "https://www.investinglive.com/feed/news"


def test_good_rss_yields_one_headline_per_complete_item(fixture_body):
    headlines = parse_feed(fixture_body("news_good.xml"), SOURCE)

    assert [h.title for h in headlines] == [
        "ECB's Lagarde: inflation risks remain tilted to the upside",
        "US ISM manufacturing 47.8 vs 48.4 expected",
    ]
    assert headlines[0].published_at == datetime(2026, 8, 31, 9, 14, tzinfo=UTC)
    assert headlines[0].source_url == SOURCE


def test_html_in_the_body_is_reduced_to_text(fixture_body):
    """Markup that reaches a prompt is tokens spent on nothing, and a place to hide."""
    body = parse_feed(fixture_body("news_good.xml"), SOURCE)[0].body

    assert "<" not in body and ">" not in body
    assert "Governing Council" in body
    assert "Rates to stay restrictive" in body


def test_items_missing_identity_fields_are_dropped_not_guessed(fixture_body):
    """No link, no title, or no parseable date — all three are dropped."""
    links = {h.link for h in parse_feed(fixture_body("news_good.xml"), SOURCE)}

    assert "https://www.investinglive.com/data/anonymous" not in links
    assert "https://www.investinglive.com/data/baddate" not in links
    assert len(links) == 2


def test_hostile_payload_is_refused_whole(fixture_body):
    """A DTD-bearing body raises rather than returning the items it managed to read."""
    with pytest.raises(FeedParseError):
        parse_feed(fixture_body("news_hostile.xml"), SOURCE)


def test_headline_identity_is_the_link_so_a_repeat_poll_matches(fixture_body):
    first = parse_feed(fixture_body("news_good.xml"), SOURCE)
    second = parse_feed(fixture_body("news_good.xml"), SOURCE)

    assert [h.id for h in first] == [h.id for h in second]


def test_good_calendar_converts_every_row_to_utc(fixture_body):
    events, rejected = parse_calendar(fixture_body("calendar_good.json"))

    assert rejected == []
    assert len(events) == 4
    ecb = next(e for e in events if e.currency == "EUR")
    # 08:15 at -04:00 is 12:15 UTC. Converted from the quoted offset, never assumed.
    assert ecb.scheduled_at == datetime(2026, 9, 3, 12, 15, tzinfo=UTC)
    assert ecb.impact is Impact.HIGH
    assert ecb.forecast == pytest.approx(2.15)


def test_calendar_carries_no_actual_on_any_row(fixture_body):
    """The property the whole forward lane rests on. Verified against the live feed."""
    events, _ = parse_calendar(fixture_body("calendar_good.json"))

    assert all(not event.has_actual for event in events)


def test_malformed_rows_are_returned_as_named_rejections(fixture_body):
    """One bad row must not discard a week of good ones, and must not vanish either."""
    events, rejected = parse_calendar(fixture_body("calendar_one_bad_row.json"))

    assert [e.currency for e in events] == ["EUR", "USD"]
    assert len(rejected) == 3
    assert any("unknown impact" in r and "Catastrophic" in r for r in rejected)
    assert any("missing title or country" in r for r in rejected)
    assert any("carries no timezone" in r for r in rejected)


def test_a_timestamp_without_an_offset_is_rejected_not_assumed_utc(fixture_body):
    _, rejected = parse_calendar(fixture_body("calendar_one_bad_row.json"))

    assert any("2026-09-03T21:45:00" in r for r in rejected)


def test_a_non_json_body_aborts_with_no_events(fixture_body):
    """A partial calendar is indistinguishable from a quiet week, so there is no partial."""
    with pytest.raises(CalendarParseError, match="not JSON"):
        parse_calendar(fixture_body("calendar_not_json.html"))


def test_a_json_body_that_is_not_a_list_aborts(fixture_body):
    with pytest.raises(CalendarParseError, match="expected a list"):
        parse_calendar(fixture_body("calendar_not_a_list.json"))
