"""The three triggers, change detection, debouncing, and the missed-trigger catch-up.

The property this file exists to hold down is that **no trigger path reads an `actual`**.
The live calendar publishes none, on any row, including rows already in the past — so a
release has to be detected on the clock, and every test here runs against a fixture feed
with no released values anywhere in it.
"""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime, timedelta

import pytest

import engine.triggers as triggers_module
from engine.feeds.transport import FetchedBody
from engine.loop import Loop
from engine.passes import run, start_pass
from engine.reading.arithmetic import Contribution, read
from engine.reading.states import Axis
from engine.stages import Stages
from engine.store.schema import connect, transaction
from engine.triggers import (
    carry_forward,
    detect_changes,
    due_releases,
    release_triggers,
)
from engine.universe.config import load_params, load_universe
from tests.conftest import executable_source
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


def add_event(db, *, ident, currency, when, impact="High", title="Rate Decision"):
    """Store one calendar event. Deliberately no `actual` column to pass — there is none."""
    with transaction(db):
        db.execute(
            "INSERT INTO calendar_events (id, currency, title, scheduled_at, impact,"
            " forecast_text, previous_text, ingested_at)"
            " VALUES (?, ?, ?, ?, ?, '0.25%', '0.25%', ?)",
            (ident, currency, title, when.isoformat(), impact, NOW.isoformat()),
        )


# --- No trigger path reads a released value -------------------------------------------------


def test_no_code_in_the_trigger_module_reads_an_actual_field():
    """Asserted over the source with its prose stripped, not assumed."""
    code = executable_source(inspect.getsource(triggers_module))

    assert "actual" not in code.lower()


def test_triggers_fire_against_a_feed_with_no_released_values(db, params, fixture_body):
    """The recorded fixture omits `actual` on every row, exactly as the live feed does."""
    from engine.feeds.calendar_feed import parse_calendar

    events, _ = parse_calendar(fixture_body("calendar_good.json"))
    assert all(not event.has_actual for event in events)

    for event in events:
        add_event(
            db,
            ident=event.id,
            currency=event.currency,
            when=event.scheduled_at,
            impact=str(event.impact),
            title=event.title,
        )

    found = release_triggers(db, params, since=NOW, until=NOW + timedelta(days=7))

    assert found, "no release trigger fired against a feed with no actuals"
    assert all(trigger.at.tzinfo is not None for trigger in found)


# --- Watch list and impact -------------------------------------------------------------------


def test_a_high_impact_event_on_a_watched_currency_fires(db, params):
    add_event(db, ident="e1", currency="USD", when=NOW + timedelta(hours=2))

    (trigger,) = release_triggers(db, params, since=NOW, until=NOW + timedelta(days=1))

    assert trigger.currencies == ("USD",)
    assert trigger.events == ("e1",)


def test_the_trigger_fires_at_the_scheduled_time_plus_the_delay(db, params):
    scheduled = NOW + timedelta(hours=2)
    add_event(db, ident="e1", currency="USD", when=scheduled)

    (trigger,) = release_triggers(db, params, since=NOW, until=NOW + timedelta(days=1))

    assert trigger.at == scheduled + params.release_delay
    assert params.release_delay == timedelta(minutes=5)


@pytest.mark.parametrize("impact", ["Medium", "Low", "Holiday"])
def test_only_high_impact_events_fire_a_release_trigger(db, params, impact):
    add_event(db, ident="e1", currency="USD", when=NOW + timedelta(hours=2), impact=impact)

    assert release_triggers(db, params, since=NOW, until=NOW + timedelta(days=1)) == []


def test_an_unwatched_currency_does_not_fire(db, params):
    """CHF is in no traded pair, so it is neither watched nor scored."""
    add_event(db, ident="e1", currency="CHF", when=NOW + timedelta(hours=2))

    assert release_triggers(db, params, since=NOW, until=NOW + timedelta(days=1)) == []


def test_the_watch_list_is_independent_of_the_scored_entity_set(db, params, universe):
    """A currency may be watched without being scored, and the refresh must still complete."""
    from dataclasses import replace

    widened = replace(params, release_watch_list=("CHF",))
    add_event(db, ident="e1", currency="CHF", when=NOW + timedelta(hours=2))

    (trigger,) = release_triggers(db, widened, since=NOW, until=NOW + timedelta(days=1))

    assert trigger.currencies == ("CHF",)
    assert "CHF" not in universe.entities


def test_a_refresh_for_a_watched_but_unscored_currency_completes(
    db, universe, params, fixture_body
):
    """The trigger reaches no instrument, and the pass must finish rather than raise."""

    def feeds(url: str) -> FetchedBody:
        name = "calendar_good.json" if "calendar" in url else "news_good.xml"
        return FetchedBody(text=fixture_body(name), resolved_url=url)

    stub = StubClient(
        answers={
            "triage": [triage_keeping()],
            "expectation": [expectation_of()],
            "narrative": [narrative_of("Nothing on file. There is no read to act on.")],
        }
    )
    result = run(
        db,
        universe,
        params,
        Stages(client=stub, models=params.models, prices=params.prices),
        trigger="release",
        now=NOW,
        fetcher=feeds,
        trigger_events=("chf-event",),
    )

    assert result.status == "complete"


# --- Debouncing --------------------------------------------------------------------------------


def test_events_at_the_same_minute_collapse_into_one_refresh(db, params):
    """RBNZ fires four at once; without this the runtime runs four passes over one feed."""
    when = NOW + timedelta(hours=3)
    for index in range(4):
        add_event(db, ident=f"rbnz{index}", currency="NZD", when=when)

    found = release_triggers(db, params, since=NOW, until=NOW + timedelta(days=1))

    assert len(found) == 1
    assert len(found[0].events) == 4


def test_a_collapsed_trigger_records_every_event_that_contributed(db, params):
    when = NOW + timedelta(hours=3)
    for index in range(3):
        add_event(db, ident=f"nfp{index}", currency="USD", when=when)

    (trigger,) = release_triggers(db, params, since=NOW, until=NOW + timedelta(days=1))

    assert set(trigger.events) == {"nfp0", "nfp1", "nfp2"}


def test_events_inside_the_debounce_window_collapse(db, params):
    base = NOW + timedelta(hours=3)
    add_event(db, ident="a", currency="USD", when=base)
    add_event(db, ident="b", currency="USD", when=base + timedelta(minutes=8))

    found = release_triggers(db, params, since=NOW, until=NOW + timedelta(days=1))

    assert len(found) == 1


def test_a_collapsed_trigger_fires_at_the_last_event_in_the_cluster(db, params):
    """Firing at the first would refresh before the later prints have landed."""
    base = NOW + timedelta(hours=3)
    add_event(db, ident="a", currency="USD", when=base)
    add_event(db, ident="b", currency="USD", when=base + timedelta(minutes=8))

    (trigger,) = release_triggers(db, params, since=NOW, until=NOW + timedelta(days=1))

    assert trigger.at == base + timedelta(minutes=8) + params.release_delay


def test_events_outside_the_debounce_window_stay_separate(db, params):
    base = NOW + timedelta(hours=3)
    add_event(db, ident="a", currency="USD", when=base)
    add_event(db, ident="b", currency="EUR", when=base + timedelta(minutes=45))

    found = release_triggers(db, params, since=NOW, until=NOW + timedelta(days=1))

    assert len(found) == 2


def test_a_cluster_spanning_two_currencies_records_both(db, params):
    when = NOW + timedelta(hours=3)
    add_event(db, ident="a", currency="USD", when=when)
    add_event(db, ident="b", currency="CAD", when=when)

    (trigger,) = release_triggers(db, params, since=NOW, until=NOW + timedelta(days=1))

    assert trigger.currencies == ("CAD", "USD")


# --- Missed triggers ------------------------------------------------------------------------------


def test_a_trigger_missed_while_the_runtime_was_down_is_detected(db, params):
    """A read that quietly missed a payrolls print is worse than a late one."""
    add_event(db, ident="e1", currency="USD", when=NOW - timedelta(hours=1))

    missed = due_releases(db, params, now=NOW)

    assert [trigger.events for trigger in missed] == [("e1",)]


def test_a_trigger_that_already_ran_is_not_run_again(db, params):
    add_event(db, ident="e1", currency="USD", when=NOW - timedelta(hours=1))
    with transaction(db):
        db.execute(
            "INSERT INTO passes (trigger, started_at, status, trigger_events)"
            " VALUES ('release', ?, 'complete', ?)",
            ((NOW - timedelta(minutes=50)).isoformat(), json.dumps(["e1"])),
        )

    assert due_releases(db, params, now=NOW) == []


def test_a_trigger_still_ahead_of_us_is_not_treated_as_missed(db, params):
    add_event(db, ident="e1", currency="USD", when=NOW + timedelta(hours=1))

    assert due_releases(db, params, now=NOW) == []


def test_a_trigger_older_than_the_lookback_is_not_resurrected(db, params):
    """Re-running yesterday's payrolls on startup would be worse than skipping it."""
    add_event(db, ident="e1", currency="USD", when=NOW - timedelta(days=2))

    assert due_releases(db, params, now=NOW) == []


# --- Change detection -----------------------------------------------------------------------------


def push(magnitude, polarity=1, identifier="S1"):
    return Contribution(identifier, "a story", magnitude, polarity, 1.0, "OPEN")


def store_reads(db, pass_id, reads):
    with transaction(db):
        for entity, per_axis in reads.items():
            for axis, value in per_axis.items():
                db.execute(
                    "INSERT INTO reads (pass_id, entity, axis, score, state, degree,"
                    " no_read_reason, contributor_count, contributor_ids, triggers, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?)",
                    (
                        pass_id,
                        entity,
                        str(axis),
                        value.score,
                        value.state.value,
                        value.degree.value if value.degree else None,
                        value.no_read_reason,
                        len(value.contributors),
                        json.dumps(list(value.contributor_ids)),
                        NOW.isoformat(),
                    ),
                )


def reads_for(params, entity, policy_pushes, directional_pushes=()):
    return {
        entity: {
            Axis.POLICY: read(entity, Axis.POLICY, policy_pushes, params),
            Axis.DIRECTIONAL: read(entity, Axis.DIRECTIONAL, directional_pushes, params),
        }
    }


def test_the_first_read_marks_every_entity_changed(db, params):
    current = reads_for(params, "EUR", (push(6), push(5, identifier="S2")))

    changes = detect_changes(db, ("EUR",), current, previous_pass_id=None)

    assert changes.changed == {"EUR"}
    assert changes.reasons["EUR"] == "first read"


def test_an_entity_nobody_wrote_about_is_unchanged(db, params):
    pass_id = start_pass(db, "hourly", NOW)
    current = reads_for(params, "EUR", (push(6), push(5, identifier="S2")))
    store_reads(db, pass_id, current)

    changes = detect_changes(db, ("EUR",), current, previous_pass_id=pass_id)

    assert changes.unchanged == {"EUR"}
    assert changes.changed == set()


def test_a_new_situation_marks_the_entity_changed(db, params):
    pass_id = start_pass(db, "hourly", NOW)
    store_reads(db, pass_id, reads_for(params, "EUR", (push(6), push(5, identifier="S2"))))

    current = reads_for(
        params, "EUR", (push(6), push(5, identifier="S2"), push(4, identifier="S3"))
    )
    changes = detect_changes(db, ("EUR",), current, previous_pass_id=pass_id)

    assert changes.changed == {"EUR"}
    assert "situations moved" in changes.reasons["EUR"]


def test_an_hour_of_decay_alone_does_not_mark_an_entity_changed(db, params):
    """Otherwise nothing is ever skipped, and the hourly cadence re-authors every paragraph.

    A 7-day half-life moves a score by about 0.4% an hour. The paragraph quotes the score to
    one decimal, so that movement changes not one word of it.
    """
    pass_id = start_pass(db, "hourly", NOW)
    store_reads(db, pass_id, reads_for(params, "EUR", (push(6), push(5, identifier="S2"))))

    hour = 0.5 ** (1 / (7 * 24))
    decayed = (
        Contribution("S1", "a story", 6, 1, hour, "OPEN"),
        Contribution("S2", "a story", 5, 1, hour, "OPEN"),
    )
    current = reads_for(params, "EUR", decayed)
    changes = detect_changes(db, ("EUR",), current, previous_pass_id=pass_id)

    assert changes.unchanged == {"EUR"}


def test_decay_that_crosses_a_degree_band_marks_it_changed(db, params):
    """The word the paragraph uses would change, so the paragraph is re-authored."""
    pass_id = start_pass(db, "hourly", NOW)
    store_reads(db, pass_id, reads_for(params, "EUR", (push(4), push(3.1, identifier="S2"))))

    faded = (
        Contribution("S1", "a story", 4, 1, 0.9, "OPEN"),
        Contribution("S2", "a story", 3.1, 1, 0.9, "OPEN"),
    )
    changes = detect_changes(
        db, ("EUR",), reads_for(params, "EUR", faded), previous_pass_id=pass_id
    )

    assert changes.changed == {"EUR"}


def test_a_score_that_moved_visibly_with_the_same_situations_marks_it_changed(db, params):
    """Recency decay walks a read across the threshold with no new evidence at all."""
    pass_id = start_pass(db, "hourly", NOW)
    store_reads(db, pass_id, reads_for(params, "EUR", (push(6), push(5, identifier="S2"))))

    decayed = Contribution("S1", "a story", 6, 1, 0.5, "OPEN")
    current = reads_for(params, "EUR", (decayed, push(5, identifier="S2")))
    changes = detect_changes(db, ("EUR",), current, previous_pass_id=pass_id)

    assert changes.changed == {"EUR"}
    assert "score moved" in changes.reasons["EUR"]


def test_a_state_change_marks_the_entity_changed(db, params):
    pass_id = start_pass(db, "hourly", NOW)
    store_reads(db, pass_id, reads_for(params, "EUR", (push(6), push(5, identifier="S2"))))

    current = reads_for(params, "EUR", (push(6, -1), push(5, -1, identifier="S2")))
    changes = detect_changes(db, ("EUR",), current, previous_pass_id=pass_id)

    assert changes.changed == {"EUR"}


def test_a_resolved_situation_that_leaves_the_score_alone_still_marks_it_changed(db, params):
    """The contributor set is checked as well as the score, precisely for this case."""
    pass_id = start_pass(db, "hourly", NOW)
    store_reads(
        db,
        pass_id,
        reads_for(params, "EUR", (push(6), push(5, identifier="S2"), push(4, identifier="S3"))),
    )

    # S3 resolves and S4 opens at the same magnitude — the total is unchanged.
    current = reads_for(
        params, "EUR", (push(6), push(5, identifier="S2"), push(4, identifier="S4"))
    )
    changes = detect_changes(db, ("EUR",), current, previous_pass_id=pass_id)

    assert changes.changed == {"EUR"}


# --- Carry forward --------------------------------------------------------------------------------


def test_a_carried_forward_narrative_keeps_its_real_authorship_time(db):
    """The interface must show how old the paragraph is, not how old the pass is."""
    first = start_pass(db, "hourly", NOW)
    second = start_pass(db, "hourly", NOW + timedelta(hours=1))
    authored = (NOW - timedelta(hours=3)).isoformat()

    with transaction(db):
        db.execute(
            "INSERT INTO narratives (pass_id, entity, prose, cited_ids, prompt, model,"
            " effort, authored_at) VALUES (?, 'EUR', 'the read', '[]', 'p', 'm', NULL, ?)",
            (first, authored),
        )
        db.execute(
            "INSERT INTO expectations (pass_id, entity, policy_state, directional_state,"
            " confidence, resolves_at, decisive_event_id, reason, event_ids, prompt, model,"
            " effort, authored_at)"
            " VALUES (?, 'EUR', 'NEUTRAL', 'NO_READ', 'LOW', NULL, NULL, 'x', '[]', 'p',"
            " 'm', NULL, ?)",
            (first, authored),
        )
        carry_forward(db, second, first, {"EUR"})

    row = db.execute(
        "SELECT prose, authored_at FROM narratives WHERE pass_id = ?", (second,)
    ).fetchone()
    assert row["prose"] == "the read"
    assert row["authored_at"] == authored


def test_carrying_forward_nothing_is_harmless(db):
    first = start_pass(db, "hourly", NOW)
    second = start_pass(db, "hourly", NOW + timedelta(hours=1))

    with transaction(db):
        carry_forward(db, second, first, set())

    assert db.execute("SELECT count(*) FROM narratives").fetchone()[0] == 0


# --- Through a real pass --------------------------------------------------------------------------


@pytest.fixture
def feeds(fixture_body):
    def _fetch(url: str) -> FetchedBody:
        name = "calendar_good.json" if "calendar" in url else "news_good.xml"
        return FetchedBody(text=fixture_body(name), resolved_url=url)

    return _fetch


@pytest.fixture
def stub():
    return StubClient(
        answers={
            "triage": [triage_keeping(0, 1)],
            "score": [
                score_touching(("USD", 1), magnitude=6, description="Fed pushing back"),
                score_touching(("USD", 1), magnitude=5, description="inflation above target"),
            ],
            "situations": [
                situations_opening(
                    ("Fed pushing back", "POLICY", 6, (("USD", 1),)),
                    ("inflation above target", "POLICY", 5, (("USD", 1),)),
                )
            ],
            "expectation": [expectation_of()],
            "narrative": [
                narrative_of(
                    "The read is moderately hawkish (S1, S2). Nothing argues against it.",
                    ["S1", "S2"],
                )
            ],
        }
    )


def test_a_quiet_hour_writes_no_paragraphs_at_all(db, universe, params, stub, feeds):
    """Without this rule an hourly cadence re-authors thirteen Opus paragraphs to say
    exactly what last hour's said."""
    stages = Stages(client=stub, models=params.models, prices=params.prices)
    run(db, universe, params, stages, trigger="hourly", now=NOW, fetcher=feeds)

    stub.answers["triage"] = [triage_keeping()]
    before = stub.count("narrative")

    second = run(
        db,
        universe,
        params,
        Stages(client=stub, models=params.models, prices=params.prices),
        trigger="hourly",
        now=NOW,
        fetcher=feeds,
    )

    assert stub.count("narrative") == before
    assert set(second.skipped) == set(universe.entities)


def test_an_unchanged_entity_keeps_its_stored_narrative(db, universe, params, stub, feeds):
    stages = Stages(client=stub, models=params.models, prices=params.prices)
    first = run(db, universe, params, stages, trigger="hourly", now=NOW, fetcher=feeds)

    stub.answers["triage"] = [triage_keeping()]
    second = run(
        db,
        universe,
        params,
        Stages(client=stub, models=params.models, prices=params.prices),
        trigger="hourly",
        now=NOW + timedelta(hours=1),
        fetcher=feeds,
    )

    original = db.execute(
        "SELECT prose, authored_at FROM narratives WHERE pass_id = ? AND entity = 'USD'",
        (first.pass_id,),
    ).fetchone()
    carried = db.execute(
        "SELECT prose, authored_at FROM narratives WHERE pass_id = ? AND entity = 'USD'",
        (second.pass_id,),
    ).fetchone()

    assert carried["prose"] == original["prose"]
    assert carried["authored_at"] == original["authored_at"]


def test_a_changed_entity_is_re_authored(db, universe, params, stub, feeds, fixture_body):
    stages = Stages(client=stub, models=params.models, prices=params.prices)
    run(db, universe, params, stages, trigger="hourly", now=NOW, fetcher=feeds)

    extended = fixture_body("news_good.xml").replace(
        "</channel>",
        "<item><title>BoJ holds</title><description>As expected.</description>"
        "<link>https://www.investinglive.com/boj-20260831</link>"
        "<pubDate>Mon, 31 Aug 2026 15:00:00 GMT</pubDate></item></channel>",
    )

    def more(url: str) -> FetchedBody:
        body = fixture_body("calendar_good.json") if "calendar" in url else extended
        return FetchedBody(text=body, resolved_url=url)

    stub.answers["triage"] = [triage_keeping(0)]
    stub.answers["score"] = [
        score_touching(("JPY", -1), axis="POLICY", magnitude=8, description="BoJ holds")
    ]
    stub.answers["situations"] = [situations_opening(("BoJ holds", "POLICY", 8, (("JPY", -1),)))]

    second = run(
        db,
        universe,
        params,
        Stages(client=stub, models=params.models, prices=params.prices),
        trigger="hourly",
        now=NOW + timedelta(hours=1),
        fetcher=more,
    )

    assert "JPY" in second.reads
    assert "JPY" not in second.skipped
    assert "USD" in second.skipped


def test_a_forced_refresh_skips_nothing(db, universe, params, stub, feeds):
    """Change detection is not consulted at all, not merely overridden."""
    stages = Stages(client=stub, models=params.models, prices=params.prices)
    run(db, universe, params, stages, trigger="hourly", now=NOW, fetcher=feeds)

    stub.answers["triage"] = [triage_keeping()]
    forced = run(
        db,
        universe,
        params,
        Stages(client=stub, models=params.models, prices=params.prices),
        trigger="forced",
        now=NOW,
        fetcher=feeds,
        forced=True,
    )

    assert forced.skipped == []
    assert set(forced.narratives) == set(universe.entities)
    assert set(forced.expectations) == set(universe.entities)


def test_a_forced_refresh_bypasses_the_replay_cache(db, universe, params, stub, feeds):
    """The one path that always costs full price."""
    stages = Stages(client=stub, models=params.models, prices=params.prices)
    run(
        db,
        universe,
        params,
        stages,
        trigger="forced",
        now=NOW,
        fetcher=feeds,
        forced=True,
    )

    salts = [call["cache_salt"] for call in stub.calls if call["stage"] == "narrative"]

    assert salts and all(salt and salt.startswith("forced:") for salt in salts)


def test_a_non_forced_pass_uses_no_cache_salt(db, universe, params, stub, feeds):
    stages = Stages(client=stub, models=params.models, prices=params.prices)
    run(db, universe, params, stages, trigger="hourly", now=NOW, fetcher=feeds)

    assert all(call["cache_salt"] is None for call in stub.calls)


def test_the_trigger_and_the_skipped_count_are_recorded_on_the_pass(
    db, universe, params, stub, feeds
):
    stages = Stages(client=stub, models=params.models, prices=params.prices)
    run(db, universe, params, stages, trigger="hourly", now=NOW, fetcher=feeds)

    stub.answers["triage"] = [triage_keeping()]
    second = run(
        db,
        universe,
        params,
        Stages(client=stub, models=params.models, prices=params.prices),
        trigger="release",
        now=NOW,
        fetcher=feeds,
        trigger_events=("e1", "e2"),
    )

    row = db.execute("SELECT * FROM passes WHERE id = ?", (second.pass_id,)).fetchone()

    assert row["trigger"] == "release"
    assert json.loads(row["trigger_events"]) == ["e1", "e2"]
    assert len(json.loads(row["skipped_entities"])) == 13


# --- The loop -------------------------------------------------------------------------------------


def make_loop(tmp_path, universe, params, stub, feeds, clock=None):
    path = tmp_path / "engine.db"
    return Loop(
        connection_factory=lambda: connect(path),
        universe=universe,
        params=params,
        stages_factory=lambda: Stages(client=stub, models=params.models, prices=params.prices),
        fetcher=feeds,
        clock=clock or (lambda: NOW),
    )


def test_the_scheduler_refuses_to_start_while_disabled(tmp_path, universe, params, stub, feeds):
    """Not starting is a correct state, not an error — a fresh install must not look broken."""
    from dataclasses import replace

    off = replace(params, scheduler_enabled=False)
    loop = make_loop(tmp_path, universe, off, stub, feeds)

    assert loop.start() is False
    assert loop.scheduler is None


def test_a_forced_refresh_runs_while_the_scheduler_is_disabled(
    tmp_path, universe, params, stub, feeds
):
    from dataclasses import replace

    loop = make_loop(tmp_path, universe, replace(params, scheduler_enabled=False), stub, feeds)
    loop.start()

    result = loop.forced()

    assert result is not None
    assert result.status == "complete"


def test_an_hourly_tick_runs_a_full_pass(tmp_path, universe, params, stub, feeds):
    loop = make_loop(tmp_path, universe, params, stub, feeds)

    result = loop.hourly()

    assert result.trigger == "hourly"
    assert set(result.reads) == set(universe.entities)


def test_a_failed_tick_logs_and_returns_rather_than_raising(tmp_path, universe, params, feeds):
    """Raising on the scheduler's thread can take the job out and leave the loop silently dead."""
    broken = StubClient(answers={"triage": [triage_keeping(0)]}, fail_on={"score"})
    loop = make_loop(tmp_path, universe, params, broken, feeds)

    assert loop.hourly() is None


def test_the_next_tick_runs_normally_after_a_failure(tmp_path, universe, params, stub, feeds):
    """No retry within the tick, and no lock left needing to be cleared."""
    broken = StubClient(answers={"triage": [triage_keeping(0)]}, fail_on={"score"})
    path = tmp_path / "engine.db"
    loop = Loop(
        connection_factory=lambda: connect(path),
        universe=universe,
        params=params,
        stages_factory=lambda: Stages(client=broken, models=params.models, prices=params.prices),
        fetcher=feeds,
        clock=lambda: NOW,
    )
    assert loop.hourly() is None

    loop.stages_factory = lambda: Stages(client=stub, models=params.models, prices=params.prices)
    assert loop.hourly() is not None


def test_a_missed_release_trigger_runs_once_on_startup(tmp_path, universe, params, stub, feeds):
    path = tmp_path / "engine.db"
    connection = connect(path)
    add_event(connection, ident="e1", currency="USD", when=NOW - timedelta(hours=1))
    connection.close()

    loop = make_loop(tmp_path, universe, params, stub, feeds)
    missed = loop.catch_up()

    assert [trigger.events for trigger in missed] == [("e1",)]

    connection = connect(path)
    try:
        row = connection.execute(
            "SELECT trigger, trigger_events FROM passes ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        connection.close()
    assert row["trigger"] == "release"
    assert json.loads(row["trigger_events"]) == ["e1"]


def test_a_missed_trigger_is_not_run_twice(tmp_path, universe, params, stub, feeds):
    path = tmp_path / "engine.db"
    connection = connect(path)
    add_event(connection, ident="e1", currency="USD", when=NOW - timedelta(hours=1))
    connection.close()

    loop = make_loop(tmp_path, universe, params, stub, feeds)
    loop.catch_up()
    stub.answers["triage"] = [triage_keeping()]

    assert loop.catch_up() == []


def test_the_hourly_cadence_follows_the_configured_interval(
    tmp_path, universe, params, stub, feeds
):
    """The scheduler must keep the cadence the board says it keeps.

    Every page computes its staleness from `HOURLY_MINUTES`, and the cadence is the main
    lever on what the loop costs to run. A scheduler pinned to the top of the hour would
    make both of those statements false without anything on the board looking wrong.
    """
    from dataclasses import replace

    loop = make_loop(
        tmp_path, universe, replace(params, hourly_minutes=240, scheduler_enabled=True), stub, feeds
    )
    assert loop.start() is True
    try:
        interval = loop.scheduler.get_job("hourly").trigger.interval
        assert interval.total_seconds() == 240 * 60
    finally:
        loop.stop()


def test_release_only_schedules_no_tick_but_keeps_the_releases(
    tmp_path, universe, params, stub, feeds
):
    """The whole point of the mode: the expensive clock goes, the events stay armed."""
    from dataclasses import replace

    loop = make_loop(
        tmp_path, universe, replace(params, hourly_minutes=0, scheduler_enabled=True), stub, feeds
    )
    assert loop.start() is True
    try:
        assert loop.scheduler.get_job("hourly") is None
        assert loop.scheduler.get_job("release-scan") is not None
    finally:
        loop.stop()


def test_a_forced_refresh_still_works_with_no_tick(tmp_path, universe, params, stub, feeds):
    """Release-only leans on the manual refresh, so it had better run."""
    from dataclasses import replace

    loop = make_loop(tmp_path, universe, replace(params, hourly_minutes=0), stub, feeds)

    assert loop.forced() is not None
