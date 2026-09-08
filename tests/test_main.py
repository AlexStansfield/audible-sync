import logging

import pytest

from src.main import configure_logging, validate_max_download


@pytest.mark.parametrize("value", [None, 1, 10, 1000])
def test_validate_max_download_accepts_unset_and_positive_limits(value):
    validate_max_download(value)


@pytest.mark.parametrize("value", [0, -1, -100])
def test_validate_max_download_rejects_zero_and_negative_limits(value):
    """A negative limit used to slice the newest book off the queue on every run."""
    with pytest.raises(ValueError, match="max-download must be 1 or more"):
        validate_max_download(value)


def test_configure_logging_honours_the_debug_flag(monkeypatch):
    """The debug config flag was read but never applied to the log level."""
    levels = []
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: levels.append(kw["level"]))

    configure_logging(debug=True)
    configure_logging(debug=False)

    assert levels == [logging.DEBUG, logging.INFO]
