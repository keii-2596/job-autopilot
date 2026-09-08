from __future__ import annotations

import json
import hashlib
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .policy import check_domain, normalize_rule


VALID_STATUSES = {
    "discovered",
    "queued",
    "opened",
    "auth_required",
    "form_filling",
    "ready_for_review",
    "submitted",
    "failed",
    "withdrawn",
    "ignored",
}

ACTIVITY_STATES = {"requested", "running", "waiting", "paused", "completed", "failed"}
ACTIVITY_STAGES = {
    "preparing",
    "discovering",
    "screening",
    "queueing",
    "opening",
    "authenticating",
    "filling",
    "uploading",
    "reviewing",
    "awaiting_confirmation",
    "submitting",
    "completed",
    "failed",
}
ACTIVITY_SOURCES = {"web_app_server", "mcp_conversation", "codex_conversation"}

JOB_DIRECTION_TERMS = {
    "tech": [
        "Agent", "智能体", "人工智能", "AI", "AIGC", "大模型", "LLM", "算法", "计算机",
        "软件", "开发", "研发", "后端", "服务端", "Java", "C++", "Python", "Go", "数据",
        "云计算", "平台", "系统", "网络", "测试", "客户端",
    ],
    "agent": ["Agent", "智能体", "人工智能", "AI", "AIGC", "大模型", "LLM"],
    "development": [
        "开发", "研发", "后端", "服务端", "软件", "Java", "C++", "Python", "Go", "客户端",
        "测试开发", "工程师",
    ],
    "computer": [
        "计算机", "软件", "网络", "信息安全", "数据", "算法", "人工智能", "AI", "通信",
        "云计算", "系统",
    ],
    "backend": ["后端", "服务端", "后台开发", "Java", "Golang", "Python开发", "C++开发"],
    "frontend": ["前端", "Web开发", "前端开发", "React", "Vue", "JavaScript", "TypeScript"],
    "fullstack": ["全栈", "全端", "Full Stack", "Fullstack"],
    "algorithm": ["算法", "机器学习", "深度学习", "自然语言", "NLP", "计算机视觉", "推荐系统"],
    "data": ["数据开发", "数据分析", "数据科学", "数据工程", "大数据", "数据仓库", "数据库", "数仓"],
    "mobile": ["客户端", "移动开发", "Android", "iOS开发", "鸿蒙", "HarmonyOS", "Flutter"],
    "qa": ["测试开发", "软件测试", "质量保障", "自动化测试", "测试工程师"],
    "infra": ["运维", "SRE", "DevOps", "云计算", "云原生", "基础架构", "分布式", "中间件"],
    "security": ["信息安全", "网络安全", "安全开发", "安全工程师", "渗透测试", "密码学"],
    "embedded": ["嵌入式", "固件", "驱动开发", "单片机", "物联网", "机器人", "自动驾驶"],
    "hardware": ["硬件", "芯片", "集成电路", "半导体", "FPGA", "IC设计", "数字电路"],
    "game": ["游戏", "Unity", "Unreal", "引擎开发", "图形学", "图形渲染"],
    "product": ["产品经理", "产品策划", "产品运营", "用户研究", "交互设计", "用户体验"],
}

TRACKING_QUERY_KEYS = {
    "from",
    "source",
    "ref",
    "refer",
    "campaign",
    "tracking",
    "track",
    "spm",
}

DEFAULT_SETTINGS = {
    "submission_mode": "review",
    "allowed_domains": [],
    "codex_project_path": "",
    "login_method": "phone_otp",
    "autofill_profile_phone": True,
    "read_otp_via_adb": True,
    "auto_accept_standard_agreements": True,
    "solve_captcha_automatically": True,
    "job_preferences": {
        "keywords": ["Agent", "后端"],
        "locations": [],
        "recruitment_types": ["campus"],
        "excluded_keywords": [], "excluded_companies": [],
        "graduation_year": "", "employment_type": "",
        "prefer_phone_login": True, "skip_wechat_only": False, "prefer_known_companies": False,
    },
}

DEFAULT_AUTOMATION = {
    "state": "idle",
    "action": "process_queue",
    "request_id": "",
    "criteria": {},
    "message": "",
    "requested_at": None,
    "updated_at": None,
}

DEFAULT_CODEX_RUNTIME = {
    "available": None,
    "state": "idle",
    "thread_id": "",
    "turn_id": "",
    "message": "",
    "last_agent_message": "",
    "pending_request": None,
    "updated_at": None,
}

BASE_PROFILE_KEYS = {
    "name",
    "phone",
    "email",
    "city",
    "school",
    "degree",
    "major",
    "graduation_year",
    "resume_path",
    "portfolio_url",
    "github_url",
    "preferred_locations",
    "preferred_roles",
    "work_authorization",
    "earliest_start_date",
    "notes",
}

FORBIDDEN_PROFILE_TERMS = {
    "password",
    "passwd",
    "otp",
    "one-time code",
    "verification code",
    "验证码",
    "密码",
    "银行卡",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def state_dir() -> Path:
    configured = os.environ.get("JOB_AUTOPILOT_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".job-autopilot"


def database_path() -> Path:
    return state_dir() / "state.db"


def canonicalize_url(raw_url: str) -> str:
    value = (raw_url or "").strip()
    if not value:
        return ""
    parts = urlsplit(value if "://" in value else f"https://{value}")
    scheme = (parts.scheme or "https").lower()
    hostname = (parts.hostname or "").lower()
    port = parts.port
    if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        netloc = f"{hostname}:{port}"
    else:
        netloc = hostname
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lower = key.lower()
        if lower.startswith("utm_") or lower in TRACKING_QUERY_KEYS:
            continue
        query.append((key, value))
    query.sort(key=lambda item: (item[0].lower(), item[1]))
    # Many Chinese campus recruiting sites are single-page apps whose hash route
    # contains the actual job identity. Keep path-like routes, but discard ordinary
    # anchors such as #apply or #top.
    fragment = parts.fragment.strip()
    identity_fragment = fragment if fragment.startswith("/") and len(fragment) > 1 else ""
    return urlunsplit((scheme, netloc, path, urlencode(query), identity_fragment))


def application_key(url: str, jdwatch_id: int | str | None = None) -> str:
    if jdwatch_id not in (None, ""):
        return f"jdwatch:{int(jdwatch_id)}"
    canonical = canonicalize_url(url)
    if not canonical:
        raise ValueError("url or jdwatch_id is required")
    return f"url:{canonical}"


class Ledger:
    def __init__(self, path: Path | str | None = None):
        use_bundled_snapshot = path is None
        self.path = Path(path) if path else database_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()
        if use_bundled_snapshot:
            from .snapshot import seed_bundled_snapshot_if_empty

            seed_bundled_snapshot_if_empty(self)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS applications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    application_key TEXT NOT NULL UNIQUE,
                    jdwatch_id INTEGER,
                    title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    url TEXT NOT NULL,
                    canonical_url TEXT NOT NULL,
                    site_domain TEXT NOT NULL,
                    locations_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL,
                    recruitment_channel TEXT,
                    notes TEXT NOT NULL DEFAULT '',
                    confirmation_ref TEXT NOT NULL DEFAULT '',
                    discovered_at TEXT NOT NULL,
                    started_at TEXT,
                    submitted_at TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sites (
                    domain TEXT PRIMARY KEY,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    last_login_at TEXT,
                    login_method TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    application_id INTEGER,
                    event_type TEXT NOT NULL,
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(application_id) REFERENCES applications(id)
                );

                CREATE TABLE IF NOT EXISTS activity_runs (
                    run_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL,
                    state TEXT NOT NULL,
                    action TEXT NOT NULL,
                    label TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '',
                    application_id INTEGER,
                    progress_current INTEGER NOT NULL DEFAULT 0,
                    progress_total INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(application_id) REFERENCES applications(id)
                );

                CREATE TABLE IF NOT EXISTS activity_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '',
                    application_id INTEGER,
                    progress_current INTEGER NOT NULL DEFAULT 0,
                    progress_total INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES activity_runs(run_id),
                    FOREIGN KEY(application_id) REFERENCES applications(id)
                );

                CREATE INDEX IF NOT EXISTS idx_activity_runs_updated
                    ON activity_runs(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_activity_events_run
                    ON activity_events(run_id, id DESC);

                CREATE TABLE IF NOT EXISTS source_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    source_numeric_id INTEGER,
                    source_uuid TEXT NOT NULL DEFAULT '',
                    company_name TEXT NOT NULL,
                    company_type TEXT NOT NULL DEFAULT '',
                    industry TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL,
                    title_items_json TEXT NOT NULL DEFAULT '[]',
                    locations_json TEXT NOT NULL DEFAULT '[]',
                    target_graduates_json TEXT NOT NULL DEFAULT '[]',
                    education_levels_json TEXT NOT NULL DEFAULT '[]',
                    recruitment_batches_json TEXT NOT NULL DEFAULT '[]',
                    deadline_text TEXT NOT NULL DEFAULT '',
                    deadline_date TEXT,
                    deadline_status TEXT NOT NULL DEFAULT '',
                    announcement_source TEXT NOT NULL DEFAULT '',
                    announcement_url TEXT NOT NULL DEFAULT '',
                    apply_url TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    major_requirements TEXT NOT NULL DEFAULT '',
                    written_test TEXT NOT NULL DEFAULT '',
                    source_updated_at TEXT,
                    content_hash TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(source, source_key)
                );

                CREATE INDEX IF NOT EXISTS idx_source_jobs_source_updated
                    ON source_jobs(source, source_updated_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_source_jobs_company
                    ON source_jobs(source, company_name);

                CREATE TABLE IF NOT EXISTS kv (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(applications)")}
            for name, definition in {
                "actual_title": "TEXT NOT NULL DEFAULT ''",
                "actual_url": "TEXT NOT NULL DEFAULT ''",
                "actual_locations_json": "TEXT NOT NULL DEFAULT '[]'",
                "archived": "INTEGER NOT NULL DEFAULT 0",
            }.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE applications ADD COLUMN {name} {definition}")
            for key, value in (
                ("profile", {}),
                ("settings", DEFAULT_SETTINGS),
                ("automation", DEFAULT_AUTOMATION),
                ("codex_runtime", DEFAULT_CODEX_RUNTIME),
                ("profile_import", {"status": "idle", "suggestions": []}),
            ):
                db.execute(
                    "INSERT OR IGNORE INTO kv(key, value_json, updated_at) VALUES (?, ?, ?)",
                    (key, json.dumps(value, ensure_ascii=False), utc_now()),
                )

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        if "locations_json" in result:
            result["locations"] = json.loads(result.pop("locations_json") or "[]")
        if "actual_locations_json" in result:
            result["actual_locations"] = json.loads(result.pop("actual_locations_json") or "[]")
        if "archived" in result:
            result["archived"] = bool(result["archived"])
        return result

    def remember_site(self, url: str, login_method: str = "", notes: str = "") -> None:
        domain = (urlsplit(canonicalize_url(url)).hostname or "").lower()
        if not domain:
            return
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO sites(domain, first_seen_at, last_seen_at, login_method, notes)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(domain) DO UPDATE SET
                    last_seen_at=excluded.last_seen_at,
                    login_method=CASE WHEN excluded.login_method='' THEN sites.login_method ELSE excluded.login_method END,
                    notes=CASE WHEN excluded.notes='' THEN sites.notes ELSE excluded.notes END
                """,
                (domain, now, now, login_method, notes),
            )

    def mark_site_login(self, url: str, login_method: str) -> None:
        self.remember_site(url, login_method=login_method)
        domain = (urlsplit(canonicalize_url(url)).hostname or "").lower()
        with self.connect() as db:
            db.execute(
                "UPDATE sites SET last_login_at=?, login_method=? WHERE domain=?",
                (utc_now(), login_method, domain),
            )

    def record_application(
        self,
        *,
        title: str,
        company: str,
        url: str,
        jdwatch_id: int | str | None = None,
        locations: list[str] | None = None,
        recruitment_channel: str | None = None,
        status: str = "queued",
        notes: str = "",
    ) -> dict[str, Any]:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid status: {status}")
        if not title or not title.strip():
            raise ValueError("title is required")
        if not company or not company.strip():
            raise ValueError("company is required")
        canonical = canonicalize_url(url)
        if not canonical or not urlsplit(canonical).hostname:
            raise ValueError("a valid application URL is required")
        key = application_key(url, jdwatch_id)
        domain = (urlsplit(canonical).hostname or "").lower()
        now = utc_now()
        with self.connect() as db:
            existing = db.execute(
                "SELECT * FROM applications WHERE application_key=? OR actual_url=?", (key, url)
            ).fetchone()
            if existing:
                result = self._row(existing) or {}
                result["duplicate"] = True
                return result
            cursor = db.execute(
                """
                INSERT INTO applications(
                    application_key, jdwatch_id, title, company, url, canonical_url,
                    site_domain, locations_json, status, recruitment_channel, notes,
                    discovered_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    int(jdwatch_id) if jdwatch_id not in (None, "") else None,
                    title.strip(),
                    company.strip(),
                    url.strip(),
                    canonical,
                    domain,
                    json.dumps(locations or [], ensure_ascii=False),
                    status,
                    recruitment_channel,
                    notes,
                    now,
                    now,
                ),
            )
            app_id = cursor.lastrowid
            db.execute(
                "INSERT INTO events(application_id, event_type, detail_json, created_at) VALUES (?, ?, ?, ?)",
                (app_id, "created", json.dumps({"status": status}), now),
            )
        self.remember_site(url)
        result = self.get_application(int(app_id)) or {}
        result["duplicate"] = False
        return result

    def check_duplicate(self, url: str, jdwatch_id: int | str | None = None) -> dict[str, Any]:
        key = application_key(url, jdwatch_id)
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM applications WHERE application_key=? OR actual_url=?", (key, url)
            ).fetchone()
        return {"duplicate": row is not None, "application": self._row(row)}

    def get_application(self, app_id: int) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
        return self._row(row)

    def update_application(
        self,
        app_id: int,
        *,
        status: str | None = None,
        notes: str | None = None,
        confirmation_ref: str | None = None,
        actual_title: str | None = None,
        actual_url: str | None = None,
        actual_locations: list[str] | None = None,
        archived: bool | None = None,
    ) -> dict[str, Any]:
        current = self.get_application(app_id)
        if not current:
            raise KeyError(f"application {app_id} not found")
        if actual_title is not None and (not isinstance(actual_title, str) or not actual_title.strip()):
            raise ValueError("actual_title must be a non-empty string")
        if actual_url is not None and actual_url and (urlsplit(actual_url).scheme not in {"http", "https"} or not urlsplit(actual_url).hostname):
            raise ValueError("actual_url must be an HTTP(S) URL")
        if actual_locations is not None and (not isinstance(actual_locations, list) or any(not isinstance(x, str) or not x.strip() for x in actual_locations)):
            raise ValueError("actual_locations must be a list of city names")
        if archived is not None and not isinstance(archived, bool):
            raise ValueError("archived must be a boolean")
        next_status = status or current["status"]
        if next_status not in VALID_STATUSES:
            raise ValueError(f"invalid status: {next_status}")
        now = utc_now()
        started_at = current.get("started_at")
        submitted_at = current.get("submitted_at")
        if next_status in {"opened", "auth_required", "form_filling", "ready_for_review"} and not started_at:
            started_at = now
        if next_status == "submitted" and not submitted_at:
            submitted_at = now
        with self.connect() as db:
            db.execute(
                """
                UPDATE applications
                SET status=?, notes=?, confirmation_ref=?, started_at=?, submitted_at=?, updated_at=?
                WHERE id=?
                """,
                (
                    next_status,
                    current["notes"] if notes is None else notes,
                    current["confirmation_ref"] if confirmation_ref is None else confirmation_ref,
                    started_at,
                    submitted_at,
                    now,
                    app_id,
                ),
            )
            edits = {key: value for key, value in {
                "actual_title": actual_title.strip() if actual_title is not None else None,
                "actual_url": actual_url,
                "actual_locations_json": json.dumps(actual_locations, ensure_ascii=False) if actual_locations is not None else None,
                "archived": int(archived) if archived is not None else None,
            }.items() if value is not None}
            if edits:
                db.execute("UPDATE applications SET " + ", ".join(f"{k}=?" for k in edits) + " WHERE id=?", [*edits.values(), app_id])
            detail = {"from": current["status"], "to": next_status, "changes": edits}
            if confirmation_ref:
                detail["confirmation_ref"] = confirmation_ref
            db.execute(
                "INSERT INTO events(application_id, event_type, detail_json, created_at) VALUES (?, ?, ?, ?)",
                (app_id, "status_changed", json.dumps(detail, ensure_ascii=False), now),
            )
        return self.get_application(app_id) or {}

    def list_applications(self, status: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        sql = "SELECT * FROM applications"
        params: list[Any] = []
        if status:
            sql += " WHERE status=?"
            params.append(status)
        sql += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        params.append(limit)
        with self.connect() as db:
            rows = db.execute(sql, params).fetchall()
        return [self._row(row) or {} for row in rows]

    def list_sites(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM sites ORDER BY last_seen_at DESC").fetchall()
        return [dict(row) for row in rows]

    def events(self, app_id: int) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM events WHERE application_id=? ORDER BY id DESC", (app_id,)
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["detail"] = json.loads(item.pop("detail_json") or "{}")
            result.append(item)
        return result

    def summary(self) -> dict[str, Any]:
        with self.connect() as db:
            counts = {
                row["status"]: row["count"]
                for row in db.execute(
                    "SELECT status, COUNT(*) AS count FROM applications GROUP BY status"
                ).fetchall()
            }
            total = db.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
            sites = db.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
        with self.connect() as db:
            active_counts = {row["status"]: row["count"] for row in db.execute("SELECT status, COUNT(*) AS count FROM applications WHERE archived=0 GROUP BY status")}
            archived = db.execute("SELECT COUNT(*) FROM applications WHERE archived=1").fetchone()[0]
        return {"total": total, "sites": sites, "by_status": counts, "unarchived_by_status": active_counts, "archived": archived}

    @staticmethod
    def _source_job_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        for column, target in (
            ("title_items_json", "title_items"),
            ("locations_json", "locations"),
            ("target_graduates_json", "target_graduates"),
            ("education_levels_json", "education_levels"),
            ("recruitment_batches_json", "recruitment_batches"),
        ):
            result[target] = json.loads(result.pop(column) or "[]")
        return result

    def upsert_source_jobs(self, jobs: list[dict[str, Any]]) -> dict[str, int]:
        counts = {"created": 0, "changed": 0, "unchanged": 0}
        now = utc_now()
        with self.connect() as db:
            for job in jobs:
                source = str(job.get("source") or "").strip()
                source_key = str(job.get("source_key") or "").strip()
                if not source or not source_key:
                    raise ValueError("source and source_key are required")
                canonical = json.dumps(job, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                existing = db.execute(
                    "SELECT id, content_hash FROM source_jobs WHERE source=? AND source_key=?",
                    (source, source_key),
                ).fetchone()
                values = (
                    job.get("source_numeric_id"),
                    str(job.get("source_uuid") or ""),
                    str(job.get("company_name") or "未注明公司"),
                    str(job.get("company_type") or ""),
                    str(job.get("industry") or ""),
                    str(job.get("title") or "未注明岗位"),
                    json.dumps(job.get("title_items") or [], ensure_ascii=False),
                    json.dumps(job.get("locations") or [], ensure_ascii=False),
                    json.dumps(job.get("target_graduates") or [], ensure_ascii=False),
                    json.dumps(job.get("education_levels") or [], ensure_ascii=False),
                    json.dumps(job.get("recruitment_batches") or [], ensure_ascii=False),
                    str(job.get("deadline_text") or ""),
                    job.get("deadline_date"),
                    str(job.get("deadline_status") or ""),
                    str(job.get("announcement_source") or ""),
                    str(job.get("announcement_url") or ""),
                    str(job.get("apply_url") or ""),
                    str(job.get("notes") or ""),
                    str(job.get("major_requirements") or ""),
                    str(job.get("written_test") or ""),
                    job.get("source_updated_at"),
                    content_hash,
                )
                if existing:
                    changed = existing["content_hash"] != content_hash
                    db.execute(
                        """
                        UPDATE source_jobs SET source_numeric_id=?, source_uuid=?, company_name=?,
                            company_type=?, industry=?, title=?, title_items_json=?, locations_json=?,
                            target_graduates_json=?, education_levels_json=?, recruitment_batches_json=?,
                            deadline_text=?, deadline_date=?, deadline_status=?, announcement_source=?,
                            announcement_url=?, apply_url=?, notes=?, major_requirements=?, written_test=?,
                            source_updated_at=?, content_hash=?, last_seen_at=?,
                            updated_at=CASE WHEN content_hash<>? THEN ? ELSE updated_at END
                        WHERE id=?
                        """,
                        values + (now, content_hash, now, existing["id"]),
                    )
                    counts["changed" if changed else "unchanged"] += 1
                else:
                    db.execute(
                        """
                        INSERT INTO source_jobs(
                            source, source_key, source_numeric_id, source_uuid, company_name,
                            company_type, industry, title, title_items_json, locations_json,
                            target_graduates_json, education_levels_json, recruitment_batches_json,
                            deadline_text, deadline_date, deadline_status, announcement_source,
                            announcement_url, apply_url, notes, major_requirements, written_test,
                            source_updated_at, content_hash, first_seen_at, last_seen_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (source, source_key) + values + (now, now, now),
                    )
                    counts["created"] += 1
        return counts

    def get_source_job(self, job_id: int) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM source_jobs WHERE id=?", (int(job_id),)).fetchone()
        return self._source_job_row(row)

    def list_source_jobs(
        self,
        *,
        source: str = "baigua",
        query: str = "",
        location: str = "",
        direction: str = "",
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM source_jobs WHERE source=?"
        params: list[Any] = [source]
        terms = JOB_DIRECTION_TERMS.get(direction, [])
        if terms:
            searchable = "(title LIKE ? OR title_items_json LIKE ? OR industry LIKE ? OR major_requirements LIKE ?)"
            sql += " AND (" + " OR ".join(searchable for _ in terms) + ")"
            for term in terms:
                pattern = f"%{term}%"
                params.extend([pattern, pattern, pattern, pattern])
        query_terms = [term for term in re.split(r"[\s,，、]+", query.strip()) if term]
        if query_terms:
            searchable = (
                "(company_name LIKE ? OR title LIKE ? OR title_items_json LIKE ? "
                "OR industry LIKE ? OR notes LIKE ? OR major_requirements LIKE ?)"
            )
            sql += " AND (" + " OR ".join(searchable for _ in query_terms) + ")"
            for term in query_terms:
                pattern = f"%{term}%"
                params.extend([pattern] * 6)
        location_terms = [term for term in re.split(r"[\s,，、]+", location.strip()) if term]
        if location_terms:
            sql += " AND (" + " OR ".join("locations_json LIKE ?" for _ in location_terms) + ")"
            params.extend(f"%{term}%" for term in location_terms)
        sql += " ORDER BY COALESCE(source_updated_at, updated_at) DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([max(1, min(int(limit), 1000)), max(0, int(offset))])
        with self.connect() as db:
            rows = db.execute(sql, params).fetchall()
        return [self._source_job_row(row) or {} for row in rows]

    def source_jobs_summary(
        self, source: str = "baigua", *, query: str = "", direction: str = ""
    ) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT COUNT(*) AS total, MAX(source_updated_at) AS latest_source_update,
                       MAX(last_seen_at) AS last_seen_at
                FROM source_jobs WHERE source=?
                """,
                (source,),
            ).fetchone()
        filtered_total = len(
            self.list_source_jobs(source=source, query=query, direction=direction, limit=1000)
        )
        if filtered_total == 1000:
            offset = 1000
            while True:
                batch = self.list_source_jobs(
                    source=source, query=query, direction=direction, limit=1000, offset=offset
                )
                filtered_total += len(batch)
                if len(batch) < 1000:
                    break
                offset += 1000
        return {
            **dict(row),
            "filtered_total": filtered_total,
            "source": source,
            "sync": self.source_sync_state(source),
        }

    def source_sync_state(self, source: str) -> dict[str, Any]:
        return self.get_kv(f"source_sync:{source}", {})

    def set_source_sync_state(self, source: str, state: dict[str, Any]) -> dict[str, Any]:
        return self.set_kv(f"source_sync:{source}", state)

    def queue_source_job(self, job_id: int) -> dict[str, Any]:
        job = self.get_source_job(job_id)
        if not job:
            raise KeyError(f"source job {job_id} not found")
        url = str(job.get("apply_url") or job.get("announcement_url") or "")
        if not url:
            raise ValueError("该职位没有可用的投递或公告链接")
        return self.record_application(
            title=str(job["title"]),
            company=str(job["company_name"]),
            url=url,
            locations=job.get("locations") or [],
            recruitment_channel="campus/baigua",
            status="queued",
            notes=f"白瓜职位 {job['source_key']}；2027届秋招",
        )

    @staticmethod
    def _safe_activity_text(value: Any, limit: int = 300) -> str:
        text = " ".join(str(value or "").split())[:limit]
        text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[已隐藏]", text)
        text = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[已隐藏]", text)
        text = re.sub(r"\bsk[-_][A-Za-z0-9_-]{8,}\b", "[已隐藏]", text, flags=re.IGNORECASE)
        lower = text.lower()
        sensitive_markers = ("验证码", "密码", "password", "passcode", "otp", "verification code")
        if any(marker in lower for marker in sensitive_markers):
            text = re.sub(r"(?<!\d)\d{4,8}(?!\d)", "[已隐藏]", text)
        return text

    def start_activity(
        self,
        *,
        action: str,
        source: str = "codex_conversation",
        label: str = "Job Autopilot 任务",
        state: str = "running",
        stage: str = "preparing",
        message: str = "正在准备任务",
        request_id: str = "",
        progress_total: int = 0,
    ) -> dict[str, Any]:
        if source not in ACTIVITY_SOURCES:
            raise ValueError("invalid activity source")
        if state not in ACTIVITY_STATES:
            raise ValueError("invalid activity state")
        if stage not in ACTIVITY_STAGES:
            raise ValueError("invalid activity stage")
        run_id = uuid.uuid4().hex
        now = utc_now()
        safe_label = self._safe_activity_text(label, 80) or "Job Autopilot 任务"
        safe_message = self._safe_activity_text(message)
        total = max(0, int(progress_total or 0))
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO activity_runs(
                    run_id, request_id, source, state, action, label, stage, message,
                    progress_current, progress_total, started_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    run_id,
                    str(request_id or ""),
                    source,
                    state,
                    self._safe_activity_text(action, 60),
                    safe_label,
                    stage,
                    safe_message,
                    total,
                    now,
                    now,
                ),
            )
            db.execute(
                """
                INSERT INTO activity_events(
                    run_id, state, stage, message, progress_current, progress_total, created_at
                ) VALUES (?, ?, ?, ?, 0, ?, ?)
                """,
                (run_id, state, stage, safe_message, total, now),
            )
        return self.get_activity(run_id) or {}

    def get_activity(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT r.*, COALESCE(NULLIF(a.actual_title, ''), a.title) AS application_title, a.company AS application_company
                FROM activity_runs r
                LEFT JOIN applications a ON a.id=r.application_id
                WHERE r.run_id=?
                """,
                (run_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_activity(
        self,
        run_id: str,
        *,
        state: str | None = None,
        stage: str | None = None,
        message: str | None = None,
        application_id: int | None = None,
        progress_current: int | None = None,
        progress_total: int | None = None,
    ) -> dict[str, Any]:
        current = self.get_activity(run_id)
        if not current:
            raise KeyError(f"activity {run_id} not found")
        next_state = state or str(current["state"])
        next_stage = stage or str(current["stage"])
        if next_state not in ACTIVITY_STATES:
            raise ValueError("invalid activity state")
        if next_stage not in ACTIVITY_STAGES:
            raise ValueError("invalid activity stage")
        next_app = current.get("application_id") if application_id is None else int(application_id)
        if next_app is not None and not self.get_application(int(next_app)):
            raise KeyError(f"application {next_app} not found")
        next_current = int(current["progress_current"] if progress_current is None else progress_current)
        next_total = int(current["progress_total"] if progress_total is None else progress_total)
        if next_current < 0 or next_total < 0 or (next_total and next_current > next_total):
            raise ValueError("invalid activity progress")
        safe_message = self._safe_activity_text(
            current.get("message", "") if message is None else message
        )
        now = utc_now()
        completed_at = now if next_state in {"completed", "failed"} else current.get("completed_at")
        with self.connect() as db:
            db.execute(
                """
                UPDATE activity_runs SET state=?, stage=?, message=?, application_id=?,
                    progress_current=?, progress_total=?, updated_at=?, completed_at=?
                WHERE run_id=?
                """,
                (
                    next_state,
                    next_stage,
                    safe_message,
                    next_app,
                    next_current,
                    next_total,
                    now,
                    completed_at,
                    run_id,
                ),
            )
            db.execute(
                """
                INSERT INTO activity_events(
                    run_id, state, stage, message, application_id,
                    progress_current, progress_total, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    next_state,
                    next_stage,
                    safe_message,
                    next_app,
                    next_current,
                    next_total,
                    now,
                ),
            )
        return self.get_activity(run_id) or {}

    def list_activities(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT r.*, COALESCE(NULLIF(a.actual_title, ''), a.title) AS application_title, a.company AS application_company
                FROM activity_runs r
                LEFT JOIN applications a ON a.id=r.application_id
                ORDER BY CASE WHEN r.state IN ('requested','running','waiting','paused') THEN 0 ELSE 1 END,
                         r.updated_at DESC, r.rowid DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 50)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def activity_events(self, run_id: str, limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT e.*, COALESCE(NULLIF(a.actual_title, ''), a.title) AS application_title, a.company AS application_company
                FROM activity_events e
                LEFT JOIN applications a ON a.id=e.application_id
                WHERE e.run_id=? ORDER BY e.id DESC LIMIT ?
                """,
                (run_id, max(1, min(int(limit), 100))),
            ).fetchall()
        return [dict(row) for row in rows]

    def activity_snapshot(self, run_limit: int = 8, event_limit: int = 30) -> dict[str, Any]:
        runs = self.list_activities(run_limit)
        current = runs[0] if runs else None
        return {
            "current": current,
            "runs": runs,
            "events": self.activity_events(str(current["run_id"]), event_limit) if current else [],
        }

    def get_kv(self, key: str, default: Any = None) -> Any:
        with self.connect() as db:
            row = db.execute("SELECT value_json FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_kv(self, key: str, value: Any) -> Any:
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO kv(key, value_json, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at
                """,
                (key, json.dumps(value, ensure_ascii=False), utc_now()),
            )
        return value

    def profile(self) -> dict[str, Any]:
        current = self.get_kv("profile", {})
        if not isinstance(current.get("custom_fields"), list):
            current["custom_fields"] = []
        return current

    def update_profile(self, data: dict[str, Any]) -> dict[str, Any]:
        current = self.profile()
        current.update(data)
        return self.set_kv("profile", current)

    @staticmethod
    def _validate_profile_field(data: dict[str, Any]) -> dict[str, Any]:
        label = str(data.get("label", "")).strip()
        key = str(data.get("key", "")).strip().lower().replace(" ", "_")
        if not label:
            raise ValueError("field label is required")
        if not key:
            key = "field_" + uuid.uuid4().hex[:8]
        searchable = f"{label} {key}".lower()
        if any(term in searchable for term in FORBIDDEN_PROFILE_TERMS):
            raise ValueError("passwords and one-time codes may not be stored")
        aliases = data.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [item.strip() for item in aliases.split(",") if item.strip()]
        if not isinstance(aliases, list):
            raise ValueError("aliases must be a list")
        scope = str(data.get("scope", "global")).strip() or "global"
        return {
            "id": str(data.get("id") or uuid.uuid4().hex[:12]),
            "key": key,
            "label": label,
            "value": data.get("value", ""),
            "aliases": sorted({str(item).strip() for item in aliases if str(item).strip()}),
            "scope": scope,
            "source": str(data.get("source") or "user_confirmed"),
            "confirmed_at": str(data.get("confirmed_at") or utc_now()),
        }

    def upsert_profile_field(self, data: dict[str, Any]) -> dict[str, Any]:
        field = self._validate_profile_field(data)
        profile = self.profile()
        fields = list(profile.get("custom_fields") or [])
        match_index = next(
            (
                index
                for index, item in enumerate(fields)
                if item.get("id") == field["id"]
                or (item.get("key") == field["key"] and item.get("scope", "global") == field["scope"])
            ),
            None,
        )
        if match_index is None:
            fields.append(field)
        else:
            old_aliases = fields[match_index].get("aliases") or []
            field["aliases"] = sorted(set(old_aliases) | set(field["aliases"]))
            fields[match_index] = field
        profile["custom_fields"] = fields
        self.set_kv("profile", profile)
        return field

    def automation(self) -> dict[str, Any]:
        current = self.get_kv("automation", DEFAULT_AUTOMATION.copy())
        return {**DEFAULT_AUTOMATION, **current}

    def request_automation(
        self,
        action: str = "process_queue",
        criteria: dict[str, Any] | None = None,
        source: str = "codex_conversation",
    ) -> dict[str, Any]:
        if action not in {"discover_and_apply", "process_queue", "parse_resume"}:
            raise ValueError("unsupported automation action")
        now = utc_now()
        request_id = uuid.uuid4().hex
        labels = {
            "discover_and_apply": "找岗并推进投递",
            "process_queue": "继续处理投递队列",
            "parse_resume": "AI 解析简历",
        }
        activity = self.start_activity(
            action=action,
            source=source,
            label=labels[action],
            state="requested",
            stage="preparing",
            message="任务已创建",
            request_id=request_id,
        )
        state = {
            "state": "requested",
            "action": action,
            "request_id": request_id,
            "activity_run_id": activity["run_id"],
            "criteria": {**self.settings()["job_preferences"], **(criteria or {})},
            "message": "等待 Codex 接管",
            "requested_at": now,
            "updated_at": now,
        }
        return self.set_kv("automation", state)

    def set_automation_state(self, state: str, message: str = "") -> dict[str, Any]:
        if state not in {"idle", "requested", "running", "paused", "completed", "failed"}:
            raise ValueError("invalid automation state")
        current = self.automation()
        current.update({"state": state, "message": message, "updated_at": utc_now()})
        result = self.set_kv("automation", current)
        run_id = str(current.get("activity_run_id") or "")
        if run_id and self.get_activity(run_id):
            stage_by_state = {
                "requested": "preparing",
                "running": "preparing",
                "paused": "awaiting_confirmation",
                "completed": "completed",
                "failed": "failed",
            }
            activity_state = state
            if activity_state == "idle":
                activity_state = "paused"
            self.update_activity(
                run_id,
                state=activity_state,
                stage=stage_by_state.get(state, "preparing"),
                message=message,
            )
        return result

    def codex_runtime(self) -> dict[str, Any]:
        current = self.get_kv("codex_runtime", DEFAULT_CODEX_RUNTIME.copy())
        return {**DEFAULT_CODEX_RUNTIME, **current}

    def set_codex_runtime(self, **changes: Any) -> dict[str, Any]:
        current = self.codex_runtime()
        current.update(changes)
        current["updated_at"] = utc_now()
        return self.set_kv("codex_runtime", current)

    def profile_import(self) -> dict[str, Any]:
        return self.get_kv("profile_import", {"status": "idle", "suggestions": []})

    def request_profile_import(
        self, resume_path: str, source: str = "codex_conversation"
    ) -> dict[str, Any]:
        path = Path(resume_path).expanduser()
        if not path.is_absolute():
            raise ValueError("resume path must be absolute")
        if not path.is_file():
            raise ValueError("resume file does not exist")
        if path.suffix.lower() not in {".pdf", ".docx", ".txt", ".md"}:
            raise ValueError("supported resume formats: PDF, DOCX, TXT, MD")
        now = utc_now()
        request = {
            "status": "requested",
            "request_id": uuid.uuid4().hex,
            "resume_path": str(path),
            "suggestions": [],
            "message": "等待 AI 解析",
            "requested_at": now,
            "updated_at": now,
        }
        self.set_kv("profile_import", request)
        self.request_automation("parse_resume", {"resume_path": str(path)}, source=source)
        return request

    def complete_profile_import(
        self, suggestions: list[dict[str, Any]], message: str = ""
    ) -> dict[str, Any]:
        if not isinstance(suggestions, list):
            raise ValueError("suggestions must be a list")
        cleaned = []
        for raw in suggestions:
            if not isinstance(raw, dict):
                raise ValueError("each suggestion must be an object")
            label = str(raw.get("label", "")).strip()
            if not label:
                raise ValueError("suggestion label is required")
            cleaned.append(
                {
                    "id": str(raw.get("id") or uuid.uuid4().hex[:12]),
                    "target_key": str(raw.get("target_key") or "").strip(),
                    "label": label,
                    "value": raw.get("value", ""),
                    "aliases": raw.get("aliases") or [],
                    "scope": str(raw.get("scope") or "global"),
                    "confidence": raw.get("confidence"),
                    "reason": str(raw.get("reason") or ""),
                }
            )
        current = self.profile_import()
        current.update(
            {
                "status": "ready_for_review",
                "suggestions": cleaned,
                "message": message or "请确认 AI 提取的个人资料",
                "updated_at": utc_now(),
            }
        )
        self.set_automation_state("completed", "简历已解析，等待确认")
        return self.set_kv("profile_import", current)

    def apply_profile_import(self, suggestion_ids: list[str]) -> dict[str, Any]:
        pending = self.profile_import()
        allowed = set(suggestion_ids)
        chosen = [item for item in pending.get("suggestions", []) if item.get("id") in allowed]
        if not chosen:
            raise ValueError("select at least one suggestion")
        profile = self.profile()
        custom_fields = list(profile.get("custom_fields") or [])
        for item in chosen:
            target_key = str(item.get("target_key") or "").strip()
            if target_key in BASE_PROFILE_KEYS:
                profile[target_key] = item.get("value", "")
            else:
                field = self._validate_profile_field(
                    {
                        "key": target_key,
                        "label": item.get("label"),
                        "value": item.get("value"),
                        "aliases": item.get("aliases") or [],
                        "scope": item.get("scope") or "global",
                        "source": "resume_confirmed",
                    }
                )
                match_index = next(
                    (
                        index
                        for index, existing in enumerate(custom_fields)
                        if existing.get("key") == field["key"]
                        and existing.get("scope", "global") == field["scope"]
                    ),
                    None,
                )
                if match_index is None:
                    custom_fields.append(field)
                else:
                    existing_aliases = custom_fields[match_index].get("aliases") or []
                    field["aliases"] = sorted(set(existing_aliases) | set(field["aliases"]))
                    custom_fields[match_index] = field
        profile["custom_fields"] = custom_fields
        self.set_kv("profile", profile)
        pending.update(
            {
                "status": "applied",
                "applied_ids": sorted(allowed),
                "message": f"已写入 {len(chosen)} 个字段",
                "updated_at": utc_now(),
            }
        )
        self.set_kv("profile_import", pending)
        return self.profile()

    def settings(self) -> dict[str, Any]:
        current = self.get_kv("settings", DEFAULT_SETTINGS.copy())
        return {**DEFAULT_SETTINGS, **current, "job_preferences": {
            **DEFAULT_SETTINGS["job_preferences"], **current.get("job_preferences", {})
        }}

    def update_settings(self, data: dict[str, Any]) -> dict[str, Any]:
        current = self.settings()
        data = dict(data)
        if "job_preferences" in data:
            preferences = data["job_preferences"]
            if not isinstance(preferences, dict):
                raise ValueError("job_preferences must be an object")
            preferences = {**current["job_preferences"], **preferences}
            for key in ("keywords", "locations", "recruitment_types", "excluded_keywords", "excluded_companies"):
                value = preferences[key]
                if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
                    raise ValueError(f"{key} must be a list of strings")
                preferences[key] = list(dict.fromkeys(x.strip() for x in value if x.strip()))
            if any(x not in {"campus", "intern", "social"} for x in preferences["recruitment_types"]):
                raise ValueError("invalid recruitment_types")
            for key in ("prefer_phone_login", "skip_wechat_only", "prefer_known_companies"):
                if not isinstance(preferences[key], bool):
                    raise ValueError(f"{key} must be a boolean")
            for key in ("graduation_year", "employment_type"):
                if not isinstance(preferences[key], str):
                    raise ValueError(f"{key} must be a string")
            data["job_preferences"] = preferences
        current.update(data)
        mode = current.get("submission_mode")
        if mode not in {"review", "automatic"}:
            raise ValueError("submission_mode must be review or automatic")
        domains = current.get("allowed_domains", [])
        if not isinstance(domains, list):
            raise ValueError("allowed_domains must be a list")
        if any(not isinstance(d, str) for d in domains):
            raise ValueError("allowed_domains must contain strings")
        current["allowed_domains"] = sorted({normalize_rule(d) for d in domains if d.strip()})
        project = str(current.get("codex_project_path") or "").strip()
        if project:
            path = Path(project).expanduser()
            if not path.is_absolute() or not path.is_dir():
                raise ValueError("项目目录必须是本机已存在的绝对路径")
            project = str(path.resolve())
        current["codex_project_path"] = project
        if current.get("login_method") != "phone_otp":
            raise ValueError("login_method must be phone_otp")
        for key in (
            "autofill_profile_phone",
            "read_otp_via_adb",
            "auto_accept_standard_agreements",
            "solve_captcha_automatically",
        ):
            if not isinstance(current.get(key), bool):
                raise ValueError(f"{key} must be a boolean")
        return self.set_kv("settings", current)

    def check_domain_policy(self, url: str) -> dict[str, Any]:
        settings = self.settings()
        result = check_domain(url, settings["allowed_domains"])
        return {**result, "automatic": result["allowed"] and settings["submission_mode"] == "automatic"}
