"""Tests for unit-level PUMA shares and replicate lookup in the allocator."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from energy_equity.geo.allocation import (
    allocate_puma_counts_to_service,
    assign_puma_to_tracts,
    compute_household_weighted_puma_shares,
    dissolve_service_area,
    puma_shares_from_units,
)
from energy_equity.io.geo import CRS_EQUAL_AREA, read_tiger_zip
from tests.fixtures.synthetic_geo import make_geo_fixtures, make_tract_households


def _units() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "PUMA": ["00800", "00800", "00900", "00900", "00900"],
            "households": [100.0, 300.0, 200.0, 200.0, 600.0],
            "share_in_area": [1.0, 0.5, 0.0, 0.25, 0.0],
        }
    )


def test_puma_shares_from_units_household_weighted() -> None:
    shares = puma_shares_from_units(_units()).set_index("PUMA")
    assert shares.loc["00800", "households_total"] == pytest.approx(400.0)
    assert shares.loc["00800", "households_in_service"] == pytest.approx(250.0)
    assert shares.loc["00800", "share_households_in_service"] == pytest.approx(0.625)
    assert shares.loc["00900", "share_households_in_service"] == pytest.approx(0.05)
    assert shares["urban_share_within_service"].isna().all()


def test_puma_shares_from_units_clips_and_pads_codes() -> None:
    units = pd.DataFrame({"PUMA": ["800"], "households": [10.0], "share_in_area": [1.7]})
    shares = puma_shares_from_units(units)
    assert shares["PUMA"].tolist() == ["00800"]
    assert shares["share_households_in_service"].iloc[0] == pytest.approx(1.0)


def test_puma_shares_from_units_requires_columns() -> None:
    with pytest.raises(ValueError, match="share_in_area"):
        puma_shares_from_units(_units().drop(columns="share_in_area"))


def test_puma_shares_from_units_matches_tract_allocator(tmp_path: Path) -> None:
    """Feeding tract area fractions through the unit helper reproduces the tract allocator."""
    geo = make_geo_fixtures(tmp_path)
    tracts = assign_puma_to_tracts(
        read_tiger_zip(geo["tract_zip"]), read_tiger_zip(geo["puma_zip"])
    )
    service = dissolve_service_area(geo["service_path"])
    tract_hh = make_tract_households()

    expected = compute_household_weighted_puma_shares(tracts, tract_hh, service)

    t = tracts.to_crs(CRS_EQUAL_AREA)
    s = service.to_crs(CRS_EQUAL_AREA).geometry.iloc[0]
    units = pd.DataFrame(
        {
            "PUMA": t["PUMA"],
            "tract_geoid": t["tract_geoid"],
            "share_in_area": (t.geometry.intersection(s).area / t.geometry.area).to_numpy(),
        }
    ).merge(tract_hh, on="tract_geoid")
    got = puma_shares_from_units(units)

    cols = ["households_total", "households_in_service", "share_households_in_service"]
    left = expected.sort_values("PUMA").reset_index(drop=True)
    right = got.sort_values("PUMA").reset_index(drop=True)
    assert left["PUMA"].tolist() == right["PUMA"].tolist()
    np.testing.assert_allclose(left[cols].to_numpy(), right[cols].to_numpy())


def _allocation_inputs() -> tuple[pd.DataFrame, pd.DataFrame, dict[str, np.ndarray]]:
    rng = np.random.default_rng(3)
    puma_overall = pd.DataFrame(
        {"PUMA": ["00800", "00900"], "hh_total_w": [1000.0, 2000.0], "hh_eb_w": [100.0, 500.0]}
    )
    shares = puma_shares_from_units(_units())
    reps = {
        "hh_total": puma_overall[["hh_total_w"]].to_numpy() + rng.normal(0, 20, (2, 80)),
        "hh_eb": puma_overall[["hh_eb_w"]].to_numpy() + rng.normal(0, 10, (2, 80)),
    }
    return puma_overall, shares, reps


@pytest.mark.parametrize("suffixed", [False, True])
def test_allocator_accepts_both_replicate_key_spellings(suffixed: bool) -> None:
    puma_overall, shares, reps = _allocation_inputs()
    if suffixed:
        reps = {f"{k}_w": v for k, v in reps.items()}
    _, totals, _ = allocate_puma_counts_to_service(
        puma_overall, shares, metrics=["hh_total_w", "hh_eb_w"], replicates=reps
    )
    moe = totals.loc[totals["segment"] == "all", "hh_total_w_in_service_moe90"].iloc[0]
    assert np.isfinite(moe) and moe > 0


def test_allocator_custom_rate_definitions() -> None:
    puma_overall, shares, reps = _allocation_inputs()
    _, _, rates = allocate_puma_counts_to_service(
        puma_overall,
        shares,
        metrics=["hh_total_w", "hh_eb_w"],
        replicates=reps,
        rate_definitions=[("share_eb_of_all", "hh_eb_w", "hh_total_w")],
    )
    assert rates["rate"].tolist() == ["share_eb_of_all"]
    expected = (0.625 * 100 + 0.05 * 500) / (0.625 * 1000 + 0.05 * 2000)
    assert rates["estimate"].iloc[0] == pytest.approx(expected)
    assert np.isfinite(rates["moe90"].iloc[0])
