import tempfile
import unittest
from pathlib import Path

from job_autopilot.resume import extract_resume_text


class ResumeTests(unittest.TestCase):
    def test_extract_plain_text_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "resume.txt"
            path.write_text("\n  Keii  \n\nBackend Engineer\n", encoding="utf-8")
            result = extract_resume_text(path)
            self.assertEqual(result["format"], "txt")
            self.assertEqual(result["text"], "Keii\nBackend Engineer")

    def test_reject_relative_path(self):
        with self.assertRaisesRegex(ValueError, "absolute"):
            extract_resume_text("resume.pdf")


if __name__ == "__main__":
    unittest.main()
