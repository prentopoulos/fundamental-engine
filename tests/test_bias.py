"""The pre-position verdict: one direction per pair, and why.

The board's job stops at "here is the read". This answers the question that follows it —
lean which way, before the number prints — so the reasoning is visible and arguable rather
than left to the operator to redo from two columns.
"""

from __future__ import annotations

import pytest

from engine.expectation.bias import LEAN, NO_BIAS, POSITION_NOW, WAIT, bias, lean
from engine.reading.states import State

UP, DOWN, FLAT, NONE = State.POSITIVE, State.NEGATIVE, State.NEUTRAL, State.NO_READ


# --- The lean of one lane -----------------------------------------------------------------


def test_two_agreeing_axes_give_that_direction():
    """For an instrument both axes point in instrument terms — a hawkish base and a bullish
    base both mean the pair rises — so agreement between them is a real signal."""
    assert lean(UP, UP) is UP
    assert lean(DOWN, DOWN) is DOWN


def test_one_readable_axis_carries_the_lean():
    assert lean(UP, NONE) is UP
    assert lean(NONE, DOWN) is DOWN


def test_conflicting_axes_give_no_lean():
    """Axis conflict is the design's most interesting signal and deliberately not a lean."""
    assert lean(UP, DOWN) is NONE
    assert lean(DOWN, UP) is NONE


def test_two_neutral_axes_are_neutral_not_unreadable():
    assert lean(FLAT, FLAT) is FLAT


def test_nothing_readable_is_unreadable():
    assert lean(NONE, NONE) is NONE


# --- The verdict ---------------------------------------------------------------------------


def test_both_lanes_agreeing_is_the_case_worth_pre_positioning():
    """The direction does not depend on how the number prints."""
    verdict = bias(DOWN, DOWN, DOWN, DOWN)

    assert verdict.word == "bearish"
    assert verdict.stance == POSITION_NOW
    assert verdict.actionable
    assert "agree" in verdict.because


def test_a_read_with_nothing_scheduled_against_it_is_holdable():
    verdict = bias(UP, FLAT, NONE, NONE)

    assert verdict.word == "bullish"
    assert verdict.stance == POSITION_NOW


def test_a_well_signposted_heavy_event_wins_the_conflict():
    """A scheduled decision with a clear implication gets priced before it prints."""
    verdict = bias(DOWN, DOWN, UP, UP, confidence="HIGH", heavy=True)

    assert verdict.word == "bullish"
    assert verdict.stance == LEAN
    assert "signposted" in verdict.because


@pytest.mark.parametrize("confidence", ["MEDIUM", "LOW"])
def test_a_weakly_signposted_event_does_not_overturn_the_current_read(confidence):
    verdict = bias(DOWN, DOWN, UP, UP, confidence=confidence, heavy=True)

    assert verdict.word == "bearish"
    assert verdict.stance == LEAN
    assert "firmer" in verdict.because


def test_a_heavy_conflict_always_returns_a_direction():
    """CPI, payrolls, a rate decision, a missile. The operator has to take a side anyway,
    and an engine that shrugs at exactly that moment is no use."""
    for confidence in ("HIGH", "MEDIUM", "LOW"):
        verdict = bias(UP, UP, DOWN, DOWN, confidence=confidence, heavy=True)
        assert verdict.word in ("bullish", "bearish")
        assert verdict.actionable


def test_a_quiet_conflict_is_allowed_to_stay_a_disagreement():
    """Nothing market-moving is in play, so there is genuinely no call here. Inventing one
    would be worse than saying so."""
    verdict = bias(UP, UP, DOWN, DOWN, confidence="HIGH", heavy=False)

    assert verdict.word == "no bias"
    assert not verdict.actionable
    assert "nothing forces a side" in verdict.because


def test_a_heavy_event_makes_a_new_read_worth_being_early_for():
    quiet = bias(NONE, NONE, UP, UP, heavy=False)
    big = bias(NONE, NONE, UP, UP, heavy=True)

    assert quiet.stance == WAIT and not quiet.actionable
    assert big.stance == LEAN and big.actionable
    assert "heavy" in big.because


def test_an_event_that_creates_the_read_is_worth_watching_not_holding():
    verdict = bias(NONE, NONE, UP, UP)

    assert verdict.word == "bullish"
    assert verdict.stance == WAIT
    assert not verdict.actionable


def test_nothing_anywhere_leans_nowhere():
    verdict = bias(NONE, NONE, NONE, NONE)

    assert verdict.word == "no bias"
    assert verdict.stance == NO_BIAS
    assert not verdict.actionable


def test_balanced_now_and_after_is_a_finding_not_an_absence():
    verdict = bias(FLAT, FLAT, FLAT, FLAT)

    assert verdict.stance == NO_BIAS
    assert "balanced" in verdict.because


def test_every_verdict_carries_its_reasoning():
    """The call must be arguable, which means it has to say why."""
    for now in (UP, DOWN, FLAT, NONE):
        for ahead in (UP, DOWN, FLAT, NONE):
            for heavy in (True, False):
                verdict = bias(now, now, ahead, ahead, heavy=heavy)
                assert verdict.because
                assert verdict.conviction


# --- It is never stored -----------------------------------------------------------------------


def test_the_verdict_is_never_persisted(tmp_path):
    """The two lanes stay separate in storage. This is a render-time statement about them,
    not a third value written down beside them."""
    from engine.store.schema import columns, connect, tables

    connection = connect(tmp_path / "engine.db")
    try:
        for table in tables(connection):
            for column in columns(connection, table):
                assert "bias" not in column.lower()
    finally:
        connection.close()


# --- The verdict must not claim an empty calendar it does not have -----------------------------


def test_a_scheduled_week_is_not_described_as_nothing_scheduled():
    """A pair with a rate decision on Wednesday must never read `nothing scheduled`."""
    verdict = bias(
        State.NO_READ,
        State.POSITIVE,
        State.NEUTRAL,
        State.NEUTRAL,
        scheduled=True,
    )

    assert verdict.stance == POSITION_NOW
    assert "nothing scheduled" not in verdict.because
    assert verdict.because == "the week's events do not argue against it"


def test_a_genuinely_empty_calendar_still_says_nothing_scheduled():
    verdict = bias(
        State.NO_READ,
        State.POSITIVE,
        State.NEUTRAL,
        State.NEUTRAL,
        scheduled=False,
    )

    assert verdict.stance == POSITION_NOW
    assert verdict.because == "nothing scheduled argues against it"


def test_the_direction_is_unchanged_by_how_the_week_is_described():
    """Wording only. The side taken must not depend on the `scheduled` flag."""
    args = (State.NO_READ, State.POSITIVE, State.NEUTRAL, State.NEUTRAL)
    assert bias(*args, scheduled=True).direction is bias(*args, scheduled=False).direction
