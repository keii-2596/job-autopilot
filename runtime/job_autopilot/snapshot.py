from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .db import Ledger


def bundled_snapshot_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "jobs.json"


def import_snapshot(ledger: "Ledger", path: str | Path | None = None) -> dict[str, Any]:
    snapshot_path = Path(path).expanduser() if path else bundled_snapshot_path()
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    jobs = payload.get("jobs") if isinstance(payload, dict) else payload
    if not isinstance(jobs, list) or not jobs:
        raise ValueError("职位快照必须包含非空 jobs 数组")
    if len(jobs) > 50_000:
        raise ValueError("职位快照记录过多")
    scoped_jobs = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        graduates = [str(item) for item in job.get("target_graduates") or []]
        batches = [str(item) for item in job.get("recruitment_batches") or []]
        if "2027届" not in graduates or not any("秋招" in item for item in batches):
            continue
        scoped_jobs.append({**job, "source": "baigua", "notes": "公开职位快照"})
    counts = ledger.upsert_source_jobs(scoped_jobs)
    result = {
        "source": "baigua",
        "mode": "public_snapshot",
        "status": "completed",
        "complete": True,
        "pages": 0,
        "scanned": len(scoped_jobs),
        "remote_total": len(jobs),
        **counts,
    }
    ledger.set_source_sync_state("baigua", result)
    return result


def seed_bundled_snapshot_if_empty(ledger: "Ledger") -> dict[str, Any] | None:
    path = bundled_snapshot_path()
    if not path.is_file() or ledger.source_jobs_summary()["total"]:
        return None
    return import_snapshot(ledger, path)
