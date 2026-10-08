"""The commissioning check: is this pass trustworthy, and are the bands in the right place?

Run against the first live pass, before the loop is enabled. It answers three questions the
design says have to be answered by hand before this reader is believed:

1. **Was situation adherence 100%?** Anything less means the model re-described a story
   instead of naming it, which silently double-counts it and inflates a score.
2. **Do every narrative's citations resolve?** A paragraph naming a record that does not exist
   is a stage that has started inventing, and it is invisible unless someone looks.
3. **Does any entity read NEUTRAL with zero situations?** That is the exact conflation the
   four-state vocabulary exists to prevent, and it should be impossible.

It then prints the score distribution across both axes, which is what the threshold and the
degree bands should be re-tuned against after a week. `READ_THRESHOLD` was set from real data
scored on *one* axis; splitting across two will lower typical scores, so the first week is
calibration rather than signal.

    python scripts/inspect_pass.py
    python scripts/inspect_pass.py --pass-id 3
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.narrative.audit import invented_citations  # noqa: E402
from engine.store.schema import connect  # noqa: E402
from engine.universe.config import load_params  # noqa: E402

OK = "  ok"
BAD = "  DEFECT"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pass-id", type=int, help="defaults to the most recent complete pass")
    parser.add_argument(
        "--all-passes",
        action="store_true",
        help="draw the score distribution from every stored pass, not just this one",
    )
    args = parser.parse_args(argv)

    params = load_params()
    connection = connect(params.database_path)
    try:
        record = _pass(connection, args.pass_id)
        if record is None:
            print("no completed pass stored — run `python -m engine` first", file=sys.stderr)
            return 1

        print(f"pass {record['id']} · {record['trigger']} · {record['started_at']}")
        print(f"cost ${record['cost_usd']:.4f}\n")

        defects = 0
        defects += _check_adherence(record)
        defects += _check_citations(connection, record["id"])
        defects += _check_no_read(connection, record["id"])
        _warnings(record)

        _distribution(connection, None if args.all_passes else record["id"], params)
        _stage_costs(record)

        print()
        if defects:
            print(f"{defects} defect(s) found. Do not enable the loop until they are explained.")
            return 1
        mode = "enabled" if params.scheduler_enabled else "disabled"
        print(f"No integrity defects. The scheduler is {mode}; inspect the explanations too.")
        return 0
    finally:
        connection.close()


def _pass(connection, pass_id: int | None):
    if pass_id is not None:
        row = connection.execute("SELECT * FROM passes WHERE id = ?", (pass_id,)).fetchone()
    else:
        row = connection.execute(
            "SELECT * FROM passes WHERE status = 'complete' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def _check_adherence(record) -> int:
    print("Situation adherence")
    adherence = record["adherence"]
    if adherence is None:
        print("  — no situation update ran on this pass (nothing new to score)")
        return 0
    if adherence >= 1.0:
        print(f"{OK}: 100% of returned identifiers resolved")
        return 0
    print(
        f"{BAD}: {adherence:.0%}. The stage named situations that do not exist, which means "
        f"it is re-describing stories rather than selecting them."
    )
    return 1


def _check_citations(connection, pass_id: int) -> int:
    print("\nNarrative citations")
    bad = invented_citations(connection, pass_id)
    if not bad:
        total = connection.execute(
            "SELECT count(*) FROM narratives WHERE pass_id = ?", (pass_id,)
        ).fetchone()[0]
        print(f"{OK}: every citation across {total} narratives resolves to a stored situation")
        return 0
    for citation in bad:
        print(f"{BAD}: {citation.entity} cites {citation.identifier}, which does not exist")
    return len(bad)


def _check_no_read(connection, pass_id: int) -> int:
    """NEUTRAL with zero contributors is the conflation the four states exist to prevent."""
    print("\nNO READ integrity")
    offenders = connection.execute(
        "SELECT entity, axis, score FROM reads"
        " WHERE pass_id = ? AND state = 'NEUTRAL' AND contributor_count = 0",
        (pass_id,),
    ).fetchall()
    if not offenders:
        counts = dict(
            connection.execute(
                "SELECT state, count(*) FROM reads WHERE pass_id = ? GROUP BY state", (pass_id,)
            )
        )
        print(f"{OK}: no entity reads NEUTRAL with zero situations")
        print(f"       states this pass: {counts}")
        return 0
    for row in offenders:
        print(f"{BAD}: {row['entity']} {row['axis']} reads NEUTRAL with no situations")
    return len(offenders)


def _warnings(record) -> None:
    warnings = json.loads(record["warnings"] or "[]")
    rejections = json.loads(record["rejections"] or "[]")
    if warnings or rejections:
        print("\nWarnings recorded on this pass")
        for warning in warnings:
            print(f"  ! {warning}")
        for rejection in rejections:
            print(f"  ! calendar row rejected: {rejection}")


def _distribution(connection, pass_id: int | None, params) -> None:
    """The numbers the threshold and the bands should be argued with after a week."""
    print("\nScore distribution")
    sql = "SELECT axis, score, state, contributor_count FROM reads"
    values: tuple = ()
    if pass_id is not None:
        sql += " WHERE pass_id = ?"
        values = (pass_id,)
    rows = [dict(row) for row in connection.execute(sql, values)]
    if not rows:
        print("  nothing stored")
        return

    for axis in ("POLICY", "DIRECTIONAL"):
        scores = [abs(row["score"]) for row in rows if row["axis"] == axis]
        states = [row["state"] for row in rows if row["axis"] == axis]
        if not scores:
            continue
        readable = [s for s in scores if s > 0]
        print(f"\n  {axis}  ({len(scores)} reads)")
        print(
            f"    |score|  min {min(scores):.1f}  median {statistics.median(scores):.1f}"
            f"  max {max(scores):.1f}"
        )
        if readable:
            print(f"             median of non-zero: {statistics.median(readable):.1f}")
        for state in ("POSITIVE", "NEGATIVE", "NEUTRAL", "NO_READ"):
            count = states.count(state)
            if count:
                print(f"    {state:<9} {count:>3}  {'#' * count}")

        above = sum(1 for s in scores if s >= params.read_threshold)
        print(f"    at or above READ_THRESHOLD ({params.read_threshold}): {above}/{len(scores)}")
        for name, floor in params.degree_bands:
            print(f"    at or above {name} ({floor}): {sum(1 for s in scores if s >= floor)}")

    unreadable = sum(1 for row in rows if row["state"] == "NO_READ")
    share = unreadable / len(rows)
    print(f"\n  {share:.0%} of axes read NO READ.")
    if share > 0.7:
        print("    Most of the board is unreadable. After a week of passes, consider lowering")
        print("    READ_THRESHOLD — but do not weaken the breadth rule, which is what stops")
        print("    single-headline directions.")
    elif share < 0.15:
        print("    Almost everything is readable, which is suspicious this early. Check that")
        print("    the breadth rule is actually firing before trusting the directional reads.")
    else:
        print("    That is a plausible spread for a first pass. Re-band after a week, not now.")


def _stage_costs(record) -> None:
    costs = json.loads(record["stage_costs"] or "{}")
    if not costs:
        return
    print("\nCost by stage")
    for stage, detail in costs.items():
        print(
            f"  {stage:<12} ${detail['usd']:.4f}  "
            f"{detail['billed_calls']} billed / {detail['replayed_calls']} replayed  "
            f"{detail['input_tokens']} in / {detail['output_tokens']} out"
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
