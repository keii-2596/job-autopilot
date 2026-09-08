import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from job_autopilot.db import Ledger
from job_autopilot.update_guard import UpdateTaskGuard
from job_autopilot.web import DashboardServer


class UpdateGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.temp.name) / "test.db")
        self.runtime = {"state": "paused", "connected": False}
        self.codex = Mock()
        self.codex.status.side_effect = lambda: self.runtime.copy()
        self.codex.ACTIVE_STATES = {"starting", "running", "awaiting_input"}
        self.codex._submission_lock = threading.RLock()
        self.codex._run_lock = threading.Lock()
        self.syncing = False
        self.guard = UpdateTaskGuard(self.ledger, self.codex, syncing=lambda: self.syncing)

    def tearDown(self):
        self.temp.cleanup()

    def activity(self, source="web_app_server", state="running"):
        return self.ledger.start_activity(action="parse_resume", source=source, state=state, label="解析简历")

    def test_paused_disconnected_web_history_never_blocks_update(self):
        old = self.activity()
        result = self.guard.snapshot()
        self.assertFalse(result["blocked"])
        self.assertEqual(result["stale_web"][0]["run_id"], old["run_id"])
        # Read-only status doesn't change history; reconciliation preserves the record.
        self.assertEqual(self.ledger.get_activity(old["run_id"])["state"], "running")
        self.assertEqual(self.guard.reconcile()["reconciled"], 1)
        self.assertEqual(self.ledger.get_activity(old["run_id"])["state"], "paused")
        self.assertEqual(len(self.ledger.activity_events(old["run_id"])), 2)

    def test_live_start_running_and_permission_wait_still_block_even_before_connection(self):
        for state in self.codex.ACTIVE_STATES:
            self.runtime["state"] = state
            result = self.guard.snapshot()
            self.assertTrue(result["blocked"])
            self.assertEqual(result["blockers"][0]["kind"], "codex")
            self.assertFalse(result["blockers"][0]["clearable"])
        self.runtime["state"] = "paused"
        with self.codex._run_lock:
            self.assertTrue(self.guard.snapshot()["blocked"])

    def test_current_live_web_activity_cannot_be_reconciled(self):
        automation = self.ledger.request_automation("parse_resume", source="web_app_server")
        self.runtime["state"] = "running"
        run_id = automation["activity_run_id"]
        revision = self.ledger.activity_events(run_id)[0]["id"]
        with self.assertRaisesRegex(ValueError, "状态已变化"):
            self.guard.reconcile([{"run_id": run_id, "revision": revision}], confirmed=True)
        self.assertNotEqual(self.ledger.get_activity(run_id)["state"], "paused")

    def test_external_history_is_unverified_not_automatically_expired(self):
        old = self.activity(source="codex_conversation", state="waiting")
        with self.ledger.connect() as db:
            db.execute("UPDATE activity_runs SET updated_at='2000-01-01T00:00:00Z' WHERE run_id=?", (old["run_id"],))
        result = self.guard.snapshot()
        entry = result["blockers"][0]
        self.assertEqual(entry["kind"], "unverified_activity")
        self.assertEqual(self.guard.reconcile()["reconciled"], 0)
        with self.assertRaisesRegex(ValueError, "先确认"):
            self.guard.reconcile([entry])
        profile = self.ledger.update_profile({"name": "保留的资料"})
        app = self.ledger.record_application(company="示例", title="后端", url="https://example.com/job", status="opened")
        app = self.ledger.get_application(app["id"])
        result = self.guard.reconcile([entry], confirmed=True)
        self.assertFalse(result["blocked"])
        self.assertEqual(self.ledger.profile(), profile)
        self.assertEqual(self.ledger.get_application(app["id"]), app)
        self.codex.pause.assert_not_called()

    def test_stale_confirmation_rejected_even_if_update_time_is_same_second(self):
        run = self.activity(source="codex_conversation")
        entry = self.guard.snapshot()["blockers"][0]
        self.ledger.update_activity(run["run_id"], message="已重新开始")
        with self.ledger.connect() as db:
            db.execute("UPDATE activity_runs SET updated_at=? WHERE run_id=?", (entry["updated_at"], run["run_id"]))
        with self.assertRaisesRegex(ValueError, "状态已变化"):
            self.guard.reconcile([entry], confirmed=True)
        self.assertEqual(self.ledger.get_activity(run["run_id"])["state"], "running")

    def test_data_sync_blocks_and_ended_records_do_not(self):
        self.activity(state="completed")
        self.activity(source="codex_conversation", state="paused")
        self.assertFalse(self.guard.snapshot()["blocked"])
        self.syncing = True
        self.assertEqual(self.guard.snapshot()["blockers"][0]["kind"], "data_sync")

    def test_late_resume_startup_reply_cannot_resurrect_paused_or_failed_import(self):
        for status in ("paused", "failed"):
            self.ledger.set_kv("profile_import", {"request_id": "old-import", "status": status})
            result = self.ledger.update_profile_import_progress("old-import", status="running", message="迟到的启动回执")
            self.assertEqual(result["status"], status)

    def test_all_active_records_checked_beyond_ui_limit(self):
        for _ in range(55):
            self.activity(source="codex_conversation")
        self.assertEqual(len(self.guard.snapshot()["blockers"]), 55)

    def test_server_restart_reconciles_only_owned_history_and_unblocks_manual_update(self):
        run = self.activity()
        self.ledger.set_kv("github_update", {"state": "waiting", "available": True, "auto_update": False})
        server = DashboardServer(("127.0.0.1", 0), self.ledger)
        try:
            self.assertEqual(self.ledger.get_activity(run["run_id"])["state"], "paused")
            self.assertFalse(server._update_busy())
            self.assertEqual(server.updater.status()["state"], "available")
            with patch.object(server.updater, "check", return_value={"available": True, "latest_commit": "a"*40}), patch.object(server.updater, "install") as install:
                server.updater.request("install")
                server.updater._worker.join(3)
                install.assert_called_once()
        finally:
            server.server_close()


if __name__ == "__main__":
    unittest.main()
