import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from job_autopilot.db import Ledger
from job_autopilot.job_library_sync import GitHubJobLibrary, normalize_snapshot

COMMIT = "a" * 40


def job(key="one", **changes):
    return {"source": "baigua", "source_key": key, "title": "后端工程师", "company_name": "示例公司",
            "target_graduates": ["2027届"], "recruitment_batches": ["秋招"], "locations": ["上海"],
            "apply_url": f"https://jobs.example.com/{key}", **changes}


class JobLibrarySyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.temp.name) / "state.db")
        self.sync = GitHubJobLibrary(self.ledger)

    def tearDown(self):
        self.sync.close()
        if self.sync._worker:
            self.sync._worker.join(3)
        self.temp.cleanup()

    def merge(self, records, commit=COMMIT):
        rows, total = normalize_snapshot(records)
        return self.sync.merge(rows, commit, "b" * 40, total)

    def test_new_then_incremental_change_is_idempotent(self):
        first = self.merge([job(), job("two")])
        self.assertEqual(first["counts"]["created"], 2)
        old_id = self.ledger.list_source_jobs()[0]["id"]
        second = self.merge([job(locations=["杭州"]), job("two"), job("three")], "c" * 40)
        self.assertEqual(second["counts"]["created"], 1)
        self.assertEqual(second["counts"]["changed"], 1)
        third = self.merge([job(locations=["杭州"]), job("two"), job("three")])
        self.assertEqual(third["counts"], {"created": 0, "changed": 0, "unchanged": 3, "preserved": 0})
        self.assertTrue(self.ledger.get_source_job(old_id))

    def test_local_profile_applications_and_notes_are_not_imported_or_deleted(self):
        self.ledger.update_profile({"name": "本地姓名"})
        application = self.ledger.record_application(title="后端工程师", company="示例公司", url="https://jobs.example.com/one", status="submitted", notes="个人投递备注")
        application = self.ledger.get_application(application["id"])
        self.ledger.upsert_source_jobs([job(notes="本地职位备注"), job("local-only")])
        self.merge([job(notes="远程备注不得覆盖", phone="secret", id=999, content_hash="untrusted")])
        self.assertEqual(self.ledger.profile()["name"], "本地姓名")
        self.assertEqual(self.ledger.get_application(application["id"]), application)
        self.assertEqual(len(self.ledger.list_source_jobs()), 2)
        row = next(item for item in self.ledger.list_source_jobs() if item["source_key"] == "one")
        self.assertEqual(row["notes"], "本地职位备注")
        self.assertNotEqual(row["id"], 999)

    def test_newer_local_rows_and_changed_local_fields_are_preserved(self):
        self.ledger.upsert_source_jobs([job(locations=["本地地点"], source_updated_at="2026-09-09T00:00:00Z")])
        first = self.merge([job(source_updated_at="2026-09-08T00:00:00Z")])
        self.assertEqual(first["counts"]["preserved"], 1)
        second = self.merge([job(locations=["北京"], deadline_text="9月30日", source_updated_at="2026-09-10T00:00:00Z")])
        row = self.ledger.list_source_jobs()[0]
        self.assertEqual(row["locations"], ["本地地点"])
        self.assertEqual(row["deadline_text"], "9月30日")
        self.assertEqual(second["counts"]["preserved"], 1)

    def test_only_2027_autumn_is_imported(self):
        result = self.merge([job(), job("intern", recruitment_batches=["暑期实习"]), job("2026", target_graduates=["2026届"])])
        self.assertEqual(result["scoped_total"], 1)
        self.assertEqual(result["remote_total"], 3)

    def test_old_browser_key_matches_same_notice_without_creating_duplicate(self):
        self.ledger.upsert_source_jobs([job("local", apply_url="https://jobs.example.com/same")])
        result = self.merge([job("remote", apply_url="https://jobs.example.com/same")])
        self.assertEqual(result["counts"]["created"], 0)
        self.assertEqual(self.ledger.list_source_jobs()[0]["source_key"], "local")

    def test_distinct_public_identifiers_are_not_collapsed_by_shared_career_url(self):
        records = [job("one", apply_url="https://jobs.example.com/"), job("two", apply_url="https://jobs.example.com/")]
        self.assertEqual(self.merge(records)["counts"]["created"], 2)
        self.assertEqual(self.merge(records)["counts"]["unchanged"], 2)

    def test_bad_snapshot_is_rejected_before_any_write(self):
        for bad in [[job(), job()], [job(), job("bad", locations="上海")], [job(apply_url="javascript:alert(1)")], [job("other", recruitment_batches=["日常实习"])]]:
            with self.assertRaises(ValueError):
                self.merge(bad)
            self.assertEqual(self.ledger.list_source_jobs(), [])

    def test_failure_rolls_back_jobs_baselines_and_cursor(self):
        initial = self.merge([job()])
        original = self.ledger.list_source_jobs()
        with patch.object(self.ledger, "upsert_source_jobs", side_effect=RuntimeError("database failed")):
            with self.assertRaises(RuntimeError):
                self.merge([job(locations=["杭州"]), job("new")], "c" * 40)
        self.assertEqual(self.ledger.list_source_jobs(), original)
        self.assertEqual(self.sync.status()["synced_commit"], initial["synced_commit"])
        with self.ledger.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM github_job_baseline").fetchone()[0], 1)

    def test_email_text_misformatted_as_http_is_not_imported_as_a_link(self):
        result = self.merge([job(apply_url="http://%E9%82%AE%E7%AE%B1%E6%8A%95%E9%80%92%EF%BC%9Azpc@wch.cn/")])
        self.assertEqual(result["ignored_links"], 1)
        self.assertEqual(self.ledger.list_source_jobs()[0]["apply_url"], "")

    def test_unchanged_file_commit_skips_download_and_rows(self):
        self.merge([job()])
        with patch.object(self.sync, "_api", return_value=[{"sha": COMMIT}]) as api, patch("job_autopilot.job_library_sync.GitHubUpdater._fetch") as fetch:
            result = self.sync.sync()
        self.assertFalse(result["downloaded"])
        api.assert_called_once()
        fetch.assert_not_called()

    def test_blob_verification_and_real_merge(self):
        content = json.dumps([job()]).encode()
        blob = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
        metadata = {"sha": blob, "type": "file", "size": len(content)}
        with patch.object(self.sync, "_api", side_effect=[[{"sha": COMMIT}], metadata]), patch("job_autopilot.job_library_sync.GitHubUpdater._fetch", return_value=content):
            result = self.sync.sync()
        self.assertEqual(result["counts"]["created"], 1)
        with patch.object(self.sync, "_api", side_effect=[[{"sha": "c"*40}], {"status": "ahead"}, {**metadata, "sha": "f"*40}]), patch("job_autopilot.job_library_sync.GitHubUpdater._fetch", return_value=content):
            with self.assertRaisesRegex(ValueError, "校验"):
                self.sync.sync()
        self.assertEqual(self.sync.status()["synced_commit"], COMMIT)

    def test_rollback_history_is_not_applied(self):
        self.merge([job()])
        with patch.object(self.sync, "_api", side_effect=[[{"sha": "d"*40}], {"status": "behind"}]):
            with self.assertRaisesRegex(ValueError, "回退"):
                self.sync.sync()

    def test_raw_network_failure_uses_same_verified_blob_through_github_api(self):
        content = json.dumps([job()]).encode()
        blob = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
        fallback = json.dumps({"sha": blob, "encoding": "base64", "content": base64.b64encode(content).decode()}).encode()
        with patch.object(self.sync, "_api", side_effect=[[{"sha": COMMIT}], {"sha": blob, "type": "file", "size": len(content)}]), patch("job_autopilot.job_library_sync.GitHubUpdater._fetch", side_effect=[RuntimeError("timeout"), fallback]) as fetch:
            result = self.sync.sync()
        self.assertEqual(result["counts"]["created"], 1)
        self.assertTrue(fetch.call_args.args[0].endswith("/git/blobs/" + blob))

    def test_auto_sync_is_separate_opt_in_and_can_be_cancelled_before_write(self):
        with patch.object(self.sync, "request") as request:
            self.sync.tick()
            request.assert_not_called()
            self.sync.configure(True)
            self.sync.tick()
            request.assert_called_once_with(automatic=True)
        with self.assertRaises(ValueError):
            self.sync.configure("yes")
        self.sync.installing = lambda: True
        with self.assertRaisesRegex(RuntimeError, "插件正在更新"):
            self.sync.request()


if __name__ == "__main__":
    unittest.main()
