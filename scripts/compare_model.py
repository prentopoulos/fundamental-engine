"""Replay stored per-entity prompts against another model and diff the paragraphs.

This is what makes the week-two model comparison cost **one pass rather than one week**. Both
per-entity tables store the prompt verbatim — the bytes that were actually sent, never
reconstructed from the situations and events afterwards — so the same question can be put to a
cheaper model without re-ingesting a feed or re-scoring a headline.

The point is to measure the disagreement before spending the quality. `NARRATIVE` writes prose
from a fact sheet Python already computed, which is a writing task rather than a reasoning one,
so it is the safest thing to move to Sonnet. `EXPECTATION` is a genuine judgement and should be
the last. Neither claim is worth acting on without seeing the two answers next to each other.

    python scripts/compare_model.py --model claude-sonnet-5
    python scripts/compare_model.py --model claude-sonnet-5 --stage expectation --entity USD
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.model.client import ModelClient  # noqa: E402
from engine.stages import (  # noqa: E402
    STAGE_MAX_TOKENS,
    load_skill,
    parse_expectation,
    parse_narrative,
)
from engine.store.schema import connect  # noqa: E402
from engine.universe.config import load_params  # noqa: E402

STAGES = {
    "narrative": ("narratives", "prose", parse_narrative, "prose"),
    "expectation": ("expectations", "reason", parse_expectation, "reason"),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", required=True, help="the model to replay against")
    parser.add_argument(
        "--stage", default="narrative", choices=sorted(STAGES), help="which stage to compare"
    )
    parser.add_argument("--entity", help="compare one entity only")
    parser.add_argument(
        "--pass-id", type=int, help="a specific pass; defaults to the most recent complete one"
    )
    parser.add_argument(
        "--effort", help="effort level for the replay; defaults to whatever was stored"
    )
    args = parser.parse_args(argv)

    params = load_params()
    connection = connect(params.database_path)
    try:
        rows = _stored(connection, args)
        if not rows:
            print("nothing stored to compare — run a pass first", file=sys.stderr)
            return 1

        client = ModelClient(cache_dir=params.cache_dir)
        skill = load_skill(args.stage)
        table, column, parse, key = STAGES[args.stage]

        differing = 0
        failed = 0
        for row in rows:
            original = row[column]
            replayed = _replay(client, skill, row, args, parse, key)
            if replayed is None:
                failed += 1
                continue
            if _differs(original, replayed):
                differing += 1
            _report(row, original, replayed, args)

        print(
            f"\n{differing} of {len(rows)} entities differ between "
            f"{rows[0]['model']} and {args.model}."
        )
        if failed:
            print(f"{failed} comparison(s) failed; no equivalence conclusion is available.")
            return 1
        if differing == 0:
            print(
                "No wording difference detected on this pass; this does not establish equivalence."
            )
        return 0
    finally:
        connection.close()


def _stored(connection, args) -> list[dict[str, Any]]:
    table = STAGES[args.stage][0]
    pass_id = args.pass_id
    if pass_id is None:
        row = connection.execute(
            "SELECT id FROM passes WHERE status = 'complete' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return []
        pass_id = row["id"]

    sql = f"SELECT * FROM {table} WHERE pass_id = ?"
    values: tuple[Any, ...] = (pass_id,)
    if args.entity:
        sql += " AND entity = ?"
        values += (args.entity.upper(),)
    return [dict(row) for row in connection.execute(sql + " ORDER BY entity", values)]


def _replay(client, skill: str, row: dict[str, Any], args, parse, key) -> str | None:
    """Send the stored bytes to the named model. Never rebuilds the prompt."""
    try:
        reply = client.complete(
            model=args.model,
            system=skill,
            content=[{"type": "text", "text": row["prompt"]}],
            max_tokens=STAGE_MAX_TOKENS[args.stage],
            note=f"compare:{args.stage}",
            # The stored prompt is the same input, so the replay cache would return the
            # *original* model's answer for it. The salt is what forces a real call to the
            # new model — without it this script would confidently print no disagreement.
            cache_salt=f"compare:{args.model}:{args.effort or row['effort']}",
            cache_system=True,
            effort=args.effort or row["effort"],
        )
    except Exception as error:  # noqa: BLE001 - one bad entity should not stop the sweep
        print(f"  {row['entity']}: replay failed — {error}", file=sys.stderr)
        return None

    if reply.truncated:
        print(f"  {row['entity']}: reply was truncated", file=sys.stderr)
        return None
    try:
        return str(parse(reply.text)[key])
    except Exception as error:  # noqa: BLE001
        print(f"  {row['entity']}: reply did not parse — {error}", file=sys.stderr)
        return None


def _differs(original: str, replayed: str) -> bool:
    """Whether the two answers say meaningfully different things.

    Compared on words rather than characters: a comma moving is not a disagreement worth
    reporting, and a report full of them would hide the ones that are.
    """
    ratio = difflib.SequenceMatcher(None, original.split(), replayed.split()).ratio()
    return ratio < 0.9


def _report(row: dict[str, Any], original: str, replayed: str, args) -> None:
    print(f"\n{'=' * 78}\n{row['entity']}\n{'=' * 78}")
    print(f"\n-- {row['model']} (effort {row['effort'] or 'default'}) --")
    print(_wrap(original))
    print(f"\n-- {args.model} (effort {args.effort or row['effort'] or 'default'}) --")
    print(_wrap(replayed))

    if _differs(original, replayed):
        print("\n-- word diff --")
        for line in difflib.unified_diff(original.split(), replayed.split(), lineterm="", n=2):
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
                print(f"  {line}")


def _wrap(text: str, width: int = 76) -> str:
    import textwrap

    return "\n".join(textwrap.wrap(text, width=width)) or "(empty)"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
