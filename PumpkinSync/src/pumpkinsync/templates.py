from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .models import Cue, VALID_SHAPES

_EXPRESSIONS = ("normal", "squint", "wide")
_POSES = ("up", "center", "down")
_POSE_PATTERN = ("center", "up", "center", "down")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SERVABLE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})


@dataclass(frozen=True, slots=True)
class TemplateManifest:
    id: str
    name: str
    width: int
    height: int
    default_fps: int
    root: Path
    frames: dict[str, dict[str, dict[str, Path]]]
    preview: Path | None = None
    private: bool = False
    source: str = "custom"

    @classmethod
    def load(cls, path: Path | str, *, source: str = "custom") -> "TemplateManifest":
        manifest_path = Path(path).resolve()
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        root = manifest_path.parent.resolve()
        template_id = str(payload["id"])
        if not _SAFE_ID.fullmatch(template_id):
            raise ValueError(f"template id {template_id!r} must use letters, digits, '.', '_' or '-'")
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
                    candidate = _inside(root, relative)
                    if not candidate.is_file():
                        raise FileNotFoundError(f"template frame was not found: {candidate}")
                    resolved[shape][expression][pose] = candidate
            if "normal" not in resolved[shape] or "center" not in resolved[shape]["normal"]:
                raise ValueError(f"shape {shape} requires a normal/center fallback frame")

        preview: Path | None = None
        preview_name = payload.get("preview")
        candidates = [preview_name] if preview_name else ["preview.jpg", "preview.png"]
        for candidate_name in candidates:
            candidate = _inside(root, candidate_name)
            if candidate.is_file():
                preview = candidate
                break
        if preview is None:
            preview = resolved["C"]["normal"]["center"]

        return cls(
            id=template_id,
            name=str(payload.get("name", template_id)),
            width=width,
            height=height,
            default_fps=fps,
            root=root,
            frames=resolved,
            preview=preview,
            private=bool(payload.get("private_user_asset", False)),
            source=source,
        )

    def url_prefix(self) -> str:
        return f"/templates/{self.id}"

    def url_for(self, path: Path) -> str:
        return f"{self.url_prefix()}/{path.relative_to(self.root).as_posix()}"

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "private": self.private,
            "source": self.source,
            "preview_url": self.url_for(self.preview) if self.preview else None,
        }

    def public_dict(self, static_prefix: str | None = None) -> dict[str, Any]:
        prefix = static_prefix or self.url_prefix()
        frame_urls: dict[str, dict[str, dict[str, str]]] = {}
        for shape, expressions in self.frames.items():
            frame_urls[shape] = {}
            for expression, poses in expressions.items():
                frame_urls[shape][expression] = {}
                for pose, path in poses.items():
                    relative = path.relative_to(self.root).as_posix()
                    frame_urls[shape][expression][pose] = f"{prefix}/{relative}"
        return {**self.summary(), "default_fps": self.default_fps, "frames": frame_urls}

    def resolve_asset(self, relative: str) -> Path:
        """Return a servable image inside this template, or raise ValueError."""

        candidate = _inside(self.root, relative)
        if candidate.suffix.lower() not in SERVABLE_SUFFIXES or not candidate.is_file():
            raise ValueError("template asset was not found")
        return candidate


def _inside(root: Path, relative: Any) -> Path:
    candidate = (root / str(relative)).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("template paths must remain inside the template folder")
    return candidate


@dataclass(slots=True)
class TemplateLibrary:
    """Every face template available to this PumpkinSync instance."""

    templates: dict[str, TemplateManifest] = field(default_factory=dict)
    default_id: str | None = None
    errors: list[str] = field(default_factory=list)

    def add(self, template: TemplateManifest) -> bool:
        if template.id in self.templates:
            existing = self.templates[template.id]
            if existing.root == template.root:
                return False  # the same folder reached through two search paths
            self.errors.append(
                f"Skipped duplicate template id {template.id!r} at {template.root}; "
                f"already loaded from {existing.root}."
            )
            return False
        self.templates[template.id] = template
        if self.default_id is None:
            self.default_id = template.id
        return True

    def try_load(self, manifest: Path, *, source: str) -> TemplateManifest | None:
        try:
            template = TemplateManifest.load(manifest, source=source)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            self.errors.append(f"Could not load template {manifest}: {exc}")
            return None
        return template if self.add(template) else None

    @property
    def default(self) -> TemplateManifest:
        if self.default_id is None:
            raise LookupError("no face templates are available")
        return self.templates[self.default_id]

    def get(self, template_id: str | None) -> TemplateManifest:
        """Return the requested template, falling back to the default."""

        if template_id and template_id in self.templates:
            return self.templates[template_id]
        return self.default

    def __contains__(self, template_id: object) -> bool:
        return template_id in self.templates

    def summaries(self) -> list[dict[str, Any]]:
        return [
            {**template.summary(), "default": template.id == self.default_id}
            for template in self.templates.values()
        ]

    @classmethod
    def discover(
        cls,
        *,
        explicit: Iterable[Path] = (),
        package_private: Path | None = None,
        user_dirs: Iterable[Path] = (),
        builtin: Path | None = None,
    ) -> "TemplateLibrary":
        """Load templates in priority order; the first one found is the default.

        Order: explicit paths (CLI/env), the private template installed inside
        the package, folders under ``data/templates``, then the built-in pumpkin.
        """

        library = cls()
        for manifest in explicit:
            library.try_load(Path(manifest), source="custom")
        if package_private is not None and package_private.is_file():
            library.try_load(package_private, source="private")
        for directory in user_dirs:
            directory = Path(directory)
            if not directory.is_dir():
                continue
            for manifest in sorted(directory.glob("*/template.json")):
                library.try_load(manifest, source="custom")
        if builtin is not None:
            library.try_load(builtin, source="builtin")
        return library


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
