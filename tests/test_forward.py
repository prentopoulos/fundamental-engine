"""The forward lane: window selection, the no-actual guarantee, and divergence ranking."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import pytest

import engine.expectation.forward as forward_module
from engine.expectation.forward import (
    NOTHING_SCHEDULED,
    Event,
    Expectation,
    build_expectation,
    in_window,
    nothing_scheduled,
    order,
    rank_one,
    urgency,
)
from engine.reading.states import Axis, State
from engine.universe.config import load_params
from tests.conftest import executable_source

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
HORIZON = timedelta(days=7)
IMPACTS = ("High", "Medium")


@pytest.fixture(scope="module")
def params():
    return load_params()


@pytest.fixture
def weights(params):
    return params.confidence_weights


def event(hours: float, impact="High", currency="EUR", ident="e1", title="ECB Rate") -> Event:
    return Event(
        id=ident,
        currency=currency,
        title=title,
        scheduled_at=NOW + timedelta(hours=hours),
        impact=impact,
        forecast_text="2.15%",
        previous_text="2.15%",
    )


# --- Window selection ------------------------------------------------------------------


def test_medium_and_high_impact_events_in_window_are_selected():
    events = [event(24, "High"), event(48, "Medium", ident="e2"), event(72, "Low", ident="e3")]

    selected = in_window(events, now=NOW, horizon=HORIZON, impacts=IMPACTS)

    assert [e.id for e in selected] == ["e1", "e2"]


def test_low_impact_events_are_excluded():
    selected = in_window([event(24, "Low")], now=NOW, horizon=HORIZON, impacts=IMPACTS)

    assert selected == []


def test_an_event_beyond_the_horizon_is_excluded():
    selected = in_window([event(24 * 8)], now=NOW, horizon=HORIZON, impacts=IMPACTS)

    assert selected == []


def test_an_event_that_has_already_passed_is_excluded():
    """Its number arrives through the headline feed and is scored like any other headline."""
    selected = in_window([event(-2)], now=NOW, horizon=HORIZON, impacts=IMPACTS)

    assert selected == []


def test_selection_can_be_scoped_to_one_currency():
    events = [event(24, currency="EUR"), event(24, currency="USD", ident="e2")]

    selected = in_window(events, currency="USD", now=NOW, horizon=HORIZON, impacts=IMPACTS)

    assert [e.id for e in selected] == ["e2"]


def test_selected_events_come_back_in_time_order():
    events = [event(72, ident="late"), event(12, ident="early"), event(36, ident="middle")]

    selected = in_window(events, now=NOW, horizon=HORIZON, impacts=IMPACTS)

    assert [e.id for e in selected] == ["early", "middle", "late"]


def test_selection_reads_stored_rows_as_well_as_events():
    rows = [
        {
            "id": "e1",
            "currency": "EUR",
            "title": "ECB Rate",
            "scheduled_at": (NOW + timedelta(hours=24)).isoformat(),
            "impact": "High",
            "forecast_text": "2.15%",
            "previous_text": "2.15%",
        },
    ]

    selected = in_window(rows, now=NOW, horizon=HORIZON, impacts=IMPACTS)

    assert [e.id for e in selected] == ["e1"]


# --- No released value is required ------------------------------------------------------


def test_no_code_in_the_forward_lane_reads_an_actual_field():
    """The property the whole lane rests on, asserted over the source rather than assumed.

    Comments and docstrings are stripped first — the module explains at length why it never
    reads a released value, and that prose must not fail the assertion it documents.
    """
    code = executable_source(inspect.getsource(forward_module))

    assert "actual" not in code.lower()


def test_the_whole_lane_runs_against_a_feed_with_no_actuals(fixture_body, params):
    """Every row of the recorded fixture omits `actual`, exactly as the live feed does."""
    from engine.feeds.calendar_feed import parse_calendar

    events, _ = parse_calendar(fixture_body("calendar_good.json"))
    assert all(not e.has_actual for e in events)

    rows = [
        {
            "id": e.id,
            "currency": e.currency,
            "title": e.title,
            "scheduled_at": e.scheduled_at,
            "impact": str(e.impact),
            "forecast_text": e.forecast_text,
            "previous_text": e.previous_text,
        }
        for e in events
    ]
    selected = in_window(rows, now=NOW, horizon=HORIZON, impacts=IMPACTS)

    assert len(selected) == 3  # the Low-impact JPY row is excluded
    assert all("actual" not in e.line().lower() for e in selected)


def test_an_event_line_carries_only_forecast_and_previous():
    line = event(24).line()

    assert "fcst 2.15%" in line
    assert "prev 2.15%" in line
    assert "actual" not in line.lower()


# --- Nothing scheduled -------------------------------------------------------------------


def test_nothing_scheduled_is_no_read_on_both_axes_never_neutral():
    expectation = nothing_scheduled("NZD")

    assert expectation.policy is State.NO_READ
    assert expectation.directional is State.NO_READ
    assert expectation.policy is not State.NEUTRAL
    assert expectation.reason == NOTHING_SCHEDULED
    assert expectation.resolves_at is None


def test_an_entity_whose_only_events_are_low_impact_gets_nothing_scheduled():
    selected = in_window([event(24, "Low")], now=NOW, horizon=HORIZON, impacts=IMPACTS)

    assert selected == []
    assert nothing_scheduled("EUR").policy is State.NO_READ


# --- Building an expectation --------------------------------------------------------------


def test_the_decisive_events_timestamp_comes_from_the_calendar_not_the_model():
    """So `resolves_at` is always a real scheduled time, never a paraphrase of one."""
    events = [event(24, ident="e1"), event(72, ident="e2")]
    answer = {
        "policy": State.POSITIVE,
        "directional": State.NEUTRAL,
        "confidence": "HIGH",
        "reason": "Thursday's decision is the week.",
        "event_ids": ("e1", "e2"),
        "decisive_event_id": "e2",
    }

    built = build_expectation(
        "EUR", answer, events, prompt="p", model="claude-opus-5", effort="high", now=NOW
    )

    assert built.decisive_event_id == "e2"
    assert built.resolves_at == NOW + timedelta(hours=72)
    assert built.prompt == "p"
    assert built.model == "claude-opus-5"


def test_an_unrecognised_decisive_event_falls_back_to_the_latest_in_window():
    """A named event outside the window cannot be the thing the week turns on."""
    events = [event(24, ident="e1"), event(72, ident="e2")]
    answer = {
        "policy": State.POSITIVE,
        "directional": State.NO_READ,
        "confidence": "MEDIUM",
        "reason": "x",
        "event_ids": (),
        "decisive_event_id": "invented",
    }

    built = build_expectation("EUR", answer, events, prompt="p", model="m", effort=None, now=NOW)

    assert built.decisive_event_id == "e2"


# --- Divergence ranking --------------------------------------------------------------------


def expectation(policy, directional, confidence="HIGH", hours=48) -> Expectation:
    return Expectation(
        entity="EUR",
        policy=policy,
        directional=directional,
        confidence=confidence,
        reason="x",
        resolves_at=NOW + timedelta(hours=hours),
    )


def test_a_flip_outranks_a_step(weights):
    flip = rank_one(
        "USDJPY",
        State.NEGATIVE,
        State.NEUTRAL,
        expectation(State.POSITIVE, State.NEUTRAL),
        at=NOW,
        confidence_weights=weights,
    )
    step = rank_one(
        "EURUSD",
        State.NEUTRAL,
        State.NEUTRAL,
        expectation(State.POSITIVE, State.NEUTRAL),
        at=NOW,
        confidence_weights=weights,
    )

    assert flip.policy_divergence == 2.0
    assert step.policy_divergence == 1.0
    assert flip.rank > step.rank


def test_sooner_outranks_later(weights):
    thursday = rank_one(
        "USDJPY",
        State.NEGATIVE,
        State.NEUTRAL,
        expectation(State.POSITIVE, State.NEUTRAL, hours=48),
        at=NOW,
        confidence_weights=weights,
    )
    monday = rank_one(
        "EURUSD",
        State.NEGATIVE,
        State.NEUTRAL,
        expectation(State.POSITIVE, State.NEUTRAL, hours=144),
        at=NOW,
        confidence_weights=weights,
    )

    assert thursday.rank > monday.rank


def test_higher_confidence_outranks_lower(weights):
    confident = rank_one(
        "A",
        State.NEGATIVE,
        State.NEUTRAL,
        expectation(State.POSITIVE, State.NEUTRAL, "HIGH"),
        at=NOW,
        confidence_weights=weights,
    )
    tentative = rank_one(
        "B",
        State.NEGATIVE,
        State.NEUTRAL,
        expectation(State.POSITIVE, State.NEUTRAL, "LOW"),
        at=NOW,
        confidence_weights=weights,
    )

    assert confident.rank > tentative.rank


def test_a_policy_flip_is_not_diluted_by_a_quiet_directional_axis(weights):
    """`max`, never an average: the quiet axis must not bury the flip."""
    loud = rank_one(
        "US500",
        State.NEGATIVE,
        State.NEUTRAL,
        expectation(State.POSITIVE, State.NEUTRAL),
        at=NOW,
        confidence_weights=weights,
    )
    both_moving = rank_one(
        "XAUUSD",
        State.NEGATIVE,
        State.NEGATIVE,
        expectation(State.POSITIVE, State.POSITIVE),
        at=NOW,
        confidence_weights=weights,
    )

    assert loud.directional_divergence == 0.0
    assert loud.rank == pytest.approx(both_moving.rank)
    assert loud.moving_axis is Axis.POLICY


def test_the_moving_axis_is_the_one_that_changed(weights):
    row = rank_one(
        "GBPJPY",
        State.POSITIVE,
        State.NEGATIVE,
        expectation(State.POSITIVE, State.NEUTRAL),
        at=NOW,
        confidence_weights=weights,
    )

    assert row.policy_divergence == 0.0
    assert row.moving_axis is Axis.DIRECTIONAL


def test_no_change_at_all_ranks_zero_and_names_no_moving_axis(weights):
    row = rank_one(
        "EURUSD",
        State.POSITIVE,
        State.NEUTRAL,
        expectation(State.POSITIVE, State.NEUTRAL),
        at=NOW,
        confidence_weights=weights,
    )

    assert row.rank == 0.0
    assert row.moving_axis is None


def test_an_entity_with_no_expectation_ranks_bottom(weights):
    row = rank_one("EURNZD", State.NO_READ, State.NEUTRAL, None, at=NOW, confidence_weights=weights)

    assert row.rank == 0.0
    assert row.reason == NOTHING_SCHEDULED


def test_evidence_arriving_where_there_was_none_is_a_step_not_a_flip(weights):
    """A move out of NO READ is real, but nothing reversed — it must not top the board."""
    arriving = rank_one(
        "A",
        State.NO_READ,
        State.NEUTRAL,
        expectation(State.POSITIVE, State.NEUTRAL),
        at=NOW,
        confidence_weights=weights,
    )
    flipping = rank_one(
        "B",
        State.NEGATIVE,
        State.NEUTRAL,
        expectation(State.POSITIVE, State.NEUTRAL),
        at=NOW,
        confidence_weights=weights,
    )

    assert arriving.policy_divergence == 1.0
    assert flipping.rank > arriving.rank


def test_urgency_is_floored_so_an_imminent_event_cannot_dominate_by_arithmetic():
    forty_minutes = urgency(NOW + timedelta(minutes=40), NOW)
    two_days = urgency(NOW + timedelta(days=2), NOW)

    assert forty_minutes == pytest.approx(4.0)
    assert forty_minutes > two_days
    assert urgency(None, NOW) == 0.0


def test_ordering_is_descending_and_deterministic(weights):
    rows = [
        rank_one(
            "B",
            State.NEUTRAL,
            State.NEUTRAL,
            expectation(State.NEUTRAL, State.NEUTRAL),
            at=NOW,
            confidence_weights=weights,
        ),
        rank_one(
            "A",
            State.NEUTRAL,
            State.NEUTRAL,
            expectation(State.NEUTRAL, State.NEUTRAL),
            at=NOW,
            confidence_weights=weights,
        ),
        rank_one(
            "C",
            State.NEGATIVE,
            State.NEUTRAL,
            expectation(State.POSITIVE, State.NEUTRAL),
            at=NOW,
            confidence_weights=weights,
        ),
    ]

    ordered = order(rows)

    assert ordered[0].key == "C"
    assert [r.key for r in ordered[1:]] == ["A", "B"]  # tie broken by name, not by input order


# --- The lanes never merge -----------------------------------------------------------------


def test_no_function_here_returns_a_blended_score(weights):
    """The ranking consumes states; nothing combines a current score with an expected stance."""
    row = rank_one(
        "EURUSD",
        State.NEGATIVE,
        State.NEUTRAL,
        expectation(State.POSITIVE, State.NEUTRAL),
        at=NOW,
        confidence_weights=weights,
    )

    assert not hasattr(row, "score")
    assert not hasattr(row, "combined")


def test_an_expectation_holds_no_numeric_score():
    """There is no number on this side of the lane to average with the other."""
    fields = set(vars(expectation(State.POSITIVE, State.NEUTRAL)))

    assert not any("score" in name for name in fields)
