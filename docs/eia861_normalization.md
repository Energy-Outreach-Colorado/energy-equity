# EIA-861 electric bill normalization

Optional calibration of the electric portion of PUMS-derived energy costs against
utility-reported EIA Form 861 data, following the approach used by the
[DOE LEAD tool](https://data.openei.org/submissions/6219). Configured via the
`calibration.electric` block; disabled entirely when the block is absent.

## Why

ACS PUMS electric costs (`ELEP`) are survey self-reports and carry recall error,
rounding, and seasonal misreporting (respondents often report a recent month rather than
an annual average). EIA Form 861 provides administrative ground truth for the *average*:
each utility reports its total residential revenue and residential customer count, so

```
average annual residential bill = revenue / customers
```

Normalization anchors the **level** of the PUMS electric component to this
administrative average while preserving the PUMS **distribution shape** — which
households spend more, how costs relate to income, household size, and fuel mix. That
distribution is what makes household-level burden analysis possible, which is why the
package rescales PUMS costs rather than replacing them with modeled bills.

## The two stages

1. **Diagnostic (always runs when configured).** The package computes the
   service-weighted average annual electric bill implied by PUMS among bill-paying
   households, compares it to the target, and reports both plus their ratio in the log
   and in `calibration_electric.csv` in the output directory. No estimate changes.
   Use this first to learn whether your published burden numbers carry a level bias
   and in which direction. If the ratio is close to 1, calibration is unnecessary.

2. **Calibration (`apply: true`).** Each paying household's annual electric cost is
   multiplied by

   ```
   factor = target_annual_bill / observed_weighted_avg_annual_bill
   ```

   the household's total annual energy cost is re-summed, and energy burden and its
   flags are computed from the calibrated costs. This happens inside
   `prepare_household_microdata` *before* burden is thresholded — a cost level shift
   moves households across the 6% / 10% burden cutoffs nonlinearly, so it cannot be
   applied to aggregated counts after the fact. All downstream pipelines
   (`puma_table`, `service_allocation`, `eligibility_analysis`, `fixed_charge`)
   inherit the calibrated costs automatically.

### Who is calibrated

Only households with a reported electric cost (raw `ELEP` present). PUMS records
electric cost as missing both for "included in rent / condo fee" and for
"no charge / not used"; those households are excluded from the observed average and
their costs are never rescaled. This matches the target's denominator — EIA-861 counts
bill-paying customers.

### How the observed average is weighted

The observed mean uses each household's PUMS weight (`WGTP`) multiplied by its PUMA's
`share_households_in_service`, so it estimates the average bill *within the service
territory* being analyzed. The CLI builds these geography-only shares before microdata
preparation and reuses them for the service-allocation pipeline. If no shares are
available (e.g. calling `prepare_household_microdata` directly without
`service_shares`), the average falls back to statewide with a logged warning and
`scope: state` in the diagnostic — interpret the factor cautiously in that case, since
the EIA-861 target is utility-specific.

## Configuration

The package ships EIA-861 residential data for every US utility (see
[the packaged-data README](../src/energy_equity/data/eia861/README.md)), so the
usual configuration is just the utility selection:

```yaml
calibration:
  electric:
    utility_number: 15466      # EIA utility ID (find yours in the packaged CSV)
    apply: false               # start with the diagnostic
```

The row is further filtered by `geography.state_abbr` automatically (multi-state
utilities have one row per state), and the data year defaults to `vintages.pums_year` —
the survey end-year, which matches the PUMS dollar basis. Pin a different year with
`eia861_year`. `utility_name` (case-insensitive) works instead of, or in addition to,
`utility_number`. `apply` defaults to `false` (diagnostic only).

A target can also be supplied directly, skipping the table entirely:

```yaml
calibration:
  electric:
    target_annual_bill: 1153.40
```

or the packaged table can be overridden with your own CSV (same schema):

```yaml
data_sources:
  eia861_csv: ./data/my_eia861_residential.csv
```

## The packaged data / preparing your own CSV

`src/energy_equity/data/eia861/eia861_residential.csv` holds residential revenue,
sales, and customer counts per utility-state-year, currently for the 2023 and 2024
final releases, aggregated from the EIA "Sales to Ultimate Customers" workbooks by
`scripts/build_eia861_residential_csv.py` (EIA's own part-counting rules; see the
data README for details and `provenance.csv` for sources). To add a year, rerun the
script with the new workbook — `tests/unit/test_eia861_data_integrity.py` guards the
result.

A custom override CSV needs these columns:

| column                     | meaning                                                |
| -------------------------- | ------------------------------------------------------ |
| `utility_number`           | EIA utility ID                                         |
| `utility_name`             | utility name                                           |
| `state`                    | two-letter state                                       |
| `customer_class`           | must be `residential` for the rows used                |
| `revenue_thousand_dollars` | residential revenue, thousands of USD                  |
| `customers`                | residential customer (meter) count                     |
| `year` *(optional)*        | data year; lets one CSV hold multiple years            |
| `sales_mwh` *(optional)*   | residential sales; yields `avg_rate_per_kwh` in output |

When `sales_mwh` is present, the diagnostic also reports the utility's effective
average residential rate (`revenue / sales`), useful for grounding volumetric
rate-impact scenarios.

## Output

`calibration_electric.csv` (one row) in the output directory:

- `observed_avg_annual_bill`, `target_annual_bill`, `factor`
- `n_payers_unweighted`, `payer_weighted_households`, `scope`
- `target_source` (`config` or `eia861_csv`), utility identifiers and
  `avg_rate_per_kwh` when derived from a CSV
- `applied` — whether the factor was actually applied to the microdata

## Limitations (disclose when publishing calibrated numbers)

- **Customers are meters, not households.** A master-metered multifamily building is
  one EIA-861 customer serving many PUMS households. The average bill per customer is
  not exactly the average per bill-paying household. LEAD carries the same
  approximation.
- **The service polygon may be a subset of the utility.** The EIA-861 average covers
  the utility's whole territory; rates are uniform per utility but usage varies
  spatially, so a sub-territory's true average may differ.
- **Dollar-vintage alignment is approximate.** PUMS costs are adjusted to the survey
  end-year; EIA-861 is a calendar-year file. Choose the matching year and note the
  choice.
- **Restructured retail markets distort the packaged averages.** Per EIA's counting
  rules, revenue sums bundled + energy + delivery parts while customers count
  bundled + energy only, so utilities with substantial delivery-only business (retail
  choice states) can show inflated averages. Vertically integrated utilities (one
  bundled row) are exact. See the packaged-data README.
- **Electric only.** Calibrating electric but not gas shifts the electric share of
  total burden. Gas calibration via EIA-176 is a natural extension and is not yet
  implemented.
- The households excluded from calibration (cost missing → treated as $0 under
  `missing_cost_rule: zero`) remain subject to the master-metered-renter understatement
  documented in the methodology notes; calibration neither worsens nor fixes it.
