"""English name, nomination, correction, consent and question parsing.

Same contract as the Turkish parsers in ``naming``: return ``None`` when unsure. Whisper
capitalizes proper names, so most patterns require a capitalized name token.
"""

from __future__ import annotations

import re

# Latin letters incl. accented ones (Zümra, José, Łukasz); Python's re has no \p{Lu}.
_LATIN = [chr(code) for code in range(0x00C0, 0x0250)]
_UPPER = "A-Z" + "".join(ch for ch in _LATIN if ch.isupper())
_LOWER = "a-z" + "".join(ch for ch in _LATIN if ch.islower())
_NAME = rf"[{_UPPER}][{_LOWER}][{_UPPER}{_LOWER}'-]{{0,20}}"
_ANY_NAME = rf"[{_UPPER}{_LOWER}][{_LOWER}][{_UPPER}{_LOWER}'-]{{0,20}}"

_NOT_NAMES = {
    "i",
    "okay",
    "ok",
    "yes",
    "yeah",
    "no",
    "sure",
    "fine",
    "good",
    "great",
    "sorry",
    "here",
    "there",
    "back",
    "ready",
    "done",
    "home",
    "god",
    "lena",
    "utterlane",
    "english",
    "american",
    "british",
    "turkish",
    "german",
    "french",
    "spanish",
    "italian",
    "canadian",
    "australian",
    "indian",
    "chinese",
    "japanese",
    "korean",
    "russian",
    "dutch",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "doctor",
    "mister",
    "miss",
    "mrs",
    "mr",
    "ms",
    "the",
    "a",
    "an",
    "not",
    "just",
    "also",
}
_LOWER_STOP = {"not", "the", "a", "an", "so", "just", "very", "too", "also", "here", "back"}


def _clean(raw: str) -> str | None:
    word = raw.strip(" ,.;:!?\"'")
    word = re.sub(r"'s$", "", word)
    if len(word) < 2 or word.lower() in _NOT_NAMES:
        return None
    return word[0].upper() + word[1:]


_SELF_EXPLICIT = re.compile(
    rf"\b(?:[Mm]y name(?:'s| is)|[Cc]all me|[Yy]ou can call me)\s+({_ANY_NAME})(?P<end>\s*(?:$|[,.!?]|\s+and\b))?"
)
_SELF_IAM = re.compile(
    rf"(?:^|[\s,.!?])(?:I am|I'm|Im|[Tt]his is|[Ii]t's|[Ii]t is)\s+({_NAME})(?=$|[\s,.!?])"
)
_NOT_ME = re.compile(rf"\bI(?:'m| am) not\s+({_NAME})\b")

_NOMINATION_PATTERNS = [
    re.compile(
        rf"({_NAME}),?\s+(?:go ahead|your turn|you go|over to you|you're up|"
        r"what do you think|what about you|how about you|please go on|go on)"
    ),
    re.compile(
        rf"\b(?:[Oo]ver to you|[Yy]our turn|[Gg]o ahead|[Tt]he floor is yours),?\s+({_NAME})\b"
    ),
    re.compile(rf"\b[Ll]et'?s hear from\s+({_NAME})\b"),
    re.compile(rf"\b(?:[Hh]and(?:ing)? it over|[Pp]ass(?:ing)? it) to\s+({_NAME})\b"),
]

_CONSENT_YES = re.compile(
    r"\b(yes|yeah|yep|sure|of course|okay|ok|please do|remember me|absolutely|definitely|go ahead|"
    r"i agree|that's fine|fine by me)\b"
)
_CONSENT_NO = re.compile(
    r"\b(no|nope|don't|do not|no thanks|rather not|forget it|please don't|i decline)\b"
)

_QUESTION_START = re.compile(
    r"^\W*(?:what|why|how|when|where|who|whom|whose|which|can|could|would|will|do|does|did|is|are|"
    r"was|were|should|shall|may|might|have|has)\b"
)
_REQUEST = re.compile(r"\b(?:tell me|explain|recommend|suggest|help me|remind me|give me|find)\b")


def parse_self_intro(text: str, known_names: frozenset[str] = frozenset()) -> str | None:
    if _NOT_ME.search(text):
        rest = _NOT_ME.split(text, maxsplit=1)[-1]
        found = re.search(rf"(?:I(?:'m| am)\s+)?({_NAME})", rest)
        return _clean(found.group(1)) if found else None
    for match in _SELF_EXPLICIT.finditer(text):
        token = match.group(1)
        if token[0].islower() and (token.lower() in _LOWER_STOP or match.group("end") is None):
            continue
        name = _clean(token)
        if name:
            return name
    for match in _SELF_IAM.finditer(text):
        name = _clean(match.group(1))
        if name:
            return name
    if known_names:
        lowered = text.lower()
        for known in known_names:
            if re.search(rf"\b(?:i am|i'm|this is)\s+{re.escape(known)}\b", lowered):
                return known[0].upper() + known[1:]
    return None


def parse_correction(text: str) -> str | None:
    match = _NOT_ME.search(text)
    return _clean(match.group(1)) if match else None


def parse_nomination(text: str, *, exclude: frozenset[str] = frozenset()) -> str | None:
    for pattern in _NOMINATION_PATTERNS:
        match = pattern.search(text)
        if match:
            name = _clean(match.group(1))
            if name and name.lower() not in exclude:
                return name
    return None


def parse_consent(text: str) -> bool | None:
    lowered = text.lower()
    no = bool(_CONSENT_NO.search(lowered))
    yes = bool(_CONSENT_YES.search(lowered))
    if no and not re.search(r"\bremember me\b", lowered):
        return False
    if yes:
        return True
    return None


def looks_like_question(text: str) -> bool:
    lowered = text.lower().strip()
    return "?" in lowered or bool(_QUESTION_START.search(lowered) or _REQUEST.search(lowered))


def vocative_name(text: str) -> str | None:
    match = re.match(rf"\s*(?:[Hh]ey\s+)?({_NAME}),?\s", text.strip() + " ")
    if not match:
        return None
    name = _clean(match.group(1))
    if name is None:
        return None
    rest = text.strip()[match.end(1) :]
    if rest.startswith(",") or re.match(r"\s+(?:you|what|can|could|would|do|did|are|will)\b", rest):
        return name
    return None
