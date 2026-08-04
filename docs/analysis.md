# Analysis methodology

This document describes the analysis that the `energy-equity` package performs: what
questions it answers, the data it draws on, the statistics it computes, and — in detail —
how it quantifies the uncertainty (margins of error) on every estimate.

It is written for analysts and reviewers who need to understand what the numbers mean and
how far to trust them, not for users of the CLI. For how to *run* the analysis, see
[`cli.md`](cli.md); for the exact files each step writes, see [`outputs.md`](outputs.md).

---

## 1. What the analysis measures

The package quantifies **household energy burden, utility affordability, and rate-impact
scenarios** for any U.S. utility service territory, using American Community Survey (ACS)
Public Use Microdata Sample (PUMS) records as the underlying population.

The core analytical questions are:

1. **Energy burden** — what share of households spend a high fraction of their income on
   home energy (electricity, gas, and other heating fuels)? Burdened households are those
   above a configurable threshold (default ≥ 6% of income); *highly* burdened are above a
   second threshold (default ≥ 10%).
2. **Affordability eligibility** — how many households fall under income-based program
   thresholds: ≤ 80% of Area Median Income (HUD AMI limits) and ≤ 60% of State Median
   Income (the LIHEAP eligibility ceiling)? And among those, how many are energy-burdened?
3. **Rent burden** — among renters, what share pay ≥ 30% (burdened) or ≥ 50% (severely
   burdened) of income in gross rent? This contextualizes energy cost against total
   housing cost.
4. **Service-area allocation** — what do all of the above look like *for the households
   inside a particular utility's service territory*, rather than for a whole state or PUMA?
5. **Rate-impact (fixed-charge) scenarios** — if a utility raises its fixed monthly charge
   by $X, how many additional households cross into energy burden, and how is that increase
   distributed across the income spectrum (the regressivity question)?
6. **Equity breakdowns** — every metric above can be cut by race, ethnicity, gender, age,
   tenure, and household language, to surface disparate impacts.

All estimates are produced **with a 90% margin of error** (see §6), and the analysis is
**state-agnostic**: the only state-specific inputs are the ACS vintage, a HUD AMI table,
and the (packaged) LIHEAP SMI thresholds.

---

## 2. Data sources

| Source | Role |
|---|---|
| **ACS PUMS** (household records + 80 replicate weights) | The population. One row per surveyed household, with a point weight `WGTP` and replicate weights `WGTP1..WGTP80`. |
| **ACS B11001 / B19001** (tract-level tables) | Household counts per census tract (for service-area allocation) and the household income distribution (for the reporting/regressivity analysis). |
| **TIGER/Line shapefiles** | PUMA polygons, census-tract polygons, and urbanized-area polygons for the geospatial allocation. |
| **HUD Area Median Income (AMI) limits** | County-level 80% AMI thresholds, bridged to PUMAs. |
| **LIHEAP State Median Income (SMI) thresholds** | 60% SMI eligibility ceilings by state, household size, and fiscal year; packaged in `data/smi/` (see that directory's README). |
| **The service-area shapefile** | The utility territory polygon the user supplies. |

---

## 3. The analytical pipeline

The analysis runs as a strict dependency chain of five pipelines:

```
prepare_household_microdata
        │
        ▼
   puma_table ──────────────┐
        │                   │
        ▼                   ▼
 service_allocation   (reporting: B19001 + shapes)
        │
        ├──► eligibility_analysis
        └──► fixed_charge
```

### 3.1 Household preparation

`prepare_household_microdata` loads PUMS households and attaches every per-household
quantity the rest of the analysis needs:

- **Inflation adjustment.** Raw PUMS dollar fields are coded; income (`HINCP`) is scaled by
  `ADJINC` and housing costs by `ADJHSG` (both stored as integers ÷ 1,000,000) to put all
  dollars on a common year basis → `income_adjusted`.
- **Annual energy cost.** Monthly electricity (`ELEP`) and gas (`GASP`) are annualized
  (× 12) and added to annual other-fuel cost (`FULP`) → `energy_cost_annual`. ACS
  "special codes" (0–3, meaning *not used* / *included in rent* / etc.) are resolved first.
- **Optional bill normalization.** When `calibration.electric` or `calibration.gas` is
  configured, the service-weighted average annual bill implied by PUMS for that fuel
  (among households that pay for it) is compared to the administrative average from
  EIA-861 (electric, per utility) or EIA-176 (gas, per company); with `apply: true`
  each paying household's cost is rescaled by `target / observed` before burden is
  computed, anchoring the cost level to utility-reported data while preserving the
  PUMS distribution (the DOE LEAD approach). See
  [bill_normalization.md](bill_normalization.md).
- **Energy burden** = `energy_cost_annual / income_adjusted`, with flags at the 6% and 10%
  thresholds. Households with non-positive income are excluded by default (NaN burden).
- **Rent burden** = annualized gross rent (`GRNTP` × 12) / income, flagged at 30% / 50%,
  restricted to renters (`TEN` ∈ {3, 4}).
- **Eligibility thresholds**: a ≤ 80% AMI flag (county→PUMA bridged) and a ≤ 60% SMI flag
  (looked up by state, household size, and fiscal year).

### 3.2 PUMA and state tables (`puma_table`)

`tables.core.build_table` is the workhorse. Given the household frame and a set of
**count metrics** (each a boolean mask, e.g. "energy-burdened") and **ratio metrics** (a
numerator metric ÷ a denominator metric, e.g. "burdened among ≤ 80% AMI"), it produces, per
group (statewide, or per PUMA, or per PUMA × demographic):

1. **Point estimate** — multiply each mask by `WGTP`, group, and sum (a weighted count).
2. **Rate** — numerator count ÷ denominator count.
3. **90% MOE** — repeat step 1 with each of the 80 replicate weights and apply the SDR
   formula (§6).
4. **Small-N suppression** — rows with fewer than `min_unweighted_n` (default 30) unweighted
   sample records get a `small_n_flag`; their *rates* are blanked (counts are kept).

This pipeline also emits the per-PUMA **replicate matrices** so that downstream service-area
totals can propagate uncertainty correctly rather than re-deriving it (see §6.2).

### 3.3 Service-area allocation (`service_allocation`)

This is the geospatial heart of the analysis. PUMS records do not carry a finer geography
than the PUMA, but utility service areas rarely align with PUMA boundaries. The allocator
estimates the fraction of each PUMA's households that fall inside the service polygon:

1. **Assign tracts to PUMAs** by a representative-point spatial join (EPSG:4326).
2. **Intersect each tract with the service polygon** in an equal-area projection (EPSG:5070)
   to get the *area fraction* of the tract inside the service area.
3. **Weight that fraction by the tract's household count** (ACS B11001) to estimate the
   households each tract contributes.
4. **Aggregate to the PUMA** → `share_households_in_service` (and, if urbanized-area shapes
   are supplied, `urban_share_within_service`).
5. **Allocate PUMA metrics** by multiplying each PUMA estimate by its share.

These shares become three per-household weight columns — `w_service`, `w_service_urban`,
`w_service_rural` — and every service-area aggregate downstream is just a weighted sum over
them. When no urban data is supplied, the urban/rural split is `NaN` (not zero), and those
segment rows are deliberately omitted rather than defaulted to rural.

### 3.4 Eligibility analysis & fixed-charge scenarios

- **`eligibility_analysis`** compares household burden against current vs. proposed program
  thresholds within the ≤ 80% AMI (or program-defined) low-income pool, reporting how many
  households are currently eligible, would be newly added, and the percentage increase —
  overall, by segment, and by demographic dimension.
- **`fixed_charge`** adds a scenario monthly charge to each household's energy cost,
  recomputes burden, and counts the *newly* (and newly *highly*) energy-burdened households.
  It sweeps a range of dollar increases and produces a delta-sensitivity table (how many
  households see their burden rise by at least N percentage points).

### 3.4b Affordability-gap sizing

Alongside the eligibility counts, the pipeline sizes the **affordability gap**: for each
household with positive income and a non-missing energy cost,

```
gap(t) = max(0, annual_energy_cost − t × annual_income)
```

is the annual dollar reduction that would bring its energy burden down to threshold `t`.
The service-weighted sum of `gap(t)` is the total program cost of a
percentage-of-income-style intervention at that threshold, and the weighted count with
`gap(t) > 0` is the household universe it would serve. Both carry replicate-based 90%
MOEs (the gap is a fixed per-household dollar amount, so each replicate estimate is a
re-weighted sum — the same SDR machinery as every other count).

Interpretation notes: households with non-positive income are excluded (no meaningful
payment target exists), including under `nonpos_income_rule: treat_as_high`; and because
gaps are denominated in self-reported dollars, they inherit the cost level bias that
EIA-861 normalization measures and corrects — run the calibration before publishing gap
totals ([bill_normalization.md](bill_normalization.md)).

### 3.4c Multi-year trends

A single run describes one PUMS survey year. The `trends` pipeline compares several
completed runs, one per year, to show whether burden and the affordability gap are
improving or worsening in a territory. It stacks each year's gap and burden outputs
into long tables, computes year-over-year changes, and draws trend lines with shaded
90% MOE bands.

Comparability rules the pipeline and its outputs follow:

- **Change MOEs.** The margin of error of a year-over-year change is the root sum of
  squares of the two years' MOEs. This is valid because 1-year PUMS samples are drawn
  independently. Overlapping 5-year samples must not be compared this way; configure
  the pipeline with 1-year runs only.
- **Dollars stay nominal.** Costs and incomes are inflation-adjusted to each survey's
  own year, so dollar metrics carry a `dollar_basis` column set to
  `nominal_survey_year` and figure axes say so. Burden and gap *rates* are ratios and
  comparable across years as they stand.
- **Hold thresholds constant.** Use the same HUD AMI fiscal year and SMI vintage for
  every year in the comparison so eligibility populations do not drift for program
  reasons rather than economic ones. Disclose the held-constant vintage.
- **Calibrate all years or none.** Bill normalization
  ([bill_normalization.md](bill_normalization.md)) is a level correction; applying it
  to some years but not others manufactures a fake trend out of the correction itself.
  The trend tables record each year's basis in the `electric_calibration` and
  `gas_calibration` columns and the pipeline warns when they are mixed. Self-report
  bias varies by year, so calibrated runs are the preferred basis for trend exhibits.
- **Stay on one PUMA boundary set.** The 2010 to 2020 PUMA boundary change lands
  between the 2021 and 2022 vintages. Years 2022 onward compare cleanly; mixing
  boundary eras keeps each year internally valid but breaks PUMA-level comparisons.

### 3.5 Reporting & regressivity

`reporting` joins the B19001 income distribution for the service area against the statewide
distribution to show how the service-area population skews by income, and computes the
**regressivity table**: the annual fixed-charge increase as a share of each income bin's
midpoint income. A flat dollar charge is a larger share of a low income than a high one;
this table is the regressivity story. The pipeline also renders the figures (choropleths,
waterfall, regressivity curve, etc.) when the `viz` extra is installed.

---

## 4. Key statistical conventions

- **Weighting.** Every count is a weighted sum of `WGTP` (or a service-share-adjusted
  weight); the package never counts raw records as population.
- **Weighted descriptive statistics.** Means, totals, and quantiles are weighted.
  Quantiles use the "Type 7" (R/NumPy default) definition generalized to weights:
  positions placed at *cumulative weight − ½ the row's weight*, then linearly interpolated.
- **Non-positive income.** Households with income ≤ 0 are excluded from burden by default
  (their burden is undefined), rather than being recorded as infinitely burdened. An
  alternative "treat as highly burdened" rule is available.
- **Missing energy cost.** With the default rule, `NaN` electric/gas/fuel costs are treated
  as $0 (see the limitation in §7).
- **Small-N suppression.** Rates computed from fewer than 30 unweighted records are
  suppressed, because both the estimate and its MOE are unreliable at that sample size.

---

## 5. Error analysis — overview

Every published estimate is a *sample* estimate from the ACS, so every estimate carries
sampling error. The package quantifies this with the Census Bureau's own variance method
for PUMS — the **Successive Difference Replicate (SDR)** estimator — and reports a **90%
margin of error (MOE)** alongside each count and rate. This section describes that
machinery in full; the implementation lives in `weights/sdr.py`,
`weights/stats.py`, `tables/core.py`, and `geo/allocation.py`.

### 5.1 Why replicate weights

The ACS does not publish a simple variance for PUMS estimates, because the sample design
(stratification, clustering, raking) makes the naive binomial/simple-random-sample variance
wrong. Instead, the Census Bureau ships **80 replicate weights** (`WGTP1 … WGTP80`) with
each PUMS record. Each replicate is a re-weighting of the sample that perturbs the design in
a controlled way. By recomputing any estimate under all 80 replicate weightings and
measuring how much the answer moves, we recover a design-consistent variance — *without*
needing to know the design details ourselves.

This is why the package threads replicate weights through the entire pipeline: variance is
computed from how an estimate behaves across the 80 alternative weightings, not from a
closed-form formula on the point estimate.

### 5.2 The SDR variance formula

For any estimate `X` (a weighted count, a total, or a rate), let `X₀` be the **point
estimate** computed with the full-sample weight `WGTP`, and let `Xᵣ` (for `r = 1..80`) be
the same estimate recomputed with replicate weight `WGTPr`. The SDR variance is:

```
Var(X) = (4 / 80) · Σ_{r=1}^{80} (Xᵣ − X₀)²
```

The leading factor `4/80` is fixed by the ACS successive-difference replicate design (the
"4" is the design factor; "80" is the replicate count). The standard error is
`SE = √Var(X)`.

This is implemented in `sdr_variance`, which is vectorized: it accepts a 1-D array of point
estimates and a 2-D `(N, 80)` array of replicate estimates and returns one variance per row,
so all PUMA/segment/demographic rows are computed in a single array operation. Variance is
clamped at zero before the square root, so a row whose replicates exactly equal the point
estimate yields `SE = 0` rather than a `NaN`.

### 5.3 From variance to a 90% margin of error

The 90% MOE is the standard error scaled by the 90% normal critical value:

```
MOE₉₀ = Z₉₀ · SE = Z₉₀ · √Var(X)
```

The package uses the **full-precision** critical value
`Z₉₀ = 1.6448536269514722` (the inverse standard-normal CDF at 0.95), defined once as
`weights.sdr.Z_90`. The original source notebooks used the rounded `1.645`; the package's
use of the full-precision constant is the *only* reason package MOEs differ from the
notebook MOEs, and the difference is ~0.009%. This is intentional — the package is slightly
more correct. (A user who needs a different confidence level can pass a different `z`; the
formula and code support any multiplier.)

`sdr_moe` (array) and `sdr_moe_scalar` (single estimate) wrap this. The 90% MOE corresponds
to a 90% confidence interval `X₀ ± MOE₉₀`.

### 5.4 MOEs for counts vs. rates

- **Counts** (weighted household totals): the replicate estimate `Xᵣ` is just the weighted
  count under replicate `r`. SDR is applied directly.
- **Rates** (a count ÷ a count, e.g. "% burdened among ≤ 80% AMI"): the rate is recomputed
  *within each replicate* — `rateᵣ = numᵣ / denᵣ` — and the SDR formula is applied to the
  vector of 80 replicate rates. This correctly captures the correlation between numerator
  and denominator (they are computed from the same weights), which a naive
  ratio-of-independent-MOEs propagation would get wrong. `estimate_count_and_rate` in
  `weights/stats.py` returns `count`, `rate`, and the MOE for each, plus the unweighted N.

### 5.5 Worked intuition

If an estimate is stable across the 80 replicate weightings, the squared differences are
small, the variance is small, and the MOE is tight — the estimate is well-supported by the
sample. If small subgroups (a rare demographic in one PUMA) cause the estimate to swing
widely across replicates, the MOE balloons, signalling that the point estimate should not be
read precisely. This is exactly why rates built on fewer than 30 unweighted records are
suppressed (§4): at that size the replicate spread — and thus the MOE — is too large to be
informative.

---

## 6. Error propagation through service-area allocation

The subtlest part of the error analysis is propagating uncertainty when PUMA estimates are
allocated into a service territory (§3.3). The package is deliberate about this.

### 6.1 Why not combine PUMA MOEs by root-sum-of-squares (RSS)?

A service area aggregates several PUMAs. The tempting shortcut is to take each PUMA's
published MOE and combine them by RSS (`√Σ MOEᵢ²`), which assumes the PUMA estimates are
**independent**. They are not: all PUMAs in a state share the *same* Census sample frame and
the *same* replicate-weight design, so their sampling errors are correlated. RSS would
misstate the service-area MOE.

### 6.2 Replicate propagation (what the package does instead)

Instead, the allocator carries the **full (n_PUMA × 80) replicate matrix** down from
`puma_table` and forms the service-area estimate *within each replicate*:

```
For replicate r:   X_service,r = Σ_PUMA  w_PUMA · X_PUMA,r
```

where `w_PUMA` is the PUMA's allocation weight (its service share, optionally × urban or ×
rural share). This produces 80 replicate estimates of the *service-area total*, to which the
ordinary SDR formula (§5.2–5.3) is then applied. Because every PUMA is re-weighted under the
*same* replicate `r` before summing, the cross-PUMA correlation is preserved automatically —
no independence assumption is made. This is implemented in
`allocate_puma_counts_to_service`, which computes `reps_service = w @ mat` (the weighted sum
across PUMAs, per replicate) and then `sdr_moe` on the result. Service-area **rates** are
handled the same way: the numerator and denominator are each allocated per replicate, the
ratio is taken per replicate, and SDR is applied to the 80 replicate ratios.

> **Do not replace this with RSS.** It is documented in the code and in `docs/development.md` as a
> hard rule, precisely because RSS is the natural-but-wrong shortcut.

### 6.3 Degenerate-rate guard

When allocating rates, a replicate can occasionally produce a zero denominator (an empty
segment in that replicate). The code guards this: replicate ratios with a non-positive
denominator are set to `NaN`, and the MOE is only computed when at least half of the 80
replicates yield a finite ratio (the non-finite ones are then filled with the point
estimate so they contribute zero variance). This prevents a single degenerate replicate from
producing a meaningless MOE.

---

## 7. Known methodological limitations

These are inherited from the source notebooks and preserved for parity; each is a documented
limitation, not a silent bug. They should be disclosed in any methodology appendix that
ships with published numbers.

1. **"Included in rent" energy costs treated as $0.** ACS emits `NaN` for both "no
   charge / not used" *and* "energy included in rent". With the default rule both become
   $0, which **understates burden for renters in master-metered buildings** — they do pay
   for energy, just not directly. A proper fix uses the `FELEP/FGASP/FFULP` allocation
   flags to separate the cases.
2. **AMI/SMI clipping for large households.** Households larger than the maximum published
   household size are clipped to that size's threshold. SMI now ships sizes 1–12, so only
   13+-person households are affected; HUD AMI clipping depends on the supplied table.
   Affected large families use a lower threshold than they should and are under-counted as
   eligible.
3. **Within-PUMA homogeneity.** The allocator assumes the in-service portion of each PUMA
   has the same burden / income / demographic distribution as the whole PUMA. This is the
   standard small-area-estimation compromise; removing it would require sub-PUMA microdata.
4. **Tract area-weighting.** Service-area shares weight tracts by *area* overlap, assuming
   uniform household density within a tract. For irregular service shapes (pipe buffers,
   narrow corridors) the populated portion may not match the area fraction; block-group
   allocation would be tighter.
5. **SMI dollar-vintage gap.** SMI thresholds are HHS's official FFY program limits, applied
   to ADJINC-adjusted income **without further inflation adjustment**. The residual
   survey-year vs. FFY dollar-vintage gap is a documented minor limitation, not a
   correction.
6. **Two AMI methods.** A blended binary ≤ 80% AMI flag (one limit per PUMA) and a
   fractional expected-probability weight (county-share weighted within PUMA) are both
   available; the blended version can misclassify households near the boundary in
   multi-county PUMAs. `cfg.thresholds.ami_method` selects.

> The MOEs in §5–6 capture **sampling error only**. They do not capture the
> *non-sampling* uncertainty introduced by limitations 1–6 (allocation assumptions,
> threshold clipping, cost imputation). A tight MOE means the ACS sample pins the estimate
> well; it does not mean the modeling assumptions are exact.

---

## 8. Where to look in the code

| Concern | Module |
|---|---|
| SDR variance & MOE | `weights/sdr.py` |
| Weighted count / rate / MOE; weighted quantiles | `weights/stats.py` |
| Energy & rent burden math | `burden.py` |
| PUMS income/cost adjustment & scenarios | `pums/energy_cost.py` |
| Table builder (point, rate, MOE, suppression) | `tables/core.py` |
| Metric definitions | `tables/metrics.py` |
| Service-area shares & replicate-propagated MOEs | `geo/allocation.py` |
| Pipelines (orchestration) | `pipelines/*.py` |

For the methodology rationale behind specific choices (e.g. why replicate propagation over
RSS, why the full-precision `Z_90`), see the "Methodology notes" section of
[`development.md`](development.md).
