"""Walk-forward (time-series) cross-validation with an embargo gap.

    expanding:  [train .............] gap [test]
                [train ......................] gap [test]
    rolling:          [train ........] gap [test]          (fixed-length window slides)

Why not a random split? Neighbouring days are near-copies of each other (rolling features
overlap by 29 of 30 days), so a shuffled split puts "tomorrow's twin" into the training set
and the model is graded on data it has effectively seen. Walk-forward always trains on the
past and tests on the future, exactly as the model will be used.

Why the gap? The label of the last training row t is ln(close[t+1]/close[t]); the feature
`log_return_1d` of row t+1 is the SAME number. Without a gap, the test period's information
sits in the training labels. `gap` rows are dropped between train and test (gap >= 1 needed
for a 1-day target; the default 5 adds margin for serial correlation in rolling features).

Indices are positional (0..n-1) over a frame sorted by time.
"""

from collections.abc import Iterator
from dataclasses import asdict, dataclass
from typing import Any, Literal

import numpy as np


@dataclass(frozen=True)
class Fold:
    index: int
    train: np.ndarray
    test: np.ndarray


@dataclass(frozen=True)
class WalkForwardSplit:
    initial_train: int = 730  # rows in the first training window (~2 years of days)
    test_size: int = 180  # rows per test fold (~6 months)
    gap: int = 5  # embargo rows between train end and test start
    window: Literal["expanding", "rolling"] = "expanding"
    max_train: int | None = None  # rolling window length (defaults to initial_train)
    min_test: int = 30  # keep a final, shorter fold only if it has at least this many rows

    def __post_init__(self) -> None:
        if self.initial_train < 1 or self.test_size < 1 or self.min_test < 1:
            raise ValueError("initial_train, test_size and min_test must be >= 1")
        if self.gap < 0:
            raise ValueError("gap must be >= 0")
        if self.window not in ("expanding", "rolling"):
            raise ValueError("window must be 'expanding' or 'rolling'")

    def folds(self, n: int) -> list[Fold]:
        folds: list[Fold] = []
        window_len = self.max_train or self.initial_train
        test_start = self.initial_train + self.gap
        while test_start < n:
            test_end = min(test_start + self.test_size, n)
            if test_end - test_start < self.min_test:
                break
            train_end = test_start - self.gap  # exclusive
            train_start = 0 if self.window == "expanding" else max(0, train_end - window_len)
            folds.append(
                Fold(
                    index=len(folds),
                    train=np.arange(train_start, train_end),
                    test=np.arange(test_start, test_end),
                )
            )
            test_start += self.test_size
        return folds

    def split(
        self, X: Any, y: Any = None, groups: Any = None
    ) -> Iterator[tuple[np.ndarray, np.ndarray]]:
        """scikit-learn CV-splitter protocol, so it also works with cross_val_score & co."""
        for fold in self.folds(len(X)):
            yield fold.train, fold.test

    def get_n_splits(self, X: Any = None, y: Any = None, groups: Any = None) -> int:
        if X is None:
            raise ValueError("X is required to know how many folds fit")
        return len(self.folds(len(X)))

    def config(self) -> dict[str, Any]:
        return {"method": "walk_forward", **asdict(self)}


def inner_validation_split(
    n_train: int, fraction: float = 0.15, gap: int = 5, min_val: int = 20
) -> tuple[np.ndarray, np.ndarray]:
    """Chronological split of a TRAINING fold into fit / validation parts (e.g. for early
    stopping): the validation slice is the most recent `fraction` of the fold, after a gap.
    The test fold is never touched."""
    n_val = max(min_val, round(n_train * fraction))
    fit_end = n_train - n_val - gap
    if fit_end < min_val:
        raise ValueError(f"training fold of {n_train} rows is too small for an inner split")
    return np.arange(0, fit_end), np.arange(fit_end + gap, n_train)
