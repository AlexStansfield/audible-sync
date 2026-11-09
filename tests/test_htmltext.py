import pytest

from audible_sync.htmltext import html_to_text


@pytest.mark.parametrize("html", [None, "", "<p></p>", "<br>", "<script>alert(1)</script>"])
def test_nothing_to_read_is_empty(html):
    assert html_to_text(html) == ""


def test_paragraphs_become_blank_lines():
    assert html_to_text("<p>One.</p><p>Two.</p>") == "One.\n\nTwo."


def test_line_breaks_are_kept():
    assert html_to_text("<p>One<br>two<br/>three</p>") == "One\ntwo\nthree"


def test_list_items_become_bullets():
    assert html_to_text("<p>Includes:</p><ul><li>a map</li><li>a glossary</li></ul>") == (
        "Includes:\n\n• a map\n• a glossary"
    )


def test_inline_tags_are_dropped_and_entities_decoded():
    assert html_to_text("<p><b>Bold</b> &amp; <i>&quot;quoted&quot;</i> &#8212; done</p>") == 'Bold & "quoted" — done'


def test_source_whitespace_is_not_layout():
    assert html_to_text("<p>\n   Lots   of\n   space\n</p>\n\n<p>  next </p>") == "Lots of space\n\nnext"


def test_script_and_style_content_is_never_text():
    assert html_to_text("<p>Kept</p><script>var x = '<p>no</p>';</script><style>p {}</style>") == "Kept"


def test_attributes_never_reach_the_text():
    assert html_to_text('<p onclick="steal()"><a href="javascript:x">Link</a></p>') == "Link"


def test_unclosed_and_stray_tags_are_not_an_error():
    assert html_to_text("<p>Open <b>bold</p></i> text") == "Open bold\n\ntext"


def test_plain_text_passes_through():
    assert html_to_text("No markup at all") == "No markup at all"
