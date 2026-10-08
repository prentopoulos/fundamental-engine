"""The SQLite schema, and the shape rules written into it.

Two of the design's load-bearing prohibitions live here rather than in a code review:

**No column anywhere blends the two lanes.** `reads` and `expectations` are separate
tables with no foreign key implying a combination, and neither holds a field derived from
both a current score and an expected stance. A single "overall stance" column is the most
natural thing in the world to add and adding it destroys the product, so the schema is
shaped to make it an obvious edit rather than a quiet one (`design.md` §5).

**`NO READ` is a stored value, not an absence.** Every state column holds one of four
words. There is no nullable state, because a null would be rendered as "nothing" by the
first template that forgot to check, and "nothing" reads as neutral.

Prompts are stored verbatim on both per-entity tables. That is what makes the model
comparison cost one pass instead of one week — and reconstructing a prompt afterwards from
its inputs does not reproduce the bytes that were sent, so it is stored rather than rebuilt.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

from engine.store.guard import check_database_path

SCHEMA: Final = """
-- Raw feed items, de-duplicated by link. `link` is the identity the feed guarantees per
-- item and reuses on re-publish, so a repeated poll collides here rather than downstream.
CREATE TABLE IF NOT EXISTS headlines (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    body          TEXT NOT NULL,
    link          TEXT NOT NULL UNIQUE,
    published_at  TEXT NOT NULL,
    source_url    TEXT NOT NULL,
    ingested_at   TEXT NOT NULL,
    triaged       INTEGER NOT NULL DEFAULT 0,   -- 0 unseen, 1 kept, -1 dropped by triage
    scored_at     TEXT
);
CREATE INDEX IF NOT EXISTS headlines_published ON headlines (published_at);
CREATE INDEX IF NOT EXISTS headlines_untriaged ON headlines (triaged, published_at);

-- The week's schedule. Identity excludes forecast and previous so a revision arrives as
-- an update to the event rather than as a second event.
CREATE TABLE IF NOT EXISTS calendar_events (
    id            TEXT PRIMARY KEY,
    currency      TEXT NOT NULL,
    title         TEXT NOT NULL,
    scheduled_at  TEXT NOT NULL,
    impact        TEXT NOT NULL,
    forecast      REAL,
    forecast_text TEXT,
    previous      REAL,
    previous_text TEXT,
    ingested_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS calendar_scheduled ON calendar_events (scheduled_at, impact);

-- The durable stories behind the headlines. `identifier` is the S-number the model is
-- shown and must name back; it is the anti-double-counting mechanism, so it is unique.
CREATE TABLE IF NOT EXISTS situations (
    identifier        TEXT PRIMARY KEY,
    description       TEXT NOT NULL,
    axis              TEXT NOT NULL CHECK (axis IN ('POLICY', 'DIRECTIONAL')),
    magnitude         REAL NOT NULL CHECK (magnitude BETWEEN 1 AND 10),
    status            TEXT NOT NULL CHECK (status IN (
                          'OPEN', 'ESCALATING', 'FADING', 'RESOLVED_TEMP', 'RESOLVED_PERM')),
    opened_at         TEXT NOT NULL,
    last_evidence_at  TEXT NOT NULL,
    resolved_at       TEXT,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS situations_status ON situations (status, last_evidence_at);

-- One story can push two entities in opposite directions, so polarity lives on the link
-- and not on the situation. A risk-off story is +1 for JPY and -1 for AUD in one row each.
CREATE TABLE IF NOT EXISTS situation_entities (
    situation_id  TEXT NOT NULL REFERENCES situations (identifier) ON DELETE CASCADE,
    entity        TEXT NOT NULL,
    polarity      INTEGER NOT NULL CHECK (polarity IN (-1, 1)),
    PRIMARY KEY (situation_id, entity)
);
CREATE INDEX IF NOT EXISTS situation_entities_entity ON situation_entities (entity);

-- Every situation keeps links to what fed it, so any read traces back to source records.
CREATE TABLE IF NOT EXISTS situation_evidence (
    situation_id  TEXT NOT NULL REFERENCES situations (identifier) ON DELETE CASCADE,
    record_kind   TEXT NOT NULL CHECK (record_kind IN ('headline', 'event')),
    record_id     TEXT NOT NULL,
    linked_at     TEXT NOT NULL,
    PRIMARY KEY (situation_id, record_kind, record_id)
);

-- One row per entity per axis per pass. `score` is the raw number, retained always, so
-- thresholds and bands can be re-tuned against recorded passes without re-running a model.
CREATE TABLE IF NOT EXISTS reads (
    pass_id            INTEGER NOT NULL REFERENCES passes (id) ON DELETE CASCADE,
    entity             TEXT NOT NULL,
    axis               TEXT NOT NULL CHECK (axis IN ('POLICY', 'DIRECTIONAL')),
    score              REAL NOT NULL,
    state              TEXT NOT NULL CHECK (state IN (
                           'POSITIVE', 'NEGATIVE', 'NEUTRAL', 'NO_READ')),
    degree             TEXT CHECK (degree IN ('slightly', 'moderately', 'strongly')),
    no_read_reason     TEXT,
    contributor_count  INTEGER NOT NULL,
    contributor_ids    TEXT NOT NULL,   -- JSON array of situation identifiers
    triggers           TEXT NOT NULL,   -- JSON object: which tension triggers fired, with values
    created_at         TEXT NOT NULL,
    PRIMARY KEY (pass_id, entity, axis)
);
CREATE INDEX IF NOT EXISTS reads_entity ON reads (entity, axis, created_at);

-- The forward lane. Separate table, no foreign key to `reads`, nothing derived from both.
CREATE TABLE IF NOT EXISTS expectations (
    pass_id           INTEGER NOT NULL REFERENCES passes (id) ON DELETE CASCADE,
    entity            TEXT NOT NULL,
    policy_state      TEXT NOT NULL CHECK (policy_state IN (
                          'POSITIVE', 'NEGATIVE', 'NEUTRAL', 'NO_READ')),
    directional_state TEXT NOT NULL CHECK (directional_state IN (
                          'POSITIVE', 'NEGATIVE', 'NEUTRAL', 'NO_READ')),
    confidence        TEXT NOT NULL CHECK (confidence IN ('HIGH', 'MEDIUM', 'LOW')),
    resolves_at       TEXT,
    decisive_event_id TEXT,
    reason            TEXT NOT NULL,
    event_ids         TEXT NOT NULL,   -- JSON array; the events the read was derived from
    prompt            TEXT NOT NULL,   -- verbatim bytes sent, never reconstructed
    model             TEXT NOT NULL,
    effort            TEXT,
    authored_at       TEXT NOT NULL,
    PRIMARY KEY (pass_id, entity)
);

-- The paragraph, and the record ids it claimed. Storing the citations is what makes the
-- tracing rule checkable by query rather than by reading every paragraph.
CREATE TABLE IF NOT EXISTS narratives (
    pass_id      INTEGER NOT NULL REFERENCES passes (id) ON DELETE CASCADE,
    entity       TEXT NOT NULL,
    prose        TEXT NOT NULL,
    cited_ids    TEXT NOT NULL,   -- JSON array of situation identifiers
    prompt       TEXT NOT NULL,   -- verbatim bytes sent, never reconstructed
    model        TEXT NOT NULL,
    effort       TEXT,
    authored_at  TEXT NOT NULL,
    PRIMARY KEY (pass_id, entity)
);

-- When a pass ran, what triggered it, what it cost, and what it saw.
CREATE TABLE IF NOT EXISTS passes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger           TEXT NOT NULL CHECK (trigger IN ('hourly', 'release', 'forced', 'manual')),
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    status            TEXT NOT NULL CHECK (status IN ('running', 'complete', 'failed')),
    failed_stage      TEXT,
    failure           TEXT,
    trigger_events    TEXT NOT NULL DEFAULT '[]',  -- JSON; every event that contributed
    skipped_entities  TEXT NOT NULL DEFAULT '[]',  -- JSON; entities change detection skipped
    adherence         REAL,                        -- fraction of returned S-numbers that resolved
    warnings          TEXT NOT NULL DEFAULT '[]',  -- JSON array of named warnings
    rejections        TEXT NOT NULL DEFAULT '[]',  -- JSON array of calendar row rejections
    cost_usd          REAL NOT NULL DEFAULT 0.0,
    stage_costs       TEXT NOT NULL DEFAULT '{}'   -- JSON: per-stage tokens and call counts
);
CREATE INDEX IF NOT EXISTS passes_started ON passes (started_at);

-- Questions the operator asked the desk, and what it answered. Kept because a brief that
-- turns out to have been wrong is only findable if it was written down, and because every
-- question bills a model call that has to show up in the spend.
CREATE TABLE IF NOT EXISTS chats (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    asked_at  TEXT NOT NULL,
    question  TEXT NOT NULL,
    answer    TEXT NOT NULL,
    pass_id   INTEGER REFERENCES passes (id) ON DELETE SET NULL,  -- the read it answered from
    model     TEXT NOT NULL,
    cost_usd  REAL NOT NULL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS chats_asked ON chats (asked_at);
"""

# Column names that would blend the two lanes if they existed. Asserted against the live
# schema by a test, so the prohibition survives a future migration written in a hurry.
FORBIDDEN_COLUMN_FRAGMENTS: Final = (
    "overall",
    "combined",
    "blended",
    "merged",
    "average",
    "composite",
)


def connect(path: Path | str) -> sqlite3.Connection:
    """Open the database, refusing anything that might be the trading one.

    The guard runs before the connection, not after: a check that opens the file first has
    already done the thing it was meant to prevent.
    """
    resolved = check_database_path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(resolved, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    # WAL so the interface can read while a pass is writing. A read that blocks behind an
    # hourly refresh would show a spinner exactly when the operator is looking.
    connection.execute("PRAGMA journal_mode = WAL")
    create_schema(connection)
    return connection


def create_schema(connection: sqlite3.Connection) -> None:
    """Create every table and index if absent. Safe to call on every connection."""
    connection.executescript(SCHEMA)


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block inside one transaction, rolling back on any exception.

    Every write goes through here. A pass that fails halfway must leave the previous
    read intact and readable at its true age — a half-written pass would show the operator
    a board that is neither the old read nor the new one.
    """
    connection.execute("BEGIN")
    try:
        yield connection
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")


def columns(connection: sqlite3.Connection, table: str) -> tuple[str, ...]:
    """The column names of one table, for tests and for the shape assertions."""
    return tuple(row["name"] for row in connection.execute(f"PRAGMA table_info({table})"))


def tables(connection: sqlite3.Connection) -> tuple[str, ...]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )
    return tuple(sorted(row["name"] for row in rows))
