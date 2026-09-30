from __future__ import annotations

from math import ceil

import numpy as np

from .cues import merge_short_cues
from .models import Cue


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
        start = start_index * hop
        end = min(duration, index * hop)
        segment_energy = float(np.max(energies[start_index:index])) if index > start_index else 0.0
        cues.append(Cue(start, end, current, energy=segment_energy, confidence=0.72))
        if index < len(labels):
            start_index = index
            current = labels[index]
    if cues[-1].end < duration:
        cues[-1].end = duration
    return cues


def analyze_waveform(
    samples: np.ndarray,
    sample_rate: int,
    *,
    intensity: float = 1.0,
    hop_seconds: float = 0.04,
    min_cue_seconds: float = 0.08,
) -> list[Cue]:
    """Generate deterministic, phoneme-inspired cues from audio features.

    This is the no-download fallback. Rhubarb can replace it when installed.
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
    frame_length = max(hop, int(round(sample_rate * 0.05)))
    frame_count = max(1, int(ceil(mono.size / hop)))
    window = np.hanning(frame_length).astype(np.float32)
    frequencies = np.fft.rfftfreq(frame_length, d=1.0 / sample_rate)

    rms = np.zeros(frame_count, dtype=np.float32)
    centroid = np.zeros(frame_count, dtype=np.float32)
    low_ratio = np.zeros(frame_count, dtype=np.float32)
    high_ratio = np.zeros(frame_count, dtype=np.float32)
    zcr = np.zeros(frame_count, dtype=np.float32)

    low_mask = frequencies < 450.0
    high_mask = frequencies >= 2400.0
    for index in range(frame_count):
        start = index * hop
        frame = mono[start : start + frame_length]
        if frame.size < frame_length:
            frame = np.pad(frame, (0, frame_length - frame.size))
        rms[index] = float(np.sqrt(np.mean(frame * frame) + 1e-12))
        signs = np.signbit(frame)
        zcr[index] = float(np.count_nonzero(signs[1:] != signs[:-1]) / max(1, frame_length - 1))
        spectrum = np.abs(np.fft.rfft(frame * window)) ** 2
        total = float(np.sum(spectrum)) + 1e-12
        centroid[index] = float(np.sum(frequencies * spectrum) / total)
        low_ratio[index] = float(np.sum(spectrum[low_mask]) / total)
        high_ratio[index] = float(np.sum(spectrum[high_mask]) / total)

    rms = _smooth(rms, 3)
    peak_rms = float(np.quantile(rms, 0.95))
    if peak_rms < 1e-7:
        return [Cue(0.0, duration, "X", energy=0.0, confidence=1.0)]
    noise_floor = float(np.quantile(rms, 0.15))
    gate = max(peak_rms * 0.035, noise_floor * 2.4)
    denominator = max(peak_rms - gate, 1e-8)
    energies = np.clip((rms - gate) / denominator, 0.0, 1.0)
    energies = np.clip(np.power(energies, 0.72) * float(np.clip(intensity, 0.4, 1.8)), 0.0, 1.0)
    energies = _smooth(energies, 3)

    labels: list[str] = []
    previous_energy = 0.0
    for index, energy_value in enumerate(energies):
        energy = float(energy_value)
        if rms[index] <= gate or energy < 0.055:
            shape = "X"
        else:
            onset = energy - previous_energy
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
        previous_energy = energy

    cues = _labels_to_cues(labels, energies, hop_seconds, duration)
    cues = merge_short_cues(cues, min_duration=min_cue_seconds)
    # Rounding at serialization time preserves the exact total duration here.
    if cues:
        cues[0].start = 0.0
        cues[-1].end = duration
    return cues
