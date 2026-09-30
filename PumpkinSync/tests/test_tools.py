from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf

from pumpkinsync.cues import validate_cues
from pumpkinsync.tools import parse_rhubarb_json, prepare_analysis_audio, probe_media


def test_parse_rhubarb_json_fills_silence_gaps() -> None:
    payload = {
        "mouthCues": [
            {"start": 0.2, "end": 0.5, "value": "B"},
            {"start": 0.5, "end": 0.8, "value": "D"},
        ]
    }

    cues = parse_rhubarb_json(payload, duration=1.0)

    validate_cues(cues, 1.0)
    assert [(cue.start, cue.end, cue.shape) for cue in cues] == [
        (0.0, 0.2, "X"),
        (0.2, 0.5, "B"),
        (0.5, 0.8, "D"),
        (0.8, 1.0, "X"),
    ]


def test_prepare_analysis_audio_creates_mono_16khz_wav(tmp_path: Path) -> None:
    source = tmp_path / "stereo.wav"
    destination = tmp_path / "analysis.wav"
    sample_rate = 48_000
    t = np.arange(sample_rate, dtype=np.float32) / sample_rate
    stereo = np.column_stack(
        [0.25 * np.sin(2 * np.pi * 220 * t), 0.25 * np.sin(2 * np.pi * 330 * t)]
    )
    sf.write(source, stereo, sample_rate)

    prepare_analysis_audio(source, destination)

    data, actual_rate = sf.read(destination, always_2d=True)
    assert actual_rate == 16_000
    assert data.shape == (16_000, 1)
    metadata = probe_media(destination)
    assert abs(metadata.duration - 1.0) < 0.02
    assert metadata.audio_channels == 1
