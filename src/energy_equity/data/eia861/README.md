# EIA-861 residential sales, revenue, and customer counts

`eia861_residential.csv` holds residential-class electricity revenue, sales, and customer
counts per utility, state, and data year, derived from the EIA Form 861 "Sales to
Ultimate Customers" workbooks. It is the packaged default for the optional
`calibration.electric` EIA-861 bill normalization (see `docs/eia861_normalization.md`);
`data_sources.eia861_csv` overrides it.

## Schema

| column                     | type  | notes                                            |
| -------------------------- | ----- | ------------------------------------------------ |
| `utility_number`           | int   | EIA utility ID                                   |
| `utility_name`             | str   | utility name as reported to EIA                  |
| `state`                    | str   | two-letter state; multi-state utilities have one row per state |
| `customer_class`           | str   | always `residential`                             |
| `revenue_thousand_dollars` | float | residential revenue, thousands of USD            |
| `sales_mwh`                | float | residential sales, MWh                           |
| `customers`                | int   | residential customer (meter) count               |
| `ownership`                | str   | EIA ownership category                           |
| `year`                     | int   | data year                                        |

Average annual residential bill = `revenue_thousand_dollars * 1000 / customers`.

## Derivation

Rebuild (or extend with a new year) via:

```sh
uv run --with openpyxl python scripts/build_eia861_residential_csv.py Sales_Ult_Cust_<YEAR>.xlsx ...
```

Aggregation rules, taken from the workbook's own footer note:

- **Revenue** sums Parts A, B, C, and D per utility-state.
- **Sales and customers** sum Parts A, B, and D only — Part C is delivery service, whose
  customers would double-count the Part B (energy service) rows.
- Withheld rows (utility 88888), state adjustment rows (utility 99999), and
  Behind-the-Meter ownership rows (third-party owners of rooftop solar) are excluded.
- Utility-state aggregates with zero residential customers are dropped.

Consequence of the counting rule: for utilities in retail-choice states with substantial
delivery-only (Part C) business, revenue includes the delivery portion while the customer
count covers bundled/energy customers only, so the derived average bill can be distorted.
For vertically integrated utilities (single Bundled row) the figure is exact. The
customer-sited supplement (`Sales_Ult_Cust_CS`) and the small-utility Short Form file are
not included.

## Source

EIA Form 861, annual final releases, published at
<https://www.eia.gov/electricity/data/eia861/> (prior years under `archive/zip/`).
Per-year workbook and release details are in `provenance.csv`. EIA revises data between
early and final releases; only final releases are packaged. US federal data, public
domain.

`tests/unit/test_eia861_data_integrity.py` validates the schema, key uniqueness, and
that derived average bills stay within a plausible band.
