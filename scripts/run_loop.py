"""Run the local dashboard and, when enabled, scheduled research in one process.

    python scripts/run_loop.py

Scheduling is disabled by default in config/params.yaml. Enabling it installs
release triggers and any configured periodic reads. Failed refreshes are logged;
the dashboard shows the age of the latest completed read. Ctrl-C stops both."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402

from engine.loop import Loop  # noqa: E402
from engine.model.client import ModelClient  # noqa: E402
from engine.stages import Stages  # noqa: E402
from engine.store.schema import connect  # noqa: E402
from engine.universe.config import load_params, load_universe  # noqa: E402
from engine.web.app import create_app  # noqa: E402


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(name)-14s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log = logging.getLogger("engine")

    params = load_params()
    universe = load_universe()

    loop = Loop(
        connection_factory=lambda: connect(params.database_path),
        universe=universe,
        params=params,
        stages_factory=lambda: Stages(
            ModelClient(cache_dir=params.cache_dir), params.models, params.prices
        ),
    )

    if loop.start():
        upcoming = loop.pending_releases()
        log.info(
            "loop running — %s, plus %s release trigger(s) in the window",
            f"every {params.hourly_minutes}m" if params.ticks_on_a_clock else "no scheduled tick",
            len(upcoming),
        )
        for trigger in upcoming[:6]:
            log.info("  release %s", trigger.describe())
    else:
        log.warning("loop is DISABLED in config/params.yaml — serving the board only")

    log.info("board on http://127.0.0.1:8000")
    try:
        # One lock across both, so a forced refresh and an hourly tick cannot overlap.
        uvicorn.run(
            create_app(params=params, universe=universe, refresh_lock=loop.lock),
            host="127.0.0.1",
            port=8000,
            log_level="warning",
        )
    except KeyboardInterrupt:  # pragma: no cover
        pass
    finally:
        loop.stop()
        log.info("stopped")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
