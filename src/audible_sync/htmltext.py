"""
Audible's descriptions, which are HTML, as plain text and as HTML that is safe to show.

The markup is stored as Audible gave it and never leaves as it came. Files get plain
text (`html_to_text`), since a tag reader shows markup literally; the web app gets
`safe_html`, which is not Audible's markup filtered but new markup written from an
allow-list - a handful of tags with no attributes, every piece of text escaped - so
nothing Audible sends can reach a page as anything but text.
"""

import re
from html import escape
from html.parser import HTMLParser

# Tags that end a block: a paragraph break before and after
_BLOCKS = frozenset({"p", "div", "ul", "ol", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "table", "tr"})
# Whose content is never text
_SKIPPED = frozenset({"script", "style", "head", "title"})


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIPPED:
            self._skipping += 1
        elif tag in _BLOCKS:
            self.parts.append("\n\n")
        elif tag == "br":
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n• ")

    def handle_startendtag(self, tag: str, attrs) -> None:
        # `<br/>`: only the line break, never a skipped section left open
        if tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED:
            self._skipping = max(self._skipping - 1, 0)
        elif tag in _BLOCKS:
            self.parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            # Whitespace in HTML source is not layout: runs of it, newlines included, are one space
            self.parts.append(re.sub(r"\s+", " ", data))


def html_to_text(html: str | None) -> str:
    """
    The text of an HTML fragment: paragraphs as blank lines, `<br>` as a line break,
    list items as bullets, every other tag dropped and entities decoded.

    Lenient like a browser: unclosed or stray tags are not an error. Empty for None or
    for markup with no text.
    """
    if not html:
        return ""
    parser = _TextParser()
    parser.feed(html)
    parser.close()
    text = "".join(parser.parts)
    # Spaces either side of a line break are left over from the source's indentation
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    # A block inside a block (a list in a paragraph) would otherwise open several blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# What `safe_html` writes, and what each tag Audible may send becomes. Anything not
# here is dropped and its text kept, except `_SKIPPED`, whose content goes too
_SAFE_TAGS = {
    "p": "p",
    "div": "p",
    "h1": "p",
    "h2": "p",
    "h3": "p",
    "h4": "p",
    "h5": "p",
    "h6": "p",
    "blockquote": "p",
    "b": "strong",
    "strong": "strong",
    "i": "em",
    "em": "em",
    "ul": "ul",
    "ol": "ol",
    "li": "li",
}
# Opening one of these ends an open paragraph, as a browser would
_ENDS_PARAGRAPH = frozenset({"p", "ul", "ol", "li"})


class _SafeHtmlParser(HTMLParser):
    """
    Writes its own markup as it reads: allowed tags only, never an attribute, text
    escaped, and every tag it opened closed - so the output nests whatever the input did.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._open: list[str] = []
        self._skipping = 0

    def _close_through(self, tag: str) -> None:
        """Close open tags down to and including the innermost `tag`."""
        if tag not in self._open:
            return
        while self._open:
            closing = self._open.pop()
            self.out.append(f"</{closing}>")
            if closing == tag:
                return

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIPPED:
            self._skipping += 1
            return
        if self._skipping:
            return
        if tag == "br":
            self.out.append("<br>")
            return
        safe = _SAFE_TAGS.get(tag)
        if safe is None:
            return
        if safe in _ENDS_PARAGRAPH:
            self._close_through("p")
        if safe == "li":
            # A new item ends the previous one in the same list: an item is open inside
            # the innermost list, not around it
            innermost = next((t for t in reversed(self._open) if t in ("ul", "ol", "li")), None)
            if innermost == "li":
                self._close_through("li")
        self.out.append(f"<{safe}>")
        self._open.append(safe)

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag == "br" and not self._skipping:
            self.out.append("<br>")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED:
            self._skipping = max(self._skipping - 1, 0)
            return
        safe = _SAFE_TAGS.get(tag)
        if safe is not None and not self._skipping:
            self._close_through(safe)

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self.out.append(escape(re.sub(r"\s+", " ", data), quote=False))

    def result(self) -> str:
        while self._open:
            self.out.append(f"</{self._open.pop()}>")
        html = "".join(self.out)
        # Paragraphs and items with nothing in them are left out
        html = re.sub(r"<(p|li|strong|em)>\s*</\1>", "", html)
        html = re.sub(r"<(ul|ol)>\s*</\1>", "", html)
        return html.strip()


def safe_html(html: str | None) -> str:
    """
    An HTML fragment rebuilt from an allow-list: `p`, `br`, `strong`, `em`, `ul`, `ol`
    and `li`, with no attributes; other tags are dropped with their text kept (headings
    and `div`s become paragraphs, `b` and `i` become `strong` and `em`), `script` and
    `style` go with their content, and all text is escaped. Safe to insert into a page.
    Empty for None or for markup with no text.
    """
    if not html:
        return ""
    parser = _SafeHtmlParser()
    parser.feed(html)
    parser.close()
    return parser.result()
