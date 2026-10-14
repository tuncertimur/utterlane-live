from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np

from utterlane_live.embedding import MockEmbedder
from utterlane_live.segmentation import speaker_change_points, split_words

SR = 16_000


def tone(amplitude: float, seconds: float) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return (amplitude * np.sin(2 * np.pi * 180 * t)).astype(np.float32)


def test_change_found_between_two_voices() -> None:
    audio = np.concatenate([tone(0.12, 2.0), tone(0.42, 2.0)])
    cuts = speaker_change_points(audio, MockEmbedder().embed)
    assert len(cuts) == 1
    assert abs(cuts[0] - 2 * SR) <= int(0.6 * SR)


def test_no_change_for_one_voice() -> None:
    audio = tone(0.12, 5.0)
    assert speaker_change_points(audio, MockEmbedder().embed) == []


def test_three_voices_two_changes() -> None:
    audio = np.concatenate([tone(0.12, 2.0), tone(0.42, 2.0), tone(0.22, 2.0)])
    cuts = speaker_change_points(audio, MockEmbedder().embed)
    assert len(cuts) == 2


def test_split_words_by_cut() -> None:
    words = (
        (0.1, 0.4, "Ahmet"),
        (0.4, 0.7, " sen"),
        (0.7, 1.0, " konuş."),
        (2.2, 2.6, " Tamam"),
        (2.6, 3.0, " başlıyorum."),
    )
    parts = split_words(words, [int(1.5 * SR)], 3 * SR)
    assert [p[2] for p in parts] == ["Ahmet sen konuş.", "Tamam başlıyorum."]
    assert parts[0][1] == parts[1][0] == int(1.5 * SR)


def test_split_words_merges_empty_piece() -> None:
    words = ((0.1, 0.4, "Merhaba"), (0.4, 0.8, " dünya"))
    parts = split_words(words, [int(2.0 * SR)], 3 * SR)
    assert parts == [(0, 3 * SR, "Merhaba dünya")]


def test_fast_handoff_becomes_two_turns(tmp_path: Path) -> None:
    from tests.test_session import AHMET, TUNCER, Harness

    async def scenario() -> None:
        async with Harness(tmp_path, store=False) as h:
            await h.say(TUNCER, "Ben Tuncer.")
            h.stt.push("Ahmet sen konuş. Salata ben yaparım.")
            gap = np.zeros(
                int(0.15 * SR), np.float32
            )  # a natural hand-off pause, below the 650 ms endpoint
            audio = np.concatenate(
                [tone(TUNCER, 2.0), gap, tone(AHMET, 2.0), np.zeros(SR, np.float32)]
            )
            pcm = (audio * 32767).astype("<i2").tobytes()
            for i in range(0, len(pcm), 640):
                await h.session.on_audio(pcm[i : i + 640])
            await h.settle()
            turns = [m["turn"] for m in h.of("turn")]
            assert len(turns) == 3
            assert turns[1]["text"] == "Ahmet sen konuş."
            assert h.label(turns[1]["id"]) == "Tuncer"
            assert "nomination" in turns[1]["flags"] and "split" in turns[1]["flags"]
            assert turns[2]["text"] == "Salata ben yaparım."
            assert h.label(turns[2]["id"]) == "Ahmet?"
            # Ahmet is answering Tuncer, not the assistant.
            assert h.of("assistant_silent")[-1]["in_reply_to"] == turns[2]["id"]

    asyncio.run(scenario())
