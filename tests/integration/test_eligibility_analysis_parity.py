"""Parity test for the eligibility_analysis pipeline against the notebook's PIPP export."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest

from energy_equity.config import Config
from energy_equity.pipelines import eligibility_analysis, puma_table, service_allocation
from energy_equity.pums.prepare import prepare_household_microdata

PARITY_DATA_ENV = "EE_PARITY_DATA"

DEFAULT_PUMS_HOUSING = "/data/eoc/output/pums/pums_2024_1yr/csv_hco.zip"
DEFAULT_PUMS_PERSON = "/data/eoc/output/pums/pums_2024_1yr/csv_pco.zip"
DEFAULT_PUMA_SHAPE = "/data/eoc/output/pums/pums_2024_1yr/tl_2024_08_puma20.zip"
DEFAULT_TRACT_SHAPE = "/data/eoc/output/pums/pums_2024_1yr/tl_2024_08_tract.zip"
DEFAULT_HUD_AMI = "/data/eoc/output/pums/pums_2024_1yr/income-limits-80-ami.csv"
DEFAULT_SERVICE_SHAPE = "/data/eoc/geospatial/pueblo_county_08101_shapefile/pueblo_county_08101.shp"


PARITY_HEADLINE_COLUMNS = [
    "service_households",
    "low_income_households",
    "current_eligible_households",
    "proposed_eligible_households",
    "newly_added_households",
]


@pytest.mark.integration
def test_eligibility_analysis_headline_parity(tmp_path: Path) -> None:
    parity_dir_str = os.environ.get(PARITY_DATA_ENV)
    if not parity_dir_str:
        pytest.skip(f"Set {PARITY_DATA_ENV} to enable eligibility parity test.")
    parity_dir = Path(parity_dir_str)
    headline_csv = parity_dir / "pipp_threshold_analysis_exports" / "headline_summary.csv"
    notebook_ami80 = parity_dir / "ami80_by_puma.csv"
    cached_tract_hh = parity_dir / "cache" / "acs_tract_households_co.csv"
    if not headline_csv.exists():
        pytest.skip(f"Notebook headline_summary.csv not found at {headline_csv}")
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
                "service_allocation": {"build_urban_rural_split": True},
                "eligibility_analysis": {"current_threshold": 0.06, "proposed_threshold": 0.025},
            },
        }
    )

    ami80 = pd.read_csv(notebook_ami80, dtype={"PUMA": "string"})
    ami80["PUMA"] = ami80["PUMA"].astype("string").str.zfill(5)
    md = prepare_household_microdata(cfg, ami80_by_puma=ami80)
    pt = puma_table.run(cfg, microdata=md, write_demographics=False)

    tract_hh = (
        pd.read_csv(cached_tract_hh, dtype={"tract_geoid": "string"})
        if cached_tract_hh.exists()
        else None
    )
    sa = service_allocation.run(
        cfg,
        puma_overall=pt["puma_overall"],
        replicates=pt["puma_overall_replicates"],  # type: ignore[arg-type]
        tract_households=tract_hh,
        service_label="service_area",
    )
    ea = eligibility_analysis.run(cfg, microdata=md, service_shares=sa["puma_shares"])

    ours = ea["headline_summary"].copy()
    theirs = pd.read_csv(headline_csv)
    merged = ours.merge(theirs, on="segment", suffixes=("_ours", "_theirs"))

    failures: list[str] = []
    for col in PARITY_HEADLINE_COLUMNS:
        co, ct = f"{col}_ours", f"{col}_theirs"
        if co not in merged.columns or ct not in merged.columns:
            continue
        diff = (merged[co] - merged[ct]).abs()
        ref = merged[ct].abs().clip(lower=1.0)
        rel = (diff / ref).fillna(0.0)
        if rel.max() > 1e-6:
            worst = merged.loc[rel.idxmax(), ["segment", co, ct]]
            failures.append(f"{col}: max rel diff {rel.max():.6f} at {worst.to_dict()}")
    assert not failures, "Headline parity diffs:\n  " + "\n  ".join(failures)
