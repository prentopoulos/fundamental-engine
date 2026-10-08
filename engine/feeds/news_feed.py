"""Parse RSS headlines using a hardened XML parser.

Feed text is untrusted. The parser rejects unsafe XML, and incomplete items are
dropped rather than given guessed identities or publication times.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import ParseError as DefusedParseError
from defusedxml.ElementTree import fromstring

from engine.feeds.items import Headline
from engine.feeds.transport import FetchError

NEWS_URL = "https://www.forexlive.com/feed/news"

_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")


class FeedParseError(FetchError):
    """The feed body could not be understood, or refused to be parsed safely."""


def strip_html(raw: str) -> str:
    """Reduce an item's HTML body to text.

    The feed's descriptions are CDATA-wrapped HTML - `<ul>`, `<li>`, `<p>` - and the model
    is given text. Markup that reaches the prompt is tokens spent on nothing and one more
    surface for an instruction to hide in.
    """
    return _WHITESPACE.sub(" ", _TAG.sub(" ", raw)).strip()


def parse_feed(body: str, source_url: str) -> list[Headline]:
    """Read the feed into headlines.

    Unlike the calendar this raises on a bad body rather than returning rejections: an RSS
    document is one structure, so a document that does not parse has no partially-usable
    portion to salvage.
    """
    try:
        root = fromstring(body)
    except DefusedXmlException as error:
        # The hostile cases. `DefusedXmlException` is the library's own base for
        # EntitiesForbidden, DTDForbidden and ExternalReferenceForbidden, so catching it
        # covers every refusal it can make - including ones added later.
        raise FeedParseError(f"feed body was refused by the hardened parser: {error}") from error
    except DefusedParseError as error:
        raise FeedParseError(f"feed body is not parseable XML: {error}") from error

    headlines: list[Headline] = []
    for item in root.iter("item"):
        headline = _headline(item, source_url)
        if headline is not None:
            headlines.append(headline)
    return headlines


def _headline(item: object, source_url: str) -> Headline | None:
    find = getattr(item, "find", None)
    if find is None:  # pragma: no cover - defensive
        return None

    title = _text(find("title"))
    link = _text(find("link")) or _text(find("guid"))
    published = _published(_text(find("pubDate")))

    # An item without a title, a link or a time cannot be de-duplicated or dated, so it is
    # dropped rather than stored with a guessed identity.
    if not title or not link or published is None:
        return None

    return Headline(
        title=strip_html(title),
        body=strip_html(_text(find("description")) or ""),
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
    """`pubDate` is RFC 822 - "Mon, 10 Aug 2026 14:03:07 GMT" - not ISO 8601."""
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)
