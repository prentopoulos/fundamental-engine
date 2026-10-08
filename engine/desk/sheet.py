"""The fact sheet the chat stage answers from.

Built from `queries`, so the brief and the MCP server and the board are all describing one
set of values. Like the narrative sheet it carries **no raw headline text** — situation
descriptions, calendar titles and computed reads only — so an answer that cites something
can be checked against the record, and one that invents something is visible as invention.

It is a whole-board sheet rather than a per-entity one, because the operator's question is
usually about a pair and a pair is two entities plus a calendar.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from engine.desk import queries
from engine.universe.config import Params, Universe


def _pair_line(p: dict[str, Any]) -> str:
    return (
        f"  {p['ticker']:<10} now {p['now']:<38} expected {p['expected']:<26}"
        f" -> {p['bias']} ({p['stance']}: {p['reason']}); on {p['driver']}"
    )


def _situation_line(s: dict[str, Any]) -> str:
    return (
        f"  {s['id']:<5} {s['axis']:<11} m={s['magnitude']:<4g} {s['status']:<11}"
        f" {s['description']}"
    )


def brief_sheet(
    connection: Any,
    universe: Universe,
    params: Params,
    now: datetime,
) -> str:
    """Everything the desk knows, as text. The question is appended by the caller.

    Deliberately ends *without* the question so the whole thing is a stable prefix that can
    be prompt-cached across every question asked against one pass — it is ~9,000 tokens and
    was the majority of what a question cost when it was re-sent uncached each time.
    """
    state = queries.status(connection, params, now)
    rows = queries.pairs(connection, universe, params, now)
    entities = queries.entities_by_axis(connection)
    # High and Medium only. The full week runs to ~120 rows and the Low ones are bond
    # auctions and final PMI revisions that no read turns on — a third of the sheet
    # spent on things the expectation stage itself ignores (`IMPACTS` excludes Low).
    events = [
        e
        for e in queries.calendar(connection, days=7, now=now)
        if e["impact"] in params.expectation_impacts
    ]

    seen: dict[str, dict[str, Any]] = {}
    for name in entities:
        for s in queries.situations_for(connection, name):
            seen[s["id"]] = s
    situations = sorted(seen.values(), key=lambda s: (-s["magnitude"], s["id"]))

    calls = []
    for name in sorted(entities):
        record = queries.currency(connection, name)
        if record and record.get("call"):
            calls.append(f"  {name}: {record['call']}")

    forward = []
    for name in sorted(entities):
        record = queries.currency(connection, name)
        if record and record.get("expected"):
            e = record["expected"]
            when = (e["resolves_at"] or "nothing scheduled")[:16]
            forward.append(
                f"  {name:<7} policy {e['policy']:<9} direction {e['direction']:<9}"
                f" confidence {e['confidence']:<7} resolves {when}\n"
                f"          {e['reason']}"
            )

    stale = (
        "  ** THIS READ IS STALE — say so before answering about right now **\n"
        if state.get("stale")
        else ""
    )

    return "\n\n".join(
        (
            f"READ TIME: {state.get('read_at')} (pass {state.get('pass_id')}, "
            f"{state.get('age_minutes')} minutes old; auto is {state.get('cadence')})\n"
            f"{stale}NOW: {now.strftime('%a %d %b %Y %H:%M UTC')}",
            "ENTITY READS (policy / direction, with score and contributing situation count)\n"
            + "\n".join(
                f"  {name:<7} policy {v.get('policy', {}).get('reads', '—'):<20}"
                f" {v.get('policy', {}).get('score', 0):+7.2f}"
                f" ({v.get('policy', {}).get('situations', 0)})"
                f"   direction {v.get('direction', {}).get('reads', '—'):<20}"
                f" {v.get('direction', {}).get('score', 0):+7.2f}"
                f" ({v.get('direction', {}).get('situations', 0)})"
                for name, v in sorted(entities.items())
            ),
            "INSTRUMENTS — now, expected, and the pre-position verdict\n"
            + "\n".join(_pair_line(p) for p in rows),
            "EXPECTED, PER ENTITY\n" + ("\n".join(forward) or "  none produced"),
            f"OPEN SITUATIONS ({len(situations)})\n"
            + "\n".join(_situation_line(s) for s in situations),
            "CALENDAR, NEXT 7 DAYS\n"
            + (
                "\n".join(
                    f"  {e['at'][:16]}  {e['currency']:<4} {e['impact']:<7} {e['title']}"
                    + (
                        f"  (forecast {e['forecast']}, prev {e['previous']})"
                        if e.get("forecast")
                        else ""
                    )
                    for e in events
                )
                or "  nothing scheduled"
            ),
            "THE WRITTEN CALLS\n" + ("\n".join(calls) or "  none"),
        )
    )
