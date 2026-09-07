from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from job_autopilot.codex_bridge import CodexRunController
from job_autopilot.db import Ledger


class FakeClient:
    def __init__(self) -> None:
        self.on_message = lambda message: None
        self.running = False
        self.requests: list[tuple[str, dict]] = []
        self.responses: list[tuple[int | str, dict]] = []

    def start(self) -> None:
        self.running = True

    def request(self, method: str, params: dict, timeout: int = 30) -> dict:
        self.requests.append((method, params))
        if method == "thread/start":
            return {"thread": {"id": "thread-1"}}
        if method == "thread/resume":
            return {"thread": {"id": params["threadId"]}}
        if method == "turn/start":
            return {"turn": {"id": "turn-1"}}
        return {}

    def respond(self, request_id: int | str, result: dict) -> None:
        self.responses.append((request_id, result))

    def close(self) -> None:
        self.running = False


class CodexRunControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "skills" / "job-autopilot").mkdir(parents=True)
        (self.root / "skills" / "job-autopilot" / "SKILL.md").write_text(
            "# test skill", encoding="utf-8"
        )
        self.ledger = Ledger(self.root / "state.db")
        self.client = FakeClient()
        self.controller = CodexRunController(self.ledger, self.root, self.client)

    def tearDown(self) -> None:
        self.controller.close()
        self.temp.cleanup()

    def wait_for_state(self, expected: str) -> dict:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            current = self.ledger.codex_runtime()
            activity = self.ledger.activity_snapshot().get("current")
            if current["state"] == expected and activity and activity["state"] == expected:
                return current
            time.sleep(0.01)
        self.fail(f"Codex runtime never reached {expected}")

    @patch("job_autopilot.codex_bridge.shutil.which", return_value="/usr/bin/codex")
    def test_web_run_starts_a_durable_thread_and_turn(self, _which) -> None:
        automation = self.ledger.request_automation(
            "discover_and_apply", {"keywords": ["Agent", "后端"]}
        )

        status = self.controller.start_run(automation)
        self.assertIn(status["state"], {"starting", "running"})
        current = self.wait_for_state("running")
        self.assertEqual(current["thread_id"], "thread-1")
        self.assertEqual(current["turn_id"], "turn-1")

        turn_params = next(params for method, params in self.client.requests if method == "turn/start")
        self.assertIn(automation["request_id"], turn_params["input"][0]["text"])
        self.assertIn(automation["activity_run_id"], turn_params["input"][0]["text"])
        self.assertEqual(turn_params["input"][1]["type"], "skill")
        self.assertTrue(turn_params["sandboxPolicy"]["networkAccess"])
        activity = self.ledger.get_activity(automation["activity_run_id"])
        self.assertEqual(activity["state"], "running")

    def test_user_input_is_answered_but_secret_value_is_not_persisted(self) -> None:
        self.client.running = True
        self.controller._handle_message(
            {
                "id": 42,
                "method": "item/tool/requestUserInput",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "item-1",
                    "isBlocking": True,
                    "questions": [
                        {
                            "id": "otp",
                            "header": "验证码",
                            "question": "请输入短信验证码",
                            "isSecret": True,
                        }
                    ],
                },
            }
        )
        waiting = self.ledger.codex_runtime()
        self.assertEqual(waiting["state"], "awaiting_input")
        self.assertNotIn("threadId", waiting["pending_request"]["params"])

        self.controller.answer("42", {"otp": "123456"})
        self.assertEqual(
            self.client.responses,
            [(42, {"answers": {"otp": {"answers": ["123456"]}}})],
        )
        self.assertNotIn("123456", str(self.ledger.codex_runtime()))

    def test_command_details_are_not_saved_in_the_ledger(self) -> None:
        self.controller._handle_message(
            {
                "id": "approval-1",
                "method": "item/commandExecution/requestApproval",
                "params": {
                    "reason": "需要打开招聘网站",
                    "command": "tool --token secret-value",
                },
            }
        )
        pending = self.ledger.codex_runtime()["pending_request"]
        self.assertEqual(pending["params"], {})
        self.assertNotIn("secret-value", str(pending))

    def test_turn_completion_updates_runtime_and_automation(self) -> None:
        self.ledger.request_automation("process_queue", {})
        self.ledger.set_automation_state("running", "processing")
        self.ledger.set_codex_runtime(
            state="running", turn_id="turn-1", last_agent_message="已处理完队列"
        )

        self.controller._handle_message(
            {"method": "turn/completed", "params": {"turn": {"status": "completed"}}}
        )
        self.assertEqual(self.ledger.codex_runtime()["state"], "completed")
        self.assertEqual(self.ledger.automation()["state"], "completed")
        run_id = self.ledger.automation()["activity_run_id"]
        self.assertEqual(self.ledger.get_activity(run_id)["state"], "completed")


if __name__ == "__main__":
    unittest.main()
