from src.runstate import RunStage, RunState, StateProgress
from tests.conftest import make_book


def test_snapshot_is_none_between_runs():
    state = RunState()

    assert state.snapshot() is None
    assert state.running is False


def test_begin_starts_a_fresh_picture():
    state = RunState()

    state.begin(7)
    snapshot = state.snapshot()

    assert snapshot["run_id"] == 7
    assert snapshot["started_at"].endswith("+00:00")
    assert snapshot["stage"] is None
    assert snapshot["book"] is None
    assert (snapshot["books_done"], snapshot["books_total"]) == (0, 0)
    assert snapshot["transfer"] is None
    assert state.running is True


def test_end_clears_everything():
    state = RunState()
    state.begin(7)
    state.set_stage(RunStage.DOWNLOADING)

    state.end()

    assert state.snapshot() is None


def test_begin_clears_what_the_previous_run_left():
    state = RunState()
    state.begin(1)
    state.set_book(make_book("B1", "One"), done=3, total=4)

    state.begin(2)

    assert state.snapshot()["book"] is None
    assert state.snapshot()["books_done"] == 0


def test_set_book_reports_the_book_and_the_queue_position():
    state = RunState()
    state.begin(1)

    state.set_book(make_book("B1", "One"), done=2, total=5)
    snapshot = state.snapshot()

    assert snapshot["book"] == {"asin": "B1", "title": "One"}
    assert (snapshot["books_done"], snapshot["books_total"]) == (2, 5)


def test_a_new_book_ends_the_previous_transfer():
    state = RunState()
    state.begin(1)
    state.transfer_started("Book", 100)

    state.set_book(make_book("B2", "Two"), done=1, total=2)

    assert state.snapshot()["transfer"] is None


def test_transfer_lifecycle():
    state = RunState()
    state.begin(1)

    state.transfer_started("Book", 100)
    state.transfer_advanced(30)
    state.transfer_advanced(20)
    assert state.snapshot()["transfer"] == {"desc": "Book", "bytes": 50, "total": 100}

    state.transfer_finished()
    assert state.snapshot()["transfer"] is None


def test_advance_without_a_transfer_is_ignored():
    state = RunState()
    state.begin(1)

    state.transfer_advanced(10)

    assert state.snapshot()["transfer"] is None


def test_snapshot_is_a_copy():
    state = RunState()
    state.begin(1)
    state.transfer_started("Book", None)

    state.snapshot()["transfer"]["bytes"] = 999

    assert state.snapshot()["transfer"]["bytes"] == 0


def test_state_progress_drives_the_transfer():
    """The service's counterpart to TqdmProgress: same lifecycle, reported into the state."""
    state = RunState()
    state.begin(1)
    progress = StateProgress(state)

    progress.start("PDF", 10)
    progress.advance(4)
    assert state.snapshot()["transfer"] == {"desc": "PDF", "bytes": 4, "total": 10}

    progress.finish()
    assert state.snapshot()["transfer"] is None
