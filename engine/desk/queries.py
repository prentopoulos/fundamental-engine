"""Structured reads of the current board, for whatever wants to ask it questions.

Both the MCP server and the in-board chat answer from here, so the two can never drift into
describing the board differently — the failure this project keeps finding is two code paths
that each look right and disagree.

Everything is read-only and cheap: no model call, no network, no pass. A caller that wants
prose asks a model with `sheet.py`; a caller that wants values calls these.

`instrument_rows` is imported from the web layer on purpose rather than reimplemented. It
owns the beta resolution, the `NO_READ` propagation and the bias verdict, and a second copy
of that logic is precisely how the board and the brief would start disagreeing about which
way a pair leans.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from engine.passes import latest_pass
from engine.reading.states import Axis
from engine.universe.config import Params, Universe
from engine.web.views import instrument_rows

_WORDS = {
    ("POLICY", "POSITIVE"): "hawkish",
    ("POLICY", "NEGATIVE"): "dovish",
    ("DIRECTIONAL", "POSITIVE"): "bullish",
    ("DIRECTIONAL", "NEGATIVE"): "bearish",
}


def _say(axis: str, state: str, degree: str | None) -> str:
    if state == "NO_READ":
        return "no read"
    if state == "NEUTRAL":
        return "neutral"
    word = _WORDS.get((axis, state), state.lower())
    return f"{degree} {word}" if degree else word


def status(connection: Any, params: Params, now: datetime | None = None) -> dict[str, Any]:
    """When the board was last read, whether it is stale, and what it has cost."""
    moment = now or datetime.now(UTC)
    recent = latest_pass(connection)
    spent = connection.execute("SELECT sum(cost_usd) s FROM passes").fetchone()["s"] or 0.0

    if recent is None:
        return {
            "read_at": None,
            "stale": True,
            "note": "no pass has completed yet",
            "total_spent_usd": round(spent, 2),
        }

    read_at = recent["finished_at"] or recent["started_at"]
    age = moment - datetime.fromisoformat(read_at)
    return {
        "read_at": read_at,
        "pass_id": recent["id"],
        "trigger": recent["trigger"],
        "age_minutes": round(age.total_seconds() / 60),
        "stale": age > params.stale_after,
        "cadence": params.cadence_words,
        "pass_cost_usd": round(recent["cost_usd"], 4),
        "total_spent_usd": round(spent, 2),
    }


def pairs(
    connection: Any, universe: Universe, params: Params, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Every instrument: both lanes, the pre-position verdict, and what resolves it."""
    moment = now or datetime.now(UTC)
    recent = latest_pass(connection)
    if recent is None:
        return []

    out = []
    for row in instrument_rows(connection, universe, params, recent["id"], moment):
        verdict = row.bias
        out.append(
            {
                "ticker": row.ticker,
                "group": row.group,
                "now": row.pair_words,
                "expected": row.expected_words,
                "bias": verdict.word if verdict else "no bias",
                "stance": verdict.stance if verdict else "none",
                "reason": verdict.because if verdict else "not enough to lean on",
                "driver": row.driver or "nothing scheduled",
                "resolves": row.resolves_words,
                "legs": list(row.legs),
                "crosses_zero": row.crosses_zero,
            }
        )
    return out


def pair(
    connection: Any,
    universe: Universe,
    params: Params,
    ticker: str,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """One instrument, with each leg's own read attached."""
    wanted = ticker.strip().upper().replace("/", "")
    found = next(
        (p for p in pairs(connection, universe, params, now) if p["ticker"] == wanted), None
    )
    if found is None:
        return None
    found["leg_reads"] = {leg: currency(connection, leg) for leg in found["legs"]}
    return found


def currency(connection: Any, entity: str) -> dict[str, Any] | None:
    """One entity's two axes, the situations behind each, and the written call."""
    name = entity.strip().upper()
    recent = latest_pass(connection)
    if recent is None:
        return None

    axes: dict[str, Any] = {}
    for row in connection.execute(
        "SELECT axis, state, score, degree, no_read_reason, contributor_count "
        "FROM reads WHERE entity = ? AND pass_id = ?",
        (name, recent["id"]),
    ):
        key = "policy" if row["axis"] == Axis.POLICY.value else "direction"
        axes[key] = {
            "reads": _say(row["axis"], row["state"], row["degree"]),
            "state": row["state"],
            "score": round(row["score"], 2),
            "situations": row["contributor_count"],
            "why_no_read": row["no_read_reason"],
        }
    if not axes:
        return None

    call = connection.execute(
        "SELECT prose FROM narratives WHERE entity = ? ORDER BY pass_id DESC LIMIT 1", (name,)
    ).fetchone()
    forward = connection.execute(
        "SELECT policy_state, directional_state, confidence, resolves_at, reason "
        "FROM expectations WHERE entity = ? ORDER BY pass_id DESC LIMIT 1",
        (name,),
    ).fetchone()

    return {
        "entity": name,
        **axes,
        "expected": {
            "policy": forward["policy_state"],
            "direction": forward["directional_state"],
            "confidence": forward["confidence"],
            "resolves_at": forward["resolves_at"],
            "reason": forward["reason"],
        }
        if forward
        else None,
        "call": call["prose"] if call else None,
        "open_situations": situations_for(connection, name),
    }


def situations_for(connection: Any, entity: str) -> list[dict[str, Any]]:
    """Every open situation touching one entity, heaviest first."""
    return [
        {
            "id": r["identifier"],
            "axis": r["axis"],
            "magnitude": r["magnitude"],
            "polarity": "positive" if r["polarity"] > 0 else "negative",
            "status": r["status"],
            "description": r["description"],
            "last_evidence": r["last_evidence_at"],
        }
        for r in connection.execute(
            "SELECT s.identifier, s.axis, s.magnitude, s.status, s.description, "
            "       s.last_evidence_at, e.polarity "
            "FROM situations s JOIN situation_entities e ON e.situation_id = s.identifier "
            "WHERE e.entity = ? AND s.resolved_at IS NULL "
            "ORDER BY s.magnitude DESC, s.identifier",
            (entity.strip().upper(),),
        )
    ]


def situation(connection: Any, identifier: str) -> dict[str, Any] | None:
    """One situation in full, with every entity it touches."""
    name = identifier.strip().upper()
    row = connection.execute("SELECT * FROM situations WHERE identifier = ?", (name,)).fetchone()
    if row is None:
        return None
    touches = [
        {"entity": r["entity"], "polarity": "positive" if r["polarity"] > 0 else "negative"}
        for r in connection.execute(
            "SELECT entity, polarity FROM situation_entities WHERE situation_id = ?", (name,)
        )
    ]
    return {
        "id": row["identifier"],
        "axis": row["axis"],
        "magnitude": row["magnitude"],
        "status": row["status"],
        "description": row["description"],
        "opened_at": row["opened_at"],
        "last_evidence": row["last_evidence_at"],
        "resolved_at": row["resolved_at"],
        "touches": touches,
    }


def search_situations(connection: Any, text: str, limit: int = 20) -> list[dict[str, Any]]:
    """Open situations whose description matches a phrase. Plain substring, case-insensitive."""
    needle = f"%{text.strip().lower()}%"
    return [
        {
            "id": r["identifier"],
            "axis": r["axis"],
            "magnitude": r["magnitude"],
            "status": r["status"],
            "description": r["description"],
        }
        for r in connection.execute(
            "SELECT identifier, axis, magnitude, status, description FROM situations "
            "WHERE resolved_at IS NULL AND lower(description) LIKE ? "
            "ORDER BY magnitude DESC LIMIT ?",
            (needle, limit),
        )
    ]


def calendar(
    connection: Any,
    currency: str | None = None,
    days: int = 7,
    now: datetime | None = None,
    impact: str | None = None,
) -> list[dict[str, Any]]:
    """Scheduled events ahead, optionally for one currency or one impact level."""
    moment = now or datetime.now(UTC)
    sql = [
        "SELECT currency, title, scheduled_at, impact, forecast_text, previous_text "
        "FROM calendar_events WHERE scheduled_at >= ? AND scheduled_at <= ?"
    ]
    args: list[Any] = [moment.isoformat(), (moment + timedelta(days=days)).isoformat()]
    if currency:
        sql.append("AND currency = ?")
        args.append(currency.strip().upper())
    if impact:
        sql.append("AND impact = ?")
        args.append(impact.strip().title())
    sql.append("ORDER BY scheduled_at")

    return [
        {
            "currency": r["currency"],
            "title": r["title"],
            "at": r["scheduled_at"],
            "impact": r["impact"],
            "forecast": r["forecast_text"],
            "previous": r["previous_text"],
        }
        for r in connection.execute(" ".join(sql), args)
    ]


def entities_by_axis(connection: Any) -> dict[str, Any]:
    """Every entity's two axes at a glance — the cheapest whole-board answer."""
    recent = latest_pass(connection)
    if recent is None:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for r in connection.execute(
        "SELECT entity, axis, state, score, degree, contributor_count "
        "FROM reads WHERE pass_id = ? ORDER BY entity",
        (recent["id"],),
    ):
        key = "policy" if r["axis"] == Axis.POLICY.value else "direction"
        out.setdefault(r["entity"], {})[key] = {
            "reads": _say(r["axis"], r["state"], r["degree"]),
            "score": round(r["score"], 2),
            "situations": r["contributor_count"],
        }
    return out
