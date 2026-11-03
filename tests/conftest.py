"""Shared fixtures.

The repository's ``library/`` holds TEMSA data only. The two placeholder
battery-electric buses the physics tests exercise live in ``examples/vehicle/``
so they never show up in the product's library. For the tests, both are
merged into one temporary library per session.
"""

from __future__ import annotations

import pathlib
import shutil

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def merged_library(dst: pathlib.Path) -> pathlib.Path:
    """library/ plus examples/ (placeholder vehicles) copied into ``dst``."""
    shutil.copytree(ROOT / "library", dst, ignore=shutil.ignore_patterns(".cop_kutusu"))
    if EXAMPLES.exists():
        for sub in EXAMPLES.iterdir():
            if sub.is_dir():
                shutil.copytree(sub, dst / sub.name, dirs_exist_ok=True)
    return dst


@pytest.fixture(scope="session")
def demo_library(tmp_path_factory) -> pathlib.Path:
    """Read-only merged library for the whole session."""
    return merged_library(tmp_path_factory.mktemp("lib") / "library")
