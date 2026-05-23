"""Parity test: run the puma_table pipeline against the real Pueblo PUMS data and diff
against the notebook's published `puma_overall.csv`.

Skipped unless the `EE_PARITY_DATA` environment variable is set, pointing at a directory
that contains the notebook output CSVs (typically
`/data/eoc/output/pueblo/service_area_estimates_tract_weighted`). The required inputs
(PUMS ZIPs, the HUD AMI CSV, etc.) also need to be present at the paths configured
below — adjust if your local layout differs.

Run with::

    EE_PARITY_DATA=/data/eoc/output/pueblo/service_area_estimates_tract_weighted \\
    pytest tests/integration/test_puma_table_parity.py -v
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest

from energy_equity.config import Config
from energy_equity.pipelines import puma_table
from energy_equity.pums.prepare import prepare_household_microdata

PARITY_DATA_ENV = "EE_PARITY_DATA"

# Columns produced by the baseline puma_table pipeline that should round-trip with the
# corresponding notebook column. Fixed-charge "_post" / "_new_eb_*" columns are intentionally
# excluded — they belong to the fixed_charge pipeline (Step 9).
PARITY_COUNT_COLUMNS = [
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
]

DEFAULT_PUMS_HOUSING = "/data/eoc/output/pums/pums_2024_1yr/csv_hco.zip"
DEFAULT_PUMS_PERSON = "/data/eoc/output/pums/pums_2024_1yr/csv_pco.zip"
DEFAULT_PUMA_SHAPE = "/data/eoc/output/pums/pums_2024_1yr/tl_2024_08_puma20.zip"
DEFAULT_HUD_AMI = "/data/eoc/output/pums/pums_2024_1yr/income-limits-80-ami.csv"


@pytest.mark.integration
def test_puma_overall_parity_against_notebook(tmp_path: Path) -> None:
    parity_dir_str = os.environ.get(PARITY_DATA_ENV)
    if not parity_dir_str:
        pytest.skip(
            f"Set {PARITY_DATA_ENV} to a directory containing the notebook's puma_overall.csv "
            "(e.g. /data/eoc/output/pueblo/service_area_estimates_tract_weighted) to enable parity tests."
        )
    parity_dir = Path(parity_dir_str)
    notebook_csv = parity_dir / "puma_overall.csv"
    notebook_ami80 = parity_dir / "ami80_by_puma.csv"
    if not notebook_csv.exists():
        pytest.skip(f"Expected notebook output not found: {notebook_csv}")
    if not notebook_ami80.exists():
        pytest.skip(f"Expected notebook AMI bridge not found: {notebook_ami80}")
    for required in (DEFAULT_PUMS_HOUSING, DEFAULT_PUMS_PERSON):
        if not Path(required).exists():
            pytest.skip(f"PUMS input missing: {required}")

    cfg = Config.from_mapping(
        {
            "project": {"name": "parity", "output_dir": str(tmp_path / "out")},
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
                "hud_ami_csv": DEFAULT_HUD_AMI,
                "pums_housing_zip": DEFAULT_PUMS_HOUSING,
                "pums_person_zip": DEFAULT_PUMS_PERSON,
            },
            "thresholds": {
                "ami_method": "blended_threshold",
                "energy_burden_threshold": 0.06,
                "high_energy_burden_threshold": 0.10,
                "missing_cost_rule": "zero",
                "nonpos_income_rule": "exclude",
            },
            "weights": {"compute_moe": True, "replicate_count": 80},
        }
    )

    ami80 = pd.read_csv(notebook_ami80, dtype={"PUMA": "string"})
    ami80["PUMA"] = ami80["PUMA"].astype("string").str.zfill(5)
    microdata = prepare_household_microdata(cfg, ami80_by_puma=ami80)
    written = puma_table.run(cfg, microdata=microdata, write_demographics=False)
    ours = written["puma_overall"].copy()
    ours["PUMA"] = ours["PUMA"].astype(str).str.zfill(5)

    theirs = pd.read_csv(notebook_csv, dtype={"PUMA": "string"})
    theirs["PUMA"] = theirs["PUMA"].astype("string").str.zfill(5)

    # Same PUMAs, same row count.
    assert sorted(ours["PUMA"]) == sorted(theirs["PUMA"]), (
        f"PUMA set mismatch: ours={set(ours['PUMA'])} theirs={set(theirs['PUMA'])}"
    )

    merged = ours.merge(theirs, on="PUMA", suffixes=("_ours", "_theirs"))

    failures: list[str] = []
    for col in PARITY_COUNT_COLUMNS:
        if f"{col}_ours" not in merged.columns or f"{col}_theirs" not in merged.columns:
            failures.append(f"{col}: missing on one side")
            continue
        diff = (merged[f"{col}_ours"] - merged[f"{col}_theirs"]).abs()
        # Count metrics should match to within 1e-9 — sums of integers times floats.
        if diff.max() > 1e-9:
            worst = merged.loc[diff.idxmax(), ["PUMA", f"{col}_ours", f"{col}_theirs"]]
            failures.append(f"{col}: max abs diff {diff.max():.6f} at {worst.to_dict()}")

    # MOEs differ from the notebook by a constant 1.6448536/1.645 ratio (more precise z-90).
    # Allow 0.01% relative tolerance to absorb that.
    moe_cols = [f"{c}_moe90" for c in PARITY_COUNT_COLUMNS]
    for col in moe_cols:
        if f"{col}_ours" not in merged.columns or f"{col}_theirs" not in merged.columns:
            continue
        diff = (merged[f"{col}_ours"] - merged[f"{col}_theirs"]).abs()
        ref = merged[f"{col}_theirs"].abs().clip(lower=1.0)
        rel = (diff / ref).fillna(0.0)
        if rel.max() > 1e-4:
            worst = merged.loc[rel.idxmax(), ["PUMA", f"{col}_ours", f"{col}_theirs"]]
            failures.append(f"{col}: max rel diff {rel.max():.6f} at {worst.to_dict()}")

    assert not failures, "Parity diffs:\n  " + "\n  ".join(failures)
