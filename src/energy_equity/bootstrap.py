"""Pre-warm the cache with every Census input a run downloads, and report on each one.

`fetch_inputs(cfg)` is what `ee data fetch --config ...` runs. It walks the same inputs a
full `ee run all` would fetch on first use, so a later run works offline:

- the PUMS housing and person ZIPs and the TIGER tract and PUMA ZIPs, unless the config
  points at local files (`io.census_inputs`)
- the national TIGER urban-area ZIP, when the urban/rural split is on
- the 2020 tract to 2020 PUMA relationship file used by the AMI bridge
- the ACS county list, tract household counts and B19001 tract incomes from the Census
  API, which need the key named by `census_api.key_env`

Each input becomes one `FetchResult` whose status is `configured` (a local file from the
config), `cached` (already in the cache), `downloaded`, `planned` (a dry run), `skipped`
(the API calls when no key is set or `include_api` is false) or `failed`. A failure is
recorded and the walk continues, so one report shows everything that is missing.
`force` downloads the files again but leaves the Census API responses cached, since
published ACS tables do not change.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .census.api import CensusClient, fetch_with_year_fallback, resolve_tract_households
from .config import Config
from .io.census_inputs import (
    is_cached_zip,
    pums_cache_path,
    pums_url,
    resolve_pums_zip,
    resolve_tiger_zip,
    tiger_cache_path,
    tiger_urls,
)
from .io.download import ensure_urban_areas_zip, urban_areas_cache_path, urban_areas_url
from .paths import resolve_cache_dir
from .thresholds.ami import CENSUS_TRACT_TO_PUMA_URLS, load_tract_to_puma_relationship

STATUSES = ("configured", "cached", "downloaded", "planned", "skipped", "failed")


@dataclass
class FetchResult:
    """What happened to one input."""

    name: str
    status: str
    path: Path | None = None
    detail: str = ""

    @property
    def size_bytes(self) -> int | None:
        """The file size when the path exists, else None."""
        if self.path is not None and self.path.is_file():
            return self.path.stat().st_size
        return None


def _file_input(
    name: str,
    *,
    configured: Path | None,
    dest: Path,
    urls: list[str],
    run: Callable[[], Path],
    dry_run: bool,
    force: bool,
    is_cached: Callable[[Path], bool] = is_cached_zip,
) -> FetchResult:
    if configured is not None:
        try:
            return FetchResult(name, "configured", run())
        except Exception as exc:
            return FetchResult(name, "failed", Path(configured), str(exc))
    if is_cached(dest) and not force:
        return FetchResult(name, "cached", dest)
    if dry_run:
        return FetchResult(name, "planned", dest, " or ".join(urls))
    try:
        return FetchResult(name, "downloaded", run())
    except Exception as exc:
        return FetchResult(name, "failed", dest, str(exc))


def _api_input(
    name: str, *, dest: Path, run: Callable[[], object], dry_run: bool, skip_reason: str | None
) -> FetchResult:
    if dest.exists():
        return FetchResult(name, "cached", dest)
    if skip_reason is not None:
        return FetchResult(name, "skipped", None, skip_reason)
    if dry_run:
        return FetchResult(name, "planned", dest, "Census API")
    try:
        run()
    except Exception as exc:
        return FetchResult(name, "failed", None, str(exc))
    return FetchResult(name, "downloaded", dest if dest.exists() else None)


def fetch_inputs(
    cfg: Config, *, force: bool = False, dry_run: bool = False, include_api: bool = True
) -> list[FetchResult]:
    """Fetch every Census input `cfg` needs into the cache and report what happened."""
    cache_dir = resolve_cache_dir(cfg.project.cache_dir)
    geo, years, sources = cfg.geography, cfg.vintages, cfg.data_sources
    results: list[FetchResult] = []

    for kind in ("housing", "person"):
        results.append(
            _file_input(
                f"PUMS {kind}",
                configured=getattr(sources, f"pums_{kind}_zip"),
                dest=pums_cache_path(
                    cache_dir,
                    year=years.pums_year,
                    span=years.pums_span,
                    kind=kind,
                    state_abbr=geo.state_abbr,
                ),
                urls=[pums_url(years.pums_year, years.pums_span, kind, geo.state_abbr)],
                run=lambda kind=kind: resolve_pums_zip(cfg, kind, force=force),
                dry_run=dry_run,
                force=force,
            )
        )
    for layer in ("tract", "puma"):
        results.append(
            _file_input(
                f"TIGER {layer}",
                configured=getattr(sources, f"tiger_{layer}_zip"),
                dest=tiger_cache_path(
                    cache_dir, layer=layer, tiger_year=years.tiger_year, state_fips=geo.state_fips
                ),
                urls=tiger_urls(layer, years.tiger_year, geo.state_fips),
                run=lambda layer=layer: resolve_tiger_zip(cfg, layer, force=force),
                dry_run=dry_run,
                force=force,
            )
        )
    if cfg.pipelines.service_allocation.build_urban_rural_split:
        results.append(
            _file_input(
                "TIGER urban areas",
                configured=None,
                dest=urban_areas_cache_path(cache_dir, years.tiger_year),
                urls=[urban_areas_url(years.tiger_year)],
                run=lambda: ensure_urban_areas_zip(cache_dir, years.tiger_year, force=force),
                dry_run=dry_run,
                force=force,
            )
        )

    relationship = cache_dir / "2020_Census_Tract_to_2020_PUMA.txt"

    def fetch_relationship() -> Path:
        if force:
            relationship.unlink(missing_ok=True)
        load_tract_to_puma_relationship(cache_dir, geo.state_fips)
        return relationship

    results.append(
        _file_input(
            "Tract to PUMA relationship",
            configured=None,
            dest=relationship,
            urls=list(CENSUS_TRACT_TO_PUMA_URLS),
            run=fetch_relationship,
            dry_run=dry_run,
            force=force,
            is_cached=Path.is_file,
        )
    )

    skip_reason = None
    if not include_api:
        skip_reason = "Census API calls skipped"
    elif not os.environ.get(cfg.census_api.key_env):
        skip_reason = f"set {cfg.census_api.key_env} to a free Census API key"
    client = CensusClient.from_env(
        cache_dir, key_env=cfg.census_api.key_env, rate_limit_sec=cfg.census_api.rate_limit_sec
    )
    census = cache_dir / "census"
    suffix = f"{geo.state_fips}_{years.acs_year}_{years.acs_span}yr.json"
    results.append(
        _api_input(
            "ACS county list",
            dest=census / f"counties_{suffix}",
            run=lambda: fetch_with_year_fallback(
                lambda y: client.fetch_counties(geo.state_fips, y, years.acs_span),
                start_year=years.acs_year,
                fallback_years=3,
            ),
            dry_run=dry_run,
            skip_reason=skip_reason,
        )
    )
    results.append(
        _api_input(
            "ACS tract households",
            dest=cache_dir / f"acs_tract_households_{geo.state_fips}.csv",
            run=lambda: resolve_tract_households(cfg, cache_dir, client=client),
            dry_run=dry_run,
            skip_reason=skip_reason,
        )
    )
    results.append(
        _api_input(
            "ACS B19001 tract incomes",
            dest=census / f"b19001_tracts_{suffix}",
            run=lambda: client.fetch_b19001_tracts(geo.state_fips, years.acs_year, years.acs_span),
            dry_run=dry_run,
            skip_reason=skip_reason,
        )
    )
    return results
