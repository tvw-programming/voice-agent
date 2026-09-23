"""Feature 3: spelling mode for names.

When speech-to-text gets a name wrong, the caller spells it. The LLM passes the
letters exactly as heard (e.g. in `last_name_spelled`) and this file turns them
into a name:

    "P R I Y A"                  -> "Priya"
    "pee are eye why ay"         -> "Priya"
    "K U L K A R N I"            -> "Kulkarni"
    "D E S H M U K H"            -> "Deshmukh"
    "B for Bombay, H, A, T, T"   -> "Bhatt"
    "double T"                   -> "TT"
    "P jaise Pune"               -> "P"

It also supplies the "spelling listening profile": while the caller spells,
the agent waits longer before deciding they've finished speaking.
"""
import copy
import re

_LETTER_NAMES = {
    "a": "a", "ay": "a", "ae": "a", "eh": "a",
    "b": "b", "bee": "b", "be": "b", "bi": "b",
    "c": "c", "see": "c", "sea": "c", "si": "c",
    "d": "d", "dee": "d", "di": "d",
    "e": "e", "ee": "e",
    "f": "f", "ef": "f", "eff": "f",
    "g": "g", "gee": "g", "jee": "g", "ji": "g",
    "h": "h", "aitch": "h", "ech": "h", "edge": "h", "eich": "h",
    "i": "i", "eye": "i", "aai": "i", "ai": "i",
    "j": "j", "jay": "j", "je": "j",
    "k": "k", "kay": "k", "kae": "k", "ke": "k",
    "l": "l", "el": "l", "ell": "l",
    "m": "m", "em": "m",
    "n": "n", "en": "n",
    "o": "o", "oh": "o", "ow": "o",
    "p": "p", "pee": "p", "pe": "p", "pi": "p",
    "q": "q", "queue": "q", "cue": "q", "kyu": "q", "que": "q",
    "r": "r", "are": "r", "aar": "r", "ar": "r",
    "s": "s", "es": "s", "ess": "s",
    "t": "t", "tee": "t", "te": "t", "ti": "t",
    "u": "u", "you": "u", "yu": "u", "yoo": "u",
    "v": "v", "vee": "v", "ve": "v", "vi": "v",
    "w": "w", "doubleyou": "w", "dabalyu": "w", "dablu": "w",
    "x": "x", "ex": "x", "eks": "x",
    "y": "y", "why": "y", "wai": "y", "wy": "y",
    "z": "z", "zed": "z", "zee": "z", "jhed": "z", "zedd": "z",
}
# "B for Bombay", "B as in Bombay", "B jaise Bombay", "B like Bombay", "B se Bombay"
_EXAMPLE = re.compile(r"\b([a-z]+)\s+(?:for|as\s+in|like|jaise|jaisa|se|mhanje)\s+[a-z]+\b")
_DOUBLE = re.compile(r"\b(?:double|dabal|dubble)\s+([a-z]+)\b")
_NOISE = {"capital", "small", "letter", "letters", "and", "then", "next", "comma", "dot", "full", "stop",
          "the", "spelling", "is", "its", "it's", "that's", "thats", "ok", "okay", "haan", "hmm", "hmmm", "um", "umm", "uh", "uhh", "err", "sorry", "wait"}


def assemble_spelling(text: str) -> str | None:
    """Turn spoken letters into a capitalised name, or None if nothing letter-like was found.

    A word that is already a whole name ("PRIYA" typed by STT as one token) is kept as-is.
    """
    if not text:
        return None
    low = text.lower().replace("-", " ").replace(".", " ").replace(",", " ")
    low = re.sub(r"\bdouble\s*you\b", "doubleyou", low)
    low = _EXAMPLE.sub(lambda m: m.group(1), low)
    low = _DOUBLE.sub(lambda m: (_LETTER_NAMES.get(m.group(1), m.group(1)) * 2), low)
    letters, words = [], []
    for token in low.split():
        if token in _NOISE:
            continue
        if token in _LETTER_NAMES:
            letters.append(_LETTER_NAMES[token])
        elif token.isalpha() and len(token) >= 2 and len(set(token)) == 1:
            letters.append(token)             # "tt" produced by "double t"
        elif token.isalpha() and len(token) >= 3:
            words.append(token)               # "that", "surname" ... or an already-joined "PRIYA"
    if len(letters) < 2:
        # STT sometimes joins the letters itself ("PRIYA"); accept a single such word.
        if len(words) == 1 and not letters:
            letters = [words[0]]
        else:
            return None
    word = "".join(letters)
    return word[:1].upper() + word[1:]


def letters_for_readback(name: str) -> str:
    """'Priya' -> 'P-R-I-Y-A' so the agent can read the spelling back."""
    return "-".join((name or "").upper())


_ASKS_TO_SPELL = re.compile(r"\b(spell|spelling|letter by letter)\b|स्पेलिंग|अक्षर", re.I)


def asks_to_spell(agent_reply: str) -> bool:
    """True if the agent's last reply asked the caller to spell something."""
    return bool(_ASKS_TO_SPELL.search(agent_reply or ""))


def spelling_audio_config(audio_cfg: dict, spelling_cfg: dict | None) -> dict:
    """Copy of the audio config with longer pauses allowed, for one spelling turn."""
    spelling_cfg = spelling_cfg or {}
    cfg = copy.deepcopy(audio_cfg)
    cfg["vad"]["min_silence_ms"] = spelling_cfg.get("min_silence_ms", 1500)
    cfg["max_utterance_seconds"] = spelling_cfg.get("max_utterance_seconds", 30)
    return cfg


SPELLING_STT_PROMPT = "The caller is spelling a name letter by letter: A, B, C, D, E, P, R, S, T, double S."
