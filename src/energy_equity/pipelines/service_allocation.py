"""Allocate PUMA-level affordability estimates into a service-area polygon.

Reads:
  - the service-area polygon (from cfg.geography.service_area.shapefile)
  - a TIGER tract shapefile and TIGER PUMA shapefile (cfg.data_sources)
  - tract household counts (from cfg.data_sources.acs_tract_households_csv if provided,
    otherwise fetched via census/api)
  - puma_overall.csv + puma_overall_replicates.csv.gz (from a prior `puma_table` run)
  - optionally the TIGER UAC20 urban-area shapefile for urban/rural splits

Writes:
  - {service}_puma_household_shares.csv  -- (PUMA, share_in_service, urban_share)
  - {service}_by_puma.csv               -- per-PUMA in-service allocations
  - {service}_totals.csv                -- totals for segments all/urban/rural + MOEs
  - {service}_rates.csv                 -- rates with MOEs
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from loguru import logger

from ..census.api import resolve_tract_households
from ..config import Config
from ..geo.allocation import (
    allocate_puma_counts_to_service,
    assign_puma_to_tracts,
    compute_household_weighted_puma_shares,
    dissolve_service_area,
)
from ..io.download import ensure_urban_areas_zip
from ..io.geo import read_tiger_zip
from ..paths import ensure_dir, resolve_cache_dir

DEFAULT_ALLOCATE_METRICS: tuple[str, ...] = (
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
    "hh_rent_burdened_le80_w",
    "hh_rent_burdened_le60_smi_w",
)


def _write(df: pd.DataFrame, path: Path) -> Path:
    df.to_csv(path, index=False)
    logger.info("wrote {}", path.resolve())
    return path


def _load_puma_overall(out_dir: Path) -> tuple[pd.DataFrame, dict[str, np.ndarray]]:
    """Load `puma_overall.csv` + `puma_overall_replicates.csv.gz` produced by puma_table."""
    puma_csv = out_dir / "puma_overall.csv"
    reps_csv = out_dir / "puma_overall_replicates.csv.gz"
    if not puma_csv.exists():
        raise FileNotFoundError(f"{puma_csv} not found. Run `puma_table` pipeline first.")
    puma = pd.read_csv(puma_csv, dtype={"PUMA": "string"})
    puma["PUMA"] = puma["PUMA"].astype(str).str.zfill(5)

    rep_estimates: dict[str, np.ndarray] = {}
    if reps_csv.exists():
        reps = pd.read_csv(reps_csv, dtype={"PUMA": "string"})
        reps["PUMA"] = reps["PUMA"].astype(str).str.zfill(5)
        reps = reps.set_index("PUMA").loc[puma["PUMA"].tolist()]
        # Group columns by metric name (everything before `_rep<NN>`).
        for col in reps.columns:
            if "_rep" not in col:
                continue
            metric = col.rsplit("_rep", 1)[0]
            rep_estimates.setdefault(metric, []).append(col)
        for metric, cols in list(rep_estimates.items()):
            sorted_cols = sorted(cols, key=lambda c: int(c.rsplit("_rep", 1)[1]))
            rep_estimates[metric] = reps[sorted_cols].to_numpy(dtype=float)
    return puma, rep_estimates


def build_service_puma_shares(
    cfg: Config,
    *,
    tract_households: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Compute per-PUMA service-area household shares from geography inputs alone.

    This is the geography-only half of the pipeline (no dependency on puma_table
    outputs), so callers that need the shares early — e.g. bill calibration
    in `prepare_household_microdata` — can build them first and pass the result back
    into `run(..., puma_shares=...)` to avoid recomputing.
    """
    cache_dir = resolve_cache_dir(cfg.project.cache_dir)

    if tract_households is None:
        tract_households = resolve_tract_households(cfg, cache_dir)

    tract_zip = cfg.data_sources.tiger_tract_zip
    puma_zip = cfg.data_sources.tiger_puma_zip
    if tract_zip is None or not Path(tract_zip).exists():
        raise FileNotFoundError(f"TIGER tract ZIP required: {tract_zip}")
    if puma_zip is None or not Path(puma_zip).exists():
        raise FileNotFoundError(f"TIGER PUMA ZIP required: {puma_zip}")

    tracts = read_tiger_zip(tract_zip)
    pumas = read_tiger_zip(puma_zip)
    tracts_with_puma = assign_puma_to_tracts(tracts, pumas)

    service_union = dissolve_service_area(cfg.geography.service_area.shapefile)

    urban_areas: gpd.GeoDataFrame | None = None
    if cfg.pipelines.service_allocation.build_urban_rural_split:
        try:
            uac_zip = ensure_urban_areas_zip(cache_dir, cfg.vintages.tiger_year)
            urban_areas = read_tiger_zip(uac_zip)
        except Exception as exc:
            logger.warning("skipping urban/rural split (could not load UAC20): {}", exc)

    return compute_household_weighted_puma_shares(
        tracts_with_puma, tract_households, service_union, urban_areas
    )


def run(
    cfg: Config,
    *,
    metrics: Sequence[str] = DEFAULT_ALLOCATE_METRICS,
    puma_overall: pd.DataFrame | None = None,
    replicates: dict[str, np.ndarray] | None = None,
    tract_households: pd.DataFrame | None = None,
    service_label: str | None = None,
    puma_shares: pd.DataFrame | None = None,
) -> dict[str, pd.DataFrame]:
    """Run the service-area allocation. Returns dict of (label -> DataFrame)."""
    output_dir = ensure_dir(cfg.project.output_dir)
    label = service_label or Path(cfg.geography.service_area.shapefile).stem

    if puma_overall is None:
        puma_overall, loaded_reps = _load_puma_overall(output_dir)
        if replicates is None:
            replicates = loaded_reps

    if puma_shares is None:
        puma_shares = build_service_puma_shares(cfg, tract_households=tract_households)

    by_puma, totals, rates = allocate_puma_counts_to_service(
        puma_overall,
        puma_shares,
        metrics=list(metrics),
        replicates=replicates,
        compute_moe=cfg.weights.compute_moe,
    )

    written: dict[str, pd.DataFrame] = {}
    _write(puma_shares, output_dir / f"{label}_puma_household_shares.csv")
    written["puma_shares"] = puma_shares

    _write(by_puma, output_dir / f"{label}_by_puma.csv")
    written["by_puma"] = by_puma

    totals.insert(0, "service", label)
    _write(totals, output_dir / f"{label}_totals.csv")
    written["totals"] = totals

    if rates is not None and not rates.empty:
        rates.insert(0, "service", label)
        _write(rates, output_dir / f"{label}_rates.csv")
        written["rates"] = rates

    return written
