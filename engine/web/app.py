"""The loopback-only, read-only operator interface.

Two views: the board, and one entity's page. Bound to `127.0.0.1` and guarded by middleware
that refuses anything not from loopback — belt and braces, because binding alone is a
configuration away from being wrong and this database is one person's private read.

**No endpoint mutates a read.** The single POST forces a refresh, which re-derives the board
from the feeds; nothing here edits a score, a situation or a paragraph. There is deliberately
no way to hand-correct a read from the interface: a board the operator has been editing is no
longer a record of what the engine concluded.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import (
    HTMLResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.templating import Jinja2Templates

from engine.desk.sheet import brief_sheet
from engine.feeds.transport import Fetcher, fetch
from engine.model.client import ModelClient
from engine.passes import PassFailed, latest_failure, latest_pass, run
from engine.reading.states import Axis
from engine.stages import Stages
from engine.store.schema import connect
from engine.universe.config import Params, Universe, load_params, load_universe
from engine.web.scale import neutral_band, position, tone_of, what_this_week_does
from engine.web.views import Age, agreement, axis_verdict, entity_view, instrument_rows

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

LOOPBACK = {"127.0.0.1", "::1", "localhost"}

# The board groups rows by asset class rather than listing nineteen tickers flat: an
# operator scanning for "what should I look at" reads within a class, and a Nasdaq row
# sitting between two FX crosses has to be found rather than seen.
GROUP_LABELS = {
    "fx": "FOREX",
    "metals_energy": "METALS & ENERGY",
    "indices": "INDICES",
}

# What one row of each group is called, for the count beside the heading.
GROUP_UNITS = {"fx": "pairs"}


def _grouped(rows, universe):
    """Rows by asset class, each group still in divergence rank order.

    Group order follows `instruments.yaml`, not the ranking: the operator learns where the
    indices block sits on the page and reaches for it, and a board whose sections reorder
    themselves every hour takes that away. Rank still decides the order *within* a group,
    which is where it earns its keep.
    """
    order = list(dict.fromkeys(i.group for i in universe.instruments))
    buckets: dict[str, list] = {group: [] for group in order}
    for row in rows:
        buckets.setdefault(row.group, []).append(row)
    return [(group, buckets[group]) for group in order if buckets[group]]


class RefreshState:
    """Whether a forced refresh is running, and how the last one ended.

    A lock rather than a flag: the guard has to hold across the whole pass, and two clicks
    a second apart on a page that has not re-rendered would otherwise both pass a flag
    check and start two refreshes over the same feeds.

    The lock is injectable so the scheduler can share it. Running the loop and the board in
    one process — which `scripts/run_loop.py` does — they would otherwise guard themselves
    but not each other, and an hourly tick landing during a forced refresh would run a
    second pass over the same feeds and bill for it twice.
    """

    def __init__(self, lock: threading.Lock | None = None) -> None:
        self._lock = lock or threading.Lock()
        self.running = False
        self.failed_stage: str | None = None
        self.failure: str | None = None

    def begin(self) -> bool:
        # Hold the shared execution lock until finish(), so a scheduled pass cannot
        # overlap a manual refresh. A busy lock returns immediately to the browser.
        if not self._lock.acquire(blocking=False):
            return False
        self.running = True
        self.failed_stage = None
        self.failure = None
        return True

    def finish(self, failed_stage: str | None = None, failure: str | None = None) -> None:
        self.running = False
        self.failed_stage = failed_stage
        self.failure = failure
        self._lock.release()


def create_app(
    params: Params | None = None,
    universe: Universe | None = None,
    connection_factory=None,
    stages_factory=None,
    fetcher: Fetcher = fetch,
    clock=None,
    refresh_lock: threading.Lock | None = None,
    read_only: bool = False,
) -> FastAPI:
    """Build the app. Every dependency is injectable so the tests never touch a real feed."""
    params = params or load_params()
    universe = universe or load_universe()
    now = clock or (lambda: datetime.now(UTC))
    refresh_state = RefreshState(refresh_lock)

    def open_connection():
        return connection_factory() if connection_factory else connect(params.database_path)

    def build_stages():
        if stages_factory:
            return stages_factory()
        return Stages(
            client=ModelClient(cache_dir=params.cache_dir),
            models=params.models,
            prices=params.prices,
        )

    app = FastAPI(title="Fundamental Engine", docs_url=None, redoc_url=None)
    app.state.refresh = refresh_state

    @app.middleware("http")
    async def loopback_only(request: Request, call_next):
        """Refuse anything not from loopback, whatever the bind address ended up being."""
        host = request.client.host if request.client else None
        if host not in LOOPBACK:
            return Response("this interface serves loopback only", status_code=403)
        if read_only and request.method not in {"GET", "HEAD", "OPTIONS"}:
            return Response(
                "Demo mode is read-only; refresh and chat require live mode.", status_code=405
            )
        return await call_next(request)

    def context(connection, moment: datetime) -> dict[str, Any]:
        """The header every view carries: age, staleness, cadence, next triggers, warnings."""
        recent = latest_pass(connection)
        age = Age(
            authored_at=_moment(recent["finished_at"]) if recent else None,
            now=moment,
            stale_after=params.stale_after,
        )
        warnings = json.loads(recent["warnings"]) if recent else []
        return {
            "demo": read_only,
            "pass": recent,
            "failure": latest_failure(connection),
            "age": age,
            "warnings": warnings,
            "cadence": params.cadence_words,
            "scheduler_enabled": params.scheduler_enabled,
            "next_scheduled": (
                _next_hour(moment, params)
                if params.scheduler_enabled and params.ticks_on_a_clock
                else None
            ),
            "next_release": _next_release(connection, params, moment),
            "refresh": refresh_state,
            "cost": recent["cost_usd"] if recent else 0.0,
        }

    @app.get("/", response_class=HTMLResponse)
    def board(request: Request):
        moment = now()
        connection = open_connection()
        try:
            base = context(connection, moment)
            rows = (
                instrument_rows(connection, universe, params, base["pass"]["id"], moment)
                if base["pass"]
                else []
            )
            band_from, band_width = neutral_band(params)
            return TEMPLATES.TemplateResponse(
                request,
                "board.html",
                {
                    **base,
                    "rows": rows,
                    "grouped": _grouped(rows, universe),
                    "group_labels": GROUP_LABELS,
                    "group_units": GROUP_UNITS,
                    "band_from": band_from,
                    "band_width": band_width,
                    "crossing_count": sum(1 for row in rows if row.crosses_zero),
                    "unreadable_count": sum(1 for row in rows if row.unreadable),
                    "entity_count": len(universe.entities),
                    "title": "Instruments",
                },
            )
        finally:
            connection.close()

    @app.get("/entity/{entity}", response_class=HTMLResponse)
    def entity(request: Request, entity: str):
        moment = now()
        connection = open_connection()
        try:
            base = context(connection, moment)
            view = (
                entity_view(
                    connection, universe, params, base["pass"]["id"], entity.upper(), moment
                )
                if base["pass"]
                else None
            )
            if view is None:
                return TEMPLATES.TemplateResponse(
                    request,
                    "missing.html",
                    {**base, "entity": entity.upper(), "title": entity.upper()},
                    status_code=404,
                )
            ordered = list(universe.entities)
            index = ordered.index(view.entity) if view.entity in ordered else 0
            band_from, band_width = neutral_band(params)
            return TEMPLATES.TemplateResponse(
                request,
                "entity.html",
                {
                    **base,
                    "view": view,
                    "title": view.entity,
                    "previous": ordered[index - 1],
                    "next": ordered[(index + 1) % len(ordered)],
                    "axes": Axis,
                    "band_from": band_from,
                    "band_width": band_width,
                    # Passed as callables so the template renders rather than decides.
                    "tone": lambda cell: (
                        "blank" if cell is None or cell.unreadable else tone_of(cell.state)
                    ),
                    "bar_position": position,
                    "agreement": agreement,
                    "verdicts": {
                        "Policy": axis_verdict(view.policy, params),
                        "Direction": axis_verdict(view.directional, params),
                    },
                    "pull_lede": _pull_lede(view),
                    "decisive": _decisive_line(connection, view),
                },
            )
        finally:
            connection.close()

    @app.get("/instrument/{ticker}", response_class=HTMLResponse)
    def instrument(request: Request, ticker: str):
        """One instrument's legs broken out — which entity contributes what, on which axis."""
        moment = now()
        connection = open_connection()
        try:
            base = context(connection, moment)
            rows = (
                instrument_rows(connection, universe, params, base["pass"]["id"], moment)
                if base["pass"]
                else []
            )
            wanted = ticker.upper()
            index = next((i for i, row in enumerate(rows) if row.ticker == wanted), None)
            if index is None:
                return TEMPLATES.TemplateResponse(
                    request,
                    "missing.html",
                    {**base, "entity": wanted, "title": wanted},
                    status_code=404,
                )
            band_from, band_width = neutral_band(params)
            return TEMPLATES.TemplateResponse(
                request,
                "instrument.html",
                {
                    **base,
                    "row": rows[index],
                    "title": wanted,
                    "previous": rows[index - 1].ticker,
                    "next": rows[(index + 1) % len(rows)].ticker,
                    "band_from": band_from,
                    "band_width": band_width,
                    "leg_roles": _leg_roles(universe.instrument(wanted)),
                },
            )
        finally:
            connection.close()

    def _chat_history(connection, limit: int = 30) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT id, asked_at, question, answer, cost_usd FROM chats "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        ][::-1]

    @app.get("/chat", response_class=HTMLResponse)
    def chat_page(request: Request):
        moment = now()
        connection = open_connection()
        try:
            base = context(connection, moment)
            spent = connection.execute("SELECT sum(cost_usd) s FROM chats").fetchone()["s"] or 0.0
            return TEMPLATES.TemplateResponse(
                request,
                "chat.html",
                {
                    **base,
                    "history": _chat_history(connection),
                    "chat_spent": spent,
                    "title": "Desk",
                },
            )
        finally:
            connection.close()

    @app.post("/chat")
    def ask(request: Request, question: str = Form("")):
        """Answer one question from the current read. Bills one model call.

        Stored either way, with its cost: a brief that later turns out to have been wrong
        is only findable if it was written down, and an unlogged model call is spend the
        operator cannot see.
        """
        text = question.strip()
        if not text:
            return RedirectResponse("/chat", status_code=303)

        moment = now()
        connection = open_connection()
        try:
            recent = latest_pass(connection)
            stages = build_stages()
            try:
                produced = stages.chat(brief_sheet(connection, universe, params, moment), text)
                answer, model = produced.answer, produced.model
                cost = stages.ledger.total_usd
            except Exception as error:  # noqa: BLE001 - a failed brief must not 500 the board
                answer, model, cost = _why_it_failed(error), "-", 0.0

            connection.execute(
                "INSERT INTO chats (asked_at, question, answer, pass_id, model, cost_usd) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (moment.isoformat(), text, answer, recent["id"] if recent else None, model, cost),
            )
        finally:
            connection.close()
        return RedirectResponse("/chat", status_code=303)

    @app.post("/chat/stream")
    def ask_streaming(question: str = Form("")):
        """The same answer as `/chat`, streamed, and stored when it finishes.

        Server-sent events rather than a websocket: this is one-way text from server to
        page, which is exactly what SSE is, and it needs no protocol upgrade or reconnection
        logic. The page still POSTs to `/chat` when JavaScript is off.
        """
        text = question.strip()
        if not text:
            return Response("", status_code=204)

        def events():
            moment = now()
            connection = open_connection()
            try:
                recent = latest_pass(connection)
                stages = build_stages()
                answer, model, cost = "", "-", 0.0
                try:
                    sheet = brief_sheet(connection, universe, params, moment)
                    for piece in stages.chat_stream(sheet, text):
                        if isinstance(piece, str):
                            answer += piece
                            yield f"event: text\ndata: {json.dumps(piece)}\n\n"
                        else:
                            model, cost = piece.model, stages.ledger.total_usd
                except Exception as error:  # noqa: BLE001 - the page must not hang on a failure
                    answer = _why_it_failed(error)
                    yield f"event: text\ndata: {json.dumps(answer)}\n\n"

                connection.execute(
                    "INSERT INTO chats (asked_at, question, answer, pass_id, model, cost_usd) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        moment.isoformat(),
                        text,
                        answer,
                        recent["id"] if recent else None,
                        model,
                        cost,
                    ),
                )
                yield f"event: done\ndata: {json.dumps({'cost': round(cost, 4)})}\n\n"
            finally:
                connection.close()

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            # Without these a proxy or the browser will buffer the whole response and
            # deliver it at the end, which is the behaviour streaming exists to remove.
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/refresh")
    def force_refresh():
        """Re-decide everything, bypassing the replay cache. The one path that always bills.

        Runs inline rather than in a background task: the guard has to outlive the request,
        and a refresh that returned immediately would leave the operator looking at a board
        that has not changed yet with no way to tell whether anything is happening.
        """
        if not refresh_state.begin():
            return RedirectResponse("/", status_code=303)

        connection = None
        try:
            connection = open_connection()
            run(
                connection,
                universe,
                params,
                build_stages(),
                trigger="forced",
                now=now(),
                fetcher=fetcher,
                forced=True,
            )
            refresh_state.finish()
        except PassFailed as failure:
            refresh_state.finish(failed_stage=failure.stage, failure=str(failure.cause))
        except Exception as error:  # noqa: BLE001 - the interface must not 500 on a bad feed
            refresh_state.finish(failed_stage="pass", failure=str(error))
        finally:
            if connection is not None:
                connection.close()

        return RedirectResponse("/", status_code=303)

    return app


def _leg_roles(instrument) -> dict[str, str]:
    """What each leg contributes, in words, so the beta table is legible on the page.

    `USD · policy × −1.3` says more than "USD" does: it is the reason a hawkish Fed
    reads bearish hardest for the Nasdaq, and it is otherwise buried in a YAML file.
    """
    roles: dict[str, list[str]] = {}
    for axis_name, legs in (
        ("policy", instrument.policy_legs),
        ("direction", instrument.directional_legs),
    ):
        for entity, weight in legs:
            if weight == 1.0:
                note = axis_name
            elif weight == -1.0:
                note = f"{axis_name} (subtracted)"
            else:
                note = f"{axis_name} × {weight:g}".replace("-", "−")
            roles.setdefault(entity, []).append(note)
    return {entity: " · ".join(notes) for entity, notes in roles.items()}


def _pull_lede(view) -> str:
    """One sentence on what the week does to this read.

    Built from the two lanes' states, not written by a model: it is a statement about four
    enum values, and a sentence on the board with no record behind it is the thing the whole
    tracing rule exists to prevent.
    """
    if view.expectation is None:
        return "Nothing scheduled — this read stands as written."

    policy = what_this_week_does(view.policy.state, view.expectation.policy)
    directional = what_this_week_does(view.directional.state, view.expectation.directional)
    crossing = "Crosses zero this week"

    if policy == crossing and directional == crossing:
        return "Both axes turn over this week."
    if policy == crossing:
        return f"Policy turns over this week — {view.expected_policy.words}."
    if directional == crossing:
        return f"Direction turns over this week — {view.expected_directional.words}."
    if view.policy.unreadable and view.directional.unreadable:
        return "Nothing on file yet. The week is what gives this a read at all."
    if policy.startswith("Nothing") and directional.startswith("Nothing"):
        return "Nothing scheduled moves either axis. The read stands as written."
    return f"{policy}, and direction {directional[0].lower()}{directional[1:]}."


def _decisive_line(connection, view) -> str:
    """The single event the week turns on, named and timed."""
    if view.expectation is None or view.expectation.decisive_event_id is None:
        return ""
    row = connection.execute(
        "SELECT title, scheduled_at FROM calendar_events WHERE id = ?",
        (view.expectation.decisive_event_id,),
    ).fetchone()
    if row is None:
        return ""
    when = _moment(row["scheduled_at"])
    return f"{row['title']} · {when.strftime('%a %H:%M')}" if when else str(row["title"])


def _next_hour(moment: datetime, params: Params) -> datetime:
    minutes = params.hourly_minutes
    elapsed = moment.minute % minutes
    return (moment - timedelta(minutes=elapsed)).replace(second=0, microsecond=0) + timedelta(
        minutes=minutes
    )


def _next_release(connection, params: Params, moment: datetime) -> dict[str, Any] | None:
    """The next watched High-impact event's trigger time, from the schedule alone.

    No `actual` is consulted; the calendar says when, and the headline feed says what.
    """
    placeholders = ", ".join("?" for _ in params.release_watch_list)
    row = connection.execute(
        "SELECT currency, title, scheduled_at FROM calendar_events"
        f" WHERE impact = 'High' AND currency IN ({placeholders}) AND scheduled_at >= ?"
        " ORDER BY scheduled_at LIMIT 1",
        (*params.release_watch_list, moment.isoformat()),
    ).fetchone()
    if row is None:
        return None
    scheduled = _moment(row["scheduled_at"])
    return {
        "currency": row["currency"],
        "title": row["title"],
        "at": (scheduled + params.release_delay) if scheduled else None,
    }


def _moment(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def main() -> int:  # pragma: no cover - the server entry point
    """`python -m engine.web` — serve on 127.0.0.1:8000."""
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8000, log_level="info")
    return 0


def _why_it_failed(error: Exception) -> str:
    """A failed brief in words, not a pasted API error.

    The credit case is the one that will happen most and the one a raw 400 explains worst:
    the operator sees a wall of JSON where the answer should be and reasonably concludes the
    page is broken, when the engine is fine and the account is empty.
    """
    text = str(error)
    if "credit balance is too low" in text:
        return (
            "Out of API credit — nothing left to spend on an answer.\n\n"
            "Top up at console.anthropic.com under Plans & Billing. The board and every "
            "read on it are unaffected; the scheduled passes will start working again on "
            "their own once there is credit, with no restart needed."
        )
    if "authentication" in text.lower() or "invalid x-api-key" in text.lower():
        return (
            "The API key was refused. Check ANTHROPIC_API_KEY in .env — the board itself "
            "keeps working, it just cannot ask anything."
        )
    if "rate limit" in text.lower():
        return "Rate limited by the API. Wait a moment and ask again."
    return f"The desk could not answer: {text}"
