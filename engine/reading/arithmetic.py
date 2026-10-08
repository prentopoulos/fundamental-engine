"""The deterministic core: scores, the four-state machine, degrees and tension triggers.

**No model touches anything in this file.** The model assigns magnitude, polarity and axis
to a story; Python owns the score, the state, the threshold comparison and the bands. That
split is the main defence against a read that cannot be explained — every number here can
be recomputed by hand from the stored situations, and a disagreement is a bug rather than a
mood.

The four states are the design's central correction. A currency with no news and a currency
whose news genuinely cancels are not the same thing, and reporting both as `NEUTRAL` invites
the operator to trust an empty read. `NO READ` is therefore its own state, returned with a
reason, never defaulted to and never rendered as neutral.

The near-miss trigger in particular is **computed, not judged**. Asked "is this close to the
threshold?", a model would drift "not quite enough" into a mood; Python compares the score
to `NEAR_MISS_MARGIN × READ_THRESHOLD` and passes a boolean.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from engine.reading.states import Axis, Degree, State, say, state_from_sign
from engine.universe.config import Params

# Named so the interface, the narrative fact sheet and a query all agree on what fired.
NEAR_MISS = "near_miss"
AXIS_CONFLICT = "axis_conflict"
COUNTER_EVIDENCE = "counter_evidence"
DIVERGENCE = "divergence"

# The reasons an axis can be unreadable. Shown to the operator instead of a blank cell,
# because "no read" with no explanation is the same empty box the four states exist to
# avoid.
NOTHING_ON_FILE = "nothing on file"
TOO_THIN = "one story is not a read"
FADED_OUT = "what is on file has gone quiet"


@dataclass(frozen=True)
class Contribution:
    """One situation's weighted push on one entity's axis."""

    identifier: str
    description: str
    magnitude: float
    polarity: int
    recency_weight: float
    status: str

    @property
    def value(self) -> float:
        return self.magnitude * self.recency_weight * self.polarity


@dataclass
class Read:
    """One entity's read on one axis, with everything needed to explain it."""

    entity: str
    axis: Axis
    score: float
    state: State
    degree: Degree | None
    contributors: tuple[Contribution, ...]
    no_read_reason: str | None = None
    triggers: dict[str, Any] = field(default_factory=dict)

    @property
    def contributor_ids(self) -> tuple[str, ...]:
        return tuple(c.identifier for c in self.contributors)

    @property
    def words(self) -> str:
        return say(self.axis, self.state, self.degree)

    @property
    def majority_first(self) -> tuple[Contribution, ...]:
        """Contributors with the winning polarity first, so counter-evidence reads as the
        minority it is. Within each group, the largest push leads."""
        if not self.contributors:
            return ()
        winning = 1 if self.score >= 0 else -1
        return tuple(
            sorted(
                self.contributors,
                key=lambda c: (c.polarity != winning, -abs(c.value)),
            )
        )

    @property
    def minority(self) -> tuple[Contribution, ...]:
        """The situations arguing against the read, if any."""
        if not self.state.is_directional:
            return ()
        winning = 1 if self.score >= 0 else -1
        return tuple(c for c in self.contributors if c.polarity != winning)


def recency_weight(last_evidence: datetime, now: datetime, half_life: timedelta) -> float:
    """`0.5 ^ (age / half_life)`, clamped so a future timestamp cannot amplify a story.

    A feed occasionally publishes a timestamp a few minutes ahead of the clock. Left
    unclamped that would give a weight above 1 and quietly overweight the newest item.
    """
    age = (now - last_evidence).total_seconds()
    if age <= 0:
        return 1.0
    return 0.5 ** (age / half_life.total_seconds())


def contributions(
    rows: list[dict[str, Any]], *, now: datetime, half_life: timedelta
) -> tuple[Contribution, ...]:
    """Turn stored situation rows into weighted contributions."""
    built: list[Contribution] = []
    for row in rows:
        last = _moment(row["last_evidence_at"])
        built.append(
            Contribution(
                identifier=str(row["identifier"]),
                description=str(row["description"]),
                magnitude=float(row["magnitude"]),
                polarity=int(row["polarity"]),
                recency_weight=recency_weight(last, now, half_life),
                status=str(row["status"]),
            )
        )
    return tuple(built)


def score_of(contributors: tuple[Contribution, ...]) -> float:
    """`Σ (magnitude × recency_weight × polarity)`. The whole formula, in one line."""
    return sum(c.value for c in contributors)


def material(contributors: tuple[Contribution, ...], params: Params) -> tuple[Contribution, ...]:
    """The contributors still pulling enough weight to count as corroboration.

    Breadth used to be a bare count, which let a nearly-dead story prop it up: one live
    story at 6.0 plus one faded story at 0.2 scored 6.2 with two contributors and read
    directional, the second supplying 3% of the score and 50% of the corroboration. That is
    the single-headline false signal the breadth rule exists to remove, wearing the rule's
    own uniform.

    Weighing the contributors also makes a story's useful life follow its size rather than a
    shared date — a magnitude-9 shock corroborates for three times as long as a magnitude-3
    note, which is what a single `ARCHIVE_AFTER` date cannot express.
    """
    return tuple(c for c in contributors if abs(c.value) >= params.breadth_minimum)


def passes_breadth(contributors: tuple[Contribution, ...], params: Params) -> bool:
    """Whether the evidence is broad enough for a directional read.

    Either `MIN_SITUATIONS` corroborating stories, or one genuine shock — counted over the
    contributors that are still material, not over everything on file. A thin read resolves
    to `NO READ` and is reported as thin rather than flattened into a false calm.
    """
    live = material(contributors, params)
    if len(live) >= params.min_situations:
        return True
    return any(c.magnitude >= params.shock_magnitude for c in live)


def band(score: float, params: Params) -> Degree | None:
    """The degree for a magnitude. Presentation only — it never changes a state."""
    magnitude = abs(score)
    chosen: Degree | None = None
    for name, floor in params.degree_bands:  # ascending; the last match wins
        if magnitude >= floor:
            chosen = Degree(name)
    return chosen


def read(
    entity: str,
    axis: Axis,
    contributors: tuple[Contribution, ...],
    params: Params,
) -> Read:
    """Run the four-state machine for one entity on one axis.

    The order of the checks is the state machine, and it matters:

    1. no contributors at all      -> NO READ, "nothing on file"
    2. contributors, failing breadth -> NO READ, "one story is not a read"
    3. breadth met, below threshold  -> NEUTRAL, a positive finding
    4. breadth met, at or above      -> directional, with a degree

    Step 2 is the one that is easy to get wrong. A single magnitude-5 story scoring above
    the threshold must read `NO READ`, not `NEUTRAL`: the evidence is too thin to say
    anything, which is a different statement from saying the evidence balances.
    """
    total = score_of(contributors)

    if not contributors:
        return Read(entity, axis, 0.0, State.NO_READ, None, (), no_read_reason=NOTHING_ON_FILE)

    if not passes_breadth(contributors, params):
        # Distinguish "only ever had one story" from "had several and they have faded",
        # because they want opposite responses from the operator.
        reason = TOO_THIN if len(contributors) == 1 else FADED_OUT
        return Read(entity, axis, total, State.NO_READ, None, contributors, no_read_reason=reason)

    if abs(total) < params.read_threshold:
        return Read(entity, axis, total, State.NEUTRAL, None, contributors)

    return Read(entity, axis, total, state_from_sign(total), band(total, params), contributors)


def apply_triggers(
    policy: Read,
    directional: Read,
    params: Params,
    *,
    expected_policy: State | None = None,
    expected_directional: State | None = None,
    resolves_at: datetime | None = None,
) -> None:
    """Compute the four tension triggers and record them on both reads, in place.

    Each trigger stores the values behind it, not just a flag, because the narrative has to
    be able to say *what* the score was — "at +4.2 that is not enough to overturn −8.1" —
    and reconstructing that number afterwards from the read would be one more place for the
    two to disagree.
    """
    for one, other, expected in (
        (policy, directional, expected_policy),
        (directional, policy, expected_directional),
    ):
        one.triggers = {}

        # Near miss: below the bar, but close enough that it is worth saying so. Only
        # meaningful where the evidence was broad enough to be judged at all — a NO READ
        # from thinness is not a near miss, it is an absence.
        if (
            one.state is State.NEUTRAL
            and abs(one.score) >= params.near_miss_floor
            and abs(one.score) < params.read_threshold
        ):
            one.triggers[NEAR_MISS] = {
                "score": round(one.score, 2),
                "threshold": params.read_threshold,
            }

        # Axis conflict: both axes readable and pointing opposite ways. The pair is the
        # signal — it is never collapsed to one word — so the prose has to name both.
        if one.state.is_directional and other.state.is_directional and one.state is not other.state:
            one.triggers[AXIS_CONFLICT] = {
                "policy_score": round(policy.score, 2),
                "directional_score": round(directional.score, 2),
                "larger": (
                    "policy" if abs(policy.score) >= abs(directional.score) else "directional"
                ),
            }

        # Counter-evidence: minority-polarity situations on a winning axis. What is arguing
        # the other way, by identifier, so the paragraph can name it.
        minority = one.minority
        if minority:
            one.triggers[COUNTER_EVIDENCE] = {
                "situations": [c.identifier for c in minority],
                "descriptions": [c.description for c in minority],
            }

        # Divergence: the forward lane disagrees with now. The gap is the product, so this
        # is the trigger the operator most needs the paragraph to spell out.
        if expected is not None and expected is not one.state:
            one.triggers[DIVERGENCE] = {
                "now": one.state.value,
                "expected": expected.value,
                "resolves_at": resolves_at.isoformat() if resolves_at else None,
            }


def _moment(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
