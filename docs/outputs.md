# Outputs reference: tables & figures

Every pipeline writes CSVs (and the reporting pipeline writes PNG figures) under
`project.output_dir` from your config. This page explains what each file is. Rather than
list all columns of every table (`puma_overall.csv` alone has ~126), it documents a shared
**column-naming key** once, then gives each table's purpose, grain, and notable columns.

> **Interpretation caveats.** Several inherited methodology choices affect how these numbers
> should be read — ELEP/GASP "included in rent" costs treated as $0, AMI/SMI thresholds
> clipped for very large households, within-PUMA homogeneity in the service-area allocator,
> and tract area-weighting assuming uniform density. See the "Methodology notes" section of
> [`development.md`](development.md) before publishing figures. Always disclose these in a
> methodology appendix.

## Column-naming key

Most metric columns are generated from a few patterns. Learn these and the wide tables read
easily:

| Pattern | Meaning |
|---|---|
| `hh_*_w` | **Weighted household count** (sum of PUMS person/household weights). e.g. `hh_eb_w` = energy-burdened households. |
| `n_unweighted` | Unweighted sample size (number of PUMS records) for the row. |
| `*_w_moe90`, `pct_*_moe90` | **90% margin of error** for that estimate, via the Successive Difference Replicate method over 80 replicate weights (z = 1.6449). |
| `pct_*` | A **rate** (one weighted count ÷ another). |
| `small_n_flag` | True when `n_unweighted` is below the suppression threshold; rates in that row are blanked. |
| `*_eb*` / `*_heb*` | Energy-burdened (≥ burden threshold, default 6%) / highly energy-burdened (≥ high threshold, default 10%). |
| `*_le80` / `*_le60_smi` | Restricted to the **≤80% AMI** pool / the **≤60% SMI** pool. SMI thresholds are the official LIHEAP FFY limits (all states, FY2025–FY2027, one IM per fiscal year; `thresholds.smi_source="auto"` picks the year). Set `thresholds.compute_smi=false` to skip SMI — the `*_le60_smi` columns then report 0. |
| `*_rent_burdened` / `*_severe_*` | Gross rent ≥ 30% / ≥ 50% of income (renters). |
| `*_in_service`, `*_in_service_urban`, `*_in_service_rural` | A statewide estimate **allocated into the service area** (and its urban/rural split). |
| `share_households_in_service`, `urban_share_within_service` | Per-PUMA share of households inside the service area; urban share within that. |
| `segment` | `all`, `urban`, or `rural` (service-area split). |
| `population` | `All households`, `<=80% AMI`, `<=60% SMI`, or `<=80% AMI renters` (fixed-charge tables). |

`<dim>` below is one of: `race`, `ethnicity`, `gender`, `age`, `household_language`,
`head_language` (state/PUMA tables) or `race`, `ethnicity`, `gender`, `age`, `tenure`,
`household_language` (eligibility tables).

---

## Tables

### From microdata preparation (any of `ee run puma-table`, `eligibility`, `fixed-charge`, `all`)

**`calibration_electric.csv` / `calibration_gas.csv`** — one row each; written only when
the matching `calibration.electric` / `calibration.gas` config block is set. The bill
normalization diagnostics: `observed_avg_annual_bill`, `target_annual_bill`, `factor`,
`n_payers_unweighted`, `payer_weighted_households`, `scope` (`service_area` or `state`),
`target_source` and the matched utility or company identifiers (plus `avg_rate_per_kwh`
or `avg_price_per_mcf` when volume data is available), and `applied` — whether the
factor was applied to the microdata (`apply: true`) or reported only. See
[bill_normalization.md](bill_normalization.md).

### From `ee run puma-table`

**`state_overall.csv`** — one row; statewide totals for every baseline metric (energy
burden, ≤80% AMI, ≤60% SMI, rent burden) with counts, rates, and MOEs.

**`puma_overall.csv`** — one row per PUMA; the same metric set as `state_overall` plus
`PUMA`, `puma_name`, `primary_county`. This is the wide table (~126 cols) — read it through
the naming key. Notable: `hh_total_w`, `hh_eb_w`/`hh_heb_w`, `hh_le80_w`, `hh_le60_smi_w`,
`pct_eb_among_le80`, and the matching `*_moe90` columns.

**`puma_overall_replicates.csv.gz`** — one row per PUMA; the 80 replicate-weight estimates
per metric (`<metric>_w_rep01..rep80`). Machine input for propagating service-area MOEs in
`service-allocation`; not meant for direct reading.

**`state_by_<dim>.csv` / `puma_by_<dim>.csv`** — the baseline metric set broken out by a
demographic dimension: one row per category (state) or per PUMA × category.

### From `ee run service-allocation`

`<service>` is the service-area shapefile's filename stem.

**`<service>_puma_household_shares.csv`** — one row per PUMA: `households_total`,
`households_in_service`, `share_households_in_service`, `urban_share_within_service`,
`households_in_service_urban/rural`. The weights every service-area total is built from.

**`<service>_by_puma.csv`** — one row per PUMA: each allocated metric as `<metric>_in_service`
(+ `_urban`/`_rural`), i.e. each PUMA's contribution to the service-area totals.

**`<service>_totals.csv`** — three rows (`segment` = all/urban/rural): service-area totals
for each metric (`<metric>_in_service`) with `<metric>_in_service_moe90` (replicate-propagated).

**`<service>_rates.csv`** — long form: one row per (`segment`, `rate`) with `estimate`,
`moe90`, `numerator`/`denominator` names and their estimates.

### From `ee run eligibility`

**`headline_summary.csv`** — one row per `segment`. Program eligibility under current vs
proposed burden threshold: `low_income_households`, `current_eligible_households`,
`proposed_eligible_households`, `newly_added_households`, `absolute_increase_households`,
`pct_increase_vs_current`, and `*_rate_among_low_income` / `*_rate_of_all_service_households`.

**`low_income_burden_bands.csv`** — distribution of ≤80% AMI households across burden bands:
one row per (`segment`, `burden_band`) with `households` and `share_of_low_income`.

**`energy_burden_headline_summary.csv`** — like `headline_summary` but for the energy-burden
universe (all households, not only the program-eligible pool).

**`energy_burden_bands.csv`** — one row per (`segment`, `energy_burden_band`) with
`households`, `share_of_all_service_households`, `share_of_burden_valid_households`.

**`demographics_<dim>.csv`** — eligibility by demographic category: one row per category with
`low_income_households`, `current_eligible_households`, `proposed_eligible_households`,
`newly_added_households`, share-of-total and per-category rate columns.

**`demographics_<dim>_energy_burden.csv`** — the same breakdown for the energy-burden
universe (`energy_burdened_households`, `highly_energy_burdened_households`, shares, rates).

**`puma_summary.csv`** — one row per PUMA within the service area: eligibility + energy-burden
rollups (`service_households`, `current/proposed/newly_added_eligible`, `energy_burdened_*`,
rates). Source for the choropleth maps.

**`affordability_gap.csv`** — one row per (`segment`, `population`, `threshold`): the
affordability gap, i.e. the annual dollars needed to bring every household's energy burden
down to `threshold` (per household: `max(0, cost − threshold × income)`). Columns:
`gap_valid_households` (positive income, non-missing cost), `households_in_gap` (burden
strictly above the threshold), `total_gap_dollars`, `mean_gap_per_household_in_gap`,
`households_in_gap_rate`, and replicate-based `*_moe90` for the two weighted estimates.
Sizes a percentage-of-income plan or bill-assistance budget. Thresholds come from
`pipelines.eligibility_analysis.gap_thresholds` (default: the 6% and 10% burden thresholds).

**`affordability_gap_by_puma.csv`** — the same gap metrics per (`PUMA`, `threshold`) using
`w_service` weights; input for gap choropleths.

### From `ee run fixed-charge`

**`fixed_charge_headline_summary.csv`** — one row per (`segment`, `population`): baseline vs
post-increase counts (`baseline_energy_burdened_households`, `post_energy_burdened_households`,
`newly_energy_burdened_households`, the highly-burdened variants), `burden_valid_households`,
average burdens, and `annual_fixed_charge_increase_total`.

**`delta_sensitivity_summary.csv`** — one row per (`segment`, `population`,
`delta_threshold_percentage_points`): how many households see their burden rise by at least
that many points (`households`, `denominator_households`, `rate_among_burden_valid`).

**`scenario_sweep.csv`** — one row per (`segment`, `population`,
`monthly_fixed_charge_increase`): `post_energy_burdened_households`,
`newly_energy_burdened_households` (+ highly variants), `burden_valid_households`, and rates.
Drives the scenario-sweep figure.

### From `ee run reporting`

**`income_distribution_service_vs_state.csv`** — one row per B19001 income bin:
`service_households` / `statewide_households`, their shares, and `share_diff_pp`.

**`income_cutpoint_shares.csv`** — one row per income cutoff: cumulative share of households
below it, service vs statewide, and `diff_pp`.

**`regressivity_table.csv`** — one row per income bin: the annual fixed-charge increase as a
share of that bin's midpoint income (`increase_as_pct_of_income`) — lower-income bins carry a
larger share, which is the regressivity story.

### From `ee run trends`

Compares completed per-year runs listed in `pipelines.trends.runs`. Each year must have
its own finished run (matching PUMS, ACS, and TIGER vintages) in its own output
directory. Dollar values stay in each survey year's own dollars, marked by the
`dollar_basis` column (`nominal_survey_year`).

**`trends_affordability_gap.csv`** — the per-year `affordability_gap.csv` rows stacked
with a `year` column: one row per (`year`, `segment`, `population`, `threshold`) with the
gap metrics and their `*_moe90`.

**`trends_energy_burden.csv`** — one row per (`year`, `segment`): `energy_burdened_households`
(+ `_moe90`) from the allocation totals and `energy_burden_rate` (+ `_moe90`) from the
allocation rates, plus the `service` label.

Both trend tables carry `electric_calibration` and `gas_calibration` columns recording
each year's bill-normalization basis (`calibrated`, `diagnostic`, or `uncalibrated`).
Within one comparison every year must share the same basis per fuel; the pipeline warns
loudly when they are mixed, because a level correction applied to only some years
manufactures a fake trend.

**`trends_deltas.csv`** — year-over-year changes: one row per (`table`, identity columns,
`metric`, `year_from`, `year_to`) with `value_from`, `value_to`, `delta`, and `delta_moe90`.
The change MOE is the root sum of squares of the two years' MOEs, which is valid for
independent 1-year PUMS samples; it is NaN when either year's MOE is missing.

---

## Figures

Written to `output_dir/figures/*.png` by `ee run reporting` (and therefore `ee run all`).
Figures require the `viz` extra (`uv sync --extra viz`); without it the run completes and
logs that figures were skipped. Each figure is also skipped (with a warning) if its source
table is absent — so run the upstream pipelines (or `ee run all`) first. The set is
controlled by `pipelines.reporting.figures` in the config (default: all).

| File | Shows | Built from | Config name |
|---|---|---|---|
| `income_comparison.png` | Service-area vs statewide household-income distribution (grouped bars). | `income_distribution_service_vs_state.csv` | `income_comparison` |
| `regressivity_curve.png` | Fixed-charge increase as a % of income across income bins. | `regressivity_table.csv` | `regressivity_curve` |
| `scenario_sweep.png` | Newly (highly) energy-burdened households vs the monthly increase. | `scenario_sweep.csv` | `scenario_sweep` |
| `energy_burden_waterfall.png` | Baseline → newly burdened → post-increase burdened households. | `fixed_charge_headline_summary.csv` | `waterfall` |
| `demographics_<dim>.png` | Current vs proposed eligible households by race/age/ethnicity/tenure. | `demographics_<dim>.csv` | `demographic_bars` |
| `burden_bands.png` | ≤80% AMI households by burden band. | `low_income_burden_bands.csv` | `burden_bands` |
| `energy_burden_bands.png` | All service-area households by burden band. | `energy_burden_bands.csv` | `burden_bands` |
| `choropleth_current_eligible.png` | Current-eligible households by PUMA (map). | `puma_summary.csv` + TIGER PUMA layer | `choropleth` |
| `choropleth_newly_added.png` | Newly-added eligible households by PUMA (map). | `puma_summary.csv` + TIGER PUMA layer | `choropleth` |

`ee run trends` writes its own figures (config `pipelines.trends.figures`, default all).
Lines carry 90% MOE bands as shaded regions; years with a missing MOE keep their point
but get no band.

| File | Shows | Built from | Config name |
|---|---|---|---|
| `trend_affordability_gap.png` | Total affordability gap per year, All households vs ≤80% AMI. | `trends_affordability_gap.csv` | `gap_trend` |
| `trend_households_in_gap.png` | Households above the burden threshold per year. | `trends_affordability_gap.csv` | `households_in_gap_trend` |
| `trend_energy_burden_rate.png` | Energy burden rate in the territory per year. | `trends_energy_burden.csv` | `burden_rate_trend` |

Choropleths use plotly's tile-free `px.choropleth` (no Mapbox token required); PUMAs outside
the service area render empty.
