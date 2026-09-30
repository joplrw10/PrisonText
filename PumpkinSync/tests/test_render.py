from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw

from pumpkinsync.models import Cue
from pumpkinsync.render import build_concat_manifest, render_project
from pumpkinsync.templates import TemplateManifest, choose_frame
from pumpkinsync.tools import probe_media


SHAPES = "ABCDEFGHX"
EXPRESSIONS = ("normal", "squint", "wide")
POSES = ("up", "center", "down")


def make_template(root: Path, width: int = 320, height: int = 180) -> TemplateManifest:
    frames: dict[str, dict[str, dict[str, str]]] = {}
    frame_dir = root / "frames"
    frame_dir.mkdir(parents=True)
    for shape_index, shape in enumerate(SHAPES):
        frames[shape] = {}
        for expression in EXPRESSIONS:
            frames[shape][expression] = {}
            for pose in POSES:
                relative = f"frames/{shape}_{expression}_{pose}.png"
                image = Image.new("RGB", (width, height), "black")
                draw = ImageDraw.Draw(image)
                draw.rectangle((100, 50, 220, 130), outline="white", width=3)
                draw.text((150, 85), shape, fill="white")
                image.save(root / relative)
                frames[shape][expression][pose] = relative
    payload = {
        "id": "test-template",
        "name": "Test Template",
        "width": width,
        "height": height,
        "default_fps": 30,
        "frames": frames,
    }
    (root / "template.json").write_text(json.dumps(payload), encoding="utf-8")
    return TemplateManifest.load(root / "template.json")


def test_choose_frame_is_deterministic_and_respects_energy(tmp_path: Path) -> None:
    template = make_template(tmp_path / "template")

    rest = choose_frame(template, Cue(0.0, 0.2, "X", energy=1.0), cue_index=7)
    strong = choose_frame(template, Cue(0.2, 0.4, "D", energy=0.95), cue_index=1)
    accented = choose_frame(template, Cue(0.4, 0.6, "C", energy=0.65), cue_index=5)

    assert rest.name == "X_normal_center.png"
    assert strong.name == "D_wide_up.png"
    assert accented.name == "C_squint_up.png"


def test_build_concat_manifest_preserves_cue_durations(tmp_path: Path) -> None:
    template = make_template(tmp_path / "template")
    cues = [Cue(0.0, 0.3, "X"), Cue(0.3, 1.0, "D", energy=0.9)]
    manifest_path = tmp_path / "frames.ffconcat"

    selected = build_concat_manifest(cues, template, manifest_path)

    text = manifest_path.read_text(encoding="utf-8")
    assert text.startswith("ffconcat version 1.0\n")
    assert "duration 0.300000" in text
    assert "duration 0.700000" in text
    assert len(selected) == 2
    # FFmpeg's concat demuxer needs the final still repeated after its duration.
    assert text.strip().splitlines()[-1].startswith("file ")
    assert text.count("file ") == 3


def test_render_project_creates_compatible_audio_video(tmp_path: Path) -> None:
    template = make_template(tmp_path / "template")
    audio = tmp_path / "audio.wav"
    sample_rate = 48_000
    t = np.arange(sample_rate, dtype=np.float32) / sample_rate
    sf.write(audio, 0.2 * np.sin(2 * np.pi * 220 * t), sample_rate)
    output = tmp_path / "rendered.mp4"
    cues = [Cue(0.0, 0.25, "X"), Cue(0.25, 0.75, "C", energy=0.6), Cue(0.75, 1.0, "X")]

    render_project(
        audio_path=audio,
        cues=cues,
        template=template,
        output_path=output,
        width=640,
        height=360,
        fps=30,
        preview=True,
    )

    media = probe_media(output)
    assert media.video_codec == "h264"
    assert (media.width, media.height) == (640, 360)
    assert media.frame_rate == 30.0
    assert media.audio_codec == "aac"
    assert media.audio_sample_rate == 48_000
    assert media.audio_channels == 2
    assert abs(media.duration - 1.0) < 0.08


def test_render_applies_mouth_lead_and_reports_progress(tmp_path: Path) -> None:
    template = make_template(tmp_path / "template")
    audio = tmp_path / "audio.wav"
    sample_rate = 48_000
    t = np.arange(sample_rate * 2, dtype=np.float32) / sample_rate
    sf.write(audio, 0.2 * np.sin(2 * np.pi * 220 * t), sample_rate)
    output = tmp_path / "exports" / "lead.mp4"
    cues = [Cue(0.0, 1.0, "X"), Cue(1.0, 2.0, "D", energy=0.9)]
    seen: list[float] = []

    render_project(
        audio_path=audio,
        cues=cues,
        template=template,
        output_path=output,
        width=320,
        height=180,
        preview=True,
        lead_seconds=0.5,
        on_progress=seen.append,
    )

    assert seen and seen[-1] == 1.0
    assert [path.name for path in output.parent.iterdir()] == ["lead.mp4"]
    frame = tmp_path / "frame.png"
    import subprocess

    # With a 0.5 s lead the open D mouth is already showing at 0.75 s.
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", "0.75", "-i", str(output), "-frames:v", "1", str(frame)],
        check=True,
    )
    shown = np.asarray(Image.open(frame).convert("L"), dtype=np.int16)
    expected_d = np.asarray(Image.open(template.frames["D"]["wide"]["center"]).convert("L").resize((320, 180)), dtype=np.int16)
    expected_x = np.asarray(Image.open(template.frames["X"]["normal"]["center"]).convert("L").resize((320, 180)), dtype=np.int16)
    assert np.abs(shown - expected_d).mean() < np.abs(shown - expected_x).mean()
