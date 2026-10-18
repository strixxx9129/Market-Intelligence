# ADR 0005: Feature store: registry, immutable versioned sets, truncation-tested features

- Status: Accepted
- Date: 2026-10-19

## Context

Models need features computed identically in research, training and (later) serving, without
look-ahead bias, reproducibly, and contributors must be able to add features safely.
Look-ahead bias is the most common way financial ML produces fake results: on our BTC data a
centred 5-day moving average lifts naive next-day direction accuracy from ~51% to 74%, and a
`shift(-1)` feature to 100%.

## Decision

1. **Timing convention.** Candle `ts` is the period open; its close is known at the end of the
   day. A feature at row t may use rows ≤ t. The target at row t is ln(close[t+1]/close[t]).
2. **Registry.** Features are small pure functions `f(candles) -> Series`, registered with
   `@register_feature(name, description, lookback)`. Family = module name.
3. **Leakage is tested, not reviewed.** A truncation test runs over *every* registered feature
   (value at t on the full series == value at t on the series cut at t), with negative controls
   (centred window, `shift(-1)`, global z-score) proving the test can fail. A static guard
   rejects `shift(-`, `center=True`, back-filling and `targets` imports in feature modules.
   A lookback-honesty test checks that NaNs occur for exactly the declared warm-up.
4. **Targets live apart** (`features/targets.py`), are never imported by features and are never
   stored in the `features` table; training computes them from `market_prices`.
5. **Warm-up rows are dropped, never filled.** After dropping `max(lookback)` rows, any NaN/inf
   fails the build.
6. **Immutable versions.** A set version is a frozen list in `features/sets.py`. Its
   `code_hash` = SHA-256(definition + full source of each feature module). Rebuilding a version
   whose hash changed is refused; changed code needs a new version. Whole modules are hashed
   (conservative: also catches helper edits, at the cost of forcing a new version when a
   neighbouring feature changes).
7. **Storage.** `features.values` is JSONB keyed by feature name, UNIQUE(asset_id, ts,
   feature_set_id), UPSERT with `IS DISTINCT FROM`. A rebuild recomputes the full history (cheap
   at ~5k rows) and only writes rows that changed: past-only features never change, so a daily
   rebuild writes one row.
8. **Generated catalog.** `docs/feature-catalog.md` is generated from the registry; CI fails if
   it is stale. It shows each set's code hash, so any edit to feature code is visible in review.

## Consequences

- Adding a feature = one function + registration; it automatically gets the leakage, lookback
  and documentation tests, and appears in the catalog.
- JSONB values need no migration per feature, but are not individually typed or indexed;
  training loads a whole set into a DataFrame (fine at this scale).
- Formatting-only edits to a feature module change the hash too. Bump the version or revert;
  never weaken the check.
- Features exist from 2012-02-19 (after warm-up); the 2013+ modelling sample (Phase 2) is
  applied at training time.
