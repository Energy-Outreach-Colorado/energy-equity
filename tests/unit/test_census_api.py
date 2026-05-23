"""Tests for the Census API client and cache, with HTTP mocked via `responses`."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import responses

from energy_equity.census.api import (
    B19001_VARIABLES,
    BASE_URL,
    CensusClient,
    fetch_with_year_fallback,
)
from energy_equity.census.cache import CensusCache


@pytest.fixture
def client(tmp_path: Path) -> CensusClient:
    return CensusClient(api_key="fake-test-key", cache=CensusCache(tmp_path), rate_limit_sec=0.0)


@responses.activate
def test_fetch_counties(client: CensusClient) -> None:
    payload = [
        ["NAME", "state", "county"],
        ["Denver County, Colorado", "08", "031"],
        ["Pueblo County, Colorado", "08", "101"],
    ]
    responses.add(
        responses.GET,
        f"{BASE_URL}/2024/acs/acs5",
        json=payload,
        status=200,
    )
    df = client.fetch_counties("08", 2024)
    assert set(df.columns) == {"state_fips", "county_fips", "fips5", "name"}
    assert df.loc[df["county_fips"] == "101", "name"].iloc[0] == "Pueblo County, Colorado"
    assert df.loc[df["county_fips"] == "101", "fips5"].iloc[0] == "08101"


@responses.activate
def test_fetch_counties_cached_no_second_call(client: CensusClient) -> None:
    payload = [
        ["NAME", "state", "county"],
        ["Cook County, Illinois", "17", "031"],
    ]
    responses.add(
        responses.GET,
        f"{BASE_URL}/2024/acs/acs5",
        json=payload,
        status=200,
    )
    df1 = client.fetch_counties("17", 2024)
    df2 = client.fetch_counties("17", 2024)
    pd.testing.assert_frame_equal(df1, df2)
    # Only the first call should have hit the network.
    assert len(responses.calls) == 1


@responses.activate
def test_fetch_tract_households(client: CensusClient) -> None:
    payload = [
        ["B11001_001E", "state", "county", "tract"],
        ["1234", "08", "101", "001100"],
        ["2345", "08", "101", "001200"],
    ]
    responses.add(responses.GET, f"{BASE_URL}/2024/acs/acs5", json=payload, status=200)
    df = client.fetch_tract_households("08", 2024)
    assert df["households"].tolist() == [1234, 2345]
    assert df["geoid"].tolist() == ["08101001100", "08101001200"]


@responses.activate
def test_fetch_b19001_tracts(client: CensusClient) -> None:
    header = list(B19001_VARIABLES) + ["state", "county", "tract"]
    row1 = [str(i * 10) for i in range(len(B19001_VARIABLES))] + ["08", "101", "001100"]
    payload = [header, row1]
    responses.add(responses.GET, f"{BASE_URL}/2024/acs/acs5", json=payload, status=200)
    df = client.fetch_b19001_tracts("08", 2024)
    assert df.shape == (1, 4 + len(B19001_VARIABLES))
    assert df["B19001_002E"].iloc[0] == 0
    assert df["B19001_017E"].iloc[0] == 150  # (16-1)*10


@responses.activate
def test_retries_on_transient_failure(tmp_path: Path) -> None:
    client = CensusClient(
        api_key="k",
        cache=CensusCache(tmp_path),
        rate_limit_sec=0.0,
        backoff_sec=0.0,
        retries=3,
    )
    payload = [["NAME", "state", "county"], ["Foo, CO", "08", "001"]]
    responses.add(responses.GET, f"{BASE_URL}/2024/acs/acs5", json={"error": "boom"}, status=500)
    responses.add(responses.GET, f"{BASE_URL}/2024/acs/acs5", json=payload, status=200)
    df = client.fetch_counties("08", 2024)
    assert len(df) == 1
    assert len(responses.calls) == 2


def test_fetch_with_year_fallback() -> None:
    attempts: list[int] = []

    def fetcher(year: int) -> str:
        attempts.append(year)
        if year > 2022:
            raise RuntimeError("not yet published")
        return f"data-for-{year}"

    result, used_year = fetch_with_year_fallback(fetcher, start_year=2024, fallback_years=3)
    assert result == "data-for-2022"
    assert used_year == 2022
    assert attempts == [2024, 2023, 2022]


def test_fetch_with_year_fallback_exhausts() -> None:
    def fetcher(year: int) -> str:
        raise RuntimeError(f"no data for {year}")

    with pytest.raises(RuntimeError, match="All year fallbacks exhausted"):
        fetch_with_year_fallback(fetcher, start_year=2024, fallback_years=2)


def test_cache_round_trip(tmp_path: Path) -> None:
    cache = CensusCache(tmp_path)
    assert cache.load("counties", "08", 2024, 5) is None
    payload = [["NAME", "state", "county"], ["X County", "08", "001"]]
    cache.store("counties", "08", 2024, 5, payload)
    assert cache.has("counties", "08", 2024, 5)
    assert cache.load("counties", "08", 2024, 5) == payload


def test_cache_invalid_json_returns_none(tmp_path: Path) -> None:
    cache = CensusCache(tmp_path)
    # Corrupt the cache file directly.
    path = cache._path("counties", "08", 2024, 5)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not-json{")
    assert cache.load("counties", "08", 2024, 5) is None
