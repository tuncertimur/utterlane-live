"""Speaker tracker scenarios with synthetic voice vectors.

Each synthetic "person" is a random unit direction; utterances are that direction plus
noise (same-person cosine ≈ 0.7, cross-person ≈ 0). Real-voice behaviour is covered by the
private-corpus tests in ``tests/live/private``.
"""

from __future__ import annotations

import numpy as np
import pytest

from utterlane_live.config import SpeakerThresholds
from utterlane_live.speakers import UNKNOWN_ID, SpeakerTracker, StoredProfile

DIM = 448


class Voice:
    def __init__(self, seed: int) -> None:
        self.rng = np.random.default_rng(seed)
        base = self.rng.normal(size=DIM)
        self.base = base / np.linalg.norm(base)

    def utter(self, noise: float = 0.8) -> np.ndarray:
        v = self.base + noise * self.rng.normal(size=DIM) / np.sqrt(DIM)
        return (v / np.linalg.norm(v)).astype(np.float32)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float = 3.0) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def tracker(clock: Clock) -> SpeakerTracker:
    return SpeakerTracker(SpeakerThresholds(), clock=clock)


TUNCER, ZUMRA, GUEST, AHMET = Voice(1), Voice(2), Voice(3), Voice(4)


def say(tracker: SpeakerTracker, clock: Clock, voice: Voice, seconds: float = 2.0):
    clock.tick()
    return tracker.attribute(voice.utter(), seconds)


def test_self_intro_then_random_talk_is_recognized(tracker: SpeakerTracker, clock: Clock) -> None:
    att, events = say(tracker, clock, TUNCER)
    assert att.state == "new"
    assert [e.kind for e in events] == ["new_speaker"]
    assert tracker.public(att.speaker_id)["label"] == "1. kişi"

    events = tracker.apply_self_intro(att.speaker_id, "Tuncer")
    assert events[0].kind == "named"
    assert tracker.public(att.speaker_id)["label"] == "Tuncer"

    for _ in range(5):
        later, _ = say(tracker, clock, TUNCER)
        assert later.state == "known"
        assert later.speaker_id == att.speaker_id
        assert tracker.public(later.speaker_id)["label"] == "Tuncer"


def test_two_named_people_and_an_unintroduced_third(tracker: SpeakerTracker, clock: Clock) -> None:
    t, _ = say(tracker, clock, TUNCER)
    tracker.apply_self_intro(t.speaker_id, "Tuncer")
    z, _ = say(tracker, clock, ZUMRA)
    tracker.apply_self_intro(z.speaker_id, "Zümra")
    g, events = say(tracker, clock, GUEST)
    assert g.state == "new"
    assert tracker.public(g.speaker_id)["label"] == "3. kişi"
    assert events[0].message.startswith("Yeni bir ses")

    # Everyone keeps their label through a mixed conversation.
    for voice, expected in [
        (ZUMRA, "Zümra"),
        (GUEST, "3. kişi"),
        (TUNCER, "Tuncer"),
        (GUEST, "3. kişi"),
    ]:
        att, _ = say(tracker, clock, voice)
        assert att.state == "known"
        assert tracker.public(att.speaker_id)["label"] == expected
    assert tracker.present_count() == 3


def test_nomination_labels_the_next_new_voice(tracker: SpeakerTracker, clock: Clock) -> None:
    t, _ = say(tracker, clock, TUNCER)
    tracker.apply_self_intro(t.speaker_id, "Tuncer")
    tracker.apply_nomination(t.speaker_id, "Ahmet")

    # Tuncer keeps talking a bit: nomination must not stick to the nominator.
    again, _ = say(tracker, clock, TUNCER)
    assert tracker.public(again.speaker_id)["label"] == "Tuncer"

    a, events = say(tracker, clock, AHMET)
    assert a.state == "new"
    assert [e.kind for e in events] == ["new_speaker", "provisional"]
    assert tracker.public(a.speaker_id)["label"] == "Ahmet?"
    assert tracker.public(a.speaker_id)["name_state"] == "provisional"

    _, events = say(tracker, clock, AHMET)
    assert not any(e.kind == "confirmed" for e in events)
    _, events = say(tracker, clock, AHMET)
    assert any(e.kind == "confirmed" for e in events)
    assert tracker.public(a.speaker_id)["label"] == "Ahmet"


def test_nomination_of_known_person_does_not_rename_strangers(
    tracker: SpeakerTracker, clock: Clock
) -> None:
    t, _ = say(tracker, clock, TUNCER)
    tracker.apply_self_intro(t.speaker_id, "Tuncer")
    z, _ = say(tracker, clock, ZUMRA)
    tracker.apply_self_intro(z.speaker_id, "Zümra")
    tracker.apply_nomination(t.speaker_id, "Zümra")
    g, _ = say(tracker, clock, GUEST)
    assert tracker.public(g.speaker_id)["label"] == "3. kişi"


def test_nomination_expires(tracker: SpeakerTracker, clock: Clock) -> None:
    t, _ = say(tracker, clock, TUNCER)
    tracker.apply_nomination(t.speaker_id, "Ahmet")
    clock.tick(120)
    a, _ = say(tracker, clock, AHMET)
    assert tracker.public(a.speaker_id)["label"] == "2. kişi"


def test_correction_by_voice(tracker: SpeakerTracker, clock: Clock) -> None:
    t, _ = say(tracker, clock, TUNCER)
    tracker.apply_nomination(t.speaker_id, "Ahmet")
    m, _ = say(tracker, clock, AHMET)
    assert tracker.public(m.speaker_id)["label"] == "Ahmet?"
    events = tracker.apply_correction(m.speaker_id, "Ahmet", "Mehmet")
    assert events[0].kind == "named"
    assert tracker.public(m.speaker_id)["label"] == "Mehmet"
    events = tracker.apply_correction(m.speaker_id, "Mehmet", None)
    assert tracker.public(m.speaker_id)["label"] == "2. kişi"


def test_name_conflict_is_not_silently_accepted(tracker: SpeakerTracker, clock: Clock) -> None:
    t, _ = say(tracker, clock, TUNCER)
    tracker.apply_self_intro(t.speaker_id, "Tuncer")
    z, _ = say(tracker, clock, ZUMRA)
    events = tracker.apply_self_intro(z.speaker_id, "Tuncer")
    assert events[0].kind == "corrected"
    assert tracker.public(z.speaker_id)["label"] == "Tuncer?"
    assert tracker.public(t.speaker_id)["label"] == "Tuncer"


def test_short_turn_is_only_a_guess(tracker: SpeakerTracker, clock: Clock) -> None:
    t, _ = say(tracker, clock, TUNCER)
    clock.tick(1)
    att, _ = tracker.attribute(None, 0.4)
    assert att.state == "short" and att.guessed and att.speaker_id == t.speaker_id
    clock.tick(60)
    att, _ = tracker.attribute(None, 0.4)
    assert att.state == "short" and att.speaker_id == UNKNOWN_ID


def test_ambiguous_vector_is_not_forced_onto_anyone(tracker: SpeakerTracker, clock: Clock) -> None:
    t, _ = say(tracker, clock, TUNCER)
    z, _ = say(tracker, clock, ZUMRA)
    mix = TUNCER.base + ZUMRA.base
    clock.tick()
    att, _ = tracker.attribute((mix / np.linalg.norm(mix)).astype(np.float32), 2.0)
    assert att.state == "ambiguous"
    assert att.speaker_id == UNKNOWN_ID


def test_stored_profile_recognized_in_new_session(clock: Clock) -> None:
    stored = StoredProfile(
        id="p-tuncer",
        name="Tuncer",
        vectors=[TUNCER.utter() for _ in range(6)],
        created_at=0.0,
        updated_at=0.0,
    )
    tracker = SpeakerTracker(SpeakerThresholds(), [stored], clock=clock)
    att, events = say(tracker, clock, TUNCER)
    assert att.state == "known"
    assert [e.kind for e in events] == ["profile_recognized"]
    pub = tracker.public(att.speaker_id)
    assert pub["label"] == "Tuncer" and pub["persistent"] is True and pub["ordinal"] == 1
    g, _ = say(tracker, clock, GUEST)
    assert tracker.public(g.speaker_id)["label"] == "2. kişi"


def test_merge_when_two_clusters_drift_into_one_person(clock: Clock) -> None:
    tracker = SpeakerTracker(SpeakerThresholds(), clock=clock)
    t = TUNCER.base.astype(np.float32)
    rng = np.random.default_rng(7)
    o = rng.normal(size=DIM)
    o -= (o @ t) * t
    o /= np.linalg.norm(o)

    def unit(v: np.ndarray) -> np.ndarray:
        return (v / np.linalg.norm(v)).astype(np.float32)

    a, _ = tracker.attribute(t, 2.0)
    clock.tick()
    # A distant first take of the same person (e.g. far from the mic) opens a second cluster.
    b, _ = tracker.attribute(unit(0.3 * t + 0.954 * o), 2.0)
    assert b.state == "new" and b.speaker_id != a.speaker_id
    merged = False
    for _ in range(3):
        clock.tick()
        att, events = tracker.attribute(unit(0.75 * t + 0.66 * o), 2.0)
        merged = merged or any(e.kind == "merged" for e in events)
    assert merged
    assert list(tracker.speakers) == [a.speaker_id]
    assert tracker.public(a.speaker_id)["label"] == "1. kişi"


def test_assistant_voice_is_recognized_as_self(tracker: SpeakerTracker) -> None:
    lena = Voice(99)
    for _ in range(3):
        tracker.remember_assistant_voice(lena.utter())
    assert tracker.is_assistant_voice(lena.utter())
    assert not tracker.is_assistant_voice(TUNCER.utter())


def test_forget_removes_speaker_and_profile(clock: Clock) -> None:
    stored = StoredProfile("p1", "Tuncer", [TUNCER.utter() for _ in range(4)], 0.0, 0.0)
    tracker = SpeakerTracker(SpeakerThresholds(), [stored], clock=clock)
    att, _ = say(tracker, clock, TUNCER)
    tracker.forget(att.speaker_id)
    assert not tracker.speakers and not tracker.profiles
    again, _ = say(tracker, clock, TUNCER)
    assert tracker.public(again.speaker_id)["label"] == "1. kişi"
