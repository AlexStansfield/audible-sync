"""
Audible's descriptions, which are HTML, as plain text.

A description is shown in the web app and written into the files, and neither should
carry Audible's markup as it came: the web app would have to trust it, and a tag
reader shows it literally. Plain text with the paragraphs kept is enough for both, so
the API never serves HTML at all and there is nothing to sanitise.
"""

import re
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
