"""The bearish–bullish scale: turning a score into a position on a line.

This is the board's whole visual idea. Each instrument is one line running bearish to
bullish; **the dot is now, the arrow is where this week's calendar pushes it**, and the gap
between them is the thing the operator is looking for. An unreadable axis draws a dashed
line and no marks at all — there is no position to claim.

Everything here is pure arithmetic over a stored score, so it is testable without a browser
and cannot drift from what the templates draw.

**The two lanes stay separate.** A mark's position comes from one lane or the other, never
from a blend: the dot is placed from the current score, and the arrow from the expected
state's own position. Nothing in this module averages them — the distance between the two
marks is drawn, not computed into a number and stored.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from engine.reading.states import Axis, State
from engine.universe.config import Params

# The score at each end of the line. A third past the `strongly` floor of 12, so a genuinely
# strong read sits near the end without ever pinning to it — a row that hits the edge should
# mean "off the scale", and if everything pins there the scale stops carrying information.
SCALE_MAX: Final = 16.0

# Where an expected state sits when it has no score of its own. The expected lane stores
# states, not numbers — deliberately, so nothing can average the two lanes — so the arrow is
# placed at the representative position for its state rather than at a score.
#
# Directional lands just past the threshold: it says "expected to be readable in this
# direction", which is what the state means, without inventing a magnitude the model never
# gave.
_EXPECTED_AT: Final = {
    State.POSITIVE: 1.6,
    State.NEGATIVE: -1.6,
    State.NEUTRAL: 0.0,
}


@dataclass(frozen=True)
class Mark:
    """One dot or arrowhead on the line, positioned as a percentage of its width."""

    at: float  # 0–100, left to right
    tone: str  # "up" | "down" | "flat"
    kind: str  # "now" | "expected"


@dataclass(frozen=True)
class Track:
    """One axis drawn on the line: where it is, where it is going, and whether it crosses."""

    axis: Axis
    readable: bool
    now: Mark | None = None
    expected: Mark | None = None
    # The connector runs from one mark to the other. Held as left edge + width so a template
    # can place it with two numbers and no arithmetic of its own.
    bar_from: float = 0.0
    bar_width: float = 0.0
    crosses_zero: bool = False
    reason: str | None = None

    @property
    def moving(self) -> bool:
        return self.bar_width > 0.05


def position(score: float) -> float:
    """A score as a percentage across the line. Clamped, so the ends mean "off the scale"."""
    clamped = max(-SCALE_MAX, min(SCALE_MAX, score))
    return 50.0 + (clamped / SCALE_MAX) * 50.0


def neutral_band(params: Params) -> tuple[float, float]:
    """The shaded middle: the range within which no read goes directional.

    Drawn rather than described so the operator can see *why* a dot sitting just inside it
    reads neutral, without being told the threshold is 3.5.
    """
    half = (params.read_threshold / SCALE_MAX) * 50.0
    return 50.0 - half, half * 2


def tone_of(state: State) -> str:
    if state is State.POSITIVE:
        return "up"
    if state is State.NEGATIVE:
        return "down"
    return "flat"


def track(
    axis: Axis,
    now_state: State,
    now_score: float,
    expected_state: State | None,
) -> Track:
    """Place one axis's marks.

    An unreadable *current* read draws nothing — a dashed line and no dot. There is no
    honest place to put a mark for "we do not know", and putting one at zero would say
    "balanced", which is the exact conflation the four states exist to prevent.

    An unreadable *expected* read draws a dot with no arrow: we know where it is, and
    nothing scheduled will move it.
    """
    if not now_state.is_readable:
        return Track(axis=axis, readable=False, reason="nothing on file")

    here = position(now_score)
    now = Mark(at=here, tone=tone_of(now_state), kind="now")

    if expected_state is None or not expected_state.is_readable or expected_state is now_state:
        return Track(axis=axis, readable=True, now=now, bar_from=here, bar_width=0.0)

    there = position(_EXPECTED_AT[expected_state])
    return Track(
        axis=axis,
        readable=True,
        now=now,
        expected=Mark(at=there, tone=tone_of(expected_state), kind="expected"),
        bar_from=min(here, there),
        bar_width=abs(there - here),
        # A sign flip is the loudest thing the board can say: the read is not merely getting
        # stronger, it is turning over.
        crosses_zero=(here - 50.0) * (there - 50.0) < 0,
    )


def what_this_week_does(now_state: State, expected_state: State | None) -> str:
    """The plain phrase describing the move, from the two states alone.

    Deterministic and model-free: it is a statement about two enum values, and asking a model
    to phrase it would put a sentence on the board that no record backs.
    """
    if expected_state is None or not expected_state.is_readable:
        if not now_state.is_readable:
            return "Nothing to push"
        return "Nothing scheduled to move it"

    if not now_state.is_readable:
        return "Gives it a first read"

    if expected_state is now_state:
        if now_state is State.NEUTRAL:
            return "Stays neutral"
        return "Pushed further, same direction"

    if now_state is State.NEUTRAL:
        return "Lifted out of neutral"

    if expected_state is State.NEUTRAL:
        return "Pulled into neutral"

    return "Crosses zero this week"
