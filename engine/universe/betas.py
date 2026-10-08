"""Resolving entity reads onto instruments through signed betas.

Pure configuration and arithmetic — no model call, ever. An FX cross subtracts its two
legs on each axis. Everything quoted against the dollar has no central bank of its own, so
its policy axis is an inherited multiple of the USD policy read: `−1.0` for gold and
silver, `−0.5` for oil, and `−1.3 / −1.0 / −0.8` for the Nasdaq, the S&P and the Dow,
which is long-duration tech repricing hardest on rates and the Dow least.

The `−1` is the whole pre-positioning mechanic (the methodology guide): a hawkish Fed reads
bearish for an index two days before the meeting, and it only works because the policy
axis was kept separate from the directional one. Merely strong US growth is not bearish
for the S&P; a hawkish repricing is.

**Breadth is not re-tested here.** A leg that arrives as anything other than `NO READ` has
already passed the breadth rule where its evidence lives. Re-testing it against a combined
score would either double-count the requirement or silently invent a read from two thin
ones, and neither is what the rule is for.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.reading.states import Axis, Degree, State, ordinal, say, state_from_sign
from engine.universe.config import Instrument, Params


@dataclass(frozen=True)
class LegRead:
    """One entity's read on one axis, as the resolver needs it.

    Deliberately narrower than the stored read: the resolver needs a state, a score and
    nothing else, so no part of it can start depending on a situation list.
    """

    entity: str
    state: State
    score: float


@dataclass(frozen=True)
class InstrumentAxis:
    """One resolved axis of one instrument."""

    axis: Axis
    state: State
    score: float
    degree: Degree | None
    # Which leg made this unreadable, when it is. Named rather than implied, because "no
    # read" with no reason is exactly the blank box the four-state vocabulary exists to
    # avoid showing.
    unreadable_because: tuple[str, ...] = ()

    @property
    def words(self) -> str:
        return say(self.axis, self.state, self.degree)


@dataclass(frozen=True)
class InstrumentRead:
    """An instrument's `policy / directional` pair. Never collapsed to one word."""

    ticker: str
    group: str
    policy: InstrumentAxis
    directional: InstrumentAxis

    @property
    def words(self) -> str:
        return f"{self.policy.words} / {self.directional.words}"

    def axis(self, axis: Axis) -> InstrumentAxis:
        return self.policy if axis is Axis.POLICY else self.directional


def band(score: float, params: Params) -> Degree | None:
    """The degree for a magnitude, or `None` below the first band.

    Presentation only. Re-banding moves a word and never a state, which is why the bands
    can be argued with after a week without re-running anything.
    """
    magnitude = abs(score)
    chosen: Degree | None = None
    for name, floor in params.degree_bands:  # ascending, so the last match wins
        if magnitude >= floor:
            chosen = Degree(name)
    return chosen


def resolve_axis(
    instrument: Instrument,
    axis: Axis,
    reads: dict[str, LegRead],
    params: Params,
) -> InstrumentAxis:
    """Combine the legs of one axis into the instrument's read on it.

    `NO READ` propagates: if any leg the axis needs is unreadable, so is the instrument's
    axis. It is never substituted with `NEUTRAL` and never with zero — treating an unknown
    leg as zero would let `EURNZD` report a confident read off EUR alone while NZD had
    nothing on file at all.
    """
    legs = instrument.policy_legs if axis is Axis.POLICY else instrument.directional_legs

    missing = tuple(entity for entity, _ in legs if entity not in reads)
    unreadable = tuple(
        entity for entity, _ in legs if entity in reads and not reads[entity].state.is_readable
    )
    blocked = missing + unreadable
    if blocked:
        return InstrumentAxis(
            axis=axis, state=State.NO_READ, score=0.0, degree=None, unreadable_because=blocked
        )

    score = sum(weight * reads[entity].score for entity, weight in legs)

    # Every leg is readable, so the instrument reads something. Whether it reads
    # directional is the same threshold question the entity faced, asked of the combined
    # number — which is what makes NAS100USD read harder off the same USD move than US30USD
    # does, rather than all three inheriting one word.
    state = state_from_sign(score) if abs(score) >= params.read_threshold else State.NEUTRAL
    return InstrumentAxis(
        axis=axis,
        state=state,
        score=score,
        degree=band(score, params) if state.is_directional else None,
    )


def resolve_expected_axis(
    instrument: Instrument,
    axis: Axis,
    expected: dict[str, State],
    quiet: frozenset[str] = frozenset(),
) -> InstrumentAxis:
    """Resolve one axis of the **expected** lane, from states alone.

    The forward lane stores states, not numbers — deliberately, so that nothing anywhere can
    average the two lanes into one. That means the expected side cannot reuse
    `resolve_axis`, which derives its state from a score: handed expected states and current
    scores it would quietly ignore the states and hand back a copy of the current read,
    which is exactly the bug this function exists to remove.

    Legs are combined on their ordinal positions instead — `base − quote` for a cross, and
    the sign of the multiplier for an inherited axis — so a hawkish USD expectation resolves
    to a bearish expectation for an index without a number being invented for it.
    """
    legs = instrument.policy_legs if axis is Axis.POLICY else instrument.directional_legs

    # `quiet` names the legs with *nothing scheduled*, and they are the one case where an
    # unreadable expectation must not block the pair. On the current lane an unreadable leg
    # genuinely makes the pair unreadable — there is no score to compute. Forward, "nothing
    # on this leg's calendar" is not missing information, it is the statement that this leg
    # expects no change, and letting it blank the other leg's real call is how a pair with a
    # payrolls print on one side came to read `no read` and be reported as nothing to worry
    # about. A leg is only quiet when the expectation stage said so — no resolving time and
    # no call — never as a way of filling a gap.
    blocked = tuple(
        entity
        for entity, _ in legs
        if entity not in quiet and (entity not in expected or not expected[entity].is_readable)
    )
    if blocked:
        return InstrumentAxis(
            axis=axis, state=State.NO_READ, score=0.0, degree=None, unreadable_because=blocked
        )

    contributing = tuple(
        (entity, weight)
        for entity, weight in legs
        if entity in expected and expected[entity].is_readable
    )
    # Every leg quiet is the honest no-read: nothing on any calendar says anything.
    if not contributing:
        return InstrumentAxis(
            axis=axis,
            state=State.NO_READ,
            score=0.0,
            degree=None,
            unreadable_because=tuple(entity for entity, _ in legs),
        )

    total = 0.0
    for entity, weight in contributing:
        place = ordinal(expected[entity])
        # Guarded for the type checker's sake; an unreadable leg was excluded above.
        total += weight * (place if place is not None else 0)

    # No score is stored: an expected axis carries a direction and nothing else. `score`
    # stays at zero so no caller can mistake this for a number to compare or blend.
    return InstrumentAxis(axis=axis, state=state_from_sign(total), score=0.0, degree=None)


def resolve_expected(
    instrument: Instrument,
    policy: dict[str, State],
    directional: dict[str, State],
    quiet: frozenset[str] = frozenset(),
) -> InstrumentRead:
    """Both axes of the expected lane. `quiet` names legs with nothing scheduled."""
    return InstrumentRead(
        ticker=instrument.ticker,
        group=instrument.group,
        policy=resolve_expected_axis(instrument, Axis.POLICY, policy, quiet),
        directional=resolve_expected_axis(instrument, Axis.DIRECTIONAL, directional, quiet),
    )


def resolve(
    instrument: Instrument,
    policy_reads: dict[str, LegRead],
    directional_reads: dict[str, LegRead],
    params: Params,
) -> InstrumentRead:
    """Resolve both axes of one instrument.

    The two axes are passed separately rather than as one entity-keyed record, so there is
    no shape in which a policy read could be resolved onto the directional axis.
    """
    return InstrumentRead(
        ticker=instrument.ticker,
        group=instrument.group,
        policy=resolve_axis(instrument, Axis.POLICY, policy_reads, params),
        directional=resolve_axis(instrument, Axis.DIRECTIONAL, directional_reads, params),
    )
