# CLI reference

The package installs a console command, **`ee`** (alias **`energy-equity`**). With uv,
prefix any invocation with `uv run`; with an activated virtualenv you can drop the prefix.

```sh
uv run ee --help
uv run ee run all --config config.yaml
```

Every command that runs analysis takes a single YAML config (`--config/-c`); see
[`ee config init`](#ee-config-init) to scaffold one, and the `Config` model in
`src/energy_equity/config.py` for the full schema. Output files are documented in
[outputs.md](outputs.md).

## Global options

These belong to the top-level `ee` command and must be placed **before** the subcommand
(e.g. `ee -v run all …`, not `ee run all -v`).

| Option | Effect |
|---|---|
| `--verbose`, `-v` | Set log level to DEBUG (adds path-resolution and cache detail). |
| `--quiet`, `-q` | Set log level to WARNING (suppresses the per-file "wrote …" lines). |
| `--version`, `-V` | Print the installed version and exit. |
| `--help` | Show help for `ee` or any subcommand. |

Logging goes to **stderr** (default level INFO), so stdout stays clean for piping. On
startup the CLI auto-loads a `.env` file from the current directory (searching upward), so
`CENSUS_API_KEY` set there is picked up automatically; a value already exported in the shell
takes precedence over `.env`.

## `ee run` — analysis pipelines

All `ee run <pipeline>` commands take `--config/-c <path.yaml>` (required). They write CSVs
(and, for `reporting`, PNG figures) under `project.output_dir` from the config.

The pipelines form a dependency chain. `eligibility`, `fixed-charge`, and `reporting` read
CSVs that `puma-table` and `service-allocation` must have written first. **`ee run all`
sequences everything correctly**; run the individual subcommands only when those
prerequisites already exist in `output_dir`.

| Command | Purpose | Reads | Writes (see [outputs.md](outputs.md)) |
|---|---|---|---|
| `ee run puma-table -c …` | State- and PUMA-level affordability tables with replicate-weight MOEs. Builds the AMI bridge from the HUD CSV + Census if not cached. | PUMS ZIPs, HUD AMI CSV, Census API | `state_overall`, `puma_overall` (+ `…_replicates.csv.gz`), `state_by_*`, `puma_by_*` |
| `ee run service-allocation -c …` | Allocates PUMA estimates into the service-area polygon via tract-household-weighted shares (with urban/rural split). | TIGER PUMA + tract ZIPs, service shapefile, `puma_overall*`, Census tract households | `<service>_puma_household_shares`, `<service>_by_puma`, `<service>_totals`, `<service>_rates` |
| `ee run eligibility -c …` | Program-eligibility comparison (current vs proposed burden threshold) + demographic breakdowns. | PUMS, service shares | `headline_summary`, burden-band tables, `demographics_*`, `puma_summary` |
| `ee run fixed-charge -c …` | Rate-increase scenario: who becomes newly energy-burdened, plus a sweep over increase amounts. | PUMS, service shares | `fixed_charge_headline_summary`, `delta_sensitivity_summary`, `scenario_sweep` |
| `ee run reporting -c …` | Service-vs-statewide income comparison, regressivity, and **all PNG figures**. | Census B19001, TIGER tracts, service shapefile, sibling CSVs from the above | `income_distribution_service_vs_state`, `income_cutpoint_shares`, `regressivity_table`, `figures/*.png` |
| `ee run trends -c …` | Compares completed per-year runs (`pipelines.trends.runs`) into trend tables and figures with 90% MOE bands. | each listed run's `affordability_gap.csv`, `<service>_totals.csv`, `<service>_rates.csv` | `trends_affordability_gap`, `trends_energy_burden`, `trends_deltas`, `figures/trend_*.png` |
| `ee run all -c …` | Runs the five core pipelines in dependency order (`trends` is separate; it needs completed runs first). | all of the above | all of the above |

## `ee data` — download Census inputs

| Command | Purpose |
|---|---|
| `ee data fetch -c …` | Download every Census input the config needs into the cache and print one line per input with its status, size and path. Exits 1 when any input failed. |

| Option | Meaning |
|---|---|
| `--config`, `-c` | Path to the YAML config (required). |
| `--dry-run` | List each file's destination and source URLs without downloading. |
| `--force` | Download the files again even when cached. Census API responses stay cached. |
| `--skip-api` | Skip the Census API calls (county list, tract households, B19001 incomes). |

It fetches the PUMS housing and person ZIPs and the TIGER tract and PUMA ZIPs (unless
`data_sources` points at local files), the national TIGER urban-area ZIP when the
urban/rural split is on, the 2020 tract→PUMA relationship file, and the ACS tables a run
requests from the Census API, which need `CENSUS_API_KEY`. Each status is `configured`
(a local file from the config), `cached`, `downloaded`, `planned` (dry run), `skipped` or
`failed`. Pipelines download anything missing on first use as well, so the command is
optional, but it makes a later run work offline and shows every missing input at once.

PUMA boundaries must use the same decennial definitions as the PUMS codes, so config
validation rejects a `vintages.tiger_year` whose PUMA layer differs from the microdata's
(PUMS 2022 and later, and 5-year files ending 2023 or later, use 2020 PUMAs). The
2018-2022 5-year PUMS file is rejected because it has no single `PUMA` column.

```sh
uv run ee data fetch --config config.yaml --dry-run
uv run ee data fetch --config config.yaml
```

## `ee cache` — on-disk cache

The cache holds downloaded PUMS and TIGER files, Census API responses, the tract→PUMA
relationship file, TIGER urban-area shapefiles, and the built AMI bridge. PUMS ZIPs sit
under `pums/<year>_<span>yr/` and TIGER ZIPs under `tiger/<year>/`. Its location is resolved as: config
`project.cache_dir` → `EE_CACHE_DIR` env var → platformdirs default
(`~/.cache/energy-equity` on Linux).

| Command | Purpose |
|---|---|
| `ee cache info [-c config.yaml]` | Print the cache directory and its total size. With `--config`, the cache that config uses. |
| `ee cache clear --what {census\|pums\|tiger\|all} [-c config.yaml]` | Delete a cache subset (default `all`). Prompts for confirmation unless `--yes/-y` is given. |

## `ee config` — validate / scaffold

| Command | Purpose |
|---|---|
| `ee config validate <path.yaml>` | Parse and validate a config; prints the project name and state on success, or the validation error. |
| `ee config init …` | Write a starter `config.yaml`. The PUMS and TIGER paths are null so they download automatically; the service-area shapefile and HUD AMI CSV are placeholders to fill in. |

### `ee config init`

| Option | Required | Meaning |
|---|---|---|
| `--state` | yes | Two-letter postal code (e.g. `CO`, `IL`). |
| `--state-fips` | yes | Two-digit state FIPS (e.g. `"08"`). |
| `--output`, `-o` | no (default `config.yaml`) | Where to write the scaffold. |
| `--overwrite` | no | Overwrite an existing file instead of erroring. |

```sh
uv run ee config init --state CO --state-fips 08 --output config.yaml
$EDITOR config.yaml          # set the service-area shapefile and HUD AMI CSV
uv run ee config validate config.yaml
uv run ee data fetch --config config.yaml
uv run ee run all --config config.yaml
```
