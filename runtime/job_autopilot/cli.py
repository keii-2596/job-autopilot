from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .adb import connected_devices, wait_for_otp
from .db import (
    ACTIVITY_SOURCES,
    ACTIVITY_STAGES,
    ACTIVITY_STATES,
    Ledger,
    VALID_STATUSES,
    database_path,
)
from .jdwatch import auth_status, discover, get_job
from .resume import extract_resume_text
from .snapshot import bundled_snapshot_path, import_snapshot


def emit(value: Any, as_json: bool = True) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        print(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="job-autopilot",
        description="Local job discovery, application tracking, and OTP helper.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--db", help="Override SQLite database path")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Initialize the local ledger")
    sub.add_parser("status", help="Check local ledger, JDWatch, and ADB")

    discover_parser = sub.add_parser("discover", help="Discover jobs through JDWatch and remember them")
    discover_parser.add_argument("--keyword", default="")
    discover_parser.add_argument("--company", default="")
    discover_parser.add_argument("--location", default="")
    discover_parser.add_argument("--family", default="")
    discover_parser.add_argument("--category", default="")
    discover_parser.add_argument("--rt", default="campus", choices=["campus", "intern", "social"])
    discover_parser.add_argument("--page", type=int, default=1)
    discover_parser.add_argument("--page-size", type=int, default=20)

    source_jobs = sub.add_parser("source-jobs", help="List locally synced source jobs")
    source_jobs.add_argument("--query", default="")
    source_jobs.add_argument("--location", default="")
    source_jobs.add_argument("--limit", type=int, default=200)
    source_jobs.add_argument("--offset", type=int, default=0)
    sub.add_parser("source-jobs-summary", help="Show the synced source job summary")
    snapshot_import = sub.add_parser("source-jobs-import", help="Import a public job snapshot")
    snapshot_import.add_argument("path", type=Path, nargs="?", default=bundled_snapshot_path())
    source_queue = sub.add_parser("source-job-queue", help="Add a synced source job to the application queue")
    source_queue.add_argument("job_id", type=int)

    get_parser = sub.add_parser("get-job", help="Read one verified JDWatch job")
    get_parser.add_argument("job_id")

    record = sub.add_parser("record", help="Add one job/application to the ledger")
    record.add_argument("--title", required=True)
    record.add_argument("--company", required=True)
    record.add_argument("--url", required=True)
    record.add_argument("--jdwatch-id", type=int)
    record.add_argument("--location", action="append", default=[])
    record.add_argument("--channel")
    record.add_argument("--status", default="queued", choices=sorted(VALID_STATUSES))
    record.add_argument("--notes", default="")

    check = sub.add_parser("check", help="Check whether a job was already recorded")
    check.add_argument("--url", default="")
    check.add_argument("--jdwatch-id", type=int)

    update = sub.add_parser("update", help="Update an application status")
    update.add_argument("application_id", type=int)
    update.add_argument("--status", choices=sorted(VALID_STATUSES))
    update.add_argument("--notes")
    update.add_argument("--confirmation-ref")
    update.add_argument("--actual-title")
    update.add_argument("--actual-url")
    update.add_argument("--actual-location", action="append")
    archive = update.add_mutually_exclusive_group()
    archive.add_argument("--archive", dest="archived", action="store_true", default=None)
    archive.add_argument("--unarchive", dest="archived", action="store_false")

    listing = sub.add_parser("list", help="List applications")
    listing.add_argument("--status", choices=sorted(VALID_STATUSES))
    listing.add_argument("--limit", type=int, default=200)

    events = sub.add_parser("events", help="Show an application's audit trail")
    events.add_argument("application_id", type=int)

    sub.add_parser("sites", help="List remembered recruiting websites")
    sub.add_parser("profile", help="Show the local applicant profile")

    profile_set = sub.add_parser("profile-set", help="Merge a JSON file into the applicant profile")
    profile_set.add_argument("json_file", type=Path)

    field_set = sub.add_parser("profile-field-set", help="Add or update one confirmed dynamic field")
    field_set.add_argument("json_file", type=Path)

    resume_request = sub.add_parser("resume-request", help="Queue a resume for AI profile extraction")
    resume_request.add_argument("resume_path")
    resume_extract = sub.add_parser("resume-extract", help="Extract resume text for semantic AI parsing")
    resume_extract.add_argument("resume_path")
    resume_extract.add_argument("--max-chars", type=int, default=60_000)
    sub.add_parser("resume-import-status", help="Show pending resume suggestions")
    resume_complete = sub.add_parser("resume-import-complete", help="Save AI-generated profile suggestions")
    resume_complete.add_argument("json_file", type=Path)

    sub.add_parser("run-state", help="Show dashboard-requested automation state")
    run_start = sub.add_parser("run-start", help="Request an AI-managed workflow")
    run_start.add_argument(
        "--action",
        choices=["discover_and_apply", "process_queue", "parse_resume"],
        default="process_queue",
    )
    run_start.add_argument("--keyword", action="append", default=[])
    run_start.add_argument("--location", action="append", default=[])
    run_start.add_argument("--recruitment-type", default="campus")
    sub.add_parser("run-claim", help="Mark the requested workflow as running")
    run_stop = sub.add_parser("run-stop", help="Pause the requested workflow")
    run_stop.add_argument("--message", default="已暂停")
    run_complete = sub.add_parser("run-complete", help="Finish the requested workflow")
    run_complete.add_argument("--failed", action="store_true")
    run_complete.add_argument("--message", default="")

    sub.add_parser("activity", help="Show the shared Job Autopilot activity stream")
    activity_start = sub.add_parser(
        "activity-start", help="Register a Job Autopilot run from any Codex conversation"
    )
    activity_start.add_argument("--action", default="job_autopilot")
    activity_start.add_argument(
        "--source", choices=sorted(ACTIVITY_SOURCES), default="codex_conversation"
    )
    activity_start.add_argument("--label", default="Codex 对话中的 Job Autopilot 任务")
    activity_start.add_argument("--message", default="正在准备任务")
    activity_start.add_argument("--total", type=int, default=0)

    activity_update = sub.add_parser(
        "activity-update", help="Publish a safe progress update for one run"
    )
    activity_update.add_argument("run_id")
    activity_update.add_argument("--state", choices=sorted(ACTIVITY_STATES))
    activity_update.add_argument("--stage", choices=sorted(ACTIVITY_STAGES))
    activity_update.add_argument("--message")
    activity_update.add_argument("--application-id", type=int)
    activity_update.add_argument("--current", type=int)
    activity_update.add_argument("--total", type=int)

    activity_complete = sub.add_parser(
        "activity-complete", help="Finish one shared activity run"
    )
    activity_complete.add_argument("run_id")
    activity_complete.add_argument("--failed", action="store_true")
    activity_complete.add_argument("--message", default="")

    activity_events = sub.add_parser("activity-events", help="Show events for one run")
    activity_events.add_argument("run_id")
    activity_events.add_argument("--limit", type=int, default=30)

    settings = sub.add_parser("settings", help="Show workflow settings")
    settings_set = sub.add_parser("settings-set", help="Update submission policy")
    settings_set.add_argument("--submission-mode", choices=["review", "automatic"])
    settings_set.add_argument("--allowed-domain", action="append")
    settings_set.add_argument("--codex-project-path")
    policy_check = sub.add_parser("policy-check", help="Check automatic advance rules for a website")
    policy_check.add_argument("url")

    site_login = sub.add_parser("site-login", help="Remember a successful website login")
    site_login.add_argument("--url", required=True)
    site_login.add_argument("--method", default="phone_otp")

    otp = sub.add_parser("otp-wait", help="Wait for a new SMS code without storing the SMS body")
    otp.add_argument("--after-ms", type=int, default=0)
    otp.add_argument("--timeout", type=int, default=120)
    otp.add_argument("--sender-contains", default="")

    serve = sub.add_parser("serve", help="Run the local dashboard")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ledger = Ledger(args.db) if args.db else Ledger()
    try:
        if args.command == "init":
            emit({"initialized": True, "database": str(ledger.path)})
        elif args.command == "status":
            result: dict[str, Any] = {
                "version": __version__,
                "database": str(ledger.path),
                "summary": ledger.summary(),
                "activity": ledger.activity_snapshot(),
            }
            try:
                result["jdwatch"] = auth_status()
            except Exception as error:
                result["jdwatch"] = {"available": False, "error": str(error)}
            try:
                result["adb"] = {"available": True, "devices": connected_devices()}
            except Exception as error:
                result["adb"] = {"available": False, "error": str(error)}
            emit(result)
        elif args.command == "discover":
            status = auth_status()
            if status.get("token_valid") is not True:
                raise RuntimeError(status.get("hint") or "JDWatch authentication is not valid")
            emit(
                discover(
                    ledger,
                    keyword=args.keyword,
                    company=args.company,
                    location=args.location,
                    family=args.family,
                    category=args.category,
                    recruitment_type=args.rt,
                    page=args.page,
                    page_size=args.page_size,
                )
            )
        elif args.command == "source-jobs":
            emit(
                ledger.list_source_jobs(
                    query=args.query,
                    location=args.location,
                    limit=args.limit,
                    offset=args.offset,
                )
            )
        elif args.command == "source-jobs-summary":
            emit(ledger.source_jobs_summary())
        elif args.command == "source-jobs-import":
            emit(import_snapshot(ledger, args.path))
        elif args.command == "source-job-queue":
            emit(ledger.queue_source_job(args.job_id))
        elif args.command == "get-job":
            emit(get_job(args.job_id))
        elif args.command == "record":
            emit(
                ledger.record_application(
                    title=args.title,
                    company=args.company,
                    url=args.url,
                    jdwatch_id=args.jdwatch_id,
                    locations=args.location,
                    recruitment_channel=args.channel,
                    status=args.status,
                    notes=args.notes,
                )
            )
        elif args.command == "check":
            emit(ledger.check_duplicate(args.url, args.jdwatch_id))
        elif args.command == "update":
            emit(
                ledger.update_application(
                    args.application_id,
                    status=args.status,
                    notes=args.notes,
                    confirmation_ref=args.confirmation_ref,
                    actual_title=args.actual_title, actual_url=args.actual_url,
                    actual_locations=args.actual_location, archived=args.archived,
                )
            )
        elif args.command == "list":
            emit(ledger.list_applications(args.status, args.limit))
        elif args.command == "events":
            emit(ledger.events(args.application_id))
        elif args.command == "sites":
            emit(ledger.list_sites())
        elif args.command == "profile":
            emit(ledger.profile())
        elif args.command == "profile-set":
            with args.json_file.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                raise ValueError("profile JSON must be an object")
            emit(ledger.update_profile(data))
        elif args.command == "profile-field-set":
            with args.json_file.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                raise ValueError("profile field JSON must be an object")
            emit(ledger.upsert_profile_field(data))
        elif args.command == "resume-request":
            emit(ledger.request_profile_import(args.resume_path))
        elif args.command == "resume-extract":
            emit(extract_resume_text(args.resume_path, args.max_chars))
        elif args.command == "resume-import-status":
            emit(ledger.profile_import())
        elif args.command == "resume-import-complete":
            with args.json_file.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            suggestions = data.get("suggestions") if isinstance(data, dict) else data
            if not isinstance(suggestions, list):
                raise ValueError("resume suggestion JSON must be a list or contain suggestions")
            message = data.get("message", "") if isinstance(data, dict) else ""
            emit(ledger.complete_profile_import(suggestions, str(message)))
        elif args.command == "run-state":
            emit(ledger.automation())
        elif args.command == "run-start":
            emit(
                ledger.request_automation(
                    args.action,
                    {
                        "keywords": args.keyword,
                        "locations": args.location,
                        "recruitment_type": args.recruitment_type,
                    },
                )
            )
        elif args.command == "run-claim":
            emit(ledger.set_automation_state("running", "Codex 正在处理"))
        elif args.command == "run-stop":
            emit(ledger.set_automation_state("paused", args.message))
        elif args.command == "run-complete":
            emit(
                ledger.set_automation_state(
                    "failed" if args.failed else "completed",
                    args.message or ("执行失败" if args.failed else "本轮已完成"),
                )
            )
        elif args.command == "activity":
            emit(ledger.activity_snapshot())
        elif args.command == "activity-start":
            emit(
                ledger.start_activity(
                    action=args.action,
                    source=args.source,
                    label=args.label,
                    message=args.message,
                    progress_total=args.total,
                )
            )
        elif args.command == "activity-update":
            emit(
                ledger.update_activity(
                    args.run_id,
                    state=args.state,
                    stage=args.stage,
                    message=args.message,
                    application_id=args.application_id,
                    progress_current=args.current,
                    progress_total=args.total,
                )
            )
        elif args.command == "activity-complete":
            failed = bool(args.failed)
            emit(
                ledger.update_activity(
                    args.run_id,
                    state="failed" if failed else "completed",
                    stage="failed" if failed else "completed",
                    message=args.message or ("任务失败" if failed else "本轮任务已完成"),
                )
            )
        elif args.command == "activity-events":
            emit(ledger.activity_events(args.run_id, args.limit))
        elif args.command == "settings":
            emit(ledger.settings())
        elif args.command == "settings-set":
            changes: dict[str, Any] = {}
            if args.submission_mode:
                changes["submission_mode"] = args.submission_mode
            if args.allowed_domain is not None:
                changes["allowed_domains"] = args.allowed_domain
            if args.codex_project_path is not None:
                changes["codex_project_path"] = args.codex_project_path
            emit(ledger.update_settings(changes))
        elif args.command == "policy-check":
            emit(ledger.check_domain_policy(args.url))
        elif args.command == "site-login":
            ledger.mark_site_login(args.url, args.method)
            emit({"saved": True, "url": args.url, "method": args.method})
        elif args.command == "otp-wait":
            after_ms = args.after_ms or int(time.time() * 1000)
            emit(
                wait_for_otp(
                    after_ms=after_ms,
                    timeout_seconds=args.timeout,
                    sender_contains=args.sender_contains,
                )
            )
        elif args.command == "serve":
            from .web import serve

            serve(ledger, args.host, args.port)
        else:
            raise ValueError(f"unsupported command: {args.command}")
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print(json.dumps({"success": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
