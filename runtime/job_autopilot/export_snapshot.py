from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .db import Ledger


def export_snapshot(output: str | Path) -> dict[str, Any]:
    target = Path(output).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    ledger = Ledger()
    jobs: list[dict[str, Any]] = []
    offset = 0
    while True:
        batch = ledger.list_source_jobs(source="baigua", limit=1000, offset=offset)
        if not batch:
            break
        for job in batch:
            graduates = {str(item) for item in job.get("target_graduates") or []}
            batches = {str(item) for item in job.get("recruitment_batches") or []}
            if "2027届" in graduates and any("秋招" in item for item in batches):
                jobs.append(job)
        if len(batch) < 1000:
            break
        offset += len(batch)

    payload = json.dumps(jobs, ensure_ascii=False, indent=2) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        os.close(descriptor)
        Path(temporary).unlink(missing_ok=True)
        raise
    return {"output": str(target), "jobs": len(jobs)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Export the public Baigua job snapshot")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(export_snapshot(args.output), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
