"""Prediction targets: the ONLY place in the codebase that is allowed to look into the future.

Kept apart from features on purpose: nothing under `marketlens.features` (except this module)
may import it, and targets are never written to the `features` table. A test enforces both.

Row t's target describes day t+1, i.e. what happens AFTER the features of row t are known:
    next_day_log_return[t] = ln(close[t+1] / close[t])
The last row has no target yet (NaN): tomorrow hasn't happened. Training must drop it.
"""

import numpy as np
import pandas as pd


def next_day_log_return(candles: pd.DataFrame) -> pd.Series:
    close = candles["close"]
    return np.log(close.shift(-1) / close).rename("next_day_log_return")


def next_day_up(candles: pd.DataFrame) -> pd.Series:
    """1.0 if tomorrow's close is above today's, 0.0 if flat or down, NaN if unknown."""
    ret = next_day_log_return(candles)
    return (ret > 0).astype("float64").where(ret.notna()).rename("next_day_up")
