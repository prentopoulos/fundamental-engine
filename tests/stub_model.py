"""A model client that answers from a script, so the end-to-end test never bills a call.

It conforms to the same `complete(...)` signature as the real client and returns the same
`ModelReply` shape, including the token counts and the `replayed` flag — the cost ledger
reads those, and a stub that returned zeros would make the cost assertions vacuous.

Answers are chosen by the stage `note`, which is what the real client already passes for
its cache annotations. Nothing here inspects the prompt to decide what to say: a stub that
pattern-matched on prompt text would drift into re-implementing the stages it stands in for.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from engine.model.client import ModelReply


@dataclass
class StubClient:
    """Answers stage calls from a script and records what it was asked."""

    answers: dict[str, list[str]] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    # Stages named here raise instead of answering, so the failure path can be exercised.
    fail_on: set[str] = field(default_factory=set)
    replay: set[str] = field(default_factory=set)

    def complete(
        self,
        *,
        model: str,
        system: str,
        content: list[dict[str, Any]],
        max_tokens: int = 4096,
        note: str | None = None,
        cache_salt: str | None = None,
        cache_system: bool = False,
        effort: str | None = None,
    ) -> ModelReply:
        stage = note or "unknown"
        prompt = "".join(block.get("text", "") for block in content)
        self.calls.append(
            {
                "stage": stage,
                "model": model,
                "system": system,
                "prompt": prompt,
                # The blocks as sent, not just their joined text: whether a prefix
                # carries `cache_control` is the difference between a question
                # costing 9c and 4c, and it is invisible in the joined string.
                "content": content,
                "effort": effort,
                "cache_salt": cache_salt,
            }
        )

        if stage in self.fail_on:
            raise RuntimeError(f"stubbed failure in {stage}")

        scripted = self.answers.get(stage)
        if not scripted:
            raise AssertionError(f"the stub has no answer scripted for stage {stage!r}")
        text = scripted.pop(0) if len(scripted) > 1 else scripted[0]

        return ModelReply(
            text=text,
            model=model,
            replayed=stage in self.replay,
            input_tokens=len(prompt) // 4,
            output_tokens=len(text) // 4,
            stop_reason="end_turn",
        )

    def prompts_for(self, stage: str) -> list[str]:
        return [call["prompt"] for call in self.calls if call["stage"] == stage]

    def count(self, stage: str) -> int:
        return sum(1 for call in self.calls if call["stage"] == stage)


def triage_keeping(*indices: int) -> str:
    return json.dumps({"keep": list(indices)})


def score_touching(
    *entities: tuple[str, int],
    axis: str = "POLICY",
    magnitude: int = 6,
    description: str = "a scored development",
) -> str:
    return json.dumps(
        {
            "touches": [
                {
                    "entities": [
                        {"entity": name, "polarity": "POSITIVE" if sign > 0 else "NEGATIVE"}
                        for name, sign in entities
                    ],
                    "axis": axis,
                    "magnitude": magnitude,
                    "description": description,
                }
            ]
        }
    )


def situations_opening(*specs: tuple[str, str, int, tuple[tuple[str, int], ...]]) -> str:
    return json.dumps(
        {
            "updates": [
                {
                    "action": "open",
                    "description": description,
                    "axis": axis,
                    "magnitude": magnitude,
                    "entities": [
                        {"entity": name, "polarity": "POSITIVE" if sign > 0 else "NEGATIVE"}
                        for name, sign in entities
                    ],
                }
                for description, axis, magnitude, entities in specs
            ]
        }
    )


def expectation_of(
    policy: str = "POSITIVE",
    directional: str = "NO_READ",
    confidence: str = "MEDIUM",
    reason: str = "The week turns on the scheduled decision.",
) -> str:
    return json.dumps(
        {
            "policy": policy,
            "directional": directional,
            "confidence": confidence,
            "decisive_event_id": None,
            "reason": reason,
            "event_ids": [],
        }
    )


def narrative_of(prose: str, cited: list[str] | None = None) -> str:
    return json.dumps({"prose": prose, "cited": cited or []})
