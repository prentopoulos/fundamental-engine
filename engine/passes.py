"""The pass: a fixed chain, run start to finish, with everything it saw recorded.

    ingest → triage → score → situations → arithmetic → expectation → narrative → store

No agentic loop and no runtime planning. The order above is the order, and a stage that
fails stops the chain rather than degrading past it — a pass that half-ran and stored half
a board is worse than one that failed cleanly, because the previous read stays readable at
its true age and a stale read is visible while a mixed one is not.

Named `passes.py` rather than `pass.py`: `pass` is a keyword, so `python -m engine.pass`
would be unimportable. The CLI entry point in `engine/__main__.py` provides the command the
plan asks for.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from sqlite3 import Connection
from typing import Any

from engine.expectation.forward import (
    Event,
    Expectation,
    build_expectation,
    in_window,
    nothing_scheduled,
)
from engine.feeds.ingest import Ingestion, ingest, unscored, untriaged
from engine.feeds.transport import Fetcher, fetch
from engine.narrative.audit import invented_citations
from engine.narrative.check import check
from engine.narrative.factsheet import (
    cited_identifiers,
    events_block,
    expectation_sheet,
    narrative_sheet,
)
from engine.reading.arithmetic import Read, apply_triggers, contributions, read
from engine.reading.states import Axis, State
from engine.situations.lifecycle import age, apply_updates, contributing, current_set
from engine.stages import ScoredTouch, StageError, Stages
from engine.store.schema import transaction
from engine.triggers import ChangeSet, carry_forward, detect_changes
from engine.universe.betas import LegRead, resolve
from engine.universe.config import Params, Universe

TRIGGERS = ("hourly", "release", "forced", "manual")


class PassFailed(RuntimeError):
    """A stage failed and the chain stopped. Carries which stage, for the pass record."""

    def __init__(self, stage: str, cause: BaseException) -> None:
        super().__init__(f"{stage}: {cause}")
        self.stage = stage
        self.cause = cause


@dataclass
class PassResult:
    """Everything one pass produced, for the caller and for the interface."""

    pass_id: int
    trigger: str
    started_at: datetime
    trigger_events: tuple[str, ...] = ()
    finished_at: datetime | None = None
    status: str = "running"
    failed_stage: str | None = None
    ingestion: Ingestion | None = None
    reads: dict[str, dict[Axis, Read]] = field(default_factory=dict)
    expectations: dict[str, Expectation] = field(default_factory=dict)
    narratives: dict[str, str] = field(default_factory=dict)
    # The full narrative rows — prose, citations, the verbatim prompt and any constraint
    # violations — held between authoring and the single write at the end of the chain.
    narrative_rows: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    adherence: float | None = None
    cost_usd: float = 0.0
    stage_costs: dict[str, Any] = field(default_factory=dict)


def start_pass(
    connection: Connection,
    trigger: str,
    now: datetime,
    trigger_events: tuple[str, ...] = (),
) -> int:
    """Open a pass record before any work happens, so a crash still leaves a trace.

    `trigger_events` records every event that asked for this refresh, so a cluster that
    debounced four RBNZ prints into one pass can still be traced back to all four.
    """
    if trigger not in TRIGGERS:
        raise ValueError(f"unknown trigger {trigger!r}")
    with transaction(connection):
        cursor = connection.execute(
            "INSERT INTO passes (trigger, started_at, status, trigger_events)"
            " VALUES (?, ?, 'running', ?)",
            (trigger, now.isoformat(), json.dumps(list(trigger_events))),
        )
    return int(cursor.lastrowid)


def run(
    connection: Connection,
    universe: Universe,
    params: Params,
    stages: Stages,
    *,
    trigger: str = "manual",
    now: datetime | None = None,
    fetcher: Fetcher = fetch,
    poll_only: bool = False,
    forced: bool = False,
    trigger_events: tuple[str, ...] = (),
) -> PassResult:
    """Run one pass end to end.

    `poll_only` is the cheap variant: ingest, triage and score, then stop. It exists so a
    headline check between full reads costs three triage calls rather than a hundred, and
    so the calendar's abort-on-bad-body rule cannot kill a poll that only wanted headlines.

    `forced` re-decides everything for every entity and bypasses the replay cache. It is
    the one path that always costs full price, and it exists for the case where the operator
    does not trust the current read and wants it rebuilt rather than patched.
    """
    moment = now or datetime.now(UTC)
    pass_id = start_pass(connection, trigger, moment, trigger_events)
    result = PassResult(
        pass_id=pass_id, trigger=trigger, trigger_events=trigger_events, started_at=moment
    )

    try:
        result.ingestion = _ingest(connection, params, moment, fetcher, poll_only)
        result.warnings.extend(result.ingestion.warnings)

        touches, scored_ids = _triage_and_score(connection, universe, stages, forced)
        result.adherence = _update_situations(
            connection, params, stages, touches, moment, result, forced, scored_ids
        )

        if poll_only:
            return _finish(connection, result, stages, moment)

        _compute_reads(connection, universe, params, moment, result)

        # A forced refresh re-decides everything, so change detection is not consulted at
        # all — not merely overridden with an empty skip list. The operator pressed refresh
        # because they do not trust the current read; skipping any part of it would defeat
        # the one path that exists to rebuild rather than patch.
        previous = _previous_complete_pass(connection, pass_id)
        changes = (
            _all_changed(universe.entities)
            if forced
            else detect_changes(
                connection,
                universe.entities,
                result.reads,
                previous_pass_id=previous,
            )
        )
        if not forced:
            _include_calendar_changes(connection, params, moment, changes)
        result.skipped = sorted(changes.unchanged)

        _forward_and_write(connection, universe, params, stages, moment, result, forced, changes)
        _store(connection, result, moment)

        if previous is not None and changes.unchanged:
            with transaction(connection):
                carry_forward(connection, pass_id, previous, changes.unchanged)

        bad = invented_citations(connection, pass_id)
        result.warnings.extend(str(citation) for citation in bad)

        return _finish(connection, result, stages, moment)

    except PassFailed as failure:
        _fail(connection, result, stages, failure.stage, failure.cause, moment)
        raise
    except Exception as error:  # a failure outside a named stage still records a stage
        _fail(connection, result, stages, "pass", error, moment)
        raise


# --- The chain, one step per function ---------------------------------------------------


def _ingest(
    connection: Connection, params: Params, moment: datetime, fetcher: Fetcher, poll_only: bool
) -> Ingestion:
    try:
        return ingest(
            connection,
            horizon=params.horizon,
            now=moment,
            fetcher=fetcher,
            news_urls=params.headline_urls,
            sitemap_urls=params.sitemap_urls,
            with_calendar=not poll_only,
        )
    except Exception as error:
        raise PassFailed("ingest", error) from error


def _triage_and_score(
    connection: Connection,
    universe: Universe,
    stages: Stages,
    forced: bool,
) -> tuple[list[tuple[ScoredTouch, str]], tuple[str, ...]]:
    """Triage what has not been judged, then score what triage kept and left unscored.

    **Both steps work from what is pending, not from what this pass ingested.** A headline
    moves through three durable states — ingested, triaged, scored — each committed before
    the next is attempted, so a pass that dies anywhere resumes from exactly that point.
    Working from this pass's own arrivals instead would strand every headline an earlier
    failure had already stored: they are in the table, so no poll re-inserts them, and
    nothing would ever score them. The read would be permanently thinner than the feed it
    came from, and it would look exactly like a quiet week.

    Both stages are still skipped when nothing is pending, which is what keeps a quiet
    hour cheap.
    """
    # Every stage catch below is broad on purpose. A model call fails as a parse error
    # (StageError), but also as a rate limit, a timeout or a transport error, and the
    # operator needs the pass record to name the stage in all of those cases equally.
    pending = untriaged(connection)
    if pending:
        rows = [
            {
                "id": r["id"],
                "title": r["title"],
                "body": r["body"],
                "published_at": r["published_at"],
            }
            for r in pending
        ]
        try:
            kept = stages.triage(rows, universe.entities, force=forced).answer
        except Exception as error:
            raise PassFailed("triage", error) from error

        with transaction(connection):
            for index, row in enumerate(rows):
                connection.execute(
                    "UPDATE headlines SET triaged = ? WHERE id = ?",
                    (1 if index in kept else -1, row["id"]),
                )

    touches: list[tuple[ScoredTouch, str]] = []
    scored_ids: list[str] = []
    for row in unscored(connection):
        headline = {
            "id": row["id"],
            "title": row["title"],
            "body": row["body"],
            "published_at": row["published_at"],
        }
        try:
            scored = stages.score(headline, universe.entities, force=forced).answer
        except Exception as error:
            raise PassFailed("score", error) from error

        # Keep the headline pending until its situation updates are committed.
        # If the next stage fails, a retry can recover the evidence from the cache.
        scored_ids.append(str(row["id"]))
        touches.extend((touch, str(row["id"])) for touch in scored)

    return touches, tuple(scored_ids)


def _update_situations(
    connection: Connection,
    params: Params,
    stages: Stages,
    touches: list[tuple[ScoredTouch, str]],
    moment: datetime,
    result: PassResult,
    forced: bool,
    scored_ids: tuple[str, ...],
) -> float | None:
    """Age the set, then apply the stage's updates by identifier.

    Ageing runs first so the stage is shown situations already marked `FADING`, which is
    context it should have when deciding whether a quiet story has actually ended.
    """
    age(connection, now=moment, fade_after=params.fade_after)

    if not touches:
        apply_updates(
            connection,
            (),
            now=moment,
            warning_count=params.situation_warning_count,
            scored_headline_ids=scored_ids,
        )
        return None

    current = current_set(connection)
    try:
        updates = stages.situations(current, [touch for touch, _ in touches], force=forced).answer
    except Exception as error:
        raise PassFailed("situations", error) from error

    applied = apply_updates(
        connection,
        updates,
        now=moment,
        warning_count=params.situation_warning_count,
        touches=tuple(touches),
        scored_headline_ids=scored_ids,
    )
    result.warnings.extend(applied.warnings)
    return applied.adherence


def _compute_reads(
    connection: Connection,
    universe: Universe,
    params: Params,
    moment: datetime,
    result: PassResult,
) -> None:
    """The arithmetic. No model call anywhere in here."""
    for entity in universe.entities:
        per_axis: dict[Axis, Read] = {}
        for axis in Axis:
            rows = contributing(
                connection, entity, axis, now=moment, archive_after=params.archive_after
            )
            per_axis[axis] = read(
                entity,
                axis,
                contributions(rows, now=moment, half_life=params.half_life(axis)),
                params,
            )
        result.reads[entity] = per_axis


def _forward_and_write(
    connection: Connection,
    universe: Universe,
    params: Params,
    stages: Stages,
    moment: datetime,
    result: PassResult,
    forced: bool,
    changes: ChangeSet,
) -> None:
    """Expectation then narrative, per entity, with the triggers computed in between.

    The order matters: divergence is one of the four triggers, so the expectation has to
    exist before the narrative's fact sheet is built, or the paragraph would be written
    without the tension it most needs to address.
    """
    events = _in_window_events(connection, params, moment)
    attempted = 0

    for entity in universe.entities:
        policy = result.reads[entity][Axis.POLICY]
        directional = result.reads[entity][Axis.DIRECTIONAL]
        entity_events = [event for event in events if event.currency == entity]

        if not changes.should_run(entity):
            # Nothing moved. The stored expectation and narrative are carried forward with
            # their real authorship time, so the page shows how old the paragraph actually
            # is rather than how old this pass is. The triggers are still recomputed —
            # they are free, and a stale trigger set would misdescribe a live read.
            stored = _stored_expectation(connection, entity)
            if stored is not None:
                apply_triggers(
                    policy,
                    directional,
                    params,
                    expected_policy=stored.policy,
                    expected_directional=stored.directional,
                    resolves_at=stored.resolves_at,
                )
                result.expectations[entity] = stored
            continue

        expectation = _expectation_for(
            entity, policy, directional, entity_events, stages, moment, forced
        )
        result.expectations[entity] = expectation

        apply_triggers(
            policy,
            directional,
            params,
            expected_policy=expectation.policy,
            expected_directional=expectation.directional,
            resolves_at=expectation.resolves_at,
        )

        attempted += 1
        prose = _narrative_for(
            connection,
            entity,
            policy,
            directional,
            entity_events,
            expectation,
            stages,
            forced,
            result,
        )
        if prose is not None:
            result.narratives[entity] = prose

    # One unusable reply is a warning; every reply unusable is a broken stage, and a pass
    # that recorded no prose at all must not be allowed to look like it succeeded.
    if attempted and not result.narratives:
        raise PassFailed(
            "narrative",
            StageError(f"every narrative failed across {attempted} entities"),
        )


def _expectation_for(
    entity: str,
    policy: Read,
    directional: Read,
    events: list[Event],
    stages: Stages,
    moment: datetime,
    forced: bool,
) -> Expectation:
    """One entity's forward read, or `NO READ — nothing scheduled` with no call at all.

    Skipping the call when the window is empty is not only a saving: there is no question to
    ask. A model given no events would answer something, and that something would be a guess
    presented beside a real read.
    """
    if not events:
        return nothing_scheduled(entity)

    sheet = expectation_sheet(entity, policy, directional, events, moment)
    try:
        produced = stages.expectation(sheet, force=forced)
    except Exception as error:
        raise PassFailed("expectation", error) from error

    return build_expectation(
        entity,
        produced.answer,
        events,
        prompt=produced.prompt,
        model=produced.model,
        effort=produced.effort,
        now=moment,
    )


def _narrative_for(
    connection: Connection,
    entity: str,
    policy: Read,
    directional: Read,
    events: list[Event],
    expectation: Expectation,
    stages: Stages,
    forced: bool,
    result: PassResult,
) -> str | None:
    """The paragraph, checked against the constraints and stored either way.

    A paragraph that violates a constraint is stored with the violation recorded rather than
    discarded: the operator needs to see what the stage actually wrote, and a silently
    dropped narrative would leave the page that is meant to open with prose opening with a
    score table instead.

    A reply that will not parse is recorded and skipped, returning `None`. One entity's
    unusable answer must not discard the pass: every other entity's stages are already paid
    for, the situations and the arithmetic are already updated, and this entity simply keeps
    the paragraph it already had — which is still a true account of a read that has not
    changed enough to be worth rewriting. The warning is what makes the gap visible, and
    `_per_entity` still fails the pass if *every* entity failed, because that is a broken
    stage rather than one bad reply.
    """
    sheet = narrative_sheet(entity, policy, directional, events, expectation)
    try:
        produced = stages.narrative(sheet, force=forced)
    except StageError as error:
        result.warnings.append(f"narrative:{entity}:unusable reply: {error}")
        return None
    except Exception as error:
        raise PassFailed("narrative", error) from error

    answer = produced.answer
    prose = answer["prose"]
    cited = answer["cited"] or cited_identifiers(prose)

    known = {row["identifier"] for row in connection.execute("SELECT identifier FROM situations")}
    verdict = check(prose, tuple(cited), policy, directional, known)

    result.narrative_rows[entity] = {
        "prose": prose,
        "cited": list(cited),
        "prompt": produced.prompt,
        "model": produced.model,
        "effort": produced.effort,
        "violations": verdict.violations,
    }
    return prose


def _all_changed(entities: tuple[str, ...]) -> ChangeSet:
    """Every entity marked changed, for the forced path."""
    return ChangeSet(changed=set(entities), reasons={e: "forced refresh" for e in entities})


def _previous_complete_pass(connection: Connection, pass_id: int) -> int | None:
    row = connection.execute(
        "SELECT id FROM passes WHERE status = 'complete' AND id < ? ORDER BY id DESC LIMIT 1",
        (pass_id,),
    ).fetchone()
    return int(row["id"]) if row else None


def _include_calendar_changes(
    connection: Connection, params: Params, moment: datetime, changes: ChangeSet
) -> None:
    """Refresh expectations when calendar inputs change despite unchanged current scores.

    Comparing the calendar block in the saved prompt catches revisions, new events,
    and events leaving the forward window without rerunning every entity.
    """
    events = _in_window_events(connection, params, moment)
    for entity in tuple(changes.unchanged):
        stored = _stored_expectation(connection, entity)
        scheduled = [event for event in events if event.currency == entity]
        if stored is not None and not scheduled and stored.resolves_at is None:
            continue
        if stored is not None and events_block(scheduled) in stored.prompt:
            continue
        changes.unchanged.remove(entity)
        changes.changed.add(entity)
        changes.reasons[entity] = "calendar inputs changed"


def _stored_expectation(connection: Connection, entity: str) -> Expectation | None:
    """The most recent stored expectation for one entity, whatever pass produced it."""
    row = connection.execute(
        "SELECT * FROM expectations WHERE entity = ? ORDER BY pass_id DESC LIMIT 1",
        (entity,),
    ).fetchone()
    if row is None:
        return None
    record = dict(row)
    return Expectation(
        entity=entity,
        policy=State(record["policy_state"]),
        directional=State(record["directional_state"]),
        confidence=record["confidence"],
        reason=record["reason"],
        resolves_at=(
            datetime.fromisoformat(record["resolves_at"]) if record["resolves_at"] else None
        ),
        decisive_event_id=record["decisive_event_id"],
        event_ids=tuple(json.loads(record["event_ids"])),
        prompt=record["prompt"],
        model=record["model"],
        effort=record["effort"],
        authored_at=datetime.fromisoformat(record["authored_at"]),
    )


def _in_window_events(connection: Connection, params: Params, moment: datetime) -> list[Event]:
    rows = [dict(row) for row in connection.execute("SELECT * FROM calendar_events")]
    return in_window(rows, now=moment, horizon=params.horizon, impacts=params.expectation_impacts)


def _store(connection: Connection, result: PassResult, moment: datetime) -> None:
    """One transaction for the whole board, so a partial write is impossible."""
    with transaction(connection):
        for entity, per_axis in result.reads.items():
            for axis, value in per_axis.items():
                connection.execute(
                    "INSERT INTO reads (pass_id, entity, axis, score, state, degree,"
                    " no_read_reason, contributor_count, contributor_ids, triggers, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        result.pass_id,
                        entity,
                        str(axis),
                        value.score,
                        value.state.value,
                        value.degree.value if value.degree else None,
                        value.no_read_reason,
                        len(value.contributors),
                        json.dumps(list(value.contributor_ids)),
                        json.dumps(value.triggers),
                        moment.isoformat(),
                    ),
                )

        for entity, expectation in result.expectations.items():
            # Carried-forward entities are copied by `carry_forward` after this write, with
            # their original authorship time. Writing them here as well would both duplicate
            # the row and restamp it as though it had just been produced.
            if entity in result.skipped:
                continue
            connection.execute(
                "INSERT INTO expectations (pass_id, entity, policy_state, directional_state,"
                " confidence, resolves_at, decisive_event_id, reason, event_ids, prompt, model,"
                " effort, authored_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    result.pass_id,
                    entity,
                    expectation.policy.value,
                    expectation.directional.value,
                    expectation.confidence,
                    expectation.resolves_at.isoformat() if expectation.resolves_at else None,
                    expectation.decisive_event_id,
                    expectation.reason,
                    json.dumps(list(expectation.event_ids)),
                    expectation.prompt,
                    expectation.model,
                    expectation.effort,
                    (expectation.authored_at or moment).isoformat(),
                ),
            )

        for entity, record in result.narrative_rows.items():
            connection.execute(
                "INSERT INTO narratives (pass_id, entity, prose, cited_ids, prompt, model,"
                " effort, authored_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    result.pass_id,
                    entity,
                    record["prose"],
                    json.dumps(record["cited"]),
                    record["prompt"],
                    record["model"],
                    record["effort"],
                    moment.isoformat(),
                ),
            )
            for violation in record["violations"]:
                result.warnings.append(f"narrative:{entity}:{violation}")


def _finish(
    connection: Connection, result: PassResult, stages: Stages, moment: datetime
) -> PassResult:
    result.status = "complete"
    # The pass's own moment, not the wall clock: every read, expectation and narrative in
    # this pass is stamped from `moment`, and a finish time from a different clock would
    # make the displayed read age disagree with the content it describes.
    result.finished_at = moment
    result.cost_usd = stages.ledger.total_usd
    result.stage_costs = stages.ledger.as_dict()

    with transaction(connection):
        connection.execute(
            "UPDATE passes SET finished_at = ?, status = 'complete', adherence = ?,"
            " warnings = ?, rejections = ?, skipped_entities = ?, cost_usd = ?, stage_costs = ?"
            " WHERE id = ?",
            (
                result.finished_at.isoformat(),
                result.adherence,
                json.dumps(result.warnings),
                json.dumps(result.ingestion.rejections if result.ingestion else []),
                json.dumps(result.skipped),
                result.cost_usd,
                json.dumps(result.stage_costs),
                result.pass_id,
            ),
        )
    return result


def _fail(
    connection: Connection,
    result: PassResult,
    stages: Stages,
    stage: str,
    error: BaseException,
    moment: datetime,
) -> None:
    """Record which stage failed and leave the previous reads exactly where they were."""
    result.narrative_rows.clear()
    result.status = "failed"
    result.failed_stage = stage
    result.finished_at = moment
    result.cost_usd = stages.ledger.total_usd
    result.stage_costs = stages.ledger.as_dict()

    with transaction(connection):
        connection.execute(
            "UPDATE passes SET finished_at = ?, status = 'failed', failed_stage = ?,"
            " failure = ?, warnings = ?, cost_usd = ?, stage_costs = ? WHERE id = ?",
            (
                result.finished_at.isoformat(),
                stage,
                f"{type(error).__name__}: {error}",
                json.dumps(result.warnings),
                result.cost_usd,
                json.dumps(result.stage_costs),
                result.pass_id,
            ),
        )


# --- Reading a stored board back ------------------------------------------------------------


def latest_pass(connection: Connection) -> dict[str, Any] | None:
    """The most recent completed pass, which is what every view presents."""
    row = connection.execute(
        "SELECT * FROM passes WHERE status = 'complete' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def latest_failure(connection: Connection) -> dict[str, Any] | None:
    """The most recent failed pass, if it happened after the last complete one.

    A scheduled pass that failed at 06:00 leaves the board looking merely stale, and stale
    reads a nobody-is-watching hour old are normal. Surfacing the failure alongside the age
    is the difference between "nothing happened" and "something tried and could not".
    """
    row = connection.execute(
        "SELECT * FROM passes WHERE status = 'failed' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    latest = latest_pass(connection)
    if latest is not None and latest["id"] > row["id"]:
        return None
    return dict(row)


def board(
    connection: Connection, universe: Universe, params: Params, pass_id: int
) -> dict[str, Any]:
    """Resolve one stored pass onto the instrument list.

    Reads are stored per entity; instruments are derived from them at read time rather than
    stored, so widening the universe re-resolves history rather than leaving old passes with
    a stale instrument list.
    """
    reads: dict[str, dict[str, LegRead]] = {"POLICY": {}, "DIRECTIONAL": {}}
    stored: dict[str, dict[str, dict[str, Any]]] = {}

    for row in connection.execute("SELECT * FROM reads WHERE pass_id = ?", (pass_id,)):
        record = dict(row)
        reads[record["axis"]][record["entity"]] = LegRead(
            entity=record["entity"],
            state=State(record["state"]),
            score=float(record["score"]),
        )
        stored.setdefault(record["entity"], {})[record["axis"]] = record

    instruments = [
        resolve(instrument, reads["POLICY"], reads["DIRECTIONAL"], params)
        for instrument in universe.instruments
    ]
    return {"instruments": instruments, "entity_reads": stored}
