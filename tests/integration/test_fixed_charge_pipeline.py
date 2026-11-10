"""Integration test for fixed_charge pipeline using synthetic fixtures."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from energy_equity.config import Config
from energy_equity.pipelines import fixed_charge, puma_table, service_allocation
from energy_equity.pums.prepare import prepare_household_microdata
from tests.fixtures.synthetic_geo import make_geo_fixtures, make_tract_households
from tests.fixtures.synthetic_pums import (
    synthesize_ami80_by_puma,
    synthesize_pums_housing,
    synthesize_pums_person,
    write_pums_zips,
)


@pytest.fixture
def cfg_with_geo(tmp_path: Path) -> Config:
    housing = synthesize_pums_housing(n_households=300, seed=7)
    person = synthesize_pums_person(housing, seed=11)
    housing_zip, person_zip = write_pums_zips(tmp_path, housing=housing, person=person)
    geo = make_geo_fixtures(tmp_path)
    return Config.from_mapping(
        {
            "project": {"name": "fixture_run", "output_dir": str(tmp_path / "out")},
            "geography": {
                "state_fips": "08",
                "state_abbr": "CO",
                "service_area": {"shapefile": str(geo["service_path"])},
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
                "tiger_tract_zip": str(geo["tract_zip"]),
                "tiger_puma_zip": str(geo["puma_zip"]),
            },
            "weights": {"compute_moe": True, "replicate_count": 80},
            "pipelines": {
                "service_allocation": {"build_urban_rural_split": False},
                "fixed_charge": {
                    "monthly_increase": 5.0,
                    "apply_only_if_gas_positive": True,
                    "scenario_sweep": [0.0, 2.5, 5.0, 10.0],
                    "delta_thresholds_pp": [0.25, 0.50, 1.00],
                },
            },
        }
    )


def test_fixed_charge_pipeline_produces_outputs(cfg_with_geo: Config) -> None:
    ami80 = synthesize_ami80_by_puma()
    md = prepare_household_microdata(cfg_with_geo, ami80_by_puma=ami80)
    pt = puma_table.run(cfg_with_geo, microdata=md, write_demographics=False)
    sa = service_allocation.run(
        cfg_with_geo,
        puma_overall=pt["puma_overall"],
        replicates=pt["puma_overall_replicates"],  # type: ignore[arg-type]
        tract_households=make_tract_households(),
        service_label="test_service",
    )
    fc = fixed_charge.run(cfg_with_geo, microdata=md, service_shares=sa["puma_shares"])

    out_dir = Path(cfg_with_geo.project.output_dir)
    assert (out_dir / "fixed_charge_headline_summary.csv").exists()
    assert (out_dir / "delta_sensitivity_summary.csv").exists()
    assert (out_dir / "near_threshold_summary.csv").exists()
    assert (out_dir / "scenario_sweep.csv").exists()

    sweep = fc["scenario_sweep"]
    # 4 increases x 4 populations x 1 segment ("all", since urban/rural off) = 16 rows.
    assert len(sweep) == 16
    # Newly burdened should be 0 at increase=0 and >=0 at each higher amount.
    row_zero = sweep[
        (sweep["population"] == "All households") & (sweep["monthly_fixed_charge_increase"] == 0.0)
    ]
    assert row_zero["newly_energy_burdened_households"].iloc[0] == pytest.approx(0.0)
    # Newly burdened is monotone in the increase (each row at a higher monthly_increase >= the prior).
    for pop in sweep["population"].unique():
        subset = sweep[(sweep["population"] == pop) & (sweep["segment"] == "all")].sort_values(
            "monthly_fixed_charge_increase"
        )
        new_eb = subset["newly_energy_burdened_households"].to_numpy()
        diffs = new_eb[1:] - new_eb[:-1]
        assert (diffs >= -1e-9).all(), f"non-monotone newly_eb for population {pop}: {new_eb}"


def test_delta_sensitivity_monotone_in_threshold(cfg_with_geo: Config) -> None:
    """Higher delta-pp threshold should yield fewer households."""
    ami80 = synthesize_ami80_by_puma()
    md = prepare_household_microdata(cfg_with_geo, ami80_by_puma=ami80)
    pt = puma_table.run(cfg_with_geo, microdata=md, write_demographics=False)
    sa = service_allocation.run(
        cfg_with_geo,
        puma_overall=pt["puma_overall"],
        replicates=pt["puma_overall_replicates"],  # type: ignore[arg-type]
        tract_households=make_tract_households(),
        service_label="test_service",
    )
    fc = fixed_charge.run(cfg_with_geo, microdata=md, service_shares=sa["puma_shares"])
    sens = fc["delta_sensitivity_summary"]
    for pop in sens["population"].unique():
        subset = sens[(sens["population"] == pop) & (sens["segment"] == "all")].sort_values(
            "delta_threshold_percentage_points"
        )
        counts = subset["households"].to_numpy()
        diffs = counts[1:] - counts[:-1]
        # Higher threshold = fewer or equal households.
        assert (diffs <= 1e-9).all(), f"non-monotone delta sensitivity for pop {pop}: {counts}"


def test_percent_kind_with_electric_basis(cfg_with_geo: Config) -> None:
    """A percent increase on an electric-only burden basis runs end to end."""
    cfg = Config.from_mapping(
        {
            **cfg_with_geo.model_dump(mode="json"),
            "thresholds": {
                "burden_cost_basis": "electric",
                "energy_burden_threshold": 0.03,
                "high_energy_burden_threshold": 0.06,
            },
            "pipelines": {
                "service_allocation": {"build_urban_rural_split": False},
                "fixed_charge": {
                    "kind": "percent",
                    "percent_increase": 0.147,
                    "percent_of": "electric",
                    "apply_only_if_gas_positive": False,
                    "scenario_sweep": [0.0, 0.088, 0.147],
                    "delta_thresholds_pp": [0.25, 0.50, 1.00],
                },
            },
        }
    )
    ami80 = synthesize_ami80_by_puma()
    md = prepare_household_microdata(cfg, ami80_by_puma=ami80)
    assert md.burden_cost_col == "annual_electric_cost_adj"
    valid = md.df["burden_valid"]
    expected = md.df.loc[valid, "annual_electric_cost_adj"] / md.df.loc[valid, "income_adjusted"]
    assert (md.df.loc[valid, "energy_burden"] - expected).abs().max() < 1e-12
    assert not md.df.loc[md.df["ELEP"].isna(), "burden_valid"].any()
    pt = puma_table.run(cfg, microdata=md, write_demographics=False)
    sa = service_allocation.run(
        cfg,
        puma_overall=pt["puma_overall"],
        replicates=pt["puma_overall_replicates"],  # type: ignore[arg-type]
        tract_households=make_tract_households(),
        service_label="test_service",
    )
    fc = fixed_charge.run(cfg, microdata=md, service_shares=sa["puma_shares"])
    headline = fc["fixed_charge_headline_summary"]
    row = headline[(headline["segment"] == "all") & (headline["population"] == "All households")]
    hh = md.df
    assert "monthly_fixed_charge_increase" not in fc["scenario_sweep"].columns
    assert row["annual_increase_total"].iloc[0] >= 0.0
    assert (hh["ELEP"].isna() | (hh["ELEP"] > 0) | (hh["ELEP"] == 0)).all()
    near = fc["near_threshold_summary"]
    assert set(near["margin_percentage_points"]) == {0.25, 0.5, 1.0}


def test_percent_kind_with_gas_basis(cfg_with_geo: Config) -> None:
    """A percent increase on a gas-only burden basis runs end to end."""
    cfg = Config.from_mapping(
        {
            **cfg_with_geo.model_dump(mode="json"),
            "thresholds": {
                "burden_cost_basis": "gas",
                "energy_burden_threshold": 0.03,
                "high_energy_burden_threshold": 0.06,
            },
            "pipelines": {
                "service_allocation": {"build_urban_rural_split": False},
                "fixed_charge": {
                    "kind": "percent",
                    "percent_increase": 0.10,
                    "percent_of": "gas",
                    "apply_only_if_gas_positive": False,
                    "scenario_sweep": [0.0, 0.05, 0.10],
                    "delta_thresholds_pp": [0.25, 0.50, 1.00],
                },
            },
        }
    )
    ami80 = synthesize_ami80_by_puma()
    md = prepare_household_microdata(cfg, ami80_by_puma=ami80)
    assert md.burden_cost_col == "annual_gas_cost_adj"
    valid = md.df["burden_valid"]
    assert valid.any()
    expected = md.df.loc[valid, "annual_gas_cost_adj"] / md.df.loc[valid, "income_adjusted"]
    assert (md.df.loc[valid, "energy_burden"] - expected).abs().max() < 1e-12
    assert not md.df.loc[md.df["GASP"].isna(), "burden_valid"].any()
    pt = puma_table.run(cfg, microdata=md, write_demographics=False)
    sa = service_allocation.run(
        cfg,
        puma_overall=pt["puma_overall"],
        replicates=pt["puma_overall_replicates"],  # type: ignore[arg-type]
        tract_households=make_tract_households(),
        service_label="test_service",
    )
    fc = fixed_charge.run(cfg, microdata=md, service_shares=sa["puma_shares"])
    headline = fc["fixed_charge_headline_summary"]
    row = headline[(headline["segment"] == "all") & (headline["population"] == "All households")]
    assert row["annual_increase_total"].iloc[0] >= 0.0
    sweep = fc["scenario_sweep"]
    assert set(sweep["increase_amount"]) == {0.0, 0.05, 0.10}


def test_electric_basis_excludes_combined_bills(tmp_path: Path, cfg_with_geo: Config) -> None:
    """Households paying gas through the electric bill get no electric-only burden."""
    housing = synthesize_pums_housing(n_households=300, seed=7)
    housing["GASFP"] = np.where(np.arange(len(housing)) % 4 == 0, 2, 3)
    person = synthesize_pums_person(housing, seed=11)
    inputs = tmp_path / "combined"
    inputs.mkdir()
    housing_zip, person_zip = write_pums_zips(inputs, housing=housing, person=person)
    base = cfg_with_geo.model_dump(mode="json")
    sources = {
        **base["data_sources"],
        "pums_housing_zip": str(housing_zip),
        "pums_person_zip": str(person_zip),
    }
    ami80 = synthesize_ami80_by_puma()

    def prepare(basis: str) -> pd.DataFrame:
        cfg = Config.from_mapping(
            {**base, "data_sources": sources, "thresholds": {"burden_cost_basis": basis}}
        )
        return prepare_household_microdata(cfg, ami80_by_puma=ami80).df

    electric = prepare("electric")
    combined = electric["GASFP"] == 2
    assert combined.any()
    assert not electric.loc[combined, "burden_valid"].any()
    assert electric.loc[~combined, "burden_valid"].any()
    total = prepare("total")
    assert total.loc[total["GASFP"] == 2, "burden_valid"].any()
