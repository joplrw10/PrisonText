from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from pumpkinsync.templates import TemplateLibrary, TemplateManifest


def copy_template(builtin_manifest: Path, destination: Path, template_id: str) -> Path:
    shutil.copytree(builtin_manifest.parent, destination)
    manifest = destination / "template.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["id"] = template_id
    payload["name"] = template_id.title()
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    return manifest


def test_discovery_prefers_explicit_then_private_then_user_then_builtin(tmp_path: Path, builtin_manifest: Path) -> None:
    explicit = copy_template(builtin_manifest, tmp_path / "explicit", "explicit-face")
    private = copy_template(builtin_manifest, tmp_path / "pkg" / "private_template", "private-face")
    copy_template(builtin_manifest, tmp_path / "user" / "zzz", "user-face")

    library = TemplateLibrary.discover(
        explicit=[explicit], package_private=private, user_dirs=[tmp_path / "user"], builtin=builtin_manifest
    )
    without_explicit = TemplateLibrary.discover(package_private=private, builtin=builtin_manifest)
    builtin_only = TemplateLibrary.discover(package_private=tmp_path / "missing.json", builtin=builtin_manifest)

    assert list(library.templates) == ["explicit-face", "private-face", "user-face", "builtin-pumpkin"]
    assert library.default.id == "explicit-face"
    assert library.templates["private-face"].source == "private"
    assert without_explicit.default.id == "private-face"
    assert builtin_only.default.id == "builtin-pumpkin"
    assert library.get("unknown").id == "explicit-face"


def test_broken_and_duplicate_templates_are_reported_not_fatal(tmp_path: Path, builtin_manifest: Path) -> None:
    broken = tmp_path / "user" / "broken"
    broken.mkdir(parents=True)
    (broken / "template.json").write_text('{"id": "broken", "width": 10, "height": 10, "frames": {}}', encoding="utf-8")
    copy_template(builtin_manifest, tmp_path / "user" / "dupe", "builtin-pumpkin")

    library = TemplateLibrary.discover(user_dirs=[tmp_path / "user"], builtin=builtin_manifest)

    assert "builtin-pumpkin" in library
    assert any("broken" in message for message in library.errors)
    assert any("duplicate" in message for message in library.errors)


def test_same_template_reached_twice_is_silently_loaded_once(builtin_manifest: Path) -> None:
    library = TemplateLibrary.discover(explicit=[builtin_manifest], builtin=builtin_manifest)

    assert list(library.templates) == ["builtin-pumpkin"]
    assert library.errors == []


def test_template_ids_and_assets_are_path_safe(tmp_path: Path, builtin_manifest: Path) -> None:
    manifest = copy_template(builtin_manifest, tmp_path / "evil", "evil")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["id"] = "../evil"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    template = TemplateManifest.load(builtin_manifest)

    with pytest.raises(ValueError, match="template id"):
        TemplateManifest.load(manifest)
    with pytest.raises(ValueError):
        template.resolve_asset("../../etc/passwd")
    with pytest.raises(ValueError):
        template.resolve_asset("template.json")
    assert template.resolve_asset("frames/A_normal_center.png").is_file()
    assert template.public_dict()["preview_url"] == "/templates/builtin-pumpkin/preview.jpg"
