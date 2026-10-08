"""The isolation guard refuses both ways in, and lets a clean path through."""

from __future__ import annotations

import sqlite3

import pytest

from engine.store.guard import DatabaseIsolationError, check_database_path


def test_refuses_a_path_inside_the_trading_project(tmp_path):
    sibling = tmp_path / "trading_runtime" / "build"
    sibling.mkdir(parents=True)

    with pytest.raises(DatabaseIsolationError, match="trading_runtime"):
        check_database_path(sibling / "execution.db")


def test_refuses_a_path_that_walks_into_the_trading_project(tmp_path):
    """`..` segments are resolved before the check, so they cannot smuggle a path past."""
    sibling = tmp_path / "trading_runtime"
    sibling.mkdir()
    innocent = tmp_path / "fundamental_engine"
    innocent.mkdir()

    with pytest.raises(DatabaseIsolationError):
        check_database_path(innocent / ".." / "trading_runtime" / "execution.db")


def test_refuses_a_database_holding_a_verdicts_table(tmp_path):
    """Catches a trading database moved somewhere the path check cannot see it."""
    disguised = tmp_path / "engine.db"
    connection = sqlite3.connect(disguised)
    connection.execute("CREATE TABLE verdicts (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()

    with pytest.raises(DatabaseIsolationError, match="verdicts"):
        check_database_path(disguised)


def test_allows_a_clean_path_that_does_not_exist_yet(tmp_path):
    target = tmp_path / "data" / "engine.db"
    assert check_database_path(target) == target.resolve()


def test_allows_this_projects_own_database(tmp_path):
    target = tmp_path / "engine.db"
    connection = sqlite3.connect(target)
    connection.execute("CREATE TABLE reads (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()

    assert check_database_path(target) == target.resolve()
