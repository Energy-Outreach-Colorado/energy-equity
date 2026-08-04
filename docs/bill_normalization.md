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
