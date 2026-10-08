"""A free, offline tour of the real interface using clearly fictional evidence.

The demo writes to a temporary database and uses the same arithmetic and views as
live mode. It never builds a model client and cannot start a refresh or chat call.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from engine.expectation.forward import Expectation
from engine.passes import PassResult, _compute_reads, _store, start_pass
from engine.reading.arithmetic import apply_triggers
from engine.reading.states import Axis, State
from engine.situations.lifecycle import open_situation
from engine.store.schema import connect, transaction
from engine.universe.config import Params, Universe, load_params, load_universe
from engine.web.app import create_app

# These are invented scenarios, not historical releases or recorded model responses.
SCENARIOS = (
    ("Fictional Fed guidance favours tighter policy", Axis.POLICY, 6, (("USD", 1),)),
    ("Fictional inflation surprise supports higher rates", Axis.POLICY, 5, (("USD", 1),)),
    ("Fictional euro-area growth slowdown", Axis.DIRECTIONAL, 6, (("EUR", -1),)),
    ("Fictional decline in euro-area investment", Axis.DIRECTIONAL, 4, (("EUR", -1),)),
    (
        "Fictional flight to safe assets",
        Axis.DIRECTIONAL,
        8,
        (("JPY", 1), ("XAU", 1), ("SPX500", -1), ("NAS100", -1)),
    ),
    ("Fictional oil supply disruption", Axis.DIRECTIONAL, 7, (("WTI", 1), ("CAD", 1))),
)


def seed_demo(connection, universe: Universe, params: Params, now: datetime) -> int:
    """Build one reproducible board through the production scoring and storage code."""
    pass_id = start_pass(connection, "manual", now)
    with transaction(connection):
        for description, axis, magnitude, entities in SCENARIOS:
            open_situation(
                connection,
                description=description,
                axis=axis,
                magnitude=magnitude,
                entities=entities,
                now=now,
            )
        connection.execute(
            "INSERT INTO calendar_events (id, currency, title, scheduled_at, impact, "
            "forecast_text, previous_text, ingested_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "demo-usd-decision",
                "USD",
                "Fictional policy decision",
                (now + timedelta(days=2)).isoformat(),
                "High",
                "unchanged",
                "unchanged",
                now.isoformat(),
            ),
        )

    result = PassResult(pass_id=pass_id, trigger="manual", started_at=now)
    _compute_reads(connection, universe, params, now, result)
    for entity, per_axis in result.reads.items():
        policy, direction = per_axis[Axis.POLICY], per_axis[Axis.DIRECTIONAL]
        scheduled = entity == "USD"
        forward = Expectation(
            entity=entity,
            policy=State.NEGATIVE if scheduled else State.NO_READ,
            directional=State.NO_READ,
            confidence="MEDIUM",
            reason="Fictional guidance assumes a dovish shift."
            if scheduled
            else "nothing scheduled",
            resolves_at=now + timedelta(days=2) if scheduled else None,
            decisive_event_id="demo-usd-decision" if scheduled else None,
            event_ids=("demo-usd-decision",) if scheduled else (),
            model="offline-demo",
            authored_at=now,
        )
        result.expectations[entity] = forward
        apply_triggers(
            policy,
            direction,
            params,
            expected_policy=forward.policy,
            expected_directional=forward.directional,
            resolves_at=forward.resolves_at,
        )
        cited = list(dict.fromkeys(policy.contributor_ids + direction.contributor_ids))
        citations = " (" + ", ".join(cited) + ")" if cited else ""
        prose = (
            f"Fictional evidence leaves {entity} {policy.words} on policy and "
            f"{direction.words} on direction{citations}. "
            "This is an offline illustration of the scoring rules."
        )
        result.narrative_rows[entity] = {
            "prose": prose,
            "cited": cited,
            "prompt": "offline-demo",
            "model": "offline-demo",
            "effort": None,
            "violations": [],
        }
    _store(connection, result, now)
    connection.execute(
        "UPDATE passes SET status = 'complete', finished_at = ? WHERE id = ?",
        (now.isoformat(), pass_id),
    )
    return pass_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Open the free offline demo.")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    import uvicorn

    params = replace(load_params(), scheduler_enabled=False)
    universe = load_universe()
    # A temporary file lets each request open its own connection without sharing SQLite
    # connections across threads. Closing the demo removes only this disposable file.
    with TemporaryDirectory(prefix="fundamental-engine-demo-") as folder:
        demo_path = Path(folder) / "demo.db"
        connection = connect(demo_path)
        try:
            seed_demo(connection, universe, params, datetime.now(UTC))
        finally:
            connection.close()
        app = create_app(
            params=params,
            universe=universe,
            read_only=True,
            connection_factory=lambda: connect(demo_path),
        )
        print(f"Offline demo: http://127.0.0.1:{args.port} — Ctrl+C to stop.")
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
