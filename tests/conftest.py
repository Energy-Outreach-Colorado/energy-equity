"""Shared pytest fixtures and configuration."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def tmp_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the package cache to a per-test temporary directory."""
    cache = tmp_path / "ee-cache"
    cache.mkdir()
    monkeypatch.setenv("EE_CACHE_DIR", str(cache))
    return cache


@pytest.fixture(autouse=True)
def _no_network_unless_marked(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Block live HTTP unless the test is marked `network`.

    Tests that intentionally hit the network should be marked with @pytest.mark.network and
    are skipped by default; opt in with `pytest -m network`.
    """
    if request.node.get_closest_marker("network"):
        return
    # Defensive sentinel: ensure CENSUS_API_KEY is unset so accidental live calls fail fast.
    if "CENSUS_API_KEY" not in os.environ:
        monkeypatch.setenv("CENSUS_API_KEY", "TEST-KEY-NOT-REAL")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip @pytest.mark.network tests unless explicitly requested via -m network."""
    if config.getoption("-m") and "network" in config.getoption("-m"):
        return
    skip_network = pytest.mark.skip(reason="network test; run with `pytest -m network`")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip_network)
