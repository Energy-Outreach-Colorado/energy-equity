"""Parity test for the service_allocation pipeline against the notebook's outputs.

Gated by EE_PARITY_DATA (must point at a directory containing the notebook's
`service_area_puma_household_shares.csv` and `service_area_totals.csv`). The pipeline
reads the same PUMS, AMI, and TIGER inputs the notebook used.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest

from energy_equity.config import Config
from energy_equity.pipelines import puma_table, service_allocation
from energy_equity.pums.prepare import prepare_household_microdata

PARITY_DATA_ENV = "EE_PARITY_DATA"
DEFAULT_PUMS_HOUSING = "/data/eoc/output/pums/pums_2024_1yr/csv_hco.zip"
DEFAULT_PUMS_PERSON = "/data/eoc/output/pums/pums_2024_1yr/csv_pco.zip"
DEFAULT_PUMA_SHAPE = "/data/eoc/output/pums/pums_2024_1yr/tl_2024_08_puma20.zip"
DEFAULT_TRACT_SHAPE = "/data/eoc/output/pums/pums_2024_1yr/tl_2024_08_tract.zip"
DEFAULT_HUD_AMI = "/data/eoc/output/pums/pums_2024_1yr/income-limits-80-ami.csv"
DEFAULT_SERVICE_SHAPE = "/data/eoc/geospatial/pueblo_county_08101_shapefile/pueblo_county_08101.shp"


PARITY_TOTAL_COLUMNS = [
    "hh_total_w_in_service",
    "hh_le80_w_in_service",
    "hh_le60_smi_w_in_service",
    "hh_eb_w_in_service",
    "hh_heb_w_in_service",
    "hh_eb_le80_w_in_service",
    "hh_heb_le80_w_in_service",
    "hh_eb_le60_smi_w_in_service",
    "hh_heb_le60_smi_w_in_service",
    "hh_rent_burdened_w_in_service",
    "hh_severe_rent_burdened_w_in_service",
    "hh_rent_burdened_le80_w_in_service",
    "hh_rent_burdened_le60_smi_w_in_service",
]


@pytest.mark.integration
def test_service_allocation_parity_against_notebook(tmp_path: Path) -> None:
    parity_dir_str = os.environ.get(PARITY_DATA_ENV)
    if not parity_dir_str:
        pytest.skip(f"Set {PARITY_DATA_ENV} to enable service-allocation parity test.")
    parity_dir = Path(parity_dir_str)
    notebook_shares = parity_dir / "service_area_puma_household_shares.csv"
    notebook_totals = parity_dir / "service_area_totals.csv"
    notebook_ami80 = parity_dir / "ami80_by_puma.csv"
    if not notebook_shares.exists() or not notebook_totals.exists():
        pytest.skip(f"Notebook outputs missing in {parity_dir}")
    for required in (
        DEFAULT_PUMS_HOUSING,
        DEFAULT_PUMS_PERSON,
        DEFAULT_PUMA_SHAPE,
        DEFAULT_TRACT_SHAPE,
        DEFAULT_SERVICE_SHAPE,
    ):
        if not Path(required).exists():
            pytest.skip(f"Required input missing: {required}")

    cfg = Config.from_mapping(
        {
            "project": {"name": "parity", "output_dir": str(tmp_path / "out")},
            "geography": {
                "state_fips": "08",
                "state_abbr": "CO",
                "service_area": {"shapefile": DEFAULT_SERVICE_SHAPE},
            },
            "vintages": {
                "acs_year": 2024,
                "pums_year": 2024,
                "hud_ami_fy": 2025,
                "tiger_year": 2024,
            },
            "data_sources": {
                "hud_ami_csv": DEFAULT_HUD_AMI,
                "pums_housing_zip": DEFAULT_PUMS_HOUSING,
                "pums_person_zip": DEFAULT_PUMS_PERSON,
                "tiger_puma_zip": DEFAULT_PUMA_SHAPE,
                "tiger_tract_zip": DEFAULT_TRACT_SHAPE,
            },
            "weights": {"compute_moe": True, "replicate_count": 80},
            "pipelines": {
                "service_allocation": {
                    "allocator": "tract_household_weighted",
                    "build_urban_rural_split": True,
                },
            },
        }
    )

    ami80 = pd.read_csv(notebook_ami80, dtype={"PUMA": "string"})
    ami80["PUMA"] = ami80["PUMA"].astype("string").str.zfill(5)
    md = prepare_household_microdata(cfg, ami80_by_puma=ami80)
    pt = puma_table.run(cfg, microdata=md, write_demographics=False)

    # Load the notebook's cached tract household counts (avoids needing a Census API key
    # in the test environment; the conftest blocks live network for non-network-marked tests).
    cached_tract_hh = parity_dir / "cache" / "acs_tract_households_co.csv"
    tract_hh: pd.DataFrame | None = None
    if cached_tract_hh.exists():
        tract_hh = pd.read_csv(cached_tract_hh, dtype={"tract_geoid": "string"})

    written = service_allocation.run(
        cfg,
        puma_overall=pt["puma_overall"],
        replicates=pt["puma_overall_replicates"],  # type: ignore[arg-type]
        tract_households=tract_hh,
        service_label="service_area",
    )

    # Share parity: should match very tightly.
    ours_shares = written["puma_shares"].copy()
    ours_shares["PUMA"] = ours_shares["PUMA"].astype(str).str.zfill(5)
    theirs_shares = pd.read_csv(notebook_shares, dtype={"PUMA": "string"})
    theirs_shares["PUMA"] = theirs_shares["PUMA"].astype("string").str.zfill(5)
    m = ours_shares.merge(theirs_shares, on="PUMA", suffixes=("_ours", "_theirs"))
    assert len(m) == len(theirs_shares)

    share_diffs: list[str] = []
    for col in ("share_households_in_service", "urban_share_within_service"):
        if f"{col}_ours" not in m.columns:
            continue
        diff = (m[f"{col}_ours"].fillna(0.0) - m[f"{col}_theirs"].fillna(0.0)).abs()
        if diff.max() > 0.005:  # 0.5 pp share tolerance — small projection / urban-area drift
            worst = m.loc[diff.idxmax(), ["PUMA", f"{col}_ours", f"{col}_theirs"]]
            share_diffs.append(f"{col}: max diff {diff.max():.5f} at {worst.to_dict()}")
    assert not share_diffs, "PUMA share diffs:\n  " + "\n  ".join(share_diffs)

    # Total parity per segment + metric.
    ours_totals = written["totals"]
    theirs_totals = pd.read_csv(notebook_totals)
    merged_totals = ours_totals.merge(
        theirs_totals, on=["service", "segment"], suffixes=("_ours", "_theirs")
    )
    assert len(merged_totals) == len(ours_totals)

    total_failures: list[str] = []
    for col in PARITY_TOTAL_COLUMNS:
        co, ct = f"{col}_ours", f"{col}_theirs"
        if co not in merged_totals.columns or ct not in merged_totals.columns:
            continue
        diff = (merged_totals[co] - merged_totals[ct]).abs()
        # 0.5% relative tolerance for service-area totals — shares are derived from
        # spatial intersections so tiny float-precision drift is expected.
        ref = merged_totals[ct].abs().clip(lower=1.0)
        rel = (diff / ref).fillna(0.0)
        if rel.max() > 0.005:
            worst = merged_totals.loc[rel.idxmax(), ["segment", co, ct]]
            total_failures.append(f"{col}: max rel diff {rel.max():.5f} at {worst.to_dict()}")
    assert not total_failures, "Service-totals diffs:\n  " + "\n  ".join(total_failures)
