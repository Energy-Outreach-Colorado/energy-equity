# Colorado, calibrated to every utility (PUMS 2024)

This config produces the statewide PUMA tables with each household's electric and gas
costs calibrated to the EIA average bills of the utilities serving its PUMA
(`calibration.territories`, see `docs/bill_normalization.md`). It is the run behind the
census side of the equity dashboard, which reads `puma_overall.csv` and the
`puma_by_*.csv` files.

Run it from the repository root, since the crosswalk and HUD paths are relative to it.

```sh
uv run ee run puma-table --config examples/colorado_calibrated_2024/config.yaml
```

The tables land in `out/colorado_calibrated_2024/`, together with
`calibration_territories.csv` (each utility's observed and target bills and the factor
applied) and `calibration_puma_factors.csv` (the blended factor in each PUMA).

The territory polygons are EOC files under `/data/eoc/geospatial/utilities`.
`electric_crosswalk.csv` and `gas_crosswalk.csv` map each polygon name to an EIA
identifier. They are the same files the energy burden map loads from
`etl/data/utilities/` in the front-end repository, whose README explains how each
identifier was chosen and which polygons are left out, so change both together.

For a new PUMS year, copy this folder, update the vintages and PUMS paths, and check
that the library's packaged EIA-861 and EIA-176 tables include that year, since the
targets default to the EIA year equal to `pums_year`.
