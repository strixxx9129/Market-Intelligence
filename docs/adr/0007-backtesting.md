# ADR 0007: Backtesting from out-of-sample predictions, after costs

- Status: Accepted
- Date: 2026-10-19

## Context

Phase 5 produced honest out-of-sample (OOS) predictions. Turning them into a hypothetical
strategy is where most remaining self-deception happens: trading at the price the signal was
computed from, ignoring costs, and choosing parameters on the period being reported.

## Decision

1. **Inputs are OOS predictions only** (`oos_predictions`), never in-sample fits.
2. **Explicit timing.** A decision on day t (known after close[t]) is executed at `next_open`
   (open[t+1], default) or `next_close` (close[t+1]) and earns exec[t+1] → exec[t+2]. One
   `shift(1)` between target and position enforces it; a test proves a signal cannot earn the
   move it was computed from, and an "oracle" positive control shows next-day foresight only
   pays with `next_open`.
3. **Costs per unit traded**: (fee + slippage) bps × |Δposition|; long↔short flips trade 2
   units; entry costs count; open positions at the end are marked to market. Every backtest
   includes a cost sweep (0/5/15/30/60 bps) and the other execution timing.
4. **Threshold chosen on earlier folds only** (first half), with prices cut at the last
   selection day; results reported on the later folds. Selection-period and evaluation-period
   Sharpe are shown side by side.
5. **Benchmarks on the same days, timing and costs**: buy-and-hold, the best baseline model of
   the same training run through the same rule, and cash. Sharpe is reported ± an approximate
   standard error (Lo 2002); "beats" in the report requires more than 2 SE.
6. **Storage**: `backtests` row (config, full metrics JSON, git SHA, baseline version) and the
   daily equity curve as Parquet in object storage (`s3://backtests/<id>.parquet`, SeaweedFS
   locally) behind a small `ObjectStore` interface (boto3; local filesystem in tests).
7. Metrics use 365 periods/year; Sharpe/Sortino with a 0 risk-free rate.

## Consequences

- Results are hypothetical and path-dependent: ~6 years of evaluation data pins a Sharpe down
  to roughly ±0.5, so most differences between strategies are not distinguishable.
- Simple shorts (no borrow/funding costs) and no market impact beyond a flat slippage: fine for
  education, optimistic for real trading.
- SeaweedFS needs `-master.volumeSizeLimitMB=1024 -volume.max=100` so each bucket (collection)
  can get volumes on a small dev disk.
