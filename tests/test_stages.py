"""The stage parsers, against recorded replies and against replies that went wrong.

Strictness is the point. A stage that half-understands a reply produces a read that looks
completely normal on the board and is wrong, so every parser here is checked to raise
rather than to fill in a plausible default.
"""

from __future__ import annotations

import pytest

from engine.model.client import ModelReply
from engine.reading.states import Axis, State
from engine.stages import (
    STAGE_SKILLS,
    Ledger,
    StageError,
    load_skill,
    parse_expectation,
    parse_narrative,
    parse_score,
    parse_situations,
    parse_triage,
)

ENTITIES = (
    "AUD",
    "CAD",
    "EUR",
    "GBP",
    "JPY",
    "NZD",
    "USD",
    "XAU",
    "XAG",
    "WTI",
    "SPX500",
    "NAS100",
    "US30",
)


# --- Skills are plain files ------------------------------------------------------------


@pytest.mark.parametrize("stage", sorted(STAGE_SKILLS))
def test_every_stage_has_a_readable_skill_file(stage):
    assert load_skill(stage).strip()


def test_an_unbound_stage_is_an_error_not_an_empty_prompt(tmp_path):
    with pytest.raises(StageError, match="no skill bound"):
        load_skill("reflection")


def test_a_missing_skill_file_is_an_error_not_an_empty_prompt(tmp_path):
    with pytest.raises(StageError, match="is missing"):
        load_skill("triage", directory=tmp_path)


# --- Triage ----------------------------------------------------------------------------


def test_triage_returns_the_kept_indices():
    assert parse_triage('{"keep": [0, 2, 5]}', count=6) == (0, 2, 5)


def test_triage_accepts_a_fenced_block():
    assert parse_triage('```json\n{"keep": [1]}\n```', count=3) == (1,)


def test_triage_may_keep_nothing():
    """A quiet feed is a normal answer, not a failure."""
    assert parse_triage('{"keep": []}', count=4) == ()


def test_triage_drops_indices_that_were_never_sent():
    """The cheap stage naming a headline that does not exist loses nothing."""
    assert parse_triage('{"keep": [0, 99, -1]}', count=3) == (0,)


def test_triage_deduplicates_and_orders():
    assert parse_triage('{"keep": [2, 0, 2]}', count=3) == (0, 2)


def test_triage_raises_on_a_missing_key():
    with pytest.raises(StageError, match="`keep` is missing"):
        parse_triage('{"kept": [1]}', count=3)


def test_triage_raises_on_a_malformed_reply():
    with pytest.raises(StageError, match="not JSON"):
        parse_triage("I kept headlines 1 and 4.", count=5)


def test_an_empty_reply_is_an_error_not_an_empty_answer():
    """A reasoning model can spend its whole budget thinking and return no text."""
    with pytest.raises(StageError, match="returned no text"):
        parse_triage("   ", count=3)


# --- Scoring ---------------------------------------------------------------------------

GOOD_SCORE = """
{"touches": [
  {"entities": [{"entity": "EUR", "polarity": "POSITIVE"}],
   "axis": "POLICY", "magnitude": 5,
   "description": "ECB officials pushing back on 2027 cut pricing"}
]}
"""


def test_scoring_parses_a_single_touch():
    (touch,) = parse_score(GOOD_SCORE, ENTITIES)

    assert touch.entities == (("EUR", 1),)
    assert touch.axis is Axis.POLICY
    assert touch.magnitude == 5
    assert touch.description.startswith("ECB officials")


def test_one_story_can_carry_opposite_polarities():
    """The risk-off case, which is why polarity lives per entity and not per situation."""
    reply = """
    {"touches": [{"entities": [{"entity": "JPY", "polarity": "POSITIVE"},
                               {"entity": "AUD", "polarity": "NEGATIVE"},
                               {"entity": "SPX500", "polarity": "NEGATIVE"}],
                  "axis": "DIRECTIONAL", "magnitude": 6,
                  "description": "flight to quality on the border escalation"}]}
    """

    (touch,) = parse_score(reply, ENTITIES)

    assert dict(touch.entities) == {"JPY": 1, "AUD": -1, "SPX500": -1}


def test_scoring_may_touch_nothing():
    assert parse_score('{"touches": []}', ENTITIES) == ()


def test_an_entity_outside_the_universe_is_dropped():
    """The config is the universe; a read against something nobody trades has nowhere to go."""
    reply = """
    {"touches": [{"entities": [{"entity": "CHF", "polarity": "POSITIVE"},
                               {"entity": "EUR", "polarity": "NEGATIVE"}],
                  "axis": "POLICY", "magnitude": 4, "description": "SNB intervention talk"}]}
    """

    (touch,) = parse_score(reply, ENTITIES)

    assert touch.entities == (("EUR", -1),)


def test_a_touch_naming_only_unknown_entities_is_dropped_entirely():
    reply = """
    {"touches": [{"entities": [{"entity": "CHF", "polarity": "POSITIVE"}],
                  "axis": "POLICY", "magnitude": 4, "description": "SNB intervention talk"}]}
    """

    assert parse_score(reply, ENTITIES) == ()


@pytest.mark.parametrize(
    ("magnitude", "message"),
    [(0, "outside"), (11, "outside"), ("high", "not a number"), (None, "not a number")],
)
def test_a_magnitude_outside_one_to_ten_is_refused(magnitude, message):
    reply = (
        '{"touches": [{"entities": [{"entity": "EUR", "polarity": "POSITIVE"}],'
        f'  "axis": "POLICY", "magnitude": {magnitude if magnitude is not None else "null"},'
        '  "description": "x"}]}'
    ).replace('"high"', '"high"')
    if isinstance(magnitude, str):
        reply = reply.replace(f"{magnitude}", f'"{magnitude}"')

    with pytest.raises(StageError, match=message):
        parse_score(reply, ENTITIES)


def test_an_unknown_axis_is_refused():
    reply = (
        '{"touches": [{"entities": [{"entity": "EUR", "polarity": "POSITIVE"}],'
        '  "axis": "SENTIMENT", "magnitude": 4, "description": "x"}]}'
    )

    with pytest.raises(StageError, match="POLICY or DIRECTIONAL"):
        parse_score(reply, ENTITIES)


def test_an_unknown_polarity_is_refused():
    reply = (
        '{"touches": [{"entities": [{"entity": "EUR", "polarity": "MIXED"}],'
        '  "axis": "POLICY", "magnitude": 4, "description": "x"}]}'
    )

    with pytest.raises(StageError, match="POSITIVE or NEGATIVE"):
        parse_score(reply, ENTITIES)


def test_a_touch_with_no_description_is_refused():
    """The description is what a later stage sees instead of the headline."""
    reply = (
        '{"touches": [{"entities": [{"entity": "EUR", "polarity": "POSITIVE"}],'
        '  "axis": "POLICY", "magnitude": 4, "description": "  "}]}'
    )

    with pytest.raises(StageError, match="no description"):
        parse_score(reply, ENTITIES)


# --- Situations ------------------------------------------------------------------------


def test_situations_parses_the_three_actions():
    reply = """
    {"updates": [
      {"action": "update", "identifier": "S4", "magnitude": 7, "status": "ESCALATING",
       "note": "second official in a week"},
      {"action": "resolve", "identifier": "S9", "status": "RESOLVED_TEMP",
       "note": "the decision landed"},
      {"action": "open", "description": "OPEC+ signalling a supply extension",
       "axis": "DIRECTIONAL", "magnitude": 6,
       "entities": [{"entity": "WTI", "polarity": "POSITIVE"}]}
    ]}
    """

    updates = parse_situations(reply)

    assert [u.action for u in updates] == ["update", "resolve", "open"]
    assert updates[0].identifier == "S4"
    assert updates[0].magnitude == 7
    assert updates[2].entities == (("WTI", 1),)
    assert updates[2].axis is Axis.DIRECTIONAL


def test_an_identifier_is_normalised_to_upper_case():
    reply = '{"updates": [{"action": "update", "identifier": "s4", "status": "OPEN"}]}'

    assert parse_situations(reply)[0].identifier == "S4"


def test_an_update_without_an_identifier_is_refused():
    """The whole anti-double-counting mechanism is that updates name a situation."""
    reply = '{"updates": [{"action": "update", "status": "OPEN", "note": "the ECB story"}]}'

    with pytest.raises(StageError, match="names no identifier"):
        parse_situations(reply)


def test_an_open_that_names_no_entities_is_refused():
    reply = (
        '{"updates": [{"action": "open", "description": "x", "axis": "POLICY", "magnitude": 4}]}'
    )

    with pytest.raises(StageError, match="names no entities"):
        parse_situations(reply)


def test_an_unknown_action_is_refused():
    reply = '{"updates": [{"action": "merge", "identifier": "S4"}]}'

    with pytest.raises(StageError, match="unknown action"):
        parse_situations(reply)


def test_situations_may_return_nothing():
    assert parse_situations('{"updates": []}') == ()


# --- Expectation -----------------------------------------------------------------------

GOOD_EXPECTATION = """
{"policy": "POSITIVE", "directional": "NEUTRAL", "confidence": "HIGH",
 "decisive_event_id": "a1b2c3d4e5f60718",
 "reason": "Thursday's ECB decision is the week.",
 "event_ids": ["a1b2c3d4e5f60718", "1122334455667788"]}
"""


def test_expectation_parses_both_axes_and_the_decisive_event():
    answer = parse_expectation(GOOD_EXPECTATION)

    assert answer["policy"] is State.POSITIVE
    assert answer["directional"] is State.NEUTRAL
    assert answer["confidence"] == "HIGH"
    assert answer["decisive_event_id"] == "a1b2c3d4e5f60718"
    assert len(answer["event_ids"]) == 2


def test_expectation_may_return_no_read_on_an_axis():
    """An axis with nothing scheduled is unreadable, and that is a value, not an absence."""
    reply = GOOD_EXPECTATION.replace('"directional": "NEUTRAL"', '"directional": "NO_READ"')

    assert parse_expectation(reply)["directional"] is State.NO_READ


def test_a_missing_state_raises_rather_than_defaulting_to_neutral():
    """Reporting an unanswered question as a balanced week is the failure to avoid."""
    reply = '{"policy": "POSITIVE", "confidence": "HIGH", "reason": "x", "event_ids": []}'

    with pytest.raises(StageError, match="not one of the four"):
        parse_expectation(reply)


def test_an_unknown_confidence_is_refused():
    reply = GOOD_EXPECTATION.replace('"HIGH"', '"PRETTY SURE"', 1)

    with pytest.raises(StageError, match="not HIGH, MEDIUM or LOW"):
        parse_expectation(reply)


def test_an_expectation_with_no_reason_is_refused():
    """The reason is shown to the operator verbatim; an empty one is a blank cell."""
    reply = GOOD_EXPECTATION.replace('"Thursday\'s ECB decision is the week."', '""')

    with pytest.raises(StageError, match="no reason"):
        parse_expectation(reply)


# --- Narrative -------------------------------------------------------------------------


def test_narrative_parses_prose_and_citations():
    reply = '{"prose": "EUR is moderately bearish (S12, S15).", "cited": ["S12", "s15"]}'

    answer = parse_narrative(reply)

    assert answer["prose"].startswith("EUR is moderately bearish")
    assert answer["cited"] == ("S12", "S15")


def test_a_narrative_may_cite_nothing():
    """A read with no contributing situations still gets a paragraph saying so."""
    answer = parse_narrative('{"prose": "USD has nothing on file on either axis.", "cited": []}')

    assert answer["cited"] == ()


def test_an_empty_paragraph_is_refused():
    with pytest.raises(StageError, match="no prose"):
        parse_narrative('{"prose": "   ", "cited": []}')


# --- The ledger ------------------------------------------------------------------------


class _Reply:
    def __init__(self, replayed: bool, model="claude-opus-5", inp=614, out=1005):
        self.replayed = replayed
        self.model = model
        self.input_tokens = inp
        self.output_tokens = out
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0


PRICES = {"claude-opus-5": {"input": 5.0, "output": 25.0}}


def test_a_billed_call_is_priced_from_its_tokens():
    ledger = Ledger()

    ledger.record("narrative", _Reply(replayed=False), PRICES)

    # 614 in at $5/Mtok plus 1005 out at $25/Mtok — output is roughly 90% of the bill.
    assert ledger.total_usd == pytest.approx(0.00307 + 0.025125, abs=1e-9)
    assert ledger.stages["narrative"].usd > 0.028
    assert ledger.stages["narrative"].billed_calls == 1


def test_a_replayed_call_is_counted_but_costs_nothing():
    """A pass that looks cheap because it was cached must be distinguishable from a quiet one."""
    ledger = Ledger()

    ledger.record("narrative", _Reply(replayed=True), PRICES)

    assert ledger.total_usd == 0.0
    assert ledger.stages["narrative"].replayed_calls == 1
    assert ledger.stages["narrative"].billed_calls == 0


# --- A bad reply must not poison the cache -------------------------------------------------


def test_an_unparseable_reply_is_evicted_from_the_replay_cache(tmp_path):
    """Found live: the expectation stage returned malformed JSON, which the cache had
    already stored. Every retry replayed the same broken object, so the pass could never
    recover — the strict parser was right and the run was permanently stuck.
    """
    from engine.model.cache import ReplayCache
    from engine.stages import Stages

    class _Client:
        def __init__(self):
            self.cache = ReplayCache(tmp_path)
            self.calls = 0

        def complete(self, **kwargs):
            self.calls += 1
            text = "{not json" if self.calls == 1 else '{"prose": "a b. c d.", "cited": []}'
            key = "deadbeef"
            self.cache.put(key, {"text": text})
            return ModelReply(
                text=text, model="m", replayed=False, input_tokens=1, output_tokens=1, cache_key=key
            )

    client = _Client()
    stages = Stages(client, {"NARRATIVE": "m"}, {})

    with pytest.raises(StageError):
        stages.narrative("sheet")
    assert not stages.client.cache.path_for("deadbeef").exists(), "bad reply left cached"

    # The retry is a genuinely fresh call, and it succeeds.
    assert stages.narrative("sheet").answer["prose"].startswith("a b")


def test_a_reply_that_parses_stays_cached(tmp_path):
    """Eviction is for failures only — replaying good answers is the cache's whole job."""
    from engine.model.cache import ReplayCache
    from engine.stages import Stages

    class _Client:
        def __init__(self):
            self.cache = ReplayCache(tmp_path)

        def complete(self, **kwargs):
            self.cache.put("goodkey", {"text": "x"})
            return ModelReply(
                text='{"prose": "a b. c d.", "cited": []}',
                model="m",
                replayed=False,
                input_tokens=1,
                output_tokens=1,
                cache_key="goodkey",
            )

    stages = Stages(_Client(), {"NARRATIVE": "m"}, {})
    stages.narrative("sheet")

    assert stages.client.cache.path_for("goodkey").exists()
