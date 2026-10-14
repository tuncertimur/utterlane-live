"""Turkish name, nomination, correction, consent and wake-word parsing.

The parsers work on STT text. Whisper usually capitalizes proper names, so most patterns
require a capitalized name token; explicit forms ("benim adım …") also accept lowercase.
Every parser returns ``None`` when unsure: a missed introduction is cheaper than a wrong
label, and the user can always correct a label by voice or in the UI.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from types import ModuleType

_UPPER = "A-ZÇĞİÖŞÜÂÎÛ"
_LOWER = "a-zçğıöşüâîû"
_NAME = rf"[{_UPPER}][{_LOWER}]{{1,20}}"
_ANY_NAME = rf"[{_UPPER}{_LOWER}][{_LOWER}]{{1,20}}"
_APOS = "['’`]"

# Capitalized words that follow "Ben …" but are not personal names.
_NOT_NAMES = {
    "türk",
    "kürt",
    "alman",
    "ingiliz",
    "amerikalı",
    "fransız",
    "rus",
    "arap",
    "müslüman",
    "hristiyan",
    "yahudi",
    "doktor",
    "öğretmen",
    "mühendis",
    "avukat",
    "bey",
    "hanım",
    "evet",
    "hayır",
    "tamam",
    "merhaba",
    "selam",
    "lena",
    "utterlane",
    "istanbul",
    "ankara",
    "izmir",
    "türkiye",
    "allah",
    "tanrı",
    "abi",
    "abla",
    "anne",
    "baba",
}

# Words that cannot be names when they appear in lowercase after "adım/ismim".
_LOWER_STOP = {
    "ne",
    "bu",
    "şu",
    "o",
    "da",
    "de",
    "yok",
    "var",
    "değil",
    "ve",
    "ile",
    "mi",
    "mı",
}

_COPULA = re.compile(rf"{_APOS}?(?:y)?(?:ım|im|um|üm|ız|iz|uz|üz)$")
_VOWELS = set("aeıioöuüâîû")


def tr_lower(text: str) -> str:
    return text.replace("I", "ı").replace("İ", "i").lower()


def tr_title(word: str) -> str:
    if not word:
        return word
    first = word[0]
    first = "İ" if first == "i" else "I" if first == "ı" else first.upper()
    return first + tr_lower(word[1:])


def _clean_name(raw: str) -> str | None:
    word = raw.strip(" ,.;:!?\"'’`")
    if _APOS in word or any(ch in word for ch in "'’`"):
        stem = re.split(_APOS, word)[0]
    else:
        stem = word
        match = _COPULA.search(word)
        # Strip only buffered copulas ("Zümrayım" → "Zümra"); "Selim" must stay "Selim".
        if match and match.group(0).startswith("y") and len(word) - len(match.group(0)) >= 2:
            candidate = word[: match.start()]
            if candidate and candidate[-1].lower() in _VOWELS:
                stem = candidate
    if len(stem) < 2 or tr_lower(stem) in _NOT_NAMES:
        return None
    return tr_title(stem)


@dataclass(frozen=True, slots=True)
class NameClaims:
    self_intro: str | None = None
    nomination: str | None = None
    correction_from: str | None = None
    consent: bool | None = None
    addressed_assistant: bool = False


# "Benim adım X" anywhere, or "Adım X" / "İsmim X" at the start of a sentence. ("bir adım
# daha" must not match, so a bare "adım" is only trusted at a sentence start.)
_SELF_EXPLICIT = re.compile(
    rf"(?:(?:^|[\s,.!?])[Bb]enim\s+(?:ad[ıi]m|ismim)|(?:^|[.!?]\s*|,\s*)(?:[Aa]d[ıi]m|[İi]smim))"
    rf"\s+(?:da\s+|de\s+)?({_ANY_NAME}(?:{_APOS}[{_LOWER}]+)?)(?P<end>\s*(?:$|[,.!?]|\s+ve\b))?",
)
_SELF_BEN = re.compile(
    rf"(?:^|[\s,.!?])[Bb]en\s+(?:de\s+|da\s+)?({_NAME}(?:{_APOS}[{_LOWER}]+)?)(?=$|[\s,.!?])"
)
_SELF_BANA = re.compile(
    rf"(?:^|[\s,.!?])[Bb]ana\s+({_NAME})\s+(?:de|deyin|diyebilirsin|diyebilirsiniz|diyin)\b"
)
_NOT_ME = re.compile(rf"(?:^|[\s,.!?])[Bb]en\s+({_NAME})\s+değil(?:im)?\b")

_NOMINATION_PATTERNS = [
    re.compile(
        rf"({_NAME}),?\s+(?:sen|siz)\s+(?:de\s+|da\s+|bir\s+)?"
        r"(?:konuş|anlat|söyle|başla|devam\s+et|ne\s+düşünüyor|ne\s+dersin|ne\s+diyor|buyur|katıl)"
    ),
    re.compile(rf"(?:[Ss]öz|[Ss]ıra)\s+(?:sende|sizde|onda),?\s+({_NAME})\b"),
    re.compile(rf"({_NAME}),?\s+(?:söz|sıra)\s+(?:sende|sizde)\b"),
    re.compile(rf"\b[Bb]uyur(?:un)?,?\s+({_NAME})\b"),
    re.compile(rf"({_NAME}),?\s+buyur(?:un)?\b"),
    re.compile(rf"\b[Hh]adi\s+({_NAME})\b"),
    re.compile(rf"({_NAME}){_APOS}?(?:y?[ae])\s+(?:söz\s+)?(?:veriyorum|verelim|bırakıyorum)"),
    re.compile(
        rf"({_NAME}),?\s+(?:sen\s+|siz\s+)?ne\s+(?:düşünüyorsun|düşünüyorsunuz|dersin|dersiniz)"
    ),
]

_CONSENT_YES = re.compile(
    r"\b(evet|olur|tabii|tabi|elbette|kesinlikle|hatırla|kaydet|isterim|tamam|tamamdır|lütfen|aynen"
    r"|onaylıyorum|kabul\s+ediyorum|izin\s+veriyorum)\b"
)
_CONSENT_NO = re.compile(
    r"\b(hayır|istemiyorum|istemem|hatırlama|kaydetme|gerek\s+yok|olmaz|asla|yok\s+teşekkürler"
    r"|onaylamıyorum|kabul\s+etmiyorum|reddediyorum|izin\s+vermiyorum)\b"
)

_UTTERLANE = re.compile(r"(?:u|a|ö|o|ı)t+(?:ı|i|e|ö|u|a)?r+(?:\s)?l(?:e|a|ı|i)y?(?:i|ı)?n")


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_self_intro(text: str, known_names: frozenset[str] = frozenset()) -> str | None:
    """Return the name a speaker gives for themselves, or ``None``."""

    text = _norm(text)
    if _NOT_ME.search(text) and not re.search(r"\b(?:ad[ıi]m|ismim)\b", text, re.IGNORECASE):
        # "Ben Ahmet değilim, Mehmet'im" → the second clause carries the name.
        rest = _NOT_ME.split(text, maxsplit=1)[-1]
        found = re.search(rf"({_NAME}(?:{_APOS}[{_LOWER}]+)?)", rest)
        return _clean_name(found.group(1)) if found else None
    for pattern in (_SELF_EXPLICIT, _SELF_BANA, _SELF_BEN):
        for match in pattern.finditer(text):
            token = match.group(1)
            if pattern is _SELF_EXPLICIT and token[0].islower():
                # A lowercase name is trusted only when it ends the clause ("adım ahmet.").
                if tr_lower(token) in _LOWER_STOP or match.group("end") is None:
                    continue
            elif pattern is _SELF_BEN:
                # "Ben Tuncer" requires a capitalized token unless the name is already known.
                if token[0].islower() and tr_lower(token) not in known_names:
                    continue
            name = _clean_name(token)
            if name:
                return name
    if known_names:
        lowered = tr_lower(text)
        for known in known_names:
            if re.search(
                rf"(?:^|[\s,.!?])ben\s+{re.escape(known)}(?:{_APOS}?y?[ıiuü]m)?(?=$|[\s,.!?])",
                lowered,
            ):
                return tr_title(known)
    return None


def parse_correction(text: str) -> str | None:
    """Return the name a speaker says they are NOT ("Ben Ahmet değilim")."""

    match = _NOT_ME.search(_norm(text))
    return _clean_name(match.group(1)) if match else None


def parse_nomination(text: str, *, exclude: frozenset[str] = frozenset()) -> str | None:
    """Return a person the speaker hands the floor to ("Ahmet sen konuş")."""

    text = _norm(text)
    for pattern in _NOMINATION_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        name = _clean_name(match.group(1))
        if name and tr_lower(name) not in exclude:
            return name
    return None


def parse_consent(text: str) -> bool | None:
    lowered = tr_lower(_norm(text))
    no = bool(_CONSENT_NO.search(lowered))
    yes = bool(_CONSENT_YES.search(lowered))
    if no and not re.search(r"\bhatırla\b", lowered):
        return False
    if yes and not no:
        return True
    return None


def mentions_wake(text: str, assistant_name: str = "Lena") -> bool:
    lowered = tr_lower(_norm(text))
    name = tr_lower(assistant_name)
    if re.search(rf"\b{re.escape(name)}\b", lowered):
        return True
    squashed = re.sub(rf"[^{_LOWER}\s]", "", lowered)
    return bool(_UTTERLANE.search(squashed))


def analyze(
    text: str,
    *,
    assistant_name: str = "Lena",
    known_names: frozenset[str] = frozenset(),
    language: str = "tr",
) -> NameClaims:
    exclude = frozenset({tr_lower(assistant_name), "utterlane"})
    lang: ModuleType | None = None
    if language == "en":
        from utterlane_live import naming_en

        lang = naming_en
    intro_fn = lang.parse_self_intro if lang else parse_self_intro
    self_intro = intro_fn(_norm(text), known_names)
    if self_intro and tr_lower(self_intro) in exclude:
        self_intro = None
    nomination = (lang.parse_nomination if lang else parse_nomination)(_norm(text), exclude=exclude)
    if nomination and self_intro and nomination == self_intro:
        nomination = None
    return NameClaims(
        self_intro=self_intro,
        nomination=nomination,
        correction_from=(lang.parse_correction if lang else parse_correction)(_norm(text)),
        consent=(lang.parse_consent if lang else parse_consent)(text),
        addressed_assistant=mentions_wake(text, assistant_name),
    )


_QUESTION_PARTICLE = re.compile(
    r"\b(?:mı|mi|mu|mü)(?:sın|sin|sun|sün|yım|yim|yum|yüm|yız|yiz|yuz|yüz|sınız|siniz|sunuz|sünüz|dır|dir|dur|dür)?\b"
)
_QUESTION_WORD = re.compile(
    r"(?:^|[\s,])(?:ne|neden|niye|niçin|nasıl|kim|kime|kimin|hangi|kaç|nerede|nereye|nereden|ne zaman)\b"
)


def looks_like_question(text: str, language: str = "tr") -> bool:
    """True for questions and requests that could be meant for the assistant."""

    if language == "en":
        from utterlane_live.naming_en import looks_like_question as en_question

        return en_question(text)
    lowered = tr_lower(_norm(text))
    if "?" in lowered:
        return True
    if _QUESTION_PARTICLE.search(lowered) or _QUESTION_WORD.search(lowered):
        return True
    return bool(re.search(r"\b(?:söyle|anlat|açıkla|öner|bul|hatırlat|yardım\s+et)\b", lowered))


def vocative_name(text: str, language: str = "tr") -> str | None:
    """A name the sentence opens with as an address ("Zümra, sen …" / "Ahmet sen …")."""

    if language == "en":
        from utterlane_live.naming_en import vocative_name as en_vocative

        return en_vocative(text)
    match = re.match(rf"\s*(?:[Hh]ey\s+|[Bb]ak\s+)?({_NAME}),?\s", _norm(text) + " ")
    if not match:
        return None
    name = _clean_name(match.group(1))
    if name is None:
        return None
    rest = _norm(text)[match.end(1) :]
    # Require a comma or a second-person cue so "Bugün …" or "Ankara …" are not vocatives.
    if rest.startswith(",") or re.match(r"\s+(?:sen|siz|sence|sizce|bak|ne|nasıl)\b", rest):
        return name
    return None
