"""The universe is data, and the derivation from it is exact.

The assertions here are deliberately literal — the 18 tickers and the 13 entities written
out — because the value of a derived entity set is that nobody has to maintain it, and the
only way to know the derivation still works is to state the answer independently.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from engine.universe.config import ConfigError, load_params, load_universe

EXPECTED_ENTITIES = (
    "AUD",
    "CAD",
    "EUR",
    "GBP",
    "JPY",
    "NZD",
    "USD",  # currencies
    "WTI",
    "XAG",
    "XAU",  # commodities
    "NAS100",
    "SPX500",
    "US30",  # indices
)


@pytest.fixture(scope="module")
def universe():
    return load_universe()


def test_the_configured_instruments_are_grouped_as_the_file_says(universe):
    by_group: dict[str, int] = {}
    for instrument in universe.instruments:
        by_group[instrument.group] = by_group.get(instrument.group, 0) + 1

    assert sum(by_group.values()) == len(universe.instruments)
    assert set(by_group) == {"fx", "metals_energy", "indices"}
    assert by_group["metals_energy"] == 3 and by_group["indices"] == 3


def test_adding_a_cross_between_two_scored_entities_costs_no_new_entity(universe):
    """CADJPY was added after commissioning. Both legs were already scored, so it is a
    pure data edit — no new situations, no extra model calls, and it resolves from reads
    the engine already holds."""
    cross = universe.instrument("CADJPY")

    assert cross.policy_legs == (("CAD", 1.0), ("JPY", -1.0))
    assert cross.directional_legs == (("CAD", 1.0), ("JPY", -1.0))
    assert set(cross.entities) <= set(universe.entities)


def test_exactly_thirteen_entities_are_derived(universe):
    assert set(universe.entities) == set(EXPECTED_ENTITIES)
    assert len(universe.entities) == 13


def test_a_currency_in_no_configured_instrument_is_not_scored(universe):
    """CHF appears in no traded pair, so it holds no entity record and costs nothing."""
    assert "CHF" not in universe.entities


def test_nzd_survives_on_eurnzd_alone(universe):
    """Its only appearance. If EURNZD were dropped, NZD would leave with it."""
    touching = universe.instruments_touching("NZD")

    assert [i.ticker for i in touching] == ["EURNZD"]


def test_a_usd_release_reaches_eleven_instruments(universe):
    """A single NFP print moves the five USD pairs, both metals, oil and all three indices."""
    touching = {i.ticker for i in universe.instruments_touching("USD")}

    assert touching == {
        "AUDUSD",
        "EURUSD",
        "GBPUSD",
        "USDCAD",
        "USDJPY",
        "XAUUSD",
        "XAGUSD",
        "WTICOUSD",
        "SPX500USD",
        "NAS100USD",
        "US30USD",
    }


def test_every_instrument_resolves_to_entities_that_are_scored(universe):
    """No instrument may depend on an entity the pass will never produce a read for."""
    scored = set(universe.entities)
    for instrument in universe.instruments:
        assert set(instrument.entities) <= scored, instrument.ticker


# --- Failure modes: startup refuses rather than silently skipping ----------------------


def _write(tmp_path, body: str):
    path = tmp_path / "instruments.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_an_unresolvable_instrument_fails_startup(tmp_path):
    """17 rows where the config asks for 18 is the kind of gap nobody notices in time."""
    path = _write(tmp_path, "instruments:\n  odd:\n    - BITCOIN\nnon_fx: {}\n")

    with pytest.raises(ConfigError, match="cannot resolve 'BITCOIN'"):
        load_universe(path)


def test_a_positive_inherited_multiplier_is_refused(tmp_path):
    """A sign error here produces a confident read pointing the wrong way."""
    path = _write(
        tmp_path,
        "instruments:\n  indices:\n    - SPX500USD\n"
        "non_fx:\n  SPX500USD:\n    entity: SPX500\n    policy_from: USD\n"
        "    policy_multiplier: 1.0\n    directional: own\n",
    )

    with pytest.raises(ConfigError, match="must be negative"):
        load_universe(path)


def test_an_unknown_directional_rule_is_refused(tmp_path):
    path = _write(
        tmp_path,
        "instruments:\n  indices:\n    - SPX500USD\n"
        "non_fx:\n  SPX500USD:\n    entity: SPX500\n    policy_from: USD\n"
        "    policy_multiplier: -1.0\n    directional: vibes\n",
    )

    with pytest.raises(ConfigError, match="expected 'own' or 'spread'"):
        load_universe(path)


def test_adding_an_instrument_needs_no_code_change(tmp_path):
    """The claim the YAML makes: widening the scope is a data edit."""
    path = _write(tmp_path, "instruments:\n  fx:\n    - EURUSD\n    - USDCHF\nnon_fx: {}\n")

    widened = load_universe(path)

    assert [i.ticker for i in widened.instruments] == ["EURUSD", "USDCHF"]
    assert "CHF" in widened.entities


def test_extra_entities_are_scored_without_an_instrument(tmp_path):
    """The seam that lets a currency be scored ahead of its pair being added."""
    path = _write(
        tmp_path, "instruments:\n  fx:\n    - EURUSD\nnon_fx: {}\nextra_entities: [CHF]\n"
    )

    assert "CHF" in load_universe(path).entities


# --- Params ----------------------------------------------------------------------------


def test_every_parameter_the_plan_names_is_present():
    params = load_params()

    assert params.read_threshold == 3.5
    assert params.shock_magnitude == 7
    assert params.min_situations == 2
    assert params.recency_half_life["DIRECTIONAL"].days == 7
    assert params.recency_half_life["POLICY"].days == 21
    assert params.breadth_floor == 0.5
    assert params.breadth_minimum == pytest.approx(1.75)
    assert params.fade_after.days == 4
    assert params.archive_after.days == 60
    assert params.degree_bands == (("slightly", 3.5), ("moderately", 7.0), ("strongly", 12.0))
    assert params.near_miss_margin == 0.7
    assert params.near_miss_floor == pytest.approx(2.45)
    assert params.situation_warning_count == 15
    assert params.horizon.days == 7
    assert params.hourly_minutes == 0  # release-only
    assert params.stale_after_minutes == 720
    assert params.release_delay.total_seconds() == 300
    assert params.debounce.total_seconds() == 600


def test_the_release_watch_list_is_the_seven_currencies_in_a_traded_pair():
    """Independent of the scored entity set by design, but today it coincides with it."""
    watched = set(load_params().release_watch_list)
    assert watched == {"AUD", "CAD", "EUR", "GBP", "JPY", "NZD", "USD"}


def test_the_headline_sources_are_configuration():
    """Several sections of one publisher, read together. Widening coverage is a data edit."""
    urls = load_params().headline_urls

    assert len(urls) >= 1
    assert all(url.startswith("https://") for url in urls)
    # Technical analysis is out of scope, so paying to triage it would be waste.
    assert not any("technical" in url for url in urls)


def test_whether_the_scheduler_runs_is_configuration():
    """It shipped disabled and was enabled by hand after commissioning. What matters is
    that the flag is the only thing deciding it — never a default buried in code."""
    assert isinstance(load_params().scheduler_enabled, bool)


def test_a_missing_parameter_fails_startup(tmp_path):
    path = tmp_path / "params.yaml"
    path.write_text(
        "reading: {SHOCK_MAGNITUDE: 7, DEGREE_BANDS: {slightly: 3.5}}\n"
        "situations: {}\nexpectation: {}\nloop: {}\nfeeds: {}\n"
        "model: {}\ndatabase: {}\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="READ_THRESHOLD"):
        load_params(path)


def test_a_cadence_of_zero_means_release_only():
    """`HOURLY_MINUTES: 0` turns the scheduled tick off without disarming the releases."""
    from dataclasses import replace

    params = load_params()
    assert replace(params, hourly_minutes=0).ticks_on_a_clock is False
    assert replace(params, hourly_minutes=240).ticks_on_a_clock is True


def test_staleness_does_not_collapse_when_the_tick_is_off():
    """Deriving staleness from a cadence of zero would flag every read the moment it landed."""
    from dataclasses import replace

    params = load_params()
    off = replace(params, hourly_minutes=0, stale_after_minutes=720)
    on = replace(params, hourly_minutes=240)

    assert off.stale_after == timedelta(minutes=720)
    assert on.stale_after == timedelta(minutes=240) + on.stale_margin


def test_the_header_says_what_the_loop_actually_does():
    from dataclasses import replace

    params = load_params()
    assert (
        "high-impact releases only"
        in replace(params, hourly_minutes=0, scheduler_enabled=True).cadence_words
    )
    assert (
        "every 240 minutes"
        in replace(params, hourly_minutes=240, scheduler_enabled=True).cadence_words
    )
    assert "auto is off" in replace(params, scheduler_enabled=False).cadence_words
