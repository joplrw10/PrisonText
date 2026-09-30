from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

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


def run_ffmpeg_with_progress(
    arguments: Iterable[str | Path],
    *,
    duration: float,
    on_progress: Callable[[float], None] | None = None,
    timeout: float | None = None,
) -> None:
    """Run ``ffmpeg <arguments>`` and report 0..1 progress from its -progress feed."""

    args = ["ffmpeg", "-progress", "pipe:1", "-nostats", *[str(part) for part in arguments]]
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stderr:
        try:
            process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=stderr, text=True)
        except FileNotFoundError as exc:
            raise ExternalToolError("required executable was not found: ffmpeg") from exc
        timed_out = threading.Event()
        watchdog: threading.Timer | None = None
        if timeout:
            def _expire() -> None:
                timed_out.set()
                process.kill()

            watchdog = threading.Timer(timeout, _expire)
            watchdog.daemon = True
            watchdog.start()
        last_report = 0.0
        try:
            assert process.stdout is not None
            for line in process.stdout:
                key, _, value = line.strip().partition("=")
                # out_time_ms is (despite its name) microseconds, like out_time_us.
                if key in {"out_time_us", "out_time_ms"} and on_progress and duration > 0:
                    try:
                        seconds = int(value) / 1_000_000
                    except ValueError:
                        continue
                    now = time.monotonic()
                    if now - last_report > 0.2:
                        last_report = now
                        on_progress(max(0.0, min(1.0, seconds / duration)))
            process.wait()
        finally:
            if watchdog:
                watchdog.cancel()
            if process.poll() is None:
                process.kill()
                process.wait()
        if timed_out.is_set():
            raise ExternalToolError("command timed out: ffmpeg")
        if process.returncode != 0:
            stderr.seek(0)
            detail = stderr.read().strip() or "unknown error"
            raise ExternalToolError(f"ffmpeg failed: {detail[-1200:]}")
    if on_progress:
        on_progress(1.0)


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


def prepare_playback_audio(source: Path, destination: Path) -> Path:
    """Decode audio the browser may not support into a stereo 44.1 kHz WAV."""

    ensure_ffmpeg()
    destination.parent.mkdir(parents=True, exist_ok=True)
    _run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", source, "-vn",
         "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", destination],
        timeout=900,
    )
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


def parse_cue_sheet(text: str, duration: float) -> list[Cue]:
    """Read cues exported by PumpkinSync or Rhubarb (JSON or TSV).

    Accepted forms: ``{"mouthCues": [...]}`` (Rhubarb JSON), ``{"cues": [...]}``
    (PumpkinSync JSON), or tab/space separated ``start<TAB>shape`` lines
    (Rhubarb TSV), where each cue ends where the next begins.
    """

    stripped = text.strip().lstrip("\ufeff")
    if not stripped:
        raise ValueError("the cue sheet is empty")
    if stripped[0] in "{[":
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"the cue sheet is not valid JSON: {exc.msg}") from exc
        if isinstance(payload, dict) and "mouthCues" in payload:
            return parse_rhubarb_json(payload, duration)
        raw = payload.get("cues") if isinstance(payload, dict) else payload
        if not isinstance(raw, list):
            raise ValueError("JSON cue sheets need a 'cues' or 'mouthCues' list")
        try:
            items = [
                {"start": float(item["start"]), "end": float(item["end"]), "value": str(item.get("shape", item.get("value")))}
                for item in raw
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("each JSON cue needs start, end and shape") from exc
        return parse_rhubarb_json({"mouthCues": items}, duration)

    rows: list[tuple[float, str]] = []
    for number, line in enumerate(stripped.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.replace(",", "\t").split()
        if len(parts) < 2:
            raise ValueError(f"line {number} needs a start time and a mouth shape")
        try:
            start = float(parts[0])
        except ValueError as exc:
            if number == 1:
                continue  # header row
            raise ValueError(f"line {number} has an invalid time {parts[0]!r}") from exc
        rows.append((start, parts[1].upper()))
    if not rows:
        raise ValueError("the cue sheet contains no cues")
    rows.sort(key=lambda row: row[0])
    items = []
    for index, (start, shape) in enumerate(rows):
        end = rows[index + 1][0] if index + 1 < len(rows) else duration
        if end > start:
            items.append({"start": start, "end": end, "value": shape})
    return parse_rhubarb_json({"mouthCues": items}, duration)


def cues_to_tsv(cues: Iterable[Cue]) -> str:
    """Rhubarb-compatible TSV: one ``start<TAB>shape`` line per cue."""

    values = list(cues)
    lines = [f"{cue.start:.3f}\t{cue.shape}" for cue in values]
    if values:
        # A closing rest line marks where the final cue ends.
        lines.append(f"{values[-1].end:.3f}\tX")
    return "\n".join(lines) + "\n"


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
    recognizer: str | None = None,
) -> list[Cue]:
    """Run Rhubarb and return a complete cue timeline.

    ``recognizer`` may be ``"phonetic"`` (better for singing and non-English)
    or ``"pocketSphinx"`` (English speech, uses the transcript). Older Rhubarb
    builds without ``--recognizer`` are retried without the flag.
    """

    binary = find_rhubarb(executable, project_root)
    if binary is None:
        raise ExternalToolError("Rhubarb Lip Sync was not found")
    with tempfile.TemporaryDirectory(prefix="pumpkinsync-rhubarb-") as temporary:
        output = Path(temporary) / "cues.json"
        base: list[str | Path] = [binary, "-f", "json", "-o", output]
        if transcript_file and transcript_file.is_file():
            base.extend(["--dialogFile", transcript_file])
        attempts = [base + ["--recognizer", recognizer] if recognizer else base]
        if recognizer:
            attempts.append(base)
        last_error: ExternalToolError | None = None
        for command in attempts:
            try:
                _run([*command, audio_wav], timeout=1800, cwd=binary.parent)
                break
            except ExternalToolError as exc:
                last_error = exc
        else:
            assert last_error is not None
            raise last_error
        payload = json.loads(output.read_text(encoding="utf-8"))
    return parse_rhubarb_json(payload, duration)


def try_demucs_vocals(source: Path, work_dir: Path) -> tuple[Path | None, str | None]:
    """Return a vocal stem when optional Demucs is usable; otherwise a warning."""

    cached = _latest_vocals(work_dir)
    if cached is not None and cached.stat().st_mtime >= source.stat().st_mtime:
        # Re-analysis reuses the stem; separation is by far the slowest step.
        return cached, None
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
    vocals = _latest_vocals(work_dir)
    if vocals is None:
        return None, "Demucs finished without producing vocals.wav; analyzed the original audio instead."
    return vocals, None


def _latest_vocals(work_dir: Path) -> Path | None:
    if not work_dir.is_dir():
        return None
    candidates = sorted(work_dir.rglob("vocals.wav"), key=lambda item: item.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None
