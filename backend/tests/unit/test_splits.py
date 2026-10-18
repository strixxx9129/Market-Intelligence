import itertools

import numpy as np
import pytest

from marketlens.ml.splits import WalkForwardSplit, inner_validation_split

CONFIGS = [
    WalkForwardSplit(initial_train=it, test_size=ts, gap=g, window=w, max_train=mt)
    for it, ts, g, w, mt in itertools.product(
        [50, 200], [10, 37, 90], [0, 1, 5, 20], ["expanding", "rolling"], [None, 120]
    )
]


@pytest.mark.parametrize("n", [100, 333, 1000])
@pytest.mark.parametrize(
    "cfg",
    CONFIGS,
    ids=lambda c: f"{c.window}-it{c.initial_train}-ts{c.test_size}-g{c.gap}-mt{c.max_train}",
)
def test_no_test_index_is_within_gap_of_training(cfg: WalkForwardSplit, n: int) -> None:
    folds = cfg.folds(n)
    for fold in folds:
        assert len(fold.train) > 0 and len(fold.test) > 0
        # The core guarantee: every test row is strictly after every train row + gap.
        assert fold.test.min() > fold.train.max() + cfg.gap
        assert np.all(np.diff(fold.train) == 1) and np.all(np.diff(fold.test) == 1)
        assert fold.test.max() < n


@pytest.mark.parametrize("cfg", CONFIGS[:24], ids=str)
def test_folds_are_chronological_and_disjoint(cfg: WalkForwardSplit) -> None:
    folds = cfg.folds(1000)
    tests = [f.test for f in folds]
    for a, b in itertools.pairwise(tests):
        assert a.max() < b.min()  # each test fold comes after the previous one
    all_test = np.concatenate(tests) if tests else np.array([])
    assert len(all_test) == len(set(all_test.tolist()))  # no row is tested twice


def test_expanding_grows_and_rolling_slides() -> None:
    exp = WalkForwardSplit(initial_train=100, test_size=50, gap=5).folds(400)
    roll = WalkForwardSplit(initial_train=100, test_size=50, gap=5, window="rolling").folds(400)
    assert [f.train[0] for f in exp] == [0] * len(exp)
    assert len(exp) == 6  # tests start at 105, 155, ..., 355 (last fold: 45 rows >= min_test)
    assert [len(f.train) for f in exp] == [100, 150, 200, 250, 300, 350]
    assert all(len(f.train) == 100 for f in roll)
    assert [f.train[0] for f in roll] == [0, 50, 100, 150, 200, 250]
    # Same test folds either way: only the training window differs.
    assert [f.test.tolist() for f in exp] == [f.test.tolist() for f in roll]


def test_last_partial_fold_kept_only_if_large_enough() -> None:
    cfg = WalkForwardSplit(initial_train=100, test_size=50, gap=0, min_test=30)
    assert len(cfg.folds(100 + 50 * 3 + 29)[-1].test) == 50  # 29-row tail dropped
    assert len(cfg.folds(100 + 50 * 3 + 30)[-1].test) == 30  # 30-row tail kept


def test_too_little_data_gives_no_folds() -> None:
    assert WalkForwardSplit(initial_train=730).folds(700) == []


def test_sklearn_splitter_protocol() -> None:
    cfg = WalkForwardSplit(initial_train=100, test_size=50, gap=5)
    X = np.zeros((400, 3))
    pairs = list(cfg.split(X))
    assert len(pairs) == cfg.get_n_splits(X) == len(cfg.folds(400))
    assert cfg.config()["method"] == "walk_forward" and cfg.config()["gap"] == 5


@pytest.mark.parametrize("kwargs", [{"gap": -1}, {"test_size": 0}, {"window": "random"}])
def test_invalid_config_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        WalkForwardSplit(**kwargs)  # type: ignore[arg-type]


def test_inner_validation_is_the_most_recent_slice_after_a_gap() -> None:
    fit, val = inner_validation_split(1000, fraction=0.15, gap=5)
    assert val[-1] == 999 and len(val) == 150
    assert val.min() > fit.max() + 5
    with pytest.raises(ValueError, match="too small"):
        inner_validation_split(40, fraction=0.5, gap=5)
