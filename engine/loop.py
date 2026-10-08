"""The unattended loop: two jobs, both idempotent, both recording what they did.

APScheduler in-process rather than an OS task (`design.md` §10). Nothing here is
time-critical: a missed 06:00 read is visible as a stale age on every page and is corrected
at 07:00, so there is no retry-within-tick, no recovery logic and no lock that can need
clearing. Recovery logic would be more code than the failure justifies.

**The scheduler ships disabled.** Enabling it is an explicit operator action taken only
after one manual pass has been read by hand, because the failure this project most needs to
avoid is one that runs cleanly and reads nonsense. A forced refresh stays available the
whole time the loop is off.

**A failed refresh logs and waits.** The pass records which stage failed and the previous
reads stay readable at their true age; the next trigger runs normally.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from engine.feeds.transport import Fetcher, fetch
from engine.passes import PassFailed, run
from engine.triggers import ReleaseTrigger, due_releases, release_triggers
from engine.universe.config import Params, Universe

log = logging.getLogger("engine.loop")


class Loop:
    """The two scheduled jobs, and the guard that keeps them from overlapping.

    One lock across both jobs, not one each: an hourly tick and a release trigger landing in
    the same minute would otherwise both ingest the same feed and both score the same
    headlines, and the second would open duplicate situations for stories the first had just
    opened.
    """

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        universe: Universe,
        params: Params,
        stages_factory: Callable[[], Any],
        *,
        fetcher: Fetcher = fetch,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.connection_factory = connection_factory
        self.universe = universe
        self.params = params
        self.stages_factory = stages_factory
        self.fetcher = fetcher
        self.clock = clock or (lambda: datetime.now(UTC))
        self.scheduler: Any = None
        # Public so the interface can share it: a forced refresh from the board and an
        # hourly tick must not both run a pass over the same feeds.
        self.lock = threading.Lock()
        self._fired: set[datetime] = set()

    # --- The jobs ------------------------------------------------------------------------

    def hourly(self) -> Any:
        """Ingest, triage and score what is new, update situations, re-run the arithmetic.

        A full pass, not the cheap poll: the arithmetic and the per-entity stages are what
        make the board change, and change detection already keeps a quiet hour cheap.
        """
        return self._guarded("hourly", trigger="hourly")

    def release(self, trigger: ReleaseTrigger) -> Any:
        """A watched high-impact event's scheduled time plus the configured delay.

        Every event that contributed to the collapsed trigger is recorded on the pass, so a
        cluster that produced one refresh can still be traced back to all four events.
        """
        return self._guarded(
            f"release {trigger.describe()}",
            trigger="release",
            trigger_events=trigger.events,
        )

    def forced(self) -> Any:
        """Re-decide everything, bypassing the replay cache. Always costs full price."""
        return self._guarded("forced", trigger="forced", forced=True)

    # --- Scheduling ----------------------------------------------------------------------

    def pending_releases(self) -> list[ReleaseTrigger]:
        """Release triggers still ahead of us, inside the calendar horizon."""
        connection = self.connection_factory()
        try:
            moment = self.clock()
            return release_triggers(
                connection, self.params, since=moment, until=moment + self.params.horizon
            )
        finally:
            connection.close()

    def catch_up(self) -> list[ReleaseTrigger]:
        """Run any release trigger missed while the runtime was down, once each.

        Run rather than skipped: a read that quietly missed a payrolls print is worse than a
        late one, because nothing on the board says it happened.
        """
        connection = self.connection_factory()
        try:
            missed = due_releases(connection, self.params, now=self.clock())
        finally:
            connection.close()

        for trigger in missed:
            log.info("running missed release trigger %s", trigger.describe())
            self.release(trigger)
        return missed

    def start(self) -> bool:
        """Start the scheduler, if configuration allows it. Returns whether it started.

        Returns `False` rather than raising when the loop is disabled: not starting is the
        shipped, correct state, and a caller that treats it as an error would make the
        default installation look broken.
        """
        if not self.params.scheduler_enabled:
            log.info("scheduler is disabled in config; forced refresh remains available")
            return False

        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.triggers.interval import IntervalTrigger

        self.catch_up()

        self.scheduler = BackgroundScheduler(timezone="UTC")
        if self.params.ticks_on_a_clock:
            self.scheduler.add_job(
                self.hourly,
                # Driven by config, not pinned to the top of the hour. Every page already
                # computes its staleness from `HOURLY_MINUTES`, so a scheduler that ignored
                # it would make the board state a cadence it does not keep — and the cadence
                # is the main lever on what the loop costs to run.
                IntervalTrigger(minutes=self.params.hourly_minutes),
                id="hourly",
                # Two ticks queueing behind a slow pass would run back to back over the same
                # feed. Coalesce them into one and let the next tick catch up.
                coalesce=True,
                max_instances=1,
            )
        else:
            # Release-only. Measured over a week, the scheduled ticks were about five times
            # the cost of everything else and moved a read least often — a quiet 02:00 tick
            # re-reads the same feed and re-derives the same board. The releases are what
            # change it, so they are what is worth paying for; anything else is a click.
            log.info("no scheduled tick — release triggers only, plus manual refresh")
        self.scheduler.add_job(
            self._schedule_releases,
            CronTrigger(minute=1),
            id="release-scan",
            coalesce=True,
            max_instances=1,
        )
        self._schedule_releases()
        self.scheduler.start()
        log.info("scheduler started")
        return True

    def stop(self) -> None:
        if self.scheduler is not None:
            self.scheduler.shutdown(wait=False)
            self.scheduler = None

    def _schedule_releases(self) -> None:
        """Put the next hour's release triggers on the scheduler, once each.

        Re-scanned every hour rather than once at startup, because the calendar is re-fetched
        on every full pass and a revised event time has to be picked up.
        """
        if self.scheduler is None:
            return
        from apscheduler.triggers.date import DateTrigger

        moment = self.clock()
        connection = self.connection_factory()
        try:
            upcoming = release_triggers(
                connection, self.params, since=moment, until=moment + timedelta(hours=2)
            )
        finally:
            connection.close()

        for trigger in upcoming:
            if trigger.at in self._fired:
                continue
            self._fired.add(trigger.at)
            self.scheduler.add_job(
                self.release,
                DateTrigger(run_date=trigger.at),
                args=[trigger],
                id=f"release-{trigger.at.isoformat()}",
                replace_existing=True,
            )

    # --- The guard -----------------------------------------------------------------------

    def _guarded(
        self,
        label: str,
        *,
        trigger: str,
        forced: bool = False,
        trigger_events: tuple[str, ...] = (),
    ) -> Any:
        """Run one pass under the overlap lock, logging a failure and returning.

        The failure is swallowed on purpose. This is the scheduler's thread: raising would
        take the job out of the scheduler in some configurations and leave the loop silently
        dead, which is precisely the failure the visible read age exists to make impossible.
        The pass record already carries the failing stage.
        """
        if not self.lock.acquire(blocking=False):
            log.info("skipping %s: a pass is already running", label)
            return None

        connection = None
        try:
            connection = self.connection_factory()
            result = run(
                connection,
                self.universe,
                self.params,
                self.stages_factory(),
                trigger=trigger,
                now=self.clock(),
                fetcher=self.fetcher,
                forced=forced,
                trigger_events=trigger_events,
            )
            log.info(
                "%s complete: pass %s, $%.4f, %s entities skipped",
                label,
                result.pass_id,
                result.cost_usd,
                len(result.skipped),
            )
            return result
        except PassFailed as failure:
            log.warning("%s failed at stage %s: %s", label, failure.stage, failure.cause)
            return None
        except Exception as error:  # noqa: BLE001 - the loop must survive a bad feed
            log.warning("%s failed: %s", label, error)
            return None
        finally:
            if connection is not None:
                connection.close()
            self.lock.release()
