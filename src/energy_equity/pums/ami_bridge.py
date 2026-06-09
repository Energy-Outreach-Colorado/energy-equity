"""Build the PUMA-level 80% AMI table from config inputs.

This is the cfg-driven orchestration that chains the pure builders in
`energy_equity.thresholds.ami` with Census lookups, so callers (the CLI via
`prepare_household_microdata`) get a ready-to-use `ami80_by_puma` without hand-wiring the
HUD CSV, the tract->PUMA relationship file, and ACS tract household counts.

It lives in `pums/` (not `pipelines/`) so `pums.prepare` can call it without inverting the
package's dependency direction (`pums -> census/thresholds` is acyclic).

Result is cached to ``{cache_dir}/ami80_by_puma_{state_fips}_fy{hud_ami_fy}.csv`` and
reused on subsequent runs, so a user may also drop in a precomputed file there.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from loguru import logger

from ..census.api import CensusClient, fetch_with_year_fallback, resolve_tract_households
from ..config import Config
from ..paths import resolve_cache_dir
from ..thresholds.ami import (
    build_ami80_by_puma,
    build_county_name_to_fips3,
    compute_puma_county_household_weights,
    load_county_ami_limits_long_from_csv,
    load_tract_to_puma_relationship,
)


def build_ami80_bridge(
    cfg: Config,
    *,
    cache_dir: str | Path | None = None,
    tract_households: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Build (or load from cache) the PUMA-level AMI80 table. Columns: PUMA, hh_size, AMI80.

    Steps: fetch county FIPS (with ACS year fallback) -> normalize the HUD AMI CSV to a
    county/hh_size long table -> tract->PUMA relationship -> ACS tract household counts ->
    household-weighted county mix per PUMA -> blend county limits to PUMA level.
    """
    cache_dir = resolve_cache_dir(
        Path(cache_dir) if cache_dir is not None else cfg.project.cache_dir
    )
    cached = cache_dir / f"ami80_by_puma_{cfg.geography.state_fips}_fy{cfg.vintages.hud_ami_fy}.csv"
    if cached.exists():
        logger.info("loaded AMI bridge from cache {}", cached)
        out = pd.read_csv(cached, dtype={"PUMA": "string"})
        out["PUMA"] = out["PUMA"].astype("string").str.zfill(5)
        return out

    client = CensusClient.from_env(
        cache_dir,
        key_env=cfg.census_api.key_env,
        rate_limit_sec=cfg.census_api.rate_limit_sec,
    )

    counties, _year = fetch_with_year_fallback(
        lambda y: client.fetch_counties(cfg.geography.state_fips, y, cfg.vintages.acs_span),
        start_year=cfg.vintages.acs_year,
        fallback_years=3,
    )
    name_to_fips3 = build_county_name_to_fips3(counties)

    county_ami_long = load_county_ami_limits_long_from_csv(
        cfg.data_sources.hud_ami_csv,
        state_fips=cfg.geography.state_fips,
        county_name_to_fips3=name_to_fips3,
        scalar=cfg.thresholds.ami_limit_scalar,
    )

    tract_puma = load_tract_to_puma_relationship(cache_dir, cfg.geography.state_fips)
    if tract_households is None:
        tract_households = resolve_tract_households(cfg, cache_dir, client=client)

    puma_county_weights = compute_puma_county_household_weights(tract_puma, tract_households)
    ami80 = build_ami80_by_puma(puma_county_weights, county_ami_long)

    ami80.to_csv(cached, index=False)
    logger.info("built AMI bridge ({} PUMA rows) -> {}", len(ami80), cached)
    return ami80
