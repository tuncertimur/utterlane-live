"""The conversation brain: audio in → turns, speaker labels, replies and speech out.

One ``LiveSession`` owns the conversation state. A WebSocket transport can attach and
detach (reconnects keep the conversation). All heavy work (STT on the GPU, speaker
vectors on the CPU, TTS in a worker process) runs off the event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import itertools
import re
import struct
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

from utterlane_live.audio import Endpointer, EndpointerConfig, Float32, Utterance, VadModel
from utterlane_live.config import LiveConfig
from utterlane_live.embedding import Embedder
from utterlane_live.i18n import instructions as i18n_instructions
from utterlane_live.i18n import t
from utterlane_live.llm import (
    SILENT_TOKENS,
    LlmClient,
    LlmError,
    Message,
    SpendLedger,
    Usage,
    silent_token,
)
from utterlane_live.naming import analyze, looks_like_question, tr_lower, vocative_name
from utterlane_live.profiles import ProfileStore
from utterlane_live.segmentation import SPLIT_MIN_S, speaker_change_points, split_words
from utterlane_live.speakers import UNKNOWN_ID, IdentityEvent, SpeakerTracker
from utterlane_live.stt import SttEngine, SttResult
from utterlane_live.tts import (
    SentenceChunker,
    TtsEngine,
    normalize_for_speech,
    pcm16_bytes,
    resample_to_16k,
)

SendJson = Callable[[dict[str, Any]], Awaitable[None]]
SendBytes = Callable[[bytes], Awaitable[None]]

AUDIO_MAGIC = b"UTA1"
ECHO_TAIL_S = 1.2
MAX_TURNS = 300
LEVEL_INTERVAL_S = 0.1
BARGE_CHECK_MS = 600


@dataclass(slots=True)
class Transport:
    send_json: SendJson
    send_bytes: SendBytes


@dataclass(slots=True)
class Engines:
    vad_factory: Callable[[], VadModel]
    stt: SttEngine
    embedder: Embedder
    llm: LlmClient
    tts: TtsEngine
    status: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass(slots=True)
class Turn:
    id: str
    speaker_id: str
    text: str
    started_at: float
    ended_at: float
    duration_s: float
    stt_ms: int
    state: str
    score: float | None
    margin: float | None
    flags: list[str]
    vector: Float32 | None = None

    def public(self, speaker: dict[str, object]) -> dict[str, object]:
        return {
            "id": self.id,
            "speaker": speaker,
            "text": self.text,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_s": round(self.duration_s, 2),
            "stt_ms": self.stt_ms,
            "attribution": {"state": self.state, "score": self.score, "margin": self.margin},
            "flags": self.flags,
        }


@dataclass(slots=True)
class Reply:
    id: str
    seq: int
    in_reply_to: str | None
    text: str = ""
    interrupted: bool = False
    at: float = field(default_factory=time.time)
    turn_end: float | None = None
    first_text_at: float | None = None
    first_audio_at: float | None = None
    llm_started_at: float | None = None
    pipeline_ready_at: float | None = None

    def timing(self) -> dict[str, float]:
        if self.turn_end is None:
            return {}
        out: dict[str, float] = {}
        if self.pipeline_ready_at:
            out["listen_pipeline_s"] = round(self.pipeline_ready_at - self.turn_end, 3)
        if self.llm_started_at and self.first_text_at:
            out["llm_first_token_s"] = round(self.first_text_at - self.llm_started_at, 3)
        if self.first_text_at:
            out["first_text_s"] = round(self.first_text_at - self.turn_end, 3)
        if self.first_audio_at:
            out["first_audio_s"] = round(self.first_audio_at - self.turn_end, 3)
        return out


# A voice print is stored only on a clear answer: the speaker's very next turn, soon after the
# question, and short or explicitly about remembering. "Kesinlikle katılıyorum" said later in
# the conversation is agreement with someone, not consent.
CONSENT_WINDOW_S = 30.0
CONSENT_MAX_WORDS = 6
_ABOUT_MEMORY = re.compile(r"\b(?:hatırla|tanı|kaydet|sakla|remember|recogni[sz]e|save|store)")


@dataclass(slots=True)
class ConsentAsk:
    speaker_id: str
    name: str
    asked_at: float = field(default_factory=time.monotonic)


class LiveSession:
    def __init__(
        self,
        config: LiveConfig,
        engines: Engines,
        tracker: SpeakerTracker,
        profile_store: ProfileStore | None,
        ledger: SpendLedger,
    ) -> None:
        self.cfg = config
        self.engines = engines
        self.tracker = tracker
        self.store = profile_store
        self.ledger = ledger
        self.session_id = uuid.uuid4().hex[:12]
        self.lang = config.language
        self.mode = config.mode
        self.listening = True
        self.transport: Transport | None = None
        self.endpointer = self._new_endpointer()
        self.turns: deque[Turn] = deque(maxlen=MAX_TURNS)
        self.replies: deque[Reply] = deque(maxlen=MAX_TURNS)
        self._turn_ids = itertools.count(1)
        self._reply_seq = itertools.count(1)
        self._queue: asyncio.Queue[Utterance] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._reply_task: asyncio.Task[None] | None = None
        self._current_reply: Reply | None = None
        self._audible_reply: str | None = None
        self._audible_until = 0.0
        self._speech_started_at = 0.0
        self._barge_checked = False
        self._segment_is_echo = False
        self._last_level_sent = 0.0
        self._status = "loading"
        self._consent: dict[str, ConsentAsk] = {}
        self._assistant_voice_samples = 0
        self._persist_counter: dict[str, int] = {}
        self._stt_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stt")
        self._emb_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="emb")
        self._cut_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cut")
        self._last_error_code: str | None = None
        self._background: set[asyncio.Task[None]] = set()
        self._processing = 0
        self._deferred: list[str] = []
        self._turn_event = asyncio.Event()

    # ----------------------------------------------------------------- plumbing
    def _new_endpointer(self) -> Endpointer:
        c = self.cfg
        return Endpointer(
            self.engines.vad_factory(),
            EndpointerConfig(
                start_probability=c.vad_start_probability,
                end_probability=c.vad_end_probability,
                end_silence_ms=c.end_silence_ms,
                preroll_ms=c.preroll_ms,
                min_speech_ms=c.min_speech_ms,
                max_utterance_s=c.max_utterance_s,
            ),
        )

    async def _json(self, message: dict[str, Any]) -> None:
        transport = self.transport
        if transport is None:
            return
        with contextlib.suppress(Exception):
            await transport.send_json(message)

    async def _bytes(self, data: bytes) -> None:
        transport = self.transport
        if transport is None:
            return
        with contextlib.suppress(Exception):
            await transport.send_bytes(data)

    async def _set_status(self, state: str, detail: str | None = None) -> None:
        if state == self._status and detail is None:
            return
        self._status = state
        message: dict[str, Any] = {"type": "status", "state": state}
        if detail:
            message["detail"] = detail
        await self._json(message)

    async def _notice(self, level: str, code: str, message: str) -> None:
        await self._json({"type": "notice", "level": level, "code": code, "message": message})

    def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._utterance_worker(), name="utterance-worker")

    async def close(self) -> None:
        await self._cancel_reply("reset")
        if self._worker:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None
        self._stt_pool.shutdown(wait=False, cancel_futures=True)
        self._emb_pool.shutdown(wait=False, cancel_futures=True)
        self._cut_pool.shutdown(wait=False, cancel_futures=True)

    async def attach(self, transport: Transport) -> None:
        self.transport = transport
        self.endpointer = self._new_endpointer()
        self._audible_reply = None
        await self._json(
            {
                "type": "ready",
                "session_id": self.session_id,
                "assistant_name": self.cfg.assistant_name,
                "language": self.lang,
                "mode": self.mode,
                "listening": self.listening,
                "models": self.engines.status,
                "budget": {
                    "spent_usd": round(self.ledger.spent_usd, 6),
                    "cap_usd": self.ledger.cap_usd,
                },
            }
        )
        await self._send_roster()
        if self.turns:
            await self._json(
                {
                    "type": "history",
                    "turns": [self._public_turn(t) for t in self.turns],
                    "replies": [self._public_reply(r) for r in self.replies],
                }
            )
        self._status = ""
        await self._set_status("listening" if self.listening else "paused")
        await self._json({"type": "usage", **self.ledger.public()})

    async def detach(self, transport: Transport) -> None:
        if self.transport is transport:
            self.transport = None
            await self._cancel_reply("reset")

    # -------------------------------------------------------------- client input
    async def on_control(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "playback":
            await self._on_playback(str(message.get("state")), str(message.get("reply_id", "")))
        elif kind == "set_mode" and message.get("mode") in ("smart", "always", "wake"):
            self.mode = message["mode"]
            await self._notice(
                "info", "mode_changed", t(self.lang, "mode_changed", mode=self._mode_label())
            )
        elif kind == "listen":
            self.listening = bool(message.get("enabled", True))
            if not self.listening:
                self.endpointer = self._new_endpointer()
            await self._set_status("listening" if self.listening else "paused")
        elif kind == "rename_speaker":
            before = self._labels()
            name = str(message.get("name") or "").strip()[:40] or None
            events = self.tracker.rename(str(message.get("speaker_id")), name)
            await self._publish_identity(events, before)
        elif kind == "forget_speaker":
            await self._forget(str(message.get("speaker_id")))
        elif kind == "reset_session":
            await self.reset()
        elif kind == "ping":
            await self._json({"type": "pong", "t": message.get("t")})

    def _mode_label(self) -> str:
        return t(self.lang, f"mode_{self.mode}")

    async def on_audio(self, data: bytes) -> None:
        if not self.listening or len(data) % 2:
            return
        events = self.endpointer.push(data)
        now = time.monotonic()
        if now - self._last_level_sent >= LEVEL_INTERVAL_S:
            self._last_level_sent = now
            await self._json(
                {
                    "type": "level",
                    "rms": round(min(1.0, self.endpointer.level * 4), 3),
                    "speech": self.endpointer.active,
                }
            )
        for event in events:
            if event.kind == "speech_start":
                self._speech_started_at = time.time()
                self._barge_checked = False
                self._segment_is_echo = False
                if not self._assistant_audible():
                    await self._set_status("hearing")
            elif event.kind == "speech_cancel":
                if self._status == "hearing":
                    await self._set_status("listening")
            elif event.kind == "utterance" and event.utterance is not None:
                utterance = event.utterance
                utterance.started_at = self._speech_started_at or time.time() - utterance.duration_s
                utterance.ended_at = time.time()
                if self._segment_is_echo:
                    continue
                self._queue.put_nowait(utterance)
        if (
            self.endpointer.active
            and not self._barge_checked
            and self._assistant_audible()
            and self.endpointer.current_speech_ms >= max(BARGE_CHECK_MS, self.cfg.barge_in_ms)
        ):
            self._barge_checked = True
            task = asyncio.create_task(self._check_barge_in(self.endpointer.current_audio()))
            self._background.add(task)
            task.add_done_callback(self._background.discard)

    @property
    def idle(self) -> bool:
        task = self._reply_task
        return self._queue.empty() and self._processing == 0 and (task is None or task.done())

    def _assistant_audible(self) -> bool:
        return self._audible_reply is not None or time.time() < self._audible_until

    async def _on_playback(self, state: str, reply_id: str) -> None:
        if state == "started":
            self._audible_reply = reply_id
            await self._set_status("speaking")
        elif state in ("ended", "stopped"):
            if self._audible_reply == reply_id or not reply_id:
                self._audible_reply = None
                self._audible_until = time.time() + ECHO_TAIL_S
            current = self._current_reply
            if state == "stopped" and current is not None and current.id == reply_id:
                # The user pressed Stop (or the client cannot play): stop producing speech.
                await self._cancel_reply("user_stop")
            if self._reply_task is None or self._reply_task.done():
                await self._set_status("listening" if self.listening else "paused")

    async def _check_barge_in(self, audio: Float32) -> None:
        loop = asyncio.get_running_loop()
        try:
            vector = await loop.run_in_executor(self._emb_pool, self.engines.embedder.embed, audio)
        except Exception:
            vector = None
        if vector is not None and self.tracker.is_assistant_voice(vector):
            self._segment_is_echo = True
            return
        await self._barge_in()

    async def _barge_in(self) -> None:
        reply_id = self._audible_reply or (self._current_reply.id if self._current_reply else None)
        if reply_id is None:
            return
        await self._json({"type": "stop_playback", "reply_id": reply_id, "reason": "barge_in"})
        self._audible_reply = None
        self._audible_until = time.time() + 0.4
        await self._cancel_reply("barge_in")
        await self._set_status("hearing")

    async def _cancel_reply(self, reason: str) -> None:
        task = self._reply_task
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._reply_task = None

    async def reset(self) -> None:
        await self._cancel_reply("reset")
        if self._audible_reply:
            await self._json(
                {"type": "stop_playback", "reply_id": self._audible_reply, "reason": "reset"}
            )
        self._audible_reply = None
        self.turns.clear()
        self.replies.clear()
        self._consent.clear()
        self.tracker.reset()
        self.endpointer = self._new_endpointer()
        await self._json({"type": "history", "turns": [], "replies": []})
        await self._send_roster()
        await self._notice("info", "session_reset", t(self.lang, "session_reset"))
        await self._set_status("listening" if self.listening else "paused")

    async def _forget(self, speaker_id: str) -> None:
        speaker = self.tracker.forget(speaker_id)
        if speaker is None:
            return
        if speaker.profile_id and self.store is not None:
            await asyncio.to_thread(self.store.delete, speaker.profile_id)
        for turn in self.turns:
            if turn.speaker_id == speaker_id:
                turn.speaker_id = UNKNOWN_ID
                turn.vector = None
                await self._json(
                    {
                        "type": "turn_update",
                        "turn_id": turn.id,
                        "speaker": self.tracker.public(UNKNOWN_ID),
                    }
                )
        await self._json(
            {
                "type": "identity_event",
                "kind": "forgotten",
                "speaker_id": speaker_id,
                "name": speaker.name,
                "message": f"{speaker.label} unutuldu; ses izi silindi.",
            }
        )
        await self._send_roster()

    # ------------------------------------------------------------- turn pipeline
    async def _utterance_worker(self) -> None:
        while True:
            utterance = await self._queue.get()
            self._processing += 1
            try:
                await self._process(utterance)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # keep listening whatever happens
                await self._notice(
                    "error",
                    "turn_failed",
                    t(self.lang, "turn_failed", error=type(exc).__name__),
                )
            finally:
                self._processing -= 1

    def _hotwords(self) -> tuple[str, ...]:
        names = {self.cfg.assistant_name, "Utterlane"}
        names |= {s.name for s in self.tracker.speakers.values() if s.name}
        names |= {p.name for p in self.tracker.profiles.values()}
        return tuple(sorted(names))

    def _labels(self) -> dict[str, str]:
        return {sid: str(self.tracker.public(sid)["label"]) for sid in self.tracker.speakers}

    @staticmethod
    def _public_reply(reply: Reply) -> dict[str, object]:
        return {
            "reply_id": reply.id,
            "in_reply_to": reply.in_reply_to,
            "text": reply.text.strip(),
            "interrupted": reply.interrupted,
            "at": reply.at,
        }

    # ------------------------------------------------------------ integration API
    def public_turns(self, *, limit: int = 20, after: str | None = None) -> list[dict[str, object]]:
        """Recent turns (oldest first), optionally only those after ``after`` (a turn id)."""

        turns = list(self.turns)
        if after:
            ids = [turn.id for turn in turns]
            turns = turns[ids.index(after) + 1 :] if after in ids else turns
        return [self._public_turn(turn) for turn in turns[-max(1, min(limit, 200)) :]]

    async def wait_for_turn(
        self, *, after: str | None, timeout_s: float, speaker: str | None = None
    ) -> dict[str, object] | None:
        """Long-poll: return the first turn after ``after`` (matching ``speaker`` if given)."""

        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, min(timeout_s, 300.0))
        cursor = after if after is not None else (self.turns[-1].id if self.turns else None)
        while True:
            for item in self.public_turns(limit=200, after=cursor):
                cursor = str(item["id"])
                label = str(item["speaker"]["label"])  # type: ignore[index]
                sid = str(item["speaker"]["id"])  # type: ignore[index]
                if speaker is None or speaker.lower() in (label.lower(), sid.lower()):
                    return item
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            event = self._turn_event
            try:
                await asyncio.wait_for(event.wait(), timeout=remaining)
            except TimeoutError:
                return None

    async def say(self, text: str, *, interrupt: bool = True) -> str | None:
        """Speak ``text`` in the room as the assistant (no language model involved)."""

        text = text.strip()[:2000]
        if not text:
            return None
        if not interrupt and self._reply_task is not None and not self._reply_task.done():
            await self._reply_task
        reply = await self._start_reply(None, fixed_text=text)
        return reply.id

    def state(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "status": self._status,
            "listening": self.listening,
            "mode": self.mode,
            "language": self.lang,
            "assistant_name": self.cfg.assistant_name,
            "speakers_present": self.tracker.present_count(),
            "turns": len(self.turns),
        }

    def _public_turn(self, turn: Turn) -> dict[str, object]:
        return turn.public(self.tracker.public(turn.speaker_id))

    async def _send_roster(self) -> None:
        await self._json({"type": "speakers", "speakers": self.tracker.roster()})

    async def _publish_identity(self, events: list[IdentityEvent], before: dict[str, str]) -> None:
        for event in events:
            await self._json(
                {
                    "type": "identity_event",
                    "kind": event.kind,
                    "speaker_id": event.speaker_id,
                    "name": event.name,
                    "message": event.message,
                }
            )
        after = self._labels()
        merged_into = {e.speaker_id for e in events if e.kind == "merged"}
        changed = {sid for sid, label in after.items() if before.get(sid) != label} | merged_into
        live_ids = set(after)
        for turn in self.turns:
            if turn.speaker_id not in live_ids and turn.speaker_id != UNKNOWN_ID:
                # The speaker was merged away: re-point the turn to the surviving speaker.
                target = next(iter(merged_into), UNKNOWN_ID)
                turn.speaker_id = target
                await self._json(
                    {
                        "type": "turn_update",
                        "turn_id": turn.id,
                        "speaker": self.tracker.public(target),
                    }
                )
            elif turn.speaker_id in changed:
                await self._json(
                    {
                        "type": "turn_update",
                        "turn_id": turn.id,
                        "speaker": self.tracker.public(turn.speaker_id),
                    }
                )
        if events or changed:
            await self._send_roster()

    def _is_echo_text(self, text: str, started_at: float) -> bool:
        recent = [r for r in self.replies if r.at >= started_at - 30]
        if self._current_reply:
            recent.append(self._current_reply)
        needle = tr_lower(text)
        for reply in recent:
            hay = tr_lower(reply.text)
            if not hay or len(needle) < 6:
                continue
            ratio = (
                difflib.SequenceMatcher(None, needle, hay)
                .find_longest_match(0, len(needle), 0, len(hay))
                .size
            )
            if ratio >= 0.6 * len(needle):
                return True
        return False

    async def _embed(self, audio: Float32) -> Float32 | None:
        if len(audio) / 16000 < self.cfg.speakers.min_embed_s:
            return None
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._emb_pool, self.engines.embedder.embed, audio)
        except Exception:
            return None

    async def _process(self, utterance: Utterance) -> None:
        loop = asyncio.get_running_loop()
        overlapped_playback = (
            self._assistant_audible() or utterance.started_at <= self._audible_until
        )
        # STT (GPU), the whole-turn voice vector and speaker-change search (CPU) run at once.
        stt_future = loop.run_in_executor(
            self._stt_pool, self.engines.stt.transcribe, utterance.audio, self._hotwords()
        )
        whole_future = asyncio.ensure_future(self._embed(utterance.audio))
        cuts_future: asyncio.Future[list[int]] | None = None
        if utterance.duration_s >= SPLIT_MIN_S:
            cuts_future = loop.run_in_executor(
                self._cut_pool,
                speaker_change_points,
                utterance.audio,
                getattr(self.engines.embedder, "embed_fast", self.engines.embedder.embed),
                None,
                self.engines.embedder.embed,
            )
        cuts: list[int] = []
        if cuts_future is not None:
            try:
                cuts = await cuts_future
            except Exception:
                cuts = []
        whole_vector = await whole_future
        stt: SttResult = await stt_future
        if stt.rejected:
            if self._status in ("hearing", "thinking"):
                await self._set_status("listening")
            return

        pieces: list[tuple[Float32, str, float, float]] = []
        if cuts and stt.words:
            for lo, hi, text in split_words(stt.words, cuts, len(utterance.audio)):
                pieces.append(
                    (
                        utterance.audio[lo:hi],
                        text,
                        utterance.started_at + lo / 16000,
                        utterance.started_at + hi / 16000,
                    )
                )
        if len(pieces) < 2:
            pieces = [(utterance.audio, stt.text, utterance.started_at, utterance.ended_at)]
        for index, (audio, text, started_at, ended_at) in enumerate(pieces):
            vector = whole_vector if len(pieces) == 1 else await self._embed(audio)
            await self._ingest(
                audio,
                text,
                vector,
                started_at,
                ended_at,
                stt.elapsed_ms,
                overlapped_playback,
                split=len(pieces) > 1,
                final=index == len(pieces) - 1,
            )

    async def _ingest(
        self,
        audio: Float32,
        text: str,
        vector: Float32 | None,
        started_at: float,
        ended_at: float,
        stt_ms: int,
        overlapped_playback: bool,
        *,
        split: bool,
        final: bool,
    ) -> None:
        duration_s = len(audio) / 16000
        if overlapped_playback and (
            (vector is not None and self.tracker.is_assistant_voice(vector))
            or self._is_echo_text(text, started_at)
        ):
            return  # our own voice leaking from the speakers
        # Outside playback a voice that merely sounds like the assistant is a person: keep it.

        before = self._labels()
        attribution, events = self.tracker.attribute(vector, duration_s)
        claims = analyze(
            text,
            assistant_name=self.cfg.assistant_name,
            known_names=self.tracker.known_names(),
            language=self.lang,
        )
        flags: list[str] = []
        speaker_id = attribution.speaker_id
        state = attribution.state

        if (
            claims.self_intro
            and speaker_id == UNKNOWN_ID
            and vector is not None
            and duration_s >= 1.0
        ):
            owner = self.tracker.find_by_name(claims.self_intro)
            if (
                owner is not None
                and owner.centroid is not None
                and float(vector @ owner.centroid) >= self.cfg.speakers.new_speaker
            ):
                speaker_id, state = owner.id, "known"
            else:
                opened, opened_events = self.tracker.open_speaker(vector)
                speaker_id, state = opened.id, "new"
                events += opened_events

        if speaker_id != UNKNOWN_ID:
            if claims.correction_from:
                events += self.tracker.apply_correction(
                    speaker_id, claims.correction_from, claims.self_intro
                )
                flags.append("correction")
            elif claims.self_intro:
                events += self.tracker.apply_self_intro(speaker_id, claims.self_intro)
                flags.append("self_intro")
        if claims.nomination:
            self.tracker.apply_nomination(speaker_id, claims.nomination)
            flags.append("nomination")
        if claims.addressed_assistant:
            flags.append("addressed_assistant")
        if overlapped_playback:
            flags.append("barge_in")
        if split:
            flags.append("split")

        turn = Turn(
            id=f"t-{next(self._turn_ids):06d}",
            speaker_id=speaker_id,
            text=text,
            started_at=started_at,
            ended_at=ended_at,
            duration_s=duration_s,
            stt_ms=stt_ms,
            state=state,
            score=attribution.score,
            margin=attribution.margin,
            flags=flags,
            vector=vector,
        )

        consent_reply = await self._handle_consent(turn, claims.consent)
        self.turns.append(turn)
        self._turn_event.set()
        self._turn_event = asyncio.Event()
        await self._json({"type": "turn", "turn": self._public_turn(turn)})
        await self._publish_identity(events, before)
        await self._refresh_persistent_profile(turn)

        ask_consent = self._consent_question_for(turn) if "self_intro" in flags else None
        if not final:
            # Only the last speaker of a split segment gets a reply; keep other prompts for it.
            self._deferred.extend(x for x in (consent_reply, ask_consent) if x)
            return
        if self._deferred:
            consent_reply = " ".join([*self._deferred, *([consent_reply] if consent_reply else [])])
            self._deferred.clear()
        await self._decide_and_reply(
            turn, claims.addressed_assistant, bool(claims.nomination), consent_reply, ask_consent
        )

    # ------------------------------------------------------------------ consent
    def _consent_question_for(self, turn: Turn) -> str | None:
        speaker = self.tracker.speakers.get(turn.speaker_id)
        if (
            speaker is None
            or self.store is None
            or speaker.profile_id is not None
            or speaker.consent_asked
            or speaker.name_state != "confirmed"
            or not speaker.name
        ):
            return None
        speaker.consent_asked = True
        self._consent[speaker.id] = ConsentAsk(speaker.id, speaker.name)
        return t(self.lang, "ask_consent", name=speaker.name)

    async def _handle_consent(self, turn: Turn, answer: bool | None) -> str | None:
        ask = self._consent.pop(turn.speaker_id, None)
        if ask is None:
            return None
        if not turn.text.strip():  # noise, not an answer: keep waiting
            self._consent[turn.speaker_id] = ask
            return None
        clear = len(turn.text.split()) <= CONSENT_MAX_WORDS or bool(
            _ABOUT_MEMORY.search(tr_lower(turn.text))
        )
        if answer is None or not clear or time.monotonic() - ask.asked_at > CONSENT_WINDOW_S:
            return None  # no clear answer: nothing is stored and the question is not repeated
        if not answer:
            turn.flags.append("consent_no")
            await self._json(
                {
                    "type": "identity_event",
                    "kind": "consent_declined",
                    "speaker_id": ask.speaker_id,
                    "name": ask.name,
                    "message": t(self.lang, "ev_consent_declined", name=ask.name),
                }
            )
            return t(self.lang, "consent_no", name=ask.name)
        turn.flags.append("consent_yes")
        vectors = self.tracker.session_vectors(ask.speaker_id)
        if turn.vector is not None:
            vectors.append(turn.vector)
        if not vectors or self.store is None:
            return t(self.lang, "consent_need_more", name=ask.name)
        profile = await asyncio.to_thread(self.store.create, ask.name, vectors)
        self.tracker.bind_profile(ask.speaker_id, profile)
        await self._json(
            {
                "type": "identity_event",
                "kind": "profile_saved",
                "speaker_id": ask.speaker_id,
                "name": ask.name,
                "message": t(self.lang, "ev_profile_saved", name=ask.name),
            }
        )
        await self._send_roster()
        return t(self.lang, "consent_yes", name=ask.name)

    async def _refresh_persistent_profile(self, turn: Turn) -> None:
        speaker = self.tracker.speakers.get(turn.speaker_id)
        if (
            speaker is None
            or speaker.profile_id is None
            or self.store is None
            or turn.vector is None
        ):
            return
        if turn.state != "known" or (turn.score or 0) < self.cfg.speakers.update_min_score:
            return
        count = self._persist_counter.get(speaker.id, 0) + 1
        self._persist_counter[speaker.id] = count
        if count % 5:
            return
        profile = self.tracker.profiles.get(speaker.profile_id)
        if profile is None:
            return
        profile.vectors = (profile.vectors + list(speaker.vectors)[-5:])[-30:]
        profile.updated_at = time.time()
        await asyncio.to_thread(self.store.upsert, profile)

    # ------------------------------------------------------------------ replies
    async def _decide_and_reply(
        self,
        turn: Turn,
        addressed: bool,
        nominated_human: bool,
        consent_reply: str | None,
        ask_consent: str | None,
    ) -> None:
        present = max(1, self.tracker.present_count())
        allow_silence = False
        if self.mode == "always":
            respond = True
        elif self.mode == "wake":
            respond = addressed
        else:
            speaker = self.tracker.speakers.get(turn.speaker_id)
            answering_handoff = (
                speaker is not None
                and speaker.name_source == "nomination"
                and speaker.turns_since_provisional < 2
            )
            vocative = vocative_name(turn.text, self.lang)
            to_human = vocative is not None and tr_lower(vocative) != tr_lower(
                self.cfg.assistant_name
            )
            if present <= 1 or addressed:
                respond = True
            elif nominated_human or answering_handoff or to_human:
                # Someone handed the floor to a person, that person is answering, or the
                # sentence is addressed to another human ("Zümra, sen …").
                respond = False
            elif not looks_like_question(turn.text, self.lang):
                # In a group, statements that are not questions are people talking to each
                # other; stay silent without spending a model call.
                respond = False
            else:
                respond, allow_silence = True, True
        if "self_intro" in turn.flags and self.mode != "wake":
            respond, allow_silence = True, False

        policy_blocked = respond and not self._speaker_allowed(turn)
        if policy_blocked:
            respond, allow_silence = False, False
        if getattr(self.engines.llm, "mode", "") == "off" and respond:
            respond, allow_silence = False, False
            no_brain = True
        else:
            no_brain = False
        prefix = consent_reply
        if consent_reply and (no_brain or len(turn.text.split()) <= 4):
            # A bare "evet"/"hayır" needs no language-model call.
            await self._start_reply(turn, fixed_text=consent_reply)
            return
        if not respond:
            await self._json(
                {
                    "type": "assistant_silent",
                    "in_reply_to": turn.id,
                    "reason": "speaker_not_allowed"
                    if policy_blocked
                    else "no_brain"
                    if no_brain
                    else ("wake_word_missing" if self.mode == "wake" else "not_addressed"),
                }
            )
            if ask_consent:
                await self._start_reply(turn, fixed_text=ask_consent)
            else:
                await self._set_status("listening")
            return
        if (
            allow_silence
            and not ask_consent
            and self._reply_task is not None
            and not self._reply_task.done()
        ):
            return  # someone else's answer is in progress; this side remark is kept in history only
        await self._start_reply(
            turn, prefix=prefix, suffix=ask_consent, allow_silence=allow_silence
        )

    def _speaker_allowed(self, turn: Turn) -> bool:
        """Brain trigger policy (``UTTERLANE_BRAIN_SPEAKERS``): all | named | enrolled."""

        policy = self.cfg.brain.speakers
        if policy == "all":
            return True
        speaker = self.tracker.speakers.get(turn.speaker_id)
        if speaker is None or speaker.name_state != "confirmed":
            return False
        return policy == "named" or speaker.profile_id is not None

    async def _start_reply(
        self,
        turn: Turn | None,
        *,
        fixed_text: str | None = None,
        prefix: str | None = None,
        suffix: str | None = None,
        allow_silence: bool = False,
    ) -> Reply:
        previous = self._current_reply
        await self._cancel_reply("superseded")
        if previous is not None:
            await self._json(
                {"type": "stop_playback", "reply_id": previous.id, "reason": "superseded"}
            )
        reply = Reply(
            id=f"r-{uuid.uuid4().hex[:8]}",
            seq=next(self._reply_seq),
            in_reply_to=turn.id if turn else None,
            turn_end=turn.ended_at if turn else None,
            pipeline_ready_at=time.time(),
        )
        self._reply_task = asyncio.create_task(
            self._run_reply(
                reply,
                fixed_text=fixed_text,
                prefix=prefix,
                suffix=suffix,
                allow_silence=allow_silence,
            )
        )
        return reply

    def _instructions(self, allow_silence: bool) -> str:
        return i18n_instructions(
            self.lang,
            self.cfg.assistant_name,
            allow_silence=allow_silence,
            silent_token=silent_token(self.lang),
        )

    def _messages(self, upto: Turn | None) -> list[Message]:
        items: list[tuple[float, Message]] = []
        turns = list(self.turns)[-self.cfg.history_turns :]
        replies = list(self.replies)[-self.cfg.history_turns :]
        if self.cfg.brain.history == "latest":
            # Stateful agents keep their own memory: send only what happened since our last reply.
            last_reply_at = max((r.at for r in self.replies if r.text), default=0.0)
            turns = [turn for turn in turns if turn.ended_at > last_reply_at] or turns[-1:]
            replies = []
        for turn in turns:
            label = self.tracker.public(turn.speaker_id)["label"]
            tag = t(self.lang, "speaker_tag", label=label)
            items.append((turn.ended_at, Message("user", f"{tag} {turn.text}")))
            if upto is not None and turn.id == upto.id:
                break
        for reply in replies:
            if reply.text:
                text = reply.text + (
                    t(self.lang, "interrupted_suffix") if reply.interrupted else ""
                )
                items.append((reply.at, Message("assistant", text)))
        items.sort(key=lambda item: item[0])
        roster = ", ".join(str(s["label"]) for s in self.tracker.roster() if s["present"]) or t(
            self.lang, "nobody_yet"
        )
        now = datetime.now().strftime("%d.%m.%Y %H:%M")
        note = Message("developer", t(self.lang, "room_note", roster=roster, now=now))
        return [m for _, m in items] + [note]

    async def _run_reply(
        self,
        reply: Reply,
        *,
        fixed_text: str | None,
        prefix: str | None,
        suffix: str | None,
        allow_silence: bool,
    ) -> None:
        self._current_reply = reply
        sentences: asyncio.Queue[str | None] = asyncio.Queue()
        speaker_task = asyncio.create_task(self._speak(reply, sentences))
        usage = Usage()
        started = False

        async def emit(text: str) -> None:
            nonlocal started
            if not started:
                started = True
                await self._json(
                    {
                        "type": "assistant_start",
                        "reply_id": reply.id,
                        "reply_seq": reply.seq,
                        "in_reply_to": reply.in_reply_to,
                    }
                )
            reply.text += text
            reply.first_text_at = reply.first_text_at or time.time()
            await self._json({"type": "assistant_delta", "reply_id": reply.id, "text": text})

        try:
            await self._set_status("thinking")
            await self._generate(
                reply, sentences, emit, usage, fixed_text, prefix, suffix, allow_silence
            )
            await sentences.put(None)
            await speaker_task
        except asyncio.CancelledError:
            reply.interrupted = True
            speaker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await speaker_task
            await self._finish_reply(reply, usage, started)
            raise
        await self._finish_reply(reply, usage, started)

    async def _generate(
        self,
        reply: Reply,
        sentences: asyncio.Queue[str | None],
        emit: Callable[[str], Awaitable[None]],
        usage: Usage,
        fixed_text: str | None,
        prefix: str | None,
        suffix: str | None,
        allow_silence: bool,
    ) -> None:
        if prefix:
            await emit(prefix + " ")
            await sentences.put(prefix)
        if fixed_text is not None:
            await emit(fixed_text)
            await sentences.put(fixed_text)
            return
        try:
            chunker = SentenceChunker()
            held = ""
            decided = not allow_silence
            turn = next((t for t in self.turns if t.id == reply.in_reply_to), None)
            reply.llm_started_at = time.time()
            async for delta in self.engines.llm.stream(
                self._instructions(allow_silence), self._messages(turn), usage
            ):
                if not decided:
                    held += delta
                    stripped = held.strip().lower()
                    if any(stripped.startswith(token) for token in SILENT_TOKENS):
                        await self._json(
                            {
                                "type": "assistant_silent",
                                "in_reply_to": reply.in_reply_to,
                                "reason": "not_addressed",
                            }
                        )
                        break
                    if not stripped or any(token.startswith(stripped) for token in SILENT_TOKENS):
                        continue
                    decided = True
                    delta, held = held, ""
                for token in SILENT_TOKENS:
                    delta = delta.replace(token, "")
                if not delta:
                    continue
                await emit(delta)
                for sentence in chunker.feed(delta):
                    await sentences.put(sentence)
            else:
                if not decided:
                    # Empty output or a truncated "<sessiz" marker: the model chose silence.
                    await self._json(
                        {
                            "type": "assistant_silent",
                            "in_reply_to": reply.in_reply_to,
                            "reason": "not_addressed",
                        }
                    )
            for sentence in chunker.flush():
                await sentences.put(sentence)
            self._last_error_code = None
        except LlmError as error:
            await self._notice("error", error.code, error.message)
            if self._last_error_code != error.code:
                fallback = (
                    t(self.lang, "budget_spoken")
                    if error.code == "budget_exhausted"
                    else t(self.lang, "provider_spoken")
                )
                await emit(fallback)
                await sentences.put(fallback)
            self._last_error_code = error.code
        if suffix:
            await emit((" " if reply.text else "") + suffix)
            await sentences.put(suffix)

    async def _finish_reply(self, reply: Reply, usage: Usage, started: bool) -> None:
        if self._current_reply is reply:
            self._current_reply = None
        if started:
            self.replies.append(reply)
            payload: dict[str, Any] = {
                "type": "assistant_end",
                "reply_id": reply.id,
                "text": reply.text.strip(),
                "interrupted": reply.interrupted,
            }
            timing = reply.timing()
            if timing:
                payload["timing"] = timing
            if usage.input_tokens:
                payload["usage"] = {
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cost_usd": round(usage.cost_usd, 6),
                }
            await self._json(payload)
            if usage.cost_usd:
                await self._json({"type": "usage", **self.ledger.public()})
        if not self._assistant_audible() and self._status in ("thinking", "speaking"):
            await self._set_status("listening" if self.listening else "paused")

    async def _speak(self, reply: Reply, sentences: asyncio.Queue[str | None]) -> None:
        chunk_index = 0
        loop = asyncio.get_running_loop()
        try:
            while True:
                sentence = await sentences.get()
                if sentence is None:
                    break
                text = normalize_for_speech(sentence)
                if not text:
                    continue
                blocks: list[Float32] = []
                try:
                    stream = getattr(self.engines.tts, "stream", None)
                    if stream is not None:
                        async for block in stream(text):
                            rate = self.engines.tts.sample_rate
                            header = AUDIO_MAGIC + struct.pack("<III", reply.seq, chunk_index, rate)
                            await self._bytes(header + pcm16_bytes(block))
                            chunk_index += 1
                            reply.first_audio_at = reply.first_audio_at or time.time()
                            blocks.append(block)
                    else:
                        whole = await self.engines.tts.synthesize(text)
                        rate = self.engines.tts.sample_rate
                        header = AUDIO_MAGIC + struct.pack("<III", reply.seq, chunk_index, rate)
                        await self._bytes(header + pcm16_bytes(whole))
                        chunk_index += 1
                        reply.first_audio_at = reply.first_audio_at or time.time()
                        blocks.append(whole)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await self._notice(
                        "warn",
                        "tts_failed",
                        t(self.lang, "tts_failed", error=type(exc).__name__),
                    )
                    continue
                if not blocks:
                    continue
                rate = self.engines.tts.sample_rate
                audio = np.concatenate(blocks)
                if self._assistant_voice_samples < 6 and len(audio) > rate:
                    self._assistant_voice_samples += 1
                    try:
                        vector = await loop.run_in_executor(
                            self._emb_pool,
                            self.engines.embedder.embed,
                            resample_to_16k(audio, rate),
                        )
                        self.tracker.remember_assistant_voice(vector)
                    except Exception:
                        pass
        finally:
            await self._json(
                {
                    "type": "audio_end",
                    "reply_id": reply.id,
                    "reply_seq": reply.seq,
                    "chunks": chunk_index,
                }
            )
            if chunk_index == 0 and self._status == "thinking":
                await self._set_status("listening" if self.listening else "paused")


def silence_vector(dimension: int) -> Float32:
    return np.zeros(dimension, dtype=np.float32)
