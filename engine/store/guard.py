"""Keep the research database separate from execution databases.

Resolve paths before opening a file, reject reserved execution directories, and
inspect existing databases read-only for the execution-only verdicts table.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Final

# Reserved for execution systems; this reader keeps its own research database.
FORBIDDEN_DIRECTORY: Final = "trading_runtime"

# The table that identifies a trading database no matter what the file is called.
FORBIDDEN_TABLE: Final = "verdicts"


class DatabaseIsolationError(RuntimeError):
    """The configured database is, or might be, an execution database.

    Named so a caller can report it as a refusal rather than as an unexpected crash —
    this is the guard working, not the guard failing.
    """


def check_database_path(path: Path | str) -> Path:
    """Return the resolved path, or refuse to let the process continue.

    Called before any connection is opened. Resolves first so `..` segments and symlinked
    parents cannot smuggle the path past the directory check.
    """
    resolved = Path(path).expanduser().resolve()

    for part in resolved.parts:
        if part.lower() == FORBIDDEN_DIRECTORY:
            raise DatabaseIsolationError(
                f"refusing to open {resolved}: it resolves inside a "
                f"{FORBIDDEN_DIRECTORY!r} directory. This reader must never share state "
                f"with the trading project."
            )

    _refuse_trading_schema(resolved)
    return resolved


def _refuse_trading_schema(resolved: Path) -> None:
    """Refuse a database that carries the trading runtime's `verdicts` table.

    A file that does not exist yet is fine — it is about to be created here. A file that
    exists but is not a database is left to the caller: that is a different failure, and
    reporting it as an isolation breach would be a lie.
    """
    if not resolved.is_file():
        return

    try:
        connection = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    except sqlite3.Error:
        return
    try:
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND lower(name) = ?",
            (FORBIDDEN_TABLE,),
        ).fetchone()
    except sqlite3.DatabaseError:
        # Not a readable SQLite file. Not our failure to name.
        return
    finally:
        connection.close()

    if row is not None:
        raise DatabaseIsolationError(
            f"refusing to open {resolved}: it contains a {FORBIDDEN_TABLE!r} table, which "
            f"belongs to the trading runtime. Point `database.path` at this project's own "
            f"file."
        )
