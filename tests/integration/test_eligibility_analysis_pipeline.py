"""Integration test for the eligibility_analysis pipeline using synthetic fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from energy_equity.config import Config
from energy_equity.pipelines import eligibility_analysis, puma_table, service_allocation
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
    housing = synthesize_pums_housing(n_households=200, seed=7)
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
                "service_allocation": {
                    "allocator": "tract_household_weighted",
                    "build_urban_rural_split": False,
                },
                "eligibility_analysis": {
                    "current_threshold": 0.06,
                    "proposed_threshold": 0.025,
                },
            },
        }
    )


def test_eligibility_analysis_produces_all_outputs(cfg_with_geo: Config) -> None:
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
    ea = eligibility_analysis.run(cfg_with_geo, microdata=md, service_shares=sa["puma_shares"])

    out_dir = Path(cfg_with_geo.project.output_dir)
    expected_files = [
        "headline_summary.csv",
        "low_income_burden_bands.csv",
        "energy_burden_headline_summary.csv",
        "energy_burden_bands.csv",
        "demographics_race.csv",
        "demographics_age.csv",
        "demographics_tenure.csv",
        "demographics_race_energy_burden.csv",
        "puma_summary.csv",
        "affordability_gap.csv",
        "affordability_gap_by_puma.csv",
    ]
    for fname in expected_files:
        assert (out_dir / fname).exists(), f"missing output: {fname}"


def test_headline_summary_invariants(cfg_with_geo: Config) -> None:
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
    ea = eligibility_analysis.run(cfg_with_geo, microdata=md, service_shares=sa["puma_shares"])

    headline = ea["headline_summary"]
    all_row = headline[headline["segment"] == "all"].iloc[0]

    # Proposed must be >= current (lower threshold = more eligible).
    assert all_row["proposed_eligible_households"] >= all_row["current_eligible_households"]
    # Newly added = proposed - current.
    assert all_row["newly_added_households"] == pytest.approx(
        all_row["proposed_eligible_households"] - all_row["current_eligible_households"]
    )
    # Low income >= proposed eligible (eligibility is gated by AMI).
    assert all_row["low_income_households"] >= all_row["proposed_eligible_households"]


def test_affordability_gap_invariants(cfg_with_geo: Config) -> None:
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
    ea = eligibility_analysis.run(cfg_with_geo, microdata=md, service_shares=sa["puma_shares"])

    gap = ea["affordability_gap"]
    assert set(gap["threshold"].unique()) == {0.06, 0.10}
    assert (gap["total_gap_dollars"] >= 0).all()
    assert (gap["households_in_gap"] <= gap["gap_valid_households"] + 1e-9).all()
    assert gap["households_in_gap_moe90"].notna().all()
    assert gap["total_gap_dollars_moe90"].notna().all()

    all_rows = gap[(gap["segment"] == "all")].set_index(["population", "threshold"])
    assert (
        all_rows.loc[("All households", 0.10), "total_gap_dollars"]
        <= all_rows.loc[("All households", 0.06), "total_gap_dollars"]
    )
    assert (
        all_rows.loc[("<=80% AMI", 0.06), "total_gap_dollars"]
        <= all_rows.loc[("All households", 0.06), "total_gap_dollars"]
    )

    by_puma = ea["affordability_gap_by_puma"]
    t6 = by_puma[by_puma["threshold"] == 0.06]
    assert t6["total_gap_dollars"].sum() == pytest.approx(
        all_rows.loc[("All households", 0.06), "total_gap_dollars"]
    )
