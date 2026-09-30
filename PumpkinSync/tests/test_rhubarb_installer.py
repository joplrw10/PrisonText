from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest

from scripts.install_rhubarb import install_archive, select_asset


def test_select_asset_matches_requested_platform() -> None:
    release = {
        "assets": [
            {"name": "Rhubarb-Lip-Sync-1.14.0-Windows.zip", "browser_download_url": "https://example/windows"},
            {"name": "Rhubarb-Lip-Sync-1.14.0-Linux.zip", "browser_download_url": "https://example/linux"},
            {"name": "Source.zip", "browser_download_url": "https://example/source"},
        ]
    }

    assert select_asset(release, "windows")["browser_download_url"].endswith("windows")
    assert select_asset(release, "linux")["browser_download_url"].endswith("linux")


def test_select_asset_reports_missing_platform() -> None:
    with pytest.raises(ValueError, match="macos"):
        select_asset({"assets": []}, "macos")


def test_install_archive_copies_executable_and_resources(tmp_path: Path) -> None:
    archive = tmp_path / "rhubarb.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("Rhubarb-Lip-Sync/rhubarb", "binary")
        bundle.writestr("Rhubarb-Lip-Sync/res/model.dat", "model")
        bundle.writestr("Rhubarb-Lip-Sync/README.txt", "read me")

    destination = tmp_path / "tools" / "rhubarb"
    binary = install_archive(archive, destination, "linux")

    assert binary == destination / "rhubarb"
    assert binary.read_text(encoding="utf-8") == "binary"
    assert (destination / "res" / "model.dat").read_text(encoding="utf-8") == "model"
    assert os.access(binary, os.X_OK)
