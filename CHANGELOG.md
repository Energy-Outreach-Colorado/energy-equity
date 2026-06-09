# Changelog

All notable changes to this project will be documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `uv.lock` committed for reproducible builds (run `uv sync` to install).
- `.python-version` pins development Python to 3.12.
- Structured logging via **loguru** (new runtime dependency). The package is silent
  when imported as a library (`logger.disable("energy_equity")` in `__init__`); call
  `energy_equity.configure_logging(level)` to opt in. Diagnostics now go to stderr.
- CLI verbosity flags: `--verbose/-v` (DEBUG) and `--quiet/-q` (WARNING), default INFO.
- ruff `T20` (flake8-print) lint rule to prevent `print()` from creeping back in.
- AMI bridge is now auto-built from config on the CLI path. `prepare_household_microdata`
  builds the county→PUMA 80% AMI table via `pums.ami_bridge.build_ami80_bridge(cfg)` when
  no `ami80_by_puma` is supplied and `ami_method="blended_threshold"`, caching it to
  `{cache_dir}/ami80_by_puma_{state}_fy{fy}.csv`. `ee run puma-table` / `run all` now
  produce correct `<=80% AMI` numbers end to end.
- Shared `census.api.resolve_tract_households(cfg, cache_dir)` (used by both the AMI
  bridge and the service-allocation pipeline) and `thresholds.ami.build_county_name_to_fips3`.

### Fixed
- `tables.metrics` no longer crashes (`AttributeError: 'int' object has no attribute
  'astype'`) when the `ami_weight`/`smi_weight` columns are absent; the `hh_ami_valid`
  and `hh_smi_valid` metrics use a new `_valid_col` helper that returns an all-zeros mask
  for a missing column. This was the root cause of the `ee run puma-table` crash.

### Changed
- CI now uses `astral-sh/setup-uv@v3` and `uv sync --all-extras` (≈10x faster
  than the previous pip-based job).
- README quickstart updated to lead with `uv` commands (pip path kept as a
  fallback).
- **Breaking (CLI):** `--version` is now `-V` (was `-v`); `-v` is `--verbose`.
- All package diagnostics moved from `print()` (stdout) to loguru (stderr).

## [0.1.0] - 2026-05-22

Initial public release. Migrated three Colorado/Pueblo Jupyter notebooks into a
state-agnostic Python package with five pipelines, all verified against the original
notebook outputs to floating-point precision (exact match on count metrics; <0.01%
relative diff on MOEs from improved z-90 precision).

### Removed
- `notebooks/` source notebooks (replaced by the package; histories preserved upstream).

### Added
- Project scaffold: pyproject, src layout, tests, pre-commit, MIT license.
- Packaged LIHEAP SMI table (`src/energy_equity/data/smi/`) seeded with Colorado FY 2025
  values transcribed from LIHEAP-IM-2024-04.
- Foundation modules:
  - `config.py` (pydantic v2 + YAML loader)
  - `paths.py` (platformdirs cache resolution)
  - `io/{pums,geo,download}.py`
  - `census/{api,cache}.py` (state-agnostic Census ACS client)
  - `weights/{sdr,stats}.py` (SDR variance, 90% MOE, weighted descriptives)
  - `burden.py` (generic energy + rent burden math)
- PUMS preparation:
  - `pums/load.py`, `pums/prepare.py` with `HouseholdMicrodata` dataclass
  - `pums/energy_cost.py` for PUMS-specific income adjustment + fixed-charge scenarios
  - `thresholds/{ami,smi}.py` for AMI county->PUMA bridge and LIHEAP SMI lookup
- Pipelines (all five from the notebooks, plus the renamed `eligibility_analysis`):
  - `pipelines/puma_table.py`     (state + PUMA tables)
  - `pipelines/service_allocation.py` (tract-household-weighted service-area shares)
  - `pipelines/eligibility_analysis.py` (PIPP-style threshold comparison + demographics)
  - `pipelines/fixed_charge.py`   (rate-increase scenario with sweep + sensitivity)
  - `pipelines/reporting.py`      (B19001 income comparison + regressivity)
- Typer-based CLI (`ee` and `energy-equity`).
- Example config under `examples/pueblo_county_2024/`.
- GitHub Actions CI matrix (Python 3.10/3.11/3.12) with ruff + pytest.
- 103 tests (unit + integration). Parity tests against the source notebooks pass
  exactly (count metrics: 0 abs diff; MOEs: <0.01% relative diff from notebook's
  rounded z-90 multiplier).

### Notes
- The three source notebooks under `notebooks/` are kept untracked so the user can
  verify outputs against the package before they are deleted in v0.2.
- "PIPP" renamed to `eligibility_analysis` since PIPP is a Colorado-specific program
  acronym; the program label is configurable.
