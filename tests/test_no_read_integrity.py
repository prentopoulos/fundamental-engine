"""`NO READ` is never rendered or stored as `NEUTRAL`, anywhere in the system.

This is the design's central correction to the sibling project's three-state model, and it
is the kind of property that erodes one convenient default at a time: a `.get(entity,
NEUTRAL)`, a `or "neutral"` in a template, a nullable column that renders as an empty cell.
So it is asserted structurally — over the source and over every path that produces a state
— rather than only at the one call site that happened to be under test.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from engine.reading.arithmetic import read
from engine.reading.states import Axis, State, say
from engine.universe.betas import LegRead, resolve_axis
from engine.universe.config import load_params, load_universe

ENGINE = Path("engine")

# A default that turns an absent or unreadable value into NEUTRAL. Written as a pattern
# rather than a list of known offenders so a new one is caught the day it is added.
NEUTRAL_DEFAULT = re.compile(
    r"(?:get\([^)]*,\s*(?:State\.)?NEUTRAL|or\s+(?:State\.)?NEUTRAL|"
    r"default\s*=\s*(?:State\.)?NEUTRAL|NO_READ\s*(?:->|=>)\s*NEUTRAL)",
    re.IGNORECASE,
)


@pytest.fixture(scope="module")
def params():
    return load_params()


def test_no_module_defaults_a_missing_state_to_neutral():
    offenders = [
        f"{path}:{number}: {line.strip()}"
        for path in ENGINE.rglob("*.py")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if NEUTRAL_DEFAULT.search(line)
    ]

    assert offenders == [], "a state is being defaulted to NEUTRAL:\n" + "\n".join(offenders)


def test_the_two_states_are_distinct_members_not_aliases():
    assert State.NO_READ is not State.NEUTRAL
    assert State.NO_READ.value != State.NEUTRAL.value


def test_no_read_and_neutral_read_out_differently_on_both_axes():
    for axis in Axis:
        assert say(axis, State.NO_READ) == "no read"
        assert say(axis, State.NEUTRAL) == "neutral"


def test_no_read_has_no_ordinal_so_it_cannot_average_with_neutral():
    """Giving it 0 would make it arithmetically indistinguishable from NEUTRAL."""
    from engine.reading.states import ordinal

    assert ordinal(State.NO_READ) is None
    assert ordinal(State.NEUTRAL) == 0


def test_state_from_sign_never_produces_no_read():
    """`NO READ` is a statement about evidence, not about a number."""
    from engine.reading.states import state_from_sign

    for value in (-9.0, -0.001, 0.0, 0.001, 9.0):
        assert state_from_sign(value) is not State.NO_READ


def test_every_unreadable_path_through_the_arithmetic_yields_no_read(params):
    """Zero situations and failed breadth: the two ways an entity axis goes unreadable."""
    from engine.reading.arithmetic import Contribution

    thin = (Contribution("S1", "one story", 5.0, 1, 1.0, "OPEN"),)
    for contributors in ((), thin):
        result = read("EUR", Axis.POLICY, contributors, params)
        assert result.state is State.NO_READ
        assert result.state is not State.NEUTRAL


def test_every_unreadable_path_through_resolution_yields_no_read(params):
    """One unreadable leg, and a leg absent entirely."""
    universe = load_universe()
    cross = universe.instrument("EURNZD")

    unreadable = {
        "EUR": LegRead("EUR", State.POSITIVE, 9.0),
        "NZD": LegRead("NZD", State.NO_READ, 0.0),
    }
    absent = {"EUR": LegRead("EUR", State.POSITIVE, 9.0)}

    for legs in (unreadable, absent):
        axis = resolve_axis(cross, Axis.POLICY, legs, params)
        assert axis.state is State.NO_READ
        assert axis.state is not State.NEUTRAL


def test_the_schema_refuses_to_store_a_null_state(tmp_path):
    """A null would render as nothing, and nothing reads as neutral."""
    import sqlite3

    from engine.store.schema import connect

    connection = connect(tmp_path / "engine.db")
    connection.execute(
        "INSERT INTO passes (trigger, started_at, status) VALUES ('manual', 'now', 'running')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO reads (pass_id, entity, axis, score, state, contributor_count,"
            " contributor_ids, triggers, created_at)"
            " VALUES (1, 'EUR', 'POLICY', 0.0, NULL, 0, '[]', '{}', 'now')"
        )
    connection.close()


def test_the_schema_stores_no_read_as_a_word(tmp_path):
    """It is one of four stored values, not an absence to be inferred."""
    from engine.store.schema import connect

    connection = connect(tmp_path / "engine.db")
    connection.execute(
        "INSERT INTO passes (trigger, started_at, status) VALUES ('manual', 'now', 'running')"
    )
    connection.execute(
        "INSERT INTO reads (pass_id, entity, axis, score, state, no_read_reason,"
        " contributor_count, contributor_ids, triggers, created_at)"
        " VALUES (1, 'EUR', 'POLICY', 0.0, 'NO_READ', 'nothing on file', 0, '[]', '{}', 'now')"
    )

    row = connection.execute("SELECT state, no_read_reason FROM reads").fetchone()
    assert row["state"] == "NO_READ"
    assert row["no_read_reason"] == "nothing on file"
    connection.close()
