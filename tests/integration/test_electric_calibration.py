"""Integration tests for EIA-861 electric calibration inside prepare_household_microdata."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from energy_equity.config import Config
from energy_equity.pums.prepare import prepare_household_microdata
from tests.fixtures.synthetic_pums import (
    synthesize_ami80_by_puma,
    synthesize_pums_housing,
    synthesize_pums_person,
    write_pums_zips,
)


def make_cfg(
    tmp_path: Path, calibration: dict | None = None, eia861_csv: Path | None = None
) -> Config:
    housing = synthesize_pums_housing(n_households=200, seed=7)
    person = synthesize_pums_person(housing, seed=11)
    housing_zip, person_zip = write_pums_zips(tmp_path, housing=housing, person=person)

    payload = {
        "project": {"name": "fixture_run", "output_dir": str(tmp_path / "out")},
        "geography": {
            "state_fips": "08",
            "state_abbr": "CO",
            "service_area": {"shapefile": str(tmp_path / "service.shp")},
        },
        "vintages": {
            "acs_year": 2024,
            "pums_year": 2024,
            "hud_ami_fy": 2025,
            "tiger_year": 2024,
        },
        "data_sources": {
            "hud_ami_csv": str(tmp_path / "noop.csv"),
            "pums_housing_zip": str(housing_zip),
            "pums_person_zip": str(person_zip),
        },
        "weights": {"compute_moe": False},
    }
    if eia861_csv is not None:
        payload["data_sources"]["eia861_csv"] = str(eia861_csv)
    if calibration is not None:
        payload["calibration"] = calibration
    return Config.from_mapping(payload)


SERVICE_SHARES = pd.DataFrame(
    {
        "PUMA": ["00800", "00900"],
        "share_households_in_service": [1.0, 0.5],
        "urban_share_within_service": [float("nan"), float("nan")],
    }
)


def test_no_calibration_block_means_no_diagnostic(tmp_path: Path) -> None:
    cfg = make_cfg(tmp_path)
    md = prepare_household_microdata(cfg, ami80_by_puma=synthesize_ami80_by_puma())
    assert md.electric_calibration is None


def test_diagnostic_only_leaves_costs_unchanged(tmp_path: Path) -> None:
    baseline_cfg = make_cfg(tmp_path)
    baseline = prepare_household_microdata(baseline_cfg, ami80_by_puma=synthesize_ami80_by_puma())

    cfg = make_cfg(tmp_path, calibration={"electric": {"target_annual_bill": 1000.0}})
    md = prepare_household_microdata(
        cfg, ami80_by_puma=synthesize_ami80_by_puma(), service_shares=SERVICE_SHARES
    )

    diag = md.electric_calibration
    assert diag is not None
    assert diag["applied"] is False
    assert diag["scope"] == "service_area"
    assert diag["target_source"] == "config"
    assert diag["factor"] == pytest.approx(1000.0 / diag["observed_avg_annual_bill"])
    pd.testing.assert_series_equal(
        md.df["annual_energy_cost_adj"], baseline.df["annual_energy_cost_adj"]
    )
    pd.testing.assert_series_equal(md.df["energy_burden"], baseline.df["energy_burden"])


def test_apply_rescales_costs_to_target(tmp_path: Path) -> None:
    cfg = make_cfg(
        tmp_path, calibration={"electric": {"target_annual_bill": 1000.0, "apply": True}}
    )
    md = prepare_household_microdata(
        cfg, ami80_by_puma=synthesize_ami80_by_puma(), service_shares=SERVICE_SHARES
    )

    diag = md.electric_calibration
    assert diag is not None and diag["applied"] is True

    df = md.df
    payers = df["ELEP"].notna()
    shares = df["PUMA"].map(SERVICE_SHARES.set_index("PUMA")["share_households_in_service"])
    weights = (df["WGTP"] * shares)[payers]
    observed_after = (df.loc[payers, "annual_electric_cost_adj"] * weights).sum() / weights.sum()
    assert observed_after == pytest.approx(1000.0)
    resummed = (
        df["annual_electric_cost_adj"]
        + df["annual_gas_cost_adj"]
        + df["annual_other_fuel_cost_adj"]
    )
    assert np.allclose(df["annual_energy_cost_adj"], resummed)


def test_target_from_packaged_eia861_table(tmp_path: Path) -> None:
    cfg = make_cfg(tmp_path, calibration={"electric": {"utility_number": 15466}})
    md = prepare_household_microdata(
        cfg, ami80_by_puma=synthesize_ami80_by_puma(), service_shares=SERVICE_SHARES
    )

    diag = md.electric_calibration
    assert diag is not None
    assert diag["target_source"] == "eia861_packaged"
    assert diag["eia861_year"] == cfg.vintages.pums_year
    assert diag["utility_name"] == "Public Service Co of Colorado"
    assert 900 <= diag["target_annual_bill"] <= 1400


def test_target_from_eia861_csv(tmp_path: Path) -> None:
    eia_path = tmp_path / "eia861.csv"
    pd.DataFrame(
        [
            {
                "utility_number": 15466,
                "utility_name": "Public Service Co of Colorado",
                "state": "CO",
                "customer_class": "residential",
                "revenue_thousand_dollars": 1_200_000.0,
                "customers": 1_000_000,
                "year": 2023,
            }
        ]
    ).to_csv(eia_path, index=False)

    cfg = make_cfg(
        tmp_path,
        calibration={"electric": {"utility_number": 15466, "eia861_year": 2023}},
        eia861_csv=eia_path,
    )
    md = prepare_household_microdata(
        cfg, ami80_by_puma=synthesize_ami80_by_puma(), service_shares=SERVICE_SHARES
    )

    diag = md.electric_calibration
    assert diag is not None
    assert diag["target_source"] == "eia861_csv"
    assert diag["target_annual_bill"] == pytest.approx(1200.0)
    assert diag["utility_name"] == "Public Service Co of Colorado"
