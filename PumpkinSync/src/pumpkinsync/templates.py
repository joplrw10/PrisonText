from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import Cue, VALID_SHAPES

_EXPRESSIONS = ("normal", "squint", "wide")
_POSES = ("up", "center", "down")
_POSE_PATTERN = ("center", "up", "center", "down")


@dataclass(frozen=True, slots=True)
class TemplateManifest:
    id: str
    name: str
    width: int
    height: int
    default_fps: int
    root: Path
    frames: dict[str, dict[str, dict[str, Path]]]

    @classmethod
    def load(cls, path: Path | str) -> "TemplateManifest":
        manifest_path = Path(path).resolve()
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        root = manifest_path.parent.resolve()
        width = int(payload["width"])
        height = int(payload["height"])
        fps = int(payload.get("default_fps", 30))
        if width <= 0 or height <= 0 or fps <= 0:
            raise ValueError("template dimensions and frame rate must be positive")

        raw_frames: dict[str, Any] = payload.get("frames", {})
        resolved: dict[str, dict[str, dict[str, Path]]] = {}
        for shape in VALID_SHAPES:
            if shape not in raw_frames:
                raise ValueError(f"template is missing mouth shape {shape}")
            resolved[shape] = {}
            for expression, poses in raw_frames[shape].items():
                resolved[shape][expression] = {}
                for pose, relative in poses.items():
                    candidate = (root / str(relative)).resolve()
                    if not candidate.is_relative_to(root):
                        raise ValueError("template frame path must remain inside the template folder")
                    if not candidate.is_file():
                        raise FileNotFoundError(f"template frame was not found: {candidate}")
                    resolved[shape][expression][pose] = candidate
            if "normal" not in resolved[shape] or "center" not in resolved[shape]["normal"]:
                raise ValueError(f"shape {shape} requires a normal/center fallback frame")

        return cls(
            id=str(payload["id"]),
            name=str(payload.get("name", payload["id"])),
            width=width,
            height=height,
            default_fps=fps,
            root=root,
            frames=resolved,
        )

    def public_dict(self, static_prefix: str = "/template") -> dict[str, Any]:
        frame_urls: dict[str, dict[str, dict[str, str]]] = {}
        for shape, expressions in self.frames.items():
            frame_urls[shape] = {}
            for expression, poses in expressions.items():
                frame_urls[shape][expression] = {}
                for pose, path in poses.items():
                    relative = path.relative_to(self.root).as_posix()
                    frame_urls[shape][expression][pose] = f"{static_prefix}/{relative}"
        return {
            "id": self.id,
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "default_fps": self.default_fps,
            "frames": frame_urls,
        }


def _select_expression(cue: Cue, cue_index: int) -> str:
    if cue.shape == "X":
        return "normal"
    if cue.shape == "D" or cue.energy >= 0.82:
        return "wide"
    if cue.energy >= 0.52 and cue_index % 5 == 0:
        return "squint"
    return "normal"


def choose_frame(template: TemplateManifest, cue: Cue, cue_index: int) -> Path:
    shape_frames = template.frames.get(cue.shape)
    if shape_frames is None:
        shape_frames = template.frames["X"]
    expression = _select_expression(cue, cue_index)
    expression_frames = shape_frames.get(expression) or shape_frames["normal"]
    pose = "center" if cue.shape == "X" else _POSE_PATTERN[cue_index % len(_POSE_PATTERN)]
    return expression_frames.get(pose) or expression_frames.get("center") or next(iter(expression_frames.values()))
