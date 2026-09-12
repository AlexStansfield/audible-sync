import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from src.runstate import RunStage, RunState
from src.scheduler import Scheduler
from tests.conftest import make_settings

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


class FakeRun:
    """
    A pipeline stand-in that records its calls and can block until released.

    `started` is set when a run begins; the run then waits on `release` (if given)
    or the cancel event, whichever the test wants to drive.
    """

    def __init__(self, *, block=False, raise_once=None):
        self.calls = []
        self.started = threading.Event()
        self.finished = threading.Event()
        self.release = threading.Event()
        self.block = block
        self.raise_once = raise_once

    def __call__(self, settings, state, cancel):
        self.calls.append((settings, state, cancel))
        self.started.set()
        try:
            if self.block:
                # Wait for the test, or for a cancel, like the real pipeline would
                while not self.release.is_set() and not cancel.is_set():
                    self.release.wait(0.01)
            if self.raise_once is not None:
                error, self.raise_once = self.raise_once, None
                raise error
        finally:
            self.finished.set()


def _settle(scheduler, timeout=2.0):
    """
    Wait for the scheduler to mark its run over.

    The fake signals `finished` from inside the run, a moment before the scheduler's own
    bookkeeping flips `running` off; a test that re-triggers in that gap is refused.
    """
    deadline = time.monotonic() + timeout
    while scheduler.running and time.monotonic() < deadline:
        time.sleep(0.005)
    assert not scheduler.running


@pytest.fixture
def scheduler_factory():
    started = []

    def make(run=None, *, settings=None, last=None, now=NOW, state=None):
        holder = {"settings": settings or make_settings()}
        scheduler = Scheduler(
            load_settings=lambda: holder["settings"],
            run=run or FakeRun(),
            state=state or RunState(),
            last_run_start=lambda: last,
            now=lambda: now,
            stop_timeout=2.0,
        )
        scheduler.settings_holder = holder
        started.append(scheduler)
        return scheduler

    yield make
    for scheduler in started:
        scheduler.stop()


# --- the arithmetic, no thread -----------------------------------------------------


def test_next_run_is_none_when_the_schedule_is_disabled(scheduler_factory):
    scheduler = scheduler_factory(settings=make_settings(sync_enabled=False), last=None)

    assert scheduler.next_run_at() is None
    assert scheduler.status()["next_run_at"] is None


def test_next_run_is_now_when_nothing_has_ever_run(scheduler_factory):
    scheduler = scheduler_factory(last=None)

    assert scheduler.next_run_at() == NOW


def test_next_run_is_the_last_start_plus_the_interval(scheduler_factory):
    scheduler = scheduler_factory(settings=make_settings(sync_interval_minutes=90), last="2026-09-12T11:00:00+00:00")

    assert scheduler.next_run_at() == NOW + timedelta(minutes=30)
    assert scheduler.status()["next_run_at"] == "2026-09-12T12:30:00+00:00"


def test_an_overdue_run_is_reported_as_due_now(scheduler_factory):
    """A service that was down past its slot shows 'now', not a time in the past."""
    scheduler = scheduler_factory(settings=make_settings(sync_interval_minutes=60), last="2026-09-12T09:00:00+00:00")

    assert scheduler.next_run_at() == NOW


def test_status_reports_the_schedule_and_whether_a_run_is_going(scheduler_factory):
    scheduler = scheduler_factory(settings=make_settings(sync_interval_minutes=45), last=None)

    assert scheduler.status() == {
        "enabled": True,
        "interval_minutes": 45,
        "next_run_at": "2026-09-12T12:00:00+00:00",
        "running": False,
    }


def test_delay_is_zero_when_due_and_none_when_disabled(scheduler_factory):
    due = scheduler_factory(last=None)
    later = scheduler_factory(settings=make_settings(sync_interval_minutes=60), last="2026-09-12T11:10:00+00:00")
    disabled = scheduler_factory(settings=make_settings(sync_enabled=False))

    assert due._delay(due.settings_holder["settings"]) == 0.0
    assert later._delay(later.settings_holder["settings"]) == 600.0
    assert disabled._delay(disabled.settings_holder["settings"]) is None


def test_cancel_does_nothing_when_idle(scheduler_factory):
    assert scheduler_factory().cancel() is False


# --- the thread ----------------------------------------------------------------------


def test_a_due_run_starts_on_its_own(scheduler_factory):
    run = FakeRun()
    scheduler = scheduler_factory(run, last=None)

    scheduler.start()

    assert run.finished.wait(2)
    settings, state, cancel = run.calls[0]
    assert settings == scheduler.settings_holder["settings"]
    assert isinstance(state, RunState)
    assert isinstance(cancel, threading.Event)


def test_a_disabled_schedule_never_runs_but_a_trigger_still_does(scheduler_factory):
    run = FakeRun()
    scheduler = scheduler_factory(run, settings=make_settings(sync_enabled=False))
    scheduler.start()

    assert not run.started.wait(0.2)
    assert scheduler.trigger() is True
    assert run.finished.wait(2)
    assert len(run.calls) == 1


def test_a_run_that_is_not_due_waits(scheduler_factory):
    run = FakeRun()
    scheduler = scheduler_factory(
        run, settings=make_settings(sync_interval_minutes=60), last="2026-09-12T11:50:00+00:00"
    )
    scheduler.start()

    assert not run.started.wait(0.2)
    assert run.calls == []


def test_trigger_is_refused_while_a_run_is_going(scheduler_factory):
    run = FakeRun(block=True)
    scheduler = scheduler_factory(run, settings=make_settings(sync_enabled=False))
    scheduler.start()
    scheduler.trigger()
    assert run.started.wait(2)

    assert scheduler.running is True
    assert scheduler.trigger() is False

    run.release.set()
    assert run.finished.wait(2)


def test_cancel_sets_the_event_the_run_is_watching(scheduler_factory):
    run = FakeRun(block=True)
    state = RunState()
    scheduler = scheduler_factory(run, settings=make_settings(sync_enabled=False), state=state)
    scheduler.start()
    scheduler.trigger()
    assert run.started.wait(2)
    state.begin(1)

    assert scheduler.cancel() is True

    _, _, cancel = run.calls[0]
    assert cancel.is_set()
    assert state.snapshot()["stage"] == RunStage.CANCELLING
    assert run.finished.wait(2)


def test_the_cancel_event_is_fresh_for_the_next_run(scheduler_factory):
    """A cancelled run must not leave the next one cancelled before it starts."""
    run = FakeRun(block=True)
    scheduler = scheduler_factory(run, settings=make_settings(sync_enabled=False))
    scheduler.start()
    scheduler.trigger()
    assert run.started.wait(2)
    scheduler.cancel()
    assert run.finished.wait(2)
    _settle(scheduler)
    run.started.clear()
    run.finished.clear()

    assert scheduler.trigger() is True
    assert run.started.wait(2)

    _, _, cancel = run.calls[1]
    assert not cancel.is_set()
    run.release.set()


def test_stop_cancels_the_run_in_flight_and_joins(scheduler_factory):
    """What a docker stop turns into: the run hands its book back and the thread ends."""
    run = FakeRun(block=True)
    scheduler = scheduler_factory(run, settings=make_settings(sync_enabled=False))
    scheduler.start()
    scheduler.trigger()
    assert run.started.wait(2)

    scheduler.stop()

    assert run.finished.is_set()
    assert not scheduler._thread.is_alive()


def test_a_failing_run_does_not_kill_the_thread(scheduler_factory, caplog):
    run = FakeRun(raise_once=RuntimeError("audible down"))
    scheduler = scheduler_factory(run, settings=make_settings(sync_enabled=False))
    scheduler.start()
    scheduler.trigger()
    assert run.finished.wait(2)
    _settle(scheduler)
    run.finished.clear()

    assert scheduler.trigger() is True

    assert run.finished.wait(2)
    assert len(run.calls) == 2
    assert "Sync run failed" in caplog.text


def test_unreadable_settings_park_the_thread_until_they_are_fixed(scheduler_factory, caplog):
    run = FakeRun()
    scheduler = scheduler_factory(run, settings=make_settings(sync_enabled=False))
    holder = scheduler.settings_holder
    good = holder["settings"]

    def broken():
        raise ValueError("bitrate must be a whole number")

    scheduler._load_settings = broken
    scheduler.start()
    scheduler.trigger()
    assert not run.started.wait(0.2)
    assert "Could not read the settings" in caplog.text

    scheduler._load_settings = lambda: good
    scheduler.wake()

    assert run.finished.wait(2)


def test_wake_makes_a_changed_interval_take_effect(scheduler_factory):
    """The thread is asleep for hours; a settings change must not wait for it."""
    run = FakeRun()
    scheduler = scheduler_factory(run, settings=make_settings(sync_enabled=False))
    scheduler.start()
    assert not run.started.wait(0.1)

    scheduler.settings_holder["settings"] = make_settings(sync_enabled=True)
    scheduler.wake()

    assert run.finished.wait(2)
