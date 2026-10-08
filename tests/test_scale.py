"""The bearish–bullish scale: where a score lands, and what is drawn when nothing does.

The board's whole claim is that a glance down the line tells you where the book sits. That
only holds if the mapping is exact and the unreadable case draws nothing, so both are
pinned down here rather than eyeballed in a browser.
"""

from __future__ import annotations

import pytest

from engine.reading.states import Axis, State
from engine.universe.config import load_params
from engine.web.scale import (
    SCALE_MAX,
    neutral_band,
    position,
    tone_of,
    track,
    what_this_week_does,
)


@pytest.fixture(scope="module")
def params():
    return load_params()


# --- Placing a score ---------------------------------------------------------------------


def test_zero_sits_dead_centre():
    assert position(0.0) == 50.0


@pytest.mark.parametrize(
    ("score", "expected"),
    [(SCALE_MAX, 100.0), (-SCALE_MAX, 0.0), (SCALE_MAX / 2, 75.0), (-SCALE_MAX / 2, 25.0)],
)
def test_a_score_maps_linearly_across_the_line(score, expected):
    assert position(score) == pytest.approx(expected)


def test_a_score_off_the_scale_pins_to_the_end_rather_than_overflowing(params):
    """The end means "off the scale". A mark drawn past it would leave the card."""
    assert position(999.0) == 100.0
    assert position(-999.0) == 0.0


def test_the_neutral_band_is_the_threshold_drawn(params):
    """The shaded middle is exactly ±READ_THRESHOLD, so a dot inside it visibly explains
    why the read is neutral without the operator being told the number."""
    start, width = neutral_band(params)

    assert start == pytest.approx(position(-params.read_threshold))
    assert start + width == pytest.approx(position(params.read_threshold))


def test_re_tuning_the_threshold_moves_the_band(params):
    """The band is derived, not drawn at a fixed width — lowering the bar widens nothing."""
    from dataclasses import replace

    narrower = neutral_band(replace(params, read_threshold=2.0))
    wider = neutral_band(replace(params, read_threshold=8.0))

    assert narrower[1] < wider[1]


# --- Tone ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (State.POSITIVE, "up"),
        (State.NEGATIVE, "down"),
        (State.NEUTRAL, "flat"),
        (State.NO_READ, "flat"),
    ],
)
def test_tone_follows_the_state(state, expected):
    assert tone_of(state) == expected


# --- Drawing one axis -----------------------------------------------------------------------


def test_an_unreadable_read_draws_nothing_at_all():
    """A dot at zero would say "balanced", which is the conflation the four states prevent."""
    drawn = track(Axis.POLICY, State.NO_READ, 0.0, State.POSITIVE)

    assert drawn.readable is False
    assert drawn.now is None
    assert drawn.expected is None
    assert drawn.bar_width == 0.0
    assert drawn.reason == "nothing on file"


def test_a_readable_read_with_no_expectation_draws_a_dot_and_no_arrow():
    """We know where it is, and nothing scheduled will move it."""
    drawn = track(Axis.POLICY, State.POSITIVE, 8.0, None)

    assert drawn.readable is True
    assert drawn.now is not None
    assert drawn.expected is None
    assert drawn.moving is False


def test_an_unchanged_expectation_draws_no_arrow():
    drawn = track(Axis.POLICY, State.POSITIVE, 8.0, State.POSITIVE)

    assert drawn.expected is None
    assert drawn.moving is False


def test_a_changed_expectation_draws_an_arrow_and_a_connector():
    drawn = track(Axis.POLICY, State.NEGATIVE, -8.0, State.POSITIVE)

    assert drawn.expected is not None
    assert drawn.moving is True
    assert drawn.bar_from == pytest.approx(position(-8.0))
    assert drawn.bar_width > 0


def test_a_sign_flip_is_marked_as_crossing_zero():
    """The loudest thing the board can say: the read is not strengthening, it is turning."""
    drawn = track(Axis.POLICY, State.NEGATIVE, -8.0, State.POSITIVE)

    assert drawn.crosses_zero is True


def test_getting_stronger_in_the_same_direction_is_not_a_crossing():
    drawn = track(Axis.DIRECTIONAL, State.NEUTRAL, 1.0, State.POSITIVE)

    assert drawn.crosses_zero is False
    assert drawn.moving is True


def test_an_unreadable_expectation_leaves_the_dot_alone():
    drawn = track(Axis.POLICY, State.POSITIVE, 8.0, State.NO_READ)

    assert drawn.now is not None
    assert drawn.expected is None


def test_the_expected_mark_carries_no_score(params):
    """The forward lane stores states, not numbers. The arrow is placed at the position for
    its state — nothing here invents a magnitude the model never gave."""
    directional = track(Axis.POLICY, State.NEUTRAL, 0.0, State.POSITIVE)
    same_again = track(Axis.POLICY, State.NEUTRAL, 0.0, State.POSITIVE)

    assert directional.expected.at == same_again.expected.at
    assert directional.expected.at > 50.0


# --- The phrase -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "expected", "phrase"),
    [
        (State.NEGATIVE, State.POSITIVE, "Crosses zero this week"),
        (State.POSITIVE, State.NEGATIVE, "Crosses zero this week"),
        (State.NEUTRAL, State.POSITIVE, "Lifted out of neutral"),
        (State.POSITIVE, State.NEUTRAL, "Pulled into neutral"),
        (State.POSITIVE, State.POSITIVE, "Pushed further, same direction"),
        (State.NEUTRAL, State.NEUTRAL, "Stays neutral"),
        (State.NO_READ, State.POSITIVE, "Gives it a first read"),
        (State.POSITIVE, State.NO_READ, "Nothing scheduled to move it"),
        (State.NO_READ, State.NO_READ, "Nothing to push"),
        (State.POSITIVE, None, "Nothing scheduled to move it"),
        (State.NO_READ, None, "Nothing to push"),
    ],
)
def test_the_phrase_describes_the_move(now, expected, phrase):
    """Deterministic and model-free: a statement about two enum values. Asking a model to
    phrase it would put a sentence on the board that no record backs."""
    assert what_this_week_does(now, expected) == phrase
