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
        self.projects = []

    def start(self) -> None:
        self.running = True

    def request(self, method: str, params: dict, timeout: int = 30) -> dict:
        self.requests.append((method, params))
        if method == "thread/start":
            return {"thread": {"id": "thread-1"}}
        if method == "project/list":
            return {"data": self.projects}
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
        self.ledger.update_settings({"codex_project_path": str(self.root)})
        self.client = FakeClient()
        self.client.projects = [{"id": "project-1", "name": "Test", "roots": [{"path": str(self.root)}]}]
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

    def wait_for_runtime_state(self, expected: str) -> dict:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            current = self.ledger.codex_runtime()
            # Startup persists runtime, import and activity in successive writes.
            # Wait for the fake startup to finish before injecting later events.
            if current["state"] == expected and not self.controller._run_lock.locked():
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
        created = next(params for method, params in self.client.requests if method == "thread/start")
        self.assertEqual(created["projectId"], "project-1")
        activity = self.ledger.get_activity(automation["activity_run_id"])
        self.assertEqual(activity["state"], "running")

    @patch("job_autopilot.codex_bridge.shutil.which", return_value="/usr/bin/codex")
    def test_freeform_message_resumes_thread_and_starts_turn(self, _which) -> None:
        self.ledger.set_codex_runtime(state="completed", thread_id="thread-existing")

        status = self.controller.send_message("只看上海的 Agent 校招岗位")

        self.assertIn(status["state"], {"starting", "running"})
        current = self.wait_for_runtime_state("running")
        self.assertEqual(current["thread_id"], "thread-existing")
        methods = [method for method, _ in self.client.requests]
        self.assertIn("thread/resume", methods)
        turn_params = next(params for method, params in self.client.requests if method == "turn/start")
        self.assertEqual(turn_params["input"][0]["text"], "$job-autopilot\n只看上海的 Agent 校招岗位")
        self.assertEqual(turn_params["input"][1]["type"], "skill")
        self.assertEqual(turn_params["cwd"], str(self.root.resolve()))
        resumed = next(params for method, params in self.client.requests if method == "thread/resume")
        self.assertEqual(resumed["cwd"], str(self.root.resolve()))
        self.assertIn(("thread/metadata/update", {"threadId": "thread-existing", "projectId": "project-1"}), self.client.requests)

    @patch("job_autopilot.codex_bridge.shutil.which", return_value="/usr/bin/codex")
    def test_message_during_active_turn_uses_turn_steer(self, _which) -> None:
        self.client.running = True
        self.ledger.set_codex_runtime(
            state="running", thread_id="thread-1", turn_id="turn-1"
        )

        self.controller.send_message("再排除测试岗位")

        method, params = self.client.requests[-1]
        self.assertEqual(method, "turn/steer")
        self.assertEqual(params["expectedTurnId"], "turn-1")
        self.assertEqual(params["input"], [{"type": "text", "text": "$job-autopilot\n再排除测试岗位"}])

    def test_explicit_skill_is_not_duplicated(self):
        text = "$job-autopilot 请继续"
        self.assertEqual(self.controller._turn_params("thread-1", text)["input"][0]["text"], text)

    def test_release_interrupts_and_closes_but_keeps_thread(self):
        self.client.running = True
        self.ledger.set_codex_runtime(state="running", thread_id="thread-1", turn_id="turn-1")
        status = self.controller.release()
        self.assertEqual(status["state"], "released")
        self.assertFalse(status["connected"])
        self.assertEqual(status["thread_id"], "thread-1")
        self.assertIn(("turn/interrupt", {"threadId": "thread-1", "turnId": "turn-1"}), self.client.requests)

    def test_completed_turn_releases_connection(self):
        self.client.running = True
        self.ledger.set_codex_runtime(state="running", thread_id="thread-1")
        self.controller._handle_message({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
        self.controller._release_thread.join(2)
        self.assertFalse(self.client.running)
        self.assertEqual(self.ledger.codex_runtime()["state"], "completed")

    def test_retryable_error_keeps_active_turn(self):
        self.client.running = True
        self.ledger.set_codex_runtime(state="running", thread_id="thread-1")
        self.controller._handle_message({"method": "error", "params": {"willRetry": True, "message": "retrying"}})
        self.assertEqual(self.ledger.codex_runtime()["state"], "running")
        self.assertTrue(self.client.running)

    @patch("job_autopilot.codex_bridge.shutil.which", return_value="/usr/bin/codex")
    def test_locked_thread_does_not_silently_create_another(self, _which):
        self.ledger.set_codex_runtime(state="released", thread_id="thread-existing")
        original = self.client.request
        def request(method, params, timeout=30):
            if method == "thread/resume":
                raise RuntimeError("session is in use")
            return original(method, params, timeout)
        with patch.object(self.client, "request", side_effect=request):
            self.controller.send_message("继续")
            self.wait_for_runtime_state("failed")
        self.assertEqual(self.ledger.codex_runtime()["thread_id"], "thread-existing")
        self.assertNotIn("thread/start", [method for method, _ in self.client.requests])

    @patch("job_autopilot.codex_bridge.shutil.which", return_value="/usr/bin/codex")
    def test_message_waits_for_pending_confirmation(self, _which) -> None:
        self.ledger.set_codex_runtime(
            state="awaiting_input", pending_request={"id": "approval-1"}
        )

        with self.assertRaisesRegex(RuntimeError, "先处理上方确认项"):
            self.controller.send_message("继续")

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

    def make_resume(self):
        path = self.root / "resume.txt"
        path.write_text("Test candidate, backend developer", encoding="utf-8")
        return str(path)

    @patch("job_autopilot.codex_bridge.shutil.which", return_value="/usr/bin/codex")
    def test_busy_resume_and_run_requests_do_not_overwrite_any_records(self, _which):
        original = self.ledger.request_automation("process_queue")
        original_import = self.ledger.profile_import()
        for status in self.controller.ACTIVE_STATES:
            self.ledger.set_codex_runtime(state=status)
            # Starting is protected even before the subprocess has connected.
            with self.assertRaisesRegex(RuntimeError, "当前任务未被更改"):
                self.controller.request_resume(self.make_resume())
            with self.assertRaises(RuntimeError):
                self.controller.request_run("discover_and_apply", {})
            self.assertEqual(self.ledger.automation(), original)
            self.assertEqual(self.ledger.profile_import(), original_import)

    @patch("job_autopilot.codex_bridge.shutil.which", return_value="/usr/bin/codex")
    def test_resume_stream_is_correlated_and_keeps_review_results(self, _which):
        self.controller.request_resume(self.make_resume())
        self.wait_for_runtime_state("running")
        request = self.ledger.profile_import()
        self.assertEqual(request["status"], "running")
        self.assertEqual(request["automation_request_id"], self.ledger.automation()["request_id"])
        self.assertEqual(self.ledger.codex_runtime()["resume_request_id"], request["request_id"])
        prompt = next(p for m, p in self.client.requests if m == "turn/start")["input"][0]["text"]
        self.assertIn("本轮只解析简历", prompt)
        for delta in ["正在读取", "简历"]:
            self.controller._handle_message({"method": "item/agentMessage/delta", "params": {
                "threadId": "thread-1", "turnId": "turn-1", "itemId": "msg-1", "delta": delta,
            }})
        self.controller._handle_message({"method": "item/agentMessage/delta", "params": {
            "threadId": "unrelated", "itemId": "msg-1", "delta": "不要串入这段回复",
        }})
        self.assertEqual(self.ledger.profile_import()["agent_messages"][0]["text"], "正在读取简历")
        self.controller._handle_message({"id": 31, "method": "item/permissions/requestApproval", "params": {"reason": "读取简历", "permissions": {"fileSystem": {"read": ["/test/resume"]}}}})
        self.assertEqual(self.ledger.profile_import()["status"], "awaiting_input")
        self.controller.answer("31", {}, "accept")
        self.assertEqual(self.ledger.profile_import()["status"], "running")
        self.assertEqual(self.client.responses[-1][1]["scope"], "turn")
        self.ledger.complete_profile_import([{"label": "姓名", "target_key": "name", "value": "测试姓名"}])
        self.controller._handle_message({"method": "item/completed", "params": {"item": {
            "id": "msg-2", "type": "agentMessage", "text": "提取完成，请确认字段",
        }}})
        self.controller._handle_message({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
        pending = self.ledger.profile_import()
        self.assertEqual(pending["status"], "ready_for_review")
        self.assertEqual(pending["agent_messages"][-1]["text"], "提取完成，请确认字段")
        self.assertNotEqual(self.ledger.profile().get("name"), "测试姓名")

    @patch("job_autopilot.codex_bridge.shutil.which", return_value="/usr/bin/codex")
    def test_resume_without_candidates_is_failed_not_forever_waiting(self, _which):
        self.controller.request_resume(self.make_resume())
        self.wait_for_runtime_state("running")
        self.controller._handle_message({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
        self.assertEqual(self.ledger.profile_import()["status"], "failed")

    @patch("job_autopilot.codex_bridge.shutil.which", return_value="/usr/bin/codex")
    def test_resume_pause_restart_and_other_turn_are_isolated(self, _which):
        self.controller.request_resume(self.make_resume())
        self.wait_for_runtime_state("running")
        self.controller.pause()
        self.assertEqual(self.ledger.profile_import()["status"], "paused")
        self.controller.send_message("帮我找岗位")
        self.wait_for_runtime_state("running")
        self.controller._handle_message({"method": "item/agentMessage/delta", "params": {"delta": "找岗进展"}})
        self.assertEqual(self.ledger.profile_import()["agent_messages"], [])
        self.ledger.request_profile_import(self.make_resume(), source="web_app_server")
        restarted = CodexRunController(self.ledger, self.root, FakeClient())
        self.assertEqual(self.ledger.profile_import()["status"], "paused")
        restarted.close()

    def test_stale_import_updates_are_ignored(self):
        original = self.ledger.request_profile_import(self.make_resume())
        new = self.ledger.request_profile_import(self.make_resume())
        self.ledger.update_profile_import_progress(original["request_id"], status="failed", message="旧请求", delta="old")
        self.assertEqual(self.ledger.profile_import(), new)


if __name__ == "__main__":
    unittest.main()
