import pytest

from utterlane_live.naming import analyze, looks_like_question, vocative_name
from utterlane_live.naming_en import (
    parse_consent,
    parse_correction,
    parse_nomination,
    parse_self_intro,
)


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("Hi, I'm Alex.", "Alex"),
        ("I am Maria and I work here.", "Maria"),
        ("My name is Daniel.", "Daniel"),
        ("my name's priya.", "Priya"),
        ("You can call me Sam.", "Sam"),
        ("This is Tuncer speaking.", "Tuncer"),
        ("Hello everyone, I'm Zümra.", "Zümra"),
        ("My name is Çağrı.", "Çağrı"),
        ("This is Łukasz speaking.", "Łukasz"),
    ],
)
def test_self_intro(text: str, name: str) -> None:
    assert parse_self_intro(text) == name


@pytest.mark.parametrize(
    "text",
    [
        "I'm tired today.",
        "I'm English, by the way.",
        "It's Monday again.",
        "I am not sure about that.",
        "This is great!",
        "My name is not important.",
        "I'm OK.",
    ],
)
def test_self_intro_rejects_non_names(text: str) -> None:
    assert parse_self_intro(text) is None


def test_correction() -> None:
    assert parse_correction("No, I'm not Alex, I'm Sam.") == "Alex"
    assert parse_self_intro("No, I'm not Alex, I'm Sam.") == "Sam"


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("Alex, go ahead.", "Alex"),
        ("Maria, what do you think?", "Maria"),
        ("Over to you, Sam.", "Sam"),
        ("Your turn, Daniel.", "Daniel"),
        ("Let's hear from Priya.", "Priya"),
        ("Ömer, go ahead.", "Ömer"),
    ],
)
def test_nomination(text: str, name: str) -> None:
    assert parse_nomination(text) == name


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("Yes, please do.", True),
        ("Sure.", True),
        ("No thanks.", False),
        ("Please don't.", False),
        ("Nice weather.", None),
    ],
)
def test_consent(text: str, value: bool | None) -> None:
    assert parse_consent(text) is value


def test_question_and_vocative() -> None:
    assert looks_like_question("What time is it", "en")
    assert looks_like_question("Can you recommend a movie", "en")
    assert looks_like_question("Tell me a joke.", "en")
    assert not looks_like_question("I think we should leave at six.", "en")
    assert vocative_name("Maria, can you pass the salt?", "en") == "Maria"
    assert vocative_name("Today we start early.", "en") is None


def test_analyze_english() -> None:
    claims = analyze("Hi Lena, I'm Alex. Sam, go ahead.", assistant_name="Lena", language="en")
    assert claims.self_intro == "Alex"
    assert claims.nomination == "Sam"
    assert claims.addressed_assistant
