import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from job_autopilot.db import Ledger
from job_autopilot.web import DashboardServer


class ResumeStreamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ledger = Ledger(self.root / "state.db")
        self.server = DashboardServer(("127.0.0.1", 0), self.ledger)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.connections = []

    def tearDown(self):
        self.server.stopping.set()
        for connection in self.connections:
            connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(2)
        self.temp.cleanup()

    def connect(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        self.connections.append(connection)
        return connection

    def stream(self):
        connection = self.connect()
        connection.request("GET", "/api/profile/import/events")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertIn("text/event-stream", response.getheader("Content-Type"))
        return response

    def snapshot(self, response):
        for _ in range(40):
            line = response.readline().decode()
            if line.startswith("data: "):
                return json.loads(line[6:])
        self.fail("No streamed snapshot")

    def test_stream_delta_and_reconnect_replay(self):
        resume = self.root / "resume.txt"
        resume.write_text("Example resume", encoding="utf-8")
        request = self.ledger.request_profile_import(str(resume))
        response = self.stream()
        self.assertEqual(self.snapshot(response)["status"], "requested")
        self.ledger.update_profile_import_progress(request["request_id"], status="running", message="理解简历", delta="开始", item_id="one")
        self.assertEqual(self.snapshot(response)["agent_messages"][0]["text"], "开始")
        self.ledger.update_profile_import_progress(request["request_id"], delta="解析", item_id="one")
        self.assertEqual(self.snapshot(response)["agent_messages"][0]["text"], "开始解析")
        replay = self.snapshot(self.stream())
        self.assertEqual(replay["request_id"], request["request_id"])
        self.assertEqual(replay["agent_messages"][0]["text"], "开始解析")

    def test_busy_http_request_leaves_automation_and_profile_untouched(self):
        original = self.ledger.request_automation("process_queue")
        profile = self.ledger.profile_import()
        self.ledger.set_codex_runtime(state="starting")
        connection = self.connect()
        connection.request("POST", "/api/profile/resume-request", json.dumps({"resume_path": "/missing.pdf"}), {"Content-Type": "application/json"})
        response = connection.getresponse()
        self.assertEqual(response.status, 400)
        self.assertIn("当前任务未被更改", json.loads(response.read())["error"])
        self.assertEqual(self.ledger.automation(), original)
        self.assertEqual(self.ledger.profile_import(), profile)

    def test_update_controls_require_same_origin_and_explicit_json_header(self):
        for headers in [{}, {"X-Job-Autopilot": "dashboard", "Origin": "https://evil.example"}, {"X-Job-Autopilot": "dashboard", "Host": "evil.example"}]:
            connection = self.connect()
            connection.request("POST", "/api/updates/settings", '{"auto_update":true}', headers)
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
        self.assertFalse(self.server.updater.status()["auto_update"])
        connection = self.connect()
        connection.request("POST", "/api/updates/settings", '{"auto_update":true}', {"X-Job-Autopilot": "dashboard", "Content-Type": "application/json"})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertTrue(json.loads(response.read())["auto_update"])

    def test_job_sync_controls_are_guarded_and_independent_of_plugin_updates(self):
        for endpoint in ("settings", "start"):
            for headers in [{}, {"X-Job-Autopilot": "dashboard", "Origin": "https://evil.example"}, {"X-Job-Autopilot": "dashboard", "Host": "evil.example"}]:
                connection = self.connect()
                connection.request("POST", "/api/job-library-sync/" + endpoint, '{"auto_sync":true}', headers)
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
        connection = self.connect()
        connection.request("POST", "/api/job-library-sync/settings", '{"auto_sync":true}', {"X-Job-Autopilot": "dashboard", "Content-Type": "application/json"})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertTrue(json.loads(response.read())["auto_sync"])
        self.assertFalse(self.server.updater.status()["auto_update"])
        self.assertFalse(self.server.job_library_sync._worker)

    def test_reconcile_requires_origin_and_confirmation_then_returns_live_guard(self):
        run = self.ledger.start_activity(action="discover_and_apply", source="codex_conversation")
        entry = self.server.update_guard.snapshot()["blockers"][0]
        payload = json.dumps({"entries": [entry], "confirmed": True})
        for headers in ({}, {"X-Job-Autopilot": "dashboard", "Origin": "https://evil.example"}):
            connection = self.connect()
            connection.request("POST", "/api/updates/reconcile", payload, headers)
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
        self.assertEqual(self.ledger.get_activity(run["run_id"])["state"], "running")
        connection = self.connect()
        connection.request("POST", "/api/updates/reconcile", payload, {"X-Job-Autopilot": "dashboard", "Content-Type": "application/json"})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        result = json.loads(response.read())
        self.assertEqual(result["reconciled"], 1)
        self.assertFalse(result["task_guard"]["blocked"])


if __name__ == "__main__":
    unittest.main()
