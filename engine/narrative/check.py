"""Checking a returned paragraph against the constraints the arithmetic imposed.

The skill file asks for these properties; this module verifies them. The two are not
redundant — an instruction the model followed on 95% of entities is a defect the operator
should be able to see on the other 5%, and "it usually complies" is not a property.

Violations are returned, never repaired. Rewriting a paragraph to fit the bound would put
this module in the business of authoring, and a silently-edited narrative is worse than a
flagged one: the operator would be reading prose no stage takes responsibility for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

from engine.narrative.factsheet import cited_identifiers, sentence_count
from engine.reading.arithmetic import Read
from engine.reading.states import Degree

MIN_SENTENCES: Final = 2
MAX_SENTENCES: Final = 5

# The only intensity language allowed. Anything stronger is the writer's judgement
# substituting for the arithmetic's, which is exactly what the degree bands exist to stop.
ALLOWED_INTENSIFIERS: Final = frozenset(degree.value for degree in Degree)

# The words a stance is stated in, on either axis. A degree word only makes a claim about
# the read when it is attached to one of these.
STANCE_WORDS: Final = ("hawkish", "dovish", "bullish", "bearish", "neutral", "no read")

_DEGREE_ON_A_STANCE: Final = {
    degree.value: re.compile(rf"\b{degree.value}\s+(?:{'|'.join(STANCE_WORDS)})\b")
    for degree in Degree
}

FORBIDDEN_INTENSIFIERS: Final = (
    "very",
    "extremely",
    "sharply",
    "deeply",
    "massively",
    "hugely",
    "somewhat",
    "mildly",
    "aggressively",
    "profoundly",
    "markedly",
    "significantly",
)


@dataclass
class NarrativeCheck:
    """What a paragraph got wrong, if anything."""

    violations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def __str__(self) -> str:
        return "; ".join(self.violations)


def check(
    prose: str,
    cited: tuple[str, ...],
    policy: Read,
    directional: Read,
    known_identifiers: set[str],
) -> NarrativeCheck:
    """Verify the bound, the intensity language, the citations and the tensions."""
    result = NarrativeCheck()

    count = sentence_count(prose)
    if count < MIN_SENTENCES:
        result.violations.append(
            f"only {count} sentence(s); the bound is {MIN_SENTENCES}–{MAX_SENTENCES}"
        )
    elif count > MAX_SENTENCES:
        result.violations.append(f"{count} sentences; the bound is {MIN_SENTENCES}–{MAX_SENTENCES}")

    lowered = prose.lower()
    for word in FORBIDDEN_INTENSIFIERS:
        if f" {word} " in f" {lowered} ":
            result.violations.append(
                f"intensity word {word!r} is not one of {sorted(ALLOWED_INTENSIFIERS)}"
            )

    result.violations.extend(_degree_mismatches(lowered, policy, directional))
    result.violations.extend(_citation_problems(prose, cited, known_identifiers))
    result.violations.extend(_unaddressed_tensions(lowered, policy, directional))

    return result


def _degree_mismatches(lowered: str, *reads: Read) -> list[str]:
    """A degree word attached to a *stance* the arithmetic banded differently.

    Only counted when the word sits immediately before a stance word — "slightly hawkish",
    "strongly bearish". The rule is about intensity claims on the read, and these three
    words are ordinary English besides: a first live pass wrote "slightly lower JOLTS",
    which is a description of data and not a claim about anything the arithmetic banded.
    Flagging that would have the checker crying wolf on every other paragraph, which is how
    a warning stops being read at all.
    """
    assigned = {read.degree.value for read in reads if read.degree is not None}
    used = {word for word in ALLOWED_INTENSIFIERS if _DEGREE_ON_A_STANCE[word].search(lowered)}

    return [
        f"used degree {word!r} on a stance the arithmetic did not band that way"
        for word in sorted(used - assigned)
    ]


def _citation_problems(prose: str, cited: tuple[str, ...], known: set[str]) -> list[str]:
    """Identifiers the prose names that do not exist, and a `cited` list that disagrees."""
    problems: list[str] = []

    named = cited_identifiers(prose)
    for identifier in named:
        if identifier not in known:
            problems.append(f"cites {identifier}, which resolves to no situation")

    missing = set(named) - set(cited)
    if missing:
        problems.append(f"names {sorted(missing)} in the prose but omits them from `cited`")

    return problems


def _unaddressed_tensions(lowered: str, policy: Read, directional: Read) -> list[str]:
    """Fired triggers the paragraph did not visibly address.

    Deliberately loose. The obligation is that the tension is *discussed*, and prose has
    many ways to do that, so this looks for the concrete anchor each trigger supplies — an
    identifier, a score, a time — rather than for a phrasing. A near miss that never names
    its number has not been addressed however elegantly it was written around.
    """
    problems: list[str] = []

    for read in (policy, directional):
        for name, detail in read.triggers.items():
            if name == "near_miss":
                score = f"{abs(detail['score']):g}"
                if score not in lowered:
                    problems.append(
                        f"near miss on {read.axis.value} fired but {score} is not stated"
                    )
            elif name == "counter_evidence":
                if not any(str(s).lower() in lowered for s in detail["situations"]):
                    problems.append(
                        f"counter-evidence on {read.axis.value} fired but no minority "
                        f"situation is named"
                    )
            elif name == "axis_conflict":
                if "policy" not in lowered or not any(
                    word in lowered for word in ("direction", "bullish", "bearish")
                ):
                    problems.append("axis conflict fired but both axes are not named")

    return problems
