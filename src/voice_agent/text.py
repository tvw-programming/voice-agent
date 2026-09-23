"""Text helpers: sentence splitting for streaming TTS and spoken-form normalisation."""
import re

from num2words import num2words

_SENTENCE_END = re.compile(r"(.+?[.!?\u0964])(?=\s|$)", re.S)
_MIN_SENTENCE_CHARS = 12


def split_sentences(buffer: str) -> tuple[list[str], str]:
    """Return complete sentences from buffer and the unfinished remainder.
    Very short fragments ("Hi.") are held and merged with the next sentence."""
    sentences: list[str] = []
    pending = ""
    pos = 0
    for m in _SENTENCE_END.finditer(buffer):
        pending += m.group(1)
        pos = m.end()
        if len(pending.strip()) >= _MIN_SENTENCE_CHARS:
            sentences.append(pending.strip())
            pending = ""
    return sentences, pending + buffer[pos:]


_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S)
_MD = re.compile(r"(\*\*|__|`|^#+\s*|^\s*[-*]\s+|\]\([^)]*\)|\[)", re.M)
_RUPEE = re.compile(r"(?:₹|\bRs\.?|\bINR)\s?(\d[\d,]*(?:\.\d+)?)", re.I)
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s?%")
_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{2,3})+|\d+)(?:\.(\d+))?(?![\w])")

_ABBREV = {
    r"\bStd\.": "Standard",
    r"\bDiv\.": "Division",
    r"\bNo\.": "Number",
    r"\bDr\.": "Doctor",
    r"\bMr\.": "Mister",
    r"\bMrs\.": "Missus",
}


def _num(whole: str, decimals: str | None = None) -> str:
    words = num2words(int(whole.replace(",", "")), lang="en_IN")
    if decimals:
        words += " point " + " ".join(num2words(int(d)) for d in decimals)
    return words


def strip_thinking(text: str) -> str:
    return _THINK_BLOCK.sub("", text)


def normalize_for_speech(text: str) -> str:
    text = strip_thinking(text)
    text = _MD.sub("", text)
    for pattern, repl in _ABBREV.items():
        text = re.sub(pattern, repl, text)

    def rupee(m):
        whole, _, dec = m.group(1).partition(".")
        words = _num(whole) + " rupees"
        if dec and int(dec):
            words += " and " + num2words(int(dec[:2].ljust(2, "0"))) + " paise"
        return words

    def percent(m):
        whole, _, dec = m.group(1).partition(".")
        return _num(whole, dec or None) + " percent"

    text = _RUPEE.sub(rupee, text)
    text = _PERCENT.sub(percent, text)
    text = _NUMBER.sub(lambda m: _num(m.group(1), m.group(2)), text)
    return re.sub(r"\s{2,}", " ", text).strip()


_DIGIT_WORDS = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "shunya": "0", "ek": "1", "do": "2", "teen": "3", "char": "4", "chaar": "4",
    "paanch": "5", "panch": "5", "chhe": "6", "che": "6", "saat": "7", "aath": "8", "nau": "9",
}


def spoken_digits(text: str) -> str:
    """'four three two one' / '4 3 2 1' / 'char teen do ek' -> '4321'."""
    out = []
    for token in re.findall(r"[a-zA-Z]+|\d", str(text).lower()):
        if token.isdigit():
            out.append(token)
        elif token in _DIGIT_WORDS:
            out.append(_DIGIT_WORDS[token])
    return "".join(out)
