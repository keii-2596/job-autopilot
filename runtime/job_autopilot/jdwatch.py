from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .db import Ledger


def find_jdwatch() -> str:
    configured = os.environ.get("JDWATCH_BIN")
    candidates = [
        configured,
        shutil.which("jdwatch"),
        str(Path.home() / "Library" / "pnpm" / "bin" / "jdwatch"),
        str(Path.home() / ".local" / "bin" / "jdwatch"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).expanduser().is_file():
            return str(Path(candidate).expanduser())
    raise FileNotFoundError(
        "jdwatch CLI not found; install @jdwatch/cli or set JDWATCH_BIN"
    )


def _run(args: list[str], timeout: int = 90) -> dict[str, Any]:
    binary = find_jdwatch()
    env = os.environ.copy()
    node_dir = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node" / "bin"
    env["PATH"] = f"{node_dir}:{env.get('PATH', '')}"
    process = subprocess.run(
        [binary, *args, "--json"],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    if process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip() or "unknown jdwatch error"
        raise RuntimeError(detail)
    return json.loads(process.stdout)


def auth_status() -> dict[str, Any]:
    return _run(["auth", "status"], timeout=30)


def get_job(job_id: int | str) -> dict[str, Any]:
    return _run(["jobs", "get", str(job_id)]).get("job", {})


def discover(
    ledger: Ledger,
    *,
    keyword: str = "",
    company: str = "",
    location: str = "",
    family: str = "",
    category: str = "",
    recruitment_type: str = "campus",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    args = ["jobs", "search"]
    options = {
        "--keyword": keyword,
        "--company": company,
        "--location": location,
        "--family": family,
        "--category": category,
        "--rt": recruitment_type,
    }
    for flag, value in options.items():
        if value:
            args.extend([flag, value])
    args.extend(["--page", str(page), "--page-size", str(max(1, min(page_size, 50)))])
    payload = _run(args)
    added = 0
    duplicates = 0
    applications = []
    for job in payload.get("jobs", []):
        url = job.get("url") or ""
        if not url:
            continue
        result = ledger.record_application(
            title=job.get("title") or "Untitled job",
            company=job.get("company_display_name") or job.get("company") or "Unknown company",
            url=url,
            jdwatch_id=job.get("id"),
            locations=job.get("work_locations") or [],
            recruitment_channel=job.get("recruitment_channel") or job.get("recruitment_type"),
            status="discovered",
        )
        if result.get("duplicate"):
            duplicates += 1
        else:
            added += 1
        applications.append(result)
    return {
        "query_total": payload.get("total", len(payload.get("jobs", []))),
        "page": payload.get("page", page),
        "added": added,
        "duplicates": duplicates,
        "applications": applications,
    }
