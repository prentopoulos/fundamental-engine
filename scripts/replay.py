"""Replay archived headlines in date order to build a historical situation record.

Each day is processed with its own clock, so situations can open, evolve, and decay.
Titles come from sitemap URL slugs, which lose punctuation and exact figures; article
bodies are unavailable. Sitemap modification dates may differ from publication dates.
This utility is not a performance backtest and does not reproduce historical calendars.

Use --dry-run to inspect coverage without storing data or making model calls.
Use --full-read to finish with a current, live-feed pass after the archive walk.

    python scripts/replay.py --from 2026-08-08 --to 2026-08-20 --dry-run"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.feeds.items import Headline  # noqa: E402
from engine.feeds.transport import USER_AGENT, FetchedBody  # noqa: E402
from engine.model.client import ModelClient  # noqa: E402
from engine.passes import PassFailed, run  # noqa: E402
from engine.stages import Stages  # noqa: E402
from engine.store.schema import connect, transaction  # noqa: E402
from engine.universe.config import load_params, load_universe  # noqa: E402

SITEMAP = "https://investinglive.com/sitemaps/news/articles/{week}.xml"

# An empty RSS document. The replay drives the ordinary pass machinery, which always tries
# to ingest — handing it an empty feed makes that a no-op, and triage then picks up the
# backfilled rows through the same pending-work query a resumed pass uses.
EMPTY_FEED = '<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'


@dataclass(frozen=True)
class Archived:
    """One article as the sitemap describes it: a URL, a time, and a title from the slug."""

    published_at: datetime
    link: str
    title: str

    def as_headline(self, source: str) -> Headline:
        return Headline(
            title=self.title,
            body="",  # a sitemap carries none, and echoing the title would fake corroboration
            link=self.link,
            published_at=self.published_at,
            source_url=source,
        )


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read(20_000_000).decode("utf-8", "replace")


def weeks_covering(start: date, end: date) -> list[str]:
    """The ISO week files spanning a date range, plus one either side for safety."""
    seen: list[str] = []
    day = start - timedelta(days=7)
    while day <= end + timedelta(days=7):
        year, week, _ = day.isocalendar()
        label = f"{year}-W{week:02d}"
        if label not in seen:
            seen.append(label)
        day += timedelta(days=1)
    return seen


def title_from_slug(link: str) -> str:
    """Recover a headline from a URL slug.

    Lossy and honestly so: hyphens that were decimal points become spaces, possessives lose
    their apostrophe. The story survives, the exact figures do not.
    """
    slug = link.rstrip("/").rsplit("/", 1)[-1]
    words = slug.replace("-", " ").strip()
    return words[:1].upper() + words[1:] if words else ""


def archive(start: date, end: date) -> list[Archived]:
    """Every archived article in the range, oldest first."""
    found: dict[str, Archived] = {}
    for week in weeks_covering(start, end):
        try:
            body = fetch(SITEMAP.format(week=week))
        except Exception as error:  # noqa: BLE001 - a missing week is not fatal
            print(f"  ! {week}: {type(error).__name__}", file=sys.stderr)
            continue
        for entry in re.findall(r"<url>(.*?)</url>", body, re.DOTALL):
            loc = re.search(r"<loc>(.*?)<", entry)
            when = re.search(r"<lastmod>(.*?)<", entry)
            if not loc or not when:
                continue
            moment = datetime.fromisoformat(when.group(1))
            if not (start <= moment.date() <= end):
                continue
            title = title_from_slug(loc.group(1))
            if title:
                found[loc.group(1)] = Archived(moment, loc.group(1), title)
    return sorted(found.values(), key=lambda a: a.published_at)


def store(connection, batch: list[Archived], source: str) -> int:
    """Insert a day's articles as untriaged headlines. De-duplicates against what we hold."""
    added = 0
    with transaction(connection):
        for item in batch:
            headline = item.as_headline(source)
            cursor = connection.execute(
                "INSERT INTO headlines (id, title, body, link, published_at, source_url,"
                " ingested_at, triaged) VALUES (?, ?, ?, ?, ?, ?, ?, 0)"
                " ON CONFLICT (link) DO NOTHING",
                (
                    headline.id,
                    headline.title,
                    headline.body,
                    headline.link,
                    headline.published_at.isoformat(),
                    headline.source_url,
                    datetime.now(UTC).isoformat(),
                ),
            )
            added += cursor.rowcount
    return added


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="start", required=True, help="YYYY-MM-DD, inclusive")
    parser.add_argument("--to", dest="end", required=True, help="YYYY-MM-DD, inclusive")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch and count only — no model calls, nothing stored",
    )
    parser.add_argument(
        "--full-read",
        action="store_true",
        help="after the walk, run one complete pass so the board reflects the new set",
    )
    args = parser.parse_args(argv)

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if start > end:
        print("--from is after --to", file=sys.stderr)
        return 1

    print(f"Fetching the archive for {start} .. {end} …")
    items = archive(start, end)
    if not items:
        print("nothing found in that range", file=sys.stderr)
        return 1

    by_day: dict[date, list[Archived]] = {}
    for item in items:
        by_day.setdefault(item.published_at.date(), []).append(item)

    print(f"  {len(items)} articles across {len(by_day)} days\n")
    for day in sorted(by_day):
        print(f"    {day}  {len(by_day[day]):>3} articles")

    if args.dry_run:
        print(
            "\n  dry run — nothing stored, no model calls. "
            "Scoring uses configured models and may incur API charges."
        )
        print("  Sample headlines recovered from slugs:")
        for item in items[:5]:
            print(f"    {item.published_at:%Y-%m-%d}  {item.title[:84]}")
        return 0

    params, universe = load_params(), load_universe()
    connection = connect(params.database_path)

    def empty_feeds(url: str) -> FetchedBody:
        """Stand in for the headline feed during the walk, so ingest is a no-op.

        Only ever used with `poll_only=True`, which does not touch the calendar. The final
        read below uses the real fetcher — it needs today's actual calendar and headlines,
        because an RSS stub cannot stand in for the calendar's JSON response.
        """
        return FetchedBody(text=EMPTY_FEED, resolved_url=url)

    print("\nWalking forward. Each day is processed as that day.\n")
    spent = 0.0
    try:
        for day in sorted(by_day):
            added = store(connection, by_day[day], SITEMAP.format(week="archive"))
            # End of that day, so everything published on it is in the past for this pass.
            moment = datetime.combine(day, datetime.min.time(), UTC) + timedelta(
                hours=23, minutes=59
            )

            stages = Stages(ModelClient(cache_dir=params.cache_dir), params.models, params.prices)
            try:
                result = run(
                    connection,
                    universe,
                    params,
                    stages,
                    trigger="manual",
                    now=moment,
                    fetcher=empty_feeds,
                    poll_only=True,
                )
            except PassFailed as failure:
                print(f"  {day}  FAILED at {failure.stage}: {failure.cause}", file=sys.stderr)
                print("  The walk stops here; everything before it is stored and resumable.")
                return 1

            spent += result.cost_usd
            situations = connection.execute("SELECT count(*) FROM situations").fetchone()[0]
            print(
                f"  {day}  +{added:>3} articles   {situations:>3} situations   "
                f"${result.cost_usd:>5.3f}   (${spent:.2f} total)",
                flush=True,
            )

        if args.full_read:
            print("\nRunning one full read so the board reflects the matured set …")
            stages = Stages(ModelClient(cache_dir=params.cache_dir), params.models, params.prices)
            final = run(
                connection,
                universe,
                params,
                stages,
                trigger="manual",
                now=datetime.now(UTC),
            )
            spent += final.cost_usd
            print(f"  pass {final.pass_id} complete, ${final.cost_usd:.3f}")

        print(f"\nDone. ${spent:.2f} spent. Inspect with scripts/inspect_pass.py")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
