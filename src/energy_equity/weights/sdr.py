"""Successive Difference Replicate (SDR) variance and 90% margins of error.

The Census Bureau publishes 80 replicate weights for each ACS PUMS sample. The variance of
any weighted estimate is computed as::

    Var(X) = (4/80) * sum_{r=1..80} (X_r - X_0)^2

where X_0 is the full-sample estimate and X_r is the estimate produced using replicate r's
weights. The 90% margin of error is `1.645 * sqrt(Var)`.

Functions here are intentionally array-shaped so they can be applied row-wise across many
PUMA/segment estimates at once.
"""

from __future__ import annotations

import numpy as np

PUMS_REPLICATE_COUNT = 80
PUMS_REPLICATE_FACTOR = 4 / PUMS_REPLICATE_COUNT
Z_90 = 1.6448536269514722  # Inverse standard-normal CDF at 0.95.


def sdr_variance(
    point: np.ndarray,
    replicates: np.ndarray,
    *,
    factor: float = PUMS_REPLICATE_FACTOR,
) -> np.ndarray:
    """Compute SDR variance per row.

    Args:
        point: 1-D array of point estimates, length N.
        replicates: 2-D array of replicate estimates, shape (N, R).
        factor: variance factor (4/R for PUMS' R=80).

    Returns:
        1-D array of length N. Returns 0 where the row of replicates is identical to
        the point estimate (yielding a 0 variance) so callers can safely take sqrt.
    """
    point_arr = np.asarray(point, dtype=float)
    rep_arr = np.asarray(replicates, dtype=float)
    if rep_arr.ndim == 1:
        rep_arr = rep_arr[np.newaxis, :]
        point_arr = np.atleast_1d(point_arr)
    if rep_arr.shape[0] != point_arr.shape[0]:
        raise ValueError(
            f"point and replicates must have the same leading dimension; got "
            f"point={point_arr.shape}, replicates={rep_arr.shape}"
        )
    diff = rep_arr - point_arr[:, np.newaxis]
    return factor * (diff * diff).sum(axis=1)


def sdr_moe(
    point: np.ndarray,
    replicates: np.ndarray,
    *,
    factor: float = PUMS_REPLICATE_FACTOR,
    z: float = Z_90,
) -> np.ndarray:
    """SDR 90% margin of error (or any z-scaled MOE) per row."""
    variance = sdr_variance(point, replicates, factor=factor)
    return z * np.sqrt(np.maximum(variance, 0.0))


def sdr_moe_scalar(
    point: float,
    replicates: np.ndarray,
    *,
    factor: float = PUMS_REPLICATE_FACTOR,
    z: float = Z_90,
) -> float:
    """Scalar variant of `sdr_moe` for one estimate with its replicate vector."""
    arr = sdr_moe(
        np.asarray([point], dtype=float),
        np.asarray(replicates, dtype=float)[np.newaxis, :],
        factor=factor,
        z=z,
    )
    return float(arr[0])


def replicate_metric_cols(metric: str, n: int = PUMS_REPLICATE_COUNT) -> list[str]:
    """Generate replicate-suffixed metric column names, e.g. `hh_total_w_rep1..rep80`."""
    return [f"{metric}_rep{i}" for i in range(1, n + 1)]
