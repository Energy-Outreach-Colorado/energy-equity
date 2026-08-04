# EIA-176 residential natural gas sales, revenue, and customer counts

`eia176_residential.csv` holds residential-class natural gas sales revenue, volume, and
customer counts per company, state, and data year, derived from EIA Form 176 company
level data served by the EIA Natural Gas Query System (NGQS). It is the packaged
default for the optional `calibration.gas` bill normalization (see
`docs/bill_normalization.md`); `data_sources.eia176_csv` overrides it.

## Schema

| column                     | type  | notes                                              |
| -------------------------- | ----- | -------------------------------------------------- |
| `company_id`               | int   | EIA-176 company identifier (no relation to EIA-861 utility numbers) |
| `company_name`             | str   | company name as reported to EIA (often abbreviated) |
| `state`                    | str   | two-letter state; multi-state companies have one row per state |
| `customer_class`           | str   | always `residential`                               |
| `revenue_thousand_dollars` | float | residential sales revenue, thousands of USD        |
| `volume_mcf`               | float | residential sales volume, thousand cubic feet      |
| `customers`                | int   | residential sales customer (meter) count           |
| `ownership`                | str   | ownership category from the EIA-176 flags          |
| `year`                     | int   | data year                                          |

Average annual residential gas bill = `revenue_thousand_dollars * 1000 / customers`.

## Derivation

Rebuild (or extend with new years) via:

```sh
curl -sL "https://www.eia.gov/naturalgas/ngqs/data/report/RP4/data/<START>/<END>/ICA/All" -o rp4.json
uv run python scripts/build_eia176_residential_csv.py rp4.json
```

Rules applied by the builder:

- **Sales only.** Revenue, volume, and customers come from the residential *sales*
  fields. Transport customers buy their gas from a third party, so the distributor's
  transported revenue excludes the commodity cost; including them would understate
  average bills. This mirrors the bundled versus delivery treatment in the EIA-861
  electric data.
- Field letters are resolved from the RP4 response's own `columns` descriptor, not
  hardcoded.
- National "Total of All Companies" rows and rows with zero residential sales
  customers are dropped.

## Source

EIA Form 176, company level annual data, via the NGQS JSON API
(<https://www.eia.gov/naturalgas/ngqs/>). Retrieval details are in `provenance.csv`.
US federal data, public domain. Note that EIA-176 company names are often abbreviated
("PUB SERVICE CO OF COLORADO") and company identifiers are unrelated to EIA-861
utility numbers, so gas and electric calibration are configured independently.

`tests/unit/test_eia176_data_integrity.py` validates the schema, key uniqueness, and
that derived average bills stay within a plausible band.
