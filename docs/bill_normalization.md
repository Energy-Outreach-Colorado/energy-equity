# Utility bill normalization (electric and gas)

Optional calibration of PUMS-derived energy costs against utility-reported data,
following the approach used by the [DOE LEAD tool](https://data.openei.org/submissions/6219).
Electric costs calibrate against EIA Form 861 (per utility) and natural gas costs
against EIA Form 176 (per company). Each fuel is configured independently via the
`calibration.electric` and `calibration.gas` blocks and is disabled entirely when its
block is absent.

## Why

ACS PUMS energy costs (`ELEP` for electricity, `GASP` for gas) are survey self-reports
and carry recall error, rounding, and seasonal misreporting. Respondents often report a
recent month rather than an annual average, and the bias can vary by year. EIA
administrative data provides ground truth for the *average*: each utility or gas
company reports its total residential revenue and residential customer count, so

```
average annual residential bill = revenue / customers
```

Normalization anchors the **level** of each PUMS fuel component to its administrative
average while preserving the PUMS **distribution shape**. The distribution (which
households spend more, how costs relate to income) is what makes household-level burden
analysis possible, which is why the package rescales PUMS costs rather than replacing
them with modeled bills.

## The two stages

1. **Diagnostic (always runs when configured).** For each configured fuel the package
   computes the service-weighted average annual bill implied by PUMS among households
   that pay for that fuel, compares it to the target, and reports both plus their ratio
   in the log and in `calibration_electric.csv` / `calibration_gas.csv`. No estimate
   changes. If the ratio is close to 1, calibration is unnecessary.

2. **Calibration (`apply: true`).** Each paying household's annual cost for the fuel is
   multiplied by

   ```
   factor = target_annual_bill / observed_weighted_avg_annual_bill
   ```

   the household's total annual energy cost is re-summed from its components, and
   energy burden and its flags are computed from the calibrated costs. This happens
   inside `prepare_household_microdata` *before* burden is thresholded, because a cost
   level shift moves households across the burden cutoffs nonlinearly. All downstream
   pipelines inherit the calibrated costs automatically. The two fuels can be applied
   together; totals re-sum from current components, so the result does not depend on
   order.

### Who is calibrated

Only households with a reported cost for the fuel (raw `ELEP` or `GASP` present). PUMS
records a cost as missing both for "included in rent" and for "no charge / not used";
those households are excluded from the observed average and never rescaled. This
matches the administrative denominator, which counts bill-paying customers. All-electric
homes are therefore never touched by gas calibration.

### How the observed average is weighted

The observed mean uses each household's PUMS weight (`WGTP`) multiplied by its PUMA's
`share_households_in_service`, so it estimates the average bill within the service
territory being analyzed. The CLI builds these geography-only shares before microdata
preparation whenever either fuel is configured. Without shares the average falls back
to statewide with a logged warning and `scope: state` in the diagnostic.

## Configuration

Both packaged datasets ship with the package (see the data READMEs under
`src/energy_equity/data/eia861/` and `src/energy_equity/data/eia176/`), so the usual
configuration is just the utility and company selection:

```yaml
calibration:
  electric:
    utility_number: 15466      # EIA-861 utility ID
    apply: false               # start with the diagnostic
  gas:
    company_id: 17611459       # EIA-176 company ID (unrelated to the EIA-861 number)
    apply: false
```

Rows are filtered by `geography.state_abbr` automatically, and the data year defaults
to `vintages.pums_year` (the survey end-year, matching the PUMS dollar basis). Pin a
different year with `eia861_year` / `eia176_year`. Name selection also works
(`utility_name` / `company_name`, case-insensitive), but note that EIA-176 names are
often abbreviated ("PUB SERVICE CO OF COLORADO") — check the packaged CSV for the
exact spelling. There is no shared identifier between the two forms, so electric and
gas are always selected separately.

A target can be supplied directly, skipping the tables entirely
(`target_annual_bill: 1153.40` in either block), and each packaged table can be
overridden with your own CSV via `data_sources.eia861_csv` / `data_sources.eia176_csv`.

## The packaged data

- **Electric**: `data/eia861/eia861_residential.csv` — residential revenue, sales, and
  customer counts per utility, state, and year (2022 through 2024 final releases),
  aggregated from the EIA "Sales to Ultimate Customers" workbooks by
  `scripts/build_eia861_residential_csv.py` using EIA's own part-counting rules.
- **Gas**: `data/eia176/eia176_residential.csv` — residential sales revenue, volume,
  and customer counts per company, state, and year (2022 through 2024), from the EIA
  NGQS company-level API by `scripts/build_eia176_residential_csv.py`. Sales only:
  transport customers buy their gas elsewhere, so distributor transport revenue
  excludes the commodity cost.

Both ship with `provenance.csv` files and data-integrity tests. To add a year, rerun
the builder script; the schema details and custom-CSV column requirements are in each
data README.

When volume data is present the diagnostics also report the effective average rate
(`avg_rate_per_kwh` for electric, `avg_price_per_mcf` for gas).

## Output

`calibration_electric.csv` and `calibration_gas.csv` (one row each) in the output
directory:

- `observed_avg_annual_bill`, `target_annual_bill`, `factor`
- `n_payers_unweighted`, `payer_weighted_households`, `scope`
- `target_source` (`config`, `eia861_csv`/`eia176_csv`, or
  `eia861_packaged`/`eia176_packaged`) and the matched identifiers
- `applied` — whether the factor was actually applied to the microdata

## Trend comparisons: all years or none

Within a multi-year trend comparison (`ee run trends`), every year must use the same
calibration treatment per fuel. Applying a level correction to some years but not
others manufactures a fake trend out of the correction itself. The trends pipeline
records each run's basis in `electric_calibration` and `gas_calibration` columns
(`calibrated`, `diagnostic`, or `uncalibrated`) and warns loudly when they are mixed.

## Calibrating every utility at once

The configuration above calibrates one service area to one utility. A statewide product
such as a map needs every utility at the same time, and PUMS locates a household only to
its PUMA, which several utilities may serve. `energy_equity.territory_calibration`
handles that case.

1. **One factor per utility.** Each territory's observed average weights paying
   households by `WGTP` times its PUMA share, exactly as the single-territory
   diagnostic does, and its own factor is target over observed.
2. **Pooling small territories.** A PUMA holds about 100,000 people, so a small
   utility's weighted households are mostly its neighbours' customers. A territory whose
   overlap (the household-weighted mean of its PUMA shares) is below 0.35 takes the
   pooled factor of the reliable territories in its EIA ownership class, and falls back
   to all reliable territories when its class has none. Cooperatives and municipal
   utilities behave differently enough in Colorado (reliable cooperatives' own electric
   factors mostly ran 0.69 to 0.91 in 2024, municipal utilities' 0.58 to 0.68) that a
   single statewide pool would over-correct rural households.
3. **Blending by PUMA.** Each PUMA applies the share-weighted mean of the factors of the
   territories that touch it. A PUMA no territory touches keeps its reported costs.
4. **Combined bills.** Some utilities, Xcel Energy among them, bill gas and electricity
   together, and PUMS records such households with `GASFP = 2` and the whole bill in
   `ELEP`. In the 2024 Colorado 1-year file they are 25.9% of weighted electric payers.
   They are left out of the electric average and calibrated against the combined target,
   the utility's electric average plus the blended gas average over its territory.

The simplest way in is the `calibration.territories` config block, which the CLI and
`prepare_household_microdata` pick up on their own.

```yaml
calibration:
  territories:
    electric_territories: /data/eoc/geospatial/utilities/utility_electric.shp
    electric_crosswalk: examples/colorado_calibrated_2024/electric_crosswalk.csv
    gas_territories: /data/eoc/geospatial/utilities/atmos_gas.shp
    gas_crosswalk: examples/colorado_calibrated_2024/gas_crosswalk.csv
```

Each crosswalk is a CSV with `territory_name` and `eia_id` columns, mapping the polygon
names in `name_column` (`Name` by default) to EIA-861 utility numbers or EIA-176
company identifiers. Either fuel may be left out. `eia_year` defaults to
`vintages.pums_year` and `min_overlap` to 0.35, and `data_sources.eia861_csv` and
`eia176_csv` replace the packaged tables when set. The block cannot be combined with
the `electric` and `gas` blocks. A run writes `calibration_territories.csv`, one row per
fuel and utility with its observed and target bills, own factor, overlap and the factor
applied, and `calibration_puma_factors.csv`, the blended factor in each PUMA.
`examples/colorado_calibrated_2024/` is the complete Colorado configuration.

From Python, pass territories to `prepare_household_microdata(cfg,
electric_territories=..., gas_territories=...)` instead. The result's
`territory_calibration` holds the per-utility tables and the factor applied in each
PUMA. `build_territories` builds territories from a polygon layer and a crosswalk, and
`territories_from_config` does the same from a config block.

On the 2024 Colorado file this moved the statewide share of households above 6% burden
from 13.8% to 9.3% and above 10% from 7.7% to 5.1% (checked 2026-09-27). Xcel's own
electric factor is 0.64 once combined-bill households are separated, against 0.59 with
them included, and those households' combined factor is 0.85.

The single-territory path still treats combined-bill households as ordinary electric
payers.

## Limitations (disclose when publishing calibrated numbers)

- **Customers are meters, not households.** A master-metered multifamily building is
  one customer serving many PUMS households. LEAD carries the same approximation.
- **The service polygon may be a subset of the utility.** The administrative average
  covers the whole territory; usage varies spatially within it.
- **Dollar-vintage alignment is approximate.** PUMS costs are adjusted to the survey
  end-year; the EIA files are calendar-year. Use the matching year and note the choice.
- **Bypass and transport service.** Electric: restructured retail markets can inflate
  packaged averages (revenue includes delivery-only business, customers do not). Gas:
  transport-only customers are excluded entirely. Vertically integrated utilities are
  exact. See the data READMEs.
- **Factor uncertainty is not propagated.** The observed average is itself a survey
  estimate, but the factor is treated as fixed in the MOE machinery (the same
  convention as the geographic shares).
- The households excluded from calibration (cost missing, treated as $0 under
  `missing_cost_rule: zero`) remain subject to the master-metered-renter
  understatement documented in the methodology notes.
