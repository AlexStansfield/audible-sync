from pathlib import Path

import pytest

from src.naming import (
    DEFAULT_FILENAME_TEMPLATE,
    DEFAULT_FOLDER_TEMPLATE,
    book_output_paths,
    book_template_values,
    render_template,
    sanitize_filename,
    validate_templates,
)
from tests.conftest import make_book

LIBRARY = "audiobooks"


def test_default_layout_with_series_and_sequence():
    book = make_book(
        title="One Word Kill", authors=("Mark Lawrence",), series=[{"title": "Nick Hayes Series", "sequence": "1"}]
    )
    folder, stem = book_output_paths(book, LIBRARY)
    assert folder == Path(LIBRARY) / "Mark Lawrence" / "Nick Hayes Series" / "1 - One Word Kill"
    assert stem == "One Word Kill"


def test_default_layout_without_series():
    book = make_book(title="Snow Crash", authors=("Neal Stephenson",))
    folder, stem = book_output_paths(book, LIBRARY)
    assert folder == Path(LIBRARY) / "Neal Stephenson" / "Snow Crash"
    assert stem == "Snow Crash"


def test_default_layout_with_series_but_no_sequence():
    book = make_book(
        title="Dune", authors=("Frank Herbert",), series=[{"title": "The Dune Sequence", "sequence": None}]
    )
    folder, stem = book_output_paths(book, LIBRARY)
    assert folder == Path(LIBRARY) / "Frank Herbert" / "The Dune Sequence" / "Dune"
    assert stem == "Dune"


def test_default_layout_files_a_two_series_book_under_its_primary_series():
    """
    *Dune* is in "Dune" at 1 and "The Dune Sequence" at 12; it belongs under "Dune".

    The sequence in the folder name has to come from the same entry as the title,
    or the book files as "Dune/12 - Dune".
    """
    book = make_book(
        title="Dune",
        authors=("Frank Herbert",),
        series=[
            {"title": "The Dune Sequence", "sequence": "12", "series_asin": "B00I53X24U"},
            {"title": "Dune", "sequence": "1", "series_asin": "B01H4IQOGO"},
        ],
    )
    folder, _ = book_output_paths(book, LIBRARY)
    assert folder == Path(LIBRARY) / "Frank Herbert" / "Dune" / "1 - Dune"


def test_a_series_is_not_split_by_the_order_the_api_returned():
    """
    The bug this rule exists for: the His Dark Materials trilogy landed in two
    folders because Audible returns its typo'd duplicate first for some books.
    """
    common = {"authors": ("Philip Pullman",)}
    first = make_book(
        title="Northern Lights",
        series=[{"title": "His Dark Materialsik", "sequence": "1"}, {"title": "His Dark Materials", "sequence": "1"}],
        **common,
    )
    third = make_book(
        title="The Amber Spyglass",
        series=[{"title": "His Dark Materials", "sequence": "3"}, {"title": "His Dark Materialsik", "sequence": "3"}],
        **common,
    )

    assert book_output_paths(first, LIBRARY)[0].parent == book_output_paths(third, LIBRARY)[0].parent
    assert (
        book_output_paths(first, LIBRARY)[0]
        == Path(LIBRARY) / "Philip Pullman" / "His Dark Materials" / "1 - Northern Lights"
    )


def test_explicit_defaults_match_implicit_defaults():
    book = make_book(series=[{"title": "S", "sequence": "2"}])
    explicit = book_output_paths(book, LIBRARY, DEFAULT_FOLDER_TEMPLATE, DEFAULT_FILENAME_TEMPLATE)
    assert explicit == book_output_paths(book, LIBRARY)


@pytest.mark.parametrize(
    ("template", "values", "expected"),
    [
        ("{title}[ ({year})]", {"title": "T", "year": "2020"}, "T (2020)"),
        ("{title}[ ({year})]", {"title": "T", "year": ""}, "T"),
        ("[{series}/][{sequence} - ]{title}", {"series": "", "sequence": "1", "title": "T"}, "1 - T"),
        ("[{series}/][{sequence} - ]{title}", {"series": "S", "sequence": "", "title": "T"}, "S/T"),
        ("[{series} {sequence}] {title}", {"series": "S", "sequence": "", "title": "T"}, " T"),
        ("literal only", {}, "literal only"),
    ],
)
def test_render_template_optional_groups(template, values, expected):
    assert render_template(template, values) == expected


def test_bare_template_drops_empty_folder_segment():
    book = make_book(title="Snow Crash", authors=("Neal Stephenson",))
    folder, _ = book_output_paths(book, LIBRARY, folder_template="{author}/{series}/{title}")
    assert folder == Path(LIBRARY) / "Neal Stephenson" / "Snow Crash"


def test_title_with_path_characters_stays_one_segment():
    book = make_book(title="Good Omens / The Nice Bit: Part 1", authors=("A",))
    folder, stem = book_output_paths(book, LIBRARY, folder_template="{title}")
    assert folder == Path(LIBRARY) / "Good Omens - The Nice Bit - Part 1"
    assert stem == "Good Omens - The Nice Bit - Part 1"


def test_dot_segments_cannot_escape_library_root():
    book = make_book(title="T")
    folder, _ = book_output_paths(book, LIBRARY, folder_template="../..//{title}")
    assert folder == Path(LIBRARY) / "T"


def test_unknown_placeholder_raises_with_name():
    with pytest.raises(ValueError, match=r"\{nope\}"):
        validate_templates("{author}/{nope}", "{title}")
    with pytest.raises(ValueError, match=r"\{nope\}"):
        validate_templates("[{nope}/]{title}", "{title}")
    with pytest.raises(ValueError, match=r"\{nope\}"):
        validate_templates("{title}", "{nope}")


def test_separator_in_filename_template_raises():
    with pytest.raises(ValueError, match="filename"):
        validate_templates("{author}", "{author}/{title}")
    with pytest.raises(ValueError, match="filename"):
        validate_templates("{author}", "{author}\\{title}")


def test_default_templates_validate():
    validate_templates(DEFAULT_FOLDER_TEMPLATE, DEFAULT_FILENAME_TEMPLATE)


def test_empty_folder_and_stem_fall_back_to_asin():
    book = make_book(asin="B00X", title="T")
    folder, stem = book_output_paths(book, LIBRARY, folder_template="{series}", filename_template="{subtitle}")
    assert folder == Path(LIBRARY) / "B00X"
    assert stem == "B00X"


def test_template_values_join_lists_and_extract_year():
    book = make_book(
        asin="B00Y",
        subtitle="A Sub: Title",
        authors=("A One", "B Two"),
        narrators=("N One", "N Two"),
        series=[{"title": "S", "sequence": "3"}],
        release_date="1999-12-31",
    )
    values = book_template_values(book)
    assert values["asin"] == "B00Y"
    assert values["author"] == "A One"
    assert values["authors"] == "A One, B Two"
    assert values["narrator"] == "N One"
    assert values["narrators"] == "N One, N Two"
    assert values["subtitle"] == "A Sub - Title"
    assert values["series"] == "S"
    assert values["sequence"] == "3"
    assert values["year"] == "1999"


def test_template_values_for_sparse_book():
    book = make_book(asin="B00Z", title="", authors=(), narrators=(), release_date=None)
    values = book_template_values(book)
    assert values["title"] == "B00Z"
    assert values["author"] == "Unknown Author"
    assert values["authors"] == "Unknown Author"
    assert values["narrator"] == ""
    assert values["narrators"] == ""
    assert values["series"] == ""
    assert values["sequence"] == ""
    assert values["year"] == ""


def test_sanitize_filename_truncates_to_byte_budget_for_multibyte_scripts():
    result = sanitize_filename("あ" * 120)
    assert len(result.encode("utf-8")) <= 200
    assert result == "あ" * 66


def test_sanitize_filename_escapes_windows_reserved_names():
    assert sanitize_filename("CON") == "CON_"
    assert sanitize_filename("nul.m4b") == "nul.m4b_"
    assert sanitize_filename("com9") == "com9_"
    assert sanitize_filename("Console") == "Console"


def test_sanitize_filename_does_not_leave_double_spaces_behind_dropped_characters():
    assert sanitize_filename("Who? Me") == "Who Me"
    assert sanitize_filename('A "B" C') == "A B C"


def test_sequence_without_series_title_does_not_become_a_folder():
    book = make_book(title="Dune", authors=("Frank Herbert",), series=[{"title": None, "sequence": "2"}])
    folder, stem = book_output_paths(book, LIBRARY)
    assert folder == Path(LIBRARY) / "Frank Herbert" / "Dune"
    assert stem == "Dune"
