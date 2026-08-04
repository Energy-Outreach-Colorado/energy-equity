# Developer guide

Architecture, conventions, and methodology notes for working on this codebase.

## What this project is

`energy-equity` is a state-agnostic Python package that quantifies household energy burden,
utility affordability, and rate-impact scenarios from ACS PUMS microdata for any US service
territory. It was built by migrating three Colorado/Pueblo Jupyter notebooks into five
reusable pipelines; the parity tests under `tests/integration/test_*_parity.py` confirm the
ported pipelines reproduce the notebook outputs to floating-point precision.

## Build, lint, test

The project is uv-first. `uv.lock` is committed for reproducible builds; `.python-version`
pins development to Python 3.12.

```sh
uv sync --all-extras                    # install core + viz + dev into .venv/
uv run pytest tests -q                  # full test suite (network-bound tests skipped by default)
uv run pytest tests/unit -q             # unit tests only (no fixtures)
uv run pytest tests/integration -q      # integration + parity (parity skips without EE_PARITY_DATA)
uv run pytest tests/unit/test_burden.py::test_compute_energy_burden_basic -v   # single test
uv run ruff check src tests             # lint
uv run ruff format --check src tests    # formatting check
uv run ruff format src tests            # apply formatter
uv run ee --help                        # CLI
```

pip also works (see README), but uv is faster and the lockfile is the source of truth for
the dev environment.

### Parity tests against the source notebook outputs

The `tests/integration/test_*_parity.py` files diff package outputs against the original
notebook's CSVs. They are gated by the `EE_PARITY_DATA` environment variable so they skip
in CI and on machines that don't have the source data:

```sh
EE_PARITY_DATA=/data/eoc/output/pueblo/service_area_estimates_tract_weighted \
    uv run pytest tests/integration -q
```

When the env var is set, these tests also pull the cached `acs_tract_households_co.csv`
and `ami80_by_puma.csv` from that directory so they don't require a Census API key.

### `conftest.py` blocks live network by default

`tests/conftest.py` sets `CENSUS_API_KEY=TEST-KEY-NOT-REAL` for any test not marked
`@pytest.mark.network`. To run a live-network test intentionally:

```sh
uv run pytest -m network --tb=short
```

Tests that need ACS data but should *not* hit the network (parity tests, fixture-based
integration tests) accept pre-loaded DataFrames via keyword args on the pipeline `run`
functions — wire fixtures through those rather than touching the network.

## Architecture

### Pipeline DAG

The five pipelines form a strict dependency chain:

```
prepare_household_microdata --> puma_table --> service_allocation --> eligibility_analysis
                                                                  \--> fixed_charge
reporting (consumes B19001 + service-area shapes; reads no pipeline output directly)
trends (consumes completed per-year run OUTPUT DIRS via pipelines.trends.runs; not part
        of `ee run all` -- each year's run must finish first)
```

Every pipeline lives in `src/energy_equity/pipelines/<name>.py` and exposes a single
`run(cfg: Config, ...)` entry point. The CLI's `ee run all` chains the five core
pipelines in this order.

### Central data object: `HouseholdMicrodata`

`pums.prepare.HouseholdMicrodata` is a small dataclass wrapping the household-level
DataFrame plus the metadata downstream functions need (point weight column name, the 80
replicate weight columns, vintage). Passing the dataclass instead of bare DataFrames
eliminates positional-arg bugs across the ~15 builders that consume it. **When extending
a pipeline, accept and return `HouseholdMicrodata`, not raw DataFrames.**

### Library vs pipeline separation

Strict rule, enforced by reading:

- **Library**: `DataFrame in → DataFrame out`. Lives outside `pipelines/`. The ~30 builder
  functions in `tables/{core,metrics,summaries,scenarios}.py` and `reporting/{income,metrics}.py`
  are all library. They can be called from a notebook without importing any pipeline code.
- **Pipeline**: reads YAML, resolves paths, decides what to write, chains builders.
  Lives in `pipelines/<name>.py`.

A new analytical metric belongs in `tables/` or `reporting/`. A new orchestration concern
(new output filename, new dependency on another pipeline's output) belongs in `pipelines/`.

### How service-area weighting works

The `service_allocation` pipeline produces `share_households_in_service` and
`urban_share_within_service` per PUMA. Downstream pipelines (`eligibility_analysis`,
`fixed_charge`) call `tables.summaries.attach_service_weights_and_eligibility_flags` to
multiply each household's PUMS weight by the PUMA's service share, creating three new
per-household weight columns: `w_service`, `w_service_urban`, `w_service_rural`. Every
service-area aggregate that follows just does weighted sums over these columns.

Two consequences:
- When urban-area data isn't supplied, `urban_share_within_service` is `NaN` (not 0), so
  `w_service_urban` and `w_service_rural` are also `NaN` and the corresponding segment
  rows are *omitted* from outputs. This is correct behavior — don't "fix" it to default
  rural.
- The `summaries.SEGMENT_WEIGHTS` constant maps segment label → weight column name. Both
  `tables/summaries.py` and `tables/scenarios.py` iterate over this map; keep them in sync.

### Replicate weights and MOEs

ACS PUMS ships 80 replicate weights (`WGTP1..WGTP80`). Variance is computed via the
Successive Difference Replicate formula `Var = (4/80) * Σ(rep − point)²` and the 90% MOE
as `1.645σ`. Implementation choices worth knowing:

- `weights.sdr.Z_90 = 1.6448536...` (full precision). The notebooks used the rounded
  `1.645`; that's the only source of the ~0.009% MOE diff in the parity tests. Don't
  revert this — the package is more correct.
- `tables.core.build_table` returns replicate matrices (`return_replicates=True`) so
  service-area MOEs can propagate them correctly. `puma_table.run` writes
  `puma_overall_replicates.csv.gz` for downstream pipelines, but in-memory hand-off is
  preferred (the `replicates` kwarg on `service_allocation.run`).
- `geo.allocation.allocate_puma_counts_to_service` recomputes the SDR formula in the
  service-area aggregate from the replicate-aggregated counts (replicate propagation, not
  RSS). Don't switch to RSS for service totals — PUMAs share a replicate frame.

### CRS discipline (geospatial)

The package follows a strict CRS convention to avoid silent errors in spatial joins:

- **EPSG:4326** (WGS84 lon/lat) for spatial joins (`assign_puma_to_tracts`,
  point-in-polygon).
- **EPSG:5070** (NAD83 / Conus Albers) for area arithmetic
  (`compute_household_weighted_puma_shares`, service-area intersection, urban overlap).

Constants live in `io.geo` (`CRS_GEOGRAPHIC`, `CRS_EQUAL_AREA`). Functions accept any
input CRS and call `.to_crs(...)` explicitly. Test fixtures (`tests/fixtures/synthetic_geo.py`)
build geometries directly in EPSG:5070 so test assertions aren't affected by reprojection
distortion — keep new synthetic fixtures in the same projection.

### State-agnostic design

The original notebooks were Colorado-hardcoded. The package replaced this with:

- **County FIPS lookup**: `census.api.CensusClient.fetch_counties(state_fips, year)` calls
  the ACS API. No `CO_COUNTY_FIPS3` dict.
- **LIHEAP SMI thresholds**: shipped as `src/energy_equity/data/smi/liheap_smi_by_state_year.csv`.
  Coverage: all 50 states + DC + PR, household sizes 1–12, for FY2025/FY2026/FY2027 — one
  LIHEAP IM per mandatory fiscal year (FY2025←IM2024-02, FY2026←IM2025-02, FY2027←FY2027
  Attachment 4), cited per state-year in `provenance.csv`. All IMs are published at the LIHEAP
  Information Memoranda index: https://acf.gov/ocs/policy-guidance/liheap-information-memoranda
  (see `data/smi/README.md`). The published
  60% SMI values follow CFR 96.85: `base60 = floor(0.60 * SMI_4person)`, then
  `value(size) = floor(base60 * pct(size))` (52/68/84/100/116/132% for sizes 1–6; +3 pts per
  size to 150% at 12). `tests/unit/test_smi_data_integrity.py` re-derives every row from that
  formula, so a hand-edit that breaks it fails CI. `thresholds.smi_source` is `"auto"` (pick
  the packaged FY ≤ `vintages.hud_ami_fy`) or an explicit `"liheap_fy####"`;
  `thresholds.compute_smi=false` skips SMI entirely; `data_sources.smi_csv` overrides the
  packaged file. **No inflation adjustment** is applied — these are HHS's official FFY
  program thresholds (already projected from the 2019–2023 ACS); household income (ADJINC-
  adjusted) is compared to them as-is. The residual survey-year vs FFY dollar-vintage gap is
  a documented minor limitation, not a correction.
- **Configuration**: pydantic v2 models in `config.py` validate a single YAML per run.
  `extra="forbid"` so typos fail loudly. The `state_abbr` validator uppercases input.

### Utility bill normalization (calibration)

`calibration.py` (top-level library module, sibling of `burden.py`) implements
LEAD-style normalization of PUMS energy costs against administrative averages:
electric (`calibration.electric`, EIA-861 per utility) and natural gas
(`calibration.gas`, EIA-176 per company). The generic core is
`compute_bill_calibration` / `apply_bill_calibration`; the fuel-named functions are
thin wrappers. See `docs/bill_normalization.md`. Rules that matter when touching it:

- **Off by default.** The `calibration:` config block absent → no behavior change at all.
  Parity tests depend on this; never make calibration (or its diagnostic) run implicitly.
- Two stages per fuel: with a fuel's block configured, the diagnostic (observed vs
  target average bill → `calibration_electric.csv` / `calibration_gas.csv` + log line)
  always runs; costs are rescaled only when `apply: true`.
- The rescale happens inside `prepare_household_microdata` *between*
  `apply_energy_cost_adjustment` and `compute_energy_burden_pums` — it must precede
  burden thresholding (a level shift moves households across the 6%/10% cutoffs
  nonlinearly). Only households with the fuel's raw cost present (`ELEP` / `GASP`) are
  averaged or rescaled; NaN-cost households are excluded on both sides, matching the
  administrative customer denominators. `apply_bill_calibration` re-sums totals from
  current components, so applying both fuels is order-independent (tested).
- Trend runs must calibrate all years or none per fuel; `pipelines/trends.py` records
  each run's basis (`electric_calibration` / `gas_calibration` columns, detected from
  the calibration CSVs in each run dir) and warns on a mix.
- The observed mean is service-weighted: the CLI calls
  `service_allocation.build_service_puma_shares(cfg)` (the geography-only half of that
  pipeline, no puma_table dependency) before prepare and passes the shares to both
  `prepare_household_microdata(service_shares=...)` and
  `service_allocation.run(puma_shares=...)` so they're computed once. Without shares the
  mean falls back to statewide with a warning (`scope: "state"`).
- Administrative data ships packaged: `data/eia861/eia861_residential.csv` (electric,
  per utility-state-year, 2022–2024 final releases, rebuilt via
  `scripts/build_eia861_residential_csv.py`) and `data/eia176/eia176_residential.csv`
  (gas, per company-state-year, 2022–2024, rebuilt via
  `scripts/build_eia176_residential_csv.py` from the EIA NGQS RP4 JSON API); both with
  provenance files and integrity tests — same pattern as `data/smi/`. Selection:
  `utility_number`/`utility_name` (electric) or `company_id`/`company_name` (gas, no
  shared key with EIA-861), with `geography.state_abbr` applied automatically and the
  year defaulting to `vintages.pums_year`. `data_sources.eia861_csv`/`eia176_csv`
  override the packaged files; `target_annual_bill` bypasses the tables entirely. Config
  validation lives in a `Config` model_validator.

### "PIPP" was renamed to "eligibility_analysis"

PIPP is an Ohio-then-Colorado program acronym, not a general concept. The pipeline is
`pipelines/eligibility_analysis.py` and the config key is
`pipelines.eligibility_analysis.program_name` (defaults to "PIPP" cosmetically). Don't
re-introduce PIPP into module names or output column names — keep program-specific naming
inside config values, not source.

### Output paths and the cache

- `cfg.project.output_dir` is where each pipeline writes its CSVs. Created if missing.
- `cfg.project.cache_dir` (or `EE_CACHE_DIR`, or `platformdirs.user_cache_dir("energy-equity")`
  in that precedence) holds downloaded Census tables, TIGER urban-area shapefiles, and
  per-request JSON caches under `census/`. Never default to a project-relative `./cache/`
  — that pollutes user repos.

### Logging

The package uses **loguru**, never `print()` (ruff `T20` enforces this). Conventions:

- In any module that needs to emit a diagnostic: `from loguru import logger`, then
  `logger.info("wrote {}", path)` / `logger.debug(...)` / `logger.warning(...)`. Use
  loguru's `{}` brace style with lazy args, not f-strings, so formatting is skipped when
  the level is suppressed. Don't add `[OUT]`/`[geo]`-style prefixes — loguru already
  stamps the level and module name.
- Level mapping in use: file-written / cache-written → `info`; path resolution detail →
  `debug`; API year-fallback, unmatched tracts, skipped urban split → `warning`.
- **Silent by default as a library**: `__init__.py` calls `logger.disable("energy_equity")`,
  so importing the package emits nothing. `_logging.configure_logging(level)` calls
  `logger.enable("energy_equity")` + adds a stderr sink; the CLI root callback invokes it.
  A library consumer who wants output calls `energy_equity.configure_logging()`.
- CLI verbosity: the root callback maps `--verbose/-v` → DEBUG, `--quiet/-q` → WARNING,
  default INFO. **`--version` is `-V`** (not `-v`, which is now verbose). Genuine
  user-facing CLI output (version string, config-validation result, cache info) stays as
  `typer.echo()` to stdout — only diagnostics go through loguru to stderr.

### What the `build_table` function does

`tables.core.build_table(microdata, group_cols, *, count_metrics, ratio_metrics, ...)`
is the workhorse. It runs each `CountMetric.mask_fn` once on the household frame, then:

1. For the point estimate: multiplies the mask by `WGTP`, groups by `group_cols`, sums.
2. For ratios: divides point-estimate numerator over denominator metric.
3. For MOEs (when `compute_moe=True` and replicate columns exist): repeats step 1 with
   each of the 80 replicate weights, builds a (n_groups, 80) matrix per metric, then calls
   `weights.sdr.sdr_moe`.
4. Optional small-N suppression based on `min_unweighted_n`.
5. Optional sort + replicate-matrix reorder so `rep_estimates[m][i, :]` lines up with
   row `i` of the returned DataFrame.

`tables/metrics.py` defines `BASELINE_COUNT_METRICS` and `BASELINE_RATIO_METRICS` — the
metric set the `puma_table` pipeline emits. Other pipelines (eligibility, fixed_charge)
sidestep `build_table` and use simpler per-segment loops over `w_service*` columns.

## Methodology notes (read these before changing math)

These are not bugs — they're inherited from the notebooks and preserved for parity. Each
is a known limitation worth fixing in a future minor version. Document, don't silently
"fix":

1. **ELEP/GASP NaN handling**: With `missing_cost_rule="zero"` (the default), `NaN` values
   for electric/gas costs become $0 in the annual cost sum. ACS PUMS emits `NaN` both for
   "no charge / not used" and for "included in rent / condo fee". The latter group does
   pay for energy, just not directly — treating their cost as zero **understates burden
   for renters in master-metered buildings**. A proper fix uses the `FELEP/FGASP/FFULP`
   allocation flags to distinguish the cases.

2. **AMI/SMI clipping for large households**: Households with `NP` greater than the max
   published household size get clipped. SMI now ships sizes 1–12 (was 1–10), so SMI
   clipping only affects 13+-person households; HUD AMI clipping depends on the supplied
   CSV. Large families above the cap use a smaller threshold than they should and are
   under-counted as eligible. The CFR 96.85 "additional person" rule (+3 pts/person) could
   extend SMI further in code if needed.

3. **Within-PUMA homogeneity**: The service-area allocator assumes the in-service portion
   of each PUMA has the same burden / income / demographic distribution as the full PUMA.
   This is the standard small-area-estimation compromise; eliminating it would require
   sub-PUMA microdata. **Always disclose in any methodology appendix that ships with
   published numbers.**

4. **Tract area-weighting**: `compute_household_weighted_puma_shares` weights tracts by
   *area* overlap with the service polygon, then multiplies by tract household counts.
   This assumes uniform household density within each tract. For irregular service shapes
   (utility pipe buffers, narrow corridors) the populated portion may not match the
   area-overlap fraction. Block-group level allocation would be tighter.

5. **Two AMI methods**: `thresholds.ami.attach_ami_blended_threshold` produces a single
   binary `le_80_ami` flag per household using a PUMA-blended AMI limit. `attach_ami_expected_probability`
   produces a fractional `ami_weight` in [0, 1] by summing within-PUMA county-share
   weights where the household qualifies. The blended version misclassifies households
   near the boundary in PUMAs that span counties with different limits; expected_probability
   is more methodologically defensible but harder to reason about in tabular outputs. Both
   are kept; `cfg.thresholds.ami_method` selects.

## Dependencies

Runtime:
- `pandas`, `numpy` for data manipulation
- `pyyaml` for config loading; `pydantic ≥ 2.5` for validation
- `typer` for the CLI
- `loguru` for logging (see the Logging section)
- `requests` + `platformdirs` for HTTP + cache resolution
- `geopandas`, `shapely`, `pyproj` for geospatial work — these are non-trivial to install
  on some platforms; uv resolves the binary wheels correctly via the lockfile

Optional:
- `[viz]` extra: `plotly`, `kaleido`. Reporting figure builders gate `import plotly` so
  the rest of the package works without them. Don't add a hard plotly dep to any
  non-reporting module.
  - Figure builders live in `reporting/figures.py` (pure `DataFrame[, geo] -> plotly Figure`,
    plus `save_figure` → PNG via kaleido), behind a `HAS_PLOTLY` flag. `pipelines/reporting.py`
    orchestrates: it renders the figures named in `cfg.pipelines.reporting.figures` to
    `output_dir/figures/`, reading the *other* pipelines' CSVs from `output_dir` (so it runs
    last in `run all`). Missing input or missing plotly → skip-with-warning, never fail the
    run. Choropleths use plotly's tile-free `px.choropleth` (no Mapbox token); join key is
    TIGER `PUMACE20` ↔ `puma_summary.PUMA`.

Dev:
- `pytest`, `pytest-regressions`, `responses` (HTTP mocking)
- `ruff` for lint + format
- `mypy` (config in `pyproject.toml`; not enforced in CI yet)

## CI

`.github/workflows/ci.yml` runs the matrix on Python 3.10/3.11/3.12 via
`astral-sh/setup-uv@v3` + `uv sync --all-extras`. Lint and tests are required; parity
tests are skipped (no `/data/eoc/...` in CI).

## When adding a new state

1. SMI is already packaged for all states (FY2025/FY2026). For a later FY, add rows to
   `src/energy_equity/data/smi/liheap_smi_by_state_year.csv` (cite the IM in `provenance.csv`)
   or point `data_sources.smi_csv` at your own table; or set `thresholds.compute_smi=false`
   to run without SMI. New SMI rows must satisfy the CFR-96.85 integrity test.
2. Obtain a county-level HUD 80% AMI CSV for the state and FY you want (HUD publishes
   these annually). Point `data_sources.hud_ami_csv` at it.
3. The Census API call for county FIPS works for any state — no code changes needed.
4. Optionally pass `census_api.key_env` if your key lives in a different env var.

## When adding a new pipeline

1. Add a module under `src/energy_equity/pipelines/<name>.py` with a `run(cfg, ...)` entry
   point.
2. Add a `<Name>PipelineConfig` model in `config.py` and wire it into `PipelinesConfig`.
3. Add a Typer subcommand in `cli.py` and chain it from `run_all` if it belongs there.
4. Add a synthetic-fixture integration test under `tests/integration/`.
5. If notebook outputs exist for the new pipeline, add a `test_*_parity.py` gated by
   `EE_PARITY_DATA`.

## Things to never do

- Don't add Colorado-specific data outside `data/smi/`. Use `cfg.geography.state_fips`.
- Don't hardcode a Census API key. Read from `os.environ[cfg.census_api.key_env]`.
- Don't import plotly outside `reporting/` or `pipelines/reporting.py` — it's optional.
- Don't bypass the `uv.lock` in CI ("just install latest"). The pinned versions are what
  the parity tests were verified against.
- Don't reintroduce raw DataFrames into pipeline signatures where `HouseholdMicrodata`
  fits — the dataclass is the contract.
- Don't use `print()` in library or pipeline code — use loguru's `logger` (ruff `T20`
  enforces this). Don't call `configure_logging()` / `logger.enable(...)` from inside
  library code either; only the CLI entry point (or an explicit consumer) configures sinks.
