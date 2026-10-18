# ADR 0003: Exploration tooling: percent-format notebooks in a separate `eda` image

- Status: Accepted
- Date: 2026-10-18

## Context

Phase 2 explores the Bitcoin data in notebooks. Notebooks bring heavy libraries (Jupyter,
matplotlib, statsmodels, kaggle) that the API never needs. `.ipynb` files store JSON plus
outputs, which makes diffs unreadable and lets data leak into git. Contributors may have only
Docker installed, and Docker Desktop VMs are often limited to ~4 GB RAM.

## Decision

1. **Notebook source is a jupytext percent-format `.py` file** (`notebooks/01_eda.py`). It opens
   as a notebook in Jupyter Lab / VS Code, diffs like code, and is linted by ruff (root
   `ruff.toml`). Executed `.ipynb` files are gitignored; figures used by docs are written to
   `docs/figures/` and committed.
2. **A separate `eda` dependency group** in `backend/pyproject.toml`, installed only by a new
   `eda` Dockerfile stage (built on `dev`). The `prod` and `dev` images are unchanged.
3. **A `notebook` compose service under the `eda` profile**: it is not started by `make up`,
   mounts the repo at `/workspace` and `~/.kaggle` read-only, and publishes Jupyter on
   `127.0.0.1` only (no token, never exposed to the network).
4. Make targets: `make data` (download + hash manifest), `make notebook`, `make eda`
   (execute end-to-end).
5. Raw data stays in gitignored `data/raw/`, with `MANIFEST.json` (SHA-256, licence, time).
   The notebook asserts the hash so every result is tied to a specific data version.
6. The CSV is read in 1M-row chunks: pyarrow's multi-threaded parser ran out of memory in a
   4 GB Docker VM.

## Consequences

- One lock file for the whole backend, so EDA and later pipelines use identical library versions.
- The `eda` image is large (~1.5 GB); it is only built when someone runs an EDA target, and
  `make lint` (which also lints `scripts/` and `notebooks/`) builds it once.
- Notebooks remain exploration only (CLAUDE.md); reusable logic found here gets reimplemented
  as tested modules under `backend/src/marketlens/` in later phases.
