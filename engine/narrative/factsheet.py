"""The fact sheets the two per-entity stages are given, and nothing else.

The narrative stage receives a structured sheet — the two scores, states and degrees, the
contributing situations with identifiers and magnitudes, the in-window events, the expected
stance, and the fired triggers — and **never the headline text**.

That is the whole enforcement mechanism for the tracing rule (`design.md` §4). A stage that
cannot see a headline cannot cite one, so "every claim traces to a record" is true by
construction rather than by inspection. The alternative — handing it the headlines so the
prose is richer — was rejected because a paragraph quoting a headline no situation was
opened for is a claim with nothing behind it, and the point of storing the cited ids is that
a spot check can catch invention.

The cost is that prose quality is bounded by situation-description quality. That is
acceptable: descriptions are one-line summaries the scoring stage writes for exactly this
purpose.
"""

from __future__ import annotations

import re
from datetime import datetime

from engine.expectation.forward import Event, Expectation
from engine.reading.arithmetic import (
    AXIS_CONFLICT,
    COUNTER_EVIDENCE,
    DIVERGENCE,
    NEAR_MISS,
    Read,
)
from engine.reading.states import Axis, State, say

# What each trigger obliges the paragraph to say. Written into the sheet so the obligation
# travels with the fact rather than living only in the skill file.
TRIGGER_OBLIGATIONS = {
    NEAR_MISS: "state what the score was and that it was not enough to change the read",
    AXIS_CONFLICT: (
        "name both axes, say which is larger, and say which one you think wins — with why, "
        "and with what would change your mind"
    ),
    COUNTER_EVIDENCE: "name what is arguing the other way, by identifier",
    DIVERGENCE: (
        "name the resolving event and its time, say what the read becomes, and say whether "
        "you think it actually will — the operator is deciding whether to hold through it"
    ),
}

_SITUATION_REFERENCE = re.compile(r"\bS\d+\b")


def axis_block(read: Read) -> str:
    """One axis, as the sheet states it: words, score, count, and why if unreadable."""
    header = f"{read.axis.value}: {say(read.axis, read.state, read.degree)}"
    if read.state is State.NO_READ:
        return f"{header} — {read.no_read_reason}"

    lines = [f"{header} (score {read.score:+.1f}, {len(read.contributors)} contributing)"]
    for contribution in read.majority_first:
        sign = "+" if contribution.polarity > 0 else "−"
        lines.append(
            f"  {contribution.identifier} · magnitude {contribution.magnitude:g} {sign} ·"
            f" weight {contribution.recency_weight:.2f} · {contribution.status}\n"
            f"      {contribution.description}"
        )
    return "\n".join(lines)


def trigger_block(policy: Read, directional: Read) -> str:
    """The tensions the arithmetic found, with the values behind them.

    When nothing fired, the sheet says so explicitly rather than omitting the section. An
    absent section reads as an oversight and invites the stage to look for a tension to
    fill it; an explicit "none" is an instruction to be brief.
    """
    fired: list[str] = []
    for read in (policy, directional):
        for name, detail in read.triggers.items():
            obligation = TRIGGER_OBLIGATIONS.get(name, "")
            fired.append(f"- {name} on {read.axis.value}: {detail}\n  You must {obligation}.")

    if not fired:
        return (
            "TENSIONS: none fired. Nothing is arguing with this read. Be brief and do not "
            "manufacture a tension."
        )
    return "TENSIONS — address every one of these:\n" + "\n".join(fired)


def expectation_block(expectation: Expectation | None) -> str:
    if expectation is None:
        return "EXPECTED: not yet produced for this entity."
    resolves = (
        expectation.resolves_at.strftime("%a %d %b %H:%M UTC") if expectation.resolves_at else "—"
    )
    return (
        f"EXPECTED\n"
        f"  policy: {say(Axis.POLICY, expectation.policy)}\n"
        f"  directional: {say(Axis.DIRECTIONAL, expectation.directional)}\n"
        f"  confidence: {expectation.confidence}\n"
        f"  resolves: {resolves}\n"
        f"  reason: {expectation.reason}"
    )


def events_block(events: list[Event]) -> str:
    if not events:
        return "IN-WINDOW EVENTS: none scheduled."
    return "IN-WINDOW EVENTS (Medium and High, next 7 days):\n" + "\n".join(
        f"  {event.line()}" for event in events
    )


def narrative_sheet(
    entity: str,
    policy: Read,
    directional: Read,
    events: list[Event],
    expectation: Expectation | None,
) -> str:
    """The complete narrative fact sheet. Contains no raw feed text by construction.

    Every string that reaches this function comes from a stored record: a situation
    description written by the scoring stage, a calendar event title, a state word from the
    arithmetic. The headline bodies are never passed in and there is no parameter to pass
    them in through.
    """
    return "\n\n".join(
        (
            f"ENTITY: {entity}",
            "CURRENT READ\n" + axis_block(policy) + "\n" + axis_block(directional),
            events_block(events),
            expectation_block(expectation),
            trigger_block(policy, directional),
            "Write the breakdown. Two to five sentences. Cite situations by identifier.",
        )
    )


def expectation_sheet(
    entity: str,
    policy: Read,
    directional: Read,
    events: list[Event],
    now: datetime,
) -> str:
    """The forward stage's sheet: current situations as context, the week's events as input.

    The current read is included so the expectation is a statement about *change* rather
    than a fresh guess — but it is context, not the answer, and the skill says so.
    """
    return "\n\n".join(
        (
            f"ENTITY: {entity}",
            f"NOW: {now.strftime('%a %d %b %H:%M UTC')}",
            "CURRENT READ\n" + axis_block(policy) + "\n" + axis_block(directional),
            events_block(events),
            "Give the expected stance for the week ahead on each axis.",
        )
    )


def cited_identifiers(prose: str) -> tuple[str, ...]:
    """Every `S`-number the prose actually names, in order of first appearance.

    Read from the prose rather than trusted from the `cited` field: the two disagreeing is
    itself worth catching, and the stored list must reflect what the paragraph claims.
    """
    seen: list[str] = []
    for match in _SITUATION_REFERENCE.findall(prose):
        if match not in seen:
            seen.append(match)
    return tuple(seen)


def sentence_count(prose: str) -> int:
    """A count good enough to enforce a two-to-five bound.

    Deliberately crude — abbreviations and decimals will occasionally miscount by one — and
    used only to flag a paragraph that has clearly run away or stopped short, never to
    reject one that is within a sentence of the bound.
    """
    return len([part for part in re.split(r"[.!?]+(?:\s|$)", prose.strip()) if part.strip()])
