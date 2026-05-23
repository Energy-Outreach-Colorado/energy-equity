"""Eligibility analysis pipeline (formerly the PIPP threshold-comparison notebook section).

For a given service area, compares household counts under a "current" energy-burden
threshold vs a "proposed" threshold (Colorado PIPP: 6% vs 2.5%, but configurable for any
state's analogous program). Writes:

  - headline_summary.csv           PIPP eligibility comparison by segment
  - low_income_burden_bands.csv    distribution of <=80% AMI households across bands
  - energy_burden_headline_summary.csv
  - energy_burden_bands.csv
  - demographics_<dim>.csv         PIPP eligibility breakdown by each demographic
  - demographics_<dim>_energy_burden.csv
  - puma_summary.csv               PUMA-level rollup within service area

This is the renamed `pipp_analysis` from the plan — "PIPP" is a CO-specific program
acronym; the cosmetic label is `cfg.pipelines.eligibility_analysis.program_name`.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..config import Config
from ..paths import ensure_dir
from ..pums.prepare import HouseholdMicrodata
from ..tables.summaries import (
    attach_service_weights_and_eligibility_flags,
    build_burden_band_summary,
    build_demographic_table,
    build_energy_burden_band_summary,
    build_energy_burden_demographic_table,
    build_energy_burden_headline_summary,
    build_headline_summary,
    build_puma_summary,
)

DEMOGRAPHIC_COLUMNS: tuple[tuple[str, str], ...] = (
    ("race", "head_race"),
    ("ethnicity", "head_ethnicity"),
    ("gender", "head_gender"),
    ("age", "head_age_group"),
    ("tenure", "tenure"),
    ("household_language", "household_language"),
)


def _write(df: pd.DataFrame, path: Path) -> Path:
    df.to_csv(path, index=False)
    print(f"[OUT] {path.resolve()}")
    return path


def run(
    cfg: Config,
    microdata: HouseholdMicrodata,
    service_shares: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """Run the eligibility analysis and write all output CSVs.

    `service_shares` is the DataFrame returned by `pipelines.service_allocation.run`
    keyed `puma_shares` (or equivalently the `_puma_household_shares.csv` it writes).
    """
    output_dir = ensure_dir(cfg.project.output_dir)
    eligibility = cfg.pipelines.eligibility_analysis

    hh = attach_service_weights_and_eligibility_flags(
        microdata.df.copy(),
        service_shares,
        energy_burden_threshold=cfg.thresholds.energy_burden_threshold,
        high_energy_burden_threshold=cfg.thresholds.high_energy_burden_threshold,
        current_threshold=eligibility.current_threshold,
        proposed_threshold=eligibility.proposed_threshold,
        weight_col=microdata.point_weight_col,
    )

    written: dict[str, pd.DataFrame] = {}

    headline = build_headline_summary(hh)
    _write(headline, output_dir / "headline_summary.csv")
    written["headline_summary"] = headline

    bands = build_burden_band_summary(
        hh,
        current_threshold=eligibility.current_threshold,
        proposed_threshold=eligibility.proposed_threshold,
    )
    _write(bands, output_dir / "low_income_burden_bands.csv")
    written["low_income_burden_bands"] = bands

    eb_headline = build_energy_burden_headline_summary(hh)
    _write(eb_headline, output_dir / "energy_burden_headline_summary.csv")
    written["energy_burden_headline_summary"] = eb_headline

    eb_bands = build_energy_burden_band_summary(
        hh,
        energy_burden_threshold=cfg.thresholds.energy_burden_threshold,
        high_energy_burden_threshold=cfg.thresholds.high_energy_burden_threshold,
    )
    _write(eb_bands, output_dir / "energy_burden_bands.csv")
    written["energy_burden_bands"] = eb_bands

    # PIPP-style demographic tables.
    for dim_name, col in DEMOGRAPHIC_COLUMNS:
        if col not in hh.columns:
            continue
        tab = build_demographic_table(hh, category_col=col)
        _write(tab, output_dir / f"demographics_{dim_name}.csv")
        written[f"demographics_{dim_name}"] = tab

    # Energy-burden demographic tables (separate from PIPP -- include all burden-valid hhs).
    for dim_name, col in DEMOGRAPHIC_COLUMNS:
        if col not in hh.columns:
            continue
        tab = build_energy_burden_demographic_table(hh, category_col=col)
        _write(tab, output_dir / f"demographics_{dim_name}_energy_burden.csv")
        written[f"demographics_{dim_name}_energy_burden"] = tab

    puma_summary = build_puma_summary(hh)
    if microdata.puma_lookup is not None:
        puma_summary = puma_summary.merge(microdata.puma_lookup, on="PUMA", how="left")
    _write(puma_summary, output_dir / "puma_summary.csv")
    written["puma_summary"] = puma_summary

    written["households_weighted"] = hh
    return written
