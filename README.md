# energy-equity
The purpose of this code base is to provide a standardized methodology to calculate energy affordability
for across a specified geospatial area. The metrics include energy burden, utility affordability, and 
rate-impact analysis from ACS PUMS microdata, HUD AMI limits, and LIHEAP SMI thresholds. Geospatial areas
are defined using US state and service-territory shapefiles.

## Analysis overview

At a high level, the repository answers five kinds of questions about a utility service
territory or other geography utilizing public US Census microdata:

- **Energy burden** — what share or percentage of household income goes to home energy, who is
  "energy-burdened" (≥6%) or "highly burdened" (≥10%), and how that varies across the
  population.
- **Affordability & program eligibility** — how many households fall under income
  thresholds (≤80% Area Median Income, ≤60% State Median Income) and burden thresholds, and
  how a *proposed* change in utility rates would expand or shrink the eligible population.
- **Affordability Gap** - The total annual dollars needed to bring every
  household's energy burden down to a target threshold, which sizes the budget for a
  percentage-of-income payment plan (PIPP) or bill-assistance program.
- **Rate-impact scenarios** — if a utility raises a fixed monthly charge, how many
  households get pushed over the burden thresholds, who they are, and how the impact scales
  with the size of the increase.
- **Equity & distribution** — the same metrics broken out by race, ethnicity, age, tenure,
  and language; the service area's income distribution vs the statewide distribution; and
  the regressivity of a flat charge (it costs lower-income households a larger share of
  income).
- **Geographic / service-territory estimation** — because Census microdata is only published at
  the coarse PUMA level, this code allocates PUMA estimates into an user provided service-area
  polygon using tract-household-weighted small-area estimation, with optional urban/rural
  splits and PUMA-level maps.

The sections below describe the concrete pipelines and their outputs. See
[Documentation](#documentation) for the full command and output reference.

## What it does

Given a service-territory polygon (a utility's gas or electric footprint, a county, a city), the
package builds:

1. **State and PUMA-level affordability tables** with replicate-weight 90% margins of error
   (energy burden, rent burden, AMI ≤80%, SMI ≤60%, demographic cuts).
2. **Service-territory allocations** by tract-household-weighted PUMA shares, with optional
   urban/rural splits.
3. **Eligibility analysis** comparing current vs proposed energy burden thresholds
   (configurable; default 6% / 2.5% for Colorado PIPP), including affordability gap
   sizing. Affordability gap sizing is the annual dollars required to bring households at or below a burden
   threshold, reported by population segment and per PUMA.
4. **Fixed-charge / rate-increase impact scenarios**: how many households are newly
   energy-burdened at 6% and 10% under a proposed monthly bill increase, with demographic
   incidence and a sensitivity sweep.
5. **Reporting metrics**: service vs statewide income distribution (ACS B19001),
   regressivity curves, double-burden (energy + rent) intersections, choropleth maps.

## Status

Pre-release. v0.1 

## Authors
- [@e-baumer](https://github.com/e-baumer)
- [@tfpgh](https://github.com/tfpgh)

## Install

Recommended (with [uv](https://docs.astral.sh/uv/)

```sh
uv sync                                   # core deps
uv sync --extra viz                       # adds Plotly + Kaleido for figures
uv sync --all-extras                      # everything + dev tools
```

Requires Python 3.10+. The committed `uv.lock` pins exact versions used in
development and CI.

## Quickstart

```sh
uv run ee config init --state CO --state-fips 08 --output config.yaml
$EDITOR config.yaml                       # set paths to your service shapefile, PUMS, AMI
uv run ee run all --config config.yaml
```

(Without uv, replace `uv run ee` with `ee` after activating your venv.)

Outputs land under `project.output_dir` from the config: CSV tables from every pipeline and
PNG figures under `figures/` (the latter need the `viz` extra). What each command and each
output table/figure means is documented in full under [Documentation](#documentation).

## Documentation

- **[docs/cli.md](docs/cli.md)** — every CLI command and option (`ee run …`, `ee cache …`,
  `ee config …`), the global verbosity flags, and pipeline prerequisites.
- **[docs/outputs.md](docs/outputs.md)** — a column-naming key plus a description of every
  output table and figure the analysis produces.
- **[docs/analysis.md](docs/analysis.md)** — the analysis methodology: what each metric
  measures, the pipeline, and a detailed description of the error/margin-of-error analysis.
- **[docs/eia861_normalization.md](docs/eia861_normalization.md)** — optional EIA-861
  electric bill normalization: methodology, configuration, CSV schema, and caveats.

## EIA-861 bill normalization (optional)

PUMS electric costs are derived from self reported survey data. The EIA Form 861 reports each utility's actual
residential revenue and customer count. The optional `calibration.electric` config block
compares the PUMS-implied average annual electric bill in your service territory against
the utility's EIA-861 average (the diagnostic, written to `calibration_electric.csv`),
and with `apply: true` rescales each bill-paying household's electric cost by
`target / observed` before energy burden is computed. This normalizes the cost level to
administrative data while preserving the PUMS distribution similar to the DOE LEAD tool.

EIA-861 residential data for every US utility (2023 and 2024 final releases) is included in the
repository. You can enable the normalization by including the utility selection:

```yaml
calibration:
  electric:
    utility_number: 15466   # EIA utility ID; row auto-filtered to your state + PUMS year
    apply: false            # start with the diagnostic; set true to calibrate
```

Omit the block to disable the normalization. See
[docs/eia861_normalization.md](docs/eia861_normalization.md) for the packaged data,
custom-CSV override schema, and methodology caveats.

## Logging

The CLI logs progress to **stderr** at `INFO` by default (file-written confirmations,
key steps). Control verbosity with global flags placed before the subcommand:

```sh
uv run ee -v run all --config config.yaml   # DEBUG: also shows path resolution
uv run ee -q run all --config config.yaml   # WARNING and above only
uv run ee -V                                 # print version (note: -V, not -v)
```

Diagnostics go to stderr, so stdout stays clean for piping.

When you import the package as a library it is **silent by default** — it won't log
unless you opt in:

```python
import energy_equity
energy_equity.configure_logging("INFO")   # or: from loguru import logger; logger.enable("energy_equity")
from energy_equity.pipelines import puma_table
```

## Data you need to provide

- A service-territory polygon (`.shp`, `.geojson`, or `.gpkg`)
- ACS PUMS housing + person ZIPs for your state (Census)
- TIGER PUMA + tract shapefile ZIPs for your state (Census)
- A county-level HUD 80% AMI limits CSV for the FY you want - Colorado's HUD 80% AMI limits is already included in the repository
- A free Census API key in the `CENSUS_API_KEY` environment variable

### Census API key (`.env`)

Set `CENSUS_API_KEY` in your shell, or copy `.env.example` to `.env` and fill it in — the
`ee` CLI auto-loads a `.env` from the current directory (searching upward) at startup.
Real shell variables take precedence over `.env`. `.env` is gitignored; never commit a key.

**Data Sources**
- 2024 PUMS 1-year data - https://www2.census.gov/programs-surveys/acs/data/pums/2024/1-Year/
- Obtain Census API key - https://api.census.gov/data/key_signup.html
- TIGER PUMA + trac shapefile - https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.html
- Colorado 80% AMI Income Limits (CHFA) - https://www.chfainfo.com/rental-housing/asset-management/rent-income-limits
- An example CSV file for Colorado 80% AMI thresholds (2025) is provided in examples/

The CLI prints concrete download URLs and Census API endpoints if a required file is missing.

## License

MIT. See `LICENSE`.
