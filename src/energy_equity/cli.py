"""Typer-based CLI for energy-equity.

Entry point: ``ee`` (also installed as ``energy-equity``). Subcommands map 1:1 to the
pipeline modules so users can run any phase independently or chain them via ``ee run all``.

Examples:

    ee config init --state CO --output config.yaml
    ee config validate config.yaml
    ee run puma-table           --config config.yaml
    ee run service-allocation   --config config.yaml
    ee run eligibility          --config config.yaml
    ee run fixed-charge         --config config.yaml
    ee run reporting            --config config.yaml
    ee run all                  --config config.yaml
    ee data fetch               --config config.yaml
    ee cache info
    ee cache clear --what census
"""

from __future__ import annotations

import shutil
from pathlib import Path

import typer
from dotenv import find_dotenv, load_dotenv

from . import __version__
from ._logging import configure_logging
from .config import Config
from .paths import default_cache_dir, resolve_cache_dir

app = typer.Typer(
    name="ee",
    help="Energy-equity analysis pipelines for ACS PUMS microdata.",
    add_completion=False,
    no_args_is_help=True,
)
run_app = typer.Typer(no_args_is_help=True, help="Run an analysis pipeline.")
cache_app = typer.Typer(no_args_is_help=True, help="Inspect or clear the on-disk cache.")
config_app = typer.Typer(no_args_is_help=True, help="Validate or scaffold a YAML config.")
data_app = typer.Typer(no_args_is_help=True, help="Download the Census inputs a run needs.")
app.add_typer(run_app, name="run")
app.add_typer(data_app, name="data")
app.add_typer(cache_app, name="cache")
app.add_typer(config_app, name="config")


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-V", help="Print version and exit."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose (DEBUG) logging."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Quiet (WARNING and above only)."),
) -> None:
    # Runs before every subcommand. Load a .env (searching up from the working directory)
    # so CENSUS_API_KEY etc. are available; real shell vars take precedence (override=False).
    load_dotenv(find_dotenv(usecwd=True))
    level = "DEBUG" if verbose else "WARNING" if quiet else "INFO"
    configure_logging(level)
    if version:
        typer.echo(f"energy-equity {__version__}")
        raise typer.Exit()


# ----- `run` subcommands -----------------------------------------------------


def _load_cfg(path: Path) -> Config:
    if not path.exists():
        typer.echo(f"Config file not found: {path}", err=True)
        raise typer.Exit(code=2)
    return Config.from_yaml(path)


def _prepare_microdata(cfg: Config):
    """Prepare household microdata, running bill calibration when configured.

    A `calibration.territories` block writes `calibration_territories.csv` (one row per
    fuel and utility) and `calibration_puma_factors.csv` (the factor applied per PUMA)
    to the output directory.

    Calibration needs the service-area PUMA shares before burden is computed, so the
    geography-only share builder runs first; the shares are returned for reuse by
    `service_allocation.run` to avoid recomputing them. Returns (microdata, shares).
    """
    import pandas as pd
    from loguru import logger

    from .paths import ensure_dir
    from .pipelines import service_allocation
    from .pums.prepare import prepare_household_microdata

    shares = None
    if cfg.calibration.electric is not None or cfg.calibration.gas is not None:
        shares = service_allocation.build_service_puma_shares(cfg)
    md = prepare_household_microdata(cfg, service_shares=shares)
    for fuel, diagnostic in (
        ("electric", md.electric_calibration),
        ("gas", md.gas_calibration),
    ):
        if diagnostic is not None:
            out_path = ensure_dir(cfg.project.output_dir) / f"calibration_{fuel}.csv"
            pd.DataFrame([diagnostic]).to_csv(out_path, index=False)
            logger.info("wrote {}", out_path.resolve())
    territory = md.territory_calibration
    if territory is not None:
        out_dir = ensure_dir(cfg.project.output_dir)
        tables = [
            t for t in (territory.electric, territory.gas, territory.combined) if t is not None
        ]
        for name, frame in (
            ("calibration_territories.csv", pd.concat(tables, ignore_index=True)),
            ("calibration_puma_factors.csv", territory.puma_factors),
        ):
            frame.to_csv(out_dir / name, index=False)
            logger.info("wrote {}", (out_dir / name).resolve())
    return md, shares


@run_app.command("puma-table")
def run_puma_table(
    config: Path = typer.Option(..., "--config", "-c", help="Path to YAML config."),
) -> None:
    from .pipelines import puma_table

    cfg = _load_cfg(config)
    md, _ = _prepare_microdata(cfg)
    puma_table.run(cfg, microdata=md)


@run_app.command("service-allocation")
def run_service_allocation(config: Path = typer.Option(..., "--config", "-c")) -> None:
    from .pipelines import service_allocation

    cfg = _load_cfg(config)
    service_allocation.run(cfg)


@run_app.command("eligibility")
def run_eligibility(config: Path = typer.Option(..., "--config", "-c")) -> None:
    """Eligibility (formerly PIPP) threshold comparison + demographic breakdowns."""
    from .pipelines import eligibility_analysis, service_allocation

    cfg = _load_cfg(config)
    md, shares = _prepare_microdata(cfg)
    sa = service_allocation.run(cfg, puma_overall=None, replicates=None, puma_shares=shares)
    eligibility_analysis.run(cfg, microdata=md, service_shares=sa["puma_shares"])


@run_app.command("fixed-charge")
def run_fixed_charge(config: Path = typer.Option(..., "--config", "-c")) -> None:
    from .pipelines import fixed_charge, service_allocation

    cfg = _load_cfg(config)
    md, shares = _prepare_microdata(cfg)
    sa = service_allocation.run(cfg, puma_overall=None, replicates=None, puma_shares=shares)
    fixed_charge.run(cfg, microdata=md, service_shares=sa["puma_shares"])


@run_app.command("reporting")
def run_reporting(config: Path = typer.Option(..., "--config", "-c")) -> None:
    from .pipelines import reporting

    cfg = _load_cfg(config)
    reporting.run(cfg)


@run_app.command("trends")
def run_trends(config: Path = typer.Option(..., "--config", "-c")) -> None:
    """Compare completed per-year runs (pipelines.trends.runs) into trend tables and figures."""
    from .pipelines import trends

    cfg = _load_cfg(config)
    trends.run(cfg)


@run_app.command("all")
def run_all(config: Path = typer.Option(..., "--config", "-c")) -> None:
    """Run every pipeline in dependency order: puma-table -> service-allocation -> eligibility -> fixed-charge -> reporting."""
    from .pipelines import (
        eligibility_analysis,
        fixed_charge,
        puma_table,
        reporting,
        service_allocation,
    )

    cfg = _load_cfg(config)
    md, shares = _prepare_microdata(cfg)
    pt = puma_table.run(cfg, microdata=md)
    sa = service_allocation.run(
        cfg,
        puma_overall=pt["puma_overall"],
        replicates=pt.get("puma_overall_replicates"),  # type: ignore[arg-type]
        puma_shares=shares,
    )
    eligibility_analysis.run(cfg, microdata=md, service_shares=sa["puma_shares"])
    fixed_charge.run(cfg, microdata=md, service_shares=sa["puma_shares"])
    reporting.run(cfg)


# ----- `cache` subcommands ---------------------------------------------------


def _cache_dir_for(config: Path | None) -> Path:
    """The cache a config uses, or the environment and platform default without one."""
    return resolve_cache_dir(_load_cfg(config).project.cache_dir if config else None)


@cache_app.command("info")
def cache_info(
    config: Path = typer.Option(
        None, "--config", "-c", help="Report on the cache this config uses (project.cache_dir)."
    ),
) -> None:
    path = _cache_dir_for(config)
    total_bytes = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    typer.echo(f"Cache directory: {path}")
    typer.echo(f"Total size:      {total_bytes / (1 << 20):.2f} MB")
    typer.echo(f"Default would be {default_cache_dir()}")


@cache_app.command("clear")
def cache_clear(
    what: str = typer.Option("all", help="Which subset to clear: census|pums|tiger|all."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    config: Path = typer.Option(
        None, "--config", "-c", help="Clear the cache this config uses (project.cache_dir)."
    ),
) -> None:
    path = _cache_dir_for(config)
    target = path if what == "all" else path / what
    if not target.exists():
        typer.echo(f"Nothing to clear at {target}.")
        return
    if not yes:
        typer.confirm(f"Delete {target}?", abort=True)
    shutil.rmtree(target)
    typer.echo(f"Cleared {target}.")


@data_app.command(
    "fetch",
    help=(
        "Download every Census input the config needs into the cache and report on each. "
        "PUMS and TIGER files come from www2.census.gov unless the config points at local "
        "files, and the ACS tables come from the Census API, which needs a free key. Exits "
        "with status 1 when any input failed."
    ),
)
def data_fetch(
    config: Path = typer.Option(..., "--config", "-c", help="Path to YAML config."),
    force: bool = typer.Option(False, "--force", help="Download files again even if cached."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="List what would be downloaded without downloading."
    ),
    skip_api: bool = typer.Option(False, "--skip-api", help="Skip the Census API calls."),
) -> None:
    """Run `bootstrap.fetch_inputs` and print one line per input."""
    from .bootstrap import fetch_inputs

    cfg = _load_cfg(config)
    results = fetch_inputs(cfg, force=force, dry_run=dry_run, include_api=not skip_api)
    width = max(len(r.name) for r in results)
    for r in results:
        size = f"{r.size_bytes / (1 << 20):8.1f} MB" if r.size_bytes is not None else " " * 11
        where = str(r.path) if r.path is not None else ""
        typer.echo(f"{r.status:<10}  {r.name:<{width}}  {size}  {where}")
        if r.detail:
            typer.echo(f"{'':<10}  {'':<{width}}  {'':<11}  {r.detail}")
    typer.echo(f"Cache directory: {resolve_cache_dir(cfg.project.cache_dir)}")
    if any(r.status == "failed" for r in results):
        raise typer.Exit(code=1)


# ----- `config` subcommands --------------------------------------------------


@config_app.command("validate")
def config_validate(config: Path = typer.Argument(..., help="Path to YAML config.")) -> None:
    cfg = _load_cfg(config)
    typer.echo(f"OK: {config} parses; project={cfg.project.name}, state={cfg.geography.state_abbr}")


@config_app.command("init")
def config_init(
    state: str = typer.Option(..., "--state", help="Two-letter postal code (e.g. CO, IL)."),
    state_fips: str = typer.Option(..., "--state-fips", help='Two-digit state FIPS, e.g. "08".'),
    output: Path = typer.Option(
        Path("config.yaml"), "--output", "-o", help="Where to write the config."
    ),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite an existing file."),
) -> None:
    """Scaffold a starter config.yaml.

    The PUMS and TIGER paths are null, so a run downloads them into the cache. Only the
    service-area shapefile and the HUD AMI CSV are placeholders to fill in.
    """
    if output.exists() and not overwrite:
        typer.echo(f"{output} exists; pass --overwrite to replace.", err=True)
        raise typer.Exit(code=2)
    template = _STARTER_TEMPLATE.format(state_fips=state_fips, state_abbr=state.upper())
    output.write_text(template, encoding="utf-8")
    typer.echo(
        f"Wrote {output}. Set the service-area shapefile and HUD AMI CSV, then run "
        f"`ee config validate {output}` and `ee data fetch --config {output}`."
    )


_STARTER_TEMPLATE = """\
project:
  name: my_run
  output_dir: ./out
  cache_dir: null
geography:
  state_fips: "{state_fips}"
  state_abbr: {state_abbr}
  service_area:
    shapefile: ./data/service.shp
vintages:
  acs_year: 2024
  pums_year: 2024
  hud_ami_fy: 2025
  tiger_year: 2024
data_sources:
  hud_ami_csv: ./data/hud_ami.csv
  pums_housing_zip: null
  pums_person_zip: null
  tiger_puma_zip: null
  tiger_tract_zip: null
weights:
  compute_moe: true
  replicate_count: 80
pipelines:
  service_allocation:
    build_urban_rural_split: true
  eligibility_analysis:
    program_name: PIPP
    current_threshold: 0.06
    proposed_threshold: 0.025
  fixed_charge:
    monthly_increase: 4.00
    apply_only_if_gas_positive: true
    scenario_sweep: [0, 1, 2, 3, 4, 5, 7.5, 10, 15]
"""
