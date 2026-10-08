"""Expose the saved research board to an external assistant over local MCP.

Run python mcp_server.py after installing the mcp extra and completing a live pass.
Tools open the existing database read-only; they do not refresh it or call a model.
See docs/running.md for a portable client configuration.

Stdout belongs to the MCP protocol. Send diagnostics to stderr.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server import MCPServer  # noqa: E402

from engine.desk import queries  # noqa: E402
from engine.store.guard import check_database_path  # noqa: E402
from engine.universe.config import load_params, load_universe  # noqa: E402

PARAMS = load_params()
UNIVERSE = load_universe()

mcp = MCPServer("fundamental-engine")


def _db():
    """Open existing results without creating a database or changing its schema."""
    database = check_database_path(PARAMS.database_path)
    if not database.is_file():
        raise FileNotFoundError("No saved read exists yet. Run python -m engine first.")
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _with_db(work) -> Any:
    connection = _db()
    try:
        return work(connection)
    finally:
        connection.close()


@mcp.tool()
def status() -> dict[str, Any]:
    """When the board was last read, whether that read is stale, and total spend to date.

    Check this first when the question is about right now — an answer from a six-hour-old
    read needs saying so.
    """
    return _with_db(lambda c: queries.status(c, PARAMS))


@mcp.tool()
def board() -> list[dict[str, Any]]:
    """Every instrument: the current read, the expected read, and the pre-position verdict.

    `bias` is which way to lean before the week's events print — not where the pair has
    been. `stance` is one of position / lean / wait / none.
    """
    return _with_db(lambda c: queries.pairs(c, UNIVERSE, PARAMS))


@mcp.tool()
def instrument(ticker: str) -> dict[str, Any] | None:
    """One pair in full, including each leg's own two-axis read and open situations.

    Use this for a question about a specific pair, e.g. CADJPY or XAUUSD. Returns None if
    the ticker is not in the traded universe.
    """
    return _with_db(lambda c: queries.pair(c, UNIVERSE, PARAMS, ticker))


@mcp.tool()
def entity(name: str) -> dict[str, Any] | None:
    """One currency or asset: both axes with scores, the open situations behind them,
    the forward expectation, and the written call from the last pass.

    `no read` means there is not enough evidence to call it, which is never the same as
    neutral. `why_no_read` says which it is.
    """
    return _with_db(lambda c: queries.currency(c, name))


@mcp.tool()
def upcoming(
    currency: str | None = None, days: int = 7, impact: str | None = None
) -> list[dict[str, Any]]:
    """Scheduled calendar events ahead. Filter by currency (EUR) and impact (High).

    Use this to answer what is coming that could move a position. An empty list is a real
    answer: it means nothing is scheduled, which is itself worth knowing.
    """
    return _with_db(lambda c: queries.calendar(c, currency, days, impact=impact))


@mcp.tool()
def situation(identifier: str) -> dict[str, Any] | None:
    """One situation by its S-number, with every entity it touches and its polarity.

    Situations are the engine's unit of evidence. Every read traces to a set of these.
    """
    return _with_db(lambda c: queries.situation(c, identifier))


@mcp.tool()
def find_situations(text: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search open situations by description, e.g. 'intervention', 'oil', 'tariff'.

    Use this when the question is about a theme rather than a pair.
    """
    return _with_db(lambda c: queries.search_situations(c, text, limit))


@mcp.tool()
def all_entities() -> dict[str, Any]:
    """Every tracked entity's two axes at a glance. The cheapest whole-board answer."""
    return _with_db(queries.entities_by_axis)


if __name__ == "__main__":
    print("fundamental-engine MCP server on stdio", file=sys.stderr)
    mcp.run()
