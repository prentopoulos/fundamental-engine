"""What causes a read to be made, and what a read is allowed to skip.

Three triggers and no others: **hourly**, **on release**, and **forced**. Each is recorded on
the pass it produces, so any read on the board can be traced to what caused it.

**Releases are detected on the clock, not by polling for a value.** Verified against the live
endpoint on 2026-08-31: the calendar's key set is `country · date · forecast · impact ·
previous · title` and nothing else, not even on rows already in the past. So a release cannot
be detected by watching the calendar for a number to appear. The schedule says *when*; the
headline feed says *what* — `investinglive` carries the printed number in prose within a
minute or two of release, so the refresh the clock fires ingests the actual as a headline.
This is why no HTML scraping appears anywhere in this project.

**Change detection is what makes an hourly cadence affordable.** The two expensive per-entity
stages re-run only for entities whose situations or scores actually moved. Without it, a quiet
hour would re-author thirteen Opus paragraphs to say exactly what last hour's said.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from sqlite3 import Connection
from typing import Any

from engine.reading.states import Axis
from engine.universe.config import Params

# The precision the fact sheet quotes a score to, and therefore the precision at which a
# score change is one the paragraph would notice.
SCORE_PLACES = 1


@dataclass(frozen=True)
class ReleaseTrigger:
    """One moment at which a refresh should run, and every event that asked for it."""

    at: datetime
    events: tuple[str, ...]
    currencies: tuple[str, ...]

    def describe(self) -> str:
        return f"{self.at.isoformat()} · {', '.join(self.currencies)} · {len(self.events)} event(s)"


@dataclass
class ChangeSet:
    """Which entities moved since the previous pass, and which did not."""

    changed: set[str] = field(default_factory=set)
    unchanged: set[str] = field(default_factory=set)
    reasons: dict[str, str] = field(default_factory=dict)

    def should_run(self, entity: str) -> bool:
        return entity in self.changed


def release_triggers(
    connection: Connection,
    params: Params,
    *,
    since: datetime,
    until: datetime,
) -> list[ReleaseTrigger]:
    """Watched High-impact events, collapsed into distinct moments.

    Only High impact, and only on the watch list — which is configuration and deliberately
    independent of the scored entity set, so a currency may be watched without being scored
    and a trigger on an unscored currency still completes without error.

    Events whose trigger times fall inside the debounce window become one refresh. The week
    of 2026-08-31 has 15 qualifying events but only about 8 distinct moments, because the
    RBNZ fires four at once and the US payrolls print fires three; without this the runtime
    would run four full passes over the same feed in the same minute.
    """
    if not params.release_watch_list:
        return []

    placeholders = ", ".join("?" for _ in params.release_watch_list)
    rows = connection.execute(
        "SELECT id, currency, scheduled_at FROM calendar_events"
        f" WHERE impact = 'High' AND currency IN ({placeholders})"
        " ORDER BY scheduled_at",
        tuple(params.release_watch_list),
    ).fetchall()

    triggers: list[ReleaseTrigger] = []
    pending: list[tuple[datetime, str, str]] = []

    for row in rows:
        fires_at = _moment(row["scheduled_at"]) + params.release_delay
        if not (since < fires_at <= until):
            continue
        if pending and fires_at - pending[0][0] > params.debounce:
            triggers.append(_collapse(pending))
            pending = []
        pending.append((fires_at, str(row["id"]), str(row["currency"])))

    if pending:
        triggers.append(_collapse(pending))
    return triggers


def due_releases(
    connection: Connection,
    params: Params,
    *,
    now: datetime,
    lookback: timedelta | None = None,
) -> list[ReleaseTrigger]:
    """Release triggers that should already have run and have not.

    Answers the missed-trigger case: the runtime was down at 12:35 and starts at 13:10. The
    trigger is run once on startup rather than skipped silently — a read that quietly missed
    a payrolls print is worse than a late one, because nothing on the board says so.

    "Has not run" is decided from the stored passes: a release pass whose recorded trigger
    events include this trigger's events means the work was done.
    """
    window = lookback if lookback is not None else timedelta(hours=12)
    candidates = release_triggers(connection, params, since=now - window, until=now)

    already: set[str] = set()
    for row in connection.execute(
        "SELECT trigger_events FROM passes WHERE trigger = 'release' AND started_at >= ?",
        ((now - window).isoformat(),),
    ):
        try:
            already.update(str(event) for event in json.loads(row["trigger_events"]))
        except json.JSONDecodeError:
            continue

    return [trigger for trigger in candidates if not set(trigger.events) & already]


def detect_changes(
    connection: Connection,
    entities: tuple[str, ...],
    current: dict[str, dict[Axis, Any]],
    *,
    previous_pass_id: int | None,
) -> ChangeSet:
    """Which entities' situations or scores moved since the previous pass.

    Three things are compared, and the choice of the third is load-bearing.

    The **contributor set** catches a story opening or resolving at a magnitude that happens
    to leave the total where it was. The **state** and **degree** catch recency decay
    quietly walking a read across a boundary with no new evidence at all.

    The **score** is compared *as the paragraph would quote it* — rounded to one decimal,
    which is how the fact sheet states it — and not exactly. Comparing exactly would make
    every entity changed on every tick, because a 7-day half-life moves every score a
    little every hour, and change detection would then skip nothing and re-author thirteen
    Opus paragraphs an hour to say what the last hour's said. The question is not "did the
    number move" but "would the paragraph be different", and a decay of 0.004 would not
    change a word of it.

    With no previous pass, everything is changed — the first read has to be written.
    """
    result = ChangeSet()

    if previous_pass_id is None:
        result.changed.update(entities)
        for entity in entities:
            result.reasons[entity] = "first read"
        return result

    stored: dict[tuple[str, str], dict[str, Any]] = {}
    for row in connection.execute(
        "SELECT entity, axis, score, state, degree, contributor_ids FROM reads WHERE pass_id = ?",
        (previous_pass_id,),
    ):
        stored[(row["entity"], row["axis"])] = dict(row)

    for entity in entities:
        moved: list[str] = []
        for axis in Axis:
            before = stored.get((entity, str(axis)))
            after = current.get(entity, {}).get(axis)
            if after is None:
                continue
            if before is None:
                moved.append(f"{axis.value} is new")
                continue

            if set(json.loads(before["contributor_ids"])) != set(after.contributor_ids):
                moved.append(f"{axis.value} situations moved")
            elif before["state"] != after.state.value:
                moved.append(f"{axis.value} state moved")
            elif (before["degree"] or None) != (after.degree.value if after.degree else None):
                moved.append(f"{axis.value} degree moved")
            elif round(float(before["score"]), SCORE_PLACES) != round(after.score, SCORE_PLACES):
                moved.append(f"{axis.value} score moved")

        if moved:
            result.changed.add(entity)
            result.reasons[entity] = "; ".join(moved)
        else:
            result.unchanged.add(entity)

    return result


def carry_forward(
    connection: Connection,
    pass_id: int,
    previous_pass_id: int,
    entities: set[str],
) -> None:
    """Copy an unchanged entity's expectation and narrative onto this pass.

    The row is copied rather than left behind so a single pass id addresses a complete
    board — but `authored_at` is carried over unchanged, which is the point: the interface
    then shows the age of the content that was really produced, not the age of the pass that
    happened to carry it. An entity nobody wrote about must not look freshly considered.
    """
    if not entities:
        return

    placeholders = ", ".join("?" for _ in entities)
    ordered = tuple(sorted(entities))

    connection.execute(
        "INSERT INTO expectations (pass_id, entity, policy_state, directional_state,"
        " confidence, resolves_at, decisive_event_id, reason, event_ids, prompt, model,"
        " effort, authored_at)"
        " SELECT ?, entity, policy_state, directional_state, confidence, resolves_at,"
        " decisive_event_id, reason, event_ids, prompt, model, effort, authored_at"
        f" FROM expectations WHERE pass_id = ? AND entity IN ({placeholders})",
        (pass_id, previous_pass_id, *ordered),
    )
    connection.execute(
        "INSERT INTO narratives (pass_id, entity, prose, cited_ids, prompt, model, effort,"
        " authored_at)"
        " SELECT ?, entity, prose, cited_ids, prompt, model, effort, authored_at"
        f" FROM narratives WHERE pass_id = ? AND entity IN ({placeholders})",
        (pass_id, previous_pass_id, *ordered),
    )


def _collapse(pending: list[tuple[datetime, str, str]]) -> ReleaseTrigger:
    """One refresh for a cluster, firing at the last event in it.

    The last rather than the first: firing at the earliest means the refresh runs before the
    later events in the cluster have printed, which is the situation debouncing exists to
    avoid in the first place.
    """
    return ReleaseTrigger(
        at=max(moment for moment, _, _ in pending),
        events=tuple(event for _, event, _ in pending),
        currencies=tuple(sorted({currency for _, _, currency in pending})),
    )


def _moment(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
