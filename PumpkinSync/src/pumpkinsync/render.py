from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Iterable

from .cues import validate_cues
from .models import Cue
from .templates import TemplateManifest, choose_frame
from .tools import ensure_ffmpeg, probe_media, run_command


def _concat_quote(path: Path) -> str:
    # FFconcat accepts backslash escaping inside a single-quoted path.
    return "'" + str(path.resolve()).replace("\\", "/").replace("'", "'\\''") + "'"


def build_concat_manifest(
    cues: Iterable[Cue],
    template: TemplateManifest,
    manifest_path: Path,
) -> list[Path]:
    values = list(cues)
    if not values:
        raise ValueError("at least one cue is required")
    selected = [choose_frame(template, cue, index) for index, cue in enumerate(values)]
    lines = ["ffconcat version 1.0"]
    for cue, frame in zip(values, selected, strict=True):
        lines.append(f"file {_concat_quote(frame)}")
        lines.append(f"duration {cue.duration:.6f}")
    # Without a final repeated still, concat ignores the final duration directive.
    lines.append(f"file {_concat_quote(selected[-1])}")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return selected


def render_project(
    *,
    audio_path: Path,
    cues: Iterable[Cue],
    template: TemplateManifest,
    output_path: Path,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    preview: bool = False,
) -> Path:
    if width <= 0 or height <= 0 or fps <= 0:
        raise ValueError("render dimensions and frame rate must be positive")
    ensure_ffmpeg()
    audio_probe = probe_media(audio_path)
    values = list(cues)
    validate_cues(values, audio_probe.duration, tolerance=0.08)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="pumpkinsync-render-", dir=output_path.parent) as temporary:
        concat_path = Path(temporary) / "frames.ffconcat"
        build_concat_manifest(values, template, concat_path)
        preset = "veryfast" if preview else "medium"
        crf = "25" if preview else "18"
        audio_bitrate = "192k" if preview else "256k"
        filter_graph = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p"
        )
        run_command(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                concat_path,
                "-i",
                audio_path,
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-vf",
                filter_graph,
                "-r",
                str(fps),
                "-fps_mode",
                "cfr",
                "-c:v",
                "libx264",
                "-preset",
                preset,
                "-crf",
                crf,
                "-profile:v",
                "high",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                audio_bitrate,
                "-ar",
                "48000",
                "-ac",
                "2",
                "-movflags",
                "+faststart",
                "-shortest",
                output_path,
            ],
            timeout=7200,
        )
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError("render did not create an output file")
    return output_path
