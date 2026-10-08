"""Parsing a Google News sitemap into the same `Headline` records the RSS parser produces.

The publisher serves its recent articles twice: as RSS, capped at 25 items per section, and
as a news sitemap at `latest.xml`, which carries ~78 entries with `<news:title>` and a
publication date. The two overlap heavily but not completely — measured 2026-09-01, the
sitemap held 35 articles none of the three RSS sections did.

**This is not the article archive.** That exists — 943 weekly files back to 2008 — and it is
useless here: those files carry `<loc>` and nothing else, so reading them would mean fetching
and parsing each article's HTML, which the methodology guide rules out and which would add a second
untrusted boundary for the sake of headlines that `ARCHIVE_AFTER` discards anyway.

**Bodies are absent by construction.** A sitemap entry is a title, a URL and a timestamp. The
scoring stage is given an empty body for these, which is thinner input than an RSS item — but
these titles are written to stand alone ("Japan manufacturing PMI hits 54.9 as new orders
surge most since 2018"), and a scored title is worth more than a headline never seen.

Hardened the same way as the RSS parser: `defusedxml`, and an entry missing a title, a link
or a parseable date is dropped rather than stored with a guessed identity.
"""

from __future__ import annotations

from datetime import UTC, datetime

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import ParseError as DefusedParseError
from defusedxml.ElementTree import fromstring

from engine.feeds.items import Headline
from engine.feeds.news_feed import FeedParseError, strip_html

LATEST_URL = "https://investinglive.com/latest.xml"

_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
_NEWS_NS = "{http://www.google.com/schemas/sitemap-news/0.9}"


def parse_news_sitemap(body: str, source_url: str) -> list[Headline]:
    """Read a news sitemap into headlines.

    Raises on a body that will not parse, exactly as the RSS parser does: a sitemap is one
    document, so a document that does not parse has no partially-usable portion to salvage.
    """
    try:
        root = fromstring(body)
    except DefusedXmlException as error:
        raise FeedParseError(f"sitemap was refused by the hardened parser: {error}") from error
    except DefusedParseError as error:
        raise FeedParseError(f"sitemap is not parseable XML: {error}") from error

    headlines: list[Headline] = []
    for entry in root.iter(f"{_SITEMAP_NS}url"):
        headline = _headline(entry, source_url)
        if headline is not None:
            headlines.append(headline)
    return headlines


def _headline(entry: object, source_url: str) -> Headline | None:
    find = getattr(entry, "find", None)
    if find is None:  # pragma: no cover - defensive
        return None

    link = _text(find(f"{_SITEMAP_NS}loc"))
    news = find(f"{_NEWS_NS}news")
    if news is None:
        return None

    title = _text(news.find(f"{_NEWS_NS}title"))
    published = _published(_text(news.find(f"{_NEWS_NS}publication_date")))

    # Same rule as the RSS parser: no title, no link or no time means no identity, so the
    # entry is dropped rather than stored under a guess.
    if not title or not link or published is None:
        return None

    return Headline(
        title=strip_html(title),
        # A sitemap carries no body. Empty rather than a repeat of the title, so the
        # scoring stage is not handed the same sentence twice as if it were corroboration.
        body="",
        link=link,
        published_at=published,
        source_url=source_url,
    )


def _text(node: object) -> str | None:
    if node is None:
        return None
    text = getattr(node, "text", None)
    return text.strip() if isinstance(text, str) and text.strip() else None


def _published(raw: str | None) -> datetime | None:
    """Sitemaps date in ISO 8601, where RSS uses RFC 822."""
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # Same refusal as the calendar parser: a timestamp with no offset is rejected
        # rather than assumed to be UTC.
        return None
    return parsed.astimezone(UTC)
