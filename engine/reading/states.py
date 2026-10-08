"""The four states, the two axes, and the words each pairing is read out in.

The states are stored sign-neutral — `POSITIVE`, `NEGATIVE`, `NEUTRAL`, `NO_READ` — and
rendered into the axis's own vocabulary at the edge. Keeping one internal vocabulary means
the arithmetic, the beta resolution and the divergence rank are written once rather than
once per axis, and it removes the class of bug where a policy score is accidentally read
out in directional words.

`NO_READ` is a distinct member, never a default and never `None`. the methodology guide is
explicit that a currency with no news and a currency whose news genuinely cancels are not
the same thing, and that showing a blank box as neutral invites the operator to trust an
empty read. Everything in this module exists to make conflating them require deliberate
effort.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class Axis(StrEnum):
    """What a situation primarily drives.

    POLICY is rate decisions, inflation, central bank speech, employment as a rate input.
    DIRECTIONAL is growth, geopolitics, flows, sentiment, flight to quality, earnings.

    The split exists because the equity inversion only lives on the policy axis: bad
    growth news is bearish for both USD and SPX500, while hawkish repricing is bullish for
    one and bearish for the other. One field per situation buys that distinction.
    """

    POLICY = "POLICY"
    DIRECTIONAL = "DIRECTIONAL"


class State(StrEnum):
    """One of exactly four readings for one axis of one entity."""

    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NEUTRAL = "NEUTRAL"
    NO_READ = "NO_READ"

    @property
    def is_directional(self) -> bool:
        return self in (State.POSITIVE, State.NEGATIVE)

    @property
    def is_readable(self) -> bool:
        """Whether the axis says anything at all. `NEUTRAL` does; `NO_READ` does not."""
        return self is not State.NO_READ


class Degree(StrEnum):
    """The banded intensity of a directional read.

    These three words are the only intensity language allowed anywhere in the system,
    including in the prose. The narrative takes the degree the arithmetic assigned rather
    than reaching for "very" or "extremely" — the intensity is the arithmetic's, not the
    writer's.
    """

    SLIGHTLY = "slightly"
    MODERATELY = "moderately"
    STRONGLY = "strongly"


# The words each state is read out in, per axis. The only place the sign-neutral internal
# vocabulary becomes the operator's.
WORDS: Final[dict[Axis, dict[State, str]]] = {
    Axis.POLICY: {
        State.POSITIVE: "hawkish",
        State.NEGATIVE: "dovish",
        State.NEUTRAL: "neutral",
        State.NO_READ: "no read",
    },
    Axis.DIRECTIONAL: {
        State.POSITIVE: "bullish",
        State.NEGATIVE: "bearish",
        State.NEUTRAL: "neutral",
        State.NO_READ: "no read",
    },
}

# Ordinals for divergence: a flip is 2, a step is 1, no change is 0. `NO_READ` has no
# ordinal on purpose — it is not a point on the line between dovish and hawkish, and
# giving it one (0, say) would make it arithmetically indistinguishable from NEUTRAL,
# which is the exact conflation the four-state vocabulary exists to prevent.
_ORDINALS: Final[dict[State, int]] = {
    State.NEGATIVE: -1,
    State.NEUTRAL: 0,
    State.POSITIVE: 1,
}


def say(axis: Axis, state: State, degree: Degree | None = None) -> str:
    """Render a state in its axis's words, with the degree in front when there is one.

    >>> say(Axis.POLICY, State.POSITIVE, Degree.SLIGHTLY)
    'slightly hawkish'
    >>> say(Axis.DIRECTIONAL, State.NO_READ)
    'no read'
    """
    word = WORDS[axis][state]
    if degree is not None and state.is_directional:
        return f"{degree.value} {word}"
    return word


def ordinal(state: State) -> int | None:
    """The state's position on its axis, or `None` when the axis is unreadable."""
    return _ORDINALS.get(state)


def divergence(current: State, expected: State) -> float:
    """How far a read would have to move to become the expected one.

    `2` is a flip (dovish to hawkish), `1` a step (neutral to bullish), `0` no change.

    A move to or from `NO_READ` scores `1` when the other side is directional and `0`
    otherwise. It is a real change worth surfacing — evidence arriving where there was
    none — but it is not a flip, because nothing reversed: there was no prior position to
    reverse. Averaging it in as a flip would push empty entities to the top of the board,
    which is the opposite of what the ranking is for.
    """
    if current is expected:
        return 0.0

    here, there = ordinal(current), ordinal(expected)
    if here is None or there is None:
        readable = expected if here is None else current
        return 1.0 if readable.is_directional else 0.0
    return float(abs(there - here))


def state_from_sign(value: float) -> State:
    """`POSITIVE`, `NEGATIVE` or `NEUTRAL` from a signed number.

    Never returns `NO_READ`: that is a statement about the evidence, not about a number,
    and it can only be decided where the evidence is counted.
    """
    if value > 0:
        return State.POSITIVE
    if value < 0:
        return State.NEGATIVE
    return State.NEUTRAL
