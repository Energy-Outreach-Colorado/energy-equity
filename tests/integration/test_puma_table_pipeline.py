"""End-to-end test of the puma_table pipeline against synthetic PUMS data."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import responses
from responses.matchers import query_param_matcher

from energy_equity.census.api import BASE_URL
from energy_equity.config import Config
from energy_equity.pipelines import puma_table
from energy_equity.pums.prepare import prepare_household_microdata
from tests.fixtures.synthetic_pums import (
    synthesize_ami80_by_puma,
    synthesize_pums_housing,
    synthesize_pums_person,
    write_pums_zips,
)


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    housing = synthesize_pums_housing(n_households=120, seed=7)
    person = synthesize_pums_person(housing, seed=11)
    housing_zip, person_zip = write_pums_zips(tmp_path, housing=housing, person=person)

    return Config.from_mapping(
        {
            "project": {"name": "fixture_run", "output_dir": str(tmp_path / "out")},
            "geography": {
                "state_fips": "08",
                "state_abbr": "CO",
                "service_area": {"shapefile": str(tmp_path / "noop.shp")},
            },
            "vintages": {
                "acs_year": 2024,
                "pums_year": 2024,
                "hud_ami_fy": 2025,
                "tiger_year": 2024,
            },
            "data_sources": {
                "hud_ami_csv": str(tmp_path / "noop_ami.csv"),
                "pums_housing_zip": str(housing_zip),
                "pums_person_zip": str(person_zip),
            },
            "weights": {"compute_moe": True, "replicate_count": 80},
        }
    )


def test_puma_table_pipeline_produces_expected_outputs(cfg: Config, tmp_path: Path) -> None:
    ami80 = synthesize_ami80_by_puma()
    microdata = prepare_household_microdata(cfg, ami80_by_puma=ami80)
    written = puma_table.run(cfg, microdata=microdata)

    out_dir = tmp_path / "out"
    assert (out_dir / "puma_overall.csv").exists()
    assert (out_dir / "state_overall.csv").exists()
    assert (out_dir / "puma_by_race.csv").exists()
    assert (out_dir / "state_by_age.csv").exists()

    puma_overall = pd.read_csv(out_dir / "puma_overall.csv")
    # Two PUMAs in the fixture.
    assert sorted(puma_overall["PUMA"].astype(str).str.zfill(5).unique().tolist()) == [
        "00800",
        "00900",
    ]

    expected_metric_cols = {
        "hh_total_w",
        "hh_burden_valid_w",
        "hh_eb_w",
        "hh_heb_w",
        "hh_ami_valid_w",
        "hh_le80_w",
        "hh_eb_le80_w",
        "hh_heb_le80_w",
        "hh_smi_valid_w",
        "hh_le60_smi_w",
        "hh_eb_le60_smi_w",
        "hh_heb_le60_smi_w",
        "hh_renter_w",
        "hh_rent_valid_w",
        "hh_rent_burdened_w",
        "hh_severe_rent_burdened_w",
    }
    assert expected_metric_cols.issubset(set(puma_overall.columns))
    # MOE columns should exist for each metric.
    assert "hh_total_w_moe90" in puma_overall.columns
    assert "pct_eb_among_le80_moe90" in puma_overall.columns

    # MOEs should be non-negative.
    moe_cols = [c for c in puma_overall.columns if c.endswith("_moe90")]
    for col in moe_cols:
        finite = puma_overall[col].dropna()
        if len(finite):
            assert (finite >= 0).all(), f"Negative MOE in {col}: {finite.min()}"


def test_state_overall_aggregates_match_puma_sums(cfg: Config) -> None:
    ami80 = synthesize_ami80_by_puma()
    microdata = prepare_household_microdata(cfg, ami80_by_puma=ami80)
    written = puma_table.run(cfg, microdata=microdata, write_demographics=False)

    state = written["state_overall"]
    puma = written["puma_overall"]
    # PUMA-level hh_total_w should sum to the state total.
    assert state["hh_total_w"].iloc[0] == pytest.approx(puma["hh_total_w"].sum())
    # n_unweighted similarly.
    assert state["n_unweighted"].iloc[0] == puma["n_unweighted"].sum()


def test_small_n_suppression_flags_low_count_groups(cfg: Config) -> None:
    """With suppression at min_unweighted_n=30 and 60 households per PUMA, at least PUMA-level groups should be unflagged."""
    ami80 = synthesize_ami80_by_puma()
    microdata = prepare_household_microdata(cfg, ami80_by_puma=ami80)
    written = puma_table.run(cfg, microdata=microdata, write_demographics=False)
    puma = written["puma_overall"]
    assert "small_n_flag" in puma.columns
    # 120 households across 2 PUMAs ~ 60/PUMA -> well above 30.
    assert (~puma["small_n_flag"]).all()


@responses.activate
def test_puma_table_autobuilds_ami_bridge_when_not_passed(tmp_path: Path) -> None:
    """Regression for the CLI path: puma_table.run(cfg) with no ami80_by_puma should
    auto-build the AMI bridge (mocked Census) and produce non-zero <=80% AMI counts —
    previously this path crashed in the hh_ami_valid metric."""
    housing = synthesize_pums_housing(n_households=120, seed=7)
    person = synthesize_pums_person(housing, seed=11)
    housing_zip, person_zip = write_pums_zips(tmp_path, housing=housing, person=person)

    # Real HUD AMI CSV with generous thresholds so a meaningful share qualifies.
    hud = tmp_path / "hud_ami.csv"
    pd.DataFrame(
        {
            "County": ["Pueblo", "El Paso"],
            **{str(s): [80000 + s * 5000, 80000 + s * 5000] for s in range(1, 9)},
        }
    ).to_csv(hud, index=False)

    cache = tmp_path / "cache"
    cache.mkdir()
    # Tracts -> PUMAs that match the synthetic PUMS (00800, 00900).
    (cache / "2020_Census_Tract_to_2020_PUMA.txt").write_text(
        "STATEFP,COUNTYFP,TRACTCE,PUMA5CE\n08,101,000100,00800\n08,041,000300,00900\n"
    )

    responses.add(
        responses.GET,
        f"{BASE_URL}/2024/acs/acs5",
        json=[
            ["NAME", "state", "county"],
            ["Pueblo County, Colorado", "08", "101"],
            ["El Paso County, Colorado", "08", "041"],
        ],
        match=[query_param_matcher({"get": "NAME"}, strict_match=False)],
    )
    responses.add(
        responses.GET,
        f"{BASE_URL}/2024/acs/acs5",
        json=[
            ["B11001_001E", "state", "county", "tract"],
            ["1500", "08", "101", "000100"],
            ["1500", "08", "041", "000300"],
        ],
        match=[query_param_matcher({"get": "B11001_001E"}, strict_match=False)],
    )

    cfg = Config.from_mapping(
        {
            "project": {
                "name": "autobuild",
                "output_dir": str(tmp_path / "out"),
                "cache_dir": str(cache),
            },
            "geography": {
                "state_fips": "08",
                "state_abbr": "CO",
                "service_area": {"shapefile": str(tmp_path / "noop.shp")},
            },
            "vintages": {
                "acs_year": 2024,
                "pums_year": 2024,
                "hud_ami_fy": 2025,
                "tiger_year": 2024,
            },
            "data_sources": {
                "hud_ami_csv": str(hud),
                "pums_housing_zip": str(housing_zip),
                "pums_person_zip": str(person_zip),
            },
            "weights": {"compute_moe": False},
        }
    )

    # No microdata, no ami80_by_puma -> prepare_household_microdata auto-builds the bridge.
    written = puma_table.run(cfg, write_demographics=False)
    puma = written["puma_overall"]
    # Bridge populated ami_weight for every matched household -> hh_ami_valid == hh_total.
    assert puma["hh_ami_valid_w"].sum() == pytest.approx(puma["hh_total_w"].sum())
    # And a non-trivial share qualifies as <=80% AMI.
    assert puma["hh_le80_w"].sum() > 0
