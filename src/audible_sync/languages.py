"""
Audible's languages as ISO 639-1 codes.

A leaf, so `model` can work a stored book's code out on read: the library keeps Audible's
own word, as it keeps the description as Audible gave it, and a word added here applies
to every stored book without reading the library again.
"""

import logging

logger = logging.getLogger(__name__)

# Audible names a language by a word ("english"); the marketplaces' common ones
_LANGUAGES = {
    "english": "en",
    "german": "de",
    "french": "fr",
    "spanish": "es",
    "italian": "it",
    "japanese": "ja",
    "portuguese": "pt",
    "dutch": "nl",
    "danish": "da",
    "swedish": "sv",
    "norwegian": "no",
    "finnish": "fi",
    "polish": "pl",
    "russian": "ru",
    "turkish": "tr",
    "hindi": "hi",
    "arabic": "ar",
    "hebrew": "he",
    "korean": "ko",
    "mandarin_chinese": "zh",
    "chinese": "zh",
    "catalan": "ca",
    "greek": "el",
    "czech": "cs",
    "hungarian": "hu",
    "romanian": "ro",
    "ukrainian": "uk",
    "welsh": "cy",
    "irish": "ga",
    "afrikaans": "af",
    "latin": "la",
}


def language_code(word: str | None) -> str | None:
    """Audible's word for a language ("english") as ISO 639-1 ("en"); None when unknown."""
    if not word:
        return None
    code = _LANGUAGES.get(word.strip().lower())
    if code is None:
        logger.debug("No language code for %r", word)
    return code
