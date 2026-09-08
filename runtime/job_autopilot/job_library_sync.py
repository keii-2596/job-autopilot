"""Incrementally merge the public GitHub job snapshot; never import personal data."""
from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import re
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .db import Ledger, utc_now
from .updater import BRANCH, CHECK_INTERVAL, GitHubUpdater, REPOSITORY

ARRAY_FIELDS = {"title_items", "locations", "target_graduates", "education_levels", "recruitment_batches"}
TEXT_FIELDS = {
    "source_uuid", "company_name", "company_type", "industry", "title", "deadline_text",
    "deadline_status", "announcement_source", "announcement_url", "apply_url",
    "major_requirements", "written_test",
}
NULLABLE_FIELDS = {"source_numeric_id", "deadline_date", "source_updated_at"}
PUBLIC_FIELDS = ARRAY_FIELDS | TEXT_FIELDS | NULLABLE_FIELDS


def normalize_snapshot(payload):
    rows = payload.get("jobs") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not rows or len(rows) > 50_000:
        raise ValueError("公开快照必须包含 1 到 50000 条职位")
    result, seen = [], set()
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("快照中的职位必须是对象")
        record = {}
        for field in PUBLIC_FIELDS:
            if field not in raw:
                continue
            value = raw[field]
            if field in ARRAY_FIELDS:
                if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                    raise ValueError(f"职位字段 {field} 必须是字符串数组")
                value = list(dict.fromkeys(item.strip() for item in value if item.strip()))
            elif field == "source_numeric_id":
                if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
                    raise ValueError("source_numeric_id 必须是整数")
            elif not isinstance(value, str) and not (field in NULLABLE_FIELDS and value is None):
                raise ValueError(f"职位字段 {field} 类型不正确")
            record[field] = value
        if "2027届" not in record.get("target_graduates", []) or not any("秋招" in item for item in record.get("recruitment_batches", [])):
            continue
        key = raw.get("source_key")
        if not isinstance(key, str) or not key.strip() or len(key) > 512:
            raise ValueError("职位缺少稳定标识 source_key")
        key = key.strip()
        if key in seen:
            raise ValueError("快照包含重复职位标识，未写入任何数据")
        seen.add(key)
        if not record.get("company_name") or not record.get("title"):
            raise ValueError("职位缺少公司或岗位名称")
        for field in ("apply_url", "announcement_url"):
            if record.get(field):
                url = urlsplit(record[field])
                if url.scheme not in {"http", "https"} or not url.hostname:
                    raise ValueError("快照包含不安全的岗位链接")
                if url.username or url.password:
                    # Historic snapshots occasionally turn email-application text into
                    # an HTTP URL. Never expose credential-bearing links in the UI.
                    record[field] = ""
                    record["_ignored_links"] = record.get("_ignored_links", 0) + 1
        record["source_key"] = key
        # Used only for freshness comparison, not copied into local timestamps.
        record["_observed_at"] = raw.get("updated_at")
        result.append(record)
    if not result:
        raise ValueError("快照没有 2027 届秋招职位，未修改本地职位库")
    return result, len(rows)


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).timestamp()
    except ValueError:
        return None


class GitHubJobLibrary:
    def __init__(self, ledger: Ledger, installing=lambda: False):
        self.ledger = ledger
        self.installing = installing
        self._lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._stop = threading.Event()
        self._worker = None
        self._scheduler = None
        with ledger.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS github_job_baseline (source_key TEXT PRIMARY KEY, value_json TEXT NOT NULL)")
        if self.status()["state"] in {"checking", "downloading", "merging"}:
            self._save(state="interrupted", message="上次职位同步已中断，可以重新同步；已提交的数据不会重复导入。")

    def status(self):
        return {"state": "idle", "auto_sync": False, "message": "尚未从 GitHub 同步职位",
                **self.ledger.get_kv("github_job_library", {}), "repository": REPOSITORY,
                "snapshot_path": "data/jobs.json", "interval_hours": 6}

    def _save(self, **changes):
        with self._state_lock:
            current = self.ledger.get_kv("github_job_library", {})
            current.update(changes)
            current["updated_at"] = utc_now()
            self.ledger.set_kv("github_job_library", current)
        return self.status()

    def configure(self, enabled):
        if not isinstance(enabled, bool):
            raise ValueError("自动同步开关必须是布尔值")
        return self._save(auto_sync=enabled, checked_epoch=0 if enabled else self.status().get("checked_epoch", 0))

    def request(self, *, automatic=False):
        if self.installing():
            raise RuntimeError("插件正在更新，请稍后同步职位数据")
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("职位库正在同步，请稍候")
        self._save(state="checking", message="正在检查 GitHub 职位数据版本", checked_epoch=time.time())
        self._worker = threading.Thread(target=self._run, args=(automatic,), daemon=True)
        self._worker.start()
        return self.status()

    def _api(self, path):
        return json.loads(GitHubUpdater._fetch(f"https://api.github.com/repos/{REPOSITORY}/{path}"))

    def sync(self, *, automatic=False):
        history = self._api(f"commits?path=data/jobs.json&sha={BRANCH}&per_page=1")
        if not isinstance(history, list) or not history:
            raise ValueError("GitHub 仓库中没有公开职位快照")
        commit = history[0].get("sha", "")
        if not re.fullmatch(r"[a-f0-9]{40}", commit):
            raise ValueError("无效的快照提交编号")
        previous = self.status()
        if commit == previous.get("synced_commit"):
            return self._save(state="completed", checked_at=utc_now(), downloaded=False,
                              counts={"created": 0, "changed": 0, "unchanged": previous.get("scoped_total", 0), "preserved": 0},
                              message="GitHub 职位数据没有变化，未重复下载或写入。")
        if previous.get("synced_commit"):
            comparison = self._api(f"compare/{previous['synced_commit']}...{commit}")
            if comparison.get("status") != "ahead":
                raise ValueError("远程数据版本回退或分叉，已停止同步，未覆盖本地数据。")
        metadata = self._api(f"contents/data/jobs.json?ref={commit}")
        blob = metadata.get("sha", "")
        if metadata.get("type") != "file" or not re.fullmatch(r"[a-f0-9]{40}", blob) or metadata.get("size", 0) > 30_000_000:
            raise ValueError("职位快照元信息不正确或文件过大")
        if blob == previous.get("synced_blob"):
            return self._save(state="completed", synced_commit=commit, checked_at=utc_now(), downloaded=False,
                              counts={"created": 0, "changed": 0, "unchanged": previous.get("scoped_total", 0), "preserved": 0},
                              message="公开快照内容没有变化，未重复下载或写入。")
        self._save(state="downloading", message="正在下载已固定版本的公开职位快照")
        try:
            content = GitHubUpdater._fetch(f"https://raw.githubusercontent.com/{REPOSITORY}/{commit}/data/jobs.json", 30_000_000)
        except (RuntimeError, OSError):
            self._save(message="快照下载地址暂不可用，正在通过 GitHub 接口读取同一版本")
            fallback = json.loads(GitHubUpdater._fetch(f"https://api.github.com/repos/{REPOSITORY}/git/blobs/{blob}", 42_000_000))
            if fallback.get("encoding") != "base64" or fallback.get("sha") != blob:
                raise ValueError("备用快照格式或版本不正确")
            content = base64.b64decode("".join(fallback.get("content", "").split()), validate=True)
            if len(content) > 30_000_000:
                raise ValueError("职位快照文件过大")
        digest = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
        if digest != blob:
            raise ValueError("职位快照校验不一致，未写入任何数据")
        rows, total = normalize_snapshot(json.loads(content))
        if self._stop.is_set() or (automatic and not self.status()["auto_sync"]):
            return self._save(state="interrupted", message="同步已取消，未写入数据")
        if self.installing():
            raise RuntimeError("插件正在更新，本次数据尚未写入，请稍后重试")
        self._save(state="merging", message=f"正在比对 {len(rows)} 条 2027 届秋招职位，仅合并新增和变更项")
        return self.merge(rows, commit, blob, total)

    def merge(self, rows, commit, blob, remote_total):
        counts = {"created": 0, "changed": 0, "unchanged": 0, "preserved": 0}
        ignored_links = sum(row.get("_ignored_links", 0) for row in rows)
        # Lock and commit rows, per-job baselines and successful cursor together.
        with self._state_lock, self.ledger.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = {row["source_key"]: self.ledger._source_job_row(row) for row in db.execute("SELECT * FROM source_jobs WHERE source='baigua'")}
            baselines = {row["source_key"]: json.loads(row["value_json"]) for row in db.execute("SELECT * FROM github_job_baseline")}
            aliases = {}
            remote_keys = {row["source_key"] for row in rows}
            claimed_aliases = set()
            def index_job(item):
                for field in ("apply_url", "announcement_url"):
                    if item.get(field):
                        identity = (item["company_name"], item["title"], field, item[field])
                        aliases.setdefault(identity, set()).add(item["source_key"])
            for item in existing.values():
                if item["source_key"] not in remote_keys:
                    index_job(item)
            changes = []
            for remote in rows:
                key = remote["source_key"]
                current = existing.get(key)
                if current is None:
                    # Historical browser imports can use a different key for the same notice.
                    keys = set().union(*(aliases.get((remote["company_name"], remote["title"], field, remote.get(field)), set()) for field in ("apply_url", "announcement_url")))
                    matches = [existing[item_key] for item_key in keys if (item := existing[item_key])["company_name"] == remote["company_name"] and item["title"] == remote["title"]
                               and any(remote.get(field) and item.get(field) == remote[field] for field in ("apply_url", "announcement_url"))]
                    if len(matches) == 1:
                        current = matches[0]
                        if current["source_key"] in claimed_aliases:
                            counts["preserved"] += 1
                            continue
                        claimed_aliases.add(current["source_key"])
                    elif len(matches) > 1:
                        counts["preserved"] += 1
                        continue
                public = {field: value for field, value in remote.items() if field in PUBLIC_FIELDS}
                baseline = baselines.get(key)
                conflict = False
                if current is None:
                    job = {**public, "source": "baigua", "source_key": key, "notes": "公开职位快照"}
                    changes.append(job)
                    existing[key] = job
                    counts["created"] += 1
                else:
                    job = {field: current.get(field) for field in PUBLIC_FIELDS}
                    job.update(source="baigua", source_key=current["source_key"], notes=current.get("notes") or "")
                    changed = False
                    local_time = timestamp(current.get("source_updated_at"))
                    remote_time = timestamp(remote.get("source_updated_at"))
                    observed = timestamp(remote.get("_observed_at"))
                    local_observed = timestamp(current.get("updated_at"))
                    older = ((local_time is not None and remote_time is not None and local_time > remote_time)
                             or (baseline is None and observed is not None and local_observed is not None and local_observed > observed))
                    for field, value in public.items():
                        if current.get(field) == value or (baseline is not None and baseline.get(field) == value):
                            continue
                        empty = [] if field in ARRAY_FIELDS else (None if field in NULLABLE_FIELDS else "")
                        if older or (baseline is not None and current.get(field, empty) != baseline.get(field, empty)):
                            conflict = True
                            continue
                        job[field] = value
                        changed = True
                    if changed:
                        changes.append(job)
                        counts["changed"] += 1
                        existing[current["source_key"]] = {**current, **job}
                    elif not conflict:
                        counts["unchanged"] += 1
                    if conflict:
                        counts["preserved"] += 1
                db.execute("INSERT INTO github_job_baseline(source_key,value_json) VALUES (?,?) ON CONFLICT(source_key) DO UPDATE SET value_json=excluded.value_json", (key, json.dumps(public, ensure_ascii=False)))
            if changes:
                self.ledger.upsert_source_jobs(changes, _connection=db)
            row = db.execute("SELECT value_json FROM kv WHERE key='github_job_library'").fetchone()
            state = json.loads(row["value_json"]) if row else {}
            state.update(state="completed", synced_commit=commit, synced_blob=blob, synced_at=utc_now(), checked_at=utc_now(),
                         updated_at=utc_now(), downloaded=True, counts=counts, scoped_total=len(rows), remote_total=remote_total,
                         ignored_links=ignored_links,
                         message=f"同步完成：新增 {counts['created']}，更新 {counts['changed']}，未变 {counts['unchanged']}，保留本地内容 {counts['preserved']}。"
                                 + (f"已忽略 {ignored_links} 个格式不正确的投递链接。" if ignored_links else ""))
            db.execute("INSERT INTO kv(key,value_json,updated_at) VALUES ('github_job_library',?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at", (json.dumps(state, ensure_ascii=False), utc_now()))
        return self.status()

    def _run(self, automatic=False):
        try:
            with (self.ledger.path.parent / "github-jobs-sync.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.sync(automatic=automatic)
        except Exception as error:
            self._save(state="failed", message=f"职位同步失败：{error}"[:500])
        finally:
            self._lock.release()

    def tick(self):
        current = self.status()
        if current["auto_sync"] and not self._lock.locked() and not self.installing() and time.time() - current.get("checked_epoch", 0) >= CHECK_INTERVAL:
            self.request(automatic=True)

    def start(self):
        def loop():
            while not self._stop.wait(30):
                try:
                    self.tick()
                except Exception:
                    pass
        self._scheduler = threading.Thread(target=loop, daemon=True)
        self._scheduler.start()

    def close(self):
        self._stop.set()
        if self._scheduler:
            self._scheduler.join(timeout=2)
