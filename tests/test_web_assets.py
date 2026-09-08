from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from job_autopilot.web import _load_static_assets


ASSET_ROOT = Path(__file__).parents[1] / "runtime" / "job_autopilot" / "web_static"


class StaticAssetTests(unittest.TestCase):
    def test_assets_remain_available_after_source_directory_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "index.html").write_bytes(b"standalone")
            (root / "embedded.html").write_bytes(b"embedded")
            assets = _load_static_assets(root)

        self.assertEqual(assets["index.html"], b"standalone")
        self.assertEqual(assets["embedded.html"], b"embedded")

    def test_incomplete_asset_bundle_fails_at_startup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "index.html").write_bytes(b"standalone")

            with self.assertRaisesRegex(RuntimeError, "assets are incomplete"):
                _load_static_assets(root)

    def test_both_frontends_render_the_shared_activity_stream(self) -> None:
        embedded = (ASSET_ROOT / "embedded.html").read_text(encoding="utf-8")
        dashboard = (ASSET_ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn("/api/activity", embedded)
        self.assertIn('id="activity-list"', embedded)
        self.assertIn("/api/activity", dashboard)
        self.assertIn("renderActivity", dashboard)

    def test_dashboard_exposes_freeform_codex_conversation(self) -> None:
        page = (ASSET_ROOT / "index.html").read_text(encoding="utf-8")
        dashboard = (ASSET_ROOT / "app.js").read_text(encoding="utf-8")
        backend = (Path(__file__).parents[1] / "runtime" / "job_autopilot" / "web.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('id="codex-chat-form"', page)
        self.assertIn('id="codex-chat-input"', page)
        self.assertIn('/api/codex/message', dashboard)
        self.assertIn('/api/codex/message', backend)

    def test_dashboard_defaults_to_technical_jobs_without_sync_controls(self) -> None:
        dashboard = (ASSET_ROOT / "app.js").read_text(encoding="utf-8")
        page = (ASSET_ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn('sourceJobDirection: "tech"', dashboard)
        self.assertIn('id="source-job-direction"', page)
        self.assertNotIn('id="source-auth-button"', page)
        self.assertNotIn('id="source-sync-button"', page)
        self.assertNotIn("Cookie 请求头值", page)


if __name__ == "__main__":
    unittest.main()
