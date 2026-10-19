"""Download a Kaggle dataset into a raw-data directory, idempotently.

Used by the `kaggle_btc` connector's `refresh()` (Celery worker, daily) and by
`scripts/download_data.py` (manual `make data`). Runs ONLY in workers/scripts, never in the
API (rule 5).

- The zip is kept, so Kaggle only re-downloads when the remote dataset is newer.
- Every extracted file is SHA-256 hashed and recorded in MANIFEST.json with the licence.
- Network failures are raised as TransientError so Celery retries them with backoff.
Auth: ~/.kaggle/kaggle.json, ~/.kaggle/access_token or KAGGLE_API_TOKEN.
"""

import hashlib
import json
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from marketlens.core.errors import PermanentError, TransientError


@dataclass
class DownloadResult:
    dataset: str
    licences: list[str]
    files: dict[str, dict[str, Any]]  # name -> {bytes, sha256}
    changed: list[str] = field(default_factory=list)
    downloaded: bool = False


def sha256_of(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _api() -> Any:
    try:
        # Imported lazily: `import kaggle` authenticates as a side effect.
        from kaggle.api.kaggle_api_extended import KaggleApi
    except SystemExit as exc:  # the package exits when it finds no credentials
        raise PermanentError("Kaggle credentials not found (~/.kaggle)") from exc
    api = KaggleApi()
    try:
        api.authenticate()
    except SystemExit as exc:
        raise PermanentError("Kaggle authentication failed: check ~/.kaggle") from exc
    return api


def fetch_licences(api: Any, dataset: str) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        meta = json.loads(Path(api.dataset_metadata(dataset, tmp)).read_text())
    meta = meta.get("info", meta)  # kaggle>=2 nests the fields under "info"
    return [lic.get("name", "?") for lic in meta.get("licenses", [])]


def download_dataset(dataset: str, raw_dir: Path, force: bool = False) -> DownloadResult:
    api = _api()
    try:
        licences = fetch_licences(api, dataset)
        raw_dir.mkdir(parents=True, exist_ok=True)
        zip_path = raw_dir / f"{dataset.split('/')[1]}.zip"
        mtime_before = zip_path.stat().st_mtime if zip_path.exists() else None
        api.dataset_download_files(dataset, path=str(raw_dir), force=force, quiet=True)
    except (requests.exceptions.RequestException, OSError) as exc:
        if isinstance(exc, FileNotFoundError | PermissionError):
            raise
        raise TransientError(f"Kaggle download failed: {exc}") from exc

    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(raw_dir)
        names = zf.namelist()

    manifest = raw_dir / "MANIFEST.json"
    previous = json.loads(manifest.read_text()) if manifest.exists() else {"files": {}}
    files, changed = {}, []
    for name in names:
        path = raw_dir / name
        digest = sha256_of(path)
        files[name] = {"bytes": path.stat().st_size, "sha256": digest}
        if previous["files"].get(name, {}).get("sha256") != digest:
            changed.append(name)
    manifest.write_text(
        json.dumps(
            {
                "dataset": dataset,
                "licences": licences,
                "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "files": files,
            },
            indent=2,
        )
        + "\n"
    )
    return DownloadResult(
        dataset=dataset,
        licences=licences,
        files=files,
        changed=changed,
        downloaded=zip_path.stat().st_mtime != mtime_before,
    )
