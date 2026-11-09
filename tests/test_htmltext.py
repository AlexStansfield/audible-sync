import pytest

from audible_sync.htmltext import html_to_text, safe_html


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


# --- safe_html -------------------------------------------------------------------


@pytest.mark.parametrize("html", [None, "", "<p></p>", "<b> </b>", "<ul><li></li></ul>", "<script>x()</script>"])
def test_safe_html_of_nothing_to_show_is_empty(html):
    assert safe_html(html) == ""


def test_safe_html_keeps_the_formatting():
    assert safe_html("<p>A <b>bold</b> and <i>quiet</i> tale.</p><p>One<br>two<br/>three</p>") == (
        "<p>A <strong>bold</strong> and <em>quiet</em> tale.</p><p>One<br>two<br>three</p>"
    )


def test_safe_html_keeps_lists():
    assert safe_html("<ul><li>a</li><li>b</li></ul><ol><li>c</li></ol>") == (
        "<ul><li>a</li><li>b</li></ul><ol><li>c</li></ol>"
    )


@pytest.mark.parametrize(
    "html",
    [
        '<p onclick="steal()">x</p>',
        '<p style="background:url(javascript:x)">x</p>',
        '<a href="javascript:steal()">x</a>',
        "<img src=x onerror=steal()>x",
        "<iframe src=//evil></iframe>x",
        "<svg onload=steal()>x</svg>",
        '<form action="//evil"><input name=x></form>x',
    ],
)
def test_safe_html_never_carries_an_attribute_or_another_tag(html):
    out = safe_html(html)

    assert "=" not in out
    assert all(tag not in out for tag in ("<a", "<img", "<iframe", "<svg", "<form", "<input"))
    assert "x" in out


def test_safe_html_drops_script_and_style_with_their_content():
    assert safe_html("<p>Kept</p><script>var x = '<p>no</p>';</script><style>p {}</style>") == "<p>Kept</p>"


def test_safe_html_escapes_text_so_it_stays_text():
    # Entities decode on the way in and are escaped again on the way out
    assert safe_html("<p>&lt;script&gt;alert(1)&lt;/script&gt; &amp; 1 &lt; 2</p>") == (
        "<p>&lt;script&gt;alert(1)&lt;/script&gt; &amp; 1 &lt; 2</p>"
    )


def test_safe_html_drops_comments_and_cdata():
    assert safe_html("<!-- <script>x()</script> --><![CDATA[<b>x</b>]]><p>ok</p>") == "<p>ok</p>"


def test_safe_html_closes_what_it_opens_whatever_the_input_did():
    assert safe_html("<p>Open <b>bold</p></i> text") == "<p>Open <strong>bold</strong></p> text"
    assert safe_html("<ul><li>a<li>b</ul><p>after") == "<ul><li>a</li><li>b</li></ul><p>after</p>"
    assert safe_html("<p>one<p>two</p></p>") == "<p>one</p><p>two</p>"


def test_safe_html_turns_headings_and_divs_into_paragraphs():
    assert safe_html("<h2>Title</h2><div>Body</div>") == "<p>Title</p><p>Body</p>"


def test_safe_html_collapses_source_whitespace():
    assert safe_html("<p>\n   Lots   of\n   space\n</p>") == "<p> Lots of space </p>"
