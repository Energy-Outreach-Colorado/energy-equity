# Utility territory crosswalks

These files tie utility service-territory polygons to EIA identifiers, so
`territory_calibration` can calibrate each utility's self-reported PUMS costs to its
administrative average bill. They are the single copy used everywhere the calibration
runs. The `calibration.territories` config block reads them when it names territory
files without crosswalks, and the energy burden map's ETL reads them through
`load_packaged_crosswalk`. Pinning the same library commit therefore guarantees the same
utilities in every product.

## Files

One pair per state, named by the lower-case state abbreviation.

| file                      | maps                                                    |
| ------------------------- | ------------------------------------------------------- |
| `co_electric_crosswalk.csv` | electric territory names to EIA-861 utility numbers   |
| `co_gas_crosswalk.csv`      | gas territory names to EIA-176 company identifiers    |

Both have two columns, `territory_name` and `eia_id`. Several names may map to one
identifier when a utility's territory is drawn in pieces, as the three Black Hills gas
pieces are, since EIA-176 reports Black Hills as one company.

The polygons are not packaged. The Colorado names match the `Name` column of EOC's
`utility_electric.shp` and `atmos_gas.shp`, kept under `/data/eoc/geospatial/utilities`
on the development host and uploaded to the map's input bucket for production loads.

## How the Colorado identifiers were chosen

Names were matched by hand against the packaged 2024 Colorado rows of `eia861/` and
`eia176/` on 2026-09-27. Intermountain Rural Electric Association reports to EIA under
its current name, CORE Electric Cooperative (utility 9336).

Some polygons are left out on purpose.

- Small municipal systems such as Aspen, Burlington and Holyoke, and Gunnison County
  Electric Association, have no residential row in the packaged EIA-861 table, so they
  have no target. The PUMAs they sit in take the blended factor of the other utilities
  there.
- Southwestern Electric Coop and Wheatland Electric Coop serve fewer than 40 Colorado
  customers, and Tri-County Electric has no Colorado polygon.
- New Mexico Gas Company, Navajo Tribal Utility, ComFurT Gas, Eastern Colorado Utility,
  Keyes Utility Authority, Lamar Utilities and Walsenburg Utilities have no Colorado
  EIA-176 residential row.

`build_territories` raises when a crosswalk name has no matching polygon, so a renamed
feature fails the run instead of silently dropping a utility.
`tests/unit/test_utility_crosswalk_integrity.py` checks that every identifier has a
residential row in the packaged EIA tables for each year they cover.
