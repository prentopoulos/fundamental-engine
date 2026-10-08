"""Assembling what the templates render, so the templates stay dumb.

Every view model is built here from stored records. Templates receive finished strings and
booleans and make no decisions — a template that has to choose a word is a template that can
choose the wrong one, and the one word this project cannot afford to get wrong is "neutral".

Nothing in this module or below it holds a price, a level or a candle. The interface answers
"what should I go look at?" and then gets out of the way; the chart work happens in
TradingView, by hand.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from sqlite3 import Connection
from typing import Any

from engine.expectation.bias import Bias, bias
from engine.expectation.forward import Event, Expectation, in_window, rank_one
from engine.reading.states import Axis, Degree, State, say
from engine.universe.betas import InstrumentRead, LegRead, resolve, resolve_expected
from engine.universe.config import Instrument, Params, Universe
from engine.web.scale import Track, track, what_this_week_does


@dataclass
class Age:
    """How old a read is, and whether that is now a problem.

    Shown on every view so a stalled loop is visible rather than silent. The age is of the
    content, not of the pass that carried it: an entity whose narrative was retained by
    change detection shows when that paragraph was really written.
    """

    authored_at: datetime | None
    now: datetime
    stale_after: timedelta

    @property
    def delta(self) -> timedelta | None:
        return None if self.authored_at is None else self.now - self.authored_at

    @property
    def is_stale(self) -> bool:
        delta = self.delta
        return delta is None or delta > self.stale_after

    @property
    def words(self) -> str:
        delta = self.delta
        if delta is None:
            return "never"
        minutes = int(delta.total_seconds() // 60)
        if minutes < 1:
            return "just now"
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        if hours < 48:
            return f"{hours}h {minutes % 60}m ago"
        return f"{hours // 24}d ago"


@dataclass
class AxisCell:
    """One axis of one row, already turned into the words it is displayed in."""

    axis: Axis
    words: str
    state: State
    score: float | None = None
    contributors: int = 0
    reason: str | None = None
    moving: bool = False

    @property
    def unreadable(self) -> bool:
        return self.state is State.NO_READ

    @property
    def display(self) -> str:
        """The moving axis is capitalised, so the eye lands on the change.

        Not bolded in a stylesheet — capitalisation survives a screenshot, a narrow window
        and a stylesheet that failed to load, and this is the one signal the board exists
        to deliver.
        """
        return self.words.upper() if self.moving else self.words


@dataclass
class InstrumentRow:
    """One line of the board."""

    ticker: str
    group: str
    now_policy: AxisCell
    now_directional: AxisCell
    expected_policy: AxisCell
    expected_directional: AxisCell
    resolves_at: datetime | None
    reason: str
    rank: float
    legs: tuple[str, ...] = ()
    # The design draws both axes on one line: policy above the baseline, direction below.
    # Two tracks rather than one, because the pair is the read and collapsing it to a single
    # position would throw away the disagreement that is usually the most interesting thing
    # on the screen.
    policy_track: Track | None = None
    directional_track: Track | None = None
    phrase: str = ""
    driver: str = ""
    beta_note: str = ""
    # Computed at render time from two stored reads and thrown away. Never persisted —
    # no column anywhere may hold a value derived from both lanes (the methodology guide).
    bias: Bias | None = None

    @property
    def crosses_zero(self) -> bool:
        return any(
            t is not None and t.crosses_zero for t in (self.policy_track, self.directional_track)
        )

    @property
    def unreadable(self) -> bool:
        return self.now_policy.unreadable and self.now_directional.unreadable

    @property
    def pair_words(self) -> str:
        """The pair, with the blocking leg named where an axis is unreadable.

        `no read` on a cross says nothing about which half is missing. Naming it turns a
        dead end into an answer: four crosses read `no read (CAD)` on policy, so one more
        Canadian story fixes all four.
        """
        return f"{self._say(self.now_policy)} / {self._say(self.now_directional)}"

    @staticmethod
    def _say(cell: AxisCell) -> str:
        if cell.unreadable and cell.reason:
            legs = cell.reason.replace(" unreadable", "")
            return f"{cell.words} ({legs})"
        return cell.words

    @property
    def resolves_words(self) -> str:
        return self.resolves_at.strftime("%a %H:%M") if self.resolves_at else "—"

    @property
    def now_words(self) -> str:
        return f"{self.now_policy.display} / {self.now_directional.display}"

    @property
    def expected_words(self) -> str:
        return f"{self.expected_policy.display} / {self.expected_directional.display}"


@dataclass
class SituationRow:
    """One contributing situation on a detail view."""

    identifier: str
    description: str
    magnitude: float
    polarity: int
    weight: float
    status: str
    minority: bool

    @property
    def sign(self) -> str:
        return "+" if self.polarity > 0 else "−"


@dataclass
class EntityView:
    """Everything behind one entity's read, in the order the operator reads it."""

    entity: str
    narrative: str
    narrative_age: Age
    policy: AxisCell
    directional: AxisCell
    policy_situations: list[SituationRow] = field(default_factory=list)
    directional_situations: list[SituationRow] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    expectation: Expectation | None = None
    # The expected pair, already turned into words here rather than in the template. A
    # template that has to choose a word is a template that can choose the wrong one, and
    # the one word this project cannot afford to get wrong is "neutral".
    expected_policy: AxisCell | None = None
    expected_directional: AxisCell | None = None
    instruments: tuple[str, ...] = ()
    cited: tuple[str, ...] = ()
    triggers: tuple[str, ...] = ()
    open_situations: int = 0
    # "Window to position: 2 days" — the sentence the methodology guide calls the entire product.
    # Nothing computed it before; the board showed a resolving time and left the subtraction
    # to the operator, which is the arithmetic the engine exists to have already done.
    window_days: float | None = None

    @property
    def window_words(self) -> str:
        if self.window_days is None:
            return "nothing scheduled"
        if self.window_days < 1:
            hours = max(1, int(round(self.window_days * 24)))
            return f"{hours} hour{'s' if hours != 1 else ''}"
        days = int(round(self.window_days))
        return f"{days} day{'s' if days != 1 else ''}"


def cell(axis: Axis, state: State, degree: Degree | None = None, **extra: Any) -> AxisCell:
    return AxisCell(axis=axis, words=say(axis, state, degree), state=state, **extra)


def load_reads(connection: Connection, pass_id: int) -> dict[str, dict[str, dict[str, Any]]]:
    """Stored reads, keyed by entity then axis."""
    stored: dict[str, dict[str, dict[str, Any]]] = {}
    for row in connection.execute("SELECT * FROM reads WHERE pass_id = ?", (pass_id,)):
        record = dict(row)
        stored.setdefault(record["entity"], {})[record["axis"]] = record
    return stored


def load_expectations(connection: Connection, pass_id: int) -> dict[str, Expectation]:
    found: dict[str, Expectation] = {}
    for row in connection.execute("SELECT * FROM expectations WHERE pass_id = ?", (pass_id,)):
        record = dict(row)
        found[record["entity"]] = Expectation(
            entity=record["entity"],
            policy=State(record["policy_state"]),
            directional=State(record["directional_state"]),
            confidence=record["confidence"],
            reason=record["reason"],
            resolves_at=_moment(record["resolves_at"]),
            decisive_event_id=record["decisive_event_id"],
            event_ids=tuple(json.loads(record["event_ids"])),
            prompt=record["prompt"],
            model=record["model"],
            effort=record["effort"],
            authored_at=_moment(record["authored_at"]),
        )
    return found


def load_narratives(connection: Connection, pass_id: int) -> dict[str, dict[str, Any]]:
    return {
        row["entity"]: dict(row)
        for row in connection.execute("SELECT * FROM narratives WHERE pass_id = ?", (pass_id,))
    }


def instrument_rows(
    connection: Connection,
    universe: Universe,
    params: Params,
    pass_id: int,
    now: datetime,
) -> list[InstrumentRow]:
    """The board: one row per instrument, ordered by divergence rank.

    Both lanes are resolved onto the instrument separately and displayed side by side. No
    step here combines them — the gap between them is the product.
    """
    stored = load_reads(connection, pass_id)
    expectations = load_expectations(connection, pass_id)

    peaks = _shock_magnitudes(connection, params)
    now_legs = _legs(stored)
    # The expected lane is carried as states, never as scores — see `resolve_expected_axis`.
    expected_states = {
        axis: {
            entity: (
                expectations[entity].axis(Axis(axis)) if entity in expectations else State.NO_READ
            )
            for entity in stored
        }
        for axis in ("POLICY", "DIRECTIONAL")
    }
    # Legs the expectation stage found nothing scheduled for. They are excluded from
    # blocking the pair's forward lane rather than blanking it: a quiet JPY must not delete
    # a CAD payrolls call and leave the row claiming nothing is scheduled. An entity absent
    # from `expectations` altogether is *not* quiet — that is missing information and still
    # blocks.
    quiet = frozenset(
        entity for entity, forward in expectations.items() if forward.resolves_at is None
    )

    rows: list[InstrumentRow] = []
    for instrument in universe.instruments:
        current = resolve(instrument, now_legs["POLICY"], now_legs["DIRECTIONAL"], params)
        expected = resolve_expected(
            instrument, expected_states["POLICY"], expected_states["DIRECTIONAL"], quiet
        )

        # An instrument's expectation is its legs'. Confidence and the resolving time come
        # from the leg that is actually moving, so a quiet leg cannot mute a loud one.
        leg_expectations = [expectations[e] for e in instrument.entities if e in expectations]
        driver = _driver(leg_expectations, current, expected)

        ranked = rank_one(
            instrument.ticker,
            current.policy.state,
            current.directional.state,
            driver,
            at=now,
            confidence_weights=params.confidence_weights,
            reason=driver.reason if driver else "nothing scheduled",
        )

        policy_track = track(
            Axis.POLICY, current.policy.state, current.policy.score, expected.policy.state
        )
        directional_track = track(
            Axis.DIRECTIONAL,
            current.directional.state,
            current.directional.score,
            expected.directional.state,
        )
        # The phrase describes whichever axis actually moves. `max` again, never a blend:
        # a policy flip is worth saying out loud even when direction is asleep.
        if ranked.policy_divergence >= ranked.directional_divergence:
            phrase = what_this_week_does(current.policy.state, expected.policy.state)
        else:
            phrase = what_this_week_does(current.directional.state, expected.directional.state)

        rows.append(
            InstrumentRow(
                ticker=instrument.ticker,
                group=instrument.group,
                policy_track=policy_track,
                directional_track=directional_track,
                phrase=phrase,
                driver=_driver_note(driver, instrument, connection),
                beta_note=_beta_note(instrument),
                bias=bias(
                    current.policy.state,
                    current.directional.state,
                    expected.policy.state,
                    expected.directional.state,
                    driver.confidence if driver else "MEDIUM",
                    _is_heavy(connection, instrument, driver, peaks, params),
                    any(e.resolves_at is not None for e in leg_expectations),
                ),
                now_policy=_axis_cell(current, Axis.POLICY, ranked.policy_divergence > 0),
                now_directional=_axis_cell(
                    current, Axis.DIRECTIONAL, ranked.directional_divergence > 0
                ),
                expected_policy=_axis_cell(expected, Axis.POLICY, ranked.policy_divergence > 0),
                expected_directional=_axis_cell(
                    expected, Axis.DIRECTIONAL, ranked.directional_divergence > 0
                ),
                resolves_at=ranked.resolves_at,
                reason=ranked.reason,
                rank=ranked.rank,
                legs=instrument.entities,
            )
        )

    # Descending rank, ties broken on the sooner resolution then the ticker. Deterministic
    # because the interface offers keyboard navigation in this order, and a board that
    # reshuffles between two identical passes would move the row under the operator's hands.
    return sorted(
        rows,
        key=lambda r: (
            -r.rank,
            r.resolves_at.timestamp() if r.resolves_at else float("inf"),
            r.ticker,
        ),
    )


def entity_view(
    connection: Connection,
    universe: Universe,
    params: Params,
    pass_id: int,
    entity: str,
    now: datetime,
) -> EntityView | None:
    """One entity's page, assembled in the order it is read.

    The narrative comes first because that is what the page is for. A page that opens with a
    score table invites the operator to do the synthesis themselves, which is the job the
    engine was built to do.
    """
    stored = load_reads(connection, pass_id).get(entity)
    if stored is None:
        return None

    narratives = load_narratives(connection, pass_id)
    expectations = load_expectations(connection, pass_id)
    record = narratives.get(entity)

    events = [event for event in _events(connection, params, now) if event.currency == entity]

    policy = stored.get("POLICY", {})
    directional = stored.get("DIRECTIONAL", {})
    expectation = expectations.get(entity)

    cited = tuple(json.loads(record["cited_ids"])) if record else ()
    open_situations = connection.execute(
        "SELECT count(DISTINCT s.identifier) FROM situations s"
        " JOIN situation_entities e ON e.situation_id = s.identifier"
        " WHERE e.entity = ? AND s.status IN ('OPEN', 'ESCALATING', 'FADING')",
        (entity,),
    ).fetchone()[0]

    fired: list[str] = []
    for record_axis in (policy, directional):
        if record_axis:
            fired.extend(json.loads(record_axis["triggers"]).keys())

    window = None
    if expectation is not None and expectation.resolves_at is not None:
        window = max(0.0, (expectation.resolves_at - now).total_seconds() / 86_400)

    return EntityView(
        entity=entity,
        narrative=record["prose"] if record else "",
        narrative_age=Age(
            authored_at=_moment(record["authored_at"]) if record else None,
            now=now,
            stale_after=params.stale_after,
        ),
        policy=_stored_cell(Axis.POLICY, policy),
        directional=_stored_cell(Axis.DIRECTIONAL, directional),
        policy_situations=_situations(connection, entity, Axis.POLICY, policy, params, now),
        directional_situations=_situations(
            connection, entity, Axis.DIRECTIONAL, directional, params, now
        ),
        events=events,
        expectation=expectation,
        expected_policy=(cell(Axis.POLICY, expectation.policy) if expectation else None),
        expected_directional=(
            cell(Axis.DIRECTIONAL, expectation.directional) if expectation else None
        ),
        instruments=tuple(i.ticker for i in universe.instruments_touching(entity)),
        cited=cited,
        triggers=tuple(dict.fromkeys(fired)),
        open_situations=open_situations,
        window_days=window,
    )


# --- Assembly helpers -------------------------------------------------------------------


def _legs(stored: dict[str, dict[str, dict[str, Any]]]) -> dict[str, dict[str, LegRead]]:
    """Current entity reads, as the resolver wants them.

    Only the current lane goes through here. The expected lane has no scores at all, so it
    resolves through `resolve_expected` on states — which is what stops the two lanes from
    ever sharing an arithmetic path.
    """
    legs: dict[str, dict[str, LegRead]] = {"POLICY": {}, "DIRECTIONAL": {}}
    for entity, axes in stored.items():
        for axis, record in axes.items():
            legs[axis][entity] = LegRead(
                entity=entity,
                state=State(record["state"]),
                score=float(record["score"]),
            )
    return legs


def _shock_magnitudes(connection: Connection, params: Params) -> dict[str, float]:
    """The largest live magnitude on each entity, for deciding what counts as heavy.

    `SHOCK_MAGNITUDE` is the scoring skill's own bar for a shock — a surprise rate move, an
    unexpected large miss, a major geopolitical event — so a missile or a war lands here
    without needing anything on a calendar.
    """
    return {
        row["entity"]: float(row["m"])
        for row in connection.execute(
            "SELECT e.entity, max(s.magnitude) AS m FROM situations s"
            " JOIN situation_entities e ON e.situation_id = s.identifier"
            " WHERE s.status IN ('OPEN', 'ESCALATING', 'FADING') GROUP BY e.entity"
        )
    }


def _is_heavy(
    connection: Connection,
    instrument: Instrument,
    driver: Expectation | None,
    peaks: dict[str, float],
    params: Params,
) -> bool:
    """Whether anything market-moving is in play for this instrument.

    Either a High impact event decides its week — CPI, payrolls, a rate decision — or one of
    its legs carries a shock-magnitude story. Either is enough to force a side on a
    conflict; without one, a disagreement is allowed to stay a disagreement.
    """
    if any(peaks.get(entity, 0.0) >= params.shock_magnitude for entity in instrument.entities):
        return True

    if driver is None or driver.decisive_event_id is None:
        return False
    row = connection.execute(
        "SELECT impact FROM calendar_events WHERE id = ?", (driver.decisive_event_id,)
    ).fetchone()
    return bool(row) and str(row["impact"]) == "High"


def _beta_note(instrument: Instrument) -> str:
    """`policy beta −1.3` for an instrument that inherits its policy axis.

    Shown because the number is the whole reason a hawkish Fed reads bearish for an index,
    and an operator who cannot see it has to take the row on faith.
    """
    if len(instrument.policy_legs) != 1:
        return ""
    entity, weight = instrument.policy_legs[0]
    if weight == 1.0:
        return ""
    return f"policy beta {weight:+.1f}".replace("+", "").replace("-", "−")


def _driver_note(driver: Expectation | None, instrument: Instrument, connection: Connection) -> str:
    """The event the row turns on, named and timed."""
    if driver is None or driver.decisive_event_id is None:
        return ""
    row = connection.execute(
        "SELECT title, scheduled_at FROM calendar_events WHERE id = ?",
        (driver.decisive_event_id,),
    ).fetchone()
    if row is None:
        return ""
    when = _moment(row["scheduled_at"])
    title = str(row["title"])
    short = title if len(title) <= 34 else title[:33] + "…"
    return f"{short} · {when.strftime('%a %H:%M')}" if when else short


def _driver(
    leg_expectations: list[Expectation],
    current: InstrumentRead,
    expected: InstrumentRead,
) -> Expectation | None:
    """The leg expectation the instrument's row should quote.

    The most confident one with a real resolving time, so a row whose USD leg has an FOMC on
    Thursday does not quote a quiet EUR leg's "nothing scheduled" as its reason.
    """
    scheduled = [e for e in leg_expectations if e.resolves_at is not None]
    if not scheduled:
        return leg_expectations[0] if leg_expectations else None

    ranking = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    chosen = min(scheduled, key=lambda e: (ranking.get(e.confidence, 3), e.resolves_at))
    return Expectation(
        entity=chosen.entity,
        policy=expected.policy.state,
        directional=expected.directional.state,
        confidence=chosen.confidence,
        reason=chosen.reason,
        resolves_at=chosen.resolves_at,
        decisive_event_id=chosen.decisive_event_id,
    )


def _axis_cell(read: InstrumentRead, axis: Axis, moving: bool) -> AxisCell:
    resolved = read.axis(axis)
    return AxisCell(
        axis=axis,
        words=resolved.words,
        state=resolved.state,
        score=resolved.score,
        reason=(
            f"{', '.join(resolved.unreadable_because)} unreadable"
            if resolved.unreadable_because
            else None
        ),
        moving=moving,
    )


def _stored_cell(axis: Axis, record: dict[str, Any]) -> AxisCell:
    if not record:
        return cell(axis, State.NO_READ, reason="nothing on file")
    degree = Degree(record["degree"]) if record["degree"] else None
    return AxisCell(
        axis=axis,
        words=say(axis, State(record["state"]), degree),
        state=State(record["state"]),
        score=float(record["score"]),
        contributors=int(record["contributor_count"]),
        reason=record["no_read_reason"],
    )


def _situations(
    connection: Connection,
    entity: str,
    axis: Axis,
    record: dict[str, Any],
    params: Params,
    now: datetime,
) -> list[SituationRow]:
    """The contributing situations, majority polarity first.

    Read from the stored contributor list rather than recomputed, so the page shows what the
    pass actually used — a situation that has since aged out still explains the read it is
    displayed beside.
    """
    if not record:
        return []
    identifiers = json.loads(record["contributor_ids"])
    if not identifiers:
        return []

    placeholders = ", ".join("?" for _ in identifiers)
    rows = connection.execute(
        "SELECT s.identifier, s.description, s.magnitude, s.status, s.last_evidence_at,"
        " e.polarity FROM situations s"
        " JOIN situation_entities e ON e.situation_id = s.identifier"
        f" WHERE s.identifier IN ({placeholders}) AND e.entity = ?",
        (*identifiers, entity),
    ).fetchall()

    from engine.reading.arithmetic import recency_weight

    score = float(record["score"])
    winning = 1 if score >= 0 else -1
    built = [
        SituationRow(
            identifier=row["identifier"],
            description=row["description"],
            magnitude=float(row["magnitude"]),
            polarity=int(row["polarity"]),
            weight=recency_weight(
                _moment(row["last_evidence_at"]) or now, now, params.half_life(axis)
            ),
            status=row["status"],
            minority=int(row["polarity"]) != winning,
        )
        for row in rows
    ]
    return sorted(built, key=lambda s: (s.minority, -s.magnitude * s.weight))


WORDS_FOR = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven"}


def agreement(situations: list[SituationRow], axis_label: str) -> str:
    """`Direction — three of four agree`, or `Policy — both agree`.

    A count the operator would otherwise do by eye down a column of signs. It is the same
    fact the counter-evidence trigger fires on, said in the place the evidence is listed.
    """
    if not situations:
        return axis_label
    majority = sum(1 for s in situations if not s.minority)
    total = len(situations)
    if majority == total:
        return f"{axis_label} — {'both' if total == 2 else 'all'} agree"
    return (
        f"{axis_label} — {WORDS_FOR.get(majority, majority)} of {WORDS_FOR.get(total, total)} agree"
    )


def axis_verdict(cell: AxisCell, params: Params) -> str:
    """One line saying what the number means against the bar.

    `Just clears the 3.5 bar` is a more useful thing to read than `+4.2`, and it is the
    sentence the design puts under every axis. Deterministic — it is a comparison, not a
    judgement, so no model is asked for it.
    """
    if cell.unreadable:
        return cell.reason or "nothing on file"
    magnitude = abs(cell.score or 0.0)
    bar = params.read_threshold
    if cell.state is State.NEUTRAL:
        if magnitude >= params.near_miss_floor:
            return f"Short of the {bar:g} bar, but not by much."
        return f"Evidence exists and it cancels — well short of the {bar:g} bar."
    if magnitude < bar * 1.3:
        return f"Just clears the {bar:g} bar."
    if magnitude >= params.degree_bands[-1][1]:
        return f"Far past the {bar:g} bar."
    return f"Well past the {bar:g} bar."


def _events(connection: Connection, params: Params, now: datetime) -> list[Event]:
    rows = [dict(row) for row in connection.execute("SELECT * FROM calendar_events")]
    return in_window(rows, now=now, horizon=params.horizon, impacts=params.expectation_impacts)


def _moment(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
