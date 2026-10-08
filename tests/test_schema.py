"""The schema exists, and its shape enforces the prohibitions the design rests on."""

from __future__ import annotations

import json
import sqlite3

import pytest

from engine.store.schema import FORBIDDEN_COLUMN_FRAGMENTS, columns, connect, tables, transaction

EXPECTED_TABLES = (
    "calendar_events",
    "expectations",
    "headlines",
    "narratives",
    "passes",
    "reads",
    "situation_entities",
    "situation_evidence",
    "situations",
)


@pytest.fixture
def db(tmp_path):
    connection = connect(tmp_path / "engine.db")
    yield connection
    connection.close()


def test_first_connection_creates_every_table(db):
    assert set(tables(db)) >= set(EXPECTED_TABLES)


def test_connecting_twice_does_not_fail(tmp_path):
    """Schema creation runs on every connection, so it has to be idempotent."""
    first = connect(tmp_path / "engine.db")
    first.close()
    second = connect(tmp_path / "engine.db")
    assert set(tables(second)) >= set(EXPECTED_TABLES)
    second.close()


def test_situations_carry_an_axis_and_entities_carry_a_polarity(db):
    assert "axis" in columns(db, "situations")
    assert "polarity" in columns(db, "situation_entities")


def test_a_situation_axis_outside_the_two_is_refused(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO situations (identifier, description, axis, magnitude, status,"
            " opened_at, last_evidence_at, updated_at)"
            " VALUES ('S1', 'x', 'VIBES', 5, 'OPEN', 'now', 'now', 'now')"
        )


def test_one_story_can_push_two_entities_opposite_ways(db):
    """A risk-off story is positive for JPY and negative for AUD, in the same pass."""
    with transaction(db):
        db.execute(
            "INSERT INTO situations (identifier, description, axis, magnitude, status,"
            " opened_at, last_evidence_at, updated_at)"
            " VALUES ('S1', 'flight to quality', 'DIRECTIONAL', 6, 'OPEN', 'now', 'now', 'now')"
        )
        db.executemany(
            "INSERT INTO situation_entities (situation_id, entity, polarity) VALUES (?, ?, ?)",
            [("S1", "JPY", 1), ("S1", "AUD", -1), ("S1", "SPX500", -1)],
        )

    rows = dict(
        db.execute("SELECT entity, polarity FROM situation_entities WHERE situation_id = 'S1'")
    )
    assert rows == {"JPY": 1, "AUD": -1, "SPX500": -1}


def test_a_polarity_that_is_not_plus_or_minus_one_is_refused(db):
    db.execute(
        "INSERT INTO situations (identifier, description, axis, magnitude, status,"
        " opened_at, last_evidence_at, updated_at)"
        " VALUES ('S1', 'x', 'POLICY', 5, 'OPEN', 'now', 'now', 'now')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO situation_entities (situation_id, entity, polarity)"
            " VALUES ('S1', 'EUR', 0)"
        )


def test_reads_keep_the_raw_score_the_contributors_and_the_triggers(db):
    present = columns(db, "reads")

    assert {"score", "state", "degree", "contributor_count", "contributor_ids", "triggers"} <= set(
        present
    )


def test_a_state_outside_the_four_is_refused(db):
    """`NO_READ` is one of four stored words, not a null and not an absence."""
    db.execute(
        "INSERT INTO passes (trigger, started_at, status) VALUES ('manual', 'now', 'running')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO reads (pass_id, entity, axis, score, state, contributor_count,"
            " contributor_ids, triggers, created_at)"
            " VALUES (1, 'EUR', 'POLICY', 4.0, 'MAYBE', 2, '[]', '{}', 'now')"
        )


def test_the_state_column_is_not_nullable(db):
    """A null state would render as nothing, and nothing reads as neutral."""
    db.execute(
        "INSERT INTO passes (trigger, started_at, status) VALUES ('manual', 'now', 'running')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO reads (pass_id, entity, axis, score, state, contributor_count,"
            " contributor_ids, triggers, created_at)"
            " VALUES (1, 'EUR', 'POLICY', 4.0, NULL, 2, '[]', '{}', 'now')"
        )


def test_narratives_store_the_cited_ids_beside_the_prose(db):
    assert "cited_ids" in columns(db, "narratives")


@pytest.mark.parametrize("table", ["narratives", "expectations"])
def test_per_entity_tables_store_the_verbatim_prompt_and_its_routing(db, table):
    """Stored, never reconstructed — a rebuilt prompt is not what was sent."""
    assert {"prompt", "model", "effort"} <= set(columns(db, table))


# --- The lanes never merge -------------------------------------------------------------


def test_no_column_anywhere_blends_a_current_score_with_an_expected_stance(db):
    """A single 'overall stance' column is the easiest thing to add and it kills the product."""
    offenders: list[str] = []
    for table in tables(db):
        for column in columns(db, table):
            lowered = column.lower()
            if any(fragment in lowered for fragment in FORBIDDEN_COLUMN_FRAGMENTS):
                offenders.append(f"{table}.{column}")

    assert offenders == []


def test_reads_and_expectations_share_no_foreign_key(db):
    """No relation in the schema implies the two lanes combine."""
    for table in ("reads", "expectations"):
        targets = {row["table"] for row in db.execute(f"PRAGMA foreign_key_list({table})")}
        assert targets == {"passes"}, f"{table} references {targets}"


def test_neither_lane_holds_a_score_belonging_to_the_other(db):
    """`expectations` carries states and a confidence; it carries no number to average."""
    expectation_columns = set(columns(db, "expectations"))

    assert not any("score" in c for c in expectation_columns)


# --- Transactions ----------------------------------------------------------------------


def test_a_failing_write_leaves_the_previous_state_intact(db):
    """A pass that dies halfway must not leave a board that is neither old nor new."""
    db.execute(
        "INSERT INTO passes (trigger, started_at, status) VALUES ('manual', 'a', 'complete')"
    )

    with pytest.raises(RuntimeError), transaction(db):
        db.execute(
            "INSERT INTO passes (trigger, started_at, status) VALUES ('hourly', 'b', 'running')"
        )
        raise RuntimeError("stage failed")

    assert [row["started_at"] for row in db.execute("SELECT started_at FROM passes")] == ["a"]


def test_a_successful_transaction_commits(db):
    with transaction(db):
        db.execute(
            "INSERT INTO passes (trigger, started_at, status, warnings)"
            " VALUES ('forced', 'a', 'complete', ?)",
            (json.dumps(["empty_calendar_week"]),),
        )

    row = db.execute("SELECT warnings FROM passes").fetchone()
    assert json.loads(row["warnings"]) == ["empty_calendar_week"]
