from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .cues import merge_short_cues, validate_cues
from .models import Cue, VALID_SHAPES


class ExternalToolError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MediaProbe:
    duration: float
    video_codec: str | None = None
    width: int | None = None
    height: int | None = None
    frame_rate: float | None = None
    audio_codec: str | None = None
    audio_sample_rate: int | None = None
    audio_channels: int | None = None


def _run(
    command: Iterable[str | Path],
    *,
    timeout: float | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    args = [str(part) for part in command]
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            text=True,
            capture_output=True,
            check=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise ExternalToolError(f"required executable was not found: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ExternalToolError(f"command timed out: {args[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "unknown error").strip()
        raise ExternalToolError(f"{args[0]} failed: {detail[-1200:]}") from exc


def run_command(
    command: Iterable[str | Path],
    *,
    timeout: float | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a required local media command with consistent errors."""

    return _run(command, timeout=timeout, cwd=cwd)


def ensure_ffmpeg() -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise ExternalToolError("FFmpeg and FFprobe must both be available on PATH")


def prepare_analysis_audio(source: Path, destination: Path, sample_rate: int = 16_000) -> Path:
    ensure_ffmpeg()
    destination.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            source,
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
            destination,
        ],
        timeout=600,
    )
    if not destination.is_file() or destination.stat().st_size < 44:
        raise ExternalToolError("FFmpeg did not create a usable analysis WAV")
    return destination


def _ratio(value: str | None) -> float | None:
    if not value or value in {"0/0", "N/A"}:
        return None
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        denominator_value = float(denominator)
        return float(numerator) / denominator_value if denominator_value else None
    return float(value)


def probe_media(path: Path) -> MediaProbe:
    ensure_ffmpeg()
    completed = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,codec_name,width,height,avg_frame_rate,sample_rate,channels",
            "-of",
            "json",
            path,
        ],
        timeout=120,
    )
    data = json.loads(completed.stdout)
    duration = float(data.get("format", {}).get("duration") or 0.0)
    values: dict[str, Any] = {"duration": duration}
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and values.get("video_codec") is None:
            values.update(
                video_codec=stream.get("codec_name"),
                width=int(stream["width"]) if stream.get("width") else None,
                height=int(stream["height"]) if stream.get("height") else None,
                frame_rate=_ratio(stream.get("avg_frame_rate")),
            )
        elif stream.get("codec_type") == "audio" and values.get("audio_codec") is None:
            values.update(
                audio_codec=stream.get("codec_name"),
                audio_sample_rate=int(stream["sample_rate"]) if stream.get("sample_rate") else None,
                audio_channels=int(stream["channels"]) if stream.get("channels") else None,
            )
    return MediaProbe(**values)


def parse_rhubarb_json(payload: dict[str, Any], duration: float) -> list[Cue]:
    if duration <= 0:
        raise ValueError("duration must be positive")
    raw = sorted(payload.get("mouthCues", []), key=lambda item: float(item["start"]))
    result: list[Cue] = []
    cursor = 0.0
    energy_by_shape = {"X": 0.0, "A": 0.12, "B": 0.28, "C": 0.52, "D": 0.95, "E": 0.63, "F": 0.40, "G": 0.35, "H": 0.48}
    for item in raw:
        start = max(0.0, min(duration, float(item["start"])))
        end = max(start, min(duration, float(item["end"])))
        shape = str(item.get("value", "X")).upper()
        if shape not in VALID_SHAPES:
            raise ValueError(f"unknown Rhubarb mouth shape {shape!r}")
        if start > cursor + 1e-6:
            result.append(Cue(cursor, start, "X", energy=0.0, confidence=1.0))
        start = max(start, cursor)
        if end > start + 1e-6:
            result.append(Cue(start, end, shape, energy=energy_by_shape[shape], confidence=0.95))
            cursor = end
    if cursor < duration:
        result.append(Cue(cursor, duration, "X", energy=0.0, confidence=1.0))
    if not result:
        result = [Cue(0.0, duration, "X", energy=0.0, confidence=1.0)]
    result = merge_short_cues(result, min_duration=0.04)
    result[0].start = 0.0
    result[-1].end = duration
    validate_cues(result, duration)
    return result


def find_rhubarb(explicit: Path | None = None, project_root: Path | None = None) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(explicit)
    if os.environ.get("PUMPKINSYNC_RHUBARB"):
        candidates.append(Path(os.environ["PUMPKINSYNC_RHUBARB"]))
    if project_root:
        candidates.extend(
            [
                project_root / "tools" / "rhubarb" / "rhubarb.exe",
                project_root / "tools" / "rhubarb" / "rhubarb",
            ]
        )
    located = shutil.which("rhubarb") or shutil.which("rhubarb.exe")
    if located:
        candidates.append(Path(located))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def run_rhubarb(
    audio_wav: Path,
    *,
    duration: float,
    transcript_file: Path | None = None,
    executable: Path | None = None,
    project_root: Path | None = None,
) -> list[Cue]:
    binary = find_rhubarb(executable, project_root)
    if binary is None:
        raise ExternalToolError("Rhubarb Lip Sync was not found")
    with tempfile.TemporaryDirectory(prefix="pumpkinsync-rhubarb-") as temporary:
        output = Path(temporary) / "cues.json"
        command: list[str | Path] = [binary, "-f", "json", "-o", output]
        if transcript_file and transcript_file.is_file():
            command.extend(["--dialogFile", transcript_file])
        command.append(audio_wav)
        _run(command, timeout=900)
        payload = json.loads(output.read_text(encoding="utf-8"))
    return parse_rhubarb_json(payload, duration)


def try_demucs_vocals(source: Path, work_dir: Path) -> tuple[Path | None, str | None]:
    """Return a vocal stem when optional Demucs is usable; otherwise a warning."""

    if importlib.util.find_spec("demucs") is None:
        return None, "Demucs is not installed; analyzed the original audio instead."
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        _run(
            [
                sys.executable,
                "-m",
                "demucs",
                "--two-stems=vocals",
                "--name=htdemucs",
                "--out",
                work_dir,
                source,
            ],
            timeout=7200,
        )
    except ExternalToolError as exc:
        return None, f"Vocal isolation failed; analyzed the original audio instead. {exc}"
    candidates = sorted(work_dir.rglob("vocals.wav"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not candidates:
        return None, "Demucs finished without producing vocals.wav; analyzed the original audio instead."
    return candidates[0], None
