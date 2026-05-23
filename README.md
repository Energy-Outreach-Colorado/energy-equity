# energy-equity

Energy burden, utility affordability, and rate-impact analysis from ACS PUMS microdata,
HUD AMI limits, and LIHEAP SMI thresholds. Parameterized by US state and service-territory
shapefile, so the same pipelines work for any utility, county, or program footprint.

## What it does

Given a service-territory polygon (a utility's gas or electric footprint, a county, a city), the
package builds:

1. **State and PUMA-level affordability tables** with replicate-weight 90% margins of error
   (energy burden, rent burden, AMI ≤80%, SMI ≤60%, demographic cuts).
2. **Service-territory allocations** by tract-household-weighted PUMA shares, with optional
   urban/rural splits.
3. **Eligibility analysis** comparing current vs proposed energy-burden thresholds
   (configurable; default 6% / 2.5% for Colorado PIPP).
4. **Fixed-charge / rate-increase impact scenarios**: how many households are newly
   energy-burdened at 6% and 10% under a proposed monthly bill increase, with demographic
   incidence and a sensitivity sweep.
5. **Persuasive reporting metrics**: service vs statewide income distribution (ACS B19001),
   regressivity curves, double-burden (energy + rent) intersections, choropleth maps.

## Status

Pre-release. v0.1 

## Install

Recommended (with [uv](https://docs.astral.sh/uv/), which is dramatically faster and
gives you a reproducible lockfile):

```sh
uv sync                                   # core deps
uv sync --extra viz                       # adds Plotly + Kaleido for figures
uv sync --all-extras                      # everything + dev tools
```

Or with plain pip:

```sh
pip install energy-equity                 # core
pip install "energy-equity[viz]"
pip install "energy-equity[dev]"
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

Outputs land under `project.output_dir` from the config.

## Data you need to provide

- A service-territory polygon (`.shp`, `.geojson`, or `.gpkg`)
- ACS PUMS housing + person ZIPs for your state (Census)
- TIGER PUMA + tract shapefile ZIPs for your state (Census)
- A county-level HUD 80% AMI limits CSV for the FY you want
- A free Census API key in the `CENSUS_API_KEY` environment variable

The CLI prints concrete download URLs and Census API endpoints if a required file is missing.

## License

MIT. See `LICENSE`.
