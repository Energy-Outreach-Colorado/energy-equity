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
from loguru import logger

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
    logger.info("wrote {}", path.resolve())
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

    # Census parsers emit `geoid`; the downstream join key is `tract_geoid` (same rename
    # resolve_tract_households applies). Normalize before merging.
    if "tract_geoid" not in b19001_per_tract.columns and "geoid" in b19001_per_tract.columns:
        b19001_per_tract = b19001_per_tract.rename(columns={"geoid": "tract_geoid"})

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

    _render_figures(cfg, output_dir, comparison=comparison, regressivity=regressivity)
    return written


def _render_figures(
    cfg: Config,
    output_dir: Path,
    *,
    comparison: pd.DataFrame,
    regressivity: pd.DataFrame,
) -> None:
    """Render the figures named in cfg to output_dir/figures/ as PNGs.

    Reads the other pipelines' CSVs from output_dir (they ran earlier in `run all`). Each
    figure's inputs are checked individually: a missing input or unreadable layer logs a
    warning and skips just that figure. If plotly isn't installed, all figures are skipped
    with a single warning. Figures never fail the run.
    """
    from ..reporting import figures as F

    requested = list(cfg.pipelines.reporting.figures)
    if not requested:
        return
    if not F.HAS_PLOTLY:
        logger.warning(
            "plotly not installed; skipping {} figure(s). Install the viz extra: "
            "`uv sync --extra viz` (or pip install 'energy-equity[viz]').",
            len(requested),
        )
        return

    fig_dir = ensure_dir(output_dir / "figures")

    def _load(name: str) -> pd.DataFrame | None:
        path = output_dir / name
        if not path.exists():
            return None
        return pd.read_csv(path)

    def _emit(fig_name: str, fname: str, build) -> None:
        try:
            fig = build()
            if fig is None:
                return
            F.save_figure(fig, fig_dir / fname)
            logger.info("wrote {}", (fig_dir / fname).resolve())
        except Exception as exc:  # noqa: BLE001 - figures must never fail the run
            logger.warning("skipping figure {} ({}): {}", fig_name, fname, exc)

    if "income_comparison" in requested:
        _emit(
            "income_comparison",
            "income_comparison.png",
            lambda: F.fig_income_comparison(comparison),
        )
    if "regressivity_curve" in requested:
        _emit(
            "regressivity_curve",
            "regressivity_curve.png",
            lambda: F.fig_regressivity_curve(regressivity),
        )

    if "scenario_sweep" in requested:
        sweep = _load("scenario_sweep.csv")
        if sweep is None:
            logger.warning(
                "skipping scenario_sweep figure: scenario_sweep.csv not found (run fixed-charge)"
            )
        else:
            _emit("scenario_sweep", "scenario_sweep.png", lambda: F.fig_scenario_sweep(sweep))

    if "waterfall" in requested:
        headline = _load("fixed_charge_headline_summary.csv")
        if headline is None:
            logger.warning("skipping waterfall figure: fixed_charge_headline_summary.csv not found")
        else:
            _emit(
                "waterfall", "energy_burden_waterfall.png", lambda: F.fig_burden_waterfall(headline)
            )

    if "demographic_bars" in requested:
        for dim, col in (
            ("race", "head_race"),
            ("age", "head_age_group"),
            ("ethnicity", "head_ethnicity"),
            ("tenure", "tenure"),
        ):
            demo = _load(f"demographics_{dim}.csv")
            if demo is None or col not in demo.columns:
                logger.warning("skipping demographics_{} figure: input missing", dim)
                continue
            _emit(
                f"demographic_bars[{dim}]",
                f"demographics_{dim}.png",
                lambda demo=demo, col=col: F.fig_demographic_bars(demo, category_col=col),
            )

    if "burden_bands" in requested:
        li = _load("low_income_burden_bands.csv")
        if li is not None:
            _emit(
                "burden_bands",
                "burden_bands.png",
                lambda: F.fig_burden_bands(
                    li, band_col="burden_band", share_col="share_of_low_income"
                ),
            )
        eb = _load("energy_burden_bands.csv")
        if eb is not None:
            _emit(
                "energy_burden_bands",
                "energy_burden_bands.png",
                lambda: F.fig_burden_bands(
                    eb, band_col="energy_burden_band", share_col="share_of_all_service_households"
                ),
            )

    if "choropleth" in requested:
        _render_choropleths(cfg, fig_dir, output_dir, emit=_emit)


def _render_choropleths(cfg: Config, fig_dir: Path, output_dir: Path, *, emit) -> None:
    """PUMA-level choropleths from puma_summary.csv + the TIGER PUMA layer."""
    from ..reporting import figures as F

    puma_summary_path = output_dir / "puma_summary.csv"
    puma_zip = cfg.data_sources.tiger_puma_zip
    if not puma_summary_path.exists():
        logger.warning("skipping choropleths: puma_summary.csv not found (run eligibility)")
        return
    if puma_zip is None or not Path(puma_zip).exists():
        logger.warning("skipping choropleths: TIGER PUMA zip not configured/found")
        return

    puma_gdf = read_tiger_zip(puma_zip)
    values = pd.read_csv(puma_summary_path, dtype={"PUMA": "string"})

    for value_col, fname, title in (
        (
            "current_eligible_households",
            "choropleth_current_eligible.png",
            "Current-eligible households by PUMA",
        ),
        (
            "newly_added_households",
            "choropleth_newly_added.png",
            "Newly-added eligible households by PUMA",
        ),
    ):
        if value_col not in values.columns:
            logger.warning("skipping choropleth {}: column {} missing", fname, value_col)
            continue
        emit(
            f"choropleth[{value_col}]",
            fname,
            lambda value_col=value_col, title=title: F.fig_puma_choropleth(
                puma_gdf, values, value_col=value_col, title=title
            ),
        )
