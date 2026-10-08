"""The fact sheet contains no feed text, states every tension, and invents none.

The fact-sheet constraint is the enforcement mechanism for the tracing rule, so the tests
that matter most here are the negative ones: what the stage is *not* given, and what it is
told when there is nothing to say.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from engine.expectation.forward import Event, Expectation
from engine.narrative.audit import invented_citations
from engine.narrative.factsheet import (
    TRIGGER_OBLIGATIONS,
    cited_identifiers,
    expectation_sheet,
    narrative_sheet,
    sentence_count,
)
from engine.reading.arithmetic import Contribution, apply_triggers, read
from engine.reading.states import Axis, State
from engine.store.schema import connect, transaction
from engine.universe.config import load_params

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)

# The body text of a headline. If any of this reaches a fact sheet, the tracing rule is a
# hope rather than a mechanism.
FEED_BODY = (
    "Speaking in Frankfurt, the ECB President said the Governing Council is not done, "
    "adding that rates will stay restrictive for as long as necessary."
)


@pytest.fixture(scope="module")
def params():
    return load_params()


def push(
    magnitude, polarity=1, identifier="S1", description="ECB pushing back on cuts", weight=1.0
):
    return Contribution(identifier, description, magnitude, polarity, weight, "OPEN")


def event(hours=48, ident="e1", title="ECB Main Refinancing Rate"):
    return Event(
        id=ident,
        currency="EUR",
        title=title,
        scheduled_at=NOW + timedelta(hours=hours),
        impact="High",
        forecast_text="2.15%",
        previous_text="2.15%",
    )


def expectation(policy=State.POSITIVE, directional=State.NEUTRAL):
    return Expectation(
        entity="EUR",
        policy=policy,
        directional=directional,
        confidence="HIGH",
        reason="Thursday's ECB decision is the week.",
        resolves_at=NOW + timedelta(hours=48),
        decisive_event_id="e1",
    )


def sheet(params, policy_pushes, directional_pushes, events=None, exp=None, **triggers):
    policy = read("EUR", Axis.POLICY, policy_pushes, params)
    directional = read("EUR", Axis.DIRECTIONAL, directional_pushes, params)
    apply_triggers(policy, directional, params, **triggers)
    return narrative_sheet("EUR", policy, directional, events or [], exp)


# --- What the stage is not given --------------------------------------------------------


def test_the_fact_sheet_contains_no_feed_body_text(params):
    """The stage cannot cite what it was not given. That is the whole mechanism."""
    built = sheet(params, (push(6), push(5, identifier="S2")), (), [event()], expectation())

    assert "Frankfurt" not in built
    assert "Governing Council" not in built
    assert FEED_BODY not in built


def test_there_is_no_parameter_to_pass_headlines_in_through(params):
    """Not merely unused — absent, so a later edit cannot start passing them by habit."""
    import inspect

    signature = inspect.signature(narrative_sheet)

    assert set(signature.parameters) == {"entity", "policy", "directional", "events", "expectation"}
    assert not any("headline" in name for name in signature.parameters)


def test_the_sheet_carries_situation_descriptions_rather_than_headlines(params):
    built = sheet(
        params,
        (
            push(6, description="ECB officials pushing back on 2027 cut pricing"),
            push(5, identifier="S2", description="periphery spreads widening"),
        ),
        (),
    )

    assert "ECB officials pushing back on 2027 cut pricing" in built
    assert "periphery spreads widening" in built


# --- What the sheet states --------------------------------------------------------------


def test_the_sheet_states_both_axes_with_score_degree_and_count(params):
    built = sheet(params, (push(5), push(4, identifier="S2")), ())

    assert "POLICY: moderately hawkish (score +9.0, 2 contributing)" in built
    assert "DIRECTIONAL: no read — nothing on file" in built


def test_situations_are_listed_with_identifiers_and_magnitudes(params):
    built = sheet(params, (push(6), push(5, identifier="S2")), ())

    assert "S1 · magnitude 6" in built
    assert "S2 · magnitude 5" in built


def test_the_majority_polarity_is_listed_first(params):
    built = sheet(
        params,
        (
            push(6, identifier="S1"),
            push(5, identifier="S2"),
            push(3, -1, identifier="S9", description="arguing the other way"),
        ),
        (),
    )

    assert built.index("S1 ·") < built.index("S9 ·")


def test_an_unreadable_axis_states_its_reason(params):
    built = sheet(params, (push(5),), ())

    assert "POLICY: no read — one story is not a read" in built


def test_in_window_events_appear_with_forecast_and_previous(params):
    built = sheet(params, (), (), [event()])

    assert "ECB Main Refinancing Rate" in built
    assert "fcst 2.15%" in built
    assert "actual" not in built.lower()


def test_the_expected_stance_appears_beside_the_current_one(params):
    built = sheet(params, (push(6, -1), push(5, -1, identifier="S2")), (), [event()], expectation())

    assert "EXPECTED" in built
    assert "policy: hawkish" in built
    assert "confidence: HIGH" in built


def test_an_entity_with_nothing_scheduled_says_so(params):
    built = sheet(params, (), ())

    assert "IN-WINDOW EVENTS: none scheduled." in built


# --- Triggers -----------------------------------------------------------------------------


def test_a_fired_trigger_appears_with_its_obligation(params):
    """The obligation travels with the fact rather than living only in the skill file."""
    built = sheet(params, (push(5), push(2.2, -1, identifier="S2")), ())

    assert "near_miss on POLICY" in built
    assert TRIGGER_OBLIGATIONS["near_miss"] in built


def test_axis_conflict_states_both_scores_and_which_is_larger(params):
    built = sheet(
        params,
        (push(5), push(4, identifier="S2")),
        (push(6, -1, identifier="S3"), push(5, -1, identifier="S4")),
    )

    assert "axis_conflict" in built
    assert "'larger': 'directional'" in built


def test_counter_evidence_names_the_minority_situations(params):
    built = sheet(
        params,
        (push(6), push(5, identifier="S2"), push(3, -1, identifier="S9")),
        (),
    )

    assert "counter_evidence" in built
    assert "'S9'" in built


def test_divergence_states_the_resolving_time(params):
    built = sheet(
        params,
        (push(6, -1), push(5, -1, identifier="S2")),
        (),
        [event()],
        expectation(),
        expected_policy=State.POSITIVE,
        resolves_at=NOW + timedelta(hours=48),
    )

    assert "divergence on POLICY" in built
    assert TRIGGER_OBLIGATIONS["divergence"] in built


def test_no_tension_is_stated_explicitly_rather_than_omitted(params):
    """An absent section invites the stage to find a tension to fill it."""
    built = sheet(params, (push(6), push(5, identifier="S2")), ())

    assert "TENSIONS: none fired" in built
    assert "do not manufacture a tension" in built


def test_a_quiet_read_carries_no_trigger_obligations(params):
    built = sheet(params, (push(6), push(5, identifier="S2")), ())

    assert "You must" not in built


# --- The expectation sheet -----------------------------------------------------------------


def test_the_expectation_sheet_carries_the_current_read_as_context(params):
    policy = read("EUR", Axis.POLICY, (push(6), push(5, identifier="S2")), params)
    directional = read("EUR", Axis.DIRECTIONAL, (), params)

    built = expectation_sheet("EUR", policy, directional, [event()], NOW)

    assert "CURRENT READ" in built
    assert "ECB Main Refinancing Rate" in built
    assert "Frankfurt" not in built


# --- Citations ------------------------------------------------------------------------------


def test_citations_are_read_from_the_prose_in_order():
    prose = "EUR is bearish (S12, S15) and the spread story (S19) has not faded, unlike S12."

    assert cited_identifiers(prose) == ("S12", "S15", "S19")


def test_prose_citing_nothing_returns_nothing():
    assert cited_identifiers("USD has nothing on file on either axis.") == ()


@pytest.mark.parametrize(
    ("prose", "expected"),
    [
        ("One sentence.", 1),
        ("Two sentences. Here is the second.", 2),
        ("A. B. C. D. E.", 5),
        ("No terminator", 1),
    ],
)
def test_sentence_counting_is_good_enough_for_the_bound(prose, expected):
    assert sentence_count(prose) == expected


# --- The citation audit ----------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    connection = connect(tmp_path / "engine.db")
    with transaction(connection):
        connection.execute(
            "INSERT INTO passes (trigger, started_at, status) VALUES ('manual', 'now', 'complete')"
        )
        connection.execute(
            "INSERT INTO situations (identifier, description, axis, magnitude, status,"
            " opened_at, last_evidence_at, updated_at)"
            " VALUES ('S12', 'soft growth', 'DIRECTIONAL', 6, 'OPEN', 'now', 'now', 'now')"
        )
    yield connection
    connection.close()


def store_narrative(db, prose: str, cited: list[str]) -> None:
    with transaction(db):
        db.execute(
            "INSERT INTO narratives (pass_id, entity, prose, cited_ids, prompt, model, effort,"
            " authored_at) VALUES (1, 'EUR', ?, ?, 'p', 'claude-opus-5', 'medium', 'now')",
            (prose, json.dumps(cited)),
        )


def test_a_narrative_citing_a_real_situation_passes_the_audit(db):
    store_narrative(db, "EUR is bearish (S12).", ["S12"])

    assert invented_citations(db) == []


def test_a_planted_bad_citation_is_caught(db):
    """The spot check the whole tracing rule rests on."""
    store_narrative(db, "EUR is bearish (S12, S99).", ["S12", "S99"])

    (found,) = invented_citations(db)

    assert found.entity == "EUR"
    assert found.identifier == "S99"
    assert str(found) == "invented_citation:EUR:S99"


def test_a_citation_to_a_resolved_situation_is_not_invention(db):
    """Crying wolf here would ruin the one signal that has to stay trustworthy."""
    with transaction(db):
        db.execute("UPDATE situations SET status = 'RESOLVED_PERM' WHERE identifier = 'S12'")
    store_narrative(db, "EUR was bearish on S12, now resolved.", ["S12"])

    assert invented_citations(db) == []


def test_the_audit_can_be_scoped_to_one_pass(db):
    store_narrative(db, "EUR is bearish (S99).", ["S99"])

    assert len(invented_citations(db, pass_id=1)) == 1
    assert invented_citations(db, pass_id=2) == []
