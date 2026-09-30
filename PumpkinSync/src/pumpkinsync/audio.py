from __future__ import annotations

from math import ceil

import numpy as np
from scipy.signal import find_peaks

from .cues import merge_short_cues
from .models import Cue

# Singing voice carries most of its intelligibility here; kick drums, bass and
# cymbals mostly fall outside it.
VOCAL_BAND_HZ = (250.0, 3500.0)
_CHUNK_FRAMES = 2048


def _as_mono_float(samples: np.ndarray) -> np.ndarray:
    value = np.asarray(samples)
    if value.ndim == 2:
        value = value.mean(axis=1)
    if value.ndim != 1:
        raise ValueError("audio samples must be one-dimensional or channel-last stereo")
    if np.issubdtype(value.dtype, np.integer):
        maximum = max(abs(np.iinfo(value.dtype).min), np.iinfo(value.dtype).max)
        value = value.astype(np.float32) / float(maximum)
    else:
        value = value.astype(np.float32, copy=False)
    return np.nan_to_num(value, nan=0.0, posinf=1.0, neginf=-1.0)


def extract_waveform_peaks(samples: np.ndarray, points: int = 1200) -> list[float]:
    if points <= 0:
        raise ValueError("points must be positive")
    mono = np.abs(_as_mono_float(samples))
    if mono.size == 0:
        return [0.0] * points
    edges = np.linspace(0, mono.size, points + 1, dtype=np.int64)
    peaks = np.zeros(points, dtype=np.float32)
    for index in range(points):
        start, end = int(edges[index]), int(edges[index + 1])
        if end <= start:
            sample_index = min(start, mono.size - 1)
            peaks[index] = mono[sample_index]
        else:
            peaks[index] = float(np.max(mono[start:end]))
    maximum = float(np.max(peaks))
    if maximum > 0:
        peaks /= maximum
    return np.clip(peaks, 0.0, 1.0).astype(float).tolist()


def waveform_points_for(duration: float) -> int:
    """Enough waveform detail to zoom into long tracks without huge payloads."""

    return int(min(24_000, max(1200, round(duration * 100))))


def _smooth(values: np.ndarray, width: int = 3) -> np.ndarray:
    if width <= 1 or values.size < 2:
        return values
    kernel = np.ones(width, dtype=np.float32) / float(width)
    padded = np.pad(values, (width // 2, width - 1 - width // 2), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def _labels_to_cues(labels: list[str], energies: np.ndarray, hop: float, duration: float) -> list[Cue]:
    if not labels:
        return [Cue(0.0, duration, "X")]
    cues: list[Cue] = []
    start_index = 0
    current = labels[0]
    for index in range(1, len(labels) + 1):
        changed = index == len(labels) or labels[index] != current
        if not changed:
            continue
        start = min(duration, start_index * hop)
        end = min(duration, index * hop)
        segment_energy = float(np.max(energies[start_index:index])) if index > start_index else 0.0
        if end > start:
            cues.append(Cue(start, end, current, energy=segment_energy, confidence=0.72))
        if index < len(labels):
            start_index = index
            current = labels[index]
    if not cues:
        return [Cue(0.0, duration, "X")]
    if cues[-1].end < duration:
        cues[-1].end = duration
    return cues


def _frame_features(
    mono: np.ndarray, sample_rate: int, hop: int, frame_length: int, band: tuple[float, float] | None
) -> dict[str, np.ndarray]:
    frame_count = max(1, int(ceil(mono.size / hop)))
    padded = np.pad(mono, (0, frame_count * hop + frame_length - mono.size))
    window = np.hanning(frame_length).astype(np.float32)
    frequencies = np.fft.rfftfreq(frame_length, d=1.0 / sample_rate)
    low_mask = frequencies < 450.0
    high_mask = frequencies >= 2400.0
    band_mask = (frequencies >= band[0]) & (frequencies < band[1]) if band else None
    offsets = np.arange(frame_length)

    features = {
        name: np.zeros(frame_count, dtype=np.float32)
        for name in ("rms", "band", "centroid", "low_ratio", "high_ratio", "zcr")
    }
    window_power = float(np.sum(window * window)) or 1.0
    for first in range(0, frame_count, _CHUNK_FRAMES):
        last = min(frame_count, first + _CHUNK_FRAMES)
        frames = padded[(np.arange(first, last) * hop)[:, None] + offsets]
        features["rms"][first:last] = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
        signs = np.signbit(frames)
        features["zcr"][first:last] = np.count_nonzero(signs[:, 1:] != signs[:, :-1], axis=1) / max(1, frame_length - 1)
        spectrum = np.abs(np.fft.rfft(frames * window, axis=1)) ** 2
        total = np.sum(spectrum, axis=1) + 1e-12
        features["centroid"][first:last] = np.sum(spectrum * frequencies, axis=1) / total
        features["low_ratio"][first:last] = np.sum(spectrum[:, low_mask], axis=1) / total
        features["high_ratio"][first:last] = np.sum(spectrum[:, high_mask], axis=1) / total
        if band_mask is not None:
            # Parseval: band power per sample, comparable in scale to RMS.
            features["band"][first:last] = np.sqrt(
                2.0 * np.sum(spectrum[:, band_mask], axis=1) / (frame_length * window_power) + 1e-12
            )
    return features


def _syllable_closures(
    energies: np.ndarray, voiced: np.ndarray, hop_seconds: float, min_cue_seconds: float
) -> dict[int, str]:
    """Find dips between syllables where a real mouth briefly closes.

    Continuous speech rarely falls fully silent between syllables, so a purely
    level-driven mouth just hangs open. Each clear valley between two energy
    peaks becomes a short closed (A) or nearly-closed (B) cue.
    """

    if energies.size < 5:
        return {}
    peaks, _ = find_peaks(
        energies,
        prominence=0.12,
        distance=max(1, int(round(0.10 / hop_seconds))),
    )
    half_width = max(1, int(ceil(min_cue_seconds / hop_seconds / 2.0)))
    closures: dict[int, str] = {}
    for left, right in zip(peaks[:-1], peaks[1:]):
        if right - left < 2 * half_width + 2:
            continue
        valley = int(left + np.argmin(energies[left:right]))
        floor = float(energies[valley])
        shoulder = float(min(energies[left], energies[right]))
        if shoulder <= 0 or floor > shoulder * 0.62 or not voiced[valley]:
            continue
        shape = "A" if floor < shoulder * 0.3 else "B"
        threshold = floor + 0.35 * (shoulder - floor)
        start = valley
        while start - 1 > left and energies[start - 1] <= threshold:
            start -= 1
        end = valley
        while end + 1 < right and energies[end + 1] <= threshold:
            end += 1
        # Wide enough to survive short-cue merging, never swallowing a peak.
        start = max(left + 1, min(start, valley - half_width))
        end = min(right - 1, max(end, valley + half_width))
        for index in range(start, end + 1):
            if voiced[index]:
                closures[index] = shape
    return closures


def analyze_waveform(
    samples: np.ndarray,
    sample_rate: int,
    *,
    intensity: float = 1.0,
    mode: str = "skit",
    hop_seconds: float = 0.02,
    min_cue_seconds: float = 0.08,
) -> list[Cue]:
    """Generate deterministic, phoneme-inspired cues from audio features.

    This is the no-download fallback. Rhubarb can replace it when installed.
    ``mode="song"`` measures loudness only inside the vocal band so drums and
    bass drive the mouth less.
    """

    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    if hop_seconds <= 0:
        raise ValueError("hop_seconds must be positive")
    mono = _as_mono_float(samples)
    if mono.size == 0:
        raise ValueError("audio contains no samples")

    duration = mono.size / float(sample_rate)
    peak_amplitude = float(np.max(np.abs(mono)))
    if peak_amplitude < 1e-7:
        return [Cue(0.0, duration, "X", energy=0.0, confidence=1.0)]

    hop = max(1, int(round(sample_rate * hop_seconds)))
    actual_hop = hop / float(sample_rate)
    frame_length = max(hop, int(round(sample_rate * 0.05)))
    song = mode == "song"
    features = _frame_features(mono, sample_rate, hop, frame_length, VOCAL_BAND_HZ if song else None)

    smoothing = max(1, int(round(0.06 / actual_hop)))
    level = _smooth(features["band"] if song else features["rms"], smoothing)
    peak_level = float(np.quantile(level, 0.95))
    if peak_level < 1e-7:
        return [Cue(0.0, duration, "X", energy=0.0, confidence=1.0)]
    noise_floor = float(np.quantile(level, 0.15))
    gate = max(peak_level * (0.06 if song else 0.035), noise_floor * 2.4)
    denominator = max(peak_level - gate, 1e-8)
    energies = np.clip((level - gate) / denominator, 0.0, 1.0)
    energies = np.clip(np.power(energies, 0.72) * float(np.clip(intensity, 0.4, 1.8)), 0.0, 1.0)
    energies = _smooth(energies, smoothing)

    voiced = (level > gate) & (energies >= 0.055)
    closures = _syllable_closures(energies, voiced, actual_hop, min_cue_seconds)
    onset_span = max(1, int(round(0.04 / actual_hop)))
    centroid = features["centroid"]
    low_ratio = features["low_ratio"]
    high_ratio = features["high_ratio"]
    zcr = features["zcr"]

    labels: list[str] = []
    for index, energy_value in enumerate(energies):
        energy = float(energy_value)
        if not voiced[index]:
            shape = "X"
        elif index in closures:
            shape = closures[index]
        else:
            onset = energy - float(energies[max(0, index - onset_span)])
            centroid_hz = float(centroid[index])
            consonant_like = high_ratio[index] > 0.34 or zcr[index] > 0.19
            rounded = low_ratio[index] > 0.58 or centroid_hz < 720.0

            if onset > 0.28 and energy < 0.72:
                shape = "A"
            elif consonant_like:
                shape = "H" if energy > 0.68 else "G" if energy > 0.36 else "B"
            elif rounded:
                if energy > 0.82:
                    shape = "D"
                elif energy > 0.48:
                    shape = "E"
                else:
                    shape = "F"
            elif centroid_hz > 1850.0:
                shape = "G" if energy > 0.60 else "B"
            elif energy > 0.78:
                shape = "D"
            elif energy > 0.46:
                shape = "C"
            elif energy > 0.22:
                shape = "E"
            else:
                shape = "B"
        labels.append(shape)

    cues = _labels_to_cues(labels, energies, actual_hop, duration)
    cues = merge_short_cues(cues, min_duration=min_cue_seconds)
    # Rounding at serialization time preserves the exact total duration here.
    if cues:
        cues[0].start = 0.0
        cues[-1].end = duration
    return cues
