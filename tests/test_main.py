import logging

from src.main import configure_logging


def test_configure_logging_honours_the_debug_flag(monkeypatch):
    """The debug config flag was read but never applied to the log level."""
    levels = []
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: levels.append(kw["level"]))

    configure_logging(debug=True)
    configure_logging(debug=False)

    assert levels == [logging.DEBUG, logging.INFO]
