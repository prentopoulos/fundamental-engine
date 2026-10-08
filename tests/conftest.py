"""Shared fixtures. Nothing here reaches the network."""

from __future__ import annotations

import io
import tokenize
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def executable_source(source: str) -> str:
    """The code of a module with its comments and string literals removed.

    Several properties in this project are stated as "nothing here reads X" and are worth
    asserting over the source rather than trusting. Prose that *explains* why the engine
    never reads a released value would otherwise fail the very assertion it documents, so
    the docstrings and comments come out before the check.
    """
    kept: list[str] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type in (tokenize.COMMENT, tokenize.STRING, tokenize.NL, tokenize.NEWLINE):
            continue
        kept.append(token.string)
    return " ".join(kept)


@pytest.fixture
def fixture_body():
    """Read a recorded feed body by filename."""

    def _read(name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8")

    return _read
