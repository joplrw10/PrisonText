from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pumpkinsync.builtin_template import GENERATOR_VERSION, ensure_builtin_template  # noqa: E402

# Drawing the built-in template takes a few seconds, so every test shares one
# cached copy (regenerated automatically when the drawing code changes).
BUILTIN_DIR = Path(tempfile.gettempdir()) / f"pumpkinsync-test-builtin-v{GENERATOR_VERSION}"
os.environ["PUMPKINSYNC_BUILTIN_TEMPLATE_DIR"] = str(BUILTIN_DIR)


@pytest.fixture(scope="session")
def builtin_manifest() -> Path:
    return ensure_builtin_template(BUILTIN_DIR)
