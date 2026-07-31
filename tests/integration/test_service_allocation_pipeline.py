"""End-to-end test of the service_allocation pipeline using synthetic geo + PUMS fixtures."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from energy_equity.config import Config
from energy_equity.geo.allocation import (
    assign_puma_to_tracts,
    compute_household_weighted_puma_shares,
    dissolve_service_area,
)
from energy_equity.io.geo import read_tiger_zip
from energy_equity.pipelines import puma_table, service_allocation
from energy_equity.pums.prepare import prepare_household_microdata
from tests.fixtures.synthetic_geo import make_geo_fixtures, make_tract_households
from tests.fixtures.synthetic_pums import (
    synthesize_ami80_by_puma,
    synthesize_pums_housing,
    synthesize_pums_person,
    write_pums_zips,
)


def test_assign_puma_to_tracts_basic(tmp_path: Path) -> None:
    geo = make_geo_fixtures(tmp_path)
    tracts = read_tiger_zip(geo["tract_zip"])
    pumas = read_tiger_zip(geo["puma_zip"])
    out = assign_puma_to_tracts(tracts, pumas)
    assert sorted(out["PUMA"].dropna().tolist()) == ["00800", "00800", "00900", "00900"]


def test_compute_household_weighted_puma_shares_clean_geometry(tmp_path: Path) -> None:
    """With the synthetic 50/50 overlap, each PUMA should have a 25% share in service."""
    geo = make_geo_fixtures(tmp_path)
    tracts = read_tiger_zip(geo["tract_zip"])
    pumas = read_tiger_zip(geo["puma_zip"])
    tracts_with_puma = assign_puma_to_tracts(tracts, pumas)

    service_union = dissolve_service_area(geo["service_path"])
    tract_hh = make_tract_households()
    shares = compute_household_weighted_puma_shares(tracts_with_puma, tract_hh, service_union)

    shares = shares.sort_values("PUMA").reset_index(drop=True)
    assert sorted(shares["PUMA"].tolist()) == ["00800", "00900"]
    # Half of T2 (500 hh) is in service for PUMA 00800 with 2000 total -> 25%.
    np.testing.assert_allclose(shares["share_households_in_service"], 0.25, atol=1e-6)
    np.testing.assert_allclose(shares["households_in_service"], 500.0, atol=1e-6)


@pytest.fixture
def cfg_with_geo(tmp_path: Path) -> tuple[Config, dict[str, Path]]:
    """Build a Config and synthetic data covering both PUMS and geo fixtures."""
    housing = synthesize_pums_housing(n_households=120, seed=7)
    person = synthesize_pums_person(housing, seed=11)
    housing_zip, person_zip = write_pums_zips(tmp_path, housing=housing, person=person)
    geo = make_geo_fixtures(tmp_path)

    cfg = Config.from_mapping(
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
                "hud_ami_csv": str(tmp_path / "noop_ami.csv"),
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
            },
        }
    )
    return cfg, geo


def test_service_allocation_pipeline_end_to_end(cfg_with_geo) -> None:
    cfg, geo = cfg_with_geo
    ami80 = synthesize_ami80_by_puma()
    md = prepare_household_microdata(cfg, ami80_by_puma=ami80)
    pt = puma_table.run(cfg, microdata=md)
    puma_overall = pt["puma_overall"]
    reps = {
        m.removesuffix("_w"): mat
        for m, mat in pt["puma_overall_replicates"].items()  # type: ignore[union-attr]
    }
    # Hand off in-memory so we skip the round-trip through CSV.
    written = service_allocation.run(
        cfg,
        puma_overall=puma_overall,
        replicates=pt["puma_overall_replicates"],  # type: ignore[arg-type]
        tract_households=make_tract_households(),
        service_label="test_service",
    )

    # Files were written.
    out_dir = Path(cfg.project.output_dir)
    assert (out_dir / "test_service_puma_household_shares.csv").exists()
    assert (out_dir / "test_service_totals.csv").exists()
    assert (out_dir / "test_service_rates.csv").exists()

    # Totals sum: with both PUMAs at 25% share, total service hh ≈ 0.25 * (puma1 + puma2).
    totals = written["totals"]
    total_all = totals.loc[totals["segment"] == "all", "hh_total_w_in_service"].iloc[0]
    state_total = puma_overall["hh_total_w"].sum()
    assert total_all == pytest.approx(0.25 * state_total, rel=1e-6)


def test_precomputed_puma_shares_match_internal_build(cfg_with_geo) -> None:
    cfg, geo = cfg_with_geo
    ami80 = synthesize_ami80_by_puma()
    md = prepare_household_microdata(cfg, ami80_by_puma=ami80)
    pt = puma_table.run(cfg, microdata=md, write_demographics=False)

    shares = service_allocation.build_service_puma_shares(
        cfg, tract_households=make_tract_households()
    )
    with_precomputed = service_allocation.run(
        cfg,
        puma_overall=pt["puma_overall"],
        replicates=pt["puma_overall_replicates"],  # type: ignore[arg-type]
        puma_shares=shares,
        service_label="precomputed",
    )
    internal = service_allocation.run(
        cfg,
        puma_overall=pt["puma_overall"],
        replicates=pt["puma_overall_replicates"],  # type: ignore[arg-type]
        tract_households=make_tract_households(),
        service_label="internal",
    )
    for key in ("puma_shares", "by_puma"):
        left = with_precomputed[key].reset_index(drop=True)
        right = internal[key].reset_index(drop=True)
        np.testing.assert_allclose(
            left.select_dtypes("number").to_numpy(),
            right.select_dtypes("number").to_numpy(),
        )


def test_service_allocation_moe_nonnegative(cfg_with_geo) -> None:
    cfg, geo = cfg_with_geo
    ami80 = synthesize_ami80_by_puma()
    md = prepare_household_microdata(cfg, ami80_by_puma=ami80)
    pt = puma_table.run(cfg, microdata=md, write_demographics=False)
    written = service_allocation.run(
        cfg,
        puma_overall=pt["puma_overall"],
        replicates=pt["puma_overall_replicates"],  # type: ignore[arg-type]
        tract_households=make_tract_households(),
        service_label="test_service",
    )
    totals = written["totals"]
    moe_cols = [c for c in totals.columns if c.endswith("_moe90")]
    assert moe_cols  # MOEs were produced
    for col in moe_cols:
        finite = totals[col].dropna()
        if len(finite):
            assert (finite >= 0).all()
