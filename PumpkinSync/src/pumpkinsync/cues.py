from __future__ import annotations

from dataclasses import replace
from math import isclose
from typing import Iterable

from .models import Cue, VALID_SHAPES


def _weighted_energy(left: Cue, right: Cue) -> float:
    duration = max(0.0, left.duration) + max(0.0, right.duration)
    if duration <= 0:
        return max(left.energy, right.energy)
    return (
        max(0.0, left.duration) * left.energy
        + max(0.0, right.duration) * right.energy
    ) / duration


def _compact_adjacent(cues: Iterable[Cue], tolerance: float = 1e-6) -> list[Cue]:
    compacted: list[Cue] = []
    for cue in cues:
        current = replace(cue)
        if (
            compacted
            and compacted[-1].shape == current.shape
            and isclose(compacted[-1].end, current.start, abs_tol=tolerance)
        ):
            previous = compacted.pop()
            compacted.append(
                Cue(
                    start=previous.start,
                    end=current.end,
                    shape=current.shape,
                    energy=_weighted_energy(previous, current),
                    confidence=min(previous.confidence, current.confidence),
                )
            )
        else:
            compacted.append(current)
    return compacted


def merge_short_cues(cues: Iterable[Cue], min_duration: float = 0.08) -> list[Cue]:
    """Absorb brief visual pops into a neighboring cue and compact equal shapes.

    Non-rest transients directly after rest are absorbed forward so a mouth does not
    flash open before a phrase. Transients before rest are absorbed backward.
    """

    if min_duration <= 0:
        return _compact_adjacent(cues)

    work = _compact_adjacent(cues)
    index = 0
    while len(work) > 1 and index < len(work):
        cue = work[index]
        if cue.duration + 1e-9 >= min_duration:
            index += 1
            continue

        if index == 0:
            next_cue = work[1]
            work[1] = Cue(
                cue.start,
                next_cue.end,
                next_cue.shape,
                _weighted_energy(cue, next_cue),
                min(cue.confidence, next_cue.confidence),
            )
            del work[0]
        elif index == len(work) - 1:
            previous = work[index - 1]
            work[index - 1] = Cue(
                previous.start,
                cue.end,
                previous.shape,
                _weighted_energy(previous, cue),
                min(previous.confidence, cue.confidence),
            )
            del work[index]
            index = max(0, index - 1)
        else:
            previous = work[index - 1]
            following = work[index + 1]
            absorb_forward = previous.shape == "X" and following.shape != "X"
            if following.shape == "X" and previous.shape != "X":
                absorb_forward = False
            elif previous.shape != "X" and following.shape != "X":
                absorb_forward = following.duration > previous.duration

            if absorb_forward:
                work[index + 1] = Cue(
                    cue.start,
                    following.end,
                    following.shape,
                    _weighted_energy(cue, following),
                    min(cue.confidence, following.confidence),
                )
                del work[index]
            else:
                work[index - 1] = Cue(
                    previous.start,
                    cue.end,
                    previous.shape,
                    _weighted_energy(previous, cue),
                    min(previous.confidence, cue.confidence),
                )
                del work[index]
                index = max(0, index - 1)
        work = _compact_adjacent(work)

    return work


def validate_cues(cues: Iterable[Cue], duration: float, tolerance: float = 1e-3) -> None:
    values = list(cues)
    if duration <= 0:
        raise ValueError("duration must be positive")
    if not values:
        raise ValueError("at least one cue is required")

    for index, cue in enumerate(values):
        if cue.shape not in VALID_SHAPES:
            raise ValueError(f"unknown mouth shape {cue.shape!r}")
        if cue.start < -tolerance:
            raise ValueError("cue starts before zero")
        if cue.end <= cue.start:
            raise ValueError("cue end must be after start")
        if not 0.0 <= cue.energy <= 1.0:
            raise ValueError("cue energy must be between zero and one")
        if index:
            previous = values[index - 1]
            delta = cue.start - previous.end
            if delta > tolerance:
                raise ValueError(f"gap between cues at index {index - 1} and {index}")
            if delta < -tolerance:
                raise ValueError(f"overlap between cues at index {index - 1} and {index}")

    if abs(values[0].start) > tolerance:
        raise ValueError("timeline must start at zero")
    if abs(values[-1].end - duration) > tolerance:
        raise ValueError("timeline must cover the full duration")


def shift_cues(cues: Iterable[Cue], offset: float, duration: float) -> list[Cue]:
    """Move every cue boundary *offset* seconds earlier (negative moves later).

    Used for "mouth lead": animators usually open the mouth a frame or two
    before the sound is heard so the picture reads as in sync.
    """

    values = [replace(cue) for cue in cues]
    if not values or abs(offset) < 1e-9:
        return values
    shifted: list[Cue] = []
    for cue in values:
        start = min(duration, max(0.0, cue.start - offset))
        end = min(duration, max(0.0, cue.end - offset))
        if end - start > 1e-6:
            shifted.append(Cue(start, end, cue.shape, cue.energy, cue.confidence))
    if not shifted:
        return [Cue(0.0, duration, values[0].shape, values[0].energy, values[0].confidence)]
    # Whatever was pushed outside the timeline is covered by the nearest cue.
    shifted[0].start = 0.0
    shifted[-1].end = duration
    return _compact_adjacent(shifted)
