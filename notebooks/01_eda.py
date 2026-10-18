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
# # 01 · Bitcoin exploratory data analysis
#
# **Goal:** understand the BTC/USD data *before* modelling it.
#
# Data: Kaggle `mczielinski/bitcoin-historical-data` (Bitstamp 1-minute OHLCV), licence
# **CC BY-SA 4.0**. Download with `make data`; this notebook never touches the network.
#
# Run end-to-end with `make eda` (regenerates `docs/figures/` and `data/interim/`), or open
# this file in Jupyter Lab (`make notebook` → right-click → *Open With → Notebook*).
#
# > Educational analysis, not financial advice.

# %%
import hashlib
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from IPython.display import display
from matplotlib.ticker import PercentFormatter

ROOT = Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()
RAW_CSV = ROOT / "data" / "raw" / "btcusd_1-min_data.csv"
MANIFEST = ROOT / "data" / "raw" / "MANIFEST.json"
INTERIM = ROOT / "data" / "interim"
FIG_DIR = ROOT / "docs" / "figures"
INTERIM.mkdir(parents=True, exist_ok=True)
FIG_DIR.mkdir(parents=True, exist_ok=True)

ATTRIBUTION = "Data: mczielinski/bitcoin-historical-data (Kaggle, Bitstamp), CC BY-SA 4.0"
plt.rcParams.update({"figure.figsize": (11, 4.5), "figure.dpi": 110, "axes.grid": True})
pd.set_option("display.float_format", lambda x: f"{x:,.4f}")


def save(fig: plt.Figure, name: str) -> None:
    """Save a figure for docs/eda-report.md, with the licence-required attribution."""
    fig.text(0.99, 0.005, ATTRIBUTION, ha="right", va="bottom", fontsize=7, color="gray")
    fig.savefig(FIG_DIR / f"eda_{name}.png", bbox_inches="tight")


# %% [markdown]
# ## 1 · Load the raw CSV efficiently
#
# * `usecols` + explicit `dtype`: pandas doesn't have to guess types (guessing is slow and can
#   silently turn a column into `object` if one row is malformed).
# * `float64`, not `float32`: at ~$100k, float32 can only represent steps of ~$0.008, which
#   distorts the tiny 1-minute returns.
# * **Chunked reading** (`chunksize=1_000_000`): the file is parsed 1M rows at a time, so peak
#   memory ≈ final DataFrame + one chunk. The multi-threaded pyarrow parser is faster but needs
#   large buffers, and it ran out of memory in a 4 GB Docker VM. For bigger-than-RAM data
#   you would aggregate each chunk instead of keeping it.
# * First we check the file hash against `MANIFEST.json`, so every number below is tied
#   to an exact version of the data (reproducibility).

# %%
manifest = json.loads(MANIFEST.read_text())
expected_sha = manifest["files"][RAW_CSV.name]["sha256"]
digest = hashlib.sha256()
with RAW_CSV.open("rb") as fh:
    while chunk := fh.read(1 << 20):
        digest.update(chunk)
assert digest.hexdigest() == expected_sha, "raw file differs from MANIFEST.json, re-run make data"
print(f"sha256 OK: {expected_sha[:16]}…  (retrieved {manifest['retrieved_at']})")

COLS = ["Timestamp", "Open", "High", "Low", "Close", "Volume"]
chunks = pd.read_csv(
    RAW_CSV,
    usecols=COLS,
    dtype=dict.fromkeys(COLS, "float64"),
    chunksize=1_000_000,
)
raw = pd.concat(chunks, ignore_index=True)
print(f"rows: {len(raw):,}   memory: {raw.memory_usage(deep=True).sum() / 2**20:,.0f} MiB")
display(raw.head(3))
display(raw.isna().sum().rename("NaN count").to_frame().T)

# %% [markdown]
# ## 2 · Unix timestamps → UTC
#
# `Timestamp` is seconds since 1970-01-01 **UTC**. We convert with `utc=True` so the index is
# timezone-aware: naive datetimes are a classic source of off-by-hours bugs (rule 4).
# We also check that every timestamp is a whole minute and that the file is sorted.

# %%
assert (raw["Timestamp"] % 1 == 0).all(), "fractional-second timestamps"
ts_seconds = raw["Timestamp"].astype("int64")
print("all on minute boundaries:", bool((ts_seconds % 60 == 0).all()))

df = raw.drop(columns="Timestamp").set_axis(
    pd.to_datetime(ts_seconds, unit="s", utc=True).rename("ts"), axis=0
)
df.columns = df.columns.str.lower()
del raw

print("monotonic increasing:", df.index.is_monotonic_increasing)
print("first:", df.index[0], "  last:", df.index[-1], "  tz:", df.index.tz)

# %% [markdown]
# ## 3 · Data-quality report
#
# Each check answers "can I trust this row?":
#
# | Check | Why it matters |
# |---|---|
# | duplicate timestamps | double-counted volume, ambiguous prices |
# | missing minutes | gaps make a "1-minute return" secretly span hours |
# | zero volume | no trades happened: the price is a *fill*, not a market observation |
# | OHLC violations | `low ≤ open, close ≤ high` must hold; violations mean corrupt rows |

# %%
dup_ts = int(df.index.duplicated().sum())
dup_rows = int(df.reset_index().duplicated().sum())
print(f"duplicate timestamps: {dup_ts:,}   fully duplicated rows: {dup_rows:,}")
if dup_ts:
    df = df[~df.index.duplicated(keep="last")]

# %%
# Missing minutes: compare against a complete 1-minute grid from first to last timestamp.
full_grid = pd.date_range(df.index[0], df.index[-1], freq="1min", tz="UTC")
missing_idx = full_grid.difference(df.index)
print(
    f"expected minutes: {len(full_grid):,}   present: {len(df):,}   "
    f"missing: {len(missing_idx):,} ({len(missing_idx) / len(full_grid):.2%})"
)

# Gap lengths: consecutive differences > 1 minute.
step = df.index.to_series().diff().dt.total_seconds().div(60)
gaps = step[step > 1].rename("gap_minutes")
print(f"gaps: {len(gaps):,}   longest gaps:")
display(
    gaps.sort_values(ascending=False)
    .head(5)
    .to_frame()
    .assign(gap_hours=lambda g: g.gap_minutes / 60, gap_ends_at=lambda g: g.index)
    .reset_index(drop=True)
)

# %%
# Zero-volume rows and "flat fill" rows (O=H=L=C with no volume: carried-forward prices).
zero_vol = df["volume"] == 0
flat = df[["open", "high", "low"]].eq(df["close"], axis=0).all(axis=1)
flat_fill = zero_vol & flat

# OHLC consistency: low <= min(open, close) and max(open, close) <= high; prices > 0.
body_lo = df[["open", "close"]].min(axis=1)
body_hi = df[["open", "close"]].max(axis=1)
ohlc_bad = (df["low"] > body_lo) | (df["high"] < body_hi) | (df["low"] > df["high"])
non_positive = (df[["open", "high", "low", "close"]] <= 0).any(axis=1)
negative_vol = df["volume"] < 0

summary = pd.Series(
    {
        "rows": len(df),
        "zero-volume rows": int(zero_vol.sum()),
        "flat-fill rows (O=H=L=C, vol=0)": int(flat_fill.sum()),
        "OHLC violations": int(ohlc_bad.sum()),
        "non-positive prices": int(non_positive.sum()),
        "negative volume": int(negative_vol.sum()),
    },
    name="count",
).to_frame()
summary["% of rows"] = summary["count"] / len(df) * 100
display(summary)
if ohlc_bad.any():
    display(df[ohlc_bad].head())

# %%
# Quality by year: the story is rarely uniform over 14 years.
year = df.index.year
grid_year = full_grid.year
by_year = pd.DataFrame(
    {
        "expected_min": pd.Series(1, index=grid_year).groupby(level=0).sum(),
        "present_min": pd.Series(1, index=year).groupby(level=0).sum(),
        "zero_vol_min": zero_vol.groupby(year).sum(),
    }
)
by_year["missing_%"] = (1 - by_year.present_min / by_year.expected_min) * 100
by_year["zero_vol_%_of_present"] = by_year.zero_vol_min / by_year.present_min * 100
by_year["traded_%_of_expected"] = (
    (by_year.present_min - by_year.zero_vol_min) / by_year.expected_min * 100
)
display(by_year.round(2))

# %%
fig, ax = plt.subplots()
by_year[["missing_%", "zero_vol_%_of_present"]].plot.bar(ax=ax)
ax.set(title="Data quality by year: missing minutes and zero-volume minutes", ylabel="%", xlabel="")
save(fig, "quality_by_year")
plt.show()

# %% [markdown]
# ## 4 · Resample to daily OHLCV
#
# Daily bars are what we will model first (target = return from day *t* to *t+1*).
#
# * open = first, high = max, low = min, close = last, volume = sum, per **UTC** day.
# * We keep `minutes_present` and `minutes_traded` so we can flag days built from very
#   little real trading.
# * The **last, still-forming day is dropped**: its "close" is not a close yet. Using an
#   unfinished candle is a subtle form of look-ahead (rule 1).

# %%
daily = df.resample("1D").agg(
    {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
)
daily["minutes_present"] = df["close"].resample("1D").count()
daily["minutes_traded"] = (~zero_vol).resample("1D").sum()

last_day = daily.index[-1]
if df.index[-1] < last_day + pd.Timedelta(days=1) - pd.Timedelta(minutes=1):
    print(
        f"dropping incomplete last day {last_day.date()} "
        f"({int(daily.loc[last_day, 'minutes_present'])} of 1440 minutes)"
    )
    daily = daily.iloc[:-1]

empty_days = daily["close"].isna()
low_coverage = daily["minutes_traded"] < 60  # less than one hour of actual trading
print(f"days: {len(daily):,}  ({daily.index[0].date()} → {daily.index[-1].date()})")
print(f"days with no rows at all: {int(empty_days.sum())}")
print(
    f"days with < 60 traded minutes: {int(low_coverage.sum())}  "
    f"(by year: {low_coverage.groupby(daily.index.year).sum().loc[lambda s: s > 0].to_dict()})"
)
assert (daily["low"] <= daily[["open", "close"]].min(axis=1)).all()
assert (daily["high"] >= daily[["open", "close"]].max(axis=1)).all()
display(daily.tail(3))

# %%
daily["low_coverage"] = low_coverage
daily.to_parquet(INTERIM / "btc_daily.parquet")
print("saved", (INTERIM / "btc_daily.parquet").relative_to(ROOT))

# %% [markdown]
# **Reading the quality report.** The file has *no* missing minutes, because gaps were filled
# with flat, zero-volume rows that carry the last price forward. So "missing data" hides in
# the zero-volume rows instead: in 2012 ~95% of minutes had no trades, and 192 days had less
# than an hour of real trading. Prices on those days are stale fills, which would add fake zero
# returns and understate volatility.
#
# **Decision:** the return analysis below starts on **2013-01-01**. The full daily series
# (with `minutes_traded` / `low_coverage`) is saved, so later phases can make their own choice.

# %% [markdown]
# ## 5 · Simple vs log returns
#
# * simple return  $r_t = P_t / P_{t-1} - 1$: what a portfolio actually earns; adds up
#   across *assets*.
# * log return  $\ell_t = \ln(P_t / P_{t-1})$: adds up across *time*
#   ($\sum \ell_t = \ln(P_T/P_0)$), symmetric, and closer to normal. Most statistics use it.
#
# For small moves they are almost equal ($\ln(1+r) \approx r$); on big crypto days they are not.

# %%
from scipy import stats

px = daily.loc["2013-01-01":, "close"]
ret = pd.DataFrame({"simple": px.pct_change(), "log": np.log(px).diff()}).dropna()
r = ret["log"]

print(f"sample: {r.index[0].date()} → {r.index[-1].date()}  ({len(r):,} daily returns)")
print(f"sum of log returns = {r.sum():.4f}   ln(P_T/P_0) = {np.log(px.iloc[-1] / px.iloc[0]):.4f}")
big = ret.loc[ret["simple"].abs().idxmax()]
print(f"biggest day: simple {big['simple']:+.2%} vs log {big['log']:+.2%}")
display(ret.describe().T)

# %% [markdown]
# ## 6 · Plots
# ### 6a · Price on a log scale
# On a linear axis 2013 looks flat; on a log axis equal vertical distances are equal **%**
# moves, so different eras become comparable. Dashed lines mark the Bitcoin supply halvings.

# %%
HALVINGS = ["2012-11-28", "2016-07-09", "2020-05-11", "2024-04-20"]

fig, ax = plt.subplots()
ax.plot(daily.index, daily["close"], lw=0.8)
ax.set_yscale("log")
for h in HALVINGS:
    ax.axvline(pd.Timestamp(h, tz="UTC"), color="gray", ls="--", lw=0.8)
ax.set(title="BTC/USD daily close (log scale); dashed = halvings", ylabel="USD (log)")
save(fig, "price_log")
plt.show()

drawdown = daily["close"] / daily["close"].cummax() - 1
fig, ax = plt.subplots(figsize=(11, 3))
ax.fill_between(drawdown.index, drawdown, 0, color="tab:red", alpha=0.5)
ax.set(title="Drawdown from running all-time high", ylabel="drawdown")
ax.yaxis.set_major_formatter(PercentFormatter(1.0))
save(fig, "drawdown")
plt.show()
print(f"max drawdown {drawdown.min():.1%} on {drawdown.idxmin().date()}")

# %% [markdown]
# ### 6b · Return distribution vs a fitted normal
# If returns were normal, a "5σ day" would happen about once every ~10,000 years. Compare the
# histogram's tails (log y-axis, right) with the normal curve and with a Student-t, which
# has fat tails.

# %%
mu, sigma = stats.norm.fit(r)
t_df, t_loc, t_scale = stats.t.fit(r)
grid = np.linspace(r.min(), r.max(), 600)

fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
for ax, logy in zip(axes, [False, True], strict=True):
    ax.hist(r, bins=200, density=True, alpha=0.5, label="daily log returns")
    ax.plot(grid, stats.norm.pdf(grid, mu, sigma), label=f"normal (σ={sigma:.3f})")
    ax.plot(grid, stats.t.pdf(grid, t_df, t_loc, t_scale), label=f"Student-t (ν={t_df:.1f})")
    if logy:
        ax.set_yscale("log")
        ax.set_ylim(1e-3, None)
    ax.set(title="log y-axis: the tails" if logy else "linear y-axis: the centre")
axes[0].legend()
save(fig, "return_hist")
plt.show()

z = (r - mu) / sigma
tail = pd.DataFrame(
    {
        "observed days": [int((z.abs() > k).sum()) for k in (3, 4, 5)],
        "expected if normal": [len(r) * 2 * stats.norm.sf(k) for k in (3, 4, 5)],
    },
    index=["|z| > 3", "|z| > 4", "|z| > 5"],
)
print(tail.to_string(float_format=lambda x: f"{x:.2f}"))
jb = stats.jarque_bera(r)
print(
    f"skew {stats.skew(r):+.3f}   excess kurtosis {stats.kurtosis(r):.2f} (normal = 0)   "
    f"Jarque-Bera p = {jb.pvalue:.1e}"
)

# %% [markdown]
# ### 6c · 30-day rolling volatility
# Standard deviation of daily log returns over a 30-day window, annualised with $\sqrt{365}$
# (crypto trades every day; equities use 252). Watch for **clusters**: calm and wild
# periods each last for months.

# %%
vol30 = r.rolling(30).std() * np.sqrt(365)

fig, ax = plt.subplots()
ax.plot(vol30.index, vol30, lw=0.9)
ax.axhline(r.std() * np.sqrt(365), color="gray", ls="--", lw=0.8, label="full-sample vol")
ax.yaxis.set_major_formatter(PercentFormatter(1.0))
ax.set(title="30-day rolling volatility (annualised)", ylabel="volatility")
ax.legend()
save(fig, "rolling_vol")
plt.show()
print(f"rolling vol range: {vol30.min():.0%} → {vol30.max():.0%}  (median {vol30.median():.0%})")
print(
    "median 30d vol by year:", (vol30.groupby(vol30.index.year).median() * 100).round(0).to_dict()
)

# %% [markdown]
# ### 6d · Autocorrelation of returns and of |returns|
# * ACF of **returns**: does yesterday's return predict today's *direction*? Bars inside the
#   blue band are not statistically different from zero.
# * ACF of **|returns|**: does yesterday's *size* of move predict today's size?
#   This is volatility clustering, seen as a statistic instead of a picture.
#
# The Ljung-Box test checks the first *k* lags jointly (H0: no autocorrelation).

# %%
from statsmodels.graphics.tsaplots import plot_acf
from statsmodels.stats.diagnostic import acorr_ljungbox

fig, axes = plt.subplots(1, 2, figsize=(12, 4))
plot_acf(r, lags=60, ax=axes[0], zero=False, title="ACF of daily log returns")
plot_acf(r.abs(), lags=60, ax=axes[1], zero=False, title="ACF of |daily log returns|")
for ax in axes:
    ax.set_ylim(-0.15, 0.35)
save(fig, "acf")
plt.show()

lb = pd.concat(
    {
        "returns": acorr_ljungbox(r, lags=[1, 10, 30]),
        "|returns|": acorr_ljungbox(r.abs(), lags=[1, 10, 30]),
    },
    names=["series", "lags"],
)
print(lb.to_string(float_format=lambda x: f"{x:.4g}"))
acf_ret = [r.autocorr(k) for k in (1, 2, 5)]
acf_abs = [r.abs().autocorr(k) for k in (1, 10, 30)]
print(
    "autocorr returns lag 1/2/5:",
    np.round(acf_ret, 3),
    "  |returns| lag 1/10/30:",
    np.round(acf_abs, 3),
)

# %% [markdown]
# ### 6e · Yearly return and volatility
# Calendar-year return (compounded), annualised volatility, worst peak-to-trough drawdown
# *within* the year, and the best/worst day.


# %%
def year_stats(g: pd.DataFrame) -> pd.Series:
    close = g["close"]
    return pd.Series(
        {
            "return": np.expm1(g["log"].sum()),
            "volatility": g["log"].std() * np.sqrt(365),
            "max_drawdown": (close / close.cummax() - 1).min(),
            "best_day": g["simple"].max(),
            "worst_day": g["simple"].min(),
            "days": len(g),
        }
    )


yearly = ret.join(px).groupby(ret.index.year).apply(year_stats)
yearly.index.name = "year"
yearly["days"] = yearly["days"].astype(int)
pct = "{:+.1%}".format
print(
    yearly.to_string(
        formatters={
            "return": pct,
            "max_drawdown": pct,
            "best_day": pct,
            "worst_day": pct,
            "volatility": "{:.0%}".format,
        }
    )
)
print(
    f"corr(yearly |return|, volatility) = {yearly['return'].abs().corr(yearly['volatility']):.2f}"
)

# %% [markdown]
# ### 6f · Do the statistics hold in every era? (regimes)
# If a property only exists in 2013's tiny, illiquid market, a model trained on it will fail
# today. Each statistic is recomputed per era.

# %%
ERAS = {"2013-2016": ("2013", "2016"), "2017-2020": ("2017", "2020"), "2021-2026": ("2021", "2026")}


def era_stats(x: pd.Series) -> pd.Series:
    return pd.Series(
        {
            "ann_vol": x.std() * np.sqrt(365),
            "skew": stats.skew(x),
            "excess_kurtosis": stats.kurtosis(x),
            "acf1_returns": x.autocorr(1),
            "acf1_abs_returns": x.abs().autocorr(1),
            "ljungbox10_p_returns": acorr_ljungbox(x, lags=[10])["lb_pvalue"].iloc[0],
        }
    )


eras = pd.DataFrame({name: era_stats(r.loc[a:b]) for name, (a, b) in ERAS.items()}).T
print(eras.to_string(float_format=lambda v: f"{v:.3f}"))

# %% [markdown]
# ## 7 · Stationarity: ADF test on price vs returns
#
# A series is (weakly) **stationary** if its mean, variance and autocorrelation don't depend on
# time. Most models assume the relationship they learn today still holds tomorrow, which needs
# roughly stationary inputs.
#
# Augmented Dickey-Fuller test: H0 = *the series has a unit root* (non-stationary, a random
# walk). A small p-value (< 0.05) rejects H0, meaning "looks stationary".

# %%
from statsmodels.tsa.stattools import adfuller


def adf_row(series: pd.Series) -> dict[str, float]:
    stat, pvalue, lags, _nobs, crit, _ = adfuller(
        series.dropna(), autolag="AIC", result_object=False
    )
    return {"ADF stat": stat, "p-value": pvalue, "lags used": lags, "5% critical": crit["5%"]}


adf = pd.DataFrame(
    {"price": adf_row(px), "log price": adf_row(np.log(px)), "log return": adf_row(r)}
).T
print(adf.to_string(float_format=lambda x: f"{x:.4g}"))
