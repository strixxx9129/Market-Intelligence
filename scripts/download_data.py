"""Download the Kaggle "mczielinski/bitcoin-historical-data" dataset into data/raw/.

Usage (inside the eda container):  make data   |   python scripts/download_data.py [--force]

Auth: the Kaggle client reads ~/.kaggle/kaggle.json (legacy key), ~/.kaggle/access_token, or the
KAGGLE_API_TOKEN env var. In docker compose, ~/.kaggle is mounted read-only at /root/.kaggle.

Idempotent: the zip is kept, so Kaggle only re-downloads when the remote dataset is newer.
For every extracted file we print size + SHA-256 and record them in data/raw/MANIFEST.json,
so later phases can tell whether the raw input actually changed (rule 2: file hashes).
"""

import argparse
import hashlib
import json
import sys
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DATASET = "mczielinski/bitcoin-historical-data"
REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = REPO_ROOT / "data" / "raw"
MANIFEST = RAW_DIR / "MANIFEST.json"


def sha256_of(path: Path, chunk_size: int = 1 << 20) -> str:
    """Stream the file in 1 MiB chunks so multi-GB files never load fully into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def human_size(n_bytes: int) -> str:
    size = float(n_bytes)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:,.1f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


def fetch_licences(api: Any, dataset: str) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        meta_path = Path(api.dataset_metadata(dataset, tmp))
        meta = json.loads(meta_path.read_text())
    meta = meta.get("info", meta)  # kaggle>=2 nests the fields under "info"
    return [lic.get("name", "?") for lic in meta.get("licenses", [])]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", default=DATASET)
    parser.add_argument("--force", action="store_true", help="re-download even if up to date")
    args = parser.parse_args()

    # Imported here: `import kaggle` tries to authenticate immediately.
    from kaggle.api.kaggle_api_extended import KaggleApi

    api = KaggleApi()
    api.authenticate()

    licences = fetch_licences(api, args.dataset)
    print(f"Dataset:  {args.dataset}")
    print(f"Licence:  {', '.join(licences) or 'UNKNOWN - check before redistributing'}")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    api.dataset_download_files(args.dataset, path=str(RAW_DIR), force=args.force, quiet=False)

    zip_path = RAW_DIR / f"{args.dataset.split('/')[1]}.zip"
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(RAW_DIR)
        names = zf.namelist()

    previous = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {"files": {}}
    files: dict[str, dict[str, Any]] = {}
    print()
    for name in names:
        path = RAW_DIR / name
        digest = sha256_of(path)
        size = path.stat().st_size
        changed = previous["files"].get(name, {}).get("sha256") != digest
        files[name] = {"bytes": size, "sha256": digest}
        print(f"{name}\n  size:   {human_size(size)} ({size:,} bytes)")
        print(f"  sha256: {digest}  {'(new/changed)' if changed else '(unchanged)'}")

    MANIFEST.write_text(
        json.dumps(
            {
                "dataset": args.dataset,
                "licences": licences,
                "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "files": files,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"\nManifest written to {MANIFEST.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
