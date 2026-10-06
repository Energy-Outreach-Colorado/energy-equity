"""Shared pytest fixtures and configuration."""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_REAL_CONNECT = socket.socket.connect
_REAL_GETADDRINFO = socket.getaddrinfo


class NetworkBlockedError(RuntimeError):
    """Raised when a test not marked `network` tries to reach a remote host."""


def _is_local(host: object) -> bool:
    return host is None or str(host) in LOCAL_HOSTS


def _blocking_socket_functions(test_id: str):
    """Replacements for socket.connect and getaddrinfo that refuse remote hosts.

    Blocking name resolution as well as connections catches HTTP clients before they
    leave the machine, and the error names the test so the missing mock is easy to find.
    """

    def connect(self: socket.socket, address: object) -> None:
        host = address[0] if isinstance(address, tuple) else None
        if not _is_local(host):
            raise NetworkBlockedError(
                f"{test_id} tried to connect to {address!r}; mock the download or mark "
                "the test with @pytest.mark.network"
            )
        return _REAL_CONNECT(self, address)

    def getaddrinfo(host: object, *args: object, **kwargs: object):
        if not _is_local(host):
            raise NetworkBlockedError(
                f"{test_id} tried to resolve {host!r}; mock the download or mark the test "
                "with @pytest.mark.network"
            )
        return _REAL_GETADDRINFO(host, *args, **kwargs)

    return connect, getaddrinfo


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
    """Block live network access unless the test is marked `network`.

    Connections and name lookups for any host other than this machine raise
    `NetworkBlockedError`, so a test that forgets to mock a download fails instead of
    reaching census.gov. Tests that intentionally hit the network should be marked with
    @pytest.mark.network and are skipped by default; opt in with `pytest -m network`.
    """
    if request.node.get_closest_marker("network"):
        return
    connect, getaddrinfo = _blocking_socket_functions(request.node.nodeid)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
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
