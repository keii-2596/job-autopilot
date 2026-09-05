from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any


OTP_PATTERN = re.compile(r"(?<!\d)(\d{4,8})(?!\d)")


def find_adb() -> str:
    configured = os.environ.get("ADB_BIN")
    candidates = [
        configured,
        shutil.which("adb"),
        str(Path.home() / "Library" / "Android" / "sdk" / "platform-tools" / "adb"),
        "/opt/homebrew/bin/adb",
        "/usr/local/bin/adb",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).expanduser().is_file():
            return str(Path(candidate).expanduser())
    raise FileNotFoundError("adb not found; install Android platform-tools or set ADB_BIN")


def _adb(args: list[str], timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [find_adb(), *args], capture_output=True, text=True, timeout=timeout
    )


def connected_devices() -> list[dict[str, str]]:
    process = _adb(["devices", "-l"])
    if process.returncode != 0:
        raise RuntimeError(process.stderr.strip() or "unable to start adb")
    devices = []
    for line in process.stdout.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        item = {"serial": parts[0], "state": parts[1] if len(parts) > 1 else "unknown"}
        for part in parts[2:]:
            if ":" in part:
                key, value = part.split(":", 1)
                item[key] = value
        devices.append(item)
    return devices


def _latest_sms() -> dict[str, Any] | None:
    process = _adb(
        [
            "shell",
            "content",
            "query",
            "--uri",
            "content://sms/inbox",
            "--projection",
            "_id:address:date:body",
            "--sort",
            "date DESC",
        ],
        timeout=30,
    )
    if process.returncode != 0:
        raise PermissionError(process.stderr.strip() or process.stdout.strip())
    first = next((line for line in process.stdout.splitlines() if line.startswith("Row:")), "")
    if not first:
        return None
    def field(name: str, next_name: str | None = None) -> str:
        start = first.find(f"{name}=")
        if start < 0:
            return ""
        start += len(name) + 1
        if next_name:
            end = first.find(f", {next_name}=", start)
            return first[start:end if end >= 0 else None]
        return first[start:]
    return {
        "id": field("_id", "address"),
        "address": field("address", "date"),
        "date": int(field("date", "body") or 0),
        "body": field("body"),
    }


def wait_for_otp(
    *,
    after_ms: int,
    timeout_seconds: int = 120,
    sender_contains: str = "",
    poll_seconds: float = 2.0,
) -> dict[str, Any]:
    if timeout_seconds < 1 or timeout_seconds > 300:
        raise ValueError("timeout_seconds must be between 1 and 300")
    deadline = time.monotonic() + timeout_seconds
    sender_filter = sender_contains.casefold().strip()
    seen_id = None
    while time.monotonic() < deadline:
        message = _latest_sms()
        if message:
            seen_id = message["id"]
            sender_matches = not sender_filter or sender_filter in message["address"].casefold()
            if message["date"] >= after_ms and sender_matches:
                match = OTP_PATTERN.search(message["body"])
                if match:
                    return {
                        "received": True,
                        "code": match.group(1),
                        "sender": message["address"],
                        "received_at_ms": message["date"],
                        "message_id": message["id"],
                    }
        time.sleep(poll_seconds)
    return {"received": False, "last_seen_message_id": seen_id, "timeout_seconds": timeout_seconds}
