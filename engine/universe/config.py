"""Loading the two configuration files, and deriving the entity set from them.

The instrument universe is data (the methodology guide). Nothing in this module may hardcode an
instrument, a currency or a beta — the point of the YAML is that widening the scope is an
edit to a list rather than surgery on the engine, and a tuple hidden in Python would
quietly make that false.

An instrument the loader cannot resolve **fails startup**. Skipping it would leave the
interface showing 17 rows where the config asks for 18, which is the kind of discrepancy
nobody notices until they are trading on it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Final

import yaml

PROJECT_ROOT: Final = Path(__file__).resolve().parents[2]
CONFIG_DIR: Final = PROJECT_ROOT / "config"

# An FX ticker is two three-letter codes and nothing else. Anything that does not match
# has to be described explicitly under `non_fx`.
_FX_TICKER = re.compile(r"^([A-Z]{3})([A-Z]{3})$")


class ConfigError(RuntimeError):
    """The configuration cannot be resolved. Raised at startup, never swallowed."""


@dataclass(frozen=True)
class Instrument:
    """One tradeable instrument and how its two axes are built from entity reads.

    Both axes are described the same way — a list of `(entity, weight)` legs — so the
    resolver in `betas.py` has one code path rather than one per instrument family. An FX
    cross is `[(base, +1), (quote, -1)]`; an index's policy axis is `[(USD, -1.3)]`.
    """

    ticker: str
    group: str
    policy_legs: tuple[tuple[str, float], ...]
    directional_legs: tuple[tuple[str, float], ...]

    @property
    def entities(self) -> tuple[str, ...]:
        """Every entity either axis depends on, in order of first appearance."""
        seen: list[str] = []
        for entity, _ in self.policy_legs + self.directional_legs:
            if entity not in seen:
                seen.append(entity)
        return tuple(seen)


@dataclass(frozen=True)
class Universe:
    """The resolved instrument list and the entity set derived from it."""

    instruments: tuple[Instrument, ...]
    entities: tuple[str, ...]

    def instrument(self, ticker: str) -> Instrument:
        for candidate in self.instruments:
            if candidate.ticker == ticker:
                return candidate
        raise ConfigError(f"no instrument {ticker!r} in the configured universe")

    def instruments_touching(self, entity: str) -> tuple[Instrument, ...]:
        """Every instrument whose read depends on this entity.

        Used by the release trigger: a USD print moves the five USD pairs, both metals,
        oil and all three indices at once.
        """
        return tuple(i for i in self.instruments if entity in i.entities)


@dataclass(frozen=True)
class Params:
    """Every tunable number, loaded once and passed down rather than re-read.

    Held as typed fields instead of a dict so a typo in a parameter name is an error at
    startup rather than a silent `None` inside the arithmetic.
    """

    read_threshold: float
    shock_magnitude: float
    min_situations: int
    recency_half_life: dict[str, timedelta]
    breadth_floor: float
    degree_bands: tuple[tuple[str, float], ...]
    near_miss_margin: float
    fade_after: timedelta
    archive_after: timedelta
    situation_warning_count: int
    horizon: timedelta
    expectation_impacts: tuple[str, ...]
    confidence_weights: dict[str, float]
    hourly_minutes: int
    stale_margin: timedelta
    stale_after_minutes: int
    release_delay: timedelta
    debounce: timedelta
    release_watch_list: tuple[str, ...]
    headline_urls: tuple[str, ...]
    sitemap_urls: tuple[str, ...]
    scheduler_enabled: bool
    models: dict[str, str]
    prices: dict[str, dict[str, float]]
    database_path: Path
    cache_dir: Path
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @property
    def ticks_on_a_clock(self) -> bool:
        """Whether a scheduled tick runs at all. `HOURLY_MINUTES: 0` means release-only."""
        return self.hourly_minutes > 0

    @property
    def stale_after(self) -> timedelta:
        """How old a read may be before the interface calls it stale.

        On a clock this is one cadence plus the margin — a read that missed its tick. With
        the tick off there is no cadence to miss, so staleness becomes a judgement about how
        long a read stays *useful*, which is a different question and gets its own number.
        Deriving it from a cadence of zero would mark every read stale the moment it landed.
        """
        if self.ticks_on_a_clock:
            return timedelta(minutes=self.hourly_minutes) + self.stale_margin
        return timedelta(minutes=self.stale_after_minutes)

    @property
    def cadence_words(self) -> str:
        """What the loop does, for the header on every page."""
        if not self.scheduler_enabled:
            return "auto is off — refresh by hand"
        if self.ticks_on_a_clock:
            return f"every {self.hourly_minutes} minutes, and on every high-impact release"
        return "on high-impact releases only — refresh by hand for anything else"

    def half_life(self, axis: object) -> timedelta:
        """The decay clock for one axis. Policy is slower than direction on purpose."""
        return self.recency_half_life[str(axis)]

    @property
    def breadth_minimum(self) -> float:
        """The weighted contribution below which a situation stops counting as corroboration."""
        return self.breadth_floor * self.read_threshold

    @property
    def near_miss_floor(self) -> float:
        """The score at or above which a sub-threshold read is called a near miss."""
        return self.near_miss_margin * self.read_threshold


def load_universe(path: Path | None = None) -> Universe:
    """Read `instruments.yaml` into resolved instruments and the entities they need."""
    document = _read_yaml(path or CONFIG_DIR / "instruments.yaml")

    groups = document.get("instruments")
    if not isinstance(groups, dict) or not groups:
        raise ConfigError("instruments.yaml has no `instruments` mapping")
    non_fx = document.get("non_fx") or {}
    if not isinstance(non_fx, dict):
        raise ConfigError("instruments.yaml: `non_fx` must be a mapping")

    instruments: list[Instrument] = []
    for group, tickers in groups.items():
        if not isinstance(tickers, list):
            raise ConfigError(f"instruments.yaml: group {group!r} is not a list")
        for ticker in tickers:
            instruments.append(_resolve(str(ticker), str(group), non_fx))

    entities = _derive_entities(instruments, document.get("extra_entities") or [])
    return Universe(instruments=tuple(instruments), entities=entities)


def _resolve(ticker: str, group: str, non_fx: dict[str, Any]) -> Instrument:
    """Turn one configured ticker into its two sets of weighted legs.

    An explicit `non_fx` entry always wins over the FX pattern. That ordering matters:
    a six-letter non-FX ticker would otherwise be silently misread as a currency pair.
    """
    if ticker in non_fx:
        return _resolve_non_fx(ticker, group, non_fx[ticker])

    matched = _FX_TICKER.match(ticker)
    if matched is None:
        raise ConfigError(
            f"instruments.yaml: cannot resolve {ticker!r}. It is not a six-letter FX "
            f"ticker and has no entry under `non_fx`."
        )
    base, quote = matched.group(1), matched.group(2)
    legs = ((base, 1.0), (quote, -1.0))
    return Instrument(ticker=ticker, group=group, policy_legs=legs, directional_legs=legs)


def _resolve_non_fx(ticker: str, group: str, spec: Any) -> Instrument:
    if not isinstance(spec, dict):
        raise ConfigError(f"instruments.yaml: `non_fx.{ticker}` must be a mapping")

    entity = spec.get("entity")
    policy_from = spec.get("policy_from")
    multiplier = spec.get("policy_multiplier")
    directional = spec.get("directional")

    if not entity or not policy_from or multiplier is None:
        raise ConfigError(
            f"instruments.yaml: `non_fx.{ticker}` needs `entity`, `policy_from` and "
            f"`policy_multiplier`"
        )
    if not isinstance(multiplier, (int, float)):
        raise ConfigError(f"instruments.yaml: `non_fx.{ticker}.policy_multiplier` is not a number")

    # The whole pre-positioning mechanic is that this number is negative: a hawkish Fed
    # reads bearish for an index. A positive one would be a sign error producing a
    # plausible-looking read pointing the wrong way, so it is rejected rather than used.
    if multiplier >= 0:
        raise ConfigError(
            f"instruments.yaml: `non_fx.{ticker}.policy_multiplier` is {multiplier}. "
            f"Inherited policy is inverse by construction and must be negative."
        )

    if directional == "own":
        directional_legs: tuple[tuple[str, float], ...] = ((str(entity), 1.0),)
    elif directional == "spread":
        directional_legs = ((str(entity), 1.0), (str(policy_from), -1.0))
    else:
        raise ConfigError(
            f"instruments.yaml: `non_fx.{ticker}.directional` is {directional!r}, "
            f"expected 'own' or 'spread'"
        )

    return Instrument(
        ticker=ticker,
        group=group,
        policy_legs=((str(policy_from), float(multiplier)),),
        directional_legs=directional_legs,
    )


def _derive_entities(instruments: list[Instrument], extra: Any) -> tuple[str, ...]:
    """The entity set is derived, never listed.

    A currency appearing in no configured instrument is therefore not scored, holds no
    entity record, and costs nothing — which is how CHF stays out without a special case.
    """
    if not isinstance(extra, list):
        raise ConfigError("instruments.yaml: `extra_entities` must be a list")

    found: set[str] = set()
    for instrument in instruments:
        found.update(instrument.entities)
    found.update(str(name) for name in extra)
    return tuple(sorted(found))


def load_params(path: Path | None = None) -> Params:
    """Read `params.yaml` into a typed record, converting day counts to durations here."""
    document = _read_yaml(path or CONFIG_DIR / "params.yaml")

    reading = _section(document, "reading")
    situations = _section(document, "situations")
    expectation = _section(document, "expectation")
    loop = _section(document, "loop")
    feeds = _section(document, "feeds")
    model = _section(document, "model")
    database = _section(document, "database")

    bands = reading.get("DEGREE_BANDS")
    if not isinstance(bands, dict) or not bands:
        raise ConfigError("params.yaml: `reading.DEGREE_BANDS` must be a non-empty mapping")
    # Sorted ascending so banding is a scan from the bottom, and so re-ordering the file
    # cannot change a degree.
    ordered = tuple(sorted(((str(k), float(v)) for k, v in bands.items()), key=lambda p: p[1]))

    return Params(
        read_threshold=float(_require(reading, "READ_THRESHOLD")),
        shock_magnitude=float(_require(reading, "SHOCK_MAGNITUDE")),
        min_situations=int(_require(reading, "MIN_SITUATIONS")),
        recency_half_life={
            str(axis): timedelta(days=float(days))
            for axis, days in _require(reading, "RECENCY_HALF_LIFE").items()
        },
        breadth_floor=float(_require(reading, "BREADTH_FLOOR")),
        degree_bands=ordered,
        near_miss_margin=float(_require(reading, "NEAR_MISS_MARGIN")),
        fade_after=timedelta(days=float(_require(situations, "FADE_AFTER"))),
        archive_after=timedelta(days=float(_require(situations, "ARCHIVE_AFTER"))),
        situation_warning_count=int(_require(situations, "SITUATION_WARNING_COUNT")),
        horizon=timedelta(days=float(_require(expectation, "HORIZON_DAYS"))),
        expectation_impacts=tuple(str(i) for i in _require(expectation, "IMPACTS")),
        confidence_weights={
            str(k): float(v) for k, v in _require(expectation, "CONFIDENCE_WEIGHTS").items()
        },
        hourly_minutes=int(_require(loop, "HOURLY_MINUTES")),
        stale_margin=timedelta(minutes=float(_require(loop, "STALE_MARGIN_MINUTES"))),
        stale_after_minutes=int(loop.get("STALE_AFTER_MINUTES", 720)),
        release_delay=timedelta(minutes=float(_require(loop, "RELEASE_DELAY_MINUTES"))),
        debounce=timedelta(minutes=float(_require(loop, "DEBOUNCE_MINUTES"))),
        release_watch_list=tuple(str(c) for c in _require(loop, "RELEASE_WATCH_LIST")),
        headline_urls=tuple(str(u) for u in _require(feeds, "HEADLINE_URLS")),
        sitemap_urls=tuple(str(u) for u in feeds.get("SITEMAP_URLS", ())),
        scheduler_enabled=bool(_require(loop, "ENABLED")),
        models={str(k): str(v) for k, v in model.items() if k != "PRICES"},
        prices={
            str(name): {str(k): float(v) for k, v in cost.items()}
            for name, cost in (model.get("PRICES") or {}).items()
        },
        database_path=_local_path(_require(database, "path")),
        cache_dir=_local_path(_require(database, "cache_dir")),
        raw=document,
    )


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"missing configuration file {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ConfigError(f"{path} does not contain a mapping")
    return loaded


def _section(document: dict[str, Any], name: str) -> dict[str, Any]:
    section = document.get(name)
    if not isinstance(section, dict):
        raise ConfigError(f"params.yaml has no `{name}` section")
    return section


def _require(section: dict[str, Any], key: str) -> Any:
    if key not in section:
        raise ConfigError(f"params.yaml is missing `{key}`")
    return section[key]


def _local_path(value: object) -> Path:
    """Resolve working files from the checkout, even when an MCP host uses another cwd."""
    configured = Path(str(value)).expanduser()
    return configured if configured.is_absolute() else PROJECT_ROOT / configured
