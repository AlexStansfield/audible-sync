"""
Runs the pipeline on an interval, and on demand, one run at a time.

A single daemon thread waits until the next run is due, runs the pipeline, and waits
again. "Due" is worked out from the newest `sync_runs` row rather than from anything
held in memory, so a restart carries on where the previous process left off instead of
syncing on every boot. `trigger()` wakes the thread early for a manual run, `cancel()`
sets the event the pipeline checks between chunks, and `wake()` makes the thread
re-read the settings so a changed interval applies at once.

Everything the thread reads is injected - the settings loader, the pipeline, the clock
and the last-run lookup - so the tests drive it with fakes rather than with time.
"""

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from src.database import latest_sync_run_start
from src.runstate import RunStage, RunState
from src.settings import Settings

logger = logging.getLogger(__name__)

# The pipeline the scheduler runs: settings, where to report, and the stop event
Pipeline = Callable[[Settings, RunState, threading.Event], None]


def _now() -> datetime:
    return datetime.now(UTC)


class Scheduler:
    """
    One background thread, one run at a time.

    Args:
        load_settings: Called before every run and every wait, so a changed interval or
            a disabled schedule takes effect without a restart
        run: The pipeline to execute (see `Pipeline`)
        state: Where the run in flight reports; shared with the API
        last_run_start: The newest run's `started_at`, ISO 8601, or None
        now: The clock, injectable for tests
        stop_timeout: How long `stop()` waits for a run to hand back its book
    """

    def __init__(
        self,
        *,
        load_settings: Callable[[], Settings],
        run: Pipeline,
        state: RunState,
        last_run_start: Callable[[], str | None] = latest_sync_run_start,
        now: Callable[[], datetime] = _now,
        stop_timeout: float = 60.0,
    ) -> None:
        self._load_settings = load_settings
        self._run = run
        self._state = state
        self._last_run_start = last_run_start
        self._now = now
        self._stop_timeout = stop_timeout

        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._cancel = threading.Event()
        self._stopping = False
        self._triggered = False
        self._running = False
        self._thread: threading.Thread | None = None

    # --- control -------------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """
        Stop the thread, cancelling any run in flight first.

        This is what a `docker stop` turns into: the run hands its book back to the
        queue and the process exits cleanly, rather than leaving a `downloading` row
        for the stale-claim timeout to recover hours later.
        """
        with self._lock:
            self._stopping = True
            self._cancel.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(self._stop_timeout)
            if self._thread.is_alive():
                logger.warning("Scheduler thread did not stop within %.0fs", self._stop_timeout)

    def trigger(self) -> bool:
        """
        Ask for a run now. Returns False if one is already in flight.

        Works whether or not the schedule is enabled: a manual run is the point of
        turning the schedule off.
        """
        with self._lock:
            if self._running:
                return False
            self._triggered = True
        self._wake.set()
        return True

    def cancel(self) -> bool:
        """Ask the run in flight to stop. Returns False if nothing is running."""
        with self._lock:
            if not self._running:
                return False
            self._cancel.set()
        self._state.set_stage(RunStage.CANCELLING)
        return True

    def wake(self) -> None:
        """Re-read the settings now, for a changed interval or a toggled schedule."""
        self._wake.set()

    # --- state ---------------------------------------------------------------------

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def next_run_at(self, settings: Settings | None = None) -> datetime | None:
        """
        When the schedule will next start a run, or None when it is disabled.

        Never earlier than now: a run that is overdue is reported as due now, and one
        in flight reports the run after it.
        """
        settings = settings or self._load_settings()
        if not settings.sync_enabled:
            return None

        now = self._now()
        last = self._last_run_start()
        if last is None:
            return now
        due = datetime.fromisoformat(last) + timedelta(minutes=settings.sync_interval_minutes)
        return max(due, now)

    def status(self, settings: Settings | None = None) -> dict[str, Any]:
        """The scheduler as the API reports it."""
        settings = settings or self._load_settings()
        due = self.next_run_at(settings)
        return {
            "enabled": settings.sync_enabled,
            "interval_minutes": settings.sync_interval_minutes,
            "next_run_at": None if due is None else due.replace(microsecond=0).isoformat(),
            "running": self.running,
        }

    # --- the thread ----------------------------------------------------------------

    def _delay(self, settings: Settings) -> float | None:
        """Seconds until the next scheduled run: 0 when due, None when there is no schedule."""
        due = self.next_run_at(settings)
        if due is None:
            return None
        return max((due - self._now()).total_seconds(), 0.0)

    def _loop(self) -> None:
        while True:
            with self._lock:
                if self._stopping:
                    return
                triggered = self._triggered

            try:
                settings = self._load_settings()
            except Exception:
                # A bad value saved to the table must not kill the thread; wait for the
                # next change rather than spinning
                logger.exception("Could not read the settings, scheduler waiting for them to be fixed")
                self._wait(None)
                continue

            delay = 0.0 if triggered else self._delay(settings)
            if delay is None or delay > 0:
                self._wait(delay)
                continue

            self._execute(settings)

    def _wait(self, seconds: float | None) -> None:
        # None waits until something wakes us: a trigger, a settings change, or stop
        self._wake.wait(seconds)
        self._wake.clear()

    def _execute(self, settings: Settings) -> None:
        with self._lock:
            if self._stopping:
                return
            self._triggered = False
            self._running = True
            self._cancel.clear()

        logger.info("Starting sync run")
        try:
            self._run(settings, self._state, self._cancel)
        except Exception:
            # The pipeline has already recorded the failure on its run row; the thread
            # must survive to try again next interval
            logger.exception("Sync run failed")
        finally:
            with self._lock:
                self._running = False
