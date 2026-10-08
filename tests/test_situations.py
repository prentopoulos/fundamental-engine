"""The situation lifecycle: transitions, immediate resolution, ageing, and adherence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from engine.reading.states import Axis
from engine.situations.lifecycle import (
    DUPLICATION_WARNING,
    advance,
    age,
    apply_updates,
    contributing,
    current_set,
    entities_of,
    evidence_for,
    next_identifier,
    open_situation,
    resolve,
)
from engine.stages import SituationUpdate
from engine.store.schema import connect, transaction

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
FADE_AFTER = timedelta(days=4)
ARCHIVE_AFTER = timedelta(days=10)


@pytest.fixture
def db(tmp_path):
    connection = connect(tmp_path / "engine.db")
    yield connection
    connection.close()


def seed(
    db,
    description="ECB pushing back on cuts",
    axis=Axis.POLICY,
    magnitude=5.0,
    entities=(("EUR", 1),),
    when=NOW,
):
    with transaction(db):
        return open_situation(
            db,
            description=description,
            axis=axis,
            magnitude=magnitude,
            entities=entities,
            now=when,
        )


# --- Identifiers -----------------------------------------------------------------------


def test_identifiers_are_allocated_in_sequence(db):
    assert next_identifier(db) == "S1"
    assert seed(db) == "S1"
    assert seed(db, description="second") == "S2"


def test_an_identifier_is_never_re_used_after_a_situation_is_resolved(db):
    """A re-used identifier would point the old story's evidence trail at the new one."""
    seed(db)
    seed(db, description="second")
    with transaction(db):
        resolve(db, "S2", status="RESOLVED_PERM", now=NOW)

    assert next_identifier(db) == "S3"


# --- Opening and evidence --------------------------------------------------------------


def test_opening_records_the_description_axis_magnitude_and_polarities(db):
    identifier = seed(db, entities=(("JPY", 1), ("AUD", -1)))

    row = db.execute("SELECT * FROM situations WHERE identifier = ?", (identifier,)).fetchone()
    assert row["axis"] == "POLICY"
    assert row["magnitude"] == 5.0
    assert row["status"] == "OPEN"
    assert entities_of(db, identifier) == {"JPY": 1, "AUD": -1}


def test_evidence_is_retained_so_a_read_traces_to_source_records(db):
    with transaction(db):
        open_situation(
            db,
            description="x",
            axis=Axis.POLICY,
            magnitude=7,
            entities=(("EUR", 1),),
            now=NOW,
            evidence=(("headline", "abc123"), ("event", "def456")),
        )

    rows = db.execute("SELECT record_kind, record_id FROM situation_evidence").fetchall()
    assert {(r["record_kind"], r["record_id"]) for r in rows} == {
        ("headline", "abc123"),
        ("event", "def456"),
    }


def test_linking_the_same_evidence_twice_is_harmless(db):
    """A repeated poll must not duplicate the trail behind a story."""
    seed(db)
    with transaction(db):
        advance(db, "S1", now=NOW, evidence=(("headline", "abc123"),))
        advance(db, "S1", now=NOW, evidence=(("headline", "abc123"),))

    assert db.execute("SELECT count(*) FROM situation_evidence").fetchone()[0] == 1


def test_evidence_is_scoped_to_the_situation_it_actually_fed(db):
    """Found live: every headline in a pass was linked to every situation it touched, so a
    BOJ policy situation came back linked to "India gold imports double", 74 headlines deep.
    An operator asking why a situation carries magnitude 7 must get the records that answer
    it, not the whole batch.
    """
    from engine.stages import ScoredTouch

    jpy = (ScoredTouch((("JPY", 1),), Axis.POLICY, 7, "BoJ tightening"), "h-jpy")
    gold = (ScoredTouch((("XAU", 1),), Axis.DIRECTIONAL, 5, "India gold imports"), "h-gold")
    other = (ScoredTouch((("JPY", -1),), Axis.DIRECTIONAL, 4, "risk-off"), "h-risk")

    linked = evidence_for(Axis.POLICY, {"JPY"}, (jpy, gold, other))

    assert linked == (("headline", "h-jpy"),)


def test_evidence_matching_needs_both_the_axis_and_the_entity(db):
    from engine.stages import ScoredTouch

    touches = (
        (ScoredTouch((("EUR", 1),), Axis.POLICY, 6, "ECB"), "h1"),
        (ScoredTouch((("EUR", 1),), Axis.DIRECTIONAL, 6, "growth"), "h2"),
        (ScoredTouch((("USD", 1),), Axis.POLICY, 6, "Fed"), "h3"),
    )

    assert evidence_for(Axis.POLICY, {"EUR"}, touches) == (("headline", "h1"),)
    assert evidence_for(Axis.DIRECTIONAL, {"EUR"}, touches) == (("headline", "h2"),)


def test_an_opened_situation_links_only_its_own_headline(db):
    from engine.stages import ScoredTouch

    touches = (
        (ScoredTouch((("WTI", 1),), Axis.DIRECTIONAL, 6, "OPEC supply"), "h-oil"),
        (ScoredTouch((("EUR", 1),), Axis.POLICY, 6, "ECB"), "h-ecb"),
    )
    apply_updates(
        db,
        (
            SituationUpdate(
                action="open",
                description="OPEC supply extension",
                axis=Axis.DIRECTIONAL,
                magnitude=6,
                entities=(("WTI", 1),),
            ),
        ),
        now=NOW,
        warning_count=15,
        touches=touches,
    )

    linked = [r["record_id"] for r in db.execute("SELECT record_id FROM situation_evidence")]
    assert linked == ["h-oil"]


# --- Transitions -----------------------------------------------------------------------


def test_an_update_advances_the_last_evidence_time(db):
    seed(db)
    later = NOW + timedelta(hours=6)

    with transaction(db):
        advance(db, "S1", now=later, magnitude=6)

    row = db.execute("SELECT * FROM situations WHERE identifier = 'S1'").fetchone()
    assert row["last_evidence_at"] == later.isoformat()
    assert row["magnitude"] == 6


def test_evidence_time_never_moves_backwards(db):
    """Found live: a replay walking the archive matched August headlines to situations
    opened in September and aged them three weeks in one step, which would have faded live
    stories out of the read. `last_evidence_at` answers "when was this last written about",
    and that cannot go backwards whatever order the evidence arrives in.
    """
    seed(db, when=NOW)
    older = NOW - timedelta(days=21)

    with transaction(db):
        advance(db, "S1", now=older, magnitude=8)

    row = db.execute("SELECT * FROM situations WHERE identifier = 'S1'").fetchone()
    assert row["last_evidence_at"] == NOW.isoformat()
    # The update itself still lands — only the evidence clock is held.
    assert row["magnitude"] == 8
    assert row["updated_at"] == older.isoformat()


def test_older_evidence_does_not_revive_a_faded_situation_into_freshness(db):
    seed(db, when=NOW - timedelta(days=5))
    age(db, now=NOW, fade_after=FADE_AFTER)

    with transaction(db):
        advance(db, "S1", now=NOW - timedelta(days=20))

    assert (
        db.execute("SELECT last_evidence_at FROM situations").fetchone()[0]
        == (NOW - timedelta(days=5)).isoformat()
    )


def test_a_story_that_gets_bigger_escalates(db):
    seed(db)

    with transaction(db):
        advance(db, "S1", now=NOW, magnitude=8, status="ESCALATING")

    assert db.execute("SELECT status FROM situations").fetchone()[0] == "ESCALATING"


def test_silence_past_fade_after_marks_a_situation_fading(db):
    seed(db, when=NOW - timedelta(days=5))

    faded = age(db, now=NOW, fade_after=FADE_AFTER)

    assert faded == ["S1"]
    assert db.execute("SELECT status FROM situations").fetchone()[0] == "FADING"


def test_a_situation_with_recent_evidence_does_not_fade(db):
    seed(db, when=NOW - timedelta(days=1))

    assert age(db, now=NOW, fade_after=FADE_AFTER) == []


def test_new_evidence_brings_a_fading_situation_back(db):
    """A story with new evidence is not fading, whatever it was a moment ago."""
    seed(db, when=NOW - timedelta(days=5))
    age(db, now=NOW, fade_after=FADE_AFTER)

    with transaction(db):
        advance(db, "S1", now=NOW)

    assert db.execute("SELECT status FROM situations").fetchone()[0] == "OPEN"


# --- Resolution is immediate -----------------------------------------------------------


def test_a_resolved_situation_stops_contributing_in_the_same_pass(db):
    """No decay period. A resolved story that keeps pushing a read is a read of nothing."""
    seed(db)
    assert len(contributing(db, "EUR", Axis.POLICY, now=NOW, archive_after=ARCHIVE_AFTER)) == 1

    with transaction(db):
        resolve(db, "S1", status="RESOLVED_TEMP", now=NOW)

    assert contributing(db, "EUR", Axis.POLICY, now=NOW, archive_after=ARCHIVE_AFTER) == []


def test_a_resolved_situation_stays_readable_in_the_archive(db):
    seed(db)
    with transaction(db):
        resolve(db, "S1", status="RESOLVED_PERM", now=NOW)

    assert len(current_set(db, include_resolved=True)) == 1
    assert current_set(db) == []


def test_a_situation_past_archive_after_leaves_the_current_read_but_keeps_its_row(db):
    seed(db, when=NOW - timedelta(days=12))

    assert contributing(db, "EUR", Axis.POLICY, now=NOW, archive_after=ARCHIVE_AFTER) == []
    assert db.execute("SELECT count(*) FROM situations").fetchone()[0] == 1


def test_contributing_is_scoped_to_one_axis(db):
    """The axes are independent: a policy situation never reaches a directional score."""
    seed(db, axis=Axis.POLICY)
    seed(db, description="growth", axis=Axis.DIRECTIONAL)

    policy = contributing(db, "EUR", Axis.POLICY, now=NOW, archive_after=ARCHIVE_AFTER)
    directional = contributing(db, "EUR", Axis.DIRECTIONAL, now=NOW, archive_after=ARCHIVE_AFTER)

    assert [row["identifier"] for row in policy] == ["S1"]
    assert [row["identifier"] for row in directional] == ["S2"]


def test_one_story_contributes_opposite_polarities_to_two_entities(db):
    seed(
        db,
        description="flight to quality",
        axis=Axis.DIRECTIONAL,
        entities=(("JPY", 1), ("AUD", -1)),
    )

    jpy = contributing(db, "JPY", Axis.DIRECTIONAL, now=NOW, archive_after=ARCHIVE_AFTER)
    aud = contributing(db, "AUD", Axis.DIRECTIONAL, now=NOW, archive_after=ARCHIVE_AFTER)

    assert jpy[0]["polarity"] == 1
    assert aud[0]["polarity"] == -1


# --- Applying a stage's updates, and adherence ------------------------------------------


def test_an_adherent_response_applies_and_scores_one_hundred_percent(db):
    seed(db)
    seed(db, description="periphery spreads")

    result = apply_updates(
        db,
        (
            SituationUpdate(action="update", identifier="S1", magnitude=7, status="ESCALATING"),
            SituationUpdate(action="resolve", identifier="S2", status="RESOLVED_TEMP"),
        ),
        now=NOW,
        warning_count=15,
    )

    assert result.updated == ["S1"]
    assert result.resolved == ["S2"]
    assert result.rejected == []
    assert result.adherence == 1.0


def test_an_identifier_that_resolves_to_nothing_is_rejected(db):
    """Accepting it would hide exactly the failure adherence is measured to catch."""
    seed(db)

    result = apply_updates(
        db,
        (
            SituationUpdate(action="update", identifier="S1", magnitude=6),
            SituationUpdate(action="update", identifier="S99", magnitude=6),
        ),
        now=NOW,
        warning_count=15,
    )

    assert result.rejected == ["S99"]
    assert result.adherence == 0.5
    assert db.execute("SELECT count(*) FROM situations").fetchone()[0] == 1


def test_a_rejected_identifier_does_not_open_a_situation_in_its_place(db):
    apply_updates(
        db, (SituationUpdate(action="update", identifier="S7"),), now=NOW, warning_count=15
    )

    assert db.execute("SELECT count(*) FROM situations").fetchone()[0] == 0


def test_an_open_allocates_the_next_identifier_rather_than_taking_one(db):
    seed(db)

    result = apply_updates(
        db,
        (
            SituationUpdate(
                action="open",
                description="OPEC+ supply extension",
                axis=Axis.DIRECTIONAL,
                magnitude=6,
                entities=(("WTI", 1),),
            ),
        ),
        now=NOW,
        warning_count=15,
    )

    assert result.opened == ["S2"]
    assert entities_of(db, "S2") == {"WTI": 1}


def test_a_pass_with_no_named_identifiers_reports_full_adherence(db):
    """Nothing was named, so nothing failed to resolve. Not a defect signal."""
    result = apply_updates(db, (), now=NOW, warning_count=15)

    assert result.adherence == 1.0


def test_too_many_open_situations_on_one_entity_records_a_warning(db):
    """The signal that the identifier protocol is leaking and a story is being re-opened."""
    for index in range(4):
        seed(db, description=f"story {index}")

    result = apply_updates(db, (), now=NOW, warning_count=3)

    assert result.warnings == [f"{DUPLICATION_WARNING}:EUR:4"]


def test_no_warning_below_the_ceiling(db):
    for index in range(3):
        seed(db, description=f"story {index}")

    assert apply_updates(db, (), now=NOW, warning_count=3).warnings == []


def test_resolved_situations_do_not_count_towards_the_duplication_warning(db):
    for index in range(4):
        seed(db, description=f"story {index}")
    with transaction(db):
        resolve(db, "S1", status="RESOLVED_TEMP", now=NOW)
        resolve(db, "S2", status="RESOLVED_TEMP", now=NOW)

    assert apply_updates(db, (), now=NOW, warning_count=3).warnings == []
