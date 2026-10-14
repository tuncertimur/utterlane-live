"""End-to-end conversation scenarios with mock engines (no models, no network).

Audio is synthetic: each "person" is a tone with a distinct amplitude, which the
MockEmbedder maps to a distinct voice vector; transcripts come from ScriptedStt in order.
These tests prove the conversation logic, not acoustic accuracy (see tests/live/private).
"""

from __future__ import annotations

import asyncio
import struct
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from utterlane_live.audio import EnergyVad
from utterlane_live.config import LiveConfig
from utterlane_live.embedding import MockEmbedder
from utterlane_live.llm import LlmError, Message, MockLlm, SpendLedger, Usage
from utterlane_live.profiles import FileKeyProvider, ProfileStore
from utterlane_live.session import Engines, LiveSession, Transport
from utterlane_live.speakers import SpeakerTracker
from utterlane_live.stt import ScriptedStt
from utterlane_live.tts import MockTts

TUNCER, ZUMRA, GUEST, AHMET = 0.12, 0.22, 0.32, 0.42
SR = 16_000


def speech(amplitude: float, seconds: float = 1.6, silence: float = 1.0) -> bytes:
    t = np.arange(int(seconds * SR)) / SR
    voiced = amplitude * np.sin(2 * np.pi * 180 * t)
    audio = np.concatenate([voiced, np.zeros(int(silence * SR))])
    return (audio * 32767).astype("<i2").tobytes()


class Harness:
    def __init__(
        self, tmp: Path, *, llm: Any = None, mode: str = "smart", store: bool = True
    ) -> None:
        self.cfg = LiveConfig()
        self.cfg.mode = mode  # type: ignore[assignment]
        self.stt = ScriptedStt()
        self.llm = llm or MockLlm()
        self.embedder = MockEmbedder()
        self.tmp = tmp
        self.store = (
            ProfileStore(
                tmp / "profiles.bin", FileKeyProvider(tmp / "k.key"), MockEmbedder.fingerprint
            )
            if store
            else None
        )
        self.ledger = SpendLedger(tmp / "usage.json", 2.0)
        self.messages: list[dict[str, Any]] = []
        self.audio: list[bytes] = []
        profiles = self.store.load().profiles if self.store else []
        self.session = LiveSession(
            self.cfg,
            Engines(
                vad_factory=lambda: EnergyVad(0.02),
                stt=self.stt,
                embedder=self.embedder,
                llm=self.llm,
                tts=MockTts(),
            ),
            SpeakerTracker(self.cfg.speakers, profiles),
            self.store,
            self.ledger,
        )

    async def __aenter__(self) -> Harness:
        async def send_json(m: dict[str, Any]) -> None:
            self.messages.append(m)

        async def send_bytes(b: bytes) -> None:
            self.audio.append(b)

        self.transport = Transport(send_json, send_bytes)
        self.session.start()
        await self.session.attach(self.transport)
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.session.close()

    async def say(self, amplitude: float, text: str, seconds: float = 1.6) -> None:
        self.stt.push(text)
        data = speech(amplitude, seconds)
        for i in range(0, len(data), 640):
            await self.session.on_audio(data[i : i + 640])
        await self.settle()

    async def settle(self, timeout: float = 30.0) -> None:
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while loop.time() < end:
            await asyncio.sleep(0.01)
            if self.session.idle:
                await asyncio.sleep(0.02)
                if self.session.idle:
                    return
        raise AssertionError("session did not settle")

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [m for m in self.messages if m["type"] == kind]

    def last_turn(self) -> dict[str, Any]:
        return self.of("turn")[-1]["turn"]

    def label(self, turn_id: str) -> str:
        label = next(
            m["turn"]["speaker"]["label"]
            for m in self.messages
            if m["type"] == "turn" and m["turn"]["id"] == turn_id
        )
        for m in self.messages:
            if m["type"] == "turn_update" and m["turn_id"] == turn_id:
                label = m["speaker"]["label"]
        return label

    def replies(self) -> list[str]:
        return [m["text"] for m in self.of("assistant_end")]


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_intro_consent_and_random_talk(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with Harness(tmp_path) as h:
            await h.say(TUNCER, "Merhaba, ben Tuncer.")
            first = h.last_turn()
            assert h.label(first["id"]) == "Tuncer"
            assert "self_intro" in first["flags"]
            assert "ister misin" in h.replies()[-1]
            kinds = [m["kind"] for m in h.of("identity_event")]
            assert kinds[:2] == ["new_speaker", "named"]

            await h.say(TUNCER, "Evet.")
            assert any(m["kind"] == "profile_saved" for m in h.of("identity_event"))
            assert "şifreli" in h.replies()[-1]
            assert len(h.llm.calls) == 1  # "Evet." was answered without a model call

            await h.say(TUNCER, "Bugün hava çok güzel, ne yapsak?")
            assert h.label(h.last_turn()["id"]) == "Tuncer"
            assert h.replies()[-1].startswith("Anladım Tuncer")
            roster = h.of("speakers")[-1]["speakers"]
            assert roster[0]["label"] == "Tuncer" and roster[0]["persistent"] is True
            assert h.audio and h.audio[0][:4] == b"UTA1"
            seq, chunk, rate = struct.unpack("<III", h.audio[0][4:16])
            assert rate == 24_000 and chunk == 0 and seq >= 1

    run(scenario())


def test_consent_needs_a_clear_answer_right_after_the_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def saved(h: Harness) -> bool:
        return any(m["kind"] == "profile_saved" for m in h.of("identity_event"))

    async def scenario() -> None:
        for name in "abcd":
            (tmp_path / name).mkdir()
        async with Harness(tmp_path / "a") as h:
            await h.say(ZUMRA, "Merhaba, ben Zümra.")
            assert "ister misin" in h.replies()[-1]
            await h.say(ZUMRA, "Ben de hazırım, başlayalım.")  # no answer: question dropped
            await h.say(ZUMRA, "Kesinlikle katılıyorum.")  # agreeing with someone, not consent
            assert not saved(h)
        async with Harness(tmp_path / "b") as h:
            await h.say(ZUMRA, "Merhaba, ben Zümra.")
            await h.say(ZUMRA, "Tamam, bence önce demo videosunu çekip sonra yayınlayalım.")
            assert not saved(h)  # a long sentence that merely contains "tamam"
        async with Harness(tmp_path / "c") as h:
            await h.say(ZUMRA, "Merhaba, ben Zümra.")
            await h.say(ZUMRA, "Evet, sesimi bir dahaki sefere de hatırlamanı isterim.")
            assert saved(h)  # long, but explicitly about remembering
        monkeypatch.setattr("utterlane_live.session.CONSENT_WINDOW_S", 0.0)
        async with Harness(tmp_path / "d") as h:
            await h.say(ZUMRA, "Merhaba, ben Zümra.")
            await h.say(ZUMRA, "Evet.")
            assert not saved(h)  # too late

    run(scenario())


def test_stored_profile_recognized_next_session(tmp_path: Path) -> None:
    async def first() -> None:
        async with Harness(tmp_path) as h:
            await h.say(ZUMRA, "Benim adım Zümra.")
            await h.say(ZUMRA, "Evet, hatırla.")

    async def second() -> None:
        async with Harness(tmp_path) as h:
            await h.say(ZUMRA, "Günaydın, kahve hazır mı?")
            assert h.label(h.last_turn()["id"]) == "Zümra"
            assert any(m["kind"] == "profile_recognized" for m in h.of("identity_event"))
            assert "ister misin" not in h.replies()[-1]

    run(first())
    run(second())


def test_three_people_smart_mode_and_unknown_third(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with Harness(tmp_path, store=False) as h:
            await h.say(TUNCER, "Ben Tuncer.")
            await h.say(ZUMRA, "Ben de Zümra.")
            await h.say(GUEST, "Merhaba herkese, bugün çok kalabalığız")
            guest_turn = h.last_turn()
            assert h.label(guest_turn["id"]) == "3. kişi"
            assert h.of("assistant_silent")[-1]["in_reply_to"] == guest_turn["id"]

            await h.say(ZUMRA, "Lena, bugün ne yapalım?")
            assert h.label(h.last_turn()["id"]) == "Zümra"
            assert h.replies()[-1].startswith("Anladım Zümra")
            history = h.llm.calls[-1][1]
            user_lines = [m.content for m in history if m.role == "user"]
            assert "[Konuşan: 3. kişi] Merhaba herkese, bugün çok kalabalığız" in user_lines
            assert "Odadakiler: Tuncer, Zümra, 3. kişi" in history[-1].content

    run(scenario())


def test_nomination_then_retroactive_confirmation(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with Harness(tmp_path, store=False) as h:
            await h.say(TUNCER, "Ben Tuncer.")
            await h.say(ZUMRA, "Ben Zümra.")
            calls_before = len(h.llm.calls)
            await h.say(TUNCER, "Ahmet sen konuş.")
            assert "nomination" in h.last_turn()["flags"]
            assert len(h.llm.calls) == calls_before  # handing the floor to a human: no model call

            await h.say(AHMET, "Teşekkürler, ben bütçeyi anlatayım")
            first = h.last_turn()["id"]
            assert h.label(first) == "Ahmet?"
            await h.say(AHMET, "Geçen ay harcamalar arttı")
            await h.say(AHMET, "Bu ay daha dikkatli olacağız")
            assert h.label(first) == "Ahmet"  # earlier turn relabelled by turn_update
            assert any(m["kind"] == "confirmed" for m in h.of("identity_event"))

    run(scenario())


def test_correction_by_voice(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with Harness(tmp_path, store=False) as h:
            await h.say(TUNCER, "Ben Tuncer.")
            await h.say(TUNCER, "Ahmet sen konuş.")
            await h.say(GUEST, "Merhaba arkadaşlar")
            wrong = h.last_turn()["id"]
            assert h.label(wrong) == "Ahmet?"
            await h.say(GUEST, "Ben Ahmet değilim, Mehmet'im.")
            assert h.label(wrong) == "Mehmet"
            assert "correction" in h.last_turn()["flags"]

    run(scenario())


def test_assistant_own_voice_is_ignored_only_while_it_speaks(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with Harness(tmp_path, store=False) as h:
            for _ in range(3):
                await h.say(TUNCER, "Bana uzun bir cevap ver lütfen.")
            assert h.session.tracker.assistant_vectors, (
                "assistant voice should be learned from TTS output"
            )
            # MockTts peaks at ~0.09 -> the mock embedder maps it to the assistant's voice.
            turns_before = len(h.of("turn"))
            await h.session.on_control({"type": "playback", "state": "started", "reply_id": "r1"})
            await h.say(0.09, "Anladım Tuncer. Bu yerel deneme yanıtıdır.", seconds=2.0)
            await h.session.on_control({"type": "playback", "state": "ended", "reply_id": "r1"})
            assert len(h.of("turn")) == turns_before  # our own voice from the speakers

            await asyncio.sleep(1.3)  # past the echo tail
            await h.say(0.09, "Merhaba, ben Selin.", seconds=2.0)
            # Someone who merely sounds like the assistant, while it is silent, is a person.
            assert len(h.of("turn")) == turns_before + 1
            assert h.label(h.last_turn()["id"]) == "Selin"

    run(scenario())


def test_barge_in_stops_playback(tmp_path: Path) -> None:
    async def scenario() -> None:
        slow = MockLlm(lambda i, m: " ".join(["kelime"] * 40) + ".", delay_s=0.02)
        async with Harness(tmp_path, llm=slow, store=False) as h:
            h.stt.push("Ben Tuncer, bir hikaye anlat.")
            data = speech(TUNCER)
            for i in range(0, len(data), 640):
                await h.session.on_audio(data[i : i + 640])
            await asyncio.sleep(0.15)
            start = h.of("assistant_start")[-1]
            await h.session.on_control(
                {"type": "playback", "state": "started", "reply_id": start["reply_id"]}
            )
            h.stt.push("Dur, yeter.")
            interrupt = speech(TUNCER, seconds=1.0)
            for i in range(0, len(interrupt), 640):
                await h.session.on_audio(interrupt[i : i + 640])
            await h.settle()
            stops = h.of("stop_playback")
            assert (
                stops
                and stops[0]["reason"] == "barge_in"
                and stops[0]["reply_id"] == start["reply_id"]
            )
            ended = [m for m in h.of("assistant_end") if m["reply_id"] == start["reply_id"]]
            assert ended and ended[0]["interrupted"] is True
            assert h.last_turn()["text"] == "Dur, yeter."
            assert "barge_in" in h.last_turn()["flags"]

    run(scenario())


def test_provider_failure_is_spoken_once_and_shown(tmp_path: Path) -> None:
    class Failing:
        model_id = "fail"
        mode = "live"

        async def stream(self, instructions: str, messages: list[Message], usage_out: Usage):  # type: ignore[no-untyped-def]
            raise LlmError("connection_failed", "Dil modeline ulaşılamadı: connection_failed.")
            yield ""  # pragma: no cover

    async def scenario() -> None:
        async with Harness(tmp_path, llm=Failing(), store=False) as h:
            await h.say(TUNCER, "Merhaba.")
            await h.say(TUNCER, "Duyuyor musun?")
            notices = [m for m in h.of("notice") if m["code"] == "connection_failed"]
            assert len(notices) == 2
            spoken = [r for r in h.replies() if "ulaşamıyorum" in r]
            assert len(spoken) == 1

    run(scenario())


def test_budget_cap_blocks_live_calls(tmp_path: Path) -> None:
    from utterlane_live.llm import OpenAILlm

    async def scenario() -> None:
        ledger = SpendLedger(tmp_path / "u.json", 0.0)
        llm = OpenAILlm(api_key="test-key-not-real", ledger=ledger)
        async with Harness(tmp_path, llm=llm, store=False) as h:
            await h.say(TUNCER, "Merhaba.")
            assert h.of("notice")[-1]["code"] == "budget_exhausted"
            assert "bütçem doldu" in h.replies()[-1]

    run(scenario())


def test_hallucinated_transcript_is_dropped(tmp_path: Path) -> None:
    from utterlane_live.stt import SttResult

    async def scenario() -> None:
        async with Harness(tmp_path, store=False) as h:
            h.stt.transcribe = lambda audio, hotwords=(): SttResult(
                "Altyazı M.K.", 5, rejected="hallucination_pattern"
            )  # type: ignore[method-assign]
            await h.say(TUNCER, "unused")
            assert h.of("turn") == []
            assert h.llm.calls == []

    run(scenario())


def test_forget_speaker_deletes_profile(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with Harness(tmp_path) as h:
            await h.say(TUNCER, "Ben Tuncer.")
            await h.say(TUNCER, "Evet.")
            assert len(h.store.list_public()) == 1  # type: ignore[union-attr]
            speaker_id = h.last_turn()["speaker"]["id"]
            await h.session.on_control({"type": "forget_speaker", "speaker_id": speaker_id})
            assert h.store.list_public() == []  # type: ignore[union-attr]
            assert h.of("identity_event")[-1]["kind"] == "forgotten"

    run(scenario())


@pytest.mark.parametrize("mode", ["wake"])
def test_wake_mode_requires_name(tmp_path: Path, mode: str) -> None:
    async def scenario() -> None:
        async with Harness(tmp_path, mode=mode, store=False) as h:
            await h.say(TUNCER, "Bugün hava güzel.")
            assert h.of("assistant_silent")[-1]["reason"] == "wake_word_missing"
            await h.say(TUNCER, "Lena, bugün hava nasıl?")
            assert h.replies()

    run(scenario())


def test_truncated_silence_marker_counts_as_silence(tmp_path: Path) -> None:
    async def scenario() -> None:
        llm = MockLlm(
            lambda instructions, messages: "<sessiz" if "<sessiz>" in instructions else "Tamam."
        )
        async with Harness(tmp_path, llm=llm, store=False) as h:
            await h.say(TUNCER, "Ben Tuncer.")
            await h.say(ZUMRA, "Ben Zümra.")
            await h.say(TUNCER, "Yarın sinemaya gidelim mi")
            assert h.of("assistant_silent")[-1]["in_reply_to"] == h.last_turn()["id"]
            assert not any("sessiz" in r for r in h.replies())

    run(scenario())


def test_nominee_answer_is_not_interrupted_by_assistant(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with Harness(tmp_path, store=False) as h:
            await h.say(TUNCER, "Ben Tuncer.")
            await h.say(ZUMRA, "Ben Zümra.")
            await h.say(TUNCER, "Ahmet sen konuş.")
            calls = len(h.llm.calls)
            await h.say(AHMET, "Bence bu proje çok iyi gidiyor, devam edelim mi?")
            assert len(h.llm.calls) == calls
            assert h.of("assistant_silent")[-1]["in_reply_to"] == h.last_turn()["id"]
            await h.say(AHMET, "Lena, sen ne dersin?")
            assert len(h.llm.calls) == calls + 1

    run(scenario())
