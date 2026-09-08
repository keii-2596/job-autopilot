import io
import json
import stat
import subprocess
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch, Mock

from job_autopilot.db import Ledger
from job_autopilot.updater import BASE_COMMIT, CHECK_INTERVAL, GitHubUpdater, UpdateBusy, UpdateConflict, merge_content, read_archive

MANIFEST = json.dumps({"name": "job-autopilot", "version": "0.4.3"}).encode()
NEXT = "a" * 40


class ArchiveTests(unittest.TestCase):
    def archive(self, name, data=b"test", mode=0):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("root/.codex-plugin/plugin.json", MANIFEST)
            entry = zipfile.ZipInfo("root/" + name)
            entry.external_attr = mode << 16
            archive.writestr(entry, data)
        return buffer.getvalue()

    def test_private_files_are_not_distributed_by_updater(self):
        for name in ["runtime/job_autopilot/baigua.py", ".env", "private/cookie.txt", "state.db"]:
            self.assertNotIn(name, read_archive(self.archive(name)))

    def test_unsafe_paths_and_symlinks_are_rejected(self):
        for name, mode in [("../escape", 0), ("runtime/link", stat.S_IFLNK | 0o777)]:
            with self.assertRaises(ValueError):
                read_archive(self.archive(name, mode=mode))

    def test_manifest_is_required(self):
        with self.assertRaises(ValueError):
            read_archive(self.archive(".codex-plugin/plugin.json", b'{}'))

    def test_merge_preserves_local_only_lines_and_adds_upstream_lines(self):
        base = b"one\ntwo\nthree\nfour\nfive\n"
        local = b"private one\ntwo\nthree\nfour\nfive\n"
        remote = b"one\ntwo\nthree\nfour\npublic five\n"
        merged = merge_content("runtime/test.py", local, base, remote)
        self.assertEqual(merged, b"private one\ntwo\nthree\nfour\npublic five\n")

    def test_conflict_does_not_return_conflict_markers(self):
        with self.assertRaises(UpdateConflict):
            merge_content("runtime/test.py", b"local\n", b"base\n", b"remote\n")
        with self.assertRaises(UpdateConflict):
            merge_content("data/test.json", b"local", b"base", None)

    def test_manifest_cachebuster_does_not_block_upstream_version(self):
        local = MANIFEST.replace(b'0.4.3', b'0.4.3+codex.local')
        remote = MANIFEST.replace(b'0.4.3', b'0.4.4')
        merged = merge_content(".codex-plugin/plugin.json", local, MANIFEST, remote)
        self.assertEqual(json.loads(merged)["version"], "0.4.4")


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "job-autopilot"
        (self.source / ".codex-plugin").mkdir(parents=True)
        (self.source / ".codex-plugin/plugin.json").write_bytes(MANIFEST)
        self.ledger = Ledger(self.root / "state.db")
        self.busy = Mock(return_value=False)
        self.updater = GitHubUpdater(self.ledger, self.source, self.busy)
        self.updater.storage.mkdir()
        self.base = {".codex-plugin/plugin.json": MANIFEST, "runtime/example.py": b"VALUE = 1\n"}
        self.remote = {**self.base, "runtime/example.py": b"VALUE = 2\n", "runtime/new.py": b"NEW = True\n"}
        (self.source / "runtime").mkdir()
        (self.source / "runtime/example.py").write_bytes(self.base["runtime/example.py"])
        (self.source / "runtime/private.py").write_text("LOCAL = True\n")

    def tearDown(self):
        self.updater.close()
        if self.updater._worker:
            self.updater._worker.join(5)
        self.temp.cleanup()

    def test_no_remote_commit_does_not_downgrade_local_edits(self):
        with patch.object(self.updater, "_api", return_value={"sha": BASE_COMMIT, "commit": {"message": "note"}}):
            result = self.updater.check()
        self.assertEqual(result["state"], "up_to_date")
        self.assertFalse(result["available"])
        self.assertEqual(result["installed_commit"], BASE_COMMIT)

    def test_only_fast_forward_is_allowed(self):
        for status in ["behind", "diverged"]:
            with patch.object(self.updater, "_api", side_effect=[{"sha": NEXT}, {"status": status}]):
                with self.assertRaises(UpdateConflict):
                    self.updater.check()

    def test_busy_task_is_not_interrupted(self):
        self.busy.return_value = True
        with patch.object(self.updater, "check", return_value={"available": True, "latest_commit": NEXT}), patch.object(self.updater, "install") as install:
            self.updater.request("install")
            self.updater._worker.join(3)
            install.assert_not_called()
        self.assertEqual(self.updater.status()["state"], "waiting")

    def test_auto_update_is_opt_in_and_rate_limited(self):
        with patch.object(self.updater, "request") as request:
            self.updater.tick()
            request.assert_not_called()
            self.updater.configure(True)
            self.updater.tick()
            request.assert_called_once_with("install", automatic=True)
            self.updater.tick()
            request.assert_called_once()
        with self.assertRaises(ValueError):
            self.updater.configure("true")

    def test_install_preserves_profile_and_private_files_and_creates_backup(self):
        profile = self.ledger.update_profile({"name": "Private Candidate"})
        with patch.object(self.updater, "_installation", return_value=(self.source, "personal", self.root)), patch.object(self.updater, "_archive", side_effect=[self.base, self.remote]), patch("job_autopilot.updater.subprocess.run") as run:
            self.updater.install(NEXT)
            self.assertTrue(any(call.args[0][:3] == ["codex", "plugin", "add"] for call in run.call_args_list))
        self.assertEqual((self.source / "runtime/example.py").read_bytes(), b"VALUE = 2\n")
        self.assertEqual((self.source / "runtime/private.py").read_text(), "LOCAL = True\n")
        self.assertEqual(self.ledger.profile(), profile)
        backup = Path(self.updater.status()["backup_path"])
        self.assertEqual((backup / "runtime/example.py").read_bytes(), b"VALUE = 1\n")
        self.assertEqual(self.updater.status()["installed_commit"], NEXT)

    def test_guard_reports_unknown_history_and_releases_wait_without_codex_connection(self):
        guard = {"blocked": True, "blockers": [{"kind": "unverified_activity", "label": "旧投递"}], "stale_web": []}
        self.updater.guard = lambda: guard
        self.updater._save(state="waiting", available=True)
        self.assertEqual(self.updater.status()["state"], "needs_review")
        guard.update(blocked=False, blockers=[])
        self.assertEqual(self.updater.status()["state"], "available")
        self.assertIn("无需连接 Codex", self.updater.status()["message"])
        with patch.object(self.updater, "request") as request:
            self.updater.tick()
            request.assert_not_called()
            self.updater.configure(True)
            self.updater.tick()
            request.assert_called_once_with("install", automatic=True)

    def test_new_task_during_download_defers_before_file_writes(self):
        self.busy.return_value = True
        with patch.object(self.updater, "_installation", return_value=(self.source, "personal", self.root)), patch.object(self.updater, "_archive", side_effect=[self.base, self.remote]):
            with self.assertRaises(UpdateBusy):
                self.updater.install(NEXT)
        self.assertEqual((self.source / "runtime/example.py").read_bytes(), self.base["runtime/example.py"])
        self.assertFalse((self.source / "runtime/new.py").exists())

    def test_install_failure_restores_files_and_removes_only_new_update_files(self):
        with patch.object(self.updater, "_installation", return_value=(self.source, "personal", self.root)), patch.object(self.updater, "_archive", side_effect=[self.base, self.remote]), patch("job_autopilot.updater.subprocess.run", side_effect=subprocess.CalledProcessError(1, ["validation"])):
            with self.assertRaisesRegex(RuntimeError, "源文件已恢复"):
                self.updater.install(NEXT)
        self.assertEqual((self.source / "runtime/example.py").read_bytes(), b"VALUE = 1\n")
        self.assertFalse((self.source / "runtime/new.py").exists())
        self.assertTrue((self.source / "runtime/private.py").exists())
        self.assertEqual(self.updater.status()["installed_commit"], BASE_COMMIT)

    def test_symlink_local_target_is_not_followed(self):
        outside = self.root / "outside.py"
        outside.write_text("PRIVATE\n")
        (self.source / "runtime/example.py").unlink()
        (self.source / "runtime/example.py").symlink_to(outside)
        with patch.object(self.updater, "_installation", return_value=(self.source, "personal", self.root)), patch.object(self.updater, "_archive", side_effect=[self.base, self.remote]):
            with self.assertRaises(ValueError):
                self.updater.install(NEXT)
        self.assertEqual(outside.read_text(), "PRIVATE\n")

    def test_network_failure_is_visible_and_releases_lock(self):
        with patch.object(self.updater, "_api", side_effect=TimeoutError("network timeout")):
            self.updater.request("check")
            self.updater._worker.join(3)
        self.assertEqual(self.updater.status()["state"], "failed")
        self.assertIn("network timeout", self.updater.status()["message"])
        self.assertFalse(self.updater._lock.locked())

    def test_switching_auto_off_during_check_prevents_install(self):
        self.updater.configure(True)
        def check():
            self.updater.configure(False)
            return {"available": True, "latest_commit": NEXT}
        with patch.object(self.updater, "check", side_effect=check), patch.object(self.updater, "install") as install:
            self.updater.request("install", automatic=True)
            self.updater._worker.join(3)
            install.assert_not_called()


if __name__ == "__main__":
    unittest.main()
