"""Feature 4: sound-based matching for Indian names.

Spelling-based fuzzy matching misses pairs like Deshmukh/Desmukh or
Kulkarni/Kulkarny. Each name is reduced to an "Indian phonetic key" and keys
are compared instead. Standard Soundex/Metaphone keep "sh" and "s" apart, which
is exactly the confusion speech-to-text makes with Indian names, so the rules
here are custom.

    indian_phonetic_key("Deshmukh") == indian_phonetic_key("Desmukh") == "desmuk"

Used by tools/student.py for ranking candidates, generating spelling variants
to search with, and deciding when the caller must confirm a found name.
"""
import re

from rapidfuzz import fuzz
from rapidfuzz.distance import JaroWinkler

_VOWELS = "aeiou"

# Applied in order. Multi-letter patterns first.
_RULES: list[tuple[str, str]] = [
    (r"ph", "f"),
    (r"chh", "s"),
    (r"sh", "s"),
    (r"ch", "s"),
    (r"kh", "k"),
    (r"bh", "b"),
    (r"dh", "d"),
    (r"gh", "g"),
    (r"jh", "j"),
    (r"th", "t"),
    (r"ck", "k"),
    (r"w", "v"),
    (r"z", "j"),
    (r"q", "k"),
    (r"x", "ks"),
    (r"c", "k"),              # any c left after ch/chh: "Carol" -> karol
    (r"aa", "a"),
    (r"ee", "i"),
    (r"ii", "i"),
    (r"oo", "u"),
    (r"uu", "u"),
    (r"ou", "au"),            # Choudhary -> Chaudhari
    (r"iy(?=[aeiou])", "i"),  # Priya -> pria
    (r"y$", "i"),             # Kulkarny -> kulkarni
]


def indian_phonetic_key(name: str) -> str:
    key = re.sub(r"[^a-z]", "", (name or "").lower())
    for pattern, repl in _RULES:
        key = re.sub(pattern, repl, key)
    # Collapse doubled letters: "Bhatt" -> "bat", "Anna" -> "ana".
    return re.sub(r"(.)\1+", r"\1", key)


def part_similarity(heard: str, record: str) -> float:
    """0..1 similarity of one name part (first name or surname)."""
    heard, record = (heard or "").strip().lower(), (record or "").strip().lower()
    if not heard or not record:
        return 0.0
    if heard == record:
        return 1.0
    k1, k2 = indian_phonetic_key(heard), indian_phonetic_key(record)
    if k1 and k1 == k2:
        return 0.97
    spelling = fuzz.ratio(heard, record) / 100
    sound = JaroWinkler.similarity(k1, k2) if k1 and k2 else 0.0
    return max(spelling, sound)


def keys_equal(a: str, b: str) -> bool:
    return bool(a and b) and indian_phonetic_key(a) == indian_phonetic_key(b)


def name_score(first_heard: str, last_heard: str, first_rec: str, last_rec: str,
               first_weight: float = 0.4) -> tuple[float, float]:
    """Return (total, surname_score). A missing heard part is ignored."""
    last = part_similarity(last_heard, last_rec) if last_heard else None
    first = part_similarity(first_heard, first_rec) if first_heard else None
    if first is None and last is None:
        return 0.0, 0.0
    if first is None:
        return last, last
    if last is None:
        return first, 1.0
    return first_weight * first + (1 - first_weight) * last, last


# Reverse substitutions used to guess how a misheard surname is really spelled.
_VARIANT_SWAPS = [
    ("sh", "s"), ("s", "sh"),
    ("kh", "k"), ("k", "kh"),
    ("dh", "d"), ("d", "dh"),
    ("th", "t"), ("t", "th"),
    ("bh", "b"), ("b", "bh"),
    ("v", "w"), ("w", "v"),
    ("ee", "i"), ("i", "ee"),
    ("oo", "u"), ("u", "oo"), ("au", "ou"), ("ou", "au"),
    ("aa", "a"),
]


def spelling_variants(name: str, limit: int = 6) -> list[str]:
    """Plausible alternative spellings of `name`, most useful first.

    'Desmukh' -> ['Deshmukh', ...], 'Kulkarny' -> ['Kulkarni', ...].
    Only variants with the same phonetic key are returned, so they all "sound" the same.
    """
    src = (name or "").strip()
    if not src:
        return []
    low = src.lower()
    key = indian_phonetic_key(low)
    out: list[str] = []

    def add(candidate: str):
        cap = candidate[:1].upper() + candidate[1:]
        if candidate != low and cap not in out and indian_phonetic_key(candidate) == key:
            out.append(cap)

    if low.endswith("y"):
        add(low[:-1] + "i")
    if low.endswith("i"):
        add(low[:-1] + "y")
    for a, b in _VARIANT_SWAPS:
        start = 0
        while (i := low.find(a, start)) != -1:
            add(low[:i] + b + low[i + len(a):])
            start = i + 1
            if len(out) >= limit:
                return out[:limit]
    return out[:limit]
