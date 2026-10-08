"""Fetch feed text with a timeout and a response-size limit.

Tests inject a Fetcher instead of making network requests. A failed fetch raises
a named error so the pass records the failure and the previous board remains visible.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Final, Protocol

USER_AGENT: Final = "fundamental-engine/0.1 (personal trading research)"
DEFAULT_TIMEOUT: Final = 20.0

# A cap on what is read into memory. It does not defend against the entity-expansion
# attack - that payload is small on the wire - but it does bound an endpoint that starts
# streaming without end.
MAX_BODY_BYTES: Final = 8 * 1024 * 1024


class FetchError(RuntimeError):
    """A feed could not be retrieved or read.

    Typed so the brief can abort on it specifically rather than catching every exception
    and hoping it was a network problem.
    """


@dataclass(frozen=True)
class FetchedBody:
    """A retrieved body and the URL it actually came from.

    `resolved_url` is the point of this type. the headline feed has rebranded -
    `forexlive.com/feed/news` returns 301 to `investinglive.com/feed/news` - so what was
    requested and what answered are different hosts, and it is the second one that should
    be recorded against the items.
    """

    text: str
    resolved_url: str


class Fetcher(Protocol):
    """Retrieves a URL. The only way anything in this package reaches the network."""

    def __call__(self, url: str) -> FetchedBody: ...


def fetch(url: str, timeout: float = DEFAULT_TIMEOUT) -> FetchedBody:
    """Fetch over HTTPS with stdlib urllib.

    urllib follows redirects on its own and `geturl()` reports where it landed, which is
    all the redirect handling this needs - hence no HTTP dependency. An explicit
    `User-Agent` is not optional in practice: feeds routinely refuse the default one.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_BODY_BYTES + 1)
            resolved = response.geturl()
    except urllib.error.URLError as error:
        raise FetchError(f"could not fetch {url}: {error}") from error
    except OSError as error:  # timeouts and socket-level failures
        raise FetchError(f"could not fetch {url}: {error}") from error

    if len(raw) > MAX_BODY_BYTES:
        raise FetchError(f"{url} returned more than {MAX_BODY_BYTES} bytes")

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        # Deliberately not decoded with errors="replace". A body we cannot read is a body
        # we must not silently half-understand into a trading input.
        raise FetchError(f"{url} returned a body that is not UTF-8") from error

    return FetchedBody(text=text, resolved_url=resolved)
