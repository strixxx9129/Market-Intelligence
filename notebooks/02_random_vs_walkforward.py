# ---
# jupyter:
#   jupytext:
#     formats: py:percent
#     text_representation:
#       extension: .py
#       format_name: percent
#   kernelspec:
#     display_name: Python 3
#     language: python
#     name: python3
# ---

# %% [markdown]
# # 02 · Random split vs walk-forward on real BTC data
#
# **Question:** how much does a random (shuffled) train/test split flatter a model on a
# time series?
#
# Same data (feature set `v1`, 2013+), same model, same metric; only the validation scheme
# changes. Two targets:
#
# * **1-day** direction (what MarketLens predicts): labels don't overlap.
# * **7-day** direction: day t's label shares 6 of 7 days with day t+1's (overlapping labels).
#
# The unit test `tests/unit/test_random_split_inflation.py` shows the same effect on a pure
# random walk, where the true accuracy is exactly 50%.
#
# Run with `make notebook` (open as notebook) or headless:
# `cd notebooks && jupytext --to ipynb --execute 02_random_vs_walkforward.py`.
# Educational analysis, not financial advice.

# %%
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import KFold, cross_val_score

ROOT = Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()
sys.path.insert(0, str(ROOT / "backend" / "src"))  # use the project's own feature code

from marketlens.features.builder import compute_features, drop_warmup, resolve_set
from marketlens.ml.splits import WalkForwardSplit

FIG_DIR = ROOT / "docs" / "figures"
ATTRIBUTION = "Data: mczielinski/bitcoin-historical-data (Kaggle, Bitstamp), CC BY-SA 4.0"

# %% [markdown]
# ## Data: the same `v1` features the platform stores
# Recomputed from the Phase 2 daily cache with the registry code, so there is no second copy
# of the feature logic.

# %%
candles = pd.read_parquet(ROOT / "data" / "interim" / "btc_daily.parquet")
candles = candles[["open", "high", "low", "close", "volume"]]
specs = resolve_set("v1")
X_all = drop_warmup(compute_features(candles, specs), max(s.lookback for s in specs))
close = candles["close"]


def dataset(horizon: int) -> tuple[pd.DataFrame, pd.Series]:
    future = close.shift(-horizon)
    y = (np.log(future / close) > 0).astype(float).where(future.notna())
    frame = X_all.join(y.rename("y"), how="inner").loc["2013-01-01":].dropna()
    return frame.drop(columns="y"), frame["y"]


X1, y1 = dataset(1)
print(f"{len(X1):,} days, {X1.shape[1]} features, up-days {y1.mean():.1%}")

# %% [markdown]
# ## Same model, two validation schemes
# Random forest (depth-limited, as in the platform). Walk-forward uses a gap of horizon + 5
# days, so no training label overlaps the test period.

# %%
model = RandomForestClassifier(
    n_estimators=300, max_depth=6, min_samples_leaf=10, random_state=0, n_jobs=2
)
rows = []
for horizon in (1, 7):
    X, y = dataset(horizon)
    shuffled = cross_val_score(model, X, y, cv=KFold(5, shuffle=True, random_state=0))
    walk = cross_val_score(
        model, X, y, cv=WalkForwardSplit(initial_train=730, test_size=180, gap=horizon + 5)
    )
    rows.append(
        {
            "target": f"{horizon}-day direction",
            "up-rate (always-up accuracy)": y.mean(),
            "shuffled K-fold accuracy": shuffled.mean(),
            "walk-forward accuracy": walk.mean(),
            "walk-forward fold std": walk.std(ddof=1),
            "inflation (pp)": (shuffled.mean() - walk.mean()) * 100,
        }
    )
results = pd.DataFrame(rows).set_index("target")
print(results.to_string(float_format=lambda v: f"{v:.3f}"))

# %%
fig, ax = plt.subplots(figsize=(8, 4))
results[
    ["up-rate (always-up accuracy)", "shuffled K-fold accuracy", "walk-forward accuracy"]
].plot.bar(ax=ax, rot=0)
ax.axhline(0.5, color="gray", lw=0.8, ls="--")
ax.set(ylim=(0.4, 0.75), ylabel="accuracy", title="Same model, same data: shuffled vs walk-forward")
fig.text(0.99, 0.005, ATTRIBUTION, ha="right", va="bottom", fontsize=7, color="gray")
fig.savefig(FIG_DIR / "random_vs_walkforward.png", bbox_inches="tight")
plt.show()

# %% [markdown]
# ## Reading the result
# * With **overlapping 7-day labels**, shuffling lets the model "recognise" test days from
#   their near-identical neighbours in training, so accuracy jumps without any real skill.
# * With **1-day labels** the inflation is smaller, but the walk-forward number is the only one
#   that matches how the model would actually be used: trained on the past, run on the future.
# * Always compare with the **always-up** accuracy (the up-rate): a model below it has no
#   directional skill at all.
