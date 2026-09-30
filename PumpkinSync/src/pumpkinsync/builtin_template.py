"""Procedurally drawn jack-o'-lantern face template.

PumpkinSync ships no third-party artwork. This module draws an original carved
pumpkin face (9 mouth shapes x 3 expressions x 3 head poses) with Pillow and
caches the frames on disk, so the editor always works even when no private
template has been installed.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from .models import VALID_SHAPES

TEMPLATE_ID = "builtin-pumpkin"
TEMPLATE_NAME = "Classic Jack-o'-Lantern (built-in)"
# Bump whenever the drawing changes so cached frames are regenerated.
GENERATOR_VERSION = 1

CANVAS = (1920, 1080)
CENTER_X = CANVAS[0] / 2.0
NOSE = (CENTER_X, 530.0)
CANDLE = (CENTER_X, 660.0)
# Everything carved (plus its glow) lies inside this box, so the expensive
# lighting work only touches about a quarter of the canvas.
FACE_BOX = (560, 280, 1360, 900)

EXPRESSIONS = ("normal", "squint", "wide")
POSES = {
    "up": (1.010, -7.0),
    "center": (1.000, 0.0),
    "down": (0.992, 7.0),
}

Point = tuple[float, float]

# name: (half width, upper-edge sag, lower-edge sag, corner y)
# Each mouth edge is a parabola between the two corners. A positive sag bends
# the edge downward (a smile); the opening is lower sag minus upper sag.
_MOUTHS: dict[str, tuple[float, float, float, float]] = {
    "X": (175.0, 30.0, 46.0, 640.0),
    "A": (150.0, 6.0, 14.0, 668.0),
    "B": (185.0, 26.0, 84.0, 640.0),
    "C": (195.0, 22.0, 132.0, 635.0),
    "D": (205.0, 12.0, 190.0, 628.0),
    "E": (135.0, -12.0, 118.0, 652.0),
    "F": (82.0, -30.0, 58.0, 668.0),
    "G": (185.0, 22.0, 84.0, 640.0),
    "H": (195.0, 18.0, 132.0, 635.0),
}

# (centre x offset, width, height, anchored to "top" or "bottom" edge)
_TEETH: dict[str, list[tuple[float, float, float, str]]] = {
    "X": [],
    "A": [],
    "B": [(-58.0, 44.0, 26.0, "top"), (40.0, 44.0, 22.0, "bottom")],
    "C": [(-70.0, 50.0, 42.0, "top"), (62.0, 50.0, 38.0, "bottom")],
    "D": [(-92.0, 52.0, 52.0, "top"), (70.0, 52.0, 52.0, "top"), (-6.0, 56.0, 48.0, "bottom")],
    "E": [(0.0, 44.0, 30.0, "top")],
    "F": [],
    # Upper teeth resting on the lower lip for F / V.
    "G": [(-96.0, 46.0, 50.0, "top"), (-32.0, 46.0, 56.0, "top"), (32.0, 46.0, 56.0, "top"), (96.0, 46.0, 50.0, "top")],
    "H": [(-78.0, 48.0, 36.0, "top"), (78.0, 48.0, 36.0, "top")],
}

_generation_lock = threading.Lock()


def _mirror(points: Iterable[Point]) -> list[Point]:
    return [(2 * CENTER_X - x, y) for x, y in points]


def _eye_polygons(expression: str) -> tuple[list[Point], list[Point]]:
    if expression == "squint":
        # Brow slants down toward the nose for a sly, narrowed look.
        left = [(700.0, 505.0), (888.0, 505.0), (878.0, 462.0), (722.0, 428.0)]
    elif expression == "wide":
        left = [(690.0, 515.0), (900.0, 515.0), (852.0, 402.0), (800.0, 350.0), (748.0, 402.0)]
    else:
        left = [(702.0, 505.0), (888.0, 505.0), (812.0, 382.0)]
    return left, _mirror(left)


def _nose_polygon() -> list[Point]:
    return [(CENTER_X, 506.0), (CENTER_X - 42.0, 572.0), (CENTER_X + 42.0, 572.0)]


def _edge(half_width: float, sag: float, corner_y: float, steps: int = 48) -> list[Point]:
    points = []
    for index in range(steps + 1):
        t = -1.0 + 2.0 * index / steps
        points.append((CENTER_X + half_width * t, corner_y + sag * (1.0 - t * t)))
    return points


def _edge_y(x_offset: float, half_width: float, sag: float, corner_y: float) -> float:
    t = max(-1.0, min(1.0, x_offset / half_width))
    return corner_y + sag * (1.0 - t * t)


def _mouth_mask(shape: str) -> Image.Image:
    half_width, upper, lower, corner_y = _MOUTHS[shape]
    mask = Image.new("L", CANVAS, 0)
    draw = ImageDraw.Draw(mask)
    outline = _edge(half_width, upper, corner_y) + list(reversed(_edge(half_width, lower, corner_y)))
    draw.polygon(outline, fill=255)
    for x_offset, width, height, anchor in _TEETH[shape]:
        x0 = CENTER_X + x_offset - width / 2.0
        x1 = CENTER_X + x_offset + width / 2.0
        if anchor == "top":
            top = min(_edge_y(x0 - CENTER_X, half_width, upper, corner_y), _edge_y(x1 - CENTER_X, half_width, upper, corner_y))
            draw.rectangle((x0, top - 4.0, x1, top + height), fill=0)
        else:
            bottom = max(_edge_y(x0 - CENTER_X, half_width, lower, corner_y), _edge_y(x1 - CENTER_X, half_width, lower, corner_y))
            draw.rectangle((x0, bottom - height, x1, bottom + 4.0), fill=0)
    if shape == "H":
        # Tongue lifted behind the teeth for L-like sounds.
        bottom = corner_y + lower
        draw.ellipse((CENTER_X - 70.0, bottom - 62.0, CENTER_X + 70.0, bottom + 40.0), fill=0)
    return mask


def _upper_mask(expression: str) -> Image.Image:
    mask = Image.new("L", CANVAS, 0)
    draw = ImageDraw.Draw(mask)
    left, right = _eye_polygons(expression)
    draw.polygon(left, fill=255)
    draw.polygon(right, fill=255)
    draw.polygon(_nose_polygon(), fill=255)
    return mask


def _candle_light() -> np.ndarray:
    """RGB float image of warm light, brightest at the candle position."""

    left, top, right, bottom = FACE_BOX
    ys, xs = np.mgrid[top:bottom, left:right].astype(np.float32)
    distance = np.sqrt(((xs - CANDLE[0]) / 1.15) ** 2 + (ys - CANDLE[1]) ** 2)
    falloff = np.clip(distance / 360.0, 0.0, 1.0)[..., None]
    core = np.array([255, 226, 150], dtype=np.float32)
    mid = np.array([255, 128, 24], dtype=np.float32)
    rim = np.array([196, 62, 6], dtype=np.float32)
    first = np.clip(falloff * 2.0, 0.0, 1.0)
    second = np.clip(falloff * 2.0 - 1.0, 0.0, 1.0)
    return (core * (1 - first) + mid * first) * (1 - second) + rim * second


def _render_face(full_mask: Image.Image, light: np.ndarray) -> Image.Image:
    mask = full_mask.crop(FACE_BOX)
    carved = np.asarray(mask, dtype=np.float32)[..., None] / 255.0
    # A lit rim just inside each cut suggests the thickness of the pumpkin wall.
    inner = mask.filter(ImageFilter.MinFilter(9))
    rim = np.asarray(ImageChops.subtract(mask, inner), dtype=np.float32)[..., None] / 255.0
    glow = np.asarray(mask.filter(ImageFilter.GaussianBlur(22)), dtype=np.float32)[..., None] / 255.0
    halo_color = np.array([255, 96, 10], dtype=np.float32)
    rim_color = np.array([255, 238, 190], dtype=np.float32)
    image = light * carved * 0.92
    image = image * (1 - rim * 0.65) + rim_color * rim * 0.65
    image = image + halo_color * glow * (1 - carved) * 0.55
    array = np.clip(image, 0, 255).astype(np.uint8)
    array[np.max(array, axis=2) < 4] = 0
    return Image.fromarray(array, mode="RGB")


def _apply_pose(face: Image.Image, scale: float, y_offset: float) -> Image.Image:
    """Scale the cropped face about the nose, shift it, and place it on the canvas."""

    left, top = FACE_BOX[:2]
    if scale != 1.0 or y_offset != 0.0:
        nose_x, nose_y = NOSE[0] - left, NOSE[1] - top
        tx = nose_x - scale * nose_x
        ty = nose_y + y_offset - scale * nose_y
        # PIL expects the inverse map: output (x, y) samples input ((x - tx) / s, (y - ty) / s).
        inverse = (1.0 / scale, 0.0, -tx / scale, 0.0, 1.0 / scale, -ty / scale)
        face = face.transform(face.size, Image.Transform.AFFINE, inverse, resample=Image.Resampling.BICUBIC)
    canvas = Image.new("RGB", CANVAS, "black")
    canvas.paste(face, (left, top))
    return canvas


def _manifest(frames: dict[str, dict[str, dict[str, str]]]) -> dict:
    return {
        "id": TEMPLATE_ID,
        "name": TEMPLATE_NAME,
        "width": CANVAS[0],
        "height": CANVAS[1],
        "default_fps": 30,
        "generator_version": GENERATOR_VERSION,
        "private_user_asset": False,
        "preview": "preview.jpg",
        "frames": frames,
    }


def _is_current(directory: Path) -> bool:
    manifest = directory / "template.json"
    if not manifest.is_file():
        return False
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if payload.get("generator_version") != GENERATOR_VERSION:
        return False
    return all(
        (directory / relative).is_file()
        for expressions in payload.get("frames", {}).values()
        for poses in expressions.values()
        for relative in poses.values()
    )


def generate(directory: Path) -> Path:
    """Draw every frame into *directory* and return its template.json path."""

    directory.mkdir(parents=True, exist_ok=True)
    frames_dir = directory / "frames"
    frames_dir.mkdir(exist_ok=True)
    light = _candle_light()
    uppers = {expression: _upper_mask(expression) for expression in EXPRESSIONS}
    frames: dict[str, dict[str, dict[str, str]]] = {}
    for shape in VALID_SHAPES:
        mouth = _mouth_mask(shape)
        frames[shape] = {}
        for expression in EXPRESSIONS:
            face = _render_face(ImageChops.lighter(uppers[expression], mouth), light)
            frames[shape][expression] = {}
            for pose, (scale, y_offset) in POSES.items():
                relative = f"frames/{shape}_{expression}_{pose}.png"
                _apply_pose(face, scale, y_offset).save(directory / relative, format="PNG", compress_level=3)
                frames[shape][expression][pose] = relative
            if shape == "C" and expression == "normal":
                preview = _apply_pose(face, 1.0, 0.0)
                preview.thumbnail((960, 540), Image.Resampling.LANCZOS)
                preview.save(directory / "preview.jpg", quality=88)
    manifest = directory / "template.json"
    manifest.write_text(json.dumps(_manifest(frames), indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def ensure_builtin_template(directory: Path) -> Path:
    """Return the cached template manifest, generating frames when needed."""

    directory = Path(directory)
    with _generation_lock:
        if _is_current(directory):
            return directory / "template.json"
        directory.parent.mkdir(parents=True, exist_ok=True)
        # Build next to the destination, then swap it in so a crash never
        # leaves a half-written template behind.
        staging = Path(tempfile.mkdtemp(prefix=f".{directory.name}-", dir=directory.parent))
        try:
            generate(staging)
            if directory.exists():
                shutil.rmtree(directory)
            os.replace(staging, directory)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
        return directory / "template.json"
