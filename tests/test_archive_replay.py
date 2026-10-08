"""Archive replay must keep historical polls separate from a current calendar read."""

from datetime import UTC, datetime
from types import SimpleNamespace

from engine.feeds.calendar_feed import CALENDAR_URL, parse_calendar
from engine.universe.config import load_params
from scripts import replay


def test_full_read_uses_a_calendar_capable_fetcher(tmp_path, monkeypatch):
    """An RSS-only replay stub must never replace the final pass's calendar fetch."""
    from dataclasses import replace

    monkeypatch.setattr(
        replay, "load_params", lambda: replace(load_params(), database_path=tmp_path / "replay.db")
    )
    monkeypatch.setattr(
        replay,
        "archive",
        lambda *_: [
            replay.Archived(
                datetime(2026, 8, 8, 12, tzinfo=UTC),
                "https://example.org/research-note/",
                "Research note",
            )
        ],
    )
    monkeypatch.setattr(replay, "ModelClient", lambda **_: object())
    monkeypatch.setattr(replay, "Stages", lambda *_: object())
    modes = []

    def calendar_aware_pass(*_, **options):
        polling = options.get("poll_only", False)
        modes.append(polling)
        if not polling:
            # A live calendar returns JSON. The historical RSS stub used to make this fail.
            fetcher = options.get("fetcher")
            body = fetcher(CALENDAR_URL).text if fetcher else "[]"
            parse_calendar(body)
        return SimpleNamespace(cost_usd=0, pass_id=1)

    monkeypatch.setattr(replay, "run", calendar_aware_pass)
    assert replay.main(["--from", "2026-08-08", "--to", "2026-08-08", "--full-read"]) == 0
    assert modes == [True, False]
