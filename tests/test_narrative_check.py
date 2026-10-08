"""The paragraph is checked against the constraints the arithmetic imposed.

The skill file asks for these properties; these tests are about noticing when a returned
paragraph does not have them. "It usually complies" is not a property.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from engine.narrative.check import MAX_SENTENCES, check
from engine.reading.arithmetic import Contribution, apply_triggers, read
from engine.reading.states import Axis, State
from engine.universe.config import load_params

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
KNOWN = {"S1", "S2", "S9", "S12", "S15", "S19"}


@pytest.fixture(scope="module")
def params():
    return load_params()


def push(magnitude, polarity=1, identifier="S1", description="a story"):
    return Contribution(identifier, description, magnitude, polarity, 1.0, "OPEN")


def reads(params, policy_pushes=(), directional_pushes=(), **triggers):
    policy = read("EUR", Axis.POLICY, policy_pushes, params)
    directional = read("EUR", Axis.DIRECTIONAL, directional_pushes, params)
    apply_triggers(policy, directional, params, **triggers)
    return policy, directional


# --- The length bound --------------------------------------------------------------------


def test_a_paragraph_inside_the_bound_passes(params):
    policy, directional = reads(params, (push(5), push(4, identifier="S2")))
    prose = (
        "EUR reads moderately hawkish on policy (S1, S2). Nothing on the directional axis "
        "has anything on file, so the read rests on rate expectations alone."
    )

    assert check(prose, ("S1", "S2"), policy, directional, KNOWN).ok


def test_a_single_sentence_is_flagged(params):
    """Two sentences is the floor: one is a label, not an explanation."""
    policy, directional = reads(params, (push(5), push(4, identifier="S2")))

    result = check("EUR is moderately hawkish.", (), policy, directional, KNOWN)

    assert not result.ok
    assert "only 1 sentence" in str(result)


def test_a_paragraph_that_runs_away_is_flagged(params):
    policy, directional = reads(params, (push(5), push(4, identifier="S2")))
    prose = " ".join(f"Sentence {index} about the moderately hawkish read." for index in range(8))

    result = check(prose, (), policy, directional, KNOWN)

    assert f"the bound is 2–{MAX_SENTENCES}" in str(result)


# --- Intensity language -------------------------------------------------------------------


@pytest.mark.parametrize("word", ["very", "extremely", "sharply", "significantly"])
def test_an_intensifier_the_arithmetic_did_not_produce_is_flagged(params, word):
    """The intensity language is the arithmetic's, not the writer's."""
    policy, directional = reads(params, (push(5), push(4, identifier="S2")))
    prose = f"EUR is {word} hawkish on policy (S1, S2). The directional axis has nothing on file."

    result = check(prose, ("S1", "S2"), policy, directional, KNOWN)

    assert f"{word!r} is not one of" in str(result)


def test_a_degree_the_arithmetic_did_not_assign_is_flagged(params):
    """Banded `moderately`, written `strongly` — the word overstates the number behind it."""
    policy, directional = reads(params, (push(5), push(4, identifier="S2")))  # +9 -> moderately
    prose = "EUR is strongly hawkish on policy (S1, S2). The directional axis has nothing on file."

    result = check(prose, ("S1", "S2"), policy, directional, KNOWN)

    assert "'strongly' on a stance the arithmetic did not band that way" in str(result)


def test_a_degree_word_used_as_ordinary_english_is_not_flagged(params):
    """The first live pass wrote "slightly lower JOLTS" — a description of data, not a
    claim about a banded read. Flagging that would have the checker crying wolf on every
    other paragraph, which is how a warning stops being read at all."""
    policy, directional = reads(params, (push(5), push(4, identifier="S2")))
    prose = (
        "EUR is moderately hawkish on policy (S1, S2). The slightly lower JOLTS print is a "
        "mild offset only and does not overturn it."
    )

    assert check(prose, ("S1", "S2"), policy, directional, KNOWN).ok


def test_the_assigned_degree_passes(params):
    policy, directional = reads(params, (push(5), push(4, identifier="S2")))
    prose = "EUR is moderately hawkish on policy (S1, S2). Direction has nothing on file."

    assert check(prose, ("S1", "S2"), policy, directional, KNOWN).ok


# --- Citations ----------------------------------------------------------------------------


def test_a_citation_to_a_situation_that_does_not_exist_is_flagged(params):
    policy, directional = reads(params, (push(5), push(4, identifier="S2")))
    prose = "EUR is moderately hawkish (S1, S99). The directional axis has nothing on file."

    result = check(prose, ("S1", "S99"), policy, directional, KNOWN)

    assert "cites S99, which resolves to no situation" in str(result)


def test_prose_naming_an_identifier_the_cited_list_omits_is_flagged(params):
    """The two disagreeing is itself worth catching."""
    policy, directional = reads(params, (push(5), push(4, identifier="S2")))
    prose = "EUR is moderately hawkish (S1, S2). The directional axis has nothing on file."

    result = check(prose, ("S1",), policy, directional, KNOWN)

    assert "omits them from `cited`" in str(result)


# --- Fired tensions must be addressed ------------------------------------------------------


def test_a_near_miss_that_never_states_its_score_is_flagged(params):
    policy, directional = reads(params, (push(5), push(2.2, -1, identifier="S2")))
    prose = "EUR policy is neutral. There is not much to say about it this week."

    result = check(prose, (), policy, directional, KNOWN)

    assert "near miss on POLICY fired but 2.8 is not stated" in str(result)


def test_a_near_miss_that_states_its_score_passes(params):
    policy, directional = reads(params, (push(5), push(2.2, -1, identifier="S2")))
    prose = (
        "EUR policy is neutral at +2.8 (S1, S2). That is close to the 3.5 bar but not enough "
        "to tip the read, and direction has nothing on file."
    )

    assert check(prose, ("S1", "S2"), policy, directional, KNOWN).ok


def test_counter_evidence_that_names_no_minority_situation_is_flagged(params):
    policy, directional = reads(
        params, (push(6), push(5, identifier="S2"), push(3, -1, identifier="S9"))
    )
    prose = "EUR is moderately hawkish on policy. Some things argue the other way."

    result = check(prose, (), policy, directional, KNOWN)

    assert "no minority situation is named" in str(result)


def test_counter_evidence_that_names_the_minority_passes(params):
    policy, directional = reads(
        params, (push(6), push(5, identifier="S2"), push(3, -1, identifier="S9"))
    )
    prose = (
        "EUR is moderately hawkish on policy (S1, S2). Against that, S9 is arguing the other "
        "way, but it is not large enough to change the read."
    )

    assert check(prose, ("S1", "S2", "S9"), policy, directional, KNOWN).ok


def test_axis_conflict_that_names_only_one_axis_is_flagged(params):
    policy, directional = reads(
        params,
        (push(5), push(4, identifier="S2")),
        (push(6, -1, identifier="S12"), push(5, -1, identifier="S15")),
    )
    prose = "EUR reads moderately hawkish. Nothing else is worth noting this week."

    result = check(prose, (), policy, directional, KNOWN)

    assert "both axes are not named" in str(result)


def test_axis_conflict_that_names_both_axes_passes(params):
    policy, directional = reads(
        params,
        (push(5), push(4, identifier="S2")),
        (push(6, -1, identifier="S12"), push(5, -1, identifier="S15")),
    )
    prose = (
        "EUR policy is moderately hawkish at +9.0 (S1, S2), but direction is moderately "
        "bearish at -11.0 (S12, S15) and is the larger of the two. The two axes are in open "
        "conflict, so the pair is the read rather than either word alone."
    )

    assert check(prose, ("S1", "S2", "S12", "S15"), policy, directional, KNOWN).ok


# --- No tension is manufactured -------------------------------------------------------------


def test_a_quiet_read_needs_only_two_dull_sentences(params):
    """If nothing fired, the paragraph is allowed to be boring — and must be allowed to be."""
    policy, directional = reads(params, (push(6), push(5, identifier="S2")))
    prose = "EUR is moderately hawkish on policy (S1, S2). Nothing is arguing against it."

    result = check(prose, ("S1", "S2"), policy, directional, KNOWN)

    assert result.ok
    assert policy.triggers == {}


def test_a_read_with_no_situations_at_all_can_still_be_written(params):
    policy, directional = reads(params)
    prose = "EUR has nothing on file on either axis. There is no read to act on this week."

    assert check(prose, (), policy, directional, KNOWN).ok


def test_divergence_fires_and_is_carried_into_the_check(params):
    policy, directional = reads(
        params,
        (push(6, -1), push(5, -1, identifier="S2")),
        expected_policy=State.POSITIVE,
        resolves_at=NOW + timedelta(hours=48),
    )

    assert "divergence" in policy.triggers
    prose = (
        "EUR policy is moderately dovish at -11.0 (S1, S2). Thursday's decision is expected "
        "to turn it hawkish, so the read flips at that point."
    )
    assert check(prose, ("S1", "S2"), policy, directional, KNOWN).ok
