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

For households larger than the highest `hh_size` row published for a state, callers should
extrapolate using the LIHEAP IM "additional person" increment (also published per state-year).
Future versions of this file will add an `addl_person` column.

## Source

The values come from the annual HHS LIHEAP Information Memorandum, "State Median Income
Estimates for a Four-Person Family — Notice of the Federal Fiscal Year (FFY) [YEAR] State
Median Income Estimates for Use Under the Low Income Home Energy Assistance Program."

Each row in this CSV cites the IM number, publication date, and a public URL in
`provenance.csv` (sibling file). When transcribing values from new IMs, update both files in
the same commit and add the IM PDF citation.

## Initial coverage (v0.1)

Colorado FY 2025, household sizes 1–10. Other states + years welcome — open a PR with the
new rows plus the matching provenance entry.
