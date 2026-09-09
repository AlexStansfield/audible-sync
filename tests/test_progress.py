import src.progress as progress_module
from src.progress import NullProgress, TqdmProgress


class FakeBar:
    """Stands in for a tqdm bar, recording the calls the adapter makes."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.updates = []
        self.closed = False

    def update(self, amount):
        self.updates.append(amount)

    def close(self):
        self.closed = True


def _fake_tqdm(monkeypatch):
    """Replace tqdm with a recorder and hand back the list of bars it created."""
    bars = []

    def factory(**kwargs):
        bars.append(FakeBar(**kwargs))
        return bars[-1]

    monkeypatch.setattr(progress_module, "tqdm", factory)
    return bars


def test_null_progress_accepts_the_whole_lifecycle():
    """The default reporter: every call site relies on it needing no guard."""
    progress = NullProgress()

    progress.start("Book", 100)
    progress.advance(10)
    progress.finish()


def test_tqdm_progress_builds_a_bar_with_the_total_and_description(monkeypatch):
    bars = _fake_tqdm(monkeypatch)

    TqdmProgress().start("Book", 2048)

    assert bars[0].kwargs["total"] == 2048
    assert bars[0].kwargs["desc"] == "Book"
    # Byte formatting, exactly as the bar this replaced was built
    assert bars[0].kwargs["unit"] == "B"
    assert bars[0].kwargs["unit_scale"] is True
    assert bars[0].kwargs["unit_divisor"] == 1024


def test_tqdm_progress_tolerates_an_unknown_total(monkeypatch):
    """A response with no Content-Length is normal; tqdm reads None as indeterminate."""
    bars = _fake_tqdm(monkeypatch)

    TqdmProgress().start(None, None)

    assert bars[0].kwargs["total"] is None


def test_tqdm_progress_advances_and_closes(monkeypatch):
    bars = _fake_tqdm(monkeypatch)
    progress = TqdmProgress()

    progress.start("Book", 100)
    progress.advance(40)
    progress.advance(60)
    progress.finish()

    assert bars[0].updates == [40, 60]
    assert bars[0].closed is True


def test_tqdm_progress_closes_a_bar_left_open_by_the_previous_transfer(monkeypatch):
    """One instance drives every transfer in a run, so a stale bar must not leak."""
    bars = _fake_tqdm(monkeypatch)
    progress = TqdmProgress()

    progress.start("Book", 100)
    progress.start("Cover", 10)

    assert bars[0].closed is True
    assert bars[1].closed is False


def test_tqdm_progress_finish_is_safe_before_any_start(monkeypatch):
    _fake_tqdm(monkeypatch)

    TqdmProgress().finish()


def test_tqdm_progress_advance_is_safe_after_finish(monkeypatch):
    """`_stream_to_file` finishes in a `finally`; a late chunk must not explode."""
    _fake_tqdm(monkeypatch)
    progress = TqdmProgress()

    progress.start("Book", 100)
    progress.finish()
    progress.advance(10)
