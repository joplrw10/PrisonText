from __future__ import annotations

import numpy as np

from pumpkinsync.audio import analyze_waveform, extract_waveform_peaks
from pumpkinsync.cues import validate_cues


def test_silence_produces_single_rest_cue() -> None:
    sample_rate = 16_000
    samples = np.zeros(sample_rate, dtype=np.float32)

    cues = analyze_waveform(samples, sample_rate)

    assert len(cues) == 1
    assert cues[0].shape == "X"
    assert cues[0].start == 0.0
    assert abs(cues[0].end - 1.0) < 1e-6


def test_voiced_envelope_opens_only_during_active_region() -> None:
    sample_rate = 16_000
    seconds = 2.0
    t = np.arange(int(sample_rate * seconds), dtype=np.float32) / sample_rate
    samples = np.zeros_like(t)
    active = (t >= 0.5) & (t < 1.5)
    envelope = np.sin(np.pi * (t[active] - 0.5)) ** 2
    samples[active] = 0.75 * envelope * np.sin(2 * np.pi * 180 * t[active])

    cues = analyze_waveform(samples, sample_rate, intensity=1.15)

    validate_cues(cues, duration=seconds)
    assert cues[0].shape == "X"
    assert cues[-1].shape == "X"
    active_cues = [cue for cue in cues if cue.shape != "X"]
    assert active_cues
    assert any(cue.shape in {"C", "D", "E", "F"} for cue in active_cues)
    assert max(cue.energy for cue in active_cues) > 0.7


def test_noisy_consonant_like_audio_uses_narrow_shapes() -> None:
    rng = np.random.default_rng(42)
    sample_rate = 16_000
    samples = np.zeros(sample_rate, dtype=np.float32)
    noise = rng.normal(0.0, 0.25, sample_rate // 2).astype(np.float32)
    # Emphasize rapid changes to mimic an unvoiced consonant region.
    samples[sample_rate // 4 : 3 * sample_rate // 4] = np.diff(np.pad(noise, (1, 0)))[: sample_rate // 2]

    cues = analyze_waveform(samples, sample_rate)

    validate_cues(cues, duration=1.0)
    non_rest = {cue.shape for cue in cues if cue.shape != "X"}
    assert non_rest
    assert non_rest & {"A", "B", "G", "H"}


def test_waveform_peaks_are_bounded_and_requested_length() -> None:
    samples = np.linspace(-2.0, 2.0, 1000, dtype=np.float32)

    peaks = extract_waveform_peaks(samples, points=125)

    assert len(peaks) == 125
    assert min(peaks) >= 0.0
    assert max(peaks) <= 1.0
    assert max(peaks) == 1.0


def test_continuous_speech_closes_the_mouth_between_syllables() -> None:
    sample_rate = 16_000
    t = np.arange(sample_rate * 3, dtype=np.float32) / sample_rate
    # Four syllables per second that never drop to silence.
    envelope = np.clip(np.sin(2 * np.pi * 4 * t), 0, None) ** 0.7 * 0.8 + 0.08
    samples = envelope * np.sin(2 * np.pi * 170 * t) * ((t > 0.5) & (t < 2.5))

    cues = analyze_waveform(samples, sample_rate)

    validate_cues(cues, duration=3.0)
    shapes = [cue.shape for cue in cues]
    closures = sum(1 for shape in shapes[1:-1] if shape in {"A", "B"})
    assert closures >= 3, shapes


def test_song_mode_ignores_drums_outside_the_vocal_band() -> None:
    sample_rate = 16_000
    t = np.arange(sample_rate * 4, dtype=np.float32) / sample_rate
    drums = np.zeros_like(t)
    hit = np.arange(int(0.12 * sample_rate))
    for beat in np.arange(0.0, 4.0, 0.5):
        start = int(beat * sample_rate)
        drums[start : start + hit.size] += 0.9 * np.exp(-hit / 800) * np.sin(2 * np.pi * 55 * hit / sample_rate)
    vocal_region = (t > 1.2) & (t < 2.8)
    mix = drums + 0.3 * np.sin(2 * np.pi * 300 * t) * vocal_region

    song = analyze_waveform(mix, sample_rate, mode="song")

    validate_cues(song, duration=4.0)
    outside = sum(c.duration for c in song if c.shape != "X" and (c.end <= 1.2 or c.start >= 2.8))
    inside = sum(max(0.0, min(c.end, 2.8) - max(c.start, 1.2)) for c in song if c.shape != "X")
    assert outside < 0.1
    assert inside > 1.0


def test_long_audio_analysis_is_fast() -> None:
    import time

    sample_rate = 16_000
    samples = np.random.default_rng(3).normal(0.0, 0.2, sample_rate * 300).astype(np.float32)

    started = time.perf_counter()
    cues = analyze_waveform(samples, sample_rate)

    assert time.perf_counter() - started < 10.0
    validate_cues(cues, duration=300.0)
