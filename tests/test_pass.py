"""The pass, end to end over fixture feeds and a stubbed model client.

Nothing here reaches the network or bills a call. The point is the chain: that every stage
runs in the order the design fixes, that a failure stops it cleanly leaving the previous
board readable, and that a full pass produces a read, an expectation and a narrative for
every one of the thirteen entities.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from engine.feeds.transport import FetchedBody
from engine.passes import PassFailed, board, latest_pass, run
from engine.reading.states import Axis, State
from engine.stages import Stages
from engine.store.schema import connect
from engine.universe.config import load_params, load_universe
from tests.stub_model import (
    StubClient,
    expectation_of,
    narrative_of,
    score_touching,
    situations_opening,
    triage_keeping,
)

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def params():
    return load_params()


@pytest.fixture(scope="module")
def universe():
    return load_universe()


@pytest.fixture
def db(tmp_path):
    connection = connect(tmp_path / "engine.db")
    yield connection
    connection.close()


@pytest.fixture
def feeds(fixture_body):
    def _fetch(url: str) -> FetchedBody:
        name = "calendar_good.json" if "calendar" in url else "news_good.xml"
        return FetchedBody(text=fixture_body(name), resolved_url=url)

    return _fetch


@pytest.fixture
def stub():
    """A script that opens two USD policy situations, so USD reads directional."""
    return StubClient(
        answers={
            "triage": [triage_keeping(0, 1)],
            "score": [
                score_touching(
                    ("USD", 1),
                    axis="POLICY",
                    magnitude=6,
                    description="Fed officials pushing back on cuts",
                ),
                score_touching(
                    ("USD", 1),
                    axis="POLICY",
                    magnitude=5,
                    description="inflation running above target",
                ),
            ],
            "situations": [
                situations_opening(
                    ("Fed officials pushing back on cuts", "POLICY", 6, (("USD", 1),)),
                    ("inflation running above target", "POLICY", 5, (("USD", 1),)),
                )
            ],
            "expectation": [expectation_of()],
            "narrative": [
                narrative_of(
                    "The read is moderately hawkish on policy (S1, S2). Nothing is arguing "
                    "against it this week.",
                    ["S1", "S2"],
                )
            ],
        }
    )


@pytest.fixture
def stages(stub, params):
    return Stages(client=stub, models=params.models, prices=params.prices)


# --- A full pass -----------------------------------------------------------------------


def test_a_full_pass_produces_a_read_for_every_entity(db, universe, params, stages, feeds):
    result = run(db, universe, params, stages, now=NOW, fetcher=feeds)

    assert result.status == "complete"
    assert set(result.reads) == set(universe.entities)
    for per_axis in result.reads.values():
        assert set(per_axis) == {Axis.POLICY, Axis.DIRECTIONAL}


def test_a_full_pass_produces_an_expectation_and_a_narrative_for_every_entity(
    db, universe, params, stages, feeds
):
    result = run(db, universe, params, stages, now=NOW, fetcher=feeds)

    assert set(result.expectations) == set(universe.entities)
    assert set(result.narratives) == set(universe.entities)
    assert all(prose.strip() for prose in result.narratives.values())


def test_the_chain_runs_in_the_fixed_order(db, universe, params, stages, feeds, stub):
    """No agentic loop, no runtime planning — the order in the design is the order."""
    run(db, universe, params, stages, now=NOW, fetcher=feeds)

    order = [call["stage"] for call in stub.calls]
    first = {stage: order.index(stage) for stage in set(order)}

    assert first["triage"] < first["score"] < first["situations"]
    assert first["situations"] < first["expectation"] < first["narrative"]


def test_the_situations_the_stage_opened_carry_the_read(db, universe, params, stages, feeds):
    result = run(db, universe, params, stages, now=NOW, fetcher=feeds)

    usd_policy = result.reads["USD"][Axis.POLICY]

    assert usd_policy.state is State.POSITIVE
    assert usd_policy.score == pytest.approx(11.0)
    assert usd_policy.contributor_ids == ("S1", "S2")


def test_an_entity_nobody_wrote_about_reads_no_read(db, universe, params, stages, feeds):
    """A thin read reported as thin is the failure mode this design prefers."""
    result = run(db, universe, params, stages, now=NOW, fetcher=feeds)

    assert result.reads["NZD"][Axis.POLICY].state is State.NO_READ
    assert result.reads["NZD"][Axis.DIRECTIONAL].state is State.NO_READ


def test_everything_is_persisted(db, universe, params, stages, feeds):
    result = run(db, universe, params, stages, now=NOW, fetcher=feeds)

    counts = {
        table: db.execute(
            f"SELECT count(*) FROM {table} WHERE pass_id = ?", (result.pass_id,)
        ).fetchone()[0]
        for table in ("reads", "expectations", "narratives")
    }

    assert counts == {"reads": 26, "expectations": 13, "narratives": 13}


def test_the_verbatim_prompt_is_stored_not_reconstructed(db, universe, params, stages, feeds, stub):
    """A prompt rebuilt from its inputs is not the bytes that were sent."""
    result = run(db, universe, params, stages, now=NOW, fetcher=feeds)

    stored = db.execute(
        "SELECT prompt, model, effort FROM narratives WHERE pass_id = ? AND entity = 'USD'",
        (result.pass_id,),
    ).fetchone()
    sent = [call["prompt"] for call in stub.calls if call["stage"] == "narrative"]

    assert stored["prompt"] in sent
    assert stored["model"] == params.models["NARRATIVE"]
    assert stored["effort"] == "medium"


def test_the_pass_records_its_cost_and_adherence(db, universe, params, stages, feeds):
    result = run(db, universe, params, stages, now=NOW, fetcher=feeds)

    row = db.execute("SELECT * FROM passes WHERE id = ?", (result.pass_id,)).fetchone()

    assert row["status"] == "complete"
    assert row["adherence"] == 1.0
    assert row["cost_usd"] > 0
    costs = json.loads(row["stage_costs"])
    assert set(costs) == {"triage", "score", "situations", "expectation", "narrative"}
    assert costs["triage"]["billed_calls"] == 1
    assert all("input_tokens" in stage and "output_tokens" in stage for stage in costs.values())


def test_the_trigger_is_recorded_on_the_pass(db, universe, params, stages, feeds):
    result = run(
        db, universe, params, stages, trigger="forced", now=NOW, fetcher=feeds, forced=True
    )

    row = db.execute("SELECT trigger FROM passes WHERE id = ?", (result.pass_id,)).fetchone()

    assert row["trigger"] == "forced"


def test_triage_routes_to_haiku_and_everything_else_to_opus(
    db, universe, params, stages, feeds, stub
):
    run(db, universe, params, stages, now=NOW, fetcher=feeds)

    by_stage = {call["stage"]: call["model"] for call in stub.calls}

    assert by_stage["triage"] == "claude-haiku-4-5-20251001"
    assert {by_stage[s] for s in ("score", "situations", "expectation", "narrative")} == {
        "claude-opus-5-5"
    }


def test_dropped_headlines_are_marked_rather_than_left_pending(db, universe, params, stub, feeds):
    """Otherwise the next hourly pass re-triages the same headline forever."""
    stub.answers["triage"] = [triage_keeping(0)]
    stages = Stages(client=stub, models=load_params().models)

    run(db, universe, load_params(), stages, now=NOW, fetcher=feeds)

    states = [row["triaged"] for row in db.execute("SELECT triaged FROM headlines")]
    assert sorted(states) == [-1, 1]


# --- The cheap poll ---------------------------------------------------------------------


def test_the_poll_variant_stops_after_scoring(db, universe, params, stages, feeds, stub):
    result = run(db, universe, params, stages, now=NOW, fetcher=feeds, poll_only=True)

    assert result.status == "complete"
    assert result.reads == {}
    assert stub.count("expectation") == 0
    assert stub.count("narrative") == 0


def test_a_quiet_poll_costs_nothing_at_all(db, universe, params, stages, feeds, stub):
    """A second poll over an unchanged feed: everything de-duplicates, no stage runs."""
    run(db, universe, params, stages, now=NOW, fetcher=feeds, poll_only=True)
    before = len(stub.calls)

    run(db, universe, params, stages, now=NOW, fetcher=feeds, poll_only=True)

    assert len(stub.calls) == before


# --- Failure stops the chain ---------------------------------------------------------------


@pytest.mark.parametrize("stage", ["triage", "score", "situations", "expectation", "narrative"])
def test_a_stage_failure_stops_the_chain_and_records_which_stage(
    db, universe, params, stub, feeds, stage
):
    stub.fail_on = {stage}
    stages = Stages(client=stub, models=params.models, prices=params.prices)

    with pytest.raises(PassFailed) as failure:
        run(db, universe, params, stages, now=NOW, fetcher=feeds)

    assert failure.value.stage == stage
    row = db.execute("SELECT * FROM passes ORDER BY id DESC LIMIT 1").fetchone()
    assert row["status"] == "failed"
    assert row["failed_stage"] == stage


def test_a_failed_pass_leaves_the_previous_board_readable(
    db, universe, params, stub, feeds, fixture_body
):
    """A stale read is visible; a half-written one is not.

    The second pass carries a genuinely new headline, so change detection does not skip the
    narrative stage — without that the pass would complete quietly and prove nothing.
    """
    good = Stages(client=stub, models=params.models, prices=params.prices)
    first = run(db, universe, params, good, now=NOW, fetcher=feeds)

    extended = fixture_body("news_good.xml").replace(
        "</channel>",
        "<item><title>BoJ holds</title><description>As expected.</description>"
        "<link>https://www.investinglive.com/boj-20260831</link>"
        "<pubDate>Mon, 31 Aug 2026 15:00:00 GMT</pubDate></item></channel>",
    )

    def more(url: str) -> FetchedBody:
        body = fixture_body("calendar_good.json") if "calendar" in url else extended
        return FetchedBody(text=body, resolved_url=url)

    stub.fail_on = {"narrative"}
    stub.answers["triage"] = [triage_keeping(0)]
    stub.answers["score"] = [score_touching(("JPY", -1), magnitude=8, description="BoJ holds")]
    stub.answers["situations"] = [situations_opening(("BoJ holds", "POLICY", 8, (("JPY", -1),)))]
    broken = Stages(client=stub, models=params.models, prices=params.prices)

    with pytest.raises(PassFailed):
        run(db, universe, params, broken, now=NOW + timedelta(hours=1), fetcher=more)

    assert latest_pass(db)["id"] == first.pass_id
    assert db.execute("SELECT count(*) FROM reads").fetchone()[0] == 26


def test_headlines_orphaned_by_a_failed_pass_are_picked_up_by_the_next_one(
    db, universe, params, stub, feeds
):
    """Found by the first live pass: a run that ingests and then fails leaves those rows
    stored and untriaged, and no later poll re-inserts them. Working from this pass's own
    arrivals would strand them permanently, and the board would read thin forever for a
    reason that looks exactly like a quiet week.
    """
    stub.fail_on = {"triage"}
    with pytest.raises(PassFailed):
        run(
            db,
            universe,
            params,
            Stages(client=stub, models=params.models, prices=params.prices),
            now=NOW,
            fetcher=feeds,
        )

    pending = db.execute("SELECT count(*) FROM headlines WHERE triaged = 0").fetchone()[0]
    assert pending == 2

    stub.fail_on = set()
    result = run(
        db,
        universe,
        params,
        Stages(client=stub, models=params.models, prices=params.prices),
        now=NOW,
        fetcher=feeds,
    )

    assert result.status == "complete"
    assert db.execute("SELECT count(*) FROM headlines WHERE triaged = 0").fetchone()[0] == 0
    assert result.reads["USD"][Axis.POLICY].contributor_ids == ("S1", "S2")


def test_headlines_triaged_but_not_scored_are_picked_up_by_the_next_pass(
    db, universe, params, stub, feeds
):
    """The second half of the same bug, found by the second live pass: triage commits its
    verdict, then scoring dies. Those rows are kept and unscored, and nothing re-triages
    them — so the scoring step has to resume from what is unscored, not from what is new.
    """
    stub.fail_on = {"score"}
    with pytest.raises(PassFailed):
        run(
            db,
            universe,
            params,
            Stages(client=stub, models=params.models, prices=params.prices),
            now=NOW,
            fetcher=feeds,
        )

    pending = db.execute(
        "SELECT count(*) FROM headlines WHERE triaged = 1 AND scored_at IS NULL"
    ).fetchone()[0]
    assert pending == 2

    stub.fail_on = set()
    result = run(
        db,
        universe,
        params,
        Stages(client=stub, models=params.models, prices=params.prices),
        now=NOW,
        fetcher=feeds,
    )

    assert result.status == "complete"
    assert result.reads["USD"][Axis.POLICY].contributor_ids == ("S1", "S2")
    assert (
        db.execute(
            "SELECT count(*) FROM headlines WHERE triaged = 1 AND scored_at IS NULL"
        ).fetchone()[0]
        == 0
    )


def test_a_resumed_pass_does_not_pay_to_triage_again(db, universe, params, stub, feeds):
    """Triage's verdict is committed before scoring runs, so a resume never re-buys it."""
    stub.fail_on = {"score"}
    with pytest.raises(PassFailed):
        run(
            db,
            universe,
            params,
            Stages(client=stub, models=params.models, prices=params.prices),
            now=NOW,
            fetcher=feeds,
        )
    triage_calls = stub.count("triage")

    stub.fail_on = set()
    run(
        db,
        universe,
        params,
        Stages(client=stub, models=params.models, prices=params.prices),
        now=NOW,
        fetcher=feeds,
    )

    assert stub.count("triage") == triage_calls


def test_a_headline_that_touches_nothing_is_not_re_scored_forever(
    db, universe, params, stub, feeds
):
    """Marked scored whether or not it produced a touch, or every later pass re-buys it."""
    stub.answers["score"] = ['{"touches": []}']
    stages = Stages(client=stub, models=params.models, prices=params.prices)
    run(db, universe, params, stages, now=NOW, fetcher=feeds)
    scored_calls = stub.count("score")

    run(
        db,
        universe,
        params,
        Stages(client=stub, models=params.models, prices=params.prices),
        now=NOW,
        fetcher=feeds,
    )

    assert stub.count("score") == scored_calls
    assert (
        db.execute(
            "SELECT count(*) FROM headlines WHERE triaged = 1 AND scored_at IS NULL"
        ).fetchone()[0]
        == 0
    )


def test_a_second_pass_does_not_re_triage_what_the_first_already_judged(
    db, universe, params, stub, feeds
):
    """Pending, not all-time: a headline triage has already ruled on must not be re-billed."""
    stages = Stages(client=stub, models=params.models, prices=params.prices)
    run(db, universe, params, stages, now=NOW, fetcher=feeds)
    before = stub.count("triage")

    run(
        db,
        universe,
        params,
        Stages(client=stub, models=params.models, prices=params.prices),
        now=NOW,
        fetcher=feeds,
    )

    assert stub.count("triage") == before


def test_a_failed_ingest_is_recorded_against_the_pass(db, universe, params, stages, fixture_body):
    def broken(url: str) -> FetchedBody:
        body = "not json at all" if "calendar" in url else fixture_body("news_good.xml")
        return FetchedBody(text=body, resolved_url=url)

    with pytest.raises(PassFailed) as failure:
        run(db, universe, params, stages, now=NOW, fetcher=broken)

    assert failure.value.stage == "ingest"
    assert db.execute("SELECT count(*) FROM calendar_events").fetchone()[0] == 0


def test_a_failed_pass_records_what_it_had_already_spent(db, universe, params, stub, feeds):
    stub.fail_on = {"narrative"}
    stages = Stages(client=stub, models=params.models, prices=params.prices)

    with pytest.raises(PassFailed):
        run(db, universe, params, stages, now=NOW, fetcher=feeds)

    row = db.execute("SELECT cost_usd FROM passes ORDER BY id DESC LIMIT 1").fetchone()
    assert row["cost_usd"] > 0


# --- Warnings surface on the pass -------------------------------------------------------------


def test_an_empty_calendar_week_warns_on_the_pass(db, universe, params, stages, fixture_body):
    def quiet(url: str) -> FetchedBody:
        body = "[]" if "calendar" in url else fixture_body("news_good.xml")
        return FetchedBody(text=body, resolved_url=url)

    result = run(db, universe, params, stages, now=NOW, fetcher=quiet)

    assert "empty_calendar_week" in result.warnings
    stored = json.loads(
        db.execute("SELECT warnings FROM passes WHERE id = ?", (result.pass_id,)).fetchone()[0]
    )
    assert "empty_calendar_week" in stored


def test_an_invented_citation_warns_on_the_pass(db, universe, params, stub, feeds):
    """The spot check runs at the end of every pass, not only when someone remembers."""
    stub.answers["narrative"] = [
        narrative_of(
            "The read rests on S99 and nothing else. There is little more to say.", ["S99"]
        )
    ]
    stages = Stages(client=stub, models=params.models, prices=params.prices)

    result = run(db, universe, params, stages, now=NOW, fetcher=feeds)

    assert any("invented_citation" in warning for warning in result.warnings)


def test_a_narrative_breaking_the_bound_is_stored_with_the_violation_recorded(
    db, universe, params, stub, feeds
):
    """Stored, not discarded — the page that opens with prose must not open with a table."""
    stub.answers["narrative"] = [narrative_of("One sentence only.")]
    stages = Stages(client=stub, models=params.models, prices=params.prices)

    result = run(db, universe, params, stages, now=NOW, fetcher=feeds)

    assert any("narrative:" in warning and "sentence" in warning for warning in result.warnings)
    assert db.execute("SELECT count(*) FROM narratives").fetchone()[0] == 13


# --- Reading the stored board back --------------------------------------------------------------


def test_the_stored_pass_resolves_onto_the_instrument_list(db, universe, params, stages, feeds):
    result = run(db, universe, params, stages, now=NOW, fetcher=feeds)

    resolved = board(db, universe, params, result.pass_id)

    assert len(resolved["instruments"]) == len(universe.instruments)
    spx = next(i for i in resolved["instruments"] if i.ticker == "SPX500USD")
    # USD policy is +11.0 hawkish, so the index inherits -11.0 and reads bearish.
    assert spx.policy.state is State.NEGATIVE
    assert spx.policy.score == pytest.approx(-11.0)


def test_an_instrument_with_an_unreadable_leg_resolves_to_no_read(
    db, universe, params, stages, feeds
):
    result = run(db, universe, params, stages, now=NOW, fetcher=feeds)

    resolved = board(db, universe, params, result.pass_id)
    eurnzd = next(i for i in resolved["instruments"] if i.ticker == "EURNZD")

    assert eurnzd.policy.state is State.NO_READ
    assert eurnzd.directional.state is State.NO_READ


# --- One bad reply must not cost the pass ------------------------------------------------------

# The reply that actually broke production on 01 Sep: the narrative stage emitted an
# unescaped double quote inside `prose`, which makes the whole object unparseable.
UNUSABLE = '{"prose": "the ECB is "guiding neutral" here", "cited": []}'


def test_one_unusable_narrative_warns_without_discarding_the_pass(
    db, universe, params, stub, feeds
):
    """Twelve entities must not lose a paid-for pass because the thirteenth replied badly."""
    stub.answers["narrative"] = [UNUSABLE, stub.answers["narrative"][0]]
    stages = Stages(client=stub, models=params.models, prices=params.prices)

    result = run(db, universe, params, stages, now=NOW, fetcher=feeds)

    assert result.status == "complete"
    assert len(result.narratives) == len(universe.entities) - 1
    assert any("unusable reply" in warning for warning in result.warnings)


def test_an_unusable_narrative_names_the_entity_it_lost(db, universe, params, stub, feeds):
    stub.answers["narrative"] = [UNUSABLE, stub.answers["narrative"][0]]
    stages = Stages(client=stub, models=params.models, prices=params.prices)

    result = run(db, universe, params, stages, now=NOW, fetcher=feeds)

    lost = [w for w in result.warnings if "unusable reply" in w]
    assert len(lost) == 1
    entity = lost[0].split(":")[1]
    assert entity in universe.entities
    assert entity not in result.narratives


def test_every_narrative_unusable_still_fails_the_pass(db, universe, params, stub, feeds):
    """A stage that never parses is broken, and must not look like a clean pass."""
    stub.answers["narrative"] = [UNUSABLE]
    stages = Stages(client=stub, models=params.models, prices=params.prices)

    with pytest.raises(PassFailed) as failure:
        run(db, universe, params, stages, now=NOW, fetcher=feeds)

    assert failure.value.stage == "narrative"


def test_a_transport_failure_in_narrative_still_fails_the_pass(db, universe, params, stub, feeds):
    """Only an unparseable *reply* is survivable; a dead client is not."""
    stub.fail_on = {"narrative"}
    stages = Stages(client=stub, models=params.models, prices=params.prices)

    with pytest.raises(PassFailed) as failure:
        run(db, universe, params, stages, now=NOW, fetcher=feeds)

    assert failure.value.stage == "narrative"


def test_situation_failure_keeps_scored_headlines_available_for_retry(
    db, universe, params, stub, feeds
):
    stub.fail_on = {"situations"}
    with pytest.raises(PassFailed, match="situations"):
        run(db, universe, params, Stages(stub, params.models), now=NOW, fetcher=feeds)
    assert db.execute("SELECT count(*) FROM headlines WHERE scored_at IS NULL").fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM situations").fetchone()[0] == 0

    stub.fail_on.clear()
    result = run(db, universe, params, Stages(stub, params.models), now=NOW, fetcher=feeds)
    assert result.status == "complete"
    assert len(result.reads["USD"][Axis.POLICY].contributor_ids) == 2
    assert db.execute("SELECT count(DISTINCT record_id) FROM situation_evidence").fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM headlines WHERE scored_at IS NULL").fetchone()[0] == 0


def test_partial_situation_write_rolls_back_without_losing_headlines(
    db, universe, params, stub, feeds, monkeypatch
):
    import engine.situations.lifecycle as lifecycle

    original = lifecycle.open_situation
    calls = 0

    def fail_after_one(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("interrupted situation write")
        return original(*args, **kwargs)

    monkeypatch.setattr(lifecycle, "open_situation", fail_after_one)
    with pytest.raises(RuntimeError, match="interrupted"):
        run(db, universe, params, Stages(stub, params.models), now=NOW, fetcher=feeds)
    assert db.execute("SELECT count(*) FROM situations").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM headlines WHERE scored_at IS NULL").fetchone()[0] == 2
    monkeypatch.setattr(lifecycle, "open_situation", original)
    result = run(db, universe, params, Stages(stub, params.models), now=NOW, fetcher=feeds)
    assert len(result.reads["USD"][Axis.POLICY].contributor_ids) == 2


def test_calendar_revision_refreshes_an_unchanged_entity(db, universe, params, stub, feeds):
    run(db, universe, params, Stages(stub, params.models), now=NOW, fetcher=feeds)
    before = stub.count("expectation")

    def revised(url):
        body = feeds(url)
        if "calendar" in url:
            return FetchedBody(body.text.replace('"78K"', '"90K"'), body.resolved_url)
        return body

    result = run(
        db,
        universe,
        params,
        Stages(stub, params.models),
        now=NOW,
        fetcher=revised,
    )
    assert "USD" not in result.skipped
    assert stub.count("expectation") == before + 1
    assert "90K" in result.expectations["USD"].prompt
