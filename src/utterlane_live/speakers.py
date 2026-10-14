"""Session speaker tracking: who spoke, what they are called, and how sure we are.

Every finalized utterance yields (at most) one voice vector. The tracker compares it with
the speakers already heard in this session and with the stored (consented) profiles, then
either attributes the turn to a known speaker, opens a new speaker, or declares the turn
ambiguous. Names come only from what people say (self introduction, someone handing them
the floor, corrections) or from a consented stored profile — never from guessing.

A voice match is a convenience label. It never grants identity, memory or action rights.
"""

from __future__ import annotations

import itertools
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray

from utterlane_live.config import SpeakerThresholds
from utterlane_live.i18n import t
from utterlane_live.naming import tr_lower

Vector = NDArray[np.float32]
NameState = Literal["none", "provisional", "confirmed"]
AttributionState = Literal["known", "new", "ambiguous", "short", "overlap"]

UNKNOWN_ID = "unknown"
PALETTE_SIZE = 8
PROVISIONAL_CONFIRM_TURNS = 2
NOMINATION_TTL_S = 45.0
SHORT_INHERIT_S = 6.0
MAX_VECTORS = 60


def _unit(vector: NDArray[np.floating[Any]]) -> Vector:
    norm = float(np.linalg.norm(vector))
    if norm <= 0 or not np.isfinite(norm):
        raise ValueError("voice_vector_invalid")
    return (vector / norm).astype(np.float32)


@dataclass(slots=True)
class StoredProfile:
    """A consented voice profile persisted on this machine."""

    id: str
    name: str
    vectors: list[Vector]
    created_at: float
    updated_at: float
    consent_method: str = "spoken"
    model_fingerprint: str = ""

    @property
    def centroid(self) -> Vector:
        return _unit(np.mean(np.stack(self.vectors), axis=0))


@dataclass(slots=True)
class SessionSpeaker:
    id: str
    ordinal: int
    color: int
    vectors: deque[Vector] = field(default_factory=lambda: deque(maxlen=MAX_VECTORS))
    name: str | None = None
    name_state: NameState = "none"
    name_source: str | None = None
    profile_id: str | None = None
    turns: int = 0
    turns_since_provisional: int = 0
    last_seen: float = 0.0
    consent_asked: bool = False
    lang: str = "tr"
    _centroid: Vector | None = None

    @property
    def centroid(self) -> Vector | None:
        if self._centroid is None and self.vectors:
            self._centroid = _unit(np.mean(np.stack(list(self.vectors)), axis=0))
        return self._centroid

    def add_vector(self, vector: Vector) -> None:
        self.vectors.append(vector)
        self._centroid = None

    @property
    def label(self) -> str:
        if self.name and self.name_state == "confirmed":
            return self.name
        if self.name and self.name_state == "provisional":
            return f"{self.name}?"
        return t(self.lang, "person", n=self.ordinal)

    def to_public(self, *, present: bool = True) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "name": self.name,
            "name_state": self.name_state,
            "ordinal": self.ordinal,
            "persistent": self.profile_id is not None,
            "present": present,
            "turns": self.turns,
            "color": self.color,
        }


def unknown_public(lang: str = "tr") -> dict[str, object]:
    return {**UNKNOWN_PUBLIC, "label": t(lang, "unknown")}


UNKNOWN_PUBLIC: dict[str, object] = {
    "id": UNKNOWN_ID,
    "label": "Belirsiz",
    "name": None,
    "name_state": "none",
    "ordinal": 0,
    "persistent": False,
    "present": True,
    "turns": 0,
    "color": -1,
}


@dataclass(frozen=True, slots=True)
class Attribution:
    speaker_id: str
    state: AttributionState
    score: float | None = None
    margin: float | None = None
    guessed: bool = False


@dataclass(frozen=True, slots=True)
class IdentityEvent:
    kind: str
    speaker_id: str
    name: str | None
    message: str


@dataclass(slots=True)
class _Nomination:
    name: str
    nominator_id: str
    expires_at: float


class SpeakerTracker:
    def __init__(
        self,
        thresholds: SpeakerThresholds | None = None,
        profiles: list[StoredProfile] | None = None,
        *,
        clock: Callable[[], float] = time.time,
        language: str = "tr",
    ) -> None:
        self.lang = language
        self.t = thresholds or SpeakerThresholds()
        self.profiles: dict[str, StoredProfile] = {p.id: p for p in profiles or []}
        self.speakers: dict[str, SessionSpeaker] = {}
        self._ids = itertools.count(1)
        self._clock = clock
        self._nomination: _Nomination | None = None
        self._last_speaker_id: str | None = None
        self._last_turn_at = 0.0
        self.assistant_vectors: deque[Vector] = deque(maxlen=12)

    # ------------------------------------------------------------------ roster
    def roster(self, present_window_s: float = 180.0) -> list[dict[str, object]]:
        now = self._clock()
        return [
            s.to_public(present=(now - s.last_seen) <= present_window_s)
            for s in sorted(self.speakers.values(), key=lambda s: s.ordinal)
        ]

    def public(self, speaker_id: str) -> dict[str, object]:
        speaker = self.speakers.get(speaker_id)
        return speaker.to_public() if speaker else unknown_public(self.lang)

    def present_count(self, window_s: float = 180.0) -> int:
        now = self._clock()
        return sum(1 for s in self.speakers.values() if now - s.last_seen <= window_s)

    def known_names(self) -> frozenset[str]:
        names = {tr_lower(s.name) for s in self.speakers.values() if s.name}
        names |= {tr_lower(p.name) for p in self.profiles.values()}
        return frozenset(names)

    def find_by_name(self, name: str) -> SessionSpeaker | None:
        key = tr_lower(name)
        for speaker in self.speakers.values():
            if speaker.name and tr_lower(speaker.name) == key:
                return speaker
        return None

    # -------------------------------------------------------------- attribution
    def _new_speaker(self, now: float) -> SessionSpeaker:
        number = next(self._ids)
        ordinal = max((s.ordinal for s in self.speakers.values()), default=0) + 1
        speaker = SessionSpeaker(
            id=f"spk-{number}",
            ordinal=ordinal,
            color=(ordinal - 1) % PALETTE_SIZE,
            lang=self.lang,
        )
        speaker.last_seen = now
        self.speakers[speaker.id] = speaker
        return speaker

    def _candidates(self, vector: Vector) -> list[tuple[float, str, str]]:
        """(score, kind, id) for session speakers and not-yet-seen stored profiles."""

        scored: list[tuple[float, str, str]] = []
        bound = {s.profile_id for s in self.speakers.values() if s.profile_id}
        for speaker in self.speakers.values():
            centroid = speaker.centroid
            if centroid is not None:
                scored.append((float(vector @ centroid), "session", speaker.id))
        for profile in self.profiles.values():
            if profile.id not in bound:
                scored.append((float(vector @ profile.centroid), "profile", profile.id))
        scored.sort(reverse=True)
        return scored

    def is_assistant_voice(self, vector: Vector | None) -> bool:
        if vector is None or not self.assistant_vectors:
            return False
        centroid = _unit(np.mean(np.stack(list(self.assistant_vectors)), axis=0))
        return float(vector @ centroid) >= self.t.self_voice

    def remember_assistant_voice(self, vector: Vector) -> None:
        self.assistant_vectors.append(_unit(vector))

    def attribute(
        self,
        vector: Vector | None,
        duration_s: float,
        *,
        overlap: bool = False,
    ) -> tuple[Attribution, list[IdentityEvent]]:
        now = self._clock()
        events: list[IdentityEvent] = []
        if overlap:
            return Attribution(UNKNOWN_ID, "overlap"), events
        if vector is None or duration_s < self.t.min_embed_s:
            return self._short_guess(now), events

        vector = _unit(vector)
        candidates = self._candidates(vector)
        best_score = candidates[0][0] if candidates else -1.0
        second = candidates[1][0] if len(candidates) > 1 else -1.0
        margin = best_score - second
        needed = self.t.match if duration_s >= 1.0 else self.t.match_short

        if candidates and best_score >= needed and margin >= self.t.margin:
            _, kind, ident = candidates[0]
            if kind == "profile":
                speaker = self._new_speaker(now)
                profile = self.profiles[ident]
                speaker.profile_id = profile.id
                speaker.name = profile.name
                speaker.name_state = "confirmed"
                speaker.name_source = "stored_profile"
                speaker.consent_asked = True
                for stored in profile.vectors[-10:]:
                    speaker.add_vector(stored)
                events.append(
                    IdentityEvent(
                        "profile_recognized",
                        speaker.id,
                        profile.name,
                        t(self.lang, "ev_profile_recognized", name=profile.name),
                    )
                )
            else:
                speaker = self.speakers[ident]
            if best_score >= self.t.update_min_score and duration_s >= 1.0:
                speaker.add_vector(vector)
            events.extend(self._after_turn(speaker, now))
            events.extend(self._maybe_merge(speaker))
            return Attribution(speaker.id, "known", round(best_score, 4), round(margin, 4)), events

        if (not candidates or best_score < self.t.new_speaker) and duration_s >= self.t.min_new_s:
            speaker = self._new_speaker(now)
            speaker.add_vector(vector)
            events.append(
                IdentityEvent(
                    "new_speaker",
                    speaker.id,
                    None,
                    t(self.lang, "ev_new_speaker", label=speaker.label),
                )
            )
            events.extend(self._after_turn(speaker, now))
            return Attribution(
                speaker.id, "new", round(best_score, 4) if candidates else None, None
            ), events

        return Attribution(UNKNOWN_ID, "ambiguous", round(best_score, 4), round(margin, 4)), events

    def open_speaker(self, vector: Vector) -> tuple[SessionSpeaker, list[IdentityEvent]]:
        """Open a new speaker for a voice that clearly introduced itself but scored ambiguous."""

        now = self._clock()
        speaker = self._new_speaker(now)
        speaker.add_vector(_unit(vector))
        events = [
            IdentityEvent(
                "new_speaker", speaker.id, None, t(self.lang, "ev_new_speaker", label=speaker.label)
            )
        ]
        events.extend(self._after_turn(speaker, now))
        return speaker, events

    def _short_guess(self, now: float) -> Attribution:
        if self._last_speaker_id and now - self._last_turn_at <= SHORT_INHERIT_S:
            speaker = self.speakers.get(self._last_speaker_id)
            if speaker:
                speaker.turns += 1
                speaker.last_seen = now
                self._last_turn_at = now
                return Attribution(speaker.id, "short", guessed=True)
        return Attribution(UNKNOWN_ID, "short")

    def _after_turn(self, speaker: SessionSpeaker, now: float) -> list[IdentityEvent]:
        events: list[IdentityEvent] = []
        speaker.turns += 1
        speaker.last_seen = now
        self._last_speaker_id = speaker.id
        self._last_turn_at = now
        nomination = self._nomination
        if nomination and now > nomination.expires_at:
            self._nomination = nomination = None
        if nomination and speaker.id != nomination.nominator_id and speaker.name_state == "none":
            speaker.name = nomination.name
            speaker.name_state = "provisional"
            speaker.name_source = "nomination"
            speaker.turns_since_provisional = 0
            self._nomination = None
            events.append(
                IdentityEvent(
                    "provisional",
                    speaker.id,
                    speaker.name,
                    t(self.lang, "ev_provisional", name=nomination.name),
                )
            )
        elif speaker.name_state == "provisional":
            speaker.turns_since_provisional += 1
            if speaker.turns_since_provisional >= PROVISIONAL_CONFIRM_TURNS:
                speaker.name_state = "confirmed"
                events.append(
                    IdentityEvent(
                        "confirmed",
                        speaker.id,
                        speaker.name,
                        t(self.lang, "ev_confirmed", name=speaker.name),
                    )
                )
        return events

    def _maybe_merge(self, speaker: SessionSpeaker) -> list[IdentityEvent]:
        """Merge two session speakers whose voices turn out to be the same person."""

        centroid = speaker.centroid
        if centroid is None:
            return []
        for other in list(self.speakers.values()):
            if other.id == speaker.id or other.centroid is None:
                continue
            if float(centroid @ other.centroid) < self.t.merge:
                continue
            if speaker.name and other.name and tr_lower(speaker.name) != tr_lower(other.name):
                continue
            keep, drop = (speaker, other) if speaker.ordinal < other.ordinal else (other, speaker)
            for vec in drop.vectors:
                keep.add_vector(vec)
            keep.turns += drop.turns
            keep.last_seen = max(keep.last_seen, drop.last_seen)
            if not keep.name and drop.name:
                keep.name, keep.name_state, keep.name_source = (
                    drop.name,
                    drop.name_state,
                    drop.name_source,
                )
            keep.profile_id = keep.profile_id or drop.profile_id
            del self.speakers[drop.id]
            if self._last_speaker_id == drop.id:
                self._last_speaker_id = keep.id
            return [
                IdentityEvent(
                    "merged",
                    keep.id,
                    keep.name,
                    t(self.lang, "ev_merged", a=drop.label, b=keep.label),
                )
            ]
        return []

    # ------------------------------------------------------------------ naming
    def apply_self_intro(self, speaker_id: str, name: str) -> list[IdentityEvent]:
        speaker = self.speakers.get(speaker_id)
        if speaker is None:
            return []
        owner = self.find_by_name(name)
        if owner is not None and owner.id != speaker.id:
            # Someone else already holds this name in the session: keep both honest.
            if speaker.name_state != "confirmed":
                speaker.name, speaker.name_state, speaker.name_source = (
                    name,
                    "provisional",
                    "self_intro_conflict",
                )
            return [
                IdentityEvent(
                    "corrected",
                    speaker.id,
                    name,
                    t(self.lang, "ev_conflict", name=name),
                )
            ]
        if (
            speaker.name
            and tr_lower(speaker.name) == tr_lower(name)
            and speaker.name_state == "confirmed"
        ):
            return []
        previous = speaker.label
        speaker.name, speaker.name_state, speaker.name_source = name, "confirmed", "self_intro"
        if self._nomination and tr_lower(self._nomination.name) == tr_lower(name):
            self._nomination = None
        return [
            IdentityEvent(
                "named",
                speaker.id,
                name,
                t(self.lang, "ev_named", previous=previous, name=name),
            )
        ]

    def apply_nomination(self, nominator_id: str, name: str) -> list[IdentityEvent]:
        if self.find_by_name(name) is not None:
            return []
        self._nomination = _Nomination(name, nominator_id, self._clock() + NOMINATION_TTL_S)
        return []

    def apply_correction(
        self, speaker_id: str, wrong_name: str, right_name: str | None
    ) -> list[IdentityEvent]:
        speaker = self.speakers.get(speaker_id)
        if speaker is None:
            return []
        if right_name:
            return self.apply_self_intro(speaker_id, right_name)
        if speaker.name and tr_lower(speaker.name) == tr_lower(wrong_name):
            speaker.name, speaker.name_state, speaker.name_source = None, "none", None
            return [
                IdentityEvent(
                    "corrected",
                    speaker.id,
                    None,
                    t(self.lang, "ev_correction_cleared", wrong=wrong_name, label=speaker.label),
                )
            ]
        return []

    def rename(self, speaker_id: str, name: str | None) -> list[IdentityEvent]:
        speaker = self.speakers.get(speaker_id)
        if speaker is None:
            return []
        if name:
            speaker.name, speaker.name_state, speaker.name_source = name, "confirmed", "manual"
            return [
                IdentityEvent("named", speaker.id, name, t(self.lang, "ev_manual_named", name=name))
            ]
        speaker.name, speaker.name_state, speaker.name_source = None, "none", None
        return [
            IdentityEvent(
                "corrected",
                speaker.id,
                None,
                t(self.lang, "ev_manual_cleared", label=speaker.label),
            )
        ]

    def forget(self, speaker_id: str) -> SessionSpeaker | None:
        speaker = self.speakers.pop(speaker_id, None)
        if speaker and speaker.profile_id:
            self.profiles.pop(speaker.profile_id, None)
        if self._last_speaker_id == speaker_id:
            self._last_speaker_id = None
        return speaker

    # ------------------------------------------------------------ persistence
    def bind_profile(self, speaker_id: str, profile: StoredProfile) -> None:
        self.profiles[profile.id] = profile
        speaker = self.speakers.get(speaker_id)
        if speaker:
            speaker.profile_id = profile.id

    def session_vectors(self, speaker_id: str) -> list[Vector]:
        speaker = self.speakers.get(speaker_id)
        return list(speaker.vectors) if speaker else []

    def reset(self) -> None:
        self.speakers.clear()
        self._ids = itertools.count(1)
        self._nomination = None
        self._last_speaker_id = None
        self._last_turn_at = 0.0
