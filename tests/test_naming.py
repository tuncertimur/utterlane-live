import pytest

from utterlane_live.naming import (
    analyze,
    mentions_wake,
    parse_consent,
    parse_correction,
    parse_nomination,
    parse_self_intro,
    tr_lower,
)


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("Ben Tuncer.", "Tuncer"),
        ("Merhaba, ben Tuncer Timur. Bu kayıt test için.", "Tuncer"),
        ("Ben Zümra Timur. Bu kayıt Utterlane özel ses testleri için hazırlanıyor.", "Zümra"),
        ("Benim adım Ahmet.", "Ahmet"),
        ("Selam, adım Ayşe, yeni katıldım.", "Ayşe"),
        ("İsmim Mehmet ve bugün buradayım.", "Mehmet"),
        ("Ben Zümrayım.", "Zümra"),
        ("Ben Ahmet'im, merhaba.", "Ahmet"),
        ("Ben Selim.", "Selim"),
        ("Bana Can diyebilirsin.", "Can"),
        ("benim adım ahmet.", "Ahmet"),
        ("Ben de Zümra.", "Zümra"),
        ("Ben İsmail.", "İsmail"),
    ],
)
def test_self_intro_detected(text: str, name: str) -> None:
    assert parse_self_intro(text) == name


@pytest.mark.parametrize(
    "text",
    [
        "Ben de geldim.",
        "Ben şimdi çıkıyorum.",
        "Bir adım daha atalım.",
        "Ben Türk'üm.",
        "Ben İstanbul'dayım.",
        "Bugün hava çok güzel.",
        "Benim adım ne biliyor musun",
        "Ben bunu bilmiyorum.",
        "Zümra bugün proje için hangi adımları tamamlayalım?",
        "Lena, ben geldim.",
    ],
)
def test_self_intro_rejects_non_names(text: str) -> None:
    assert parse_self_intro(text) is None


def test_lowercase_ben_name_only_when_known() -> None:
    assert parse_self_intro("ben tuncer") is None
    assert parse_self_intro("ben tuncer", frozenset({"tuncer"})) == "Tuncer"


def test_correction_clause_wins() -> None:
    assert parse_correction("Hayır, ben Ahmet değilim, Mehmet'im.") == "Ahmet"
    assert parse_self_intro("Hayır, ben Ahmet değilim, Mehmet'im.") == "Mehmet"
    assert parse_correction("Ben Ahmet.") is None


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("Ahmet sen konuş.", "Ahmet"),
        ("Ahmet, sen ne düşünüyorsun?", "Ahmet"),
        ("Söz sende Zümra.", "Zümra"),
        ("Zümra, söz sende.", "Zümra"),
        ("Buyur Ahmet.", "Ahmet"),
        ("Ahmet buyur.", "Ahmet"),
        ("Hadi Ayşe, anlat.", "Ayşe"),
        ("Sözü Ahmet'e bırakıyorum.", "Ahmet"),
        ("Ahmet'e söz veriyorum.", "Ahmet"),
        ("Mehmet sen de anlat bakalım.", "Mehmet"),
    ],
)
def test_nomination_detected(text: str, name: str) -> None:
    assert parse_nomination(text) == name


def test_nomination_excludes_assistant() -> None:
    assert parse_nomination("Lena sen konuş.", exclude=frozenset({"lena"})) is None
    assert parse_nomination("Bugün hava güzel, sen ne dersin?") is None


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("Evet, hatırla.", True),
        ("Olur tabii.", True),
        ("Tamam.", True),
        ("Hayır, istemiyorum.", False),
        ("Hatırlama lütfen.", False),
        ("Gerek yok.", False),
        ("Bugün hava güzel.", None),
    ],
)
def test_consent(text: str, value: bool | None) -> None:
    assert parse_consent(text) is value


@pytest.mark.parametrize(
    "text",
    [
        "Lena, bugün hava nasıl?",
        "lena bir şey sorabilir miyim",
        "Utterlane, merhaba.",
        "Atırleyn duyuyor musun?",
        "Hey ötırleyn",
        "Uterleyn bana yardım et.",
    ],
)
def test_wake_detected(text: str) -> None:
    assert mentions_wake(text)


@pytest.mark.parametrize(
    "text", ["Bugün toplantı var.", "Ahmet sen konuş.", "Lenadan bahsetmiyorum"]
)
def test_wake_not_detected(text: str) -> None:
    assert not mentions_wake(text)


def test_analyze_combines_claims() -> None:
    claims = analyze("Merhaba Lena, ben Tuncer. Ahmet sen de konuş.")
    assert claims.self_intro == "Tuncer"
    assert claims.nomination == "Ahmet"
    assert claims.addressed_assistant
    assert claims.correction_from is None


def test_tr_lower() -> None:
    assert tr_lower("IĞDIR İzmir") == "ığdır izmir"


def test_sentence_chunker_first_chunk_breaks_early() -> None:
    from utterlane_live.tts import SentenceChunker

    chunker = SentenceChunker()
    out = list(chunker.feed("Mercimek çorbası çok güzel bir fikir, yanına pilav da yapabilirsin. "))
    assert out == ["Mercimek çorbası çok güzel bir fikir,", "yanına pilav da yapabilirsin."]
    out = list(chunker.feed("Sonra salata, ayran ve tatlı da ekleriz. "))
    assert out == ["Sonra salata, ayran ve tatlı da ekleriz."]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Sence akşam ne yesek?", True),
        ("Bu çorba kaç dakikada pişer", True),
        ("Yarın hava güzel olur mu", True),
        ("Bana bir tarif söyle", True),
        ("Aynı ay başka bir yolcu uçağı pisti aşarak duvara çarptı.", False),
        ("Bence mercimek çorbası iyi olur.", False),
    ],
)
def test_looks_like_question(text: str, expected: bool) -> None:
    from utterlane_live.naming import looks_like_question

    assert looks_like_question(text) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Zümra, sen akşam ne yemek istersin?", "Zümra"),
        ("Ahmet sen ne düşünüyorsun?", "Ahmet"),
        ("Hey Mehmet, bakar mısın?", "Mehmet"),
        ("Bugün hava çok güzel.", None),
        ("Ankara bu yıl çok soğuk mu?", None),
        ("Lena, bugün ne yapalım?", None),  # the assistant is handled by wake words
    ],
)
def test_vocative_name(text: str, expected: str | None) -> None:
    from utterlane_live.naming import vocative_name

    assert vocative_name(text) == expected


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("Bu isteği onaylamıyorum.", False),
        ("Kabul etmiyorum.", False),
        ("Onaylıyorum, kaydedebilirsin.", True),
        ("İzin veriyorum.", True),
    ],
)
def test_consent_formal_phrases(text: str, value: bool) -> None:
    assert parse_consent(text) is value


def test_foreign_script_removed_from_transcript() -> None:
    from utterlane_live.stt import clean_transcript

    assert clean_transcript("Kimliğin 계신 değil .") == "Kimliğin değil."
    assert clean_transcript("Bugün meeting var, OK?") == "Bugün meeting var, OK?"
