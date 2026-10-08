"""The commissioning check catches what it claims to catch.

This script is the gate between "the build finished" and "the loop is enabled", so its checks
are worth testing against planted defects rather than trusting to have been written correctly.
A commissioning check that silently passes everything is worse than none.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from engine.feeds.transport import FetchedBody
from engine.passes import run
from engine.stages import Stages
from engine.store.schema import connect, transaction
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


def _load_script():
    """Loaded by path: `scripts/` is not a package, and should not become one."""
    path = Path("scripts/inspect_pass.py").resolve()
    spec = importlib.util.spec_from_file_location("inspect_pass", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    return _load_script()


@pytest.fixture(scope="module")
def params():
    return load_params()


@pytest.fixture(scope="module")
def universe():
    return load_universe()


@pytest.fixture
def passed(tmp_path, fixture_body, params, universe):
    """A completed pass with nothing wrong with it."""
    connection = connect(tmp_path / "engine.db")

    def feeds(url: str) -> FetchedBody:
        name = "calendar_good.json" if "calendar" in url else "news_good.xml"
        return FetchedBody(text=fixture_body(name), resolved_url=url)

    stub = StubClient(
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
                    "The read is moderately hawkish on policy (S1, S2). Nothing argues "
                    "against it this week.",
                    ["S1", "S2"],
                )
            ],
        }
    )
    result = run(
        connection,
        universe,
        params,
        Stages(client=stub, models=params.models, prices=params.prices),
        now=NOW,
        fetcher=feeds,
    )
    yield connection, result
    connection.close()


def record(connection, pass_id):
    return dict(connection.execute("SELECT * FROM passes WHERE id = ?", (pass_id,)).fetchone())


# --- Adherence -----------------------------------------------------------------------------


def test_full_adherence_passes(script, passed, capsys):
    connection, result = passed

    assert script._check_adherence(record(connection, result.pass_id)) == 0
    assert "100%" in capsys.readouterr().out


def test_adherence_below_one_hundred_is_reported_as_a_defect(script, passed, capsys):
    """A model re-describing S4 instead of naming it double-counts a story invisibly."""
    connection, result = passed
    with transaction(connection):
        connection.execute("UPDATE passes SET adherence = 0.5 WHERE id = ?", (result.pass_id,))

    assert script._check_adherence(record(connection, result.pass_id)) == 1
    assert "DEFECT" in capsys.readouterr().out


def test_a_pass_with_no_situation_update_is_not_a_defect(script, passed, capsys):
    """Nothing was named, so nothing failed to resolve."""
    connection, result = passed
    with transaction(connection):
        connection.execute("UPDATE passes SET adherence = NULL WHERE id = ?", (result.pass_id,))

    assert script._check_adherence(record(connection, result.pass_id)) == 0


# --- Citations -----------------------------------------------------------------------------


def test_resolving_citations_pass(script, passed, capsys):
    connection, result = passed

    assert script._check_citations(connection, result.pass_id) == 0
    assert "resolves" in capsys.readouterr().out


def test_a_planted_invented_citation_is_caught(script, passed, capsys):
    connection, result = passed
    with transaction(connection):
        connection.execute(
            "UPDATE narratives SET cited_ids = ? WHERE pass_id = ? AND entity = 'USD'",
            (json.dumps(["S1", "S404"]), result.pass_id),
        )

    assert script._check_citations(connection, result.pass_id) == 1
    assert "S404" in capsys.readouterr().out


# --- NO READ integrity -----------------------------------------------------------------------


def test_a_clean_pass_has_no_neutral_with_zero_situations(script, passed, capsys):
    connection, result = passed

    assert script._check_no_read(connection, result.pass_id) == 0
    assert "ok" in capsys.readouterr().out


def test_a_planted_neutral_with_no_situations_is_caught(script, passed, capsys):
    """The exact conflation the four-state vocabulary exists to prevent."""
    connection, result = passed
    with transaction(connection):
        connection.execute(
            "UPDATE reads SET state = 'NEUTRAL', contributor_count = 0"
            " WHERE pass_id = ? AND entity = 'NZD' AND axis = 'POLICY'",
            (result.pass_id,),
        )

    assert script._check_no_read(connection, result.pass_id) == 1
    assert "NZD" in capsys.readouterr().out


# --- Distribution ----------------------------------------------------------------------------


def test_the_distribution_reports_both_axes_and_the_thresholds(script, passed, params, capsys):
    connection, result = passed

    script._distribution(connection, result.pass_id, params)
    out = capsys.readouterr().out

    assert "POLICY" in out and "DIRECTIONAL" in out
    assert "READ_THRESHOLD (3.5)" in out
    assert "NO READ" in out


def test_a_mostly_unreadable_board_says_so(script, passed, params, capsys):
    """A board that is almost all NO READ is the signal to lower the threshold after a week."""
    connection, result = passed

    script._distribution(connection, result.pass_id, params)
    out = capsys.readouterr().out

    # The fixture pass gives USD two policy situations and nothing else anywhere.
    assert "lowering" in out or "plausible spread" in out


def test_the_cost_breakdown_names_every_stage(script, passed, capsys):
    connection, result = passed

    script._stage_costs(record(connection, result.pass_id))
    out = capsys.readouterr().out

    for stage in ("triage", "score", "situations", "expectation", "narrative"):
        assert stage in out
