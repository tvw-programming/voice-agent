"""Feature 2: search students by class, division and roll number.

Supported search shapes:
  name         first + last name                      "Priya Kulkarni"
  name_class   first + last + class/division          "Aarav Deshmukh in 8-A"
  first_class  first name + class + division          "Aarav in 8-A"
  class_roll   class + division + roll number         "roll twelve, eight A"

The LLM extracts the pieces; this file normalises them ("eighth", "VIII",
"aathvi" -> "8"; "ay" -> "A") and filters API records.
"""
import re

# ---- class ------------------------------------------------------------------

_CLASS_WORDS = {
    # English cardinals and ordinals
    "one": 1, "first": 1, "two": 2, "second": 2, "three": 3, "third": 3, "four": 4, "fourth": 4,
    "five": 5, "fifth": 5, "six": 6, "sixth": 6, "seven": 7, "seventh": 7, "eight": 8, "eighth": 8,
    "nine": 9, "ninth": 9, "ten": 10, "tenth": 10, "eleven": 11, "eleventh": 11,
    "twelve": 12, "twelfth": 12,
    # Hindi ordinals (romanised)
    "pehli": 1, "pehla": 1, "doosri": 2, "dusri": 2, "doosra": 2, "teesri": 3, "tisri": 3,
    "chauthi": 4, "chautha": 4, "paanchvi": 5, "panchvi": 5, "chhathi": 6, "chhatvi": 6, "chhati": 6,
    "saatvi": 7, "satvi": 7, "aathvi": 8, "athvi": 8, "aathva": 8, "nauvi": 9, "nauvin": 9, "navvi": 9,
    "dasvi": 10, "dusvi": 10, "dasvin": 10, "gyarahvi": 11, "barahvi": 12,
    # Marathi ordinals (romanised)
    "pahili": 1, "pachvi": 5, "sahavi": 6, "aathavi": 8, "navavi": 9, "dahavi": 10,
    "akravi": 11, "baravi": 12,
}
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8,
          "ix": 9, "x": 10, "xi": 11, "xii": 12}
_PRE_PRIMARY = {
    "nursery": "Nursery",
    "lkg": "LKG", "jrkg": "LKG", "juniorkg": "LKG",
    "ukg": "UKG", "srkg": "UKG", "seniorkg": "UKG",
}
_STREAMS = {"science": "Science", "commerce": "Commerce", "arts": "Arts"}
_FILLER_WORDS = {"class", "std", "standard", "grade", "the", "in", "of", "kaksha", "iyatta", "iyatha"}


def normalize_class(value) -> str | None:
    """'8', '8th', 'eighth', 'VIII', 'aathvi', 'class 8' -> '8'; 'Jr KG' -> 'LKG'; '11 science' -> '11 Science'."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    squashed = re.sub(r"[^a-z]", "", text)
    if squashed in _PRE_PRIMARY:
        return _PRE_PRIMARY[squashed]
    stream = next((label for word, label in _STREAMS.items() if word in text), None)
    number = None
    m = re.search(r"\d+", text)
    if m:
        number = int(m.group())
    else:
        for token in re.findall(r"[a-z]+", text):
            if token in _FILLER_WORDS:
                continue
            if token in _CLASS_WORDS:
                number = _CLASS_WORDS[token]
                break
            if token in _ROMAN:
                number = _ROMAN[token]
                break
    if number is None or not 1 <= number <= 12:
        return None
    return f"{number} {stream}" if stream and number >= 11 else str(number)


# ---- division ---------------------------------------------------------------

_LETTER_NAMES = {
    "a": "A", "ay": "A", "ae": "A", "eh": "A",
    "b": "B", "bee": "B", "be": "B",
    "c": "C", "see": "C", "sea": "C", "si": "C",
    "d": "D", "dee": "D", "di": "D",
    "e": "E", "ee": "E",
    "f": "F", "ef": "F", "eff": "F",
    "g": "G", "gee": "G", "jee": "G",
    "h": "H", "aitch": "H", "ech": "H", "edge": "H",
}


def normalize_division(value, word_divisions: list[str] | None = None) -> str | None:
    """'a', 'A', 'ay', 'division B', '8-A' (takes the letter) -> 'A'. Word divisions like 'Rose' allowed via config."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for word in word_divisions or []:
        if text.lower() == word.lower() or re.search(rf"\b{re.escape(word)}\b", text, re.I):
            return word
    low = re.sub(r"\b(division|div|section|tukdi)\b\.?", " ", text.lower())
    tokens = re.findall(r"[a-z]+", low)
    for token in reversed(tokens):
        if token in _LETTER_NAMES:
            return _LETTER_NAMES[token]
    return None


# ---- roll number ------------------------------------------------------------

_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
          "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
          "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90}
_HINDI_NUMBERS = {
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "chhe": 6, "che": 6,
    "saat": 7, "aath": 8, "nau": 9, "das": 10, "gyarah": 11, "barah": 12, "baara": 12, "terah": 13,
    "chaudah": 14, "pandrah": 15, "solah": 16, "satrah": 17, "atharah": 18, "unnis": 19, "bees": 20,
    "ikkis": 21, "bais": 22, "teis": 23, "chaubis": 24, "pachchis": 25, "chhabbis": 26, "sattais": 27,
    "atthais": 28, "untis": 29, "tees": 30, "chalis": 40, "pachas": 50,
}


def normalize_roll(value) -> str | None:
    """'12', 'twelve', 'roll number 12', 'forty two', 'baara' -> '12'."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    m = re.search(r"\d+", text)
    if m:
        return str(int(m.group()))
    total, found = 0, False
    for token in re.findall(r"[a-z]+", text):
        if token in _TENS:
            total += _TENS[token]
            found = True
        elif token in _UNITS:
            total += _UNITS[token]
            found = True
        elif token in _HINDI_NUMBERS:
            total += _HINDI_NUMBERS[token]
            found = True
    return str(total) if found and total > 0 else None


# ---- search shape -----------------------------------------------------------

def search_shape(first: str, last: str, cls: str | None, div: str | None, roll: str | None) -> str | None:
    """Return the search shape, or None if the combination is not enough to search."""
    if first and last:
        return "name_class" if cls else "name"
    if first and cls and div:
        return "first_class"
    if cls and div and roll:
        return "class_roll"
    return None


def missing_info_message(first: str, last: str, cls, div, roll) -> str:
    if roll and not (cls and div):
        return "Ask for the class and division as well as the roll number."
    if cls and not div:
        return "Ask for the division too, or the student's full name."
    if last and not first:
        return "Ask for the student's first name too."
    if first and not last:
        return "Ask for the surname, or the class and division."
    return "Ask for the student's full name, or their class, division and roll number."


def same_class(record_value, wanted: str | None) -> bool:
    if not wanted:
        return True
    return normalize_class(record_value) == wanted


def same_division(record_value, wanted: str | None, word_divisions=None) -> bool:
    if not wanted:
        return True
    return normalize_division(record_value, word_divisions) == wanted


def same_roll(record_value, wanted: str | None) -> bool:
    if not wanted:
        return True
    return normalize_roll(record_value) == wanted


def filter_records(records: list[dict], get, cls: str | None, div: str | None, roll: str | None,
                   word_divisions=None) -> list[dict]:
    """Keep records matching the normalised class/division/roll. `get(record, field)` reads mapped fields."""
    return [r for r in records
            if same_class(get(r, "class"), cls)
            and same_division(get(r, "division"), div, word_divisions)
            and same_roll(get(r, "roll_number"), roll)]


def describe(cls: str | None, div: str | None, roll: str | None) -> str:
    """'roll 12 of 8-A' style text for messages to the LLM."""
    where = f"{cls}-{div}" if cls and div else (cls or "")
    return f"roll {roll} of {where}" if roll else where
