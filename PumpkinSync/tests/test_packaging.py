from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_python_package_declares_runtime_and_private_template_assets() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    project = pyproject["project"]
    assert project["name"] == "pumpkinsync"
    assert project["requires-python"] == ">=3.11"
    assert any(item.startswith("fastapi") for item in project["dependencies"])
    assert any(item.startswith("uvicorn") for item in project["dependencies"])
    assert project["scripts"]["pumpkinsync"] == "pumpkinsync.cli:main"

    package_data = pyproject["tool"]["setuptools"]["package-data"]["pumpkinsync"]
    assert "static/*" in package_data
    assert "private_template/*.json" in package_data
    assert "private_template/frames/*.png" in package_data


def test_launchers_and_container_files_expose_port_8765() -> None:
    windows = (ROOT / "run_windows.bat").read_text(encoding="utf-8")
    linux = (ROOT / "run_linux.sh").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "python.exe -m pumpkinsync" in windows
    assert "python -m pumpkinsync" in linux
    assert "EXPOSE 8765" in dockerfile
    assert "8765:8765" in compose
    assert "./data:/app/data" in compose


def test_distribution_notice_separates_code_from_private_user_assets() -> None:
    notice = (ROOT / "USER_MEDIA_NOTICE.md").read_text(encoding="utf-8").lower()
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "not covered by the mit license" in notice
    assert "do not redistribute" in notice
    assert "source videos" in notice
    assert "MIT License" in license_text
    assert "Windows" in readme
    assert "Unraid" in readme
    assert "Rhubarb" in readme
    assert "Demucs" in readme


def test_distribution_tree_contains_no_purchased_source_media() -> None:
    forbidden = {".mp4", ".m4a", ".mp3", ".pdf", ".zip"}
    files = [path for path in ROOT.rglob("*") if path.is_file() and ".git" not in path.parts]

    assert not [path for path in files if path.suffix.lower() in forbidden]


def test_optional_rhubarb_installers_use_the_official_release_api() -> None:
    windows = (ROOT / "install_rhubarb_windows.ps1").read_text(encoding="utf-8")
    linux = (ROOT / "install_rhubarb_linux.sh").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "DanielSWolf/rhubarb-lip-sync" in windows
    assert "releases/latest" in windows
    assert "DanielSWolf/rhubarb-lip-sync" in linux
    assert "releases/latest" in linux
    assert "install_rhubarb_windows.ps1" in readme
    assert "install_rhubarb_linux.sh" in readme


def test_private_templates_and_tool_downloads_are_never_committed() -> None:
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "src/pumpkinsync/private_template/" in ignored
    assert "templates/" in ignored
    assert "data/" in ignored
    assert "tools/rhubarb/*" in ignored


def test_docker_healthcheck_allows_first_run_template_drawing() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "--start-period=60s" in dockerfile
