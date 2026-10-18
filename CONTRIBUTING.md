# Contributing to MarketLens

Thanks for helping! Guides for feature and model plugins arrive in later phases; the data
connector guide is below.

## Workflow

1. Fork, create a branch (`feat/...`, `fix/...`).
2. `cp .env.example .env && make up && make migrate`.
3. Make your change **with tests**.
4. `make lint && make test` must pass (CI runs the same checks on every PR).
5. Open a PR with a [Conventional Commit](https://www.conventionalcommits.org/) title
   (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).

## Ground rules

- **No look-ahead bias.** A feature at time *t* may only use data available at or before *t*.
  Splits are chronological; never random.
- **Idempotent jobs.** Every job must be safe to re-run (UPSERT on natural keys).
- **UTC everywhere** (`timestamptz`, timezone-aware `datetime`).
- **The API never trains models or calls external data sources** during a request.
- **Type hints everywhere**; mypy runs in strict mode on `backend/src`.
- **Schema changes go through Alembic**: `docker compose ... run --rm api alembic revision --autogenerate -m "..."`, then review the generated file.
- Only add data sources whose licence allows redistribution.
- Never commit secrets; configuration comes from environment variables.

## Adding a data connector

A connector turns one data source into canonical daily candles; the pipeline handles
validation, quarantine, UPSERT and idempotency for you. See
[`kaggle_btc.py`](backend/src/marketlens/ingestion/connectors/kaggle_btc.py) for a full example
and [ADR 0004](docs/adr/0004-ingestion-pipeline.md) for the design.

```python
from marketlens.ingestion.connectors.base import BaseConnector, register_connector
from marketlens.ingestion.types import FetchResult

@register_connector
class MyExchangeConnector(BaseConnector):
    name = "my_exchange"              # used as: marketlens ingest --source my_exchange
    supported_intervals = ("1d",)
    supported_assets = ("ETH-USD",)   # empty tuple = any symbol

    def fetch(self, asset, start, end, interval="1d") -> FetchResult:
        # Return candles with columns ts (UTC period open), open, high, low, close, volume,
        # minutes_present, minutes_traded, for start <= ts < end.
        # Rules: never return an unfinished period; report every unusable raw record as a
        # QualityIssue instead of dropping it; never call the network from the API process.
        ...
```

- In-repo: put it in `ingestion/connectors/` and import it in that package's `__init__.py`.
- As a separate package: declare an entry point instead, with no change to MarketLens needed:
  `[project.entry-points."marketlens.connectors"] my_exchange = "my_pkg:MyExchangeConnector"`.
- File-based sources should implement `fingerprint()` (SHA-256 of the input) so unchanged
  inputs are skipped.
- Add a small fixture with deliberately broken rows and tests like
  `tests/unit/test_connectors.py`. Check the source's **licence** first (CLAUDE.md rule 7).

## Adding a feature

Features live in `backend/src/marketlens/features/` (one module per family). See the generated
[feature catalog](docs/feature-catalog.md) and [ADR 0005](docs/adr/0005-feature-store.md).

```python
from marketlens.features.registry import register_feature

@register_feature("close_to_sma_100", "close / SMA(close, 100 days incl. today) - 1", lookback=99)
def close_to_sma_100(c: pd.DataFrame) -> pd.Series:
    return c["close"] / c["close"].rolling(100).mean() - 1
```

Rules (all enforced by tests, so you'll know immediately):
- Use rows up to and including day *t* only. No `shift(-k)`, no `center=True`, no statistics
  over the whole series (global mean/std, min-max), no back-filling.
- `lookback` = earlier rows needed; the value must be NaN before it and finite from it on.
- New module? Import it in `features/__init__.py`.
- To use it in a model, add a **new** set version in `features/sets.py` (never edit an existing
  one), then run `make feature-catalog` and commit the regenerated catalog.

## Adding a model

Models live in `backend/src/marketlens/ml/models/`. A model is a recipe for an **unfitted**
sklearn Pipeline; training, walk-forward evaluation, baseline comparison, MLflow logging and
versioning are shared. See [ADR 0006](docs/adr/0006-model-evaluation.md).

```python
from marketlens.ml.models.base import BaseModel, linear_preprocessor, register_model

@register_model
class ElasticNetLogistic(BaseModel):
    name = "elasticnet_logistic"
    task = "classification"
    family = "linear"
    default_params: ClassVar[dict[str, Any]] = {"C": 0.1, "l1_ratio": 0.5, "max_iter": 2000}

    def build_pipeline(self, params, *, seed, categorical=()):
        return Pipeline([
            ("prep", linear_preprocessor(categorical)),   # fitted per training fold
            ("model", LogisticRegression(penalty="elasticnet", solver="saga",
                                         random_state=seed, **params)),
        ])
```

- Put anything that learns from data (scalers, encoders, imputers) **inside** the pipeline.
- Don't tune hyperparameters against the reported folds; if you need tuning, nest it inside
  the training fold.
- Your model is judged against the baselines on the same folds; "no improvement" is a fine
  result to report.

## Optional: git hooks

```bash
uv tool install pre-commit
pre-commit install
```
