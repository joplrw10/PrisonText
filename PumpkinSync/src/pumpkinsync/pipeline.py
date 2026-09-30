"""Audio-to-cue analysis shared by new uploads and re-analysis."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

import soundfile as sf

from .audio import analyze_waveform, extract_waveform_peaks, waveform_points_for
from .cues import validate_cues
from .models import Cue
from .projects import ProjectRecord, ProjectStore
from .tools import ExternalToolError, find_rhubarb, prepare_analysis_audio, run_rhubarb, try_demucs_vocals

MODES = ("skit", "song")
ANALYZERS = ("auto", "builtin", "rhubarb")
INTENSITY_RANGE = (0.4, 1.8)
MAX_TRANSCRIPT_CHARS = 200_000

Progress = Callable[[float, str], None]


@dataclass(frozen=True, slots=True)
class AnalysisSettings:
    mode: str = "skit"
    analyzer: str = "auto"
    intensity: float = 1.0
    separate_vocals: bool = False

    @classmethod
    def build(
        cls,
        *,
        mode: Any = "skit",
        analyzer: Any = "auto",
        intensity: Any = 1.0,
        separate_vocals: Any = False,
    ) -> "AnalysisSettings":
        mode = str(mode).lower().strip()
        analyzer = str(analyzer).lower().strip()
        if mode not in MODES:
            raise ValueError("mode must be 'skit' or 'song'")
        if analyzer not in ANALYZERS:
            raise ValueError("analyzer must be auto, builtin, or rhubarb")
        try:
            intensity_value = float(intensity)
        except (TypeError, ValueError) as exc:
            raise ValueError("intensity must be a number") from exc
        if not INTENSITY_RANGE[0] <= intensity_value <= INTENSITY_RANGE[1]:
            raise ValueError("intensity must be between 0.4 and 1.8")
        if isinstance(separate_vocals, str):
            separate = separate_vocals.strip().lower() in {"1", "true", "yes", "on"}
        else:
            separate = bool(separate_vocals)
        return cls(mode=mode, analyzer=analyzer, intensity=intensity_value, separate_vocals=separate and mode == "song")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def write_transcript(project: ProjectRecord, transcript: str | None) -> None:
    """Store, replace or (for an empty string) remove the project's script/lyrics."""

    if transcript is None:
        return
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        raise ValueError("the script or lyrics are too long")
    path = project.transcript_file
    if transcript.strip():
        path.write_text(transcript.strip() + "\n", encoding="utf-8")
    elif path.exists():
        path.unlink()


def run_analysis(
    store: ProjectStore,
    project: ProjectRecord,
    settings: AnalysisSettings,
    *,
    application_root: Path | None,
    progress: Progress | None = None,
) -> ProjectRecord:
    """Analyze the project's source audio and write cues/waveform files."""

    def report(fraction: float, message: str) -> None:
        if progress:
            progress(fraction, message)

    warnings: list[str] = []
    analysis_source = project.source_file
    if settings.mode == "song" and settings.separate_vocals:
        report(0.05, "Isolating vocals with Demucs (this can take several minutes)")
        vocal_source, warning = try_demucs_vocals(project.source_file, project.root / "demucs")
        if warning:
            warnings.append(warning)
        if vocal_source:
            analysis_source = vocal_source

    report(0.55, "Decoding audio")
    prepare_analysis_audio(analysis_source, project.analysis_file)
    samples, sample_rate = sf.read(project.analysis_file, dtype="float32", always_2d=False)
    duration = len(samples) / float(sample_rate)
    if duration <= 0:
        raise ValueError("the audio contains no playable samples")

    transcript_path = project.transcript_file if project.transcript_file.is_file() else None
    used_analyzer = "builtin"
    cues: list[Cue] | None = None
    if settings.analyzer in {"auto", "rhubarb"}:
        rhubarb = find_rhubarb(project_root=application_root)
        if rhubarb is None and settings.analyzer == "rhubarb":
            raise ValueError("Rhubarb was selected but its executable was not found")
        if rhubarb is not None:
            report(0.65, "Recognizing mouth shapes with Rhubarb")
            # Rhubarb's speech recognizer expects English speech; its phonetic
            # recognizer copes better with singing unless lyrics are supplied.
            recognizer = "phonetic" if settings.mode == "song" and transcript_path is None else None
            try:
                cues = run_rhubarb(
                    project.analysis_file,
                    duration=duration,
                    transcript_file=transcript_path,
                    executable=rhubarb,
                    recognizer=recognizer,
                )
                used_analyzer = "rhubarb"
            except (ExternalToolError, ValueError) as exc:
                if settings.analyzer == "rhubarb":
                    raise
                warnings.append(f"Rhubarb failed; used the built-in analyzer instead. {exc}")
    if cues is None:
        report(0.75, "Analyzing mouth movement")
        cues = analyze_waveform(samples, sample_rate, intensity=settings.intensity, mode=settings.mode)

    report(0.92, "Saving timeline")
    validate_cues(cues, duration, tolerance=0.08)
    waveform = extract_waveform_peaks(samples, points=waveform_points_for(duration))
    store.write_json(
        project,
        "cues.json",
        {"duration": duration, "analyzer": used_analyzer, "cues": [cue.to_dict() for cue in cues]},
    )
    store.write_json(project, "waveform.json", {"waveform": waveform})
    settings_payload = {**project.metadata.get("settings", {}), **settings.to_dict()}
    return store.update_metadata(
        project,
        status="ready",
        duration=duration,
        analyzer=used_analyzer,
        warnings=warnings,
        settings=settings_payload,
        error=None,
    )
