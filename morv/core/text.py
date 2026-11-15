"""Arabic text: search normalisation, barcode scanning, writing direction."""
import re

from .money import to_latin

# Fold letter shapes, so a search typed without hamza still finds the word
_MAP = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ة": "ه", "ى": "ي",
                      "ؤ": "و", "ئ": "ي", "ـ": None})
# Diacritics are dropped from search text
_HARAKAT = re.compile("[ً-ْٰ]")


def norm_ar(text: str) -> str:
    s = _HARAKAT.sub("", to_latin(text or "")).translate(_MAP).lower()
    return " ".join(s.split())


# A scanner types like a keyboard: on an Arabic layout a code arrives as Arabic
# letters, so this table maps every letter back to its Latin key.
_AR101 = {
    "ض": "q", "ص": "w", "ث": "e", "ق": "r", "ف": "t", "غ": "y", "ع": "u", "ه": "i", "خ": "o", "ح": "p",
    "ش": "a", "س": "s", "ي": "d", "ب": "f", "ل": "g", "ا": "h", "ت": "j", "ن": "k", "م": "l",
    "ئ": "z", "ء": "x", "ؤ": "c", "ر": "v", "ى": "n", "ة": "m", "ز": ".", "ظ": "/", "ط": "'",
    "ك": ";", "و": ",",
}


def scanner_fix(code: str) -> str:
    s = to_latin(code or "").strip().replace("لا", "b")  # One Arabic ligature sits alone on the B key
    return "".join(_AR101.get(ch, ch) for ch in s).upper()


LRI, PDI = "\u2066", "\u2069"   # Written as an escape: the character is invisible and is lost on copy and paste
RLM = "\u200f"   # Right-to-left mark: sets paragraph direction without showing


def ltr(value) -> str:
    s = str(value)
    return s if not s else f"{LRI}{s}{PDI}"
