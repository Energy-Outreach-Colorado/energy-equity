"""Tests for the AMI county->PUMA bridge and per-household AMI flag."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from energy_equity.thresholds.ami import (
    attach_ami_blended_threshold,
    attach_ami_expected_probability,
    build_ami80_by_puma,
    build_county_name_to_fips3,
    compute_puma_county_household_weights,
    load_county_ami_limits_long_from_csv,
)


def test_build_county_name_to_fips3_parses_census_name() -> None:
    counties = pd.DataFrame(
        {
            "name": [
                "Pueblo County, Colorado",
                "El Paso County, Colorado",
                "Denver County, Colorado",
            ],
            "county_fips": ["101", "041", "031"],
        }
    )
    out = build_county_name_to_fips3(counties)
    assert out == {"pueblo": "101", "el paso": "041", "denver": "031"}


COUNTY_NAME_TO_FIPS3 = {
    "pueblo": "101",
    "el paso": "041",
    "denver": "031",
}


@pytest.fixture
def ami_csv(tmp_path: Path) -> Path:
    df = pd.DataFrame(
        {
            "County": ["Pueblo", "El Paso", "Denver"],
            "1": [50000, 60000, 70000],
            "2": [55000, 65000, 75000],
            "3": [60000, 70000, 80000],
        }
    )
    path = tmp_path / "ami.csv"
    df.to_csv(path, index=False)
    return path


def test_load_county_ami_limits_long(ami_csv: Path) -> None:
    long = load_county_ami_limits_long_from_csv(
        ami_csv, state_fips="08", county_name_to_fips3=COUNTY_NAME_TO_FIPS3
    )
    assert set(long.columns) == {"county_fips5", "hh_size", "ami80"}
    assert long.loc[long["county_fips5"] == "08101", "ami80"].tolist() == [50000, 55000, 60000]
    assert long.shape == (9, 3)


def test_load_county_ami_limits_handles_county_suffix(tmp_path: Path) -> None:
    """Counties spelled "Pueblo County" should normalize to "pueblo"."""
    df = pd.DataFrame({"County": ["Pueblo County"], "1": [50000]})
    path = tmp_path / "ami.csv"
    df.to_csv(path, index=False)
    long = load_county_ami_limits_long_from_csv(
        path, state_fips="08", county_name_to_fips3=COUNTY_NAME_TO_FIPS3
    )
    assert long["county_fips5"].iloc[0] == "08101"


def test_load_county_ami_limits_raises_on_unknown_county(tmp_path: Path) -> None:
    df = pd.DataFrame({"County": ["Atlantis"], "1": [50000]})
    path = tmp_path / "ami.csv"
    df.to_csv(path, index=False)
    with pytest.raises(ValueError, match="did not match"):
        load_county_ami_limits_long_from_csv(
            path, state_fips="08", county_name_to_fips3=COUNTY_NAME_TO_FIPS3
        )


def test_compute_puma_county_household_weights() -> None:
    """A PUMA spanning two counties should have weights summing to 1.0."""
    tract_puma = pd.DataFrame(
        {
            "tract_geoid": ["08101000100", "08101000200", "08041000300"],
            "county_fips5": ["08101", "08101", "08041"],
            "PUMA": ["00800", "00800", "00800"],
        }
    )
    tract_hh = pd.DataFrame(
        {
            "tract_geoid": ["08101000100", "08101000200", "08041000300"],
            "households": [1000, 500, 1500],
        }
    )
    pc = compute_puma_county_household_weights(tract_puma, tract_hh)
    # Pueblo has 1500 households in this PUMA, El Paso 1500 -> 50/50 split.
    pueblo_weight = pc.loc[pc["county_fips5"] == "08101", "weight_hh"].iloc[0]
    elpaso_weight = pc.loc[pc["county_fips5"] == "08041", "weight_hh"].iloc[0]
    assert pueblo_weight == pytest.approx(0.5)
    assert elpaso_weight == pytest.approx(0.5)


def test_build_ami80_by_puma_blends_county_limits() -> None:
    pc = pd.DataFrame(
        {
            "PUMA": ["00800", "00800"],
            "county_fips5": ["08101", "08041"],
            "weight_hh": [0.5, 0.5],
            "hh_in_county_within_puma": [1500, 1500],
            "hh_in_puma_total": [3000, 3000],
        }
    )
    county_ami = pd.DataFrame(
        {
            "county_fips5": ["08101", "08101", "08041", "08041"],
            "hh_size": [1, 2, 1, 2],
            "ami80": [50000.0, 55000.0, 60000.0, 65000.0],
        }
    )
    out = build_ami80_by_puma(pc, county_ami)
    # 50/50 blend of 50k & 60k = 55k for hh_size=1.
    assert out.loc[out["hh_size"] == 1, "AMI80"].iloc[0] == 55000


def test_attach_ami_blended_threshold_flags_low_income() -> None:
    df = pd.DataFrame(
        {
            "SERIALNO": ["a", "b"],
            "PUMA": ["00800", "00800"],
            "NP": [2, 2],
            "income_adjusted": [40000.0, 90000.0],
        }
    )
    ami80 = pd.DataFrame({"PUMA": ["00800"], "hh_size": [2], "AMI80": [60000]})
    df = attach_ami_blended_threshold(df, ami80)
    assert df["AMI80"].iloc[0] == 60000
    assert bool(df["le_80_ami"].iloc[0]) is True
    assert bool(df["le_80_ami"].iloc[1]) is False
    assert df["ami_weight"].iloc[0] == pytest.approx(1.0)
    assert df["ami_weight"].iloc[1] == pytest.approx(0.0)


def test_attach_ami_blended_threshold_clips_large_households() -> None:
    """Households with NP greater than the published max get clipped to max, not NaN."""
    df = pd.DataFrame(
        {
            "SERIALNO": ["big"],
            "PUMA": ["00800"],
            "NP": [12],  # bigger than max in ami table
            "income_adjusted": [40000.0],
        }
    )
    ami80 = pd.DataFrame({"PUMA": ["00800", "00800"], "hh_size": [1, 2], "AMI80": [50000, 55000]})
    df = attach_ami_blended_threshold(df, ami80)
    assert df["AMI80"].iloc[0] == 55000  # clipped to max published size (2)
    assert df["hh_size_for_ami"].iloc[0] == 2


def test_attach_ami_expected_probability_fractional() -> None:
    """When a PUMA spans two counties with different thresholds, mid-income hh gets a fractional weight."""
    df = pd.DataFrame(
        {
            "SERIALNO": ["x"],
            "PUMA": ["00800"],
            "NP": [2],
            "income_adjusted": [
                57000.0
            ],  # qualifies in El Paso (60k limit), not Pueblo (55k limit)
        }
    )
    pc = pd.DataFrame(
        {
            "PUMA": ["00800", "00800"],
            "county_fips5": ["08101", "08041"],
            "weight_hh": [0.4, 0.6],  # 40% Pueblo, 60% El Paso
            "hh_in_county_within_puma": [400, 600],
            "hh_in_puma_total": [1000, 1000],
        }
    )
    county_ami = pd.DataFrame(
        {
            "county_fips5": ["08101", "08041"],
            "hh_size": [2, 2],
            "ami80": [55000, 60000],
        }
    )
    df = attach_ami_expected_probability(df, pc, county_ami)
    # Eligible only in El Paso (60% of PUMA) -> ami_weight = 0.6.
    assert df["ami_weight"].iloc[0] == pytest.approx(0.6)
