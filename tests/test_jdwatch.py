import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from job_autopilot.db import Ledger
from job_autopilot.jdwatch import discover


class JDWatchTests(unittest.TestCase):
    def test_discover_imports_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(Path(directory) / "state.db")
            response = {
                "total": 1,
                "page": 1,
                "jobs": [
                    {
                        "id": 99,
                        "title": "AI Agent 开发工程师",
                        "company": "example",
                        "url": "https://jobs.example.com/99",
                        "work_locations": ["北京"],
                        "recruitment_channel": "campus",
                    }
                ],
            }
            with patch("job_autopilot.jdwatch._run", return_value=response):
                first = discover(ledger, keyword="Agent")
                second = discover(ledger, keyword="Agent")
            self.assertEqual(first["added"], 1)
            self.assertEqual(second["duplicates"], 1)
            self.assertEqual(ledger.list_applications()[0]["jdwatch_id"], 99)


if __name__ == "__main__":
    unittest.main()
