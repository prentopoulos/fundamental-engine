"""Checking that stored narratives cite records that exist.

`design.md` §4 pays for the fact-sheet constraint with a promise: a paragraph citing an
identifier that resolves to no record is **detectable by query, not by reading**. This
module is that query.

It is the spot check the whole tracing rule rests on. If it is never run, storing the
citations buys nothing — so it runs at the end of every pass, and its findings land on the
pass record where the interface shows them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from sqlite3 import Connection

INVENTED_CITATION_WARNING = "invented_citation"


@dataclass(frozen=True)
class BadCitation:
    """One narrative claiming a situation that does not exist."""

    pass_id: int
    entity: str
    identifier: str

    def __str__(self) -> str:
        return f"{INVENTED_CITATION_WARNING}:{self.entity}:{self.identifier}"


def invented_citations(connection: Connection, pass_id: int | None = None) -> list[BadCitation]:
    """Narratives citing identifiers that resolve to no stored situation.

    Checked against every situation ever stored, including archived and resolved ones: a
    paragraph may legitimately cite a story that has since been resolved, and treating that
    as invention would cry wolf on the one signal that has to stay trustworthy.
    """
    known = {row["identifier"] for row in connection.execute("SELECT identifier FROM situations")}

    sql = "SELECT pass_id, entity, cited_ids FROM narratives"
    params: tuple[object, ...] = ()
    if pass_id is not None:
        sql += " WHERE pass_id = ?"
        params = (pass_id,)

    found: list[BadCitation] = []
    for row in connection.execute(sql, params):
        try:
            cited = json.loads(row["cited_ids"])
        except json.JSONDecodeError:
            found.append(BadCitation(row["pass_id"], row["entity"], "<unreadable cited_ids>"))
            continue
        for identifier in cited:
            if str(identifier) not in known:
                found.append(BadCitation(row["pass_id"], row["entity"], str(identifier)))
    return found
