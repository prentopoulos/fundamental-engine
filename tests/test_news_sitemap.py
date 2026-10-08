"""The news sitemap parser: the same rejection rules as the RSS parser, a different shape."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from engine.feeds.news_feed import FeedParseError
from engine.feeds.news_sitemap import parse_news_sitemap

SOURCE = "https://investinglive.com/latest.xml"


def test_a_well_formed_sitemap_yields_one_headline_per_complete_entry(fixture_body):
    headlines = parse_news_sitemap(fixture_body("news_sitemap.xml"), SOURCE)

    assert [h.title for h in headlines] == [
        "ECB's Lagarde: inflation risks remain tilted to the upside",
        "US ISM manufacturing 47.8 vs 48.4 expected",
    ]


def test_timestamps_are_read_as_iso_and_normalised_to_utc(fixture_body):
    """Sitemaps date in ISO 8601 where RSS uses RFC 822."""
    headlines = parse_news_sitemap(fixture_body("news_sitemap.xml"), SOURCE)

    assert headlines[0].published_at == datetime(2026, 8, 31, 9, 14, tzinfo=UTC)
    assert headlines[1].published_at == datetime(2026, 8, 31, 14, 0, 11, tzinfo=UTC)


def test_entries_missing_identity_fields_are_dropped(fixture_body):
    """No title, no offset on the date, or no news block at all — all dropped, not guessed."""
    links = {h.link for h in parse_news_sitemap(fixture_body("news_sitemap.xml"), SOURCE)}

    assert "https://investinglive.com/news/no-title/" not in links
    assert "https://investinglive.com/news/floating-date/" not in links
    assert "https://investinglive.com/news/not-an-article/" not in links


def test_bodies_are_empty_rather_than_a_repeat_of_the_title(fixture_body):
    """A sitemap carries no body. Echoing the title would hand the scoring stage the same
    sentence twice as though it were corroboration."""
    headlines = parse_news_sitemap(fixture_body("news_sitemap.xml"), SOURCE)

    assert all(h.body == "" for h in headlines)


def test_identity_matches_the_rss_parser_so_the_two_sources_de_duplicate(fixture_body):
    """Both parsers derive identity from the link, which is what makes the overlap free."""
    from engine.feeds.items import Headline

    (first, _) = parse_news_sitemap(fixture_body("news_sitemap.xml"), SOURCE)
    same_link = Headline(
        title="different wording",
        body="x",
        link=first.link,
        published_at=first.published_at,
        source_url="elsewhere",
    )

    assert same_link.id == first.id


def test_a_hostile_sitemap_is_refused_whole(fixture_body):
    with pytest.raises(FeedParseError):
        parse_news_sitemap(fixture_body("news_hostile.xml"), SOURCE)


def test_an_unparseable_body_raises_rather_than_returning_nothing(fixture_body):
    """Silently returning zero headlines would look exactly like a quiet news day."""
    with pytest.raises(FeedParseError):
        parse_news_sitemap("this is not xml at all <<<", SOURCE)
