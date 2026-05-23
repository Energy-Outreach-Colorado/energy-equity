"""Census Bureau ACS data API client.

This module replaces the Colorado-hardcoded fetchers from the original notebooks with
state-agnostic equivalents:

- `fetch_counties(state_fips, year)` returns the (FIPS5, name) table for a state, which
  replaces the hardcoded `CO_COUNTY_FIPS3` dict.
- `fetch_tract_households(state_fips, year)` returns ACS 5-yr B11001_001E (total occupied
  households) per tract.
- `fetch_b19001_tracts(state_fips, year)` returns the 16-bin household income distribution
  (B19001) per tract.

Responses are cached to disk via `CensusCache` so reruns and tests avoid network calls.
Live calls are rate-limited and retried on transient failures. The API key is read from the
`CENSUS_API_KEY` environment variable (configurable via `CensusApiConfig.key_env`).
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import pandas as pd
import requests

from ..io.pums import zfill_str
from .cache import CensusCache

T = TypeVar("T")

BASE_URL = "https://api.census.gov/data"
DEFAULT_TIMEOUT_SEC = 60

# 16 B19001 income-bin variables.
B19001_VARIABLES: tuple[str, ...] = tuple(f"B19001_{i:03d}E" for i in range(2, 18))


@dataclass(frozen=True)
class CensusClient:
    """Minimal client for ACS detailed-table endpoints.

    Construct via `CensusClient.from_env()` to load the API key from the configured env var.
    The client is stateless aside from rate-limit timing tracked at module scope.
    """

    api_key: str | None
    cache: CensusCache
    rate_limit_sec: float = 0.1
    timeout_sec: float = DEFAULT_TIMEOUT_SEC
    retries: int = 3
    backoff_sec: float = 1.5

    @classmethod
    def from_env(
        cls,
        cache_dir: str | Path,
        *,
        key_env: str = "CENSUS_API_KEY",
        rate_limit_sec: float = 0.1,
    ) -> CensusClient:
        return cls(
            api_key=os.environ.get(key_env),
            cache=CensusCache(cache_dir),
            rate_limit_sec=rate_limit_sec,
        )

    def _get(self, url: str, params: dict[str, str]) -> list[list[str]]:
        last_exc: Exception | None = None
        if self.api_key:
            params = {**params, "key": self.api_key}
        for attempt in range(1, self.retries + 1):
            try:
                time.sleep(self.rate_limit_sec)
                resp = requests.get(url, params=params, timeout=self.timeout_sec)
                resp.raise_for_status()
                data = resp.json()
                if not isinstance(data, list) or not data:
                    raise ValueError(f"Census API returned empty/invalid payload: {data!r}")
                return data
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                if attempt == self.retries:
                    break
                time.sleep(self.backoff_sec * attempt)
        raise RuntimeError(f"Census API call failed: GET {url} params={params}") from last_exc

    # ------- High-level fetchers --------------------------------------------------

    def fetch_counties(self, state_fips: str, year: int, span: int = 5) -> pd.DataFrame:
        """Return county FIPS + name for `state_fips` from ACS NAME column.

        Returns columns: `state_fips` (2), `county_fips` (3), `fips5` (5), `name`.
        """
        cached = self.cache.load("counties", state_fips, year, span)
        if cached is None:
            cached = self._get(
                f"{BASE_URL}/{year}/acs/acs{span}",
                {"get": "NAME", "for": "county:*", "in": f"state:{state_fips}"},
            )
            self.cache.store("counties", state_fips, year, span, cached)
        return _rows_to_county_frame(cached)

    def fetch_tract_households(self, state_fips: str, year: int, span: int = 5) -> pd.DataFrame:
        """Return ACS B11001_001E (total occupied households) per tract.

        Columns: `state_fips`, `county_fips`, `tract_fips`, `geoid` (11-digit),
        `households`.
        """
        cached = self.cache.load("tract_households", state_fips, year, span)
        if cached is None:
            cached = self._get(
                f"{BASE_URL}/{year}/acs/acs{span}",
                {
                    "get": "B11001_001E",
                    "for": "tract:*",
                    "in": f"state:{state_fips} county:*",
                },
            )
            self.cache.store("tract_households", state_fips, year, span, cached)
        return _rows_to_tract_hh_frame(cached)

    def fetch_b19001_tracts(self, state_fips: str, year: int, span: int = 5) -> pd.DataFrame:
        """Return the 16-bin B19001 income distribution per tract.

        Columns: `state_fips`, `county_fips`, `tract_fips`, `geoid`, then
        `B19001_002E` .. `B19001_017E` (one column per income bin).
        """
        cached = self.cache.load("b19001_tracts", state_fips, year, span)
        if cached is None:
            cached = self._get(
                f"{BASE_URL}/{year}/acs/acs{span}",
                {
                    "get": ",".join(B19001_VARIABLES),
                    "for": "tract:*",
                    "in": f"state:{state_fips} county:*",
                },
            )
            self.cache.store("b19001_tracts", state_fips, year, span, cached)
        return _rows_to_b19001_frame(cached)


def fetch_with_year_fallback(
    fetch_func: Callable[[int], T],
    *,
    start_year: int,
    fallback_years: int = 3,
) -> tuple[T, int]:
    """Try `fetch_func(year)` starting at `start_year` and falling back up to `fallback_years` earlier.

    Returns the first successful result alongside the year that produced it. Useful for ACS
    endpoints that lag behind the current calendar year. Each fallback is logged to stderr.
    """
    last_exc: Exception | None = None
    for offset in range(fallback_years + 1):
        attempt_year = start_year - offset
        try:
            return fetch_func(attempt_year), attempt_year
        except Exception as exc:
            last_exc = exc
            print(f"[census] year {attempt_year} fetch failed ({exc!r}); falling back.")
    raise RuntimeError(
        f"All year fallbacks exhausted ({start_year} down to {start_year - fallback_years})"
    ) from last_exc


# ------- Parsers (private) -------------------------------------------------------


def _rows_to_dataframe(rows: list[list[str]]) -> pd.DataFrame:
    header, *data = rows
    return pd.DataFrame(data, columns=header)


def _rows_to_county_frame(rows: list[list[str]]) -> pd.DataFrame:
    df = _rows_to_dataframe(rows)
    df = df.rename(columns={"NAME": "name", "state": "state_fips", "county": "county_fips"})
    df["state_fips"] = df["state_fips"].map(lambda s: zfill_str(s, 2))
    df["county_fips"] = df["county_fips"].map(lambda s: zfill_str(s, 3))
    df["fips5"] = df["state_fips"] + df["county_fips"]
    return df[["state_fips", "county_fips", "fips5", "name"]].reset_index(drop=True)


def _rows_to_tract_hh_frame(rows: list[list[str]]) -> pd.DataFrame:
    df = _rows_to_dataframe(rows)
    df = df.rename(
        columns={
            "B11001_001E": "households",
            "state": "state_fips",
            "county": "county_fips",
            "tract": "tract_fips",
        }
    )
    df["households"] = pd.to_numeric(df["households"], errors="coerce")
    df["state_fips"] = df["state_fips"].map(lambda s: zfill_str(s, 2))
    df["county_fips"] = df["county_fips"].map(lambda s: zfill_str(s, 3))
    df["tract_fips"] = df["tract_fips"].map(lambda s: zfill_str(s, 6))
    df["geoid"] = df["state_fips"] + df["county_fips"] + df["tract_fips"]
    return df[["state_fips", "county_fips", "tract_fips", "geoid", "households"]].reset_index(
        drop=True
    )


def _rows_to_b19001_frame(rows: list[list[str]]) -> pd.DataFrame:
    df = _rows_to_dataframe(rows)
    df = df.rename(
        columns={
            "state": "state_fips",
            "county": "county_fips",
            "tract": "tract_fips",
        }
    )
    for col in B19001_VARIABLES:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["state_fips"] = df["state_fips"].map(lambda s: zfill_str(s, 2))
    df["county_fips"] = df["county_fips"].map(lambda s: zfill_str(s, 3))
    df["tract_fips"] = df["tract_fips"].map(lambda s: zfill_str(s, 6))
    df["geoid"] = df["state_fips"] + df["county_fips"] + df["tract_fips"]
    ordered = ["state_fips", "county_fips", "tract_fips", "geoid", *B19001_VARIABLES]
    return df[ordered].reset_index(drop=True)
