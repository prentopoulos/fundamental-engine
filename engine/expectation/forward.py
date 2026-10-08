"""The forward lane: in-window event selection, the expected stance, and the ranking.

This is the anticipatory half of the product. A rate decision on Thursday moves the market
on Thursday, but the position that profits from it has to be opened on Tuesday, and this
module is what makes Tuesday visible.

**Nothing here reads an `actual`.** Verified against the live endpoint on 2026-08-31: the
calendar's key set is `country · date · forecast · impact · previous · title` and nothing
else, not even on rows already in the past. The forward read needs only title, impact,
forecast and previous — which is exactly why no HTML scraping appears anywhere in this
project, and why the whole lane keeps working on a feed that never publishes a result.

**The two lanes never merge in arithmetic.** Nothing in this file computes a blended score,
and the ranking consumes states rather than combining numbers across the lanes. The gap
between now and expected *is* the product; averaging it away destroys the only thing the
engine is for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from engine.reading.states import Axis, State, divergence

# A confidence the model did not give, or gave outside the three. Weighted as the lowest
# rather than dropped: an expectation that arrived is information, and silently ranking it
# as though it were absent would hide it.
UNKNOWN_CONFIDENCE_WEIGHT = 0.3

NOTHING_SCHEDULED = "nothing scheduled"


@dataclass(frozen=True)
class Event:
    """One in-window calendar event, as the forward lane needs it."""

    id: str
    currency: str
    title: str
    scheduled_at: datetime
    impact: str
    forecast_text: str | None = None
    previous_text: str | None = None

    def line(self) -> str:
        """One line for a prompt or a view. No `actual`, because there is never one."""
        when = self.scheduled_at.strftime("%a %d %b %H:%M UTC")
        readings = " · ".join(
            part
            for part in (
                f"fcst {self.forecast_text}" if self.forecast_text else "",
                f"prev {self.previous_text}" if self.previous_text else "",
            )
            if part
        )
        return f"[{self.id}] {when} · {self.impact} · {self.title}" + (
            f" · {readings}" if readings else ""
        )


@dataclass
class Expectation:
    """One entity's forward read. Separate from `Read` by design, and never averaged with it."""

    entity: str
    policy: State
    directional: State
    confidence: str
    reason: str
    resolves_at: datetime | None = None
    decisive_event_id: str | None = None
    event_ids: tuple[str, ...] = ()
    prompt: str = ""
    model: str = ""
    effort: str | None = None
    authored_at: datetime | None = None

    def axis(self, axis: Axis) -> State:
        return self.policy if axis is Axis.POLICY else self.directional


@dataclass
class Ranked:
    """One instrument's or entity's place on the board."""

    key: str
    rank: float
    policy_divergence: float
    directional_divergence: float
    resolves_at: datetime | None
    reason: str
    moving_axis: Axis | None = None
    detail: Any = field(default=None, repr=False)


def in_window(
    events: list[Event] | list[dict[str, Any]],
    *,
    currency: str | None = None,
    now: datetime,
    horizon: timedelta,
    impacts: tuple[str, ...],
) -> list[Event]:
    """Medium and High impact events that have not yet printed, inside the horizon.

    "Not yet printed" is decided from the schedule and the clock, because the feed carries
    no result to check. An event whose time has passed is out of the forward read — the
    number it produced arrives through the headline feed and is scored as a headline like
    anything else.
    """
    horizon_end = now + horizon
    selected: list[Event] = []
    for raw in events:
        event = raw if isinstance(raw, Event) else _event(raw)
        if currency is not None and event.currency != currency:
            continue
        if event.impact not in impacts:
            continue
        if not (now <= event.scheduled_at <= horizon_end):
            continue
        selected.append(event)
    return sorted(selected, key=lambda e: e.scheduled_at)


def nothing_scheduled(entity: str) -> Expectation:
    """The expectation for an entity with no in-window Medium or High event.

    `NO READ — nothing scheduled`, never `NEUTRAL`. A quiet week and a week with balanced
    events are different findings, and only one of them is a reason to look at a chart.
    """
    return Expectation(
        entity=entity,
        policy=State.NO_READ,
        directional=State.NO_READ,
        confidence="LOW",
        reason=NOTHING_SCHEDULED,
        resolves_at=None,
        decisive_event_id=None,
        event_ids=(),
    )


def build_expectation(
    entity: str,
    answer: dict[str, Any],
    events: list[Event],
    *,
    prompt: str,
    model: str,
    effort: str | None,
    now: datetime,
) -> Expectation:
    """Turn a parsed stage answer into a stored expectation.

    The decisive event's timestamp is looked up from the events rather than taken from the
    model, so `resolves_at` is always a real scheduled time and never a paraphrase of one.
    """
    by_id = {event.id: event for event in events}
    decisive_id = answer.get("decisive_event_id")
    decisive = by_id.get(decisive_id) if decisive_id else None

    # A named event that is not in the window is not usable as the thing the week turns on.
    # Falling back to the latest in-window event keeps `resolves_at` honest rather than null.
    if decisive is None and events:
        decisive = max(events, key=lambda e: e.scheduled_at)

    return Expectation(
        entity=entity,
        policy=answer["policy"],
        directional=answer["directional"],
        confidence=answer["confidence"],
        reason=answer["reason"],
        resolves_at=decisive.scheduled_at if decisive else None,
        decisive_event_id=decisive.id if decisive else None,
        event_ids=tuple(answer.get("event_ids", ())),
        prompt=prompt,
        model=model,
        effort=effort,
        authored_at=now,
    )


def urgency(resolves_at: datetime | None, now: datetime) -> float:
    """`1 / days_until_resolves`, so a flip on Thursday outranks the same flip next Monday.

    Floored at a quarter of a day rather than allowed to run to infinity: an event forty
    minutes out would otherwise dominate the board by arithmetic alone, and by then the
    window to position has closed anyway.
    """
    if resolves_at is None:
        return 0.0
    days = (resolves_at - now).total_seconds() / 86_400
    return 1.0 / max(days, 0.25)


def rank_one(
    key: str,
    now_policy: State,
    now_directional: State,
    expectation: Expectation | None,
    *,
    at: datetime,
    confidence_weights: dict[str, float],
    reason: str = "",
    detail: Any = None,
) -> Ranked:
    """Rank one row by divergence, weighted by urgency and confidence.

    `max` across the two axes, never a sum and never an average. A full policy flip is
    worth looking at even when the directional axis is asleep, and averaging the two would
    bury it under a quiet axis — which is the one thing the ranking must not do.
    """
    if expectation is None:
        return Ranked(key, 0.0, 0.0, 0.0, None, reason or NOTHING_SCHEDULED, None, detail)

    policy_gap = divergence(now_policy, expectation.policy)
    directional_gap = divergence(now_directional, expectation.directional)
    weight = confidence_weights.get(expectation.confidence, UNKNOWN_CONFIDENCE_WEIGHT)

    rank = max(policy_gap, directional_gap) * urgency(expectation.resolves_at, at) * weight

    moving: Axis | None = None
    if policy_gap or directional_gap:
        moving = Axis.POLICY if policy_gap >= directional_gap else Axis.DIRECTIONAL

    return Ranked(
        key=key,
        rank=rank,
        policy_divergence=policy_gap,
        directional_divergence=directional_gap,
        resolves_at=expectation.resolves_at,
        reason=reason or expectation.reason,
        moving_axis=moving,
        detail=detail,
    )


def order(rows: list[Ranked]) -> list[Ranked]:
    """Descending rank. Ties break on the sooner resolution, then on the name.

    Deterministic ordering matters more than it looks: the interface offers keyboard
    navigation in rank order, and a board that reshuffles between two identical passes
    would move the row under the operator's hands.
    """
    return sorted(
        rows,
        key=lambda r: (
            -r.rank,
            r.resolves_at.timestamp() if r.resolves_at else float("inf"),
            r.key,
        ),
    )


def _event(row: dict[str, Any]) -> Event:
    scheduled = row["scheduled_at"]
    return Event(
        id=str(row["id"]),
        currency=str(row["currency"]),
        title=str(row["title"]),
        scheduled_at=(
            scheduled if isinstance(scheduled, datetime) else datetime.fromisoformat(str(scheduled))
        ),
        impact=str(row["impact"]),
        forecast_text=row.get("forecast_text"),
        previous_text=row.get("previous_text"),
    )
