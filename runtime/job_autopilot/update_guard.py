"""Separate live dashboard work from historical activity bookkeeping."""
from __future__ import annotations

from .db import utc_now


class UpdateTaskGuard:
    def __init__(self, ledger, codex, syncing=lambda: False):
        self.ledger = ledger
        self.codex = codex
        self.syncing = syncing

    @staticmethod
    def _activities(db):
        # Do not truncate at the dashboard's display limit: every live record matters.
        return [dict(row) for row in db.execute("""
            SELECT r.*, COALESCE((SELECT MAX(e.id) FROM activity_events e
                WHERE e.run_id=r.run_id), 0) AS revision
            FROM activity_runs r WHERE r.state IN ('requested','running','waiting')
            ORDER BY r.updated_at DESC, r.rowid DESC
        """)]

    def snapshot(self):
        with self.ledger.connect() as db:
            rows = self._activities(db)
        return self._classify(rows)

    def _classify(self, rows):
        runtime = self.codex.status()
        automation = self.ledger.automation()
        active = runtime.get("state") in self.codex.ACTIVE_STATES
        switching = self.codex._run_lock.locked()
        blockers, stale = [], []
        if active or switching:
            names = {"starting": "Codex 正在启动", "running": "Codex 正在执行", "awaiting_input": "Codex 正在等待回答"}
            current_run = next((row for row in rows if row["run_id"] == automation.get("activity_run_id")), {})
            label = names.get(runtime.get("state"), "Codex 正在切换连接")
            if current_run:
                label += " · " + current_run["label"]
            blockers.append({"kind": "codex", "label": label,
                             "detail": "请在对话区域处理确认项或暂停任务后重试更新。",
                             "run_id": automation.get("activity_run_id", ""), "clearable": False})
        if self.syncing():
            blockers.append({"kind": "data_sync", "label": "职位数据正在同步",
                             "detail": "数据合并结束后即可安装更新。", "clearable": False})
        for row in rows:
            entry = {key: row[key] for key in ("run_id", "label", "source", "state", "updated_at", "revision")}
            if row["source"] == "web_app_server":
                if (active or switching) and row["run_id"] == automation.get("activity_run_id"):
                    continue
                # The sole dashboard controller owns these records. Old requests are
                # not evidence of live work after pause/restart/replacement.
                stale.append({**entry, "kind": "stale_web", "clearable": True,
                              "detail": "网页遗留记录，已不对应正在执行的请求，不阻挡更新。"})
            else:
                # Legacy external records have no reliable owner/heartbeat. Age alone
                # cannot prove a task stopped, especially while awaiting user input.
                blockers.append({**entry, "kind": "unverified_activity", "clearable": True,
                                 "detail": "其他对话留下的活动记录，无法仅凭记录确认是否仍在执行。确认原任务已停止后可整理状态。"})
        return {"blocked": bool(blockers), "blockers": blockers, "stale_web": stale}

    def reconcile(self, entries=None, *, confirmed=False):
        """Pause bookkeeping only; never interrupt a model or change applications.

        Explicit external confirmation is bound to the exact activity event revision,
        so a newly resumed task cannot be cleared by an old browser button.
        """
        if entries is not None and (not isinstance(entries, list) or len(entries) > 100):
            raise ValueError("待整理任务列表不正确")
        with self.codex._submission_lock, self.ledger.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = self._activities(db)
            snapshot = self._classify(rows)
            candidates = {item["run_id"]: item for item in snapshot["stale_web"] + snapshot["blockers"] if item.get("clearable")}
            selected = snapshot["stale_web"] if entries is None else entries
            targets = []
            for entry in selected:
                if not isinstance(entry, dict):
                    raise ValueError("待整理任务格式不正确")
                candidate = candidates.get(entry.get("run_id"))
                if not candidate or candidate["revision"] != entry.get("revision"):
                    raise ValueError("任务状态已变化，请刷新后重新核实；没有修改记录。")
                if candidate["kind"] == "unverified_activity" and confirmed is not True:
                    raise ValueError("请先确认原对话中的任务已停止；此操作不会停止正在运行的任务。")
                if candidate["run_id"] not in targets:
                    targets.append(candidate["run_id"])
            now = utc_now()
            for run_id in targets:
                row = next(row for row in rows if row["run_id"] == run_id)
                message = "已核实原任务停止，整理遗留活动状态；投递记录和历史进展保留。" if row["source"] != "web_app_server" else "网页请求已停止或被替换，已整理遗留运行状态；可继续原任务。"
                db.execute("UPDATE activity_runs SET state='paused', stage='awaiting_confirmation', message=?, updated_at=? WHERE run_id=?", (message, now, run_id))
                db.execute("""INSERT INTO activity_events(run_id,state,stage,message,application_id,
                    progress_current,progress_total,created_at) VALUES (?,'paused','awaiting_confirmation',?,?,?,?,?)""",
                           (run_id, message, row.get("application_id"), row["progress_current"], row["progress_total"], now))
        return {"reconciled": len(targets), **self.snapshot()}
