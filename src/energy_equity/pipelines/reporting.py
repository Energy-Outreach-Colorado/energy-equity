"""Persuasive reporting metrics pipeline.

Reads cached B19001 (income-bin) data per tract, allocates to the service area using the
same tract-area shares produced by `service_allocation`, and emits:

  - income_distribution_service_vs_state.csv     16-bin distribution comparison
  - income_cutpoint_shares.csv                  cumulative shares below common cutoffs
  - regressivity_table.csv                      fixed-charge as % of income by bin

Optional Plotly figures are gated by the `viz` extra; absence of plotly disables them.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from ..census.api import CensusClient
from ..census.cache import CensusCache
from ..config import Config
from ..geo.allocation import dissolve_service_area
from ..io.geo import CRS_EQUAL_AREA, read_tiger_zip
from ..paths import ensure_dir, resolve_cache_dir
from ..reporting.income import (
    aggregate_b19001,
    build_cutpoint_shares,
    build_income_distribution_comparison,
    build_regressivity_table,
)

DEFAULT_CUTOFFS: tuple[float, ...] = (15_000, 25_000, 50_000, 75_000, 100_000)


def _write(df: pd.DataFrame, path: Path) -> Path:
    df.to_csv(path, index=False)
    print(f"[OUT] {path.resolve()}")
    return path


def _build_tract_service_share(
    tracts: gpd.GeoDataFrame, service_union: gpd.GeoDataFrame
) -> pd.DataFrame:
    """Per-tract fraction of area inside the service polygon (equal-area projection)."""
    t = tracts.to_crs(CRS_EQUAL_AREA).copy()
    s = service_union.to_crs(CRS_EQUAL_AREA)
    sg = s.geometry.iloc[0]
    tract_area = t.geometry.area
    overlap = t.geometry.intersection(sg).area
    geoid_col = "GEOID" if "GEOID" in t.columns else "GEOID20"
    return pd.DataFrame(
        {
            "tract_geoid": t[geoid_col].astype(str).str.zfill(11),
            "share_tract_in_service": np.where(
                tract_area > 0, (overlap / tract_area).clip(0, 1), 0.0
            ),
        }
    )


def run(
    cfg: Config,
    *,
    b19001_per_tract: pd.DataFrame | None = None,
    tract_service_shares: pd.DataFrame | None = None,
    cutoffs: Sequence[float] = DEFAULT_CUTOFFS,
) -> dict[str, pd.DataFrame]:
    """Run the persuasive reporting pipeline.

    Caller may pass pre-loaded B19001 data and tract-service shares to skip the network
    and geospatial steps (useful for tests). Otherwise the pipeline fetches B19001 via the
    Census API and computes tract-service shares from the tract + service shapefiles.
    """
    output_dir = ensure_dir(cfg.project.output_dir)
    cache_dir = resolve_cache_dir(cfg.project.cache_dir)
    fc = cfg.pipelines.fixed_charge

    if b19001_per_tract is None:
        client = CensusClient(
            api_key=os.environ.get(cfg.census_api.key_env),
            cache=CensusCache(cache_dir),
            rate_limit_sec=cfg.census_api.rate_limit_sec,
        )
        b19001_per_tract = client.fetch_b19001_tracts(
            cfg.geography.state_fips, cfg.vintages.acs_year, cfg.vintages.acs_span
        )

    if tract_service_shares is None:
        tract_zip = cfg.data_sources.tiger_tract_zip
        if tract_zip is None or not Path(tract_zip).exists():
            raise FileNotFoundError(f"TIGER tract ZIP required: {tract_zip}")
        tracts = read_tiger_zip(tract_zip)
        service_union = dissolve_service_area(cfg.geography.service_area.shapefile)
        tract_service_shares = _build_tract_service_share(tracts, service_union)

    merged = b19001_per_tract.merge(tract_service_shares, on="tract_geoid", how="left")
    merged["share_tract_in_service"] = pd.to_numeric(
        merged.get("share_tract_in_service", 0.0), errors="coerce"
    ).fillna(0.0)

    service = aggregate_b19001(merged, weight_col="share_tract_in_service")
    statewide = aggregate_b19001(merged, weight_col=None)

    comparison = build_income_distribution_comparison(service, statewide)
    cutpoints = build_cutpoint_shares(service, statewide, cutoffs=cutoffs)
    regressivity = build_regressivity_table(
        annual_fixed_charge_increase=12.0 * float(fc.monthly_increase)
    )

    written: dict[str, pd.DataFrame] = {}
    _write(comparison, output_dir / "income_distribution_service_vs_state.csv")
    written["income_distribution_service_vs_state"] = comparison
    _write(cutpoints, output_dir / "income_cutpoint_shares.csv")
    written["income_cutpoint_shares"] = cutpoints
    _write(regressivity, output_dir / "regressivity_table.csv")
    written["regressivity_table"] = regressivity
    return written
