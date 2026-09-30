from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

VALID_SHAPES: tuple[str, ...] = ("A", "B", "C", "D", "E", "F", "G", "H", "X")


@dataclass(slots=True)
class Cue:
    start: float
    end: float
    shape: str
    energy: float = 0.0
    confidence: float = 1.0

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["start"] = round(float(self.start), 6)
        data["end"] = round(float(self.end), 6)
        data["energy"] = round(float(self.energy), 6)
        data["confidence"] = round(float(self.confidence), 6)
        return data

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Cue":
        return cls(
            start=float(value["start"]),
            end=float(value["end"]),
            shape=str(value["shape"]).upper(),
            energy=float(value.get("energy", 0.0)),
            confidence=float(value.get("confidence", 1.0)),
        )


@dataclass(slots=True)
class AnalysisResult:
    duration: float
    cues: list[Cue]
    waveform: list[float]
    analyzer: str
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "duration": round(float(self.duration), 6),
            "cues": [cue.to_dict() for cue in self.cues],
            "waveform": [round(float(point), 6) for point in self.waveform],
            "analyzer": self.analyzer,
            "warnings": list(self.warnings),
        }
