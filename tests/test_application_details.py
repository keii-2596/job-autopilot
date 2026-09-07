import sqlite3
import tempfile
import unittest
from pathlib import Path
from job_autopilot.db import Ledger

class ApplicationDetailsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.db"
        self.ledger = Ledger(self.path)
        self.item = self.ledger.record_application(title="AI、算法、产品等", company="Example", url="https://example.com/jobs", locations=["上海", "北京"])

    def tearDown(self):
        self.temp.cleanup()

    def test_actual_role_preserves_source_and_survives_reopening(self):
        self.ledger.update_application(self.item["id"], actual_title="Agent开发工程师", actual_locations=["上海"], actual_url="https://example.com/jobs/42", status="submitted", confirmation_ref="received")
        item = Ledger(self.path).get_application(self.item["id"])
        self.assertEqual(item["title"], self.item["title"])
        self.assertEqual(item["locations"], ["上海", "北京"])
        self.assertEqual(item["actual_locations"], ["上海"])
        self.assertTrue(self.ledger.check_duplicate("https://example.com/jobs/42")["duplicate"])
        run = self.ledger.start_activity(source="codex_conversation", action="process_queue", label="Test")
        run = self.ledger.update_activity(run["run_id"], application_id=item["id"])
        self.assertEqual(run["application_title"], "Agent开发工程师")

    def test_archive_is_reversible_and_does_not_change_submission(self):
        item = self.ledger.update_application(self.item["id"], status="submitted", confirmation_ref="received")
        archived = self.ledger.update_application(item["id"], archived=True)
        self.assertEqual(archived["status"], "submitted")
        self.assertEqual(archived["submitted_at"], item["submitted_at"])
        self.assertEqual(archived["confirmation_ref"], "received")
        self.assertEqual(self.ledger.summary()["archived"], 1)
        self.assertNotIn("submitted", self.ledger.summary()["unarchived_by_status"])
        self.assertFalse(self.ledger.update_application(item["id"], archived=False)["archived"])

    def test_upgrade_old_schema_is_idempotent(self):
        with sqlite3.connect(self.path) as db:
            for name in ("actual_title", "actual_url", "actual_locations_json", "archived"):
                db.execute(f"ALTER TABLE applications DROP COLUMN {name}")
        upgraded = Ledger(self.path)
        self.assertEqual(upgraded.get_application(self.item["id"])["actual_title"], "")
        self.assertFalse(Ledger(self.path).get_application(self.item["id"])["archived"])

    def test_preferences_merge_and_flow_into_new_task(self):
        self.ledger.update_settings({"job_preferences": {"locations": ["上海"], "excluded_companies": ["Example"], "employment_type": "full_time"}})
        self.ledger.update_settings({"job_preferences": {"keywords": ["Agent"]}})
        run = self.ledger.request_automation("discover_and_apply", {"locations": ["杭州"]})
        self.assertEqual(run["criteria"]["locations"], ["杭州"])
        self.assertEqual(run["criteria"]["excluded_companies"], ["Example"])
        self.assertEqual(run["criteria"]["employment_type"], "full_time")
        self.assertEqual(self.ledger.settings()["job_preferences"]["locations"], ["上海"])
        with self.assertRaises(ValueError):
            self.ledger.update_settings({"job_preferences": {"locations": "上海"}})

    def test_invalid_details_are_rejected_without_changing_status(self):
        with self.assertRaises(ValueError):
            self.ledger.update_application(self.item["id"], actual_url="javascript:alert(1)", status="submitted")
        self.assertEqual(self.ledger.get_application(self.item["id"])["status"], "queued")

    def test_mcp_preserves_preferences_and_exposes_actual_roles(self):
        from job_autopilot.mcp_server import JobAutopilotMCP
        self.ledger.update_settings({"job_preferences": {"locations": ["上海"], "keywords": ["应用开发"], "excluded_keywords": ["算法"]}})
        self.ledger.update_application(self.item["id"], actual_title="Agent开发", actual_locations=["上海"], archived=True)
        mcp = JobAutopilotMCP(self.ledger)
        snapshot = mcp.state()
        self.assertEqual(snapshot["applications"][0]["actual_title"], "Agent开发")
        self.assertTrue(snapshot["applications"][0]["archived"])
        self.assertEqual(snapshot["settings"]["job_preferences"]["locations"], ["上海"])
        mcp.call_tool("start_job_autopilot", {"action": "process_queue"})
        self.assertEqual(self.ledger.automation()["criteria"]["locations"], ["上海"])
        self.assertEqual(self.ledger.automation()["criteria"]["excluded_keywords"], ["算法"])
