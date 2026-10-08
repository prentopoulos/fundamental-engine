"""Every row of the beta table, and the way `NO READ` refuses to become a zero.

The sign errors this file is guarding against are the dangerous kind: they produce a
confident, plausible-looking read pointing the wrong way. A hawkish Fed reading *bullish*
for the Nasdaq would look like a normal row on the board.
"""

from __future__ import annotations

import pytest

from engine.reading.states import Axis, Degree, State
from engine.universe.betas import (
    LegRead,
    resolve,
    resolve_axis,
    resolve_expected,
    resolve_expected_axis,
)
from engine.universe.config import load_params, load_universe


@pytest.fixture(scope="module")
def universe():
    return load_universe()


@pytest.fixture(scope="module")
def params():
    return load_params()


def reads(**scores: float) -> dict[str, LegRead]:
    """Readable legs at the given scores. Every one passes breadth by construction."""
    return {
        entity: LegRead(
            entity=entity,
            state=State.POSITIVE if score > 0 else State.NEGATIVE if score < 0 else State.NEUTRAL,
            score=score,
        )
        for entity, score in scores.items()
    }


def unreadable(*entities: str) -> dict[str, LegRead]:
    return {entity: LegRead(entity=entity, state=State.NO_READ, score=0.0) for entity in entities}


# --- FX crosses: base minus quote, on both axes ---------------------------------------


@pytest.mark.parametrize(
    ("ticker", "base", "quote"),
    [
        ("AUDUSD", "AUD", "USD"),
        ("EURUSD", "EUR", "USD"),
        ("GBPUSD", "GBP", "USD"),
        ("USDCAD", "USD", "CAD"),
        ("USDJPY", "USD", "JPY"),
        ("EURAUD", "EUR", "AUD"),
        ("EURCAD", "EUR", "CAD"),
        ("EURJPY", "EUR", "JPY"),
        ("EURNZD", "EUR", "NZD"),
        ("GBPAUD", "GBP", "AUD"),
        ("GBPCAD", "GBP", "CAD"),
        ("GBPJPY", "GBP", "JPY"),
    ],
)
def test_fx_cross_subtracts_its_legs_on_both_axes(universe, params, ticker, base, quote):
    instrument = universe.instrument(ticker)
    legs = reads(**{base: 9.0, quote: 2.0})

    read = resolve(instrument, legs, legs, params)

    assert read.policy.score == pytest.approx(7.0)
    assert read.directional.score == pytest.approx(7.0)
    assert read.policy.state is State.POSITIVE
    assert read.directional.state is State.POSITIVE


def test_a_cross_reads_the_other_way_when_the_quote_is_stronger(universe, params):
    legs = reads(EUR=2.0, JPY=9.0)

    read = resolve(universe.instrument("EURJPY"), legs, legs, params)

    assert read.policy.score == pytest.approx(-7.0)
    assert read.policy.state is State.NEGATIVE
    assert read.words == "moderately dovish / moderately bearish"


# --- Inherited policy: the pre-positioning mechanic ------------------------------------


@pytest.mark.parametrize(
    ("ticker", "multiplier"),
    [
        ("XAUUSD", -1.0),
        ("XAGUSD", -1.0),
        ("WTICOUSD", -0.5),
        ("SPX500USD", -1.0),
        ("NAS100USD", -1.3),
        ("US30USD", -0.8),
    ],
)
def test_usd_quoted_instruments_inherit_an_inverted_usd_policy(
    universe, params, ticker, multiplier
):
    usd_policy = reads(USD=10.0)

    axis = resolve_axis(universe.instrument(ticker), Axis.POLICY, usd_policy, params)

    assert axis.score == pytest.approx(10.0 * multiplier)
    # A hawkish Fed reads bearish for every one of them. This is the whole mechanic.
    assert axis.state is State.NEGATIVE


def test_index_rate_sensitivity_is_ordered_nas_then_spx_then_dow(universe, params):
    """Long-duration tech reprices hardest on rates; the Dow least."""
    usd_policy = reads(USD=10.0)

    magnitudes = {
        ticker: abs(
            resolve_axis(universe.instrument(ticker), Axis.POLICY, usd_policy, params).score
        )
        for ticker in ("NAS100USD", "SPX500USD", "US30USD")
    }

    assert magnitudes["NAS100USD"] > magnitudes["SPX500USD"] > magnitudes["US30USD"]


def test_metals_take_their_direction_from_the_metal_minus_the_dollar(universe, params):
    axis = resolve_axis(
        universe.instrument("XAUUSD"), Axis.DIRECTIONAL, reads(XAU=8.0, USD=1.0), params
    )

    assert axis.score == pytest.approx(7.0)
    assert axis.state is State.POSITIVE


@pytest.mark.parametrize(
    ("ticker", "entity"),
    [("WTICOUSD", "WTI"), ("SPX500USD", "SPX500"), ("NAS100USD", "NAS100"), ("US30USD", "US30")],
)
def test_oil_and_indices_take_their_direction_from_their_own_situations(
    universe, params, ticker, entity
):
    """A stronger dollar is a headwind for oil, not the driver — USD is absent here."""
    axis = resolve_axis(
        universe.instrument(ticker), Axis.DIRECTIONAL, reads(**{entity: 6.0}), params
    )

    assert axis.score == pytest.approx(6.0)
    assert axis.state is State.POSITIVE


def test_a_hawkish_fed_reads_bearish_hardest_on_the_nasdaq(universe, params):
    """The worked example from the methodology guide, as one assertion."""
    usd_policy = reads(USD=12.0)
    order = [
        (t, resolve_axis(universe.instrument(t), Axis.POLICY, usd_policy, params))
        for t in ("NAS100USD", "SPX500USD", "US30USD", "XAUUSD", "XAGUSD", "WTICOUSD")
    ]

    assert all(axis.state is State.NEGATIVE for _, axis in order)
    assert order[0][1].degree is Degree.STRONGLY  # -15.6
    assert order[-1][1].degree is Degree.SLIGHTLY  # -6.0, oil moves least


# --- NO READ propagation ---------------------------------------------------------------


def test_no_read_on_one_fx_leg_makes_the_cross_unreadable(universe, params):
    legs = {**reads(EUR=9.0), **unreadable("NZD")}

    axis = resolve_axis(universe.instrument("EURNZD"), Axis.POLICY, legs, params)

    assert axis.state is State.NO_READ
    assert axis.score == 0.0
    assert axis.unreadable_because == ("NZD",)


def test_no_read_propagates_on_the_directional_axis_too(universe, params):
    legs = {**unreadable("EUR"), **reads(JPY=4.0)}

    axis = resolve_axis(universe.instrument("EURJPY"), Axis.DIRECTIONAL, legs, params)

    assert axis.state is State.NO_READ


def test_unreadable_usd_policy_propagates_through_the_inheritance(universe, params):
    """USD policy with nothing on file leaves every USD-quoted instrument unreadable."""
    for ticker in ("SPX500USD", "NAS100USD", "US30USD", "XAUUSD", "XAGUSD", "WTICOUSD"):
        axis = resolve_axis(universe.instrument(ticker), Axis.POLICY, unreadable("USD"), params)
        assert axis.state is State.NO_READ, ticker
        assert axis.unreadable_because == ("USD",)


def test_an_unreadable_leg_is_never_treated_as_zero(universe, params):
    """A zero would let a strong EUR carry EURNZD alone while NZD had nothing on file."""
    axis = resolve_axis(
        universe.instrument("EURNZD"), Axis.POLICY, {**reads(EUR=20.0), **unreadable("NZD")}, params
    )

    assert axis.state is not State.NEUTRAL
    assert axis.state is State.NO_READ


def test_a_missing_leg_is_unreadable_rather_than_absent(universe, params):
    """An entity with no read at all must not silently resolve as a neutral leg."""
    axis = resolve_axis(universe.instrument("EURNZD"), Axis.POLICY, reads(EUR=9.0), params)

    assert axis.state is State.NO_READ
    assert axis.unreadable_because == ("NZD",)


def test_one_axis_unreadable_leaves_the_other_alone(universe, params):
    """The axes are independent all the way through resolution."""
    read = resolve(
        universe.instrument("XAUUSD"),
        policy_reads=unreadable("USD"),
        directional_reads=reads(XAU=9.0, USD=1.0),
        params=params,
    )

    assert read.policy.state is State.NO_READ
    assert read.directional.state is State.POSITIVE
    assert read.words == "no read / moderately bullish"


# --- Balance and banding ---------------------------------------------------------------


def test_two_readable_legs_that_cancel_read_neutral_not_no_read(universe, params):
    """Evidence that cancels is a positive finding, and must not look like silence."""
    axis = resolve_axis(universe.instrument("EURUSD"), Axis.POLICY, reads(EUR=6.0, USD=6.0), params)

    assert axis.state is State.NEUTRAL
    assert axis.degree is None


def test_a_sub_threshold_combination_reads_neutral(universe, params):
    axis = resolve_axis(universe.instrument("EURUSD"), Axis.POLICY, reads(EUR=6.0, USD=4.0), params)

    assert axis.score == pytest.approx(2.0)
    assert axis.state is State.NEUTRAL


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (3.5, Degree.SLIGHTLY),
        (6.9, Degree.SLIGHTLY),
        (7.0, Degree.MODERATELY),
        (11.9, Degree.MODERATELY),
        (12.0, Degree.STRONGLY),
        (40.0, Degree.STRONGLY),
    ],
)
def test_degree_bands_on_a_resolved_instrument(universe, params, score, expected):
    axis = resolve_axis(
        universe.instrument("EURUSD"), Axis.POLICY, reads(EUR=score, USD=0.0), params
    )

    assert axis.degree is expected


# --- The expected lane resolves from states, never from scores -------------------------


def test_the_expected_lane_resolves_from_states_not_scores(universe):
    """The bug this exists to prevent: handed expected states, the score-based resolver
    quietly ignored them and returned a copy of the current read, so the board's expected
    column could never differ from its now column.
    """
    axis = resolve_expected_axis(
        universe.instrument("SPX500USD"), Axis.POLICY, {"USD": State.POSITIVE}
    )

    # USD expected hawkish, inherited at −1.0, so the index is expected bearish.
    assert axis.state is State.NEGATIVE


def test_an_expected_axis_carries_no_score(universe):
    """There is no number on that side of the lane, so there is nothing to average."""
    axis = resolve_expected_axis(
        universe.instrument("SPX500USD"), Axis.POLICY, {"USD": State.NEGATIVE}
    )

    assert axis.score == 0.0
    assert axis.degree is None


@pytest.mark.parametrize(
    ("base_state", "quote_state", "expected"),
    [
        (State.POSITIVE, State.NEGATIVE, State.POSITIVE),
        (State.NEGATIVE, State.POSITIVE, State.NEGATIVE),
        (State.POSITIVE, State.POSITIVE, State.NEUTRAL),
        (State.NEUTRAL, State.NEGATIVE, State.POSITIVE),
    ],
)
def test_an_expected_cross_subtracts_its_legs(universe, base_state, quote_state, expected):
    axis = resolve_expected_axis(
        universe.instrument("EURUSD"), Axis.POLICY, {"EUR": base_state, "USD": quote_state}
    )

    assert axis.state is expected


def test_no_read_propagates_through_the_expected_lane_too(universe):
    axis = resolve_expected_axis(
        universe.instrument("EURUSD"), Axis.POLICY, {"EUR": State.POSITIVE, "USD": State.NO_READ}
    )

    assert axis.state is State.NO_READ
    assert axis.unreadable_because == ("USD",)


def test_a_missing_expected_leg_is_unreadable_rather_than_neutral(universe):
    axis = resolve_expected_axis(
        universe.instrument("EURUSD"), Axis.POLICY, {"EUR": State.POSITIVE}
    )

    assert axis.state is State.NO_READ


def test_the_index_inheritance_flips_the_expected_sign_for_all_three(universe):
    """A hawkish Fed is expected bearish for every index, which is the pre-positioning
    mechanic stated in the forward lane."""
    for ticker in ("SPX500USD", "NAS100USD", "US30USD", "XAUUSD", "XAGUSD", "WTICOUSD"):
        axis = resolve_expected_axis(
            universe.instrument(ticker), Axis.POLICY, {"USD": State.POSITIVE}
        )
        assert axis.state is State.NEGATIVE, ticker


def test_both_expected_axes_resolve_together(universe):
    read = resolve_expected(
        universe.instrument("XAUUSD"),
        policy={"USD": State.POSITIVE},
        directional={"XAU": State.POSITIVE, "USD": State.NEGATIVE},
    )

    assert read.policy.state is State.NEGATIVE
    assert read.directional.state is State.POSITIVE
    assert read.words == "dovish / bullish"


# --- A quiet leg must not blank a loud one on the forward lane ---------------------------------


def test_a_leg_with_nothing_scheduled_does_not_blank_the_other_legs_expectation(universe):
    """The CADJPY defect: JPY had nothing on its calendar, CAD had a payrolls print expected
    to collapse, and `NO_READ` propagation deleted the CAD call — leaving the row claiming
    nothing was scheduled while a High-impact event sat two days out.
    """
    axis = resolve_expected_axis(
        universe.instrument("CADJPY"),
        Axis.DIRECTIONAL,
        {"CAD": State.NEGATIVE, "JPY": State.NO_READ},
        quiet=frozenset({"JPY"}),
    )

    assert axis.state is State.NEGATIVE


def test_every_leg_quiet_is_still_no_read(universe):
    """Nothing on any calendar is genuinely no forward information, not a neutral view."""
    axis = resolve_expected_axis(
        universe.instrument("CADJPY"),
        Axis.DIRECTIONAL,
        {"CAD": State.NO_READ, "JPY": State.NO_READ},
        quiet=frozenset({"CAD", "JPY"}),
    )

    assert axis.state is State.NO_READ
    assert set(axis.unreadable_because) == {"CAD", "JPY"}


def test_an_unreadable_leg_that_is_not_quiet_still_blocks(universe):
    """Only *nothing scheduled* is exempt. Missing information still makes the pair unreadable."""
    axis = resolve_expected_axis(
        universe.instrument("CADJPY"),
        Axis.DIRECTIONAL,
        {"CAD": State.NEGATIVE, "JPY": State.NO_READ},
        quiet=frozenset(),
    )

    assert axis.state is State.NO_READ
    assert axis.unreadable_because == ("JPY",)


def test_a_quiet_leg_still_carries_its_sign_onto_the_pair(universe):
    """CAD expected bearish with JPY quiet must read bearish for CADJPY, not bullish —
    the base leg's sign is preserved, not inverted, when the quote leg drops out.
    """
    bearish = resolve_expected(
        universe.instrument("CADJPY"),
        {"CAD": State.NEGATIVE, "JPY": State.NO_READ},
        {"CAD": State.NEGATIVE, "JPY": State.NO_READ},
        quiet=frozenset({"JPY"}),
    )
    bullish = resolve_expected(
        universe.instrument("CADJPY"),
        {"CAD": State.POSITIVE, "JPY": State.NO_READ},
        {"CAD": State.POSITIVE, "JPY": State.NO_READ},
        quiet=frozenset({"JPY"}),
    )

    assert bearish.directional.state is State.NEGATIVE
    assert bullish.directional.state is State.POSITIVE
