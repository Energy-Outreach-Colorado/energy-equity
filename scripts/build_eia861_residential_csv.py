"""Build the packaged EIA-861 residential CSV from Sales_Ult_Cust workbooks.

Usage:
    uv run --with openpyxl python scripts/build_eia861_residential_csv.py \
        Sales_Ult_Cust_2023.xlsx Sales_Ult_Cust_2024.xlsx

Downloads are at https://www.eia.gov/electricity/data/eia861/ (current year zips) and
https://www.eia.gov/electricity/data/eia861/archive/zip/f861YYYY.zip (prior years);
each f861YYYY.zip contains Sales_Ult_Cust_YYYY.xlsx.

Aggregation follows the counting rule stated in the workbook's footer: revenue sums
Parts A, B, C and D, while sales and customers sum Parts A, B and D only (Part C is
delivery service, whose customers would double-count the Part B energy-service rows).
Withheld rows (utility 88888), state adjustment rows (utility 99999), and
Behind-the-Meter ownership rows (third-party solar owners, per the same footer) are
excluded. Utility-state aggregates with no customers are dropped.

The output overwrites src/energy_equity/data/eia861/eia861_residential.csv; update
provenance.csv alongside it. tests/unit/test_eia861_data_integrity.py must still pass.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

RAW_COLUMNS = [
    "year",
    "utility_number",
    "utility_name",
    "part",
    "service_type",
    "data_type",
    "state",
    "ownership",
    "ba_code",
    "res_rev",
    "res_sales",
    "res_customers",
]

EXCLUDED_UTILITY_NUMBERS = (88888, 99999)
CUSTOMER_PARTS = ("A", "B", "D")
GROUP_KEYS = ["year", "utility_number", "state"]

OUTPUT_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "energy_equity"
    / "data"
    / "eia861"
    / "eia861_residential.csv"
)


def extract_residential(xlsx_path: str | Path) -> pd.DataFrame:
    df = pd.read_excel(xlsx_path, header=None, skiprows=3, usecols=range(12), names=RAW_COLUMNS)
    df["utility_number"] = pd.to_numeric(df["utility_number"], errors="coerce")
    df = df[df["utility_number"].notna()]
    df = df[~df["utility_number"].isin(EXCLUDED_UTILITY_NUMBERS)]
    df = df[df["ownership"].astype(str).str.strip().str.lower() != "behind the meter"]
    for col in ("res_rev", "res_sales", "res_customers"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    revenue = df.groupby(GROUP_KEYS, as_index=False).agg(
        utility_name=("utility_name", "first"),
        ownership=("ownership", "first"),
        revenue_thousand_dollars=("res_rev", "sum"),
    )
    counted = (
        df[df["part"].isin(CUSTOMER_PARTS)]
        .groupby(GROUP_KEYS, as_index=False)
        .agg(sales_mwh=("res_sales", "sum"), customers=("res_customers", "sum"))
    )
    out = revenue.merge(counted, on=GROUP_KEYS, how="left")
    out[["sales_mwh", "customers"]] = out[["sales_mwh", "customers"]].fillna(0.0)
    out = out[out["customers"] > 0].copy()

    out["customer_class"] = "residential"
    out["utility_number"] = out["utility_number"].astype(int)
    out["year"] = out["year"].astype(int)
    out["customers"] = out["customers"].astype(int)
    out["revenue_thousand_dollars"] = out["revenue_thousand_dollars"].round(1)
    out["sales_mwh"] = out["sales_mwh"].round(1)
    return out[
        [
            "utility_number",
            "utility_name",
            "state",
            "customer_class",
            "revenue_thousand_dollars",
            "sales_mwh",
            "customers",
            "ownership",
            "year",
        ]
    ]


def main(paths: list[str]) -> None:
    if not paths:
        raise SystemExit("Pass one or more Sales_Ult_Cust_YYYY.xlsx paths.")
    frames = [extract_residential(p) for p in paths]
    combined = (
        pd.concat(frames, ignore_index=True)
        .sort_values(["year", "state", "utility_number"])
        .reset_index(drop=True)
    )
    dupes = combined.duplicated(subset=GROUP_KEYS).sum()
    if dupes:
        raise SystemExit(f"{dupes} duplicate (year, utility_number, state) rows; aborting.")
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(OUTPUT_PATH, index=False)
    summary = combined.groupby("year").agg(
        rows=("utility_number", "size"),
        customers=("customers", "sum"),
        revenue_thousand_dollars=("revenue_thousand_dollars", "sum"),
    )
    summary["avg_annual_bill"] = (
        summary["revenue_thousand_dollars"] * 1000 / summary["customers"]
    ).round(2)
    print(f"wrote {OUTPUT_PATH}")
    print(summary.to_string())


if __name__ == "__main__":
    main(sys.argv[1:])
