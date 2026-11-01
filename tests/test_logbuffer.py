import logging

import pytest

from src.logbuffer import RingBufferHandler


@pytest.fixture
def buffered():
    """A logger of its own with the handler attached, so pytest's capture is not involved."""
    handler = RingBufferHandler(capacity=3)
    logger = logging.getLogger("tests.ring")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.addHandler(handler)
    yield logger, handler
    logger.removeHandler(handler)


def test_records_are_plain_dicts_oldest_first(buffered):
    logger, handler = buffered

    logger.info("one %s", "thing")
    logger.warning("two")

    entries = handler.records()
    assert [e["message"] for e in entries] == ["one thing", "two"]
    assert [e["level"] for e in entries] == ["INFO", "WARNING"]
    assert entries[0]["logger"] == "tests.ring"
    assert entries[0]["time"].endswith("+00:00")


def test_the_oldest_fall_off(buffered):
    logger, handler = buffered

    for n in range(5):
        logger.info("line %d", n)

    assert [e["message"] for e in handler.records()] == ["line 2", "line 3", "line 4"]


def test_limit_takes_the_newest(buffered):
    logger, handler = buffered
    for n in range(3):
        logger.info("line %d", n)

    assert [e["message"] for e in handler.records(limit=2)] == ["line 1", "line 2"]


def test_level_is_a_floor(buffered):
    logger, handler = buffered
    logger.debug("d")
    logger.info("i")
    logger.error("e")

    assert [e["level"] for e in handler.records(level="info")] == ["INFO", "ERROR"]
    assert [e["level"] for e in handler.records(level="ERROR")] == ["ERROR"]


def test_an_exception_is_kept_with_its_traceback(buffered):
    logger, handler = buffered
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        logger.exception("failed")

    message = handler.records()[0]["message"]
    assert message.startswith("failed\n")
    assert "RuntimeError: boom" in message


def test_clear(buffered):
    logger, handler = buffered
    logger.info("x")

    handler.clear()

    assert handler.records() == []
