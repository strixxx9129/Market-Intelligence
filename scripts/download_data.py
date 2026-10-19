"""Download the Kaggle "mczielinski/bitcoin-historical-data" dataset into data/raw/.

Usage (inside the eda container):  make data   |   python scripts/download_data.py [--force]

Thin CLI around `marketlens.ingestion.kaggle_download` (the same code the Celery worker runs
daily). Auth: ~/.kaggle/kaggle.json, ~/.kaggle/access_token, or KAGGLE_API_TOKEN; in docker
compose ~/.kaggle is mounted read-only at /root/.kaggle.

Idempotent: the zip is kept, so Kaggle only re-downloads when the remote dataset is newer.
Every extracted file's size and SHA-256 is printed and recorded in data/raw/MANIFEST.json.
"""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))  # current source, not a stale install

from marketlens.ingestion.kaggle_download import download_dataset  # noqa: E402

DATASET = "mczielinski/bitcoin-historical-data"
RAW_DIR = REPO_ROOT / "data" / "raw"


def human_size(n_bytes: int) -> str:
    size = float(n_bytes)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:,.1f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", default=DATASET)
    parser.add_argument("--force", action="store_true", help="re-download even if up to date")
    args = parser.parse_args()

    result = download_dataset(args.dataset, RAW_DIR, force=args.force)
    print(f"Dataset:  {result.dataset}")
    print(f"Licence:  {', '.join(result.licences) or 'UNKNOWN - check before redistributing'}")
    print(f"Download: {'new copy' if result.downloaded else 'local copy is up to date'}\n")
    for name, info in result.files.items():
        status = "(new/changed)" if name in result.changed else "(unchanged)"
        print(f"{name}\n  size:   {human_size(info['bytes'])} ({info['bytes']:,} bytes)")
        print(f"  sha256: {info['sha256']}  {status}")
    print(f"\nManifest written to {(RAW_DIR / 'MANIFEST.json').relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
