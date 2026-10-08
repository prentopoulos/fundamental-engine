"""`python -m engine` — run one pass by hand.

The plan names this `python -m engine.pass`; `pass` is a Python keyword, so a module by
that name cannot be imported and the command cannot exist. `python -m engine` is the same
command with a legal name, and `--poll` selects the cheap variant.

Nothing here starts a scheduler. The loop ships disabled and enabling it is an explicit
operator step, taken only after one manual pass has been read by hand.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime

from engine.model.client import ModelClient
from engine.passes import PassFailed, run
from engine.reading.states import Axis
from engine.stages import Stages
from engine.store.schema import connect
from engine.universe.config import load_params, load_universe


def main(argv: list[str] | None = None) -> int:
    # The Windows console defaults to cp1252, and both the fact sheets and the prose carry
    # characters it cannot encode — the Unicode minus in a score, the middle dot between
    # fields. Without this the pass completes, stores everything correctly, and then dies
    # printing its own summary, which reads as a failed pass when it was a finished one.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        prog="python -m engine", description="Run one fundamental read."
    )
    parser.add_argument(
        "--poll",
        action="store_true",
        help="the cheap variant: ingest, triage and score only, no per-entity stages",
    )
    parser.add_argument(
        "--forced",
        action="store_true",
        help="re-decide every entity and bypass the replay cache; always costs full price",
    )
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args(argv)

    params = load_params()
    universe = load_universe()
    # The guard runs inside `connect`, before any file is opened.
    connection = connect(params.database_path)

    stages = Stages(
        client=ModelClient(cache_dir=params.cache_dir),
        models=params.models,
        prices=params.prices,
    )

    try:
        result = run(
            connection,
            universe,
            params,
            stages,
            trigger="forced" if args.forced else "manual",
            now=datetime.now(UTC),
            poll_only=args.poll,
            forced=args.forced,
        )
    except PassFailed as failure:
        print(f"pass failed at stage {failure.stage}: {failure.cause}", file=sys.stderr)
        return 1
    finally:
        connection.close()

    if args.json:
        print(json.dumps(_summary(result), indent=2, default=str))
    else:
        _report(result, params, universe)
    return 0


def _summary(result) -> dict:
    return {
        "pass_id": result.pass_id,
        "trigger": result.trigger,
        "status": result.status,
        "adherence": result.adherence,
        "cost_usd": round(result.cost_usd, 4),
        "stage_costs": result.stage_costs,
        "warnings": result.warnings,
        "entities": {
            entity: {
                axis.value: {
                    "state": value.state.value,
                    "degree": value.degree.value if value.degree else None,
                    "score": round(value.score, 2),
                    "contributors": list(value.contributor_ids),
                    "triggers": sorted(value.triggers),
                }
                for axis, value in per_axis.items()
            }
            for entity, per_axis in result.reads.items()
        },
    }


def _report(result, params, universe) -> None:
    """A readable summary, for the manual pass the operator inspects before enabling the loop."""
    print(f"pass {result.pass_id} · {result.trigger} · {result.status}")
    if result.ingestion:
        print(
            f"  ingested {len(result.ingestion.new_headlines)} new headlines"
            f" ({result.ingestion.duplicate_headlines} already seen),"
            f" {len(result.ingestion.in_window_events)} in-window events"
        )
    if result.adherence is not None:
        print(f"  situation adherence: {result.adherence:.0%}")
    print(f"  cost: ${result.cost_usd:.4f}")

    for warning in result.warnings:
        print(f"  ! {warning}")

    if not result.reads:
        return

    print()
    for entity in sorted(result.reads):
        policy = result.reads[entity][Axis.POLICY]
        directional = result.reads[entity][Axis.DIRECTIONAL]
        print(f"{entity:<8} {policy.words:<22} {directional.words}")
        narrative = result.narratives.get(entity)
        if narrative:
            print(f"         {narrative}")
        print()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
