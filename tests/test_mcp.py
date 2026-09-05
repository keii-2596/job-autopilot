import tempfile
import unittest
from pathlib import Path

from job_autopilot.db import Ledger
from job_autopilot.mcp_server import JobAutopilotMCP, UI_URI


class JobAutopilotMCPTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.temp_dir.name) / "state.db")
        self.server = JobAutopilotMCP(self.ledger)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_initialize_and_tool_catalog(self):
        initialized = self.server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            }
        )
        self.assertEqual(initialized["result"]["serverInfo"]["name"], "job-autopilot")

        listing = self.server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = {item["name"]: item for item in listing["result"]["tools"]}
        self.assertIn("open_job_autopilot", tools)
        self.assertIn("start_job_autopilot", tools)
        self.assertEqual(tools["open_job_autopilot"]["_meta"]["ui"]["resourceUri"], UI_URI)

    def test_ui_resource_is_self_contained_mcp_app(self):
        response = self.server.handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "resources/read",
                "params": {"uri": UI_URI},
            }
        )
        content = response["result"]["contents"][0]
        self.assertEqual(content["mimeType"], "text/html;profile=mcp-app")
        self.assertIn("tools/call", content["text"])
        self.assertIn("ui/message", content["text"])
        self.assertNotIn('<script src="', content["text"])

    def test_start_tool_writes_shared_automation_state(self):
        result = self.server.call_tool(
            "start_job_autopilot",
            {
                "action": "discover_and_apply",
                "keywords": ["Agent", "后端"],
                "locations": ["上海"],
                "recruitment_type": "campus",
            },
        )
        self.assertEqual(self.ledger.automation()["state"], "requested")
        self.assertEqual(self.ledger.automation()["criteria"]["keywords"], ["Agent", "后端"])
        self.assertEqual(result["structuredContent"]["automation"]["state"], "requested")
        activity = result["structuredContent"]["activity"]["current"]
        self.assertEqual(activity["source"], "mcp_conversation")
        self.assertEqual(activity["request_id"], self.ledger.automation()["request_id"])

    def test_state_does_not_expose_base_profile_values(self):
        self.ledger.update_profile({"name": "Example Name", "phone": "13000000000"})
        state = self.server.state()
        self.assertNotIn("name", state["profile"])
        self.assertNotIn("phone", state["profile"])
        self.assertEqual(state["profile"]["filled_required"], 2)


if __name__ == "__main__":
    unittest.main()
