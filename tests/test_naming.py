import json
from pathlib import Path

import pytest

from src.naming import (
    DEFAULT_FILENAME_TEMPLATE,
    DEFAULT_FOLDER_TEMPLATE,
    book_output_paths,
    book_template_values,
    render_template,
    validate_templates,
)

LIBRARY = "audiobooks"


def make_row(
    asin="B001",
    title="Title",
    subtitle="",
    authors=("Author One",),
    narrators=("Narrator",),
    series=None,
    release_date="2020-05-01",
):
    """Build a database row tuple matching the library table column order."""
    row = [None] * 20
    row[0] = asin
    row[1] = title
    row[2] = subtitle
    row[3] = json.dumps(list(authors))
    row[4] = json.dumps(list(narrators))
    row[5] = json.dumps(series or [])
    row[6] = json.dumps(["Fiction"])
    row[11] = release_date
    row[12] = ""
    row[13] = "waiting_download"
    row[17] = 0
    return tuple(row)


def test_default_layout_with_series_and_sequence():
    row = make_row(
        title="One Word Kill", authors=("Mark Lawrence",), series=[{"title": "Nick Hayes Series", "sequence": "1"}]
    )
    folder, stem = book_output_paths(row, LIBRARY)
    assert folder == Path(LIBRARY) / "Mark Lawrence" / "Nick Hayes Series" / "1 - One Word Kill"
    assert stem == "One Word Kill"


def test_default_layout_without_series():
    row = make_row(title="Snow Crash", authors=("Neal Stephenson",))
    folder, stem = book_output_paths(row, LIBRARY)
    assert folder == Path(LIBRARY) / "Neal Stephenson" / "Snow Crash"
    assert stem == "Snow Crash"


def test_default_layout_with_series_but_no_sequence():
    row = make_row(title="Dune", authors=("Frank Herbert",), series=[{"title": "The Dune Sequence", "sequence": None}])
    folder, stem = book_output_paths(row, LIBRARY)
    assert folder == Path(LIBRARY) / "Frank Herbert" / "The Dune Sequence" / "Dune"
    assert stem == "Dune"


def test_explicit_defaults_match_implicit_defaults():
    row = make_row(series=[{"title": "S", "sequence": "2"}])
    explicit = book_output_paths(row, LIBRARY, DEFAULT_FOLDER_TEMPLATE, DEFAULT_FILENAME_TEMPLATE)
    assert explicit == book_output_paths(row, LIBRARY)


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
    row = make_row(title="Snow Crash", authors=("Neal Stephenson",))
    folder, _ = book_output_paths(row, LIBRARY, folder_template="{author}/{series}/{title}")
    assert folder == Path(LIBRARY) / "Neal Stephenson" / "Snow Crash"


def test_title_with_path_characters_stays_one_segment():
    row = make_row(title="Good Omens / The Nice Bit: Part 1", authors=("A",))
    folder, stem = book_output_paths(row, LIBRARY, folder_template="{title}")
    assert folder == Path(LIBRARY) / "Good Omens - The Nice Bit - Part 1"
    assert stem == "Good Omens - The Nice Bit - Part 1"


def test_dot_segments_cannot_escape_library_root():
    row = make_row(title="T")
    folder, _ = book_output_paths(row, LIBRARY, folder_template="../..//{title}")
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
    row = make_row(asin="B00X", title="T")
    folder, stem = book_output_paths(row, LIBRARY, folder_template="{series}", filename_template="{subtitle}")
    assert folder == Path(LIBRARY) / "B00X"
    assert stem == "B00X"


def test_template_values_join_lists_and_extract_year():
    row = make_row(
        asin="B00Y",
        subtitle="A Sub: Title",
        authors=("A One", "B Two"),
        narrators=("N One", "N Two"),
        series=[{"title": "S", "sequence": "3"}],
        release_date="1999-12-31",
    )
    values = book_template_values(row)
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
    row = make_row(asin="B00Z", title="", authors=(), narrators=(), release_date=None)
    values = book_template_values(row)
    assert values["title"] == "B00Z"
    assert values["author"] == "Unknown Author"
    assert values["authors"] == "Unknown Author"
    assert values["narrator"] == ""
    assert values["narrators"] == ""
    assert values["series"] == ""
    assert values["sequence"] == ""
    assert values["year"] == ""
