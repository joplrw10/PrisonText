#!/usr/bin/env python3
"""Build a private PumpkinSync face template from a user-owned performance video.

The default timestamps were selected from the owner's supplied Read with Trick pack.
Purchased source media is read in place and is never copied into the output folder.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

CANVAS = (1920, 1080)
TARGET_NOSE = (960.0, 530.0)
TARGET_EYE_DISTANCE = 344.0
EXPRESSIONS = {
    "normal": 74.000,
    "squint": 160.083,
    "wide": 88.167,
}
MOUTHS = {
    "X": 147.083,
    "A": 126.917,
    "B": 200.917,
    "C": 166.333,
    "D": 88.167,
    "E": 443.000,
    "F": 177.167,
    "G": 405.500,
    "H": 91.083,
}
POSES = {
    "up": (1.010, -7.0),
    "center": (1.000, 0.0),
    "down": (0.992, 7.0),
}


@dataclass(frozen=True)
class Component:
    label: int
    area: int
    x: int
    y: int
    width: int
    height: int
    cx: float
    cy: float


def extract_frame(source: Path, timestamp: float, output: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(source),
            "-frames:v",
            "1",
            str(output),
        ],
        check=True,
    )


def components(image: np.ndarray, threshold: int = 15) -> tuple[np.ndarray, list[Component]]:
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(
        (gray > threshold).astype(np.uint8), 8
    )
    values: list[Component] = []
    for label in range(1, count):
        x, y, width, height, area = (int(value) for value in stats[label])
        if area < 300:
            continue
        cx, cy = (float(value) for value in centroids[label])
        values.append(Component(label, area, x, y, width, height, cx, cy))
    return labels, values


def detect_landmarks(image: np.ndarray) -> tuple[Component, Component, Component]:
    _labels, values = components(image)
    center_x = image.shape[1] / 2.0
    nose_candidates = [
        item
        for item in values
        if abs(item.cx - center_x) < 130
        and item.cy < 620
        and item.area < 12_000
        and item.width < 170
        and item.height < 145
    ]
    if not nose_candidates:
        raise RuntimeError("could not identify the center nose component")
    nose = max(nose_candidates, key=lambda item: item.area)
    left_candidates = [item for item in values if item.cx < nose.cx - 70 and item.cy < nose.cy]
    right_candidates = [item for item in values if item.cx > nose.cx + 70 and item.cy < nose.cy]
    if not left_candidates or not right_candidates:
        raise RuntimeError("could not identify both eye components")
    left = max(left_candidates, key=lambda item: item.area)
    right = max(right_candidates, key=lambda item: item.area)
    return left, right, nose


def align(image: np.ndarray) -> np.ndarray:
    left, right, nose = detect_landmarks(image)
    eye_distance = right.cx - left.cx
    if eye_distance <= 0:
        raise RuntimeError("invalid eye geometry")
    scale = TARGET_EYE_DISTANCE / eye_distance
    matrix = np.array(
        [
            [scale, 0.0, TARGET_NOSE[0] - scale * nose.cx],
            [0.0, scale, TARGET_NOSE[1] - scale * nose.cy],
        ],
        dtype=np.float32,
    )
    aligned = cv2.warpAffine(
        image,
        matrix,
        CANVAS,
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0),
    )
    # Remove H.264 near-black macroblock noise while preserving the soft glow.
    aligned[np.max(aligned, axis=2) < 5] = 0
    return aligned


def layer_by_region(image: np.ndarray, region: str) -> np.ndarray:
    _labels, values = components(image, threshold=12)
    output = np.zeros_like(image)
    for item in values:
        is_mouth = item.cy > TARGET_NOSE[1] + 75
        include = is_mouth if region == "mouth" else not is_mouth
        if not include:
            continue
        padding = 34 if is_mouth else 26
        x0 = max(0, item.x - padding)
        y0 = max(0, item.y - padding)
        x1 = min(image.shape[1], item.x + item.width + padding)
        y1 = min(image.shape[0], item.y + item.height + padding)
        output[y0:y1, x0:x1] = np.maximum(
            output[y0:y1, x0:x1], image[y0:y1, x0:x1]
        )
    return output


def apply_pose(image: np.ndarray, scale: float, y_offset: float) -> np.ndarray:
    matrix = np.array(
        [
            [scale, 0.0, TARGET_NOSE[0] - scale * TARGET_NOSE[0]],
            [0.0, scale, TARGET_NOSE[1] + y_offset - scale * TARGET_NOSE[1]],
        ],
        dtype=np.float32,
    )
    posed = cv2.warpAffine(
        image,
        matrix,
        CANVAS,
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0),
    )
    posed[np.max(posed, axis=2) < 5] = 0
    return posed


def build(source: Path, output: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    output.mkdir(parents=True, exist_ok=True)
    frames_dir = output / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="pumpkinsync-template-") as temporary:
        scratch = Path(temporary)
        aligned: dict[str, np.ndarray] = {}
        timestamps = {**{f"upper_{key}": value for key, value in EXPRESSIONS.items()}, **{f"mouth_{key}": value for key, value in MOUTHS.items()}}
        for name, timestamp in timestamps.items():
            raw_path = scratch / f"{name}.png"
            extract_frame(source, timestamp, raw_path)
            raw = np.asarray(Image.open(raw_path).convert("RGB"))
            aligned[name] = align(raw)

        uppers = {
            expression: layer_by_region(aligned[f"upper_{expression}"], "upper")
            for expression in EXPRESSIONS
        }
        mouths = {
            shape: layer_by_region(aligned[f"mouth_{shape}"], "mouth")
            for shape in MOUTHS
        }

        manifest_frames: dict[str, dict[str, dict[str, str]]] = {}
        for shape, mouth in mouths.items():
            manifest_frames[shape] = {}
            for expression, upper in uppers.items():
                composite = np.maximum(upper, mouth)
                manifest_frames[shape][expression] = {}
                for pose, (scale, y_offset) in POSES.items():
                    filename = f"{shape}_{expression}_{pose}.png"
                    relative = f"frames/{filename}"
                    image = apply_pose(composite, scale, y_offset)
                    Image.fromarray(image, mode="RGB").save(
                        frames_dir / filename,
                        format="PNG",
                        optimize=True,
                        compress_level=9,
                    )
                    manifest_frames[shape][expression][pose] = relative

    manifest = {
        "id": "trick-user-private",
        "name": "Trick - Private User Template",
        "width": CANVAS[0],
        "height": CANVAS[1],
        "default_fps": 30,
        "private_user_asset": True,
        "source_media_included": False,
        "frames": manifest_frames,
    }
    (output / "template.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    preview = Image.open(frames_dir / "C_normal_center.png").convert("RGB")
    preview.thumbnail((960, 540), Image.Resampling.LANCZOS)
    preview.save(output / "preview.jpg", quality=88, optimize=True)
    print(f"Built {len(MOUTHS) * len(EXPRESSIONS) * len(POSES)} frames in {output}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True, help="User-owned performance MP4")
    parser.add_argument("--output", type=Path, required=True, help="Template output folder")
    args = parser.parse_args()
    build(args.source.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
