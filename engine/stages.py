"""The model stages: one function per stage, each re-runnable on its own.

Every stage takes its recorded inputs and returns a parsed answer **plus the reply**, which
carries the verbatim prompt, the model, the effort and the token counts. That shape is what
makes a stage debuggable: a stored prompt can be replayed against another model without
re-ingesting a feed, and a bad paragraph can be re-run alone rather than by repeating the
whole pass.

Skills are plain markdown read off disk (`design.md` §9). No manifest, no registry, no
checksums, no activation records. The sibling project's registry exists so a reflection loop
can propose skill edits against a live account with an audit trail, and it has a known
failure mode where a line-ending change silently drops a skill while still reporting the leg
active. A reader gains nothing from that and inherits a silent-failure surface.

**Parsing is strict.** A stage that half-understands a reply produces a read that looks
normal and is wrong. Every parser here raises a named error rather than filling in a
default — `NEUTRAL` is never a fallback for "could not read the answer".
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from engine.model.client import ModelClient, ModelReply
from engine.reading.states import Axis, State

SKILLS_DIR: Final = Path(__file__).resolve().parent / "prompts"

# One dict. Routing is `design.md` §8's measured choice — triage on Haiku because it is
# high volume and drops most of the feed, everything else on Opus — and moving a stage to
# Sonnet is meant to be one edit here plus a params change, not a search.
STAGE_SKILLS: Final[dict[str, str]] = {
    "triage": "triage.md",
    "score": "scoring.md",
    "situations": "situations.md",
    "expectation": "expectation.md",
    "narrative": "narrative.md",
    "chat": "chat.md",
}

# Effort per stage. `low`-`max`, controlling thinking depth and overall token spend.
# Narrative is a writing task over a fact sheet Python already computed, not a reasoning
# one, so it does not need what the judgement stages do.
#
# Effort is the lever here rather than disabling thinking: with thinking off, Opus 5 can
# write a tool call or a `<thinking>` tag into its visible text, and a stage that returns
# prose where JSON was expected fails in a way that looks like a bad model rather than a
# bad setting.
STAGE_EFFORT: Final[dict[str, str | None]] = {
    "triage": None,  # Haiku 4.5 does not take an effort level
    "score": "medium",
    "situations": "high",
    "expectation": "high",
    "narrative": "medium",
    "chat": "medium",
}

# Output budget per stage. **Thinking tokens count against this on Opus 5**, which runs
# adaptive thinking by default — the first live pass died at `score` on a 2048 budget the
# reasoning had already spent before the JSON began. The answers themselves are small; the
# budget has to cover the thinking that produces them.
#
# 16000 rather than higher because these are non-streaming calls, and that is the largest
# budget that comfortably finishes inside the SDK's default HTTP timeout.
STAGE_MAX_TOKENS: Final[dict[str, int]] = {
    "triage": 2048,  # a list of integers, from a model that is not thinking
    "score": 16000,
    "situations": 16000,
    "expectation": 16000,
    "narrative": 16000,
    "chat": 16000,
}

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class StageError(RuntimeError):
    """A stage's reply could not be understood.

    Named so the pass can record *which* stage failed and stop the chain, rather than
    catching a generic exception and continuing on a half-parsed answer.
    """


@dataclass(frozen=True)
class StageResult:
    """A parsed answer and everything needed to explain or replay it."""

    answer: Any
    reply: ModelReply
    prompt: str
    model: str
    effort: str | None
    stage: str


@dataclass(frozen=True)
class ScoredTouch:
    """One development a headline carries, as the scoring stage returned it."""

    entities: tuple[tuple[str, int], ...]  # (entity, polarity)
    axis: Axis
    magnitude: float
    description: str


@dataclass(frozen=True)
class SituationUpdate:
    """One instruction from the situation stage.

    `identifier` is empty on an `open`. On `update` and `resolve` it must resolve to a
    situation that exists — an unresolvable one is rejected and counted against adherence,
    because a re-described situation silently double-counts a story.
    """

    action: str
    identifier: str = ""
    description: str = ""
    axis: Axis | None = None
    magnitude: float | None = None
    status: str = ""
    note: str = ""
    entities: tuple[tuple[str, int], ...] = ()


@dataclass
class StageCost:
    """Per-stage accounting, accumulated across a pass."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    billed_calls: int = 0
    replayed_calls: int = 0
    usd: float = 0.0

    def record(self, reply: ModelReply, prices: dict[str, dict[str, float]]) -> None:
        if reply.replayed:
            # A replay contributes no cost. Counted separately so a pass that looks cheap
            # because it was cached is distinguishable from one that was genuinely quiet.
            self.replayed_calls += 1
            return
        self.billed_calls += 1
        self.input_tokens += reply.input_tokens
        self.output_tokens += reply.output_tokens
        self.cache_read_tokens += reply.cache_read_tokens
        self.cache_write_tokens += reply.cache_write_tokens
        rate = prices.get(reply.model, {})
        self.usd += (
            reply.input_tokens * rate.get("input", 0.0)
            + reply.output_tokens * rate.get("output", 0.0)
            + reply.cache_read_tokens * rate.get("cache_read", rate.get("input", 0.0) * 0.1)
            + reply.cache_write_tokens * rate.get("cache_write", rate.get("input", 0.0) * 1.25)
        ) / 1_000_000


@dataclass
class Ledger:
    """Every stage's cost for one pass."""

    stages: dict[str, StageCost] = field(default_factory=dict)

    def record(self, stage: str, reply: ModelReply, prices: dict[str, dict[str, float]]) -> None:
        self.stages.setdefault(stage, StageCost()).record(reply, prices)

    @property
    def total_usd(self) -> float:
        return sum(cost.usd for cost in self.stages.values())

    def as_dict(self) -> dict[str, dict[str, float | int]]:
        return {
            name: {
                "input_tokens": cost.input_tokens,
                "output_tokens": cost.output_tokens,
                "cache_read_tokens": cost.cache_read_tokens,
                "cache_write_tokens": cost.cache_write_tokens,
                "billed_calls": cost.billed_calls,
                "replayed_calls": cost.replayed_calls,
                "usd": round(cost.usd, 6),
            }
            for name, cost in sorted(self.stages.items())
        }


def load_skill(stage: str, directory: Path = SKILLS_DIR) -> str:
    """Read a stage's skill file. Missing is an error, never an empty prompt."""
    try:
        name = STAGE_SKILLS[stage]
    except KeyError:
        raise StageError(f"no skill bound to stage {stage!r}") from None
    path = directory / name
    if not path.is_file():
        raise StageError(f"skill file {path} is missing")
    return path.read_text(encoding="utf-8")


def parse_json(text: str, stage: str) -> dict[str, Any]:
    """Read a stage's reply as JSON, tolerating a fenced block but nothing else.

    Deliberately not forgiving beyond the fence: a reply that needs repairing is a reply
    that was not understood, and guessing at it is how a wrong read gets a confident face.
    """
    stripped = text.strip()
    if not stripped:
        raise StageError(f"{stage}: the model returned no text")

    fenced = _JSON_BLOCK.search(stripped)
    if fenced:
        stripped = fenced.group(1).strip()

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as error:
        raise StageError(f"{stage}: reply is not JSON: {error}") from error
    if not isinstance(parsed, dict):
        raise StageError(f"{stage}: reply is {type(parsed).__name__}, expected an object")
    return parsed


class Stages:
    """The five model stages, bound to one client and one routing table."""

    def __init__(
        self,
        client: ModelClient,
        models: dict[str, str],
        prices: dict[str, dict[str, float]] | None = None,
        skills_dir: Path = SKILLS_DIR,
    ) -> None:
        self.client = client
        self.models = models
        self.prices = prices or {}
        self.skills_dir = skills_dir
        self.ledger = Ledger()

    def forget(self, key: str) -> None:
        """Drop one recorded reply, so the next identical request is asked again."""
        if not key:
            return
        # A cache we cannot clean is not fatal — the retry simply replays the bad answer
        # and fails the same way, which is visible rather than silent.
        with contextlib.suppress(OSError):
            self.client.cache.path_for(key).unlink(missing_ok=True)

    # --- plumbing ---------------------------------------------------------------------

    def _call(
        self, stage: str, prompt: str, *, force: bool = False, varies: str | None = None
    ) -> StageResult:
        """Run one stage. `force` bypasses the replay cache for a forced refresh.

        The prompt is returned alongside the reply because two tables store it verbatim,
        and rebuilding it afterwards from its inputs does not reproduce what was sent.

        `varies` splits the request into a cacheable prefix and a tail that changes. The
        desk is the case that needs it: the board sheet is ~9,000 tokens and identical for
        every question asked against one pass, so re-sending it uncached made the sheet 60%
        of what a question cost. Marked as a prefix it is re-read at a tenth of the price
        and only the question is new.
        """
        skill = load_skill(stage, self.skills_dir)
        model = self.models.get(stage.upper())
        if not model:
            raise StageError(f"no model routed for stage {stage!r}")
        effort = STAGE_EFFORT.get(stage)

        if varies is None:
            content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        else:
            content = [
                {"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": varies},
            ]

        reply = self.client.complete(
            model=model,
            system=skill,
            content=content,
            max_tokens=STAGE_MAX_TOKENS[stage],
            note=stage,
            # A forced refresh re-decides everything, so it must not replay. The salt
            # varies the key without touching the request the model sees.
            cache_salt=_force_salt() if force else None,
            cache_system=True,
            effort=effort,
        )
        self.ledger.record(stage, reply, self.prices)

        if reply.truncated:
            # Raised rather than accepted: a reply cut off mid-JSON either fails to parse or,
            # worse, parses into a shorter answer than the model meant to give — a headline
            # list missing its tail looks exactly like a quiet feed.
            raise StageError(
                f"{stage}: the model hit its {STAGE_MAX_TOKENS[stage]}-token output budget "
                f"before finishing. The output budget may cover thinking as well as the "
                f"answer; raise STAGE_MAX_TOKENS[{stage!r}] or lower its effort."
            )
        return StageResult(
            answer=None, reply=reply, prompt=prompt, model=model, effort=effort, stage=stage
        )

    # --- the stages -------------------------------------------------------------------

    def triage(
        self,
        headlines: list[dict[str, Any]],
        entities: tuple[str, ...],
        *,
        force: bool = False,
    ) -> StageResult:
        """Drop headlines bearing on no tracked entity.

        The cheap stage, and the one that runs on everything. Returns the indices kept, so
        the caller keeps ownership of the records themselves.
        """
        listed = "\n".join(
            f"{index}. {row['title']}\n   {str(row.get('body', ''))[:400]}"
            for index, row in enumerate(headlines)
        )
        prompt = (
            f"Tracked entities: {', '.join(entities)}\n\n"
            f"Headlines:\n\n{listed}\n\n"
            f"Return the numbers of the headlines that bear on any tracked entity."
        )
        result = self._call("triage", prompt, force=force)
        return _parsed(self, result, lambda text: parse_triage(text, len(headlines)))

    def score(
        self, headline: dict[str, Any], entities: tuple[str, ...], *, force: bool = False
    ) -> StageResult:
        """Per headline: touched entities, axis, per-entity polarity, magnitude, description."""
        prompt = (
            f"Tracked entities: {', '.join(entities)}\n\n"
            f"Headline: {headline['title']}\n\n"
            f"{headline.get('body', '')}\n\n"
            f"Published: {headline['published_at']}"
        )
        result = self._call("score", prompt, force=force)
        return _parsed(self, result, lambda text: parse_score(text, entities))

    def situations(
        self,
        current: list[dict[str, Any]],
        developments: list[ScoredTouch],
        *,
        force: bool = False,
    ) -> StageResult:
        """Update the running set, by identifier.

        The current set is presented with its `S`-numbers precisely so the model can name
        them. Adherence is measured by the caller against what comes back.
        """
        if current:
            listed = "\n".join(
                f"{row['identifier']} · {row['axis']} · magnitude {row['magnitude']} ·"
                f" {row['status']} · last evidence {row['last_evidence_at']}\n"
                f"    {row['description']}"
                for row in current
            )
        else:
            listed = "(none — the set is empty)"

        new = "\n".join(
            f"- {touch.axis} · magnitude {touch.magnitude} · {_signed(touch.entities)}\n"
            f"  {touch.description}"
            for touch in developments
        )
        prompt = (
            f"Current situations:\n\n{listed}\n\n"
            f"New developments from this pass:\n\n{new or '(none)'}\n\n"
            f"Return the updates. Name existing situations by their identifier."
        )
        result = self._call("situations", prompt, force=force)
        return _parsed(self, result, parse_situations)

    def expectation(self, fact_sheet: str, *, force: bool = False) -> StageResult:
        """The forward calendar read for one entity."""
        result = self._call("expectation", fact_sheet, force=force)
        return _parsed(self, result, parse_expectation)

    def narrative(self, fact_sheet: str, *, force: bool = False) -> StageResult:
        """The written breakdown for one entity, from the fact sheet and nothing else."""
        result = self._call("narrative", fact_sheet, force=force)
        return _parsed(self, result, parse_narrative)

    def chat(self, brief_sheet: str, question: str) -> StageResult:
        """One answer to one operator question, as plain prose.

        Deliberately not parsed. Every other stage returns JSON because Python has to do
        arithmetic on the answer; this one is read by a human and nothing downstream
        computes on it, so demanding JSON would add the one failure mode — an unescaped
        quote costing the whole reply — for no benefit at all.

        Never replayed from disk: the same question asked an hour later must see the newer
        read, and a replayed answer would silently describe a board that has moved. The
        sheet is still sent as a cacheable prefix, which is the opposite concern — that is
        Anthropic re-reading a stable prefix on a call that genuinely happens.
        """
        result = self._call(
            "chat", brief_sheet, force=True, varies=f"THE OPERATOR ASKS:\n{question.strip()}"
        )
        return _with(result, result.reply.text.strip())

    def chat_stream(self, brief_sheet: str, question: str) -> Any:
        """The same answer, yielded as it is written, then a final `ModelReply`.

        Worth the second code path because the wait is the whole complaint: an answer takes
        fifteen to thirty seconds, and a blocking POST spends all of it showing nothing.
        Streamed, the first words land in about two seconds and the operator can start
        reading while the rest arrives.
        """
        model = self.models.get("CHAT")
        if not model:
            raise StageError("no model routed for stage 'chat'")

        for piece in self.client.stream(
            model=model,
            system=load_skill("chat", self.skills_dir),
            content=[
                {"type": "text", "text": brief_sheet, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": f"THE OPERATOR ASKS:\n{question.strip()}"},
            ],
            max_tokens=STAGE_MAX_TOKENS["chat"],
            effort=STAGE_EFFORT.get("chat"),
        ):
            if isinstance(piece, str):
                yield piece
            else:
                self.ledger.record("chat", piece, self.prices)
                yield piece


def _signed(entities: tuple[tuple[str, int], ...]) -> str:
    """`EUR +, USD -` — the per-entity polarity, readable at a glance in a prompt."""
    return ", ".join(f"{entity} {'+' if polarity > 0 else '-'}" for entity, polarity in entities)


def _parsed(stages: Stages, result: StageResult, parse: Any) -> StageResult:
    """Parse a reply, and forget it from the replay cache if it will not parse.

    The cache stores a reply the moment it arrives, before anything reads it — which is
    right for a good answer and poison for a bad one. A model that returns malformed JSON
    once would otherwise have that exact malformed JSON replayed for every future run over
    the same input, so the stage could never recover no matter how many times it was retried.
    Found live: the expectation stage returned a broken object and the pass became
    permanently unrunnable.

    Evicting on a parse failure makes a retry a genuinely fresh call.
    """
    try:
        return _with(result, parse(result.reply.text))
    except StageError:
        stages.forget(result.reply.cache_key)
        raise


def _with(result: StageResult, answer: Any) -> StageResult:
    return StageResult(
        answer=answer,
        reply=result.reply,
        prompt=result.prompt,
        model=result.model,
        effort=result.effort,
        stage=result.stage,
    )


def _force_salt() -> str:
    """A salt that varies per forced refresh, so nothing replays.

    Time-based rather than random so two stages inside one forced pass do not collide in
    the cache in a way that would be confusing to inspect afterwards.
    """
    from datetime import UTC, datetime

    return f"forced:{datetime.now(UTC).isoformat()}"


# --- Parsers, kept module-level so they can be tested against recorded replies ---------


def parse_triage(text: str, count: int) -> tuple[int, ...]:
    """The kept indices, bounded by what was actually sent.

    An out-of-range index is dropped rather than raising: it is the cheap stage naming a
    headline that does not exist, which loses nothing, where raising would abort a pass
    over a filter's slip.
    """
    payload = parse_json(text, "triage")
    keep = payload.get("keep")
    if not isinstance(keep, list):
        raise StageError("triage: `keep` is missing or is not a list")

    kept: list[int] = []
    for value in keep:
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        if 0 <= value < count:
            kept.append(value)
    return tuple(sorted(set(kept)))


def parse_score(text: str, entities: tuple[str, ...]) -> tuple[ScoredTouch, ...]:
    """The developments one headline carries.

    An entity outside the configured universe is dropped: the config is the universe, and
    a read against something nobody trades has nowhere to go.
    """
    payload = parse_json(text, "score")
    touches = payload.get("touches")
    if not isinstance(touches, list):
        raise StageError("score: `touches` is missing or is not a list")

    known = set(entities)
    parsed: list[ScoredTouch] = []
    for entry in touches:
        if not isinstance(entry, dict):
            raise StageError("score: a touch entry is not an object")

        axis = _axis(entry.get("axis"), "score")
        magnitude = _magnitude(entry.get("magnitude"), "score")
        description = str(entry.get("description", "")).strip()
        if not description:
            raise StageError("score: a touch entry carries no description")

        pairs: list[tuple[str, int]] = []
        raw_entities = entry.get("entities")
        if not isinstance(raw_entities, list) or not raw_entities:
            raise StageError("score: a touch entry names no entities")
        for item in raw_entities:
            if not isinstance(item, dict):
                raise StageError("score: an entity entry is not an object")
            name = str(item.get("entity", "")).strip().upper()
            polarity = _polarity(item.get("polarity"), "score")
            if name in known:
                pairs.append((name, polarity))

        if pairs:
            parsed.append(
                ScoredTouch(
                    entities=tuple(pairs), axis=axis, magnitude=magnitude, description=description
                )
            )
    return tuple(parsed)


def parse_situations(text: str) -> tuple[SituationUpdate, ...]:
    """The situation stage's instructions, structurally validated but not yet resolved.

    Whether an identifier exists is the caller's question — it owns the current set, and it
    is the one that records adherence.
    """
    payload = parse_json(text, "situations")
    updates = payload.get("updates")
    if not isinstance(updates, list):
        raise StageError("situations: `updates` is missing or is not a list")

    parsed: list[SituationUpdate] = []
    for entry in updates:
        if not isinstance(entry, dict):
            raise StageError("situations: an update entry is not an object")

        action = str(entry.get("action", "")).strip().lower()
        if action not in ("open", "update", "resolve"):
            raise StageError(f"situations: unknown action {action!r}")

        identifier = str(entry.get("identifier", "") or "").strip().upper()
        if action in ("update", "resolve") and not identifier:
            raise StageError(f"situations: a {action} names no identifier")

        entities: tuple[tuple[str, int], ...] = ()
        raw_entities = entry.get("entities")
        if isinstance(raw_entities, list):
            entities = tuple(
                (
                    str(item.get("entity", "")).strip().upper(),
                    _polarity(item.get("polarity"), "situations"),
                )
                for item in raw_entities
                if isinstance(item, dict) and str(item.get("entity", "")).strip()
            )

        if action == "open" and not entities:
            raise StageError("situations: an open names no entities")

        magnitude = entry.get("magnitude")
        parsed.append(
            SituationUpdate(
                action=action,
                identifier=identifier,
                description=str(entry.get("description", "")).strip(),
                axis=_axis(entry.get("axis"), "situations") if entry.get("axis") else None,
                magnitude=_magnitude(magnitude, "situations") if magnitude is not None else None,
                status=str(entry.get("status", "")).strip().upper(),
                note=str(entry.get("note", "")).strip(),
                entities=entities,
            )
        )
    return tuple(parsed)


def parse_expectation(text: str) -> dict[str, Any]:
    """One entity's forward read.

    `NO_READ` is a value the stage may return; it is never substituted for a missing one.
    A reply without a usable state raises rather than defaulting to `NEUTRAL`, which would
    report an unanswered question as a balanced week.
    """
    payload = parse_json(text, "expectation")

    policy = _state(payload.get("policy"), "expectation")
    directional = _state(payload.get("directional"), "expectation")
    confidence = str(payload.get("confidence", "")).strip().upper()
    if confidence not in ("HIGH", "MEDIUM", "LOW"):
        raise StageError(f"expectation: confidence {confidence!r} is not HIGH, MEDIUM or LOW")

    reason = str(payload.get("reason", "")).strip()
    if not reason:
        raise StageError("expectation: no reason given")

    raw_events = payload.get("event_ids")
    event_ids = tuple(str(e) for e in raw_events) if isinstance(raw_events, list) else ()
    decisive = payload.get("decisive_event_id")

    return {
        "policy": policy,
        "directional": directional,
        "confidence": confidence,
        "reason": reason,
        "event_ids": event_ids,
        "decisive_event_id": str(decisive) if decisive else None,
    }


def parse_narrative(text: str) -> dict[str, Any]:
    """The paragraph and the identifiers it claims to have cited."""
    payload = parse_json(text, "narrative")

    prose = str(payload.get("prose", "")).strip()
    if not prose:
        raise StageError("narrative: no prose returned")

    raw_cited = payload.get("cited")
    cited = (
        tuple(str(c).strip().upper() for c in raw_cited if str(c).strip())
        if isinstance(raw_cited, list)
        else ()
    )
    return {"prose": prose, "cited": cited}


def _axis(value: Any, stage: str) -> Axis:
    try:
        return Axis(str(value).strip().upper())
    except ValueError:
        raise StageError(f"{stage}: axis {value!r} is not POLICY or DIRECTIONAL") from None


def _state(value: Any, stage: str) -> State:
    try:
        return State(str(value).strip().upper())
    except ValueError:
        raise StageError(f"{stage}: state {value!r} is not one of the four") from None


def _polarity(value: Any, stage: str) -> int:
    text = str(value).strip().upper()
    if text in ("POSITIVE", "+1", "1"):
        return 1
    if text in ("NEGATIVE", "-1"):
        return -1
    raise StageError(f"{stage}: polarity {value!r} is not POSITIVE or NEGATIVE")


def _magnitude(value: Any, stage: str) -> float:
    try:
        magnitude = float(value)
    except (TypeError, ValueError):
        raise StageError(f"{stage}: magnitude {value!r} is not a number") from None
    if not 1 <= magnitude <= 10:
        raise StageError(f"{stage}: magnitude {magnitude} is outside 1–10")
    return magnitude
