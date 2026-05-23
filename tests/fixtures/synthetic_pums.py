"""Generate synthetic PUMS housing + person fixtures for integration tests.

The fixtures are tiny (~100 households) and intentionally not statistically meaningful;
they exist to verify pipeline plumbing, schema, and arithmetic — not to reproduce real
ACS distributions. For parity testing against real notebook outputs, set
EE_PARITY_DATA to point at a directory of real /data/eoc CSVs.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from energy_equity.io.pums import replicate_cols


def synthesize_pums_housing(
    *,
    n_households: int = 100,
    pumas: tuple[str, ...] = ("00800", "00900"),
    seed: int = 42,
    replicate_count: int = 80,
) -> pd.DataFrame:
    """Synthesize a PUMS housing CSV table with all columns the pipelines require."""
    rng = np.random.default_rng(seed=seed)
    n = n_households

    df = pd.DataFrame(
        {
            "SERIALNO": [f"2024GQ{i:07d}" for i in range(n)],
            "PUMA": rng.choice(pumas, size=n),
            "WGTP": rng.integers(low=5, high=80, size=n).astype(float),
            "NP": rng.integers(low=1, high=6, size=n),
            # Income drawn from a roughly log-normal distribution centered ~$70k.
            "HINCP": np.round(rng.lognormal(mean=np.log(70_000), sigma=0.7, size=n)),
            "ADJINC": np.full(n, 1_000_000, dtype=int),
            "ADJHSG": np.full(n, 1_000_000, dtype=int),
            "ELEP": rng.integers(low=50, high=300, size=n).astype(float),
            "GASP": rng.integers(low=0, high=150, size=n).astype(float),
            "FULP": rng.integers(low=0, high=500, size=n).astype(float),
            "HHL": rng.choice([1, 2, 3, 4, 5], size=n, p=[0.6, 0.2, 0.1, 0.05, 0.05]),
            "TEN": rng.choice([1, 2, 3, 4], size=n, p=[0.4, 0.15, 0.4, 0.05]),
            "GRNTP": rng.integers(low=500, high=2500, size=n).astype(float),
            "GRPIP": rng.integers(low=10, high=70, size=n).astype(float),
            "SMOCP": rng.integers(low=0, high=2500, size=n).astype(float),
        }
    )
    df.loc[df["TEN"].isin([1, 2]), "GRNTP"] = 0.0  # owners have no rent

    # Replicate weights: jittered around the point weight to make MOEs non-zero.
    for col in replicate_cols("WGTP", replicate_count):
        noise = rng.normal(loc=0.0, scale=0.1, size=n)
        df[col] = np.maximum(0.0, df["WGTP"] * (1.0 + noise)).round(0)
    return df


def synthesize_pums_person(housing: pd.DataFrame, *, seed: int = 99) -> pd.DataFrame:
    """One person row per household, RELSHIPP=20 (householder)."""
    rng = np.random.default_rng(seed=seed)
    n = len(housing)
    return pd.DataFrame(
        {
            "SERIALNO": housing["SERIALNO"].astype(str),
            "RELSHIPP": np.full(n, 20, dtype=int),
            "AGEP": rng.integers(low=18, high=85, size=n),
            "SEX": rng.integers(low=1, high=3, size=n),
            "RAC1P": rng.choice([1, 2, 6, 8, 9], size=n, p=[0.7, 0.1, 0.05, 0.05, 0.1]),
            "HISP": rng.choice([1, 2], size=n, p=[0.8, 0.2]),
            "LANP": rng.choice([0, 625, 1200, 2050], size=n, p=[0.6, 0.2, 0.1, 0.1]),
        }
    )


def write_pums_zips(
    tmp_dir: Path,
    *,
    housing: pd.DataFrame,
    person: pd.DataFrame,
) -> tuple[Path, Path]:
    """Write the housing and person DataFrames to PUMS-style ZIPs and return their paths."""
    housing_zip = tmp_dir / "csv_hco.zip"
    person_zip = tmp_dir / "csv_pco.zip"
    with zipfile.ZipFile(housing_zip, "w") as zf:
        zf.writestr("psam_h08.csv", housing.to_csv(index=False))
    with zipfile.ZipFile(person_zip, "w") as zf:
        zf.writestr("psam_p08.csv", person.to_csv(index=False))
    return housing_zip, person_zip


def synthesize_ami_csv(tmp_dir: Path) -> Path:
    """A minimal county-by-hh_size AMI table covering Pueblo and El Paso counties (CO)."""
    sizes = [1, 2, 3, 4, 5, 6, 7, 8]
    df = pd.DataFrame(
        {"County": ["Pueblo", "El Paso", "Denver"]}
        | {str(s): [40000 + s * 8000, 50000 + s * 8000, 60000 + s * 8000] for s in sizes}
    )
    path = tmp_dir / "ami.csv"
    df.to_csv(path, index=False)
    return path


def synthesize_ami80_by_puma(pumas: tuple[str, ...] = ("00800", "00900")) -> pd.DataFrame:
    """Pre-built PUMA-level AMI80 table (skips the bridge build)."""
    rows = []
    for puma in pumas:
        for size in range(1, 9):
            rows.append(
                {
                    "PUMA": puma,
                    "hh_size": size,
                    "AMI80": 45000 + size * 8000 + (1000 if puma == "00900" else 0),
                }
            )
    return pd.DataFrame(rows)
