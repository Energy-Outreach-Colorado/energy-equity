# Colorado, calibrated to every utility (PUMS 2024)

This config produces the statewide PUMA tables with each household's electric and gas
costs calibrated to the EIA average bills of the utilities serving its PUMA
(`calibration.territories`, see `docs/bill_normalization.md`). It is the run behind the
census side of the equity dashboard, which reads `puma_overall.csv` and the
`puma_by_*.csv` files.

Run it from the repository root, since the HUD path is relative to it.

```sh
uv run ee run puma-table --config examples/colorado_calibrated_2024/config.yaml
```

The tables land in `out/colorado_calibrated_2024/`, together with
`calibration_territories.csv` (each utility's observed and target bills and the factor
applied) and `calibration_puma_factors.csv` (the blended factor in each PUMA).

The territory polygons are EOC files under `/data/eoc/geospatial/utilities`. The config
names no crosswalk files, so the run uses the packaged Colorado crosswalks in
`src/energy_equity/data/utilities/`, which map each polygon name to an EIA identifier.
The energy burden map reads the same packaged files, so the map and this run agree on
the utilities whenever they use the same library commit. That folder's README explains
how each identifier was chosen and which polygons are left out.

For a new PUMS year, copy this folder, update the vintages and PUMS paths, and check
that the library's packaged EIA-861 and EIA-176 tables include that year, since the
targets default to the EIA year equal to `pums_year`.
