from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from pumpkinsync.cues import validate_cues
from pumpkinsync.models import Cue
from pumpkinsync.tools import (
    ExternalToolError,
    cues_to_tsv,
    parse_cue_sheet,
    parse_rhubarb_json,
    prepare_analysis_audio,
    probe_media,
    run_ffmpeg_with_progress,
)


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


def test_parse_cue_sheet_reads_rhubarb_tsv_with_header() -> None:
    text = "start\tshape\n0.00\tX\n0.20\tB\n0.50\tD\n0.80\tX\n"

    cues = parse_cue_sheet(text, duration=1.0)

    validate_cues(cues, 1.0)
    assert [(cue.start, cue.end, cue.shape) for cue in cues] == [
        (0.0, 0.2, "X"),
        (0.2, 0.5, "B"),
        (0.5, 0.8, "D"),
        (0.8, 1.0, "X"),
    ]


def test_parse_cue_sheet_reads_pumpkinsync_and_rhubarb_json() -> None:
    ours = '{"cues": [{"start": 0, "end": 0.6, "shape": "c"}, {"start": 0.6, "end": 1.0, "shape": "X"}]}'
    rhubarb = '{"mouthCues": [{"start": 0.1, "end": 0.6, "value": "E"}]}'

    assert [cue.shape for cue in parse_cue_sheet(ours, 1.0)] == ["C", "X"]
    assert [cue.shape for cue in parse_cue_sheet(rhubarb, 1.0)] == ["X", "E", "X"]


def test_parse_cue_sheet_reports_problems() -> None:
    for text, message in [("", "empty"), ("{bad", "JSON"), ("0.0\tZ", "shape"), ("x\ty\nnope\tA", "time")]:
        with pytest.raises(ValueError, match=message):
            parse_cue_sheet(text, 1.0)


def test_cues_to_tsv_round_trips() -> None:
    cues = [Cue(0.0, 0.25, "X"), Cue(0.25, 0.75, "F", energy=0.4), Cue(0.75, 1.0, "X")]

    again = parse_cue_sheet(cues_to_tsv(cues), duration=1.0)

    assert [(cue.start, cue.end, cue.shape) for cue in again] == [(c.start, c.end, c.shape) for c in cues]


def test_run_ffmpeg_with_progress_reports_and_raises(tmp_path: Path) -> None:
    output = tmp_path / "tone.wav"
    seen: list[float] = []

    run_ffmpeg_with_progress(
        ["-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", output],
        duration=2.0,
        on_progress=seen.append,
    )

    assert output.is_file()
    assert seen[-1] == 1.0
    with pytest.raises(ExternalToolError, match="ffmpeg failed"):
        run_ffmpeg_with_progress(["-i", tmp_path / "missing.wav", tmp_path / "out.wav"], duration=1.0)
