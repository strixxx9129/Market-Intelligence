"""Export the API's OpenAPI schema to frontend/openapi.json (input for `pnpm gen:api`).

    python scripts/export_openapi.py           # (re)write the snapshot
    python scripts/export_openapi.py --check   # exit 1 if the snapshot is out of date (CI)

Run via `make openapi`. The frontend's TypeScript client is generated from this file. CI fails
when an API change isn't exported, and the frontend job fails when the generated client
doesn't match the snapshot, so the backend and frontend can't silently disagree.
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = REPO_ROOT / "frontend" / "openapi.json"
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))

from marketlens.main import create_app  # noqa: E402


def render() -> str:
    schema = create_app().openapi()
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the snapshot is stale")
    args = parser.parse_args()

    content = render()
    if args.check:
        current = SNAPSHOT.read_text(encoding="utf-8") if SNAPSHOT.exists() else ""
        if current != content:
            print(f"{SNAPSHOT.relative_to(REPO_ROOT)} is out of date: run `make openapi`")
            return 1
        print("OpenAPI snapshot is up to date")
        return 0
    SNAPSHOT.write_text(content, encoding="utf-8", newline="\n")
    print(f"wrote {SNAPSHOT.relative_to(REPO_ROOT)} ({len(json.loads(content)['paths'])} paths)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
