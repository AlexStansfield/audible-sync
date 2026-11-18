import pytest

from audible_sync.languages import language_code


@pytest.mark.parametrize(
    ("word", "code"),
    [("english", "en"), ("German", "de"), (" french ", "fr"), ("klingon", None), ("", None), (None, None)],
)
def test_language_code(word, code):
    assert language_code(word) == code
