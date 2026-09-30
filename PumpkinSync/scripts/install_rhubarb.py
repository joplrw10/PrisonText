#!/usr/bin/env python3
"""Download and install the latest official Rhubarb Lip Sync release.

This helper intentionally uses only the Python standard library so it can run
inside PumpkinSync's base virtual environment without extra dependencies.
"""

from __future__ import annotations

import argparse
import json
import platform as platform_module
import shutil
import stat
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Mapping

DEFAULT_REPOSITORY = "DanielSWolf/rhubarb-lip-sync"
GITHUB_API_ROOT = "https://api.github.com/repos"


def normalize_platform(platform_name: str) -> str:
    """Return PumpkinSync's normalized platform identifier."""

    normalized = platform_name.strip().lower()
    aliases = {
        "win32": "windows",
        "win64": "windows",
        "windows": "windows",
        "linux": "linux",
        "darwin": "macos",
        "mac": "macos",
        "macos": "macos",
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ValueError(f"Unsupported platform: {platform_name}") from exc


def detect_platform() -> str:
    """Detect the current operating system for release selection."""

    return normalize_platform(platform_module.system())


def select_asset(release: Mapping[str, Any], platform_name: str) -> Mapping[str, Any]:
    """Select the official ZIP release asset for *platform_name*."""

    target = normalize_platform(platform_name)
    tokens = {
        "windows": ("windows", "win64", "win32"),
        "linux": ("linux",),
        "macos": ("macos", "mac", "osx"),
    }[target]

    for asset in release.get("assets", []):
        name = str(asset.get("name", ""))
        lower_name = name.lower()
        download_url = asset.get("browser_download_url")
        if lower_name.endswith(".zip") and any(token in lower_name for token in tokens) and download_url:
            return asset

    tag = release.get("tag_name") or release.get("name") or "latest release"
    raise ValueError(f"No {target} ZIP asset was found in {tag}.")


def _find_binary(extracted_root: Path, platform_name: str) -> Path:
    executable_name = "rhubarb.exe" if normalize_platform(platform_name) == "windows" else "rhubarb"
    candidates = sorted(
        path
        for path in extracted_root.rglob(executable_name)
        if path.is_file() and "__MACOSX" not in path.parts
    )
    if not candidates:
        raise ValueError(f"The archive does not contain {executable_name}.")
    return candidates[0]


def install_archive(archive_path: Path | str, destination: Path | str, platform_name: str) -> Path:
    """Extract a downloaded Rhubarb ZIP into *destination* and return its binary."""

    archive = Path(archive_path).expanduser().resolve()
    destination_path = Path(destination).expanduser().resolve()
    if not archive.is_file():
        raise FileNotFoundError(f"Archive not found: {archive}")

    with tempfile.TemporaryDirectory(prefix="pumpkinsync-rhubarb-") as temporary_directory:
        extracted_root = Path(temporary_directory) / "extracted"
        extracted_root.mkdir(parents=True)
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(extracted_root)

        source_binary = _find_binary(extracted_root, platform_name)
        source_root = source_binary.parent

        destination_path.mkdir(parents=True, exist_ok=True)
        for child in source_root.iterdir():
            target = destination_path / child.name
            if child.is_dir():
                shutil.copytree(child, target, dirs_exist_ok=True)
            else:
                shutil.copy2(child, target)

    binary_name = "rhubarb.exe" if normalize_platform(platform_name) == "windows" else "rhubarb"
    installed_binary = destination_path / binary_name
    if not installed_binary.is_file():
        raise RuntimeError(f"Rhubarb was not installed at {installed_binary}.")

    if normalize_platform(platform_name) != "windows":
        current_mode = installed_binary.stat().st_mode
        installed_binary.chmod(
            current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )

    return installed_binary


def _request_json(url: str) -> Mapping[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "PumpkinSync-Rhubarb-Installer",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GitHub returned HTTP {exc.code} while checking Rhubarb releases.") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach GitHub to check Rhubarb releases: {exc.reason}") from exc


def download_latest(
    destination: Path | str,
    repository: str = DEFAULT_REPOSITORY,
    platform_name: str | None = None,
) -> Path:
    """Download and install the latest release for the requested platform."""

    target_platform = normalize_platform(platform_name or detect_platform())
    release_url = f"{GITHUB_API_ROOT}/{repository}/releases/latest"
    release = _request_json(release_url)
    asset = select_asset(release, target_platform)
    download_url = str(asset["browser_download_url"])
    asset_name = str(asset.get("name") or "rhubarb.zip")

    request = urllib.request.Request(
        download_url,
        headers={"User-Agent": "PumpkinSync-Rhubarb-Installer"},
    )
    with tempfile.TemporaryDirectory(prefix="pumpkinsync-rhubarb-download-") as temporary_directory:
        archive_path = Path(temporary_directory) / asset_name
        try:
            with urllib.request.urlopen(request, timeout=120) as response, archive_path.open("wb") as output:
                shutil.copyfileobj(response, output)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"GitHub returned HTTP {exc.code} while downloading {asset_name}.") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not download {asset_name}: {exc.reason}") from exc
        return install_archive(archive_path, destination, target_platform)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install the latest official Rhubarb Lip Sync release for PumpkinSync."
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path("tools") / "rhubarb",
        help="Installation folder (default: tools/rhubarb)",
    )
    parser.add_argument(
        "--repository",
        default=DEFAULT_REPOSITORY,
        help=f"GitHub repository (default: {DEFAULT_REPOSITORY})",
    )
    parser.add_argument(
        "--platform",
        dest="platform_name",
        choices=("windows", "linux", "macos"),
        help="Override automatic platform detection.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        binary = download_latest(
            destination=args.destination,
            repository=args.repository,
            platform_name=args.platform_name,
        )
    except (FileNotFoundError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
        print(f"Rhubarb installation failed: {exc}")
        return 1

    print(f"Rhubarb installed: {binary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
