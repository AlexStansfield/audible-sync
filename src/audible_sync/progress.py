"""
Where download progress is reported to.

`_stream_to_file` used to open a `tqdm` bar itself, which is right for a terminal and
wrong for everything else: the carriage returns land in the service log and a UI has
nothing to read. The progress object is injected instead, so the CLI passes
`TqdmProgress` and the Milestone 3 service will pass something that writes where its
front end can see it.

A protocol with three methods rather than a bare callback, because a bar has a
lifecycle: it has to be created with a total, advanced, and closed.

`tqdm` is imported here and nowhere else, so no library module depends on a terminal.
"""

from typing import Protocol

from tqdm import tqdm


class Progress(Protocol):
    """One transfer's progress. Implementations must tolerate an unknown total."""

    def start(self, desc: str | None, total: int | None) -> None:
        """Begin reporting a transfer of `total` bytes, or an unknown number when None."""
        ...

    def advance(self, amount: int) -> None:
        """Record `amount` further bytes written."""
        ...

    def finish(self) -> None:
        """Release whatever `start` set up. Always called, including after a failure."""
        ...


class NullProgress:
    """
    Reports nothing.

    The default everywhere `progress` is omitted, so no call site needs to guard on
    `progress is not None`.
    """

    def start(self, desc: str | None, total: int | None) -> None:
        pass

    def advance(self, amount: int) -> None:
        pass

    def finish(self) -> None:
        pass


class TqdmProgress:
    """
    A `tqdm` bar on stderr: the CLI's reporter.

    Arguments match the bar this replaced, so a terminal run looks exactly as it did.
    One instance drives one transfer at a time, which is what the downloader does -
    `start` closes any bar left open rather than leaking it.
    """

    def __init__(self):
        self._bar = None

    def start(self, desc: str | None, total: int | None) -> None:
        self.finish()
        self._bar = tqdm(total=total, unit_scale=True, unit_divisor=1024, unit="B", desc=desc)

    def advance(self, amount: int) -> None:
        if self._bar is not None:
            self._bar.update(amount)

    def finish(self) -> None:
        if self._bar is not None:
            self._bar.close()
            self._bar = None
