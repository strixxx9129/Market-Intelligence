"""Distribution-shift statistics: PSI (primary) and the two-sample KS test (second opinion).

PSI, the Population Stability Index:
1. Cut the REFERENCE (training) data into bins: here 10 quantile bins, so each holds ~10% of
   the training rows. The edges are fixed from the training data and never move.
2. Measure what share of the reference (expected, e) and of the recent data (actual, a) falls
   in each bin.
3. PSI = Σ (a - e) · ln(a / e)

Reading it (common rule of thumb):
    < 0.10        no meaningful shift
    0.10 - 0.25   moderate shift: keep an eye on it
    > 0.25        major shift: the model now sees inputs unlike what it learned from
It's symmetric-ish and additive over bins, so `details` shows WHICH bins moved (e.g. "70% of
recent days fall in the lowest-volatility decile, versus 10% in training").

KS (Kolmogorov-Smirnov) compares the two empirical CDFs and returns a p-value. It's a useful
second opinion but over-sensitive with big samples (tiny shifts become "significant") and weak
with small ones (30 recent days), which is why PSI decides and KS is recorded alongside.
"""

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import stats

EPS = 1e-4  # replaces empty-bin shares: ln(0) is undefined


@dataclass(frozen=True)
class PsiResult:
    value: float
    edges: list[float]  # inner bin edges (outer bins are open-ended: -inf / +inf)
    expected: list[float]  # reference share per bin
    actual: list[float]  # recent share per bin
    n_reference: int
    n_current: int

    @property
    def noise_floor(self) -> float:
        return null_psi(len(self.expected), self.n_reference, self.n_current)

    def details(self) -> dict[str, Any]:
        return {
            "edges": [round(e, 8) for e in self.edges],
            "expected": [round(x, 4) for x in self.expected],
            "actual": [round(x, 4) for x in self.actual],
            "n_reference": self.n_reference,
            "n_current": self.n_current,
            "noise_floor": round(self.noise_floor, 4),
        }


def bins_for(n_current: int, max_bins: int = 10) -> int:
    """About 6 recent observations per bin: 30 days -> 5 bins, 60+ days -> 10."""
    return int(np.clip(n_current // 6, 3, max_bins))


def null_psi(bins: int, n_reference: int, n_current: int) -> float:
    """PSI you expect from sampling noise alone when NOTHING changed:
    about (bins - 1) * (1/n_ref + 1/n_cur). With 10 bins and 30 recent days that is ~0.3,
    already above the textbook 0.25 alert line (those thresholds assume thousands of rows).
    Fewer bins and the KS agreement rule keep small windows from crying wolf."""
    return (bins - 1) * (1 / max(n_reference, 1) + 1 / max(n_current, 1))


def psi_critical(bins: int, n_reference: int, n_current: int, alpha: float) -> float:
    """PSI that sampling noise exceeds only with probability `alpha`.

    Under "no change", PSI * n_ref * n_cur / (n_ref + n_cur) is approximately chi-squared with
    (bins - 1) degrees of freedom, so this turns PSI into a significance test. It catches
    changes in SPREAD that KS misses on small samples (KS is most sensitive near the median).
    """
    scale = 1 / max(n_reference, 1) + 1 / max(n_current, 1)
    return float(stats.chi2.ppf(1 - alpha, max(bins - 1, 1)) * scale)


def psi_from_shares(expected: np.ndarray, actual: np.ndarray) -> float:
    e = np.clip(np.asarray(expected, float), EPS, None)
    a = np.clip(np.asarray(actual, float), EPS, None)
    return float(np.sum((a - e) * np.log(a / e)))


def quantile_edges(reference: np.ndarray, bins: int = 10) -> np.ndarray:
    """Inner edges at the reference's 10%, 20%, ..., 90% quantiles (duplicates removed, so a
    feature with many identical values gets fewer, wider bins)."""
    qs = np.quantile(reference, np.linspace(0, 1, bins + 1)[1:-1])
    return np.unique(qs)


def shares(values: np.ndarray, inner_edges: np.ndarray) -> np.ndarray:
    idx = np.searchsorted(inner_edges, values, side="right")  # bin i: edge[i-1] <= x < edge[i]
    counts = np.bincount(idx, minlength=len(inner_edges) + 1)
    return counts / max(len(values), 1)


def psi(reference: np.ndarray, current: np.ndarray, bins: int = 10) -> PsiResult | None:
    """PSI with bins fixed from `reference`. None when the reference has no spread (a constant
    can't define bins, e.g. a baseline model's near-constant P(up))."""
    ref = np.asarray(reference, float)
    cur = np.asarray(current, float)
    ref, cur = ref[np.isfinite(ref)], cur[np.isfinite(cur)]
    if len(ref) == 0 or len(cur) == 0 or np.ptp(ref) == 0:
        return None
    edges = quantile_edges(ref, bins)
    e, a = shares(ref, edges), shares(cur, edges)
    return PsiResult(
        psi_from_shares(e, a), edges.tolist(), e.tolist(), a.tolist(), len(ref), len(cur)
    )


def psi_categorical(reference: np.ndarray, current: np.ndarray) -> PsiResult | None:
    """PSI over the categories seen in the reference (e.g. day_of_week 0..6)."""
    ref = np.asarray(reference)
    cur = np.asarray(current)
    if len(ref) == 0 or len(cur) == 0:
        return None
    cats = np.unique(ref)
    e = np.array([np.mean(ref == c) for c in cats])
    a = np.array([np.mean(cur == c) for c in cats])
    return PsiResult(
        psi_from_shares(e, a),
        cats.astype(float).tolist(),
        e.tolist(),
        a.tolist(),
        len(ref),
        len(cur),
    )


def ks_test(reference: np.ndarray, current: np.ndarray) -> tuple[float, float] | None:
    """(KS statistic, p-value), or None if either sample is empty."""
    ref = np.asarray(reference, float)
    cur = np.asarray(current, float)
    ref, cur = ref[np.isfinite(ref)], cur[np.isfinite(cur)]
    if len(ref) == 0 or len(cur) == 0:
        return None
    result = stats.ks_2samp(ref, cur)
    return float(result.statistic), float(result.pvalue)
