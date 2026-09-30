from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from pumpkinsync.builtin_template import ensure_builtin_template
from pumpkinsync.models import VALID_SHAPES
from pumpkinsync.templates import TemplateManifest


def test_builtin_template_loads_with_every_shape_expression_and_pose(builtin_manifest: Path) -> None:
    template = TemplateManifest.load(builtin_manifest)

    assert template.id == "builtin-pumpkin"
    assert (template.width, template.height) == (1920, 1080)
    assert set(template.frames) == set(VALID_SHAPES)
    for expressions in template.frames.values():
        assert set(expressions) == {"normal", "squint", "wide"}
        for poses in expressions.values():
            assert set(poses) == {"up", "center", "down"}
    assert template.preview is not None and template.preview.name == "preview.jpg"


def test_every_mouth_shape_is_visually_distinct(builtin_manifest: Path) -> None:
    template = TemplateManifest.load(builtin_manifest)
    images = {
        shape: np.asarray(Image.open(template.frames[shape]["normal"]["center"]).convert("L"), dtype=np.int16)
        for shape in VALID_SHAPES
    }

    for first in VALID_SHAPES:
        for second in VALID_SHAPES:
            if first < second:
                changed = np.count_nonzero(np.abs(images[first] - images[second]) > 40)
                assert changed > 800, (first, second, changed)


def test_open_mouths_let_more_light_through_than_closed_ones(builtin_manifest: Path) -> None:
    template = TemplateManifest.load(builtin_manifest)

    def mouth_light(shape: str) -> float:
        image = np.asarray(Image.open(template.frames[shape]["normal"]["center"]).convert("L"), dtype=np.float32)
        return float(image[600:900, 700:1220].sum())

    assert mouth_light("D") > mouth_light("C") > mouth_light("B") > mouth_light("A")


def test_ensure_reuses_cache_and_regenerates_when_damaged(tmp_path: Path, builtin_manifest: Path) -> None:
    import shutil

    target = tmp_path / "builtin"
    shutil.copytree(builtin_manifest.parent, target)
    frame = target / "frames" / "D_wide_up.png"
    stamp = frame.stat().st_mtime_ns

    ensure_builtin_template(target)
    assert frame.stat().st_mtime_ns == stamp

    frame.unlink()
    ensure_builtin_template(target)
    assert frame.is_file()
