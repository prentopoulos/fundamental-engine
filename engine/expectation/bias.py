"""The pre-position read: what the two lanes agree on, before the event prints.

the methodology guide keeps `NOW` and `EXPECTED` apart in storage and in the arithmetic, and this
does not change that: **nothing here is persisted.** It is computed at render time from two
stored reads and thrown away, so no column anywhere holds a value derived from both lanes.
A test asserts it.

What it adds is the call the operator actually has to make — *which way do I lean before the
number prints* — stated once, in one word, with the reasoning beside it so it can be argued
with. Where the two lanes agree that is simply their agreement.

Where they conflict, whether it answers at all depends on **whether anything heavy is in
play**. A quiet week with the two lanes disagreeing genuinely has no call in it, and saying
so is honest. But when a market-moving event is involved the operator has to take a side
anyway, and an engine that shrugs at exactly that moment is no use. Heavy means either:

- a **High impact** scheduled event decides the week — CPI, payrolls, a rate decision; or
- a contributing situation carries **magnitude 7 or above**, which is the scoring skill's
  own bar for a shock: *a surprise rate move, an unexpected large miss, a major
  geopolitical event*. Missiles, wars and presidents land here without needing a calendar.

The verdict is deterministic and model-free: a statement about four enum values and a
confidence. Nothing asks a model for a trading opinion.

The question it answers is the operator's: *can I take this position before the number
prints, or is the number the whole trade?*

- both lanes point the same way -> the direction survives the event either way. Position now.
- they disagree                 -> resolve toward the better-evidenced lane, and say so.
- one lane is asleep            -> the awake one carries it, with the event as a risk.

It always answers with a direction where one can be justified, because "wait and see" is
the one thing the operator can work out without an engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from engine.reading.states import State

# What the verdict is telling the operator to do. Held as a name rather than a score so it
# can never be mistaken for a number to compare or rank on.
POSITION_NOW: Final = "position"  # both lanes agree; the direction survives the print
LEAN: Final = "lean"  # the lanes disagree; one is better evidenced
WAIT: Final = "wait"  # nothing to hold yet; the event creates the read
NO_BIAS: Final = "none"  # genuinely nothing to lean on


@dataclass(frozen=True)
class Bias:
    """A pre-position verdict. Never stored, never averaged, never a score."""

    direction: State  # POSITIVE / NEGATIVE / NEUTRAL / NO_READ
    stance: str  # POSITION_NOW | LEAN | WAIT | NO_BIAS
    because: str  # the short reason, shown verbatim

    @property
    def word(self) -> str:
        if self.direction is State.POSITIVE:
            return "bullish"
        if self.direction is State.NEGATIVE:
            return "bearish"
        return "no bias"

    @property
    def actionable(self) -> bool:
        """Whether there is a direction worth holding before the event prints."""
        return self.stance in (POSITION_NOW, LEAN)

    @property
    def conviction(self) -> str:
        return {
            POSITION_NOW: "position now",
            LEAN: "lean, event is live",
            WAIT: "wait for the print",
            NO_BIAS: "nothing to lean on",
        }[self.stance]


def lean(policy: State, directional: State) -> State:
    """Which way an instrument's two axes agree, or `NO_READ` if they do not.

    For an instrument both axes already point in instrument terms — a hawkish base and a
    bullish base both mean the pair rises — so agreement between them is a real signal
    rather than a category error. Disagreement is the `axis conflict` the design treats as
    the most interesting thing on the screen, and it deliberately yields no lean at all.
    """
    readable = [s for s in (policy, directional) if s.is_readable]
    if not readable:
        return State.NO_READ

    directions = {s for s in readable if s.is_directional}
    if not directions:
        return State.NEUTRAL
    if len(directions) > 1:
        return State.NO_READ  # the axes conflict; there is no single lean to take
    return directions.pop()


def bias(
    now_policy: State,
    now_directional: State,
    expected_policy: State,
    expected_directional: State,
    confidence: str = "MEDIUM",
    heavy: bool = False,
    scheduled: bool = False,
) -> Bias:
    """The pre-position verdict for one instrument.

    `heavy` is what decides whether a conflict gets an answer: a High impact event or a
    magnitude-7 shock forces a side, and a quiet disagreement is allowed to stay a
    disagreement.

    Deterministic and model-free — a statement about four enum values, a confidence and a
    boolean. Asking a model to judge it would put a trading opinion on the board that no
    record backs.
    """
    now = lean(now_policy, now_directional)
    ahead = lean(expected_policy, expected_directional)

    # Both lanes point the same way. The direction does not depend on how the number
    # prints, which is the one case where taking the position early is justified.
    if now.is_directional and now is ahead:
        return Bias(now, POSITION_NOW, "now and the week ahead agree")

    # The current read is directional and the week ahead does not argue with it. Two
    # different things reach here and they must not be described the same way: a genuinely
    # empty calendar, and a calendar with events on it whose expected effects cancel. Saying
    # "nothing scheduled" over a pair that has a rate decision on Wednesday is a false
    # statement on a screen someone takes a position from.
    if now.is_directional and ahead in (State.NEUTRAL, State.NO_READ):
        if scheduled:
            return Bias(now, POSITION_NOW, "the week's events do not argue against it")
        return Bias(now, POSITION_NOW, "nothing scheduled argues against it")

    # They point opposite ways. With nothing heavy in play there is genuinely no call here,
    # and inventing one would be worse than saying so.
    if now.is_directional and ahead.is_directional and now is not ahead:
        if not heavy:
            return Bias(State.NO_READ, WAIT, "the lanes disagree and nothing forces a side")
        # Something market-moving is involved, so a side has to be taken. HIGH confidence
        # means a scheduled decision with a clear implication, and the market prices those
        # before they print; the expectation wins. Below that the current read is the
        # firmer of the two and the event is a risk to the position, not a reason for it.
        if confidence == "HIGH":
            return Bias(ahead, LEAN, "heavy event, well signposted — lean into it")
        return Bias(now, LEAN, "heavy event ahead, but the current read is firmer")

    # Nothing now, but the week creates a read. Worth holding early only if what creates it
    # is big enough to be worth being early for.
    if not now.is_directional and ahead.is_directional:
        if heavy:
            return Bias(ahead, LEAN, "a heavy event creates the read")
        return Bias(ahead, WAIT, "the event creates the read")

    if now is State.NEUTRAL and ahead is State.NEUTRAL:
        return Bias(State.NEUTRAL, NO_BIAS, "balanced now and after")

    return Bias(State.NO_READ, NO_BIAS, "not enough to lean on")
