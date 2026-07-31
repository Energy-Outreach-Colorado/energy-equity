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
app.add_typer(run_app, name="run")
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
    """Prepare household microdata, running EIA-861 electric calibration when configured.

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
    if cfg.calibration.electric is not None:
        shares = service_allocation.build_service_puma_shares(cfg)
    md = prepare_household_microdata(cfg, service_shares=shares)
    if md.electric_calibration is not None:
        out_path = ensure_dir(cfg.project.output_dir) / "calibration_electric.csv"
        pd.DataFrame([md.electric_calibration]).to_csv(out_path, index=False)
        logger.info("wrote {}", out_path.resolve())
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


@cache_app.command("info")
def cache_info() -> None:
    path = resolve_cache_dir(None)
    total_bytes = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    typer.echo(f"Cache directory: {path}")
    typer.echo(f"Total size:      {total_bytes / (1 << 20):.2f} MB")
    typer.echo(f"Default would be {default_cache_dir()}")


@cache_app.command("clear")
def cache_clear(
    what: str = typer.Option("all", help="Which subset to clear: census|pums|tiger|all."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    path = resolve_cache_dir(None)
    target = path if what == "all" else path / what
    if not target.exists():
        typer.echo(f"Nothing to clear at {target}.")
        return
    if not yes:
        typer.confirm(f"Delete {target}?", abort=True)
    shutil.rmtree(target)
    typer.echo(f"Cleared {target}.")


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
    """Scaffold a starter config.yaml. Paths inside are placeholders for the user to fill in."""
    if output.exists() and not overwrite:
        typer.echo(f"{output} exists; pass --overwrite to replace.", err=True)
        raise typer.Exit(code=2)
    template = _STARTER_TEMPLATE.format(state_fips=state_fips, state_abbr=state.upper())
    output.write_text(template, encoding="utf-8")
    typer.echo(f"Wrote {output}. Edit the paths and re-run `ee config validate {output}`.")


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
  pums_housing_zip: ./data/csv_hco.zip
  pums_person_zip: ./data/csv_pco.zip
  tiger_puma_zip: ./data/tl_puma.zip
  tiger_tract_zip: ./data/tl_tract.zip
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
