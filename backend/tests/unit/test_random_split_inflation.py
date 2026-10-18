"""Demo: a random (shuffled) split lies on time series; walk-forward tells the truth.

The market here is a pure random walk, so NOTHING is predictable and the honest accuracy is
50%. Features are ordinary trailing indicators (30-day return/volatility, 10-day return).

With a 7-day forward-return target, consecutive labels overlap: day t's label shares 6 of its 7
days with day t+1's. Rolling features of neighbouring days are near-identical too. A shuffled
K-fold puts day t-1 and t+1 (same features, nearly the same label) into training when day t
is tested, so a forest "recognises" the test day and looks skilful out of pure noise.

Walk-forward with a gap of at least the label horizon removes that overlap and reports the
truth: a coin flip. With a 1-day label (no overlap) the inflation mostly disappears, which is
why MarketLens predicts 1-day returns AND uses walk-forward with a gap anyway.
"""

import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import KFold, cross_val_score

from marketlens.ml.splits import WalkForwardSplit


def _random_walk_dataset(
    horizon: int, n: int = 3000, seed: int = 0
) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    close = pd.Series(np.exp(np.cumsum(rng.normal(0, 0.02, n))))
    lr = np.log(close).diff()
    X = pd.DataFrame(
        {
            "ret_30": lr.rolling(30).sum(),
            "vol_30": lr.rolling(30).std(),
            "ret_10": lr.rolling(10).sum(),
        }
    )
    future = close.shift(-horizon)
    y = (np.log(future / close) > 0).astype(float).where(future.notna())
    keep = X.notna().all(axis=1) & y.notna()
    return X[keep].to_numpy(), y[keep].to_numpy()


def _scores(horizon: int) -> tuple[float, float]:
    X, y = _random_walk_dataset(horizon)
    model = RandomForestClassifier(n_estimators=200, min_samples_leaf=5, random_state=0, n_jobs=2)
    shuffled = cross_val_score(model, X, y, cv=KFold(5, shuffle=True, random_state=0)).mean()
    walk_forward = cross_val_score(
        model, X, y, cv=WalkForwardSplit(initial_train=600, test_size=300, gap=horizon + 5)
    ).mean()
    print(f"{horizon}-day target: shuffled {shuffled:.1%}   walk-forward {walk_forward:.1%}")
    return float(shuffled), float(walk_forward)


def test_shuffled_split_invents_skill_from_noise_with_overlapping_labels() -> None:
    shuffled, walk_forward = _scores(horizon=7)
    assert shuffled > 0.56  # "a 60% model!", on a random walk
    assert walk_forward < 0.56  # the truth: no skill
    assert shuffled - walk_forward > 0.05


@pytest.mark.parametrize("horizon", [1])
def test_without_label_overlap_the_inflation_mostly_disappears(horizon: int) -> None:
    shuffled, walk_forward = _scores(horizon)
    assert abs(shuffled - 0.5) < 0.04 and abs(walk_forward - 0.5) < 0.04
