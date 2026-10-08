"""The situation set: opening, advancing, resolving, and ageing out.

Situations are the system's memory. No stage ever sees three months of headlines — only
this set, roughly 5–15 items per entity, each with a stable identifier the model must name
rather than re-describe.

Three rules shape the module:

**Identifiers are allocated here, never by a model.** `S1`, `S2`, … monotonically. A model
that invents an identifier is rejected and counted against adherence rather than accepted
into a gap.

**Resolution is immediate.** A resolved situation contributes zero to every score in the
same pass, with no decay period. A resolved story that keeps pushing a read for a few days
is a read of something that is over.

**Ageing is silence, not resolution.** `FADE_AFTER` of quiet marks a situation `FADING`;
`ARCHIVE_AFTER` removes it from the current read while leaving it readable in the archive.
The model is never asked to resolve a story because it went quiet — that would throw away a
live story on the strength of a slow news week.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from sqlite3 import Connection
from typing import Final

from engine.reading.states import Axis
from engine.stages import SituationUpdate
from engine.store.schema import transaction

OPEN_STATUSES: Final = ("OPEN", "ESCALATING", "FADING")
RESOLVED_STATUSES: Final = ("RESOLVED_TEMP", "RESOLVED_PERM")

# Recorded on the pass when one entity carries more open situations than the configured
# ceiling. It is a signal that the identifier protocol is leaking and the same story is
# being opened repeatedly — not a limit that anything is truncated to.
DUPLICATION_WARNING = "situation_duplication"

_IDENTIFIER = re.compile(r"^S(\d+)$")


@dataclass
class ApplyResult:
    """What one situation-update stage did, and how well it followed the protocol."""

    opened: list[str]
    updated: list[str]
    resolved: list[str]
    rejected: list[str]
    warnings: list[str]

    @property
    def adherence(self) -> float:
        """The fraction of named identifiers that resolved to a situation that exists.

        Below 100% is a defect to investigate, not a tuning parameter: a model that
        re-describes `S4` instead of naming it silently double-counts a story, which
        inflates a score and is nearly invisible on the board.
        """
        named = len(self.updated) + len(self.resolved) + len(self.rejected)
        return 1.0 if named == 0 else (named - len(self.rejected)) / named


def next_identifier(connection: Connection) -> str:
    """The next free `S`-number.

    Allocated from the highest ever used rather than from the count, so an archived
    situation's identifier is never handed out again — a re-used identifier would make the
    evidence trail of the old story point at the new one.
    """
    highest = 0
    for row in connection.execute("SELECT identifier FROM situations"):
        matched = _IDENTIFIER.match(str(row["identifier"]))
        if matched:
            highest = max(highest, int(matched.group(1)))
    return f"S{highest + 1}"


def current_set(
    connection: Connection, *, include_resolved: bool = False
) -> list[dict[str, object]]:
    """The situations a stage is shown, oldest evidence first.

    Resolved situations are excluded by default: showing them invites an update against a
    story that is over, and they contribute nothing to a score anyway.
    """
    sql = "SELECT * FROM situations"
    if not include_resolved:
        placeholders = ", ".join("?" for _ in OPEN_STATUSES)
        sql += f" WHERE status IN ({placeholders})"
        rows = connection.execute(sql + " ORDER BY last_evidence_at", OPEN_STATUSES)
    else:
        rows = connection.execute(sql + " ORDER BY last_evidence_at")
    return [dict(row) for row in rows]


def entities_of(connection: Connection, identifier: str) -> dict[str, int]:
    """The per-entity polarities of one situation."""
    return {
        row["entity"]: row["polarity"]
        for row in connection.execute(
            "SELECT entity, polarity FROM situation_entities WHERE situation_id = ?", (identifier,)
        )
    }


def open_situation(
    connection: Connection,
    *,
    description: str,
    axis: Axis,
    magnitude: float,
    entities: tuple[tuple[str, int], ...],
    now: datetime,
    evidence: tuple[tuple[str, str], ...] = (),
) -> str:
    """Open a new situation and link its evidence. Returns the allocated identifier."""
    identifier = next_identifier(connection)
    connection.execute(
        "INSERT INTO situations (identifier, description, axis, magnitude, status,"
        " opened_at, last_evidence_at, updated_at) VALUES (?, ?, ?, ?, 'OPEN', ?, ?, ?)",
        (
            identifier,
            description,
            str(axis),
            magnitude,
            now.isoformat(),
            now.isoformat(),
            now.isoformat(),
        ),
    )
    connection.executemany(
        "INSERT INTO situation_entities (situation_id, entity, polarity) VALUES (?, ?, ?)",
        [(identifier, entity, polarity) for entity, polarity in entities],
    )
    _link_evidence(connection, identifier, evidence, now)
    return identifier


def advance(
    connection: Connection,
    identifier: str,
    *,
    now: datetime,
    magnitude: float | None = None,
    status: str = "OPEN",
    evidence: tuple[tuple[str, str], ...] = (),
) -> None:
    """Advance an existing situation and move its last-evidence time forward.

    Advancing is what resets the ageing clock: a story with new evidence is not fading,
    whatever it was a moment ago, so a `FADING` situation that gets a new headline returns
    to `OPEN` rather than staying marked as quiet.

    **Forward only.** `last_evidence_at` answers "when was this story last written about",
    which cannot go backwards no matter what order the evidence arrives in. Older evidence
    for a story we already know about is corroboration, not a reason to call the story
    staler than it is. Without the guard, a headline processed out of order — a replay
    walking the archive, a late fetch, a clock skew — would age a live story by weeks and
    fade it out of the read.
    """
    if status not in ("OPEN", "ESCALATING"):
        status = "OPEN"

    row = connection.execute(
        "SELECT last_evidence_at FROM situations WHERE identifier = ?", (identifier,)
    ).fetchone()
    seen = max(now.isoformat(), row["last_evidence_at"]) if row else now.isoformat()

    if magnitude is None:
        connection.execute(
            "UPDATE situations SET status = ?, last_evidence_at = ?, updated_at = ?"
            " WHERE identifier = ?",
            (status, seen, now.isoformat(), identifier),
        )
    else:
        connection.execute(
            "UPDATE situations SET magnitude = ?, status = ?, last_evidence_at = ?,"
            " updated_at = ? WHERE identifier = ?",
            (magnitude, status, seen, now.isoformat(), identifier),
        )
    _link_evidence(connection, identifier, evidence, now)


def resolve(connection: Connection, identifier: str, *, status: str, now: datetime) -> None:
    """Mark a situation resolved. It stops contributing to every score immediately."""
    if status not in RESOLVED_STATUSES:
        status = "RESOLVED_TEMP"
    connection.execute(
        "UPDATE situations SET status = ?, resolved_at = ?, updated_at = ? WHERE identifier = ?",
        (status, now.isoformat(), now.isoformat(), identifier),
    )


def age(connection: Connection, *, now: datetime, fade_after: timedelta) -> list[str]:
    """Mark quiet situations `FADING`. Returns the identifiers that moved.

    Archival is not a status change: `ARCHIVE_AFTER` is applied where scores are computed,
    so an archived situation stays exactly as it was and remains readable rather than being
    rewritten into a state it never reached.
    """
    cutoff = (now - fade_after).isoformat()
    rows = connection.execute(
        "SELECT identifier FROM situations WHERE status IN ('OPEN', 'ESCALATING')"
        " AND last_evidence_at < ?",
        (cutoff,),
    ).fetchall()
    faded = [row["identifier"] for row in rows]
    if faded:
        connection.executemany(
            "UPDATE situations SET status = 'FADING', updated_at = ? WHERE identifier = ?",
            [(now.isoformat(), identifier) for identifier in faded],
        )
    return faded


def contributing(
    connection: Connection,
    entity: str,
    axis: Axis,
    *,
    now: datetime,
    archive_after: timedelta,
) -> list[dict[str, object]]:
    """The situations that count towards one entity's score on one axis.

    Two exclusions, and they work differently on purpose. Resolved situations are excluded
    outright and immediately — the story is over. Archived ones are excluded by age while
    keeping their row intact, so "why did this read change?" is answerable next week.
    """
    cutoff = (now - archive_after).isoformat()
    rows = connection.execute(
        "SELECT s.*, e.polarity FROM situations s"
        " JOIN situation_entities e ON e.situation_id = s.identifier"
        " WHERE e.entity = ? AND s.axis = ?"
        "   AND s.status IN ('OPEN', 'ESCALATING', 'FADING')"
        "   AND s.last_evidence_at >= ?"
        " ORDER BY s.last_evidence_at DESC",
        (entity, str(axis), cutoff),
    )
    return [dict(row) for row in rows]


def evidence_for(
    axis: Axis | None,
    entities: set[str],
    touches: tuple[tuple[object, str], ...],
) -> tuple[tuple[str, str], ...]:
    """The headlines that plausibly fed one situation, rather than all of them.

    Linking every headline in a pass to every situation it touched made the evidence trail
    worthless — a BOJ policy situation came back linked to "India gold imports double" and
    "US forces struck Iranian launchers", 74 headlines deep. The spec promises that an
    operator asking *why does this carry magnitude 7* gets the records that answer it.

    Filtered on axis and entity, which is the most the stage's answer supports: it returns
    updates keyed by identifier and never says which development produced which. Two policy
    stories about the same currency in one pass will still cross-link. Fixing that properly
    means asking the stage to name its source, which is a prompt change.
    """
    matched: list[tuple[str, str]] = []
    for touch, headline_id in touches:
        touch_entities = {name for name, _ in getattr(touch, "entities", ())}
        if axis is not None and getattr(touch, "axis", None) is not axis:
            continue
        if entities and not (touch_entities & entities):
            continue
        matched.append(("headline", headline_id))
    return tuple(matched)


def apply_updates(
    connection: Connection,
    updates: tuple[SituationUpdate, ...],
    *,
    now: datetime,
    warning_count: int,
    touches: tuple[tuple[object, str], ...] = (),
    scored_headline_ids: tuple[str, ...] = (),
) -> ApplyResult:
    """Apply one stage's instructions, rejecting identifiers that resolve to nothing.

    Rejection rather than creation is the point. Accepting an unknown identifier by opening
    a situation for it would hide exactly the failure adherence is measured to catch.
    """
    known = {row["identifier"] for row in connection.execute("SELECT identifier FROM situations")}
    result = ApplyResult(opened=[], updated=[], resolved=[], rejected=[], warnings=[])

    with transaction(connection):
        for update in updates:
            if update.action == "open":
                if update.axis is None or update.magnitude is None:
                    result.rejected.append(
                        f"open without an axis or magnitude: {update.description}"
                    )
                    continue
                identifier = open_situation(
                    connection,
                    description=update.description,
                    axis=update.axis,
                    magnitude=update.magnitude,
                    entities=update.entities,
                    now=now,
                    evidence=evidence_for(update.axis, {e for e, _ in update.entities}, touches),
                )
                known.add(identifier)
                result.opened.append(identifier)
                continue

            if update.identifier not in known:
                result.rejected.append(update.identifier)
                continue

            if update.action == "update":
                existing = connection.execute(
                    "SELECT axis FROM situations WHERE identifier = ?", (update.identifier,)
                ).fetchone()
                advance(
                    connection,
                    update.identifier,
                    now=now,
                    magnitude=update.magnitude,
                    status=update.status or "OPEN",
                    evidence=evidence_for(
                        Axis(existing["axis"]) if existing else None,
                        set(entities_of(connection, update.identifier)),
                        touches,
                    ),
                )
                result.updated.append(update.identifier)
            else:
                resolve(connection, update.identifier, status=update.status, now=now)
                result.resolved.append(update.identifier)

        # Evidence and completion markers belong in the same transaction. A crash
        # must leave either both committed or both available for the next pass.
        connection.executemany(
            "UPDATE headlines SET scored_at = ? WHERE id = ?",
            [(now.isoformat(), identifier) for identifier in scored_headline_ids],
        )
        result.warnings.extend(duplication_warnings(connection, warning_count))

    return result


def duplication_warnings(connection: Connection, warning_count: int) -> list[str]:
    """Entities carrying more open situations than the ceiling.

    A signal that the identifier protocol is leaking: the same story being opened again
    each pass instead of being named, which inflates that entity's score quietly.
    """
    placeholders = ", ".join("?" for _ in OPEN_STATUSES)
    rows = connection.execute(
        "SELECT e.entity, count(*) AS n FROM situation_entities e"
        " JOIN situations s ON s.identifier = e.situation_id"
        f" WHERE s.status IN ({placeholders})"
        " GROUP BY e.entity HAVING n > ?",
        (*OPEN_STATUSES, warning_count),
    )
    return [f"{DUPLICATION_WARNING}:{row['entity']}:{row['n']}" for row in rows]


def _link_evidence(
    connection: Connection,
    identifier: str,
    evidence: tuple[tuple[str, str], ...],
    now: datetime,
) -> None:
    if not evidence:
        return
    connection.executemany(
        "INSERT INTO situation_evidence (situation_id, record_kind, record_id, linked_at)"
        " VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
        [(identifier, kind, record_id, now.isoformat()) for kind, record_id in evidence],
    )
