"""The four-state machine, the breadth rule, the bands, and the four triggers.

Every scenario the stance-reading spec names appears here, plus the guarantee the whole
vocabulary rests on: `NO READ` is never stored, returned or rendered as `NEUTRAL`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from engine.reading.arithmetic import (
    AXIS_CONFLICT,
    COUNTER_EVIDENCE,
    DIVERGENCE,
    NEAR_MISS,
    NOTHING_ON_FILE,
    TOO_THIN,
    Contribution,
    apply_triggers,
    band,
    contributions,
    passes_breadth,
    read,
    recency_weight,
    score_of,
)
from engine.reading.states import Axis, Degree, State
from engine.universe.config import load_params

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def params():
    return load_params()


def push(
    magnitude: float, polarity: int = 1, weight: float = 1.0, identifier="S1", description="a story"
) -> Contribution:
    return Contribution(
        identifier=identifier,
        description=description,
        magnitude=magnitude,
        polarity=polarity,
        recency_weight=weight,
        status="OPEN",
    )


# --- Recency decay ---------------------------------------------------------------------


def test_a_situation_exactly_one_half_life_old_contributes_half_its_magnitude(params):
    last = NOW - params.half_life(Axis.DIRECTIONAL)

    weight = recency_weight(last, NOW, params.half_life(Axis.DIRECTIONAL))

    assert weight == pytest.approx(0.5)
    assert push(8, weight=weight).value == pytest.approx(4.0)


def test_two_half_lives_quarter_it(params):
    half_life = params.half_life(Axis.DIRECTIONAL)
    weight = recency_weight(NOW - 2 * half_life, NOW, half_life)

    assert weight == pytest.approx(0.25)


def test_evidence_from_this_moment_is_undecayed(params):
    assert recency_weight(NOW, NOW, params.half_life(Axis.DIRECTIONAL)) == 1.0


def test_a_timestamp_from_the_future_does_not_amplify_a_story(params):
    """Feeds occasionally publish a minute ahead of the clock; unclamped that overweights it."""
    weight = recency_weight(NOW + timedelta(minutes=5), NOW, params.half_life(Axis.DIRECTIONAL))

    assert weight == 1.0


def test_the_score_is_the_sum_of_weighted_signed_magnitudes():
    contributors = (push(8, 1, 1.0), push(6, -1, 0.5, identifier="S2"))

    assert score_of(contributors) == pytest.approx(8.0 - 3.0)


def test_contributions_are_built_from_stored_rows(params):
    rows = [
        {
            "identifier": "S1",
            "description": "x",
            "magnitude": 8,
            "polarity": 1,
            "last_evidence_at": (NOW - params.half_life(Axis.DIRECTIONAL)).isoformat(),
            "status": "OPEN",
        },
    ]

    (built,) = contributions(rows, now=NOW, half_life=params.half_life(Axis.DIRECTIONAL))

    assert built.recency_weight == pytest.approx(0.5)
    assert built.value == pytest.approx(4.0)


# --- The four states -------------------------------------------------------------------


def test_zero_situations_reads_no_read_with_nothing_on_file(params):
    result = read("EUR", Axis.POLICY, (), params)

    assert result.state is State.NO_READ
    assert result.no_read_reason == NOTHING_ON_FILE
    assert result.score == 0.0
    assert result.degree is None


def test_one_moderate_situation_reads_no_read_even_above_the_threshold(params):
    """One headline is not a read. This is the rule that removes the false signals."""
    result = read("EUR", Axis.POLICY, (push(5),), params)

    assert result.score == 5.0
    assert result.score > params.read_threshold
    assert result.state is State.NO_READ
    assert result.no_read_reason == TOO_THIN


def test_one_shock_carries_a_read_alone(params):
    """Magnitude 8 clears SHOCK_MAGNITUDE, so breadth is satisfied by depth."""
    result = read("EUR", Axis.POLICY, (push(8),), params)

    assert result.state is State.POSITIVE
    assert result.degree is Degree.MODERATELY


def test_a_shock_exactly_at_the_bar_counts(params):
    assert read("EUR", Axis.POLICY, (push(7),), params).state is State.POSITIVE


def test_two_corroborating_situations_read_directional(params):
    result = read("EUR", Axis.POLICY, (push(4), push(3, identifier="S2")), params)

    assert result.score == 7.0
    assert result.state is State.POSITIVE
    assert result.contributor_ids == ("S1", "S2")


def test_balanced_evidence_reads_neutral_which_is_a_positive_finding(params):
    """Two contributors that cancel. Evidence exists and it says the read is balanced."""
    result = read("EUR", Axis.POLICY, (push(6), push(6, -1, identifier="S2")), params)

    assert result.score == 0.0
    assert result.state is State.NEUTRAL
    assert result.no_read_reason is None


def test_broad_evidence_below_the_threshold_reads_neutral(params):
    result = read("EUR", Axis.POLICY, (push(4), push(3, -1, identifier="S2")), params)

    assert result.score == 1.0
    assert result.state is State.NEUTRAL


def test_a_negative_score_reads_the_other_way(params):
    result = read("EUR", Axis.DIRECTIONAL, (push(5, -1), push(4, -1, identifier="S2")), params)

    assert result.state is State.NEGATIVE
    assert result.words == "moderately bearish"


def test_a_score_exactly_at_the_threshold_is_directional(params):
    result = read("EUR", Axis.POLICY, (push(1.75), push(1.75, identifier="S2")), params)

    assert result.score == pytest.approx(3.5)
    assert result.state is State.POSITIVE


# --- NO READ is never NEUTRAL ----------------------------------------------------------


def test_no_read_is_never_returned_as_neutral(params):
    """The guarantee the whole four-state vocabulary rests on."""
    thin = read("EUR", Axis.POLICY, (push(5),), params)
    empty = read("EUR", Axis.POLICY, (), params)

    for result in (thin, empty):
        assert result.state is State.NO_READ
        assert result.state is not State.NEUTRAL
        assert result.words == "no read"
        assert "neutral" not in result.words


def test_an_unreadable_axis_always_carries_a_reason(params):
    """ "No read" with no explanation is the same blank box the four states exist to avoid."""
    for contributors in ((), (push(5),)):
        assert read("EUR", Axis.POLICY, contributors, params).no_read_reason


def test_a_readable_axis_carries_no_no_read_reason(params):
    broad = read("EUR", Axis.POLICY, (push(4), push(4, identifier="S2")), params)
    assert broad.no_read_reason is None


# --- Breadth ---------------------------------------------------------------------------


def test_breadth_is_met_by_count_or_by_one_shock(params):
    assert passes_breadth((push(2), push(2, identifier="S2")), params)
    assert passes_breadth((push(7),), params)
    assert not passes_breadth((push(6.9),), params)
    assert not passes_breadth((), params)


def test_a_faded_contributor_does_not_corroborate(params):
    """One live story plus one nearly-dead one is not two stories. Before the floor, the
    dead one supplied 3% of the score and 50% of the breadth test."""
    live = push(6)
    dead = push(5, identifier="S2", weight=0.04)  # worth 0.2

    assert dead.value < params.breadth_minimum
    assert not passes_breadth((live, dead), params)
    result = read("EUR", Axis.DIRECTIONAL, (live, dead), params)
    assert result.state is State.NO_READ
    assert result.score == pytest.approx(6.2)  # the score still counts it


def test_the_breadth_floor_scales_a_story_life_with_its_size(params):
    """A shock corroborates for longer than a note — what one shared date cannot express."""
    import math

    hl = params.half_life(Axis.DIRECTIONAL).days
    lives = {m: hl * math.log2(m / params.breadth_minimum) for m in (3, 7, 10)}
    assert lives[3] < lives[7] < lives[10]
    assert 5 < lives[3] < 6  # a magnitude-3 note lasts under a week
    assert 13 < lives[7] < 15  # a magnitude-7 shock lasts a fortnight


def test_material_ignores_what_has_faded_below_the_floor(params):
    from engine.reading.arithmetic import material

    kept = material((push(6), push(5, identifier="S2", weight=0.04)), params)

    assert [c.identifier for c in kept] == ["S1"]


# --- Degree ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0.0, None),
        (3.4, None),
        (3.5, Degree.SLIGHTLY),
        (6.99, Degree.SLIGHTLY),
        (7.0, Degree.MODERATELY),
        (11.99, Degree.MODERATELY),
        (12.0, Degree.STRONGLY),
        (99.0, Degree.STRONGLY),
        (-12.0, Degree.STRONGLY),
    ],
)
def test_degree_bands(params, score, expected):
    assert band(score, params) is expected


def test_a_score_of_nine_is_moderately(params):
    """The spec's worked example."""
    result = read("EUR", Axis.POLICY, (push(5), push(4, identifier="S2")), params)

    assert result.score == 9.0
    assert result.degree is Degree.MODERATELY


def test_re_banding_never_changes_a_state(params):
    """Degree is presentation. Changing the bands moves a word, never a decision."""
    from dataclasses import replace

    contributors = (push(5), push(4, identifier="S2"))
    original = read("EUR", Axis.POLICY, contributors, params)
    rebanded = read(
        "EUR",
        Axis.POLICY,
        contributors,
        replace(params, degree_bands=(("slightly", 1.0), ("moderately", 2.0), ("strongly", 3.0))),
    )

    assert rebanded.state is original.state
    assert rebanded.score == original.score
    assert rebanded.degree is not original.degree  # only the word moved


def test_the_raw_score_is_retained_on_every_read(params):
    """So thresholds and bands can be re-tuned against recorded passes without a new pass."""
    for contributors in ((), (push(5),), (push(6), push(6, -1, identifier="S2")), (push(8),)):
        assert read("EUR", Axis.POLICY, contributors, params).score is not None


# --- Contributor ordering ---------------------------------------------------------------


def test_majority_polarity_is_listed_first(params):
    """So the counter-evidence is visibly the minority on the detail view."""
    result = read(
        "EUR",
        Axis.DIRECTIONAL,
        (
            push(3, -1, identifier="S9", description="against"),
            push(6, 1, identifier="S1"),
            push(5, 1, identifier="S2"),
        ),
        params,
    )

    assert [c.identifier for c in result.majority_first] == ["S1", "S2", "S9"]
    assert [c.identifier for c in result.minority] == ["S9"]


def test_a_neutral_read_has_no_minority(params):
    """Nothing is winning, so nothing is arguing against the winner."""
    result = read("EUR", Axis.POLICY, (push(6), push(6, -1, identifier="S2")), params)

    assert result.minority == ()


# --- The four triggers ------------------------------------------------------------------


def make(params, policy_pushes, directional_pushes):
    return (
        read("EUR", Axis.POLICY, policy_pushes, params),
        read("EUR", Axis.DIRECTIONAL, directional_pushes, params),
    )


def test_near_miss_fires_within_the_margin_and_records_the_score(params):
    """2.45 is 0.7 × 3.5. A score of 2.8 misses the bar but is worth saying so."""
    policy, directional = make(params, (push(5), push(2.2, -1, identifier="S2")), ())

    apply_triggers(policy, directional, params)

    assert policy.score == pytest.approx(2.8)
    assert policy.triggers[NEAR_MISS]["score"] == 2.8
    assert policy.triggers[NEAR_MISS]["threshold"] == 3.5


def test_near_miss_does_not_fire_well_below_the_margin(params):
    policy, directional = make(params, (push(3), push(2.5, -1, identifier="S2")), ())

    apply_triggers(policy, directional, params)

    assert NEAR_MISS not in policy.triggers


def test_near_miss_does_not_fire_on_an_unreadable_axis(params):
    """A NO READ from thinness is an absence, not a near miss."""
    policy, directional = make(params, (push(3),), ())

    apply_triggers(policy, directional, params)

    assert policy.state is State.NO_READ
    assert NEAR_MISS not in policy.triggers


def test_axis_conflict_fires_when_the_two_axes_point_opposite_ways(params):
    policy, directional = make(
        params,
        (push(5), push(4, identifier="S2")),  # +9, hawkish
        (push(6, -1, identifier="S3"), push(5, -1, identifier="S4")),  # -11, bearish
    )

    apply_triggers(policy, directional, params)

    assert policy.triggers[AXIS_CONFLICT]["larger"] == "directional"
    assert directional.triggers[AXIS_CONFLICT]["policy_score"] == 9.0
    assert directional.triggers[AXIS_CONFLICT]["directional_score"] == -11.0


def test_axis_conflict_does_not_fire_when_the_axes_agree(params):
    policy, directional = make(
        params,
        (push(5), push(4, identifier="S2")),
        (push(6, identifier="S3"), push(5, identifier="S4")),
    )

    apply_triggers(policy, directional, params)

    assert AXIS_CONFLICT not in policy.triggers


def test_axis_conflict_does_not_fire_when_one_axis_is_unreadable(params):
    """Nothing is in conflict with an axis that says nothing."""
    policy, directional = make(params, (push(5), push(4, identifier="S2")), ())

    apply_triggers(policy, directional, params)

    assert AXIS_CONFLICT not in policy.triggers


def test_counter_evidence_names_the_minority_situations(params):
    policy, directional = make(
        params,
        (
            push(6, 1, identifier="S1"),
            push(5, 1, identifier="S2"),
            push(3, -1, identifier="S9", description="officials pushing the other way"),
        ),
        (),
    )

    apply_triggers(policy, directional, params)

    assert policy.triggers[COUNTER_EVIDENCE]["situations"] == ["S9"]
    assert "officials pushing the other way" in policy.triggers[COUNTER_EVIDENCE]["descriptions"]


def test_counter_evidence_does_not_fire_when_every_situation_agrees(params):
    policy, directional = make(params, (push(6), push(5, identifier="S2")), ())

    apply_triggers(policy, directional, params)

    assert COUNTER_EVIDENCE not in policy.triggers


def test_divergence_fires_when_expected_differs_from_now(params):
    policy, directional = make(params, (push(6, -1), push(5, -1, identifier="S2")), ())
    thursday = datetime(2026, 9, 3, 12, 45, tzinfo=UTC)

    apply_triggers(
        policy,
        directional,
        params,
        expected_policy=State.POSITIVE,
        resolves_at=thursday,
    )

    assert policy.triggers[DIVERGENCE] == {
        "now": "NEGATIVE",
        "expected": "POSITIVE",
        "resolves_at": thursday.isoformat(),
    }


def test_divergence_does_not_fire_when_the_week_changes_nothing(params):
    policy, directional = make(params, (push(6), push(5, identifier="S2")), ())

    apply_triggers(policy, directional, params, expected_policy=State.POSITIVE)

    assert DIVERGENCE not in policy.triggers


def test_no_triggers_at_all_is_a_recordable_answer(params):
    """Downstream is told so explicitly; a quiet read must not be dressed up as a tense one."""
    policy, directional = make(params, (push(6), push(5, identifier="S2")), ())

    apply_triggers(policy, directional, params)

    assert policy.triggers == {}
    assert directional.triggers == {}


def test_applying_triggers_twice_does_not_accumulate(params):
    """Re-running the arithmetic on the same read must produce the same triggers, not more."""
    policy, directional = make(params, (push(2), push(1, identifier="S2")), ())

    apply_triggers(policy, directional, params)
    first = dict(policy.triggers)
    apply_triggers(policy, directional, params)

    assert policy.triggers == first


# --- Two clocks -------------------------------------------------------------------------


def test_policy_decays_more_slowly_than_direction(params):
    """A stance is not an event. "The ECB is pushing back on cuts" does not become less
    true because nobody wrote about it on Thursday."""
    assert params.half_life(Axis.POLICY) > params.half_life(Axis.DIRECTIONAL)

    three_weeks = timedelta(days=21)
    policy = recency_weight(NOW - three_weeks, NOW, params.half_life(Axis.POLICY))
    direction = recency_weight(NOW - three_weeks, NOW, params.half_life(Axis.DIRECTIONAL))

    assert 7 * policy == pytest.approx(3.5, abs=0.01)  # exactly at the bar
    assert 7 * direction == pytest.approx(0.88, abs=0.01)  # dead


def test_the_archive_never_cuts_a_story_that_still_corroborates(params):
    """The archive is a backstop. If it sits inside the longest useful life it silently
    truncates the stances the slow policy clock exists to keep — which is what a 30-day
    setting did to every policy story above magnitude 5.
    """
    import math

    longest = max(
        params.half_life(axis).days * math.log2(10 / params.breadth_minimum) for axis in Axis
    )

    assert params.archive_after.days > longest, (
        f"archive at {params.archive_after.days}d cuts stories still worth counting "
        f"at {longest:.0f}d"
    )


def test_a_policy_stance_survives_a_central_bank_cycle(params):
    """Six weeks between meetings. A magnitude-7 stance must still corroborate at the next."""
    import math

    life = params.half_life(Axis.POLICY).days * math.log2(7 / params.breadth_minimum)

    assert life >= 42
    assert params.archive_after.days >= 42
