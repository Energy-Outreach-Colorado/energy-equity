# LIHEAP State Median Income (SMI) thresholds

`liheap_smi_by_state_year.csv` holds 60% SMI thresholds by state, household size, and fiscal
year for the Low Income Home Energy Assistance Program (LIHEAP). These values gate the
≤60% SMI eligibility flag used in the affordability pipelines.

## Schema

| column | type | notes |
|---|---|---|
| `state_fips` | str (2-digit) | e.g. "08" for Colorado, "17" for Illinois |
| `state_abbr` | str | e.g. "CO", "IL" |
| `fy` | int | LIHEAP fiscal year (e.g. 2025) |
| `hh_size` | int | household size, 1..N |
| `smi60_annual` | int | annualized 60% of state median income, USD |

Values are published through household size 12. The per-size figures follow CFR 96.85:
`base60 = floor(0.60 × SMI_4-person)`, then `value(size) = floor(base60 × pct(size))`, with
`pct` = 52/68/84/100/116/132% for sizes 1–6 and +3 points per additional person
(135/138/141/144/147/150% for 7–12). `tests/unit/test_smi_data_integrity.py` re-derives every
row from that formula. For households above the published max, the same +3-points-per-person
rule extends further if needed.

## Source

**All SMI values come from the HHS LIHEAP Information Memoranda, published at**
<https://acf.gov/ocs/policy-guidance/liheap-information-memoranda>.

Each year HHS issues an IM, "Attachment 4 — State Median Income (SMI) by Household Size,"
designated for *optional use* in one federal fiscal year and *mandatory use* the next. This
package labels each fiscal year by the IM that is **mandatory** for it:

| `fy` | Source IM (Attachment 4) | Underlying ACS 5-year vintage |
|---|---|---|
| 2025 | LIHEAP IM2024-02 (optional FY24 / mandatory FY25) | 2018–2022 ACS |
| 2026 | LIHEAP IM2025-02 (optional FY25 / mandatory FY26) | 2019–2023 ACS |
| 2027 | LIHEAP FY2027 Attachment 4 (optional FY26 / mandatory FY27) | 2020–2024 ACS |

These are HHS's official program thresholds — compared to household income **without any
inflation adjustment**. Per-row citations (IM number, publication date, source URL) are in
`provenance.csv` (sibling file). When adding a new fiscal year, find its IM on the index page
above, transcribe Attachment 4's 4-person SMI column, regenerate the per-size values, and add
matching `provenance.csv` rows in the same commit.

## Coverage

All 50 states + DC + Puerto Rico, household sizes 1–12, for FY2025–FY2027. Additional
state-years welcome — open a PR with the new rows plus the matching provenance entries.
