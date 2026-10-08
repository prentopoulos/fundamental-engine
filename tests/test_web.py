"""The interface: loopback only, read-only, no prices, and never the word "neutral" for
an axis that cannot be read.

Rendered against a real stored pass rather than against fixtures of the view models, so a
template that stopped receiving a field fails here rather than in front of the operator.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from engine.feeds.transport import FetchedBody
from engine.passes import run
from engine.stages import Stages
from engine.store.schema import connect, transaction
from engine.universe.config import load_params, load_universe
from engine.web.app import create_app
from tests.stub_model import (
    StubClient,
    expectation_of,
    narrative_of,
    score_touching,
    situations_opening,
    triage_keeping,
)

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
TEMPLATES = Path("engine/web/templates")

# TestClient reports a client host of "testclient" unless told otherwise, which the
# loopback middleware correctly refuses. Every fixture below therefore presents itself as
# loopback — and one test deliberately does not.
LOOPBACK = ("127.0.0.1", 51234)
ELSEWHERE = ("10.0.0.7", 51234)


@pytest.fixture(scope="module")
def params():
    return load_params()


@pytest.fixture(scope="module")
def universe():
    return load_universe()


@pytest.fixture
def seeded(tmp_path, fixture_body, params, universe):
    """A database holding one completed pass, produced by the real chain over fixtures."""
    path = tmp_path / "engine.db"
    connection = connect(path)

    def feeds(url: str) -> FetchedBody:
        name = "calendar_good.json" if "calendar" in url else "news_good.xml"
        return FetchedBody(text=fixture_body(name), resolved_url=url)

    stub = StubClient(
        answers={
            "triage": [triage_keeping(0, 1)],
            "score": [
                score_touching(("USD", 1), magnitude=6, description="Fed pushing back on cuts"),
                score_touching(("USD", 1), magnitude=5, description="inflation above target"),
            ],
            "situations": [
                situations_opening(
                    ("Fed pushing back on cuts", "POLICY", 6, (("USD", 1),)),
                    ("inflation above target", "POLICY", 5, (("USD", 1),)),
                )
            ],
            "expectation": [expectation_of(policy="NEGATIVE", confidence="HIGH")],
            "narrative": [
                narrative_of(
                    "The read is moderately hawkish on policy (S1, S2). Thursday's decision "
                    "is expected to turn it dovish, so the read flips at that point.",
                    ["S1", "S2"],
                )
            ],
        }
    )
    run(
        connection,
        universe,
        params,
        Stages(client=stub, models=params.models, prices=params.prices),
        now=NOW,
        fetcher=feeds,
    )
    connection.close()
    return path


@pytest.fixture
def client(seeded, params, universe):
    app = create_app(
        params=params,
        universe=universe,
        connection_factory=lambda: connect(seeded),
        clock=lambda: NOW + timedelta(minutes=5),
    )
    return TestClient(app, client=LOOPBACK)


@pytest.fixture
def empty_client(tmp_path, params, universe):
    """No pass has ever completed. The interface must still render."""
    path = tmp_path / "empty.db"
    connect(path).close()
    app = create_app(
        params=params,
        universe=universe,
        connection_factory=lambda: connect(path),
        clock=lambda: NOW,
    )
    return TestClient(app, client=LOOPBACK)


# --- Loopback only ------------------------------------------------------------------------


def test_a_loopback_request_is_served(client):
    assert client.get("/").status_code == 200


def test_a_request_from_any_other_address_is_refused(seeded, params, universe):
    """Belt and braces: binding alone is one configuration away from being wrong."""
    app = create_app(
        params=params,
        universe=universe,
        connection_factory=lambda: connect(seeded),
        clock=lambda: NOW,
    )

    response = TestClient(app, client=ELSEWHERE).get("/")

    assert response.status_code == 403
    assert "loopback" in response.text


def test_the_server_entry_point_binds_to_loopback():
    source = Path("engine/web/app.py").read_text(encoding="utf-8")

    assert 'host="127.0.0.1"' in source
    assert "0.0.0.0" not in source


# --- No endpoint mutates a read --------------------------------------------------------------


def test_the_write_endpoints_are_exactly_refresh_and_chat(client):
    """Three POSTs, and none of them edits a read.

    `/refresh` re-derives the board from the feeds. `/chat` and `/chat/stream` are the same
    question answered two ways — one blocking, one streamed — and both only append to
    `chats`, which is a log beside the read rather than part of it. Anything else appearing
    here is a new way for the interface to change what the engine concluded, and is meant to
    fail this test until someone argues for it.
    """
    routes = [
        (route.path, sorted(route.methods - {"HEAD", "OPTIONS"}))
        for route in client.app.routes
        if getattr(route, "methods", None)
    ]
    writes = sorted(path for path, methods in routes if methods != ["GET"])

    assert writes == ["/chat", "/chat/stream", "/refresh"]


def test_the_interface_only_ever_inserts_into_the_chat_log():
    """The one table the interface writes to is its own. Reads are derived, never typed in."""
    source = Path("engine/web/app.py").read_text(encoding="utf-8")

    inserts = re.findall(r"INSERT INTO (\w+)", source)
    assert set(inserts) == {"chats"}


def test_no_endpoint_edits_a_score_a_situation_or_a_paragraph():
    """A board the operator has been editing is no longer a record of what the engine said."""
    source = Path("engine/web/app.py").read_text(encoding="utf-8")

    assert "UPDATE reads" not in source
    assert "UPDATE narratives" not in source
    assert "UPDATE situations" not in source
    assert "DELETE" not in source


# --- The board ------------------------------------------------------------------------------


def test_the_board_lists_every_configured_instrument(client, universe):
    body = client.get("/").text

    for instrument in universe.instruments:
        assert instrument.ticker in body


def test_the_board_shows_both_lanes_on_one_line(client):
    """The dot is now, the arrow is where the week pushes it. Both, on the same scale."""
    body = client.get("/").text

    assert "where it stands today" in body
    assert "bearish" in body and "neutral" in body and "bullish" in body
    assert 'class="dot' in body


def test_an_uncorroborated_axis_still_shows_what_is_on_file(seeded, params, universe):
    """Refusing to call one story a read is the rule. Hiding the number we computed is
    just losing information — the operator can see it leans, and that it is not a read.
    """
    from engine.passes import latest_pass

    connection = connect(seeded)
    pass_id = latest_pass(connection)["id"]
    with transaction(connection):
        connection.execute(
            "UPDATE reads SET score = -5.0, contributor_count = 1,"
            " no_read_reason = 'one story is not a read'"
            " WHERE pass_id = ? AND entity = 'CAD' AND axis = 'POLICY'",
            (pass_id,),
        )
    connection.close()

    app = create_app(
        params=params,
        universe=universe,
        connection_factory=lambda: connect(seeded),
        clock=lambda: NOW,
    )
    body = TestClient(app, client=LOOPBACK).get("/entity/CAD").text

    assert "no read" in body
    assert "one story is not a read" in body
    assert "-5.0" in body  # the number we computed is still shown
    assert "ghost" in body  # and drawn, hollowed rather than hidden


def test_a_cross_names_the_leg_that_blocks_it(client):
    """`no read` on a cross says nothing about which half is missing. Naming it turns a
    dead end into an answer: one more Canadian story fixes four crosses at once."""
    body = client.get("/").text

    assert "no read (CAD)" in body


def test_the_board_says_the_arrow_is_the_future_not_the_past(client):
    """A line running dot-to-arrowhead reads naturally as "it moved from here to there".
    It is the opposite — the dot is today and the arrow has not happened yet — and an
    operator reading it backwards has the whole board inverted."""
    body = client.get("/").text

    assert "Nothing here is history" in body
    assert "where it stands today" in body
    assert "not yet happened" in body


def test_each_track_is_labelled_so_the_two_dots_are_never_ambiguous(client):
    """Two dots twelve pixels apart with nothing naming them is what the operator
    actually hit. The subtitle explained it; a legend you have to remember is not one."""
    body = client.get("/").text

    assert 'class="track-label"' in body
    assert ">policy<" in body and ">direction<" in body


def test_every_row_draws_both_axes(client, universe, params, seeded):
    """Policy above the line, direction below — the pair is never collapsed to one mark."""
    from engine.passes import latest_pass
    from engine.web.views import instrument_rows

    connection = connect(seeded)
    try:
        rows = instrument_rows(connection, universe, params, latest_pass(connection)["id"], NOW)
    finally:
        connection.close()

    assert rows
    for row in rows:
        assert row.policy_track is not None
        assert row.directional_track is not None


def test_instruments_come_back_in_descending_divergence_rank(universe, params, seeded):
    from engine.passes import latest_pass
    from engine.web.views import instrument_rows

    connection = connect(seeded)
    try:
        rows = instrument_rows(connection, universe, params, latest_pass(connection)["id"], NOW)
    finally:
        connection.close()

    ranks = [row.rank for row in rows]
    assert ranks == sorted(ranks, reverse=True)


def test_the_board_groups_by_asset_class_in_configuration_order(client, universe):
    """Group order follows the config, not the ranking: a board whose sections move every
    hour takes away the operator's ability to reach for one."""
    body = client.get("/").text

    order = [body.index(label) for label in ("FOREX", "METALS &amp; ENERGY", "INDICES")]
    assert order == sorted(order)


def test_rank_still_orders_rows_within_a_group(client, universe, params, seeded):
    from engine.passes import latest_pass
    from engine.web.views import instrument_rows

    connection = connect(seeded)
    try:
        rows = instrument_rows(connection, universe, params, latest_pass(connection)["id"], NOW)
    finally:
        connection.close()

    body = client.get("/").text
    fx = [row for row in rows if row.group == "fx"]
    positions = [body.index(f">{row.ticker}</a>") for row in fx]
    assert positions == sorted(positions)


def test_the_moving_axis_is_capitalised_so_the_change_draws_the_eye(
    client, universe, params, seeded
):
    """Capitalisation survives a screenshot and a stylesheet that failed to load."""
    from engine.passes import latest_pass
    from engine.web.views import instrument_rows

    connection = connect(seeded)
    try:
        rows = instrument_rows(connection, universe, params, latest_pass(connection)["id"], NOW)
    finally:
        connection.close()

    moving = [row for row in rows if row.now_policy.moving or row.now_directional.moving]
    assert moving, "the fixture pass should produce at least one diverging instrument"
    for row in moving:
        cell = row.now_policy if row.now_policy.moving else row.now_directional
        assert cell.display == cell.words.upper()


# --- NO READ is never neutral ------------------------------------------------------------------


def test_an_unreadable_row_shows_no_read_and_a_reason(client):
    """EURNZD's NZD leg has nothing on file, so both its axes are unreadable."""
    body = client.get("/instrument/EURNZD").text

    assert "no read" in body
    assert "unreadable" in body


def test_no_unreadable_axis_is_ever_labelled_neutral(client, universe):
    """The guarantee the whole four-state vocabulary rests on, checked on rendered HTML."""
    for path in ["/"] + [f"/instrument/{i.ticker}" for i in universe.instruments]:
        body = client.get(path).text
        # Every `no read` span carries the blank class; no neutral-classed span may hold it.
        assert 'class="flat">no read' not in body
        assert 'class="flat moving">no read' not in body


def test_the_blank_class_is_reserved_for_unreadable_axes():
    """A styling slip that made `no read` look like a reading is a real failure mode."""
    macro = (TEMPLATES / "_cell.html").read_text(encoding="utf-8")

    assert "NO_READ" in macro
    assert macro.index("NO_READ") < macro.index("POSITIVE")


# --- The entity detail view ------------------------------------------------------------------


def test_the_narrative_appears_above_all_numeric_content(client):
    """A page that opens with a score table invites the operator to do the engine's job."""
    body = client.get("/entity/USD").text

    assert "moderately hawkish on policy (S1, S2)" in body
    assert body.index("moderately hawkish on policy") < body.index('class="ax"')


def test_the_detail_view_presents_its_sections_in_the_specified_order(client):
    body = client.get("/entity/USD").text
    order = [
        "moderately hawkish on policy",  # 1. the narrative
        'class="ax"',  # 2. the two axis reads
        "What is behind the read",  # 3. contributing situations
        "The pull this week",  # 4. what the week does about it
        "This week",  # 5. the in-window events
    ]
    positions = [body.index(marker) for marker in order]

    assert positions == sorted(positions)


def test_contributing_situations_show_magnitude_and_identifier(client):
    body = client.get("/entity/USD").text

    assert "S1" in body and "S2" in body
    assert "Fed pushing back on cuts" in body


def test_an_entity_with_nothing_on_file_says_so_rather_than_reading_neutral(client):
    body = client.get("/entity/NZD").text

    assert "no read" in body
    assert "nothing on file" in body


def test_the_detail_view_links_to_every_instrument_the_entity_touches(client):
    body = client.get("/entity/USD").text

    for ticker in ("EURUSD", "XAUUSD", "SPX500USD", "NAS100USD"):
        assert f'/instrument/{ticker}"' in body


def test_an_unknown_entity_returns_a_page_rather_than_an_error(client):
    response = client.get("/entity/CHF")

    assert response.status_code == 404
    assert "Nothing is stored" in response.text


# --- Navigation -------------------------------------------------------------------------------


def test_an_instrument_page_links_to_each_of_its_legs(client):
    body = client.get("/instrument/EURJPY").text

    assert '/entity/EUR"' in body
    assert '/entity/JPY"' in body


def test_instrument_pages_offer_forward_and_back_in_rank_order(client):
    body = client.get("/instrument/EURUSD").text

    assert body.count("/instrument/") >= 2
    assert "ArrowLeft" in body and "ArrowRight" in body


def test_entity_pages_offer_keyboard_navigation(client):
    body = client.get("/entity/USD").text

    assert "ArrowLeft" in body and "ArrowRight" in body


def test_the_board_offers_keyboard_navigation_in_rank_order(client):
    body = client.get("/").text

    assert "ArrowDown" in body and "ArrowUp" in body


# --- Age, cadence and triggers ------------------------------------------------------------------


def test_every_view_shows_the_age_of_the_read(client, universe):
    for path in ("/", "/entity/USD", "/instrument/EURUSD"):
        assert "read " in client.get(path).text


def test_a_read_older_than_the_interval_plus_the_margin_is_marked_stale(seeded, params, universe):
    late = create_app(
        params=params,
        universe=universe,
        connection_factory=lambda: connect(seeded),
        clock=lambda: NOW + timedelta(hours=4),
    )

    body = TestClient(late, client=LOOPBACK).get("/").text

    assert "stale" in body


def test_a_fresh_read_is_not_marked_stale(client):
    assert "· stale" not in client.get("/").text


def test_every_view_states_the_cadence_and_the_next_triggers(client, params):
    for path in ("/", "/entity/USD", "/instrument/EURUSD"):
        body = client.get(path).text
        assert f"auto: {params.cadence_words}" in body
        assert "next release" in body or "no release watched" in body


def test_the_board_states_whether_auto_is_running(client, params):
    """Either way it must be visible — a stalled loop and a disabled one look identical
    otherwise."""
    body = client.get("/").text

    assert f"auto: {params.cadence_words}" in body
    assert "Refresh now" in body


def test_the_next_release_trigger_is_the_scheduled_time_plus_the_delay(client, params):
    """Derived from the schedule alone; no released value is consulted."""
    body = client.get("/").text

    # The soonest watched High-impact fixture event is the ECB at 12:15 UTC on Thursday;
    # the trigger fires five minutes after the scheduled time.
    assert "next release EUR 12:20" in body


# --- Warnings and failures -----------------------------------------------------------------------


def test_a_pass_warning_surfaces_on_every_view(tmp_path, fixture_body, params, universe):
    path = tmp_path / "warned.db"
    connection = connect(path)

    def quiet(url: str) -> FetchedBody:
        body = "[]" if "calendar" in url else fixture_body("news_good.xml")
        return FetchedBody(text=body, resolved_url=url)

    stub = StubClient(
        answers={
            "triage": [triage_keeping()],
            "expectation": [expectation_of()],
            "narrative": [narrative_of("Nothing on file. There is no read to act on.")],
        }
    )
    run(
        connection,
        universe,
        params,
        Stages(client=stub, models=params.models, prices=params.prices),
        now=NOW,
        fetcher=quiet,
    )
    connection.close()

    app = create_app(
        params=params,
        universe=universe,
        connection_factory=lambda: connect(path),
        clock=lambda: NOW,
    )
    body = TestClient(app, client=LOOPBACK).get("/").text

    assert "empty_calendar_week" in body


def test_a_scheduled_pass_that_failed_is_surfaced_not_merely_stale(seeded, params, universe):
    """Stale reads an hour old are normal; a failure behind them is not."""
    connection = connect(seeded)
    with transaction(connection):
        connection.execute(
            "INSERT INTO passes (trigger, started_at, finished_at, status, failed_stage,"
            " failure) VALUES ('hourly', ?, ?, 'failed', 'triage', 'the feed went away')",
            ((NOW + timedelta(hours=1)).isoformat(), (NOW + timedelta(hours=1)).isoformat()),
        )
    connection.close()

    app = create_app(
        params=params,
        universe=universe,
        connection_factory=lambda: connect(seeded),
        clock=lambda: NOW + timedelta(hours=1),
    )
    body = TestClient(app, client=LOOPBACK).get("/").text

    assert "failed at stage" in body
    assert "triage" in body
    assert "the feed went away" in body
    assert "USDJPY" in body  # the prior board is still there


def test_a_failure_older_than_the_last_good_pass_is_not_surfaced(seeded, params, universe):
    """It was already recovered from; showing it would cry wolf on every later view."""
    connection = connect(seeded)
    with transaction(connection):
        connection.execute(
            "INSERT INTO passes (id, trigger, started_at, status, failed_stage)"
            " VALUES (0, 'hourly', ?, 'failed', 'ingest')",
            ((NOW - timedelta(hours=1)).isoformat(),),
        )
    connection.close()

    app = create_app(
        params=params,
        universe=universe,
        connection_factory=lambda: connect(seeded),
        clock=lambda: NOW,
    )
    body = TestClient(app, client=LOOPBACK).get("/").text

    assert "failed at stage" not in body


def test_an_interface_with_no_completed_pass_still_renders(empty_client):
    response = empty_client.get("/")

    assert response.status_code == 200
    assert "No pass has completed yet" in response.text


# --- The forced refresh --------------------------------------------------------------------------


def test_the_refresh_control_is_present_and_enabled(client):
    body = client.get("/").text

    assert 'action="/refresh"' in body
    assert "Refresh now" in body


def test_a_second_concurrent_refresh_does_not_start(seeded, params, universe):
    """The guard has to outlive the request: two clicks a second apart must not both run."""
    app = create_app(
        params=params,
        universe=universe,
        connection_factory=lambda: connect(seeded),
        clock=lambda: NOW,
    )
    app.state.refresh.begin()  # pretend one is already running

    client = TestClient(app, client=LOOPBACK)
    body = client.get("/").text
    assert "Refreshing…" in body
    assert "disabled" in body

    response = client.post("/refresh", follow_redirects=False)
    assert response.status_code == 303


def test_a_failed_refresh_names_the_stage_and_leaves_the_prior_reads_visible(
    seeded, params, universe, fixture_body
):
    def feeds(url: str) -> FetchedBody:
        name = "calendar_good.json" if "calendar" in url else "news_good.xml"
        return FetchedBody(text=fixture_body(name), resolved_url=url)

    broken = StubClient(answers={"triage": [triage_keeping(0)]}, fail_on={"score"})
    app = create_app(
        params=params,
        universe=universe,
        connection_factory=lambda: connect(seeded),
        stages_factory=lambda: Stages(client=broken, models=params.models, prices=params.prices),
        fetcher=feeds,
        clock=lambda: NOW + timedelta(minutes=5),
    )
    client = TestClient(app, client=LOOPBACK)

    # The seeded pass already consumed the fixture headlines, so force a fresh one through.
    connection = connect(seeded)
    connection.execute("DELETE FROM headlines")
    connection.close()

    client.post("/refresh", follow_redirects=False)
    body = client.get("/").text

    assert "failed at stage" in body
    assert "score" in body
    assert "USDJPY" in body  # the previous board is still there
    assert "read " in body  # at its true age


# --- The type scale ----------------------------------------------------------------------------


# The design's palette, from the Figma file. Asserted as a set so a colour added by hand
# in a template — rather than taken from a token — is caught.
DESIGN_TOKENS = (
    "--canvas",
    "--paper",
    "--wash",
    "--line",
    "--ink-1",
    "--ink-2",
    "--ink-3",
    "--red",
    "--green",
    "--amber",
    "--sans",
    "--mono",
)

# Inline styles that carry a *design* decision rather than a *computed position*. Geometry
# from the data (a dot's percentage along the scale) can only be inline; a font size or a
# colour there is a decision that escaped the stylesheet.
DESIGN_IN_STYLE = re.compile(r'style="[^"]*(font-size|font-family|color\s*:|background\s*:\s*#)')


def test_the_design_tokens_are_declared_in_one_place():
    """Every colour and family the two views use comes from the Figma palette, named once."""
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")

    for token in DESIGN_TOKENS:
        assert f"{token}:" in base, f"{token} is not declared"


@pytest.mark.parametrize(
    "template", sorted(p.name for p in Path("engine/web/templates").glob("*.html"))
)
def test_no_template_inlines_a_design_decision(template):
    """Computed geometry may be inline. A colour or a size may not."""
    body = (TEMPLATES / template).read_text(encoding="utf-8")

    offenders = DESIGN_IN_STYLE.findall(body)
    assert offenders == [], f"{template} inlines {offenders}"


def test_the_narrative_is_the_largest_thing_on_its_page():
    """The paragraph is what the page is for; only the entity's name outranks it."""
    entity = (TEMPLATES / "entity.html").read_text(encoding="utf-8")
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")

    prose = int(re.search(r"\.read p\.prose \{[^}]*font-size:\s*(\d+)px", entity).group(1))
    title = int(re.search(r"h1\.page-title \{[^}]*font-size:\s*(\d+)px", base).group(1))
    body_size = int(re.search(r"font:\s*400 (\d+)px", base).group(1))

    assert title > prose > body_size


def test_an_unreadable_axis_draws_no_mark_on_the_scale():
    """There is no honest place to put a dot for "we do not know", and a dot at zero would
    say "balanced" — the exact conflation the four states exist to prevent."""
    from engine.reading.states import Axis, State
    from engine.web.scale import track

    drawn = track(Axis.POLICY, State.NO_READ, 0.0, State.POSITIVE)

    assert drawn.readable is False
    assert drawn.now is None and drawn.expected is None


def _visible_text(html: str) -> str:
    """The page with its stylesheet and scripts removed.

    CSS and JS carry a vocabulary of their own — `border-box`, `scrollIntoView` — and
    matching against it would flag words the operator never sees.
    """
    without_style = re.sub(r"<style>.*?</style>", "", html, flags=re.DOTALL)
    return re.sub(r"<script>.*?</script>", "", without_style, flags=re.DOTALL)


PRICE_WORDS = re.compile(
    r"\b(price|candle|ohlc|resistance|support|pips?|spread(?!s)|chart|"
    r"moving average|rsi|macd|fibonacci|stop.?loss|take.?profit|lot size)\b",
    re.IGNORECASE,
)


@pytest.mark.parametrize(
    "template", sorted(p.name for p in Path("engine/web/templates").glob("*.html"))
)
def test_no_template_renders_a_price_a_candle_a_level_or_a_chart(template):
    """The interface answers 'what should I look at?' and then gets out of the way."""
    body = _visible_text((TEMPLATES / template).read_text(encoding="utf-8"))

    assert not PRICE_WORDS.search(body), f"{template} mentions chart or price content"


def test_no_rendered_page_contains_price_or_chart_content(client, universe):
    paths = ["/", "/entity/USD", "/entity/XAU"] + [
        f"/instrument/{i.ticker}" for i in universe.instruments
    ]

    for path in paths:
        body = _visible_text(client.get(path).text)
        assert not PRICE_WORDS.search(body), f"{path} rendered chart or price content"


EXECUTION_WORDS = re.compile(r"(order|position|broker|mt5|metatrader|risk|lot)", re.IGNORECASE)


def test_no_broker_or_execution_vocabulary_reaches_the_interface(client):
    """This reader places no orders and holds no positions; its vocabulary should show it."""
    body = _visible_text(client.get("/").text)

    assert not EXECUTION_WORDS.search(body)


# --- The desk ---------------------------------------------------------------------------------


@pytest.fixture
def desk(seeded, params, universe):
    """A client whose chat stage answers from a stub, so no test bills a model call."""
    stub = StubClient(answers={"chat": ["CADJPY is bullish now (S1), and CAD jobs land Friday."]})
    return TestClient(
        create_app(
            params=params,
            universe=universe,
            connection_factory=lambda: connect(seeded),
            stages_factory=lambda: Stages(client=stub, models=params.models, prices=params.prices),
            clock=lambda: NOW + timedelta(minutes=5),
        ),
        client=LOOPBACK,
    ), stub


def test_the_desk_renders_with_no_history(desk):
    client, _ = desk

    body = client.get("/chat").text

    assert "Ask the desk" in body
    assert "Nothing asked yet" in body


def test_a_question_is_answered_and_kept(desk, seeded):
    client, _ = desk

    client.post("/chat", data={"question": "should I be short CADJPY?"}, follow_redirects=True)

    connection = connect(seeded)
    try:
        row = connection.execute("SELECT * FROM chats ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        connection.close()
    assert row["question"] == "should I be short CADJPY?"
    assert "CADJPY is bullish" in row["answer"]
    assert row["pass_id"] is not None


def test_the_question_and_answer_both_show_on_the_page(desk):
    client, _ = desk
    client.post("/chat", data={"question": "what is coming this week?"}, follow_redirects=True)

    body = client.get("/chat").text

    assert "what is coming this week?" in body
    assert "CAD jobs land Friday" in body


def test_an_empty_question_bills_nothing(desk, seeded):
    client, stub = desk

    client.post("/chat", data={"question": "   "}, follow_redirects=True)

    connection = connect(seeded)
    try:
        assert connection.execute("SELECT count(*) FROM chats").fetchone()[0] == 0
    finally:
        connection.close()
    assert stub.count("chat") == 0


def test_a_failed_answer_is_recorded_rather_than_500ing(seeded, params, universe):
    """A brief that fails must not take the board down with it."""
    stub = StubClient(answers={"chat": ["never reached"]}, fail_on={"chat"})
    client = TestClient(
        create_app(
            params=params,
            universe=universe,
            connection_factory=lambda: connect(seeded),
            stages_factory=lambda: Stages(client=stub, models=params.models, prices=params.prices),
            clock=lambda: NOW,
        ),
        client=LOOPBACK,
    )

    response = client.post("/chat", data={"question": "anything"}, follow_redirects=True)

    assert response.status_code == 200
    assert "could not answer" in response.text


def test_the_desk_never_sees_a_headline(seeded, params, universe):
    """Same rule as the narrative stage: the sheet carries records, never raw feed text."""
    from engine.desk.sheet import brief_sheet

    connection = connect(seeded)
    try:
        titles = [r["title"] for r in connection.execute("SELECT title FROM headlines")]
        sheet = brief_sheet(connection, universe, params, NOW)
    finally:
        connection.close()

    assert titles
    for title in titles:
        assert title not in sheet


def test_the_sheet_carries_no_question_so_it_can_be_cached(seeded, params, universe):
    """The board sheet must be identical across questions, or the prompt cache never hits."""
    from engine.desk.sheet import brief_sheet

    connection = connect(seeded)
    try:
        first = brief_sheet(connection, universe, params, NOW)
        second = brief_sheet(connection, universe, params, NOW)
    finally:
        connection.close()

    assert first == second
    assert "OPERATOR ASKS" not in first


def test_the_question_is_sent_as_its_own_uncached_block(desk):
    """Prefix cached, question not — otherwise every question is a fresh 9,000-token read."""
    client, stub = desk
    client.post("/chat", data={"question": "why is CAD firm?"}, follow_redirects=True)

    blocks = stub.calls[-1]["content"]
    assert len(blocks) == 2
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in blocks[1]
    assert "why is CAD firm?" in blocks[1]["text"]
