"""A thin Anthropic client that replays from cache before it bills.

Every call goes through the cache. A miss costs money and is reported as such, so the
running total is visible while authoring rather than discovered on an invoice.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from anthropic import Anthropic

from engine.model.cache import DEFAULT_CACHE_DIR, ReplayCache, cache_key

# the methodology guide routes work by model. Named here so a change is one edit, not a search.
TRIAGE_MODEL = "claude-haiku-4-5-20251001"
AUTHORING_MODEL = "claude-opus-5-5"
SCAN_MODEL = "claude-sonnet-5-5"
DEFAULT_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


class MissingApiKeyError(RuntimeError):
    """Raised when no key is available. Never falls back to an unauthenticated call."""


@dataclass(frozen=True)
class ModelReply:
    """A response, and whether it cost anything."""

    text: str
    model: str
    replayed: bool
    input_tokens: int
    output_tokens: int
    stop_reason: str = ""
    cache_key: str = ""
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def was_billed(self) -> bool:
        return not self.replayed

    @property
    def prefix_was_cached(self) -> bool:
        """Whether the prompt cache actually served this call's prefix.

        The only honest answer to "is caching working?". A prefix below the model's
        minimum is never cached and never complains, so a marker in the request proves
        nothing on its own.
        """
        return self.cache_read_tokens > 0

    @property
    def truncated(self) -> bool:
        """Whether the model ran out of output budget before finishing."""
        return self.stop_reason == "max_tokens"

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


def load_api_key(
    env_path: Path = DEFAULT_ENV_PATH,
) -> str:
    """Read the key from the environment, falling back to `.env`.

    Never logged, never returned anywhere it could reach a journal or a commit
    or exposed in the interface.
    """
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key and env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == "ANTHROPIC_API_KEY":
                key = value.strip().strip("\"'")
                break
    if not key:
        raise MissingApiKeyError(
            "no ANTHROPIC_API_KEY in the environment or .env - live model calls need one"
        )
    return key


class ModelClient:
    """Calls the Anthropic API, replaying identical requests from disk."""

    def __init__(
        self,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        api_key: str | None = None,
    ) -> None:
        self.cache = ReplayCache(cache_dir)
        self._api_key = api_key
        self._client: Anthropic | None = None

    def _anthropic(self) -> Anthropic:
        # Built lazily so a fully-cached run never needs a key at all.
        if self._client is None:
            self._client = Anthropic(api_key=self._api_key or load_api_key())
        return self._client

    def stream(
        self,
        *,
        model: str,
        system: str,
        content: list[dict[str, Any]],
        max_tokens: int = 4096,
        effort: str | None = None,
    ) -> Any:
        """Yield text as it is written, then a final `ModelReply` carrying the usage.

        Separate from `complete` rather than a flag on it, because the two have genuinely
        different contracts: `complete` records to the replay cache and returns one value,
        while this yields and can only know what it cost at the end. Folding them together
        would put a `if stream:` branch through the middle of the caching logic that the
        rest of the engine depends on being simple.

        Nothing here touches the replay cache. A streamed answer is read by a person once;
        recording it would only make a later identical question replay a stale board.
        """
        system_param: Any = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ]
        extra: dict[str, Any] = {}
        if effort is not None:
            extra["output_config"] = {"effort": effort}

        text_parts: list[str] = []
        with self._anthropic().messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system_param,
            **extra,
            messages=[{"role": "user", "content": cast(Any, content)}],
        ) as stream:
            for chunk in stream.text_stream:
                text_parts.append(chunk)
                yield chunk
            final = stream.get_final_message()

        usage = final.usage
        yield ModelReply(
            text="".join(text_parts),
            model=model,
            replayed=False,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            stop_reason=final.stop_reason or "",
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        )

    def complete(
        self,
        *,
        model: str,
        system: str,
        content: list[dict[str, Any]],
        max_tokens: int = 4096,
        note: str | None = None,
        cache_salt: str | None = None,
        cache_system: bool = False,
        effort: str | None = None,
    ) -> ModelReply:
        """Return a completion, from cache when the exact input was seen before.

        `cache_salt` varies the cache key **without touching the request**. It exists for
        the stability test, which asks the same question N times to see whether the answer
        moves: without a salt the cache would replay one response N times and report
        perfect agreement, which is the one result that test must never fake.

        `cache_system` marks the system prompt for **Anthropic-side prompt caching**, which
        is a different thing from the replay cache above and worth keeping straight: the
        replay cache lives on disk and returns a past answer for free, while the prompt
        cache re-reads a stable prefix instead of re-paying for it on a call that genuinely
        happens. It deliberately does **not** enter the replay key - the response is the
        same either way, and including it would turn enabling caching into a cache miss on
        every recorded input.
        """
        request: dict[str, Any] = {
            "model": model,
            "system": system,
            "content": content,
            "max_tokens": max_tokens,
            "salt": cache_salt,
        }
        # Effort joins the key only when set, which keeps every response recorded before
        # the parameter existed replayable. Adding a field unconditionally would change
        # the shape of every key and silently re-bill the whole cache.
        #
        # It *must* be in the key when present: unlike `cache_system`, effort changes the
        # answer, so replaying a `high` response for a `low` request would quietly report
        # the expensive model's judgement as the cheap one's - which is exactly the
        # comparison this was wired to run.
        if effort is not None:
            request["effort"] = effort

        key = cache_key(request)

        cached = self.cache.get(key)
        if cached is not None:
            response = cached["response"]
            return ModelReply(
                text=response["text"],
                model=model,
                replayed=True,
                input_tokens=response.get("input_tokens", 0),
                output_tokens=response.get("output_tokens", 0),
                stop_reason=response.get("stop_reason", ""),
                cache_key=key,
                cache_read_tokens=response.get("cache_read_tokens", 0),
                cache_write_tokens=response.get("cache_write_tokens", 0),
            )

        # A cached prefix is sent as blocks so the marker has somewhere to live; an
        # uncached one stays a plain string, which is the same bytes to the model.
        system_param: Any = (
            [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
            if cache_system
            else system
        )

        # `output_config`, not a top-level parameter. Omitted entirely when unset so the
        # request is byte-identical to the pre-effort shape.
        extra: dict[str, Any] = {}
        if effort is not None:
            extra["output_config"] = {"effort": effort}

        message = self._anthropic().messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_param,
            **extra,
            # The SDK types content as a wide union of block kinds; ours are plain
            # text and image dicts, which it accepts at runtime.
            messages=[{"role": "user", "content": cast(Any, content)}],
        )
        text = "".join(block.text for block in message.content if block.type == "text")
        payload = {
            "text": text,
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens,
            # Recorded because a prefix below the model's minimum caches silently - no
            # error, no warning, just a bill. These two numbers are the only evidence that
            # the prompt cache did anything, and `input_tokens` alone is misleading: it
            # counts only the uncached remainder.
            "cache_read_tokens": getattr(message.usage, "cache_read_input_tokens", 0) or 0,
            "cache_write_tokens": getattr(message.usage, "cache_creation_input_tokens", 0) or 0,
            # Recorded because an empty answer must be distinguishable from a considered
            # one. A reasoning model can spend its whole output budget thinking and return
            # no text at all; a caller that cannot see that will treat silence as content.
            "stop_reason": message.stop_reason or "",
        }
        self.cache.put(key, payload, note=note, request=request)

        return ModelReply(
            text=text,
            model=model,
            replayed=False,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            stop_reason=message.stop_reason or "",
            cache_key=key,
            cache_read_tokens=int(payload["cache_read_tokens"]),
            cache_write_tokens=int(payload["cache_write_tokens"]),
        )
