"""Tests for the cfg-driven AMI bridge orchestrator (Census mocked via `responses`)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import responses
from responses.matchers import query_param_matcher

from energy_equity.census.api import BASE_URL
from energy_equity.config import Config
from energy_equity.pums.ami_bridge import build_ami80_bridge

COUNTIES_PAYLOAD = [
    ["NAME", "state", "county"],
    ["Pueblo County, Colorado", "08", "101"],
    ["El Paso County, Colorado", "08", "041"],
]
TRACT_HH_PAYLOAD = [
    ["B11001_001E", "state", "county", "tract"],
    ["1000", "08", "101", "000100"],
    ["500", "08", "101", "000200"],
    ["1500", "08", "041", "000300"],
]
TRACT_TO_PUMA = (
    "STATEFP,COUNTYFP,TRACTCE,PUMA5CE\n"
    "08,101,000100,00800\n"
    "08,101,000200,00800\n"
    "08,041,000300,00900\n"
)


def _make_cfg(tmp_path: Path) -> Config:
    hud = tmp_path / "hud_ami.csv"
    pd.DataFrame(
        {"County": ["Pueblo", "El Paso"], "1": [50000, 60000], "2": [55000, 65000]}
    ).to_csv(hud, index=False)
    cache = tmp_path / "cache"
    cache.mkdir()
    # Pre-place the tract->PUMA relationship file so the bridge doesn't try to download it.
    (cache / "2020_Census_Tract_to_2020_PUMA.txt").write_text(TRACT_TO_PUMA)
    return Config.from_mapping(
        {
            "project": {
                "name": "bridge",
                "output_dir": str(tmp_path / "out"),
                "cache_dir": str(cache),
            },
            "geography": {
                "state_fips": "08",
                "state_abbr": "CO",
                "service_area": {"shapefile": str(tmp_path / "x.shp")},
            },
            "vintages": {
                "acs_year": 2024,
                "pums_year": 2024,
                "hud_ami_fy": 2025,
                "tiger_year": 2024,
            },
            "data_sources": {"hud_ami_csv": str(hud)},
        }
    )


@responses.activate
def test_build_ami80_bridge_blends_to_puma(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    responses.add(
        responses.GET,
        f"{BASE_URL}/2024/acs/acs5",
        json=COUNTIES_PAYLOAD,
        match=[query_param_matcher({"get": "NAME"}, strict_match=False)],
    )
    responses.add(
        responses.GET,
        f"{BASE_URL}/2024/acs/acs5",
        json=TRACT_HH_PAYLOAD,
        match=[query_param_matcher({"get": "B11001_001E"}, strict_match=False)],
    )

    out = build_ami80_bridge(cfg)

    assert set(out.columns) == {"PUMA", "hh_size", "AMI80"}
    assert sorted(out["PUMA"].unique()) == ["00800", "00900"]
    # PUMA 00800 is 100% Pueblo (county 08101): hh_size 1 -> 50000, 2 -> 55000.
    p8 = out[out["PUMA"] == "00800"].set_index("hh_size")["AMI80"]
    assert p8.loc[1] == 50000
    assert p8.loc[2] == 55000
    # PUMA 00900 is 100% El Paso (county 08041): hh_size 1 -> 60000.
    assert out[(out["PUMA"] == "00900") & (out["hh_size"] == 1)]["AMI80"].iloc[0] == 60000


@responses.activate
def test_build_ami80_bridge_reuses_csv_cache(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    responses.add(
        responses.GET,
        f"{BASE_URL}/2024/acs/acs5",
        json=COUNTIES_PAYLOAD,
        match=[query_param_matcher({"get": "NAME"}, strict_match=False)],
    )
    responses.add(
        responses.GET,
        f"{BASE_URL}/2024/acs/acs5",
        json=TRACT_HH_PAYLOAD,
        match=[query_param_matcher({"get": "B11001_001E"}, strict_match=False)],
    )

    first = build_ami80_bridge(cfg)
    calls_after_first = len(responses.calls)
    cached = Path(cfg.project.cache_dir) / "ami80_by_puma_08_fy2025.csv"
    assert cached.exists()

    second = build_ami80_bridge(cfg)
    # Second call is served from the CSV cache: no further HTTP.
    assert len(responses.calls) == calls_after_first
    pd.testing.assert_frame_equal(
        first.reset_index(drop=True), second.reset_index(drop=True), check_dtype=False
    )
