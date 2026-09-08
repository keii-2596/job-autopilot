import tempfile
import unittest
from pathlib import Path

from job_autopilot.db import Ledger
from job_autopilot.policy import check_domain


class PolicyTests(unittest.TestCase):
    def test_rules_match_only_the_complete_hostname(self):
        cases = [
            ("https://other.example/jobs", ["*"], True),
            ("https://Jobs.Example.com/jobs", ["jobs.example.com"], True),
            ("jobs.example.com", ["*.example.com"], True),
            ("example.com", ["*.example.com"], False),
            ("example.com.evil.test", ["*.example.com"], False),
            ("https://example.com@evil.test/jobs", ["example.com"], False),
            ("https://evil.test/example.com", ["example.com"], False),
            ("jobs1.example.com", ["jobs?.example.com"], True),
            ("jobs12.example.com", ["jobs?.example.com"], False),
            ("JOBS12.example.com", [r"re:^jobs\d+\.example\.com$"], True),
            ("jobs12.example.com.evil.test", [r"re:jobs\d+\.example\.com"], False),
        ]
        for url, rules, expected in cases:
            with self.subTest(url=url, rules=rules):
                self.assertEqual(check_domain(url, rules)["allowed"], expected)

    def test_invalid_rule_does_not_overwrite_saved_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "test.db")
            ledger.update_settings({"allowed_domains": ["*"], "submission_mode": "automatic"})
            with self.assertRaises(ValueError):
                ledger.update_settings({"allowed_domains": ["re:["]})
            self.assertEqual(ledger.settings()["allowed_domains"], ["*"])
            self.assertTrue(ledger.check_domain_policy("example.com")["automatic"])
            ledger.update_settings({"submission_mode": "review"})
            self.assertFalse(ledger.check_domain_policy("example.com")["automatic"])

    def test_project_path_validation_and_regex_case_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "test.db")
            pattern = r"re:jobs\D+\.example\.com"
            ledger.update_settings({"codex_project_path": tmp, "allowed_domains": [pattern]})
            self.assertEqual(ledger.settings()["codex_project_path"], str(Path(tmp).resolve()))
            self.assertTrue(ledger.check_domain_policy("jobshr.example.com")["allowed"])
            self.assertFalse(ledger.check_domain_policy("jobs12.example.com")["allowed"])
            with self.assertRaises(ValueError):
                ledger.update_settings({"codex_project_path": "relative-project"})

    def test_new_directions_filter_actual_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "test.db")
            ledger.upsert_source_jobs([
                {"source": "baigua", "source_key": key, "title": title}
                for key, title in [("front", "前端开发工程师"), ("back", "后端开发工程师"), ("qa", "软件测试工程师"), ("hr", "人力资源专员")]
            ])
            for direction, key in [("frontend", "front"), ("backend", "back"), ("qa", "qa")]:
                self.assertEqual([x["source_key"] for x in ledger.list_source_jobs(direction=direction)], [key])
