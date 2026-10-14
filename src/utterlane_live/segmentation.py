"""Split one endpointed utterance when the speaker changes inside it.

Fast hand-offs ("Ahmet sen konuş." immediately followed by Ahmet) can arrive as a single
VAD segment. We embed short sliding windows, look for the boundary where the mean voice
vector on the left differs most from the one on the right (binary segmentation), and cut
at the quietest point near that boundary. Word timestamps from STT split the text.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from utterlane_live.audio import SAMPLE_RATE, Float32

SPLIT_MIN_S = 2.2


@dataclass(frozen=True, slots=True)
class ChangeConfig:
    window_s: float = 1.0
    hop_s: float = 0.5
    min_side_s: float = 1.0
    threshold: float = 0.45
    max_changes: int = 2
    refine_s: float = 0.7
    # Second stage: whole-side vectors (fused model) must also disagree.
    verify_threshold: float = 0.45


def _unit(vector: Float32) -> Float32:
    return vector / max(float(np.linalg.norm(vector)), 1e-9)


def _frame_rms(audio: Float32, pos: int, frame: int = 320) -> float:
    chunk = audio[max(0, pos - frame // 2) : pos + frame // 2]
    return float(np.sqrt(np.mean(np.square(chunk)))) if len(chunk) else 0.0


def _quietest(audio: Float32, center: int, radius: int) -> int:
    """Move the cut to a clear pause near ``center`` if there is one."""

    frame = 320
    lo, hi = max(frame, center - radius), min(len(audio) - frame, center + radius)
    if hi <= lo:
        return center
    best, best_rms = center, float("inf")
    for pos in range(lo, hi, frame // 2):
        rms = _frame_rms(audio, pos, frame)
        if rms < best_rms:
            best, best_rms = pos, rms
    local = float(np.median([_frame_rms(audio, p, frame) for p in range(lo, hi, frame)]))
    return best if best_rms < 0.5 * local else center


def speaker_change_points(
    audio: Float32,
    embed: Callable[[Float32], Float32],
    config: ChangeConfig | None = None,
    verify: Callable[[Float32], Float32] | None = None,
) -> list[int]:
    """Return sample offsets where the speaker changes (sorted, possibly empty).

    ``embed`` scans short windows (a fast model is fine); ``verify`` (the accurate model)
    must confirm each candidate by comparing the whole left and right sides.
    """

    cfg = config or ChangeConfig()
    win, hop = int(cfg.window_s * SAMPLE_RATE), int(cfg.hop_s * SAMPLE_RATE)
    if len(audio) < 2 * int(cfg.min_side_s * SAMPLE_RATE) or len(audio) < win + hop:
        return []
    starts = list(range(0, len(audio) - win + 1, hop))
    rms = np.array([float(np.sqrt(np.mean(np.square(audio[s : s + win])))) for s in starts])
    voiced = rms >= 0.3 * float(np.median(rms))
    vectors: dict[int, Float32] = {}
    for s, ok in zip(starts, voiced, strict=True):
        if ok:
            vectors[s] = _unit(embed(audio[s : s + win]))

    changes: list[int] = []

    def search(lo: int, hi: int, depth: int) -> None:
        if depth >= cfg.max_changes or len(changes) >= cfg.max_changes:
            return
        side = int(cfg.min_side_s * SAMPLE_RATE)
        scored: list[tuple[int, float]] = []
        for b in range(lo + side, hi - side + 1, hop):
            left = [v for s, v in vectors.items() if s >= lo and s + win <= b]
            right = [v for s, v in vectors.items() if s >= b and s + win <= hi]
            if not left or not right:
                continue
            scored.append((b, float(_unit(np.mean(left, axis=0)) @ _unit(np.mean(right, axis=0)))))
        if not scored:
            return
        best_score = min(score for _, score in scored)
        if best_score >= cfg.threshold:
            return
        # Several boundaries can tie (window granularity): take the middle of the tied run.
        tied = [b for b, score in scored if score <= best_score + 0.02]
        best_b = (
            tied[len(tied) // 2]
            if len(tied) % 2
            else (tied[len(tied) // 2 - 1] + tied[len(tied) // 2]) // 2
        )
        cut = _quietest(audio, best_b, int(cfg.refine_s * SAMPLE_RATE))
        if verify is not None:
            left_v, right_v = _unit(verify(audio[lo:cut])), _unit(verify(audio[cut:hi]))
            if float(left_v @ right_v) >= cfg.verify_threshold:
                return
        changes.append(cut)
        search(lo, cut, depth + 1)
        search(cut, hi, depth + 1)

    search(0, len(audio), 0)
    return sorted(changes)


def split_words(
    words: tuple[tuple[float, float, str], ...],
    cuts: list[int],
    total_samples: int,
) -> list[tuple[int, int, str]]:
    """Partition timed words by cut points; returns (start_sample, end_sample, text)."""

    bounds = [0, *cuts, total_samples]
    parts: list[tuple[int, int, str]] = []
    for lo, hi in zip(bounds, bounds[1:], strict=False):
        text = "".join(
            w for start, end, w in words if lo <= int((start + end) / 2 * SAMPLE_RATE) < hi
        ).strip()
        parts.append((lo, hi, text))
    # Merge text-less pieces into their neighbour so no turn is empty.
    merged: list[tuple[int, int, str]] = []
    for lo, hi, text in parts:
        if not text and merged:
            plo, _, ptext = merged[-1]
            merged[-1] = (plo, hi, ptext)
        elif merged and not merged[-1][2]:
            plo, _, _ = merged[-1]
            merged[-1] = (plo, hi, text)
        else:
            merged.append((lo, hi, text))
    return merged
