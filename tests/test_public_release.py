"""Regression checks for the offline demo, read-only MCP, and shared execution lock."""

import sqlite3
import threading
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from engine.demo import seed_demo
from engine.loop import Loop
from engine.model.client import ModelReply
from engine.stages import Ledger
from engine.store.schema import connect
from engine.universe.config import load_params, load_universe
from engine.web.app import RefreshState, create_app

NOW = datetime(2026, 10, 7, 12, tzinfo=UTC)


def test_demo_renders_real_views_and_blocks_paid_endpoints(tmp_path):
    database = tmp_path / "demo.db"
    params, universe = load_params(), load_universe()
    connection = connect(database)
    try:
        seed_demo(connection, universe, params, NOW)
        assert connection.execute("SELECT sum(cost_usd) FROM passes").fetchone()[0] == 0
    finally:
        connection.close()

    def forbid_calls():
        raise AssertionError("A demo attempted to construct a model stage.")

    app = create_app(
        params=params,
        universe=universe,
        read_only=True,
        clock=lambda: NOW,
        connection_factory=lambda: connect(database),
        stages_factory=forbid_calls,
    )
    with TestClient(app, client=("127.0.0.1", 1234)) as client:
        assert "Demo mode" in client.get("/").text
        assert "Fictional" in client.get("/entity/USD").text
        assert client.get("/instrument/NAS100USD").status_code == 200
        for endpoint in ("/refresh", "/chat", "/chat/stream"):
            assert client.post(endpoint, data={"question": "anything"}).status_code == 405


def test_mcp_cannot_write_or_create_a_database(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    import mcp_server

    database = tmp_path / "saved.db"
    connection = connect(database)
    connection.close()
    monkeypatch.setattr(mcp_server, "PARAMS", replace(load_params(), database_path=database))
    connection = mcp_server._db()
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("CREATE TABLE should_not_exist (id INTEGER)")
    finally:
        connection.close()
    missing = tmp_path / "missing.db"
    monkeypatch.setattr(mcp_server, "PARAMS", replace(load_params(), database_path=missing))
    with pytest.raises(FileNotFoundError, match="saved read"):
        mcp_server._db()
    assert not missing.exists()


def test_web_refresh_holds_the_same_lock_as_the_scheduler():
    lock = threading.Lock()
    state = RefreshState(lock)
    assert state.begin()
    assert state.running
    assert not lock.acquire(blocking=False)
    assert not state.begin()
    state.finish()
    assert not state.running
    assert lock.acquire(blocking=False)
    lock.release()


def test_failed_database_open_does_not_leave_the_scheduler_locked():
    def unavailable():
        raise OSError("temporary database failure")

    loop = Loop(
        connection_factory=unavailable,
        universe=load_universe(),
        params=load_params(),
        stages_factory=lambda: None,
    )
    assert loop.hourly() is None
    assert loop.lock.acquire(blocking=False)
    loop.lock.release()


def test_prompt_cache_tokens_are_included_in_estimated_cost():
    reply = ModelReply(
        text="answer",
        model="example",
        replayed=False,
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=1000,
        cache_write_tokens=200,
    )
    prices = {"example": {"input": 4, "output": 20, "cache_read": 0.4, "cache_write": 5}}
    ledger = Ledger()
    ledger.record("narrative", reply, prices)
    assert ledger.total_usd == pytest.approx((400 + 1000 + 400 + 1000) / 1_000_000)
    ledger.record("narrative", replace(reply, replayed=True), prices)
    assert ledger.total_usd == pytest.approx(0.0028)
