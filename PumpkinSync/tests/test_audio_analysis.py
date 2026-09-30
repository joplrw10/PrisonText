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
