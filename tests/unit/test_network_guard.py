"""The test-suite network guard refuses remote hosts for unmarked tests."""

from __future__ import annotations

import socket

import pytest
import requests


def test_http_request_is_blocked() -> None:
    with pytest.raises(Exception, match="mock the download"):
        requests.get("https://www2.census.gov/", timeout=5)


def test_name_lookup_is_blocked() -> None:
    with pytest.raises(Exception, match="tried to resolve 'example.org'"):
        socket.getaddrinfo("example.org", 443)


def test_local_lookups_still_work() -> None:
    assert socket.getaddrinfo("localhost", 80)
