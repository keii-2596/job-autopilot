import tempfile
import unittest
from pathlib import Path

from job_autopilot.db import Ledger, canonicalize_url


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.temp.name) / "state.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_canonicalize_url_removes_tracking_but_keeps_job_identity(self):
        value = canonicalize_url(
            "HTTPS://Zhaopin.Example.com/job/?utm_source=x&jobId=42&ref=feed#apply"
        )
        self.assertEqual(value, "https://zhaopin.example.com/job?jobId=42")

    def test_canonicalize_url_preserves_spa_job_route(self):
        value = canonicalize_url(
            "https://campus.example.com/recruit/#/campus/job-info/13613"
        )
        self.assertEqual(
            value,
            "https://campus.example.com/recruit#/campus/job-info/13613",
        )

    def test_jdwatch_id_prevents_duplicate_records(self):
        first = self.ledger.record_application(
            title="Backend Engineer",
            company="Example",
            url="https://jobs.example.com/one",
            jdwatch_id=42,
        )
        second = self.ledger.record_application(
            title="Backend Engineer renamed",
            company="Example",
            url="https://jobs.example.com/another-url",
            jdwatch_id=42,
        )
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(self.ledger.summary()["total"], 1)

    def test_normalized_url_prevents_duplicate_records_without_id(self):
        self.ledger.record_application(
            title="Agent Engineer",
            company="Example",
            url="https://jobs.example.com/apply?id=9&utm_campaign=fall#form",
        )
        duplicate = self.ledger.record_application(
            title="Agent Engineer",
            company="Example",
            url="https://jobs.example.com/apply?utm_source=mail&id=9",
        )
        self.assertTrue(duplicate["duplicate"])

    def test_status_update_writes_timestamps_and_event(self):
        item = self.ledger.record_application(
            title="Agent Platform",
            company="Example",
            url="https://jobs.example.com/agent",
        )
        ready = self.ledger.update_application(item["id"], status="ready_for_review")
        submitted = self.ledger.update_application(
            item["id"], status="submitted", confirmation_ref="A-100"
        )
        self.assertIsNotNone(ready["started_at"])
        self.assertIsNotNone(submitted["submitted_at"])
        self.assertEqual(submitted["confirmation_ref"], "A-100")
        self.assertGreaterEqual(len(self.ledger.events(item["id"])), 3)

    def test_site_is_remembered(self):
        self.ledger.record_application(
            title="Backend",
            company="Example",
            url="https://careers.example.com/jobs/1",
        )
        self.ledger.mark_site_login("https://careers.example.com/login", "phone_otp")
        sites = self.ledger.list_sites()
        self.assertEqual(sites[0]["domain"], "careers.example.com")
        self.assertEqual(sites[0]["login_method"], "phone_otp")
        self.assertIsNotNone(sites[0]["last_login_at"])

    def test_settings_are_local_and_validated(self):
        settings = self.ledger.update_settings(
            {
                "submission_mode": "automatic",
                "allowed_domains": ["Jobs.Example.com", "jobs.example.com", ""],
            }
        )
        self.assertEqual(settings["submission_mode"], "automatic")
        self.assertEqual(settings["allowed_domains"], ["jobs.example.com"])
        self.assertEqual(settings["login_method"], "phone_otp")
        self.assertTrue(settings["autofill_profile_phone"])
        self.assertTrue(settings["read_otp_via_adb"])
        self.assertTrue(settings["auto_accept_standard_agreements"])
        self.assertTrue(settings["solve_captcha_automatically"])
        with self.assertRaises(ValueError):
            self.ledger.update_settings({"submission_mode": "reckless"})
        with self.assertRaises(ValueError):
            self.ledger.update_settings({"login_method": "email"})
        with self.assertRaises(ValueError):
            self.ledger.update_settings({"read_otp_via_adb": "yes"})
        with self.assertRaises(ValueError):
            self.ledger.update_settings({"auto_accept_standard_agreements": "yes"})
        with self.assertRaises(ValueError):
            self.ledger.update_settings({"solve_captcha_automatically": "yes"})

    def test_source_jobs_support_multiple_keywords_and_locations(self):
        self.ledger.upsert_source_jobs(
            [
                {
                    "source": "baigua",
                    "source_key": "agent-shanghai",
                    "company_name": "示例公司",
                    "title": "Agent 平台工程师",
                    "locations": ["上海"],
                },
                {
                    "source": "baigua",
                    "source_key": "backend-hangzhou",
                    "company_name": "另一家公司",
                    "title": "后端开发工程师",
                    "locations": ["杭州"],
                },
                {
                    "source": "baigua",
                    "source_key": "sales-beijing",
                    "company_name": "第三家公司",
                    "title": "销售培训生",
                    "locations": ["北京"],
                },
            ]
        )

        matches = self.ledger.list_source_jobs(
            query="Agent,后端", location="上海、杭州", limit=10
        )
        self.assertEqual(
            {item["source_key"] for item in matches},
            {"agent-shanghai", "backend-hangzhou"},
        )

    def test_application_requires_real_identity_fields_and_url(self):
        with self.assertRaisesRegex(ValueError, "title is required"):
            self.ledger.record_application(
                title=" ", company="Example", url="https://jobs.example.com/1"
            )
        with self.assertRaisesRegex(ValueError, "company is required"):
            self.ledger.record_application(
                title="Backend", company=" ", url="https://jobs.example.com/1"
            )
        with self.assertRaisesRegex(ValueError, "valid application URL"):
            self.ledger.record_application(
                title="Backend", company="Example", url="https://"
            )

    def test_frontend_can_request_and_pause_an_ai_run(self):
        requested = self.ledger.request_automation(
            "discover_and_apply",
            {"keywords": ["Agent"], "locations": ["上海"]},
            source="web_app_server",
        )
        self.assertEqual(requested["state"], "requested")
        self.assertEqual(requested["criteria"]["keywords"], ["Agent"])
        activity = self.ledger.get_activity(requested["activity_run_id"])
        self.assertEqual(activity["source"], "web_app_server")
        paused = self.ledger.set_automation_state("paused", "user paused")
        self.assertEqual(paused["request_id"], requested["request_id"])
        self.assertEqual(paused["state"], "paused")
        self.assertEqual(
            self.ledger.get_activity(requested["activity_run_id"])["state"], "paused"
        )

    def test_shared_activity_tracks_job_progress_and_events(self):
        application = self.ledger.record_application(
            title="Agent Engineer",
            company="Example",
            url="https://jobs.example.com/agent-1",
        )
        run = self.ledger.start_activity(
            action="discover_and_apply",
            label="查找校招岗位",
            progress_total=3,
        )
        updated = self.ledger.update_activity(
            run["run_id"],
            state="running",
            stage="filling",
            message="正在填写第 2 个岗位",
            application_id=application["id"],
            progress_current=2,
        )
        self.assertEqual(updated["application_title"], "Agent Engineer")
        self.assertEqual(updated["progress_current"], 2)
        snapshot = self.ledger.activity_snapshot()
        self.assertEqual(snapshot["current"]["run_id"], run["run_id"])
        self.assertEqual(snapshot["events"][0]["stage"], "filling")
        self.assertEqual(len(snapshot["events"]), 2)

    def test_activity_messages_redact_obvious_sensitive_values(self):
        run = self.ledger.start_activity(
            action="test",
            message="验证码 123456，手机号 13800138000，邮箱 me@example.com，key sk_redaction_test_abcdef123456",
        )
        self.assertNotIn("123456", run["message"])
        self.assertNotIn("13800138000", run["message"])
        self.assertNotIn("me@example.com", run["message"])
        self.assertNotIn("sk_redaction_test_abcdef123456", run["message"])
        self.assertGreaterEqual(run["message"].count("[已隐藏]"), 4)

    def test_codex_runtime_is_persistent_and_has_safe_defaults(self):
        initial = self.ledger.codex_runtime()
        self.assertEqual(initial["state"], "idle")
        self.assertIsNone(initial["pending_request"])
        running = self.ledger.set_codex_runtime(
            state="running", thread_id="thread-1", message="Codex 正在执行"
        )
        self.assertEqual(running["thread_id"], "thread-1")
        self.assertEqual(self.ledger.codex_runtime()["state"], "running")

    def test_confirmed_dynamic_fields_are_reusable_and_merge_aliases(self):
        first = self.ledger.upsert_profile_field(
            {
                "key": "work_authorization_detail",
                "label": "工作许可说明",
                "value": "无需赞助",
                "aliases": ["Will you require sponsorship?"],
            }
        )
        second = self.ledger.upsert_profile_field(
            {
                "key": "work_authorization_detail",
                "label": "工作许可说明",
                "value": "无需赞助",
                "aliases": ["是否需要签证赞助"],
            }
        )
        fields = self.ledger.profile()["custom_fields"]
        self.assertEqual(first["key"], second["key"])
        self.assertEqual(len(fields), 1)
        self.assertEqual(len(fields[0]["aliases"]), 2)

    def test_resume_suggestions_require_confirmation_before_profile_write(self):
        resume = Path(self.temp.name) / "resume.txt"
        resume.write_text("Example resume", encoding="utf-8")
        requested = self.ledger.request_profile_import(str(resume))
        self.assertEqual(requested["status"], "requested")
        ready = self.ledger.complete_profile_import(
            [
                {"target_key": "major", "label": "专业", "value": "软件工程"},
                {
                    "target_key": "preferred_stack",
                    "label": "偏好技术栈",
                    "value": "Python",
                    "aliases": ["Primary stack"],
                },
            ]
        )
        self.assertNotIn("major", self.ledger.profile())
        chosen = [item["id"] for item in ready["suggestions"]]
        profile = self.ledger.apply_profile_import(chosen)
        self.assertEqual(profile["major"], "软件工程")
        self.assertEqual(profile["custom_fields"][0]["key"], "preferred_stack")


if __name__ == "__main__":
    unittest.main()
