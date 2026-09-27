# Changelog

All notable changes to this project will be documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `geo.allocation.puma_shares_from_units` builds household-weighted PUMA shares from overlap
  fractions at any resolution that nests in PUMAs (tract, block group, or block), so callers
  such as a web map can apportion PUMA estimates to an arbitrary polygon.
- `allocate_puma_counts_to_service` accepts `rate_definitions`, which defaults to the
  previous hard-coded list (now exported as `DEFAULT_RATE_DEFINITIONS`).
- `tables.metrics.ENERGY_BURDEN_BAND_COUNT_METRICS` gives weighted household counts in the
  burden bands 0–2%, 2–4%, 4–6%, 6–10%, 10–20% and 20% or more. The bands partition
  `hh_burden_valid` and can be apportioned like any other count.
- `tables.metrics.ENERGY_COST_TOTAL_METRICS` gives weighted totals of annual energy cost and
  income over valid-burden households, from which average cost and aggregate burden follow for
  any apportioned area.

### Fixed
- `ee run all` reported blank MOEs for every service-area total and rate. The in-memory
  replicate hand-off from `puma_table` uses bare metric names (`hh_total`) while the allocator
  looked up `_w` column names (`hh_total_w`). The allocator now accepts both spellings.
- Building a wheel failed with "A second file is being added to the wheel archive", so the
  package could not be installed from git or an sdist. The `force-include` table added
  `src/energy_equity/data` a second time on top of `packages`, which already ships it. The
  table is removed and the wheel still carries every packaged data file.

## [0.2.0] - 2026-08-04

### Added
- `uv.lock` committed for reproducible builds (run `uv sync` to install).
- `.python-version` pins development Python to 3.12.
- **All-state LIHEAP SMI data**: the packaged `data/smi` table covers all 50 states + DC +
  Puerto Rico, household sizes 1–12, for FY2025, FY2026, and FY2027 — one LIHEAP IM per
  mandatory fiscal year (FY2025←IM2024-02, FY2026←IM2025-02, FY2027←FY2027 Attachment 4),
  cited per state-year in `provenance.csv`. A data-integrity test re-derives every value
  from the CFR 96.85 formula.
- `thresholds.smi_source: "auto"` (new default) selects the packaged SMI fiscal year closest
  to (and not after) `vintages.hud_ami_fy`; an explicit `liheap_fy####` that diverges from the
  analysis vintage now warns instead of silently mismatching.
- `thresholds.compute_smi` (default true) to run without SMI for an uncovered state-year — the
  `<=60% SMI` metrics report zero instead of erroring.
- `data_sources.smi_csv` optional override to supply your own SMI table without a code change.
- Structured logging via **loguru** (new runtime dependency). Silent when imported as a
  library (`logger.disable("energy_equity")` in `__init__`); call
  `energy_equity.configure_logging(level)` to opt in. Diagnostics go to stderr.
- CLI verbosity flags `--verbose/-v` (DEBUG) and `--quiet/-q` (WARNING); the `ee` CLI
  auto-loads a `.env` (via `python-dotenv`) so `CENSUS_API_KEY` is picked up without exporting.
- ruff `T20` (flake8-print) lint rule to keep `print()` out of the package.
- AMI bridge auto-built from config on the CLI path (`pums.ami_bridge.build_ami80_bridge`),
  cached to `{cache_dir}/ami80_by_puma_{state}_fy{fy}.csv`, so `ee run puma-table` / `run all`
  produce correct `<=80% AMI` numbers end to end. Shared helpers
  `census.api.resolve_tract_households` and `thresholds.ami.build_county_name_to_fips3`.
- PNG figures in the reporting pipeline (`reporting/figures.py`): income comparison,
  regressivity curve, scenario sweep, energy-burden waterfall, demographic bars, burden
  bands, and per-PUMA choropleths, written to `<output_dir>/figures/`. Controlled by
  `pipelines.reporting.figures`; requires the `viz` extra (skips with a warning otherwise).
- Documentation pages: `docs/cli.md` (CLI reference) and `docs/outputs.md` (tables + figures).
- **Utility bill normalization** (`calibration.electric` / `calibration.gas`): LEAD-style
  calibration of PUMS self-reported energy costs against administrative averages, per
  electric utility (EIA-861) and per gas company (EIA-176). Diagnostic-first
  (`calibration_electric.csv` / `calibration_gas.csv` report observed vs target average
  bills); opt-in `apply: true` rescales paying households' costs before burden is
  thresholded. Packaged residential data for every US electric utility and gas company,
  2022 through 2024, ships with provenance files, rebuild scripts, and data-integrity
  tests (`data/eia861`, `data/eia176`). See `docs/bill_normalization.md`.
- **Affordability-gap sizing** in the eligibility pipeline: `affordability_gap.csv`
  (segment by population by threshold, with replicate-weight 90% MOEs) and
  `affordability_gap_by_puma.csv` size the annual dollars needed to bring every
  household's energy burden down to a threshold. Thresholds via
  `pipelines.eligibility_analysis.gap_thresholds`.
- **Multi-year trend analysis** (`ee run trends`): compares completed per-year runs into
  `trends_affordability_gap.csv`, `trends_energy_burden.csv`, and `trends_deltas.csv`
  (year-over-year changes with root-sum-of-squares MOEs, valid for independent 1-year
  PUMS samples), plus trend-line figures with shaded 90% MOE bands. Trend tables record
  each run's bill-normalization basis per fuel and the pipeline warns when years mix
  calibrated and uncalibrated runs.
- `service_allocation.build_service_puma_shares`: the geography-only PUMA share builder,
  factored out so the CLI computes shares once and reuses them for calibration and
  allocation.

### Changed
- SMI uses HHS's official FFY thresholds with no inflation adjustment (documented), one IM
  per mandatory fiscal year; max packaged household size raised 10 → 12, and per-size values
  use HHS's floor (CFR 96.85) rather than round.
- CI now uses `astral-sh/setup-uv@v3` and `uv sync --all-extras` (≈10x faster than the
  previous pip-based job); README quickstart leads with `uv` commands (pip path kept).
- **Breaking (CLI):** `--version` is now `-V` (was `-v`); `-v` is `--verbose`. All package
  diagnostics moved from `print()` (stdout) to loguru (stderr).

### Fixed
- `tables.metrics` no longer crashes (`AttributeError: 'int' object has no attribute
  'astype'`) when the `ami_weight`/`smi_weight` columns are absent — the `hh_ami_valid` /
  `hh_smi_valid` metrics use a new `_valid_col` helper. Root cause of the `ee run puma-table`
  crash.
- `reporting.run` no longer raises `KeyError: 'tract_geoid'` — the B19001 frame's `geoid`
  column is normalized to `tract_geoid` before the merge.
- Removed a stray `ipdb.set_trace()` breakpoint in `census.api.CensusClient._get` (and the
  accidental `ipdb` dependency) that hard-stopped every live Census API call.

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
