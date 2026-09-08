from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .db import Ledger, state_dir


@dataclass
class _PendingResponse:
    ready: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: str = ""


class CodexAppServerClient:
    """Small JSONL client for the local Codex App Server."""

    def __init__(
        self,
        on_message: Callable[[dict[str, Any]], None],
        command: tuple[str, ...] = ("codex", "app-server", "--stdio"),
    ):
        self.on_message = on_message
        self.command = command
        self.process: subprocess.Popen[str] | None = None
        self._next_id = 1
        self._pending: dict[int, _PendingResponse] = {}
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._stopping = False
        self._readers: list[threading.Thread] = []

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            self._stopping = False
            self.process = subprocess.Popen(
                list(self.command),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            self._readers = [
                threading.Thread(target=self._read_stdout, daemon=True),
                threading.Thread(target=self._drain_stderr, daemon=True),
            ]
            for reader in self._readers:
                reader.start()
        self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "job_autopilot",
                    "title": "Job Autopilot",
                    "version": __version__,
                },
                "capabilities": {"experimentalApi": True},
            },
            timeout=20,
        )
        self.notify("initialized", {})

    def request(self, method: str, params: dict[str, Any], timeout: int = 30) -> Any:
        if not self.running:
            raise RuntimeError("Codex App Server 未运行")
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            pending = _PendingResponse()
            self._pending[request_id] = pending
        self._write({"method": method, "id": request_id, "params": params})
        if not pending.ready.wait(timeout):
            with self._lock:
                self._pending.pop(request_id, None)
            raise TimeoutError(f"Codex App Server 调用超时：{method}")
        if pending.error:
            raise RuntimeError(pending.error)
        return pending.result

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"method": method, "params": params})

    def respond(self, request_id: int | str, result: dict[str, Any]) -> None:
        self._write({"id": request_id, "result": result})

    def _write(self, payload: dict[str, Any]) -> None:
        process = self.process
        if not process or not process.stdin or process.poll() is not None:
            raise RuntimeError("Codex App Server 连接已关闭")
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._write_lock:
            process.stdin.write(line)
            process.stdin.flush()

    def _read_stdout(self) -> None:
        process = self.process
        if not process or not process.stdout:
            return
        for raw in process.stdout:
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            request_id = message.get("id")
            if request_id is not None and "method" not in message:
                with self._lock:
                    pending = self._pending.pop(request_id, None)
                if pending:
                    if message.get("error"):
                        error = message["error"]
                        pending.error = str(error.get("message") if isinstance(error, dict) else error)
                    else:
                        pending.result = message.get("result")
                    pending.ready.set()
                continue
            self.on_message(message)
        if self.process is process:
            self._connection_closed()

    def _drain_stderr(self) -> None:
        process = self.process
        if not process or not process.stderr:
            return
        for _ in process.stderr:
            pass

    def _connection_closed(self) -> None:
        with self._lock:
            pending_items = list(self._pending.values())
            self._pending.clear()
        for pending in pending_items:
            pending.error = "Codex App Server 意外退出"
            pending.ready.set()
        if not self._stopping:
            self.on_message({"method": "bridge/disconnected", "params": {}})

    def close(self) -> None:
        self._stopping = True
        process = self.process
        if not process or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        for reader in self._readers:
            if reader is not threading.current_thread():
                reader.join(timeout=3)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream:
                stream.close()
        self.process = None


class CodexRunController:
    """Own one durable Job Autopilot thread and expose it to the dashboard."""

    ACTIVE_STATES = {"starting", "running", "awaiting_input"}

    def __init__(
        self,
        ledger: Ledger,
        plugin_root: Path | None = None,
        client: CodexAppServerClient | None = None,
    ):
        self.ledger = ledger
        self.plugin_root = (plugin_root or Path(__file__).resolve().parents[2]).resolve()
        self.skill_path = self.plugin_root / "skills" / "job-autopilot" / "SKILL.md"
        self.client = client or CodexAppServerClient(self._handle_message)
        self.client.on_message = self._handle_message
        self._run_lock = threading.Lock()
        self._submission_lock = threading.RLock()
        self._resume_request_id = ""
        self._release_thread: threading.Thread | None = None
        current = self.ledger.codex_runtime()
        if current.get("state") in self.ACTIVE_STATES:
            self.ledger.set_codex_runtime(
                state="paused",
                turn_id="",
                pending_request=None,
                message="后台服务已重启，可以继续上次任务",
            )
        pending_import = self.ledger.profile_import()
        if (pending_import.get("source", "web_app_server") == "web_app_server"
                and pending_import.get("status") in {"requested", "starting", "running", "awaiting_input"}):
            self.ledger.update_profile_import_progress(
                str(pending_import.get("request_id") or ""), status="paused",
                message="后台已重启或原解析任务已中断，请点击重新解析。",
            )

    def status(self) -> dict[str, Any]:
        current = self.ledger.codex_runtime()
        current["available"] = shutil.which("codex") is not None
        current["connected"] = self.client.running
        current["project_path"] = str(self.project_path())
        return current

    def project_path(self) -> Path:
        configured = str(self.ledger.settings().get("codex_project_path") or "")
        return Path(configured).resolve() if configured else state_dir().resolve() / "workspace"

    @staticmethod
    def _project_list(client: CodexAppServerClient) -> list[dict[str, Any]]:
        projects = []
        cursor = None
        while True:
            params: dict[str, Any] = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            result = client.request("project/list", params)
            projects.extend(result.get("data") or [])
            cursor = result.get("nextCursor")
            if not cursor:
                return projects

    def projects(self) -> list[dict[str, Any]]:
        client = CodexAppServerClient(lambda message: None)
        try:
            client.start()
            return [{"id": p["id"], "name": p["name"], "roots": p.get("roots", [])}
                    for p in self._project_list(client)]
        finally:
            client.close()

    @staticmethod
    def _skill_prompt(prompt: str) -> str:
        if re.search(r"\$job-autopilot(?::job-autopilot)?(?![\w:-])", prompt):
            return prompt
        return "$job-autopilot\n" + prompt

    def start_run(self, automation: dict[str, Any]) -> dict[str, Any]:
        with self._submission_lock:
            return self._start_run(automation)

    def _assert_can_start(self) -> None:
        current = self.ledger.codex_runtime()
        if current.get("state") in self.ACTIVE_STATES:
            raise RuntimeError("Codex 正在处理其他请求，请等待完成或先暂停；当前任务未被更改。")
        if not shutil.which("codex"):
            raise RuntimeError("未找到 Codex CLI，无法从网页启动任务")

    def request_run(self, action: str, criteria: dict[str, Any]) -> dict[str, Any]:
        with self._submission_lock:
            self._assert_can_start()
            automation = self.ledger.request_automation(action, criteria, source="web_app_server")
            return {"automation": automation, "codex": self._start_run(automation)}

    def request_resume(self, resume_path: str) -> dict[str, Any]:
        with self._submission_lock:
            self._assert_can_start()
            self.ledger.request_profile_import(resume_path, source="web_app_server")
            automation = self.ledger.automation()
            runtime = self._start_run(automation)
            return {"profile_import": self.ledger.profile_import(), "automation": automation, "codex": runtime}

    def _start_run(self, automation: dict[str, Any]) -> dict[str, Any]:
        self._assert_can_start()
        self._resume_request_id = ""
        if automation.get("action") == "parse_resume":
            pending = self.ledger.profile_import()
            if pending.get("automation_request_id") == automation.get("request_id"):
                self._resume_request_id = str(pending.get("request_id") or "")
        self._resume_progress("starting", "正在启动 Codex 简历解析")
        self.ledger.set_codex_runtime(
            state="starting",
            turn_id="",
            message="正在启动 Codex 后台任务",
            last_agent_message="",
            pending_request=None,
            resume_request_id=self._resume_request_id,
        )
        self._activity_update(
            automation,
            state="running",
            stage="preparing",
            message="正在启动 Codex 后台任务",
        )
        threading.Thread(target=self._begin_run, args=(automation,), daemon=True).start()
        return self.status()

    def send_message(self, message: str) -> dict[str, Any]:
        with self._submission_lock:
            return self._send_message(message)

    def _send_message(self, message: str) -> dict[str, Any]:
        """Send a free-form dashboard message to the durable Codex thread."""
        prompt = message.strip()
        if not prompt:
            raise ValueError("请输入要发送给 Codex 的内容")
        if len(prompt) > 8000:
            raise ValueError("消息过长，请控制在 8000 个字符以内")
        if not shutil.which("codex"):
            raise RuntimeError("未找到 Codex CLI，无法发送消息")

        current = self.ledger.codex_runtime()
        state = str(current.get("state") or "idle")
        if state == "awaiting_input":
            raise RuntimeError("Codex 正在等待确认，请先处理上方确认项")
        if state == "starting":
            raise RuntimeError("Codex 正在启动，请稍后再发送")

        thread_id = str(current.get("thread_id") or "")
        turn_id = str(current.get("turn_id") or "")
        if state == "running" and self.client.running and thread_id and turn_id:
            self.client.request(
                "turn/steer",
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": self._skill_prompt(prompt)}],
                    "expectedTurnId": turn_id,
                },
                timeout=30,
            )
            self.ledger.set_codex_runtime(message="已将补充指令发送给 Codex")
            return self.status()
        if state == "running":
            raise RuntimeError("Codex 后台连接已关闭，请稍后重新发送")

        self._resume_request_id = ""
        self.ledger.set_codex_runtime(
            state="starting",
            turn_id="",
            message="正在发送给 Codex",
            last_agent_message="",
            pending_request=None,
            resume_request_id="",
        )
        threading.Thread(target=self._begin_message, args=(prompt,), daemon=True).start()
        return self.status()

    def _ensure_thread(self) -> str:
        workdir = self.project_path()
        workdir.mkdir(parents=True, exist_ok=True)
        self.client.start()
        project_id = ""
        if self.ledger.settings().get("codex_project_path"):
            for project in self._project_list(self.client):
                if any(Path(root["path"]).resolve() == workdir for root in project.get("roots", [])):
                    project_id = str(project["id"])
                    break
            if not project_id:
                raise ValueError("这个目录尚未添加为 Codex 项目，请在网页选择已有项目")
        current = self.ledger.codex_runtime()
        thread_id = str(current.get("thread_id") or "")
        if thread_id:
            resumed = self.client.request(
                "thread/resume", {
                    "threadId": thread_id, "cwd": str(workdir),
                    "developerInstructions": self._workspace_instructions(),
                }
            )
            thread_id = str((resumed or {}).get("thread", {}).get("id") or thread_id)
            if project_id:
                self.client.request("thread/metadata/update", {"threadId": thread_id, "projectId": project_id})
        if thread_id:
            return thread_id

        created = self.client.request(
            "thread/start",
            {
                "cwd": str(workdir),
                "projectId": project_id or None,
                "sandbox": "workspace-write",
                "approvalPolicy": "on-request",
                "approvalsReviewer": "user",
                "personality": "friendly",
                "serviceName": "job_autopilot",
                "developerInstructions": self._workspace_instructions(),
            },
            timeout=30,
        )
        thread_id = str((created or {}).get("thread", {}).get("id") or "")
        if not thread_id:
            raise RuntimeError("Codex 没有返回任务 ID")
        try:
            self.client.request("thread/name/set", {"threadId": thread_id, "name": "Job Autopilot"})
        except Exception:
            pass
        return thread_id

    def _workspace_instructions(self) -> str:
        return (
            f"本会话由 Job Autopilot 网页发起，工作目录是用户选择的项目。"
            f"插件命令使用绝对路径 {self.plugin_root / 'scripts' / 'job-autopilot'}，"
            "不要假定当前目录包含 scripts。只有本条消息携带 request_id 时才核对该编号；"
            "普通自然语言指令按本轮需求执行，不要套用历史消息的 request_id。"
        )

    def _turn_params(self, thread_id: str, prompt: str) -> dict[str, Any]:
        inputs: list[dict[str, Any]] = [{"type": "text", "text": self._skill_prompt(prompt)}]
        if self.skill_path.is_file():
            inputs.append(
                {
                    "type": "skill",
                    "name": "job-autopilot:job-autopilot",
                    "path": str(self.skill_path),
                }
            )
        return {
            "threadId": thread_id,
            "input": inputs,
            "cwd": str(self.project_path()),
            "approvalPolicy": "on-request",
            "approvalsReviewer": "user",
            "sandboxPolicy": {
                "type": "workspaceWrite",
                "writableRoots": [str(self.project_path()), str(state_dir().resolve())],
                "networkAccess": True,
            },
            "summary": "concise",
            "personality": "friendly",
        }

    def _begin_message(self, prompt: str) -> None:
        if not self._run_lock.acquire(blocking=False):
            self.ledger.set_codex_runtime(state="failed", message="另一个 Codex 请求正在启动")
            return
        try:
            thread_id = self._ensure_thread()
            self.ledger.set_codex_runtime(thread_id=thread_id)
            started = self.client.request(
                "turn/start", self._turn_params(thread_id, prompt), timeout=30
            )
            turn_id = str((started or {}).get("turn", {}).get("id") or "")
            if self.ledger.codex_runtime().get("state") == "starting":
                self.ledger.set_codex_runtime(state="running", turn_id=turn_id, message="Codex 正在回复")
        except Exception as error:
            self.ledger.set_codex_runtime(
                state="failed",
                turn_id="",
                pending_request=None,
                message=f"消息发送失败：{error}",
            )
            self.client.close()
        finally:
            self._run_lock.release()

    def _begin_run(self, automation: dict[str, Any]) -> None:
        if not self._run_lock.acquire(blocking=False):
            return
        try:
            thread_id = self._ensure_thread()
            self.ledger.set_codex_runtime(thread_id=thread_id)
            prompt = self._prompt_for(automation)
            started = self.client.request(
                "turn/start",
                self._turn_params(thread_id, prompt),
                timeout=30,
            )
            turn_id = str((started or {}).get("turn", {}).get("id") or "")
            if self.ledger.codex_runtime().get("state") == "starting":
                self.ledger.set_codex_runtime(state="running", turn_id=turn_id, message="Codex 正在执行")
                self._resume_progress("running", "Codex 已接管，正在读取并理解简历")
            if self.ledger.codex_runtime().get("state") == "running":
                self._activity_update(
                    automation, state="running", stage="preparing", message="Codex 已开始执行"
                )
        except Exception as error:
            message = f"Codex 启动失败：{error}"
            self.ledger.set_codex_runtime(state="failed", message=message, pending_request=None)
            self.ledger.set_automation_state("failed", message)
            self._resume_progress("failed", message)
            self.client.close()
        finally:
            self._run_lock.release()

    def _prompt_for(self, automation: dict[str, Any]) -> str:
        request_id = str(automation.get("request_id") or "")
        activity_run_id = str(automation.get("activity_run_id") or "")
        action = str(automation.get("action") or "process_queue")
        resume_instruction = (
            "本轮只解析简历，不执行找岗或投递。使用 resume-extract 提取文本，理解后通过 "
            "resume-import-complete 保存候选字段，不直接写入个人资料。请用简短的可见回复"
            "说明正在读取、理解和生成建议的进展；不要在进度回复里复述完整简历或敏感编号。"
        ) if action == "parse_resume" else ""
        return (
            "$job-autopilot:job-autopilot\n"
            f"接管 Job Autopilot 网页刚刚发起的任务。request_id={request_id}，"
            f"activity_run_id={activity_run_id}，action={action}。"
            "用户已明确要求从网页启动本轮工作。先读取 run-state 并确认 request_id 一致，再按技能流程认领和执行。"
            "不要修改 Job Autopilot 插件源码。需要个人事实、验证码、敏感信息传输或最终提交确认时，"
            "使用 request_user_input 等待用户回答；完成或失败后务必更新本地 run-state。"
            + resume_instruction
        )

    def pause(self) -> dict[str, Any]:
        current = self.ledger.codex_runtime()
        thread_id = str(current.get("thread_id") or "")
        turn_id = str(current.get("turn_id") or "")
        if self.client.running and thread_id and turn_id:
            try:
                self.client.request(
                    "turn/interrupt",
                    {"threadId": thread_id, "turnId": turn_id},
                    timeout=10,
                )
            except Exception:
                pass
        self.ledger.set_codex_runtime(
            state="paused", turn_id="", pending_request=None, message="已由用户暂停"
        )
        self._resume_progress("paused", "解析已暂停，已收到的回复保留；可重新解析。")
        self._activity_update(
            self.ledger.automation(),
            state="paused",
            stage="awaiting_confirmation",
            message="任务已由用户暂停",
        )
        return self.status()

    def release(self) -> dict[str, Any]:
        """Release the dedicated App Server process so desktop can resume this thread."""
        with self._run_lock:
            current = self.ledger.codex_runtime()
            if self.client.running and current.get("turn_id"):
                self.pause()
            self.client.close()
            self.ledger.set_codex_runtime(
                state="released", turn_id="", pending_request=None,
                message="网页已释放会话，请在桌面任务中点击重试；也可稍后从网页继续。",
            )
        return self.status()

    def _release_when_idle(self) -> None:
        with self._run_lock:
            if self.ledger.codex_runtime().get("state") in {"completed", "paused", "failed"}:
                self.client.close()

    def _schedule_release(self) -> None:
        # Never wait for the reader process from inside its event callback.
        self._release_thread = threading.Thread(target=self._release_when_idle, daemon=True)
        self._release_thread.start()

    def answer(self, request_id: str, answers: dict[str, Any], decision: str = "") -> dict[str, Any]:
        current = self.ledger.codex_runtime()
        pending = current.get("pending_request")
        if not isinstance(pending, dict) or str(pending.get("id")) != str(request_id):
            raise ValueError("待确认请求已过期，请刷新页面")
        if not self.client.running:
            raise RuntimeError("Codex 后台连接已关闭，请重新启动任务")
        kind = str(pending.get("kind") or "")
        raw_id = pending.get("rpc_id")
        params = pending.get("params") if isinstance(pending.get("params"), dict) else {}
        if kind == "user_input":
            response_answers: dict[str, Any] = {}
            for question in params.get("questions") or []:
                question_id = str(question.get("id") or "")
                value = answers.get(question_id, "")
                values = value if isinstance(value, list) else [value]
                response_answers[question_id] = {
                    "answers": [str(item) for item in values if str(item)]
                }
            response = {"answers": response_answers}
        elif kind in {"command", "file_change"}:
            response = {"decision": "accept" if decision == "accept" else "decline"}
        elif kind == "permissions":
            response = {
                "permissions": params.get("permissions") if decision == "accept" else {},
                "scope": "turn",
            }
        else:
            response = {"action": "decline", "content": None}
        self.client.respond(raw_id, response)
        self.ledger.set_codex_runtime(
            state="running", pending_request=None, message="已收到确认，Codex 继续执行"
        )
        self._resume_progress("running", "已收到确认，Codex 继续解析")
        self._activity_update(
            self.ledger.automation(),
            state="running",
            message="用户已确认，任务继续执行",
        )
        return self.status()

    def _handle_message(self, message: dict[str, Any]) -> None:
        method = str(message.get("method") or "")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        current = self.ledger.codex_runtime()
        if params.get("threadId") and current.get("thread_id") and params["threadId"] != current["thread_id"]:
            return
        if params.get("turnId") and current.get("turn_id") and params["turnId"] != current["turn_id"]:
            return
        if message.get("id") is not None and method:
            pending = self._normalize_server_request(message.get("id"), method, params)
            self.ledger.set_codex_runtime(
                state="awaiting_input",
                message=pending["message"],
                pending_request=pending,
            )
            self._resume_progress("awaiting_input", pending["message"])
            self._activity_update(
                self.ledger.automation(),
                state="waiting",
                stage="awaiting_confirmation",
                message=pending["message"],
            )
            return
        if method == "turn/started":
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            self.ledger.set_codex_runtime(
                state="running", turn_id=str(turn.get("id") or ""), message="Codex 正在执行"
            )
            self._resume_progress("running", "Codex 已接管，正在读取并理解简历")
            self._activity_update(
                self.ledger.automation(),
                state="running",
                stage="preparing",
                message="Codex 正在执行",
            )
        elif method == "item/agentMessage/delta":
            delta = str(params.get("delta") or "")
            if delta:
                current = self.ledger.codex_runtime()
                combined = (str(current.get("last_agent_message") or "") + delta)[-6000:]
                self.ledger.set_codex_runtime(last_agent_message=combined, message="Codex 正在处理")
                self.ledger.update_profile_import_progress(
                    self._resume_request_id, item_id=str(params.get("itemId") or ""), delta=delta
                )
        elif method == "item/completed":
            item = params.get("item") if isinstance(params.get("item"), dict) else {}
            if item.get("type") == "agentMessage" and item.get("text"):
                self.ledger.set_codex_runtime(last_agent_message=str(item["text"])[-6000:])
                self.ledger.update_profile_import_progress(
                    self._resume_request_id, item_id=str(item.get("id") or ""), text=str(item["text"])
                )
        elif method == "turn/completed":
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            status = str(turn.get("status") or "completed")
            error = turn.get("error") if isinstance(turn.get("error"), dict) else {}
            current = self.ledger.codex_runtime()
            if status == "interrupted":
                self._resume_progress("paused", "解析已暂停，已收到的回复保留；可重新解析。")
                self.ledger.set_codex_runtime(
                    state="paused", turn_id="", pending_request=None, message="本轮已暂停，可以继续"
                )
            elif status == "failed":
                detail = str(error.get("message") or "Codex 执行失败")
                self._resume_progress("failed", detail)
                self.ledger.set_codex_runtime(
                    state="failed", turn_id="", pending_request=None, message=detail
                )
                if self.ledger.automation().get("state") in {"requested", "running"}:
                    self.ledger.set_automation_state("failed", detail)
                else:
                    self._activity_update(
                        self.ledger.automation(),
                        state="failed",
                        stage="failed",
                        message=detail,
                    )
            elif current.get("state") != "awaiting_input":
                self._resume_progress("failed", "本轮已结束，但没有生成可确认的资料字段。请查看回复后重新解析。")
                message_text = str(current.get("last_agent_message") or "本轮 Codex 任务已结束")
                self.ledger.set_codex_runtime(
                    state="completed", turn_id="", pending_request=None, message=message_text[:300]
                )
                if self.ledger.automation().get("state") in {"requested", "running"}:
                    self.ledger.set_automation_state("completed", "本轮 Codex 任务已结束")
                else:
                    self._activity_update(
                        self.ledger.automation(),
                        state="completed",
                        stage="completed",
                        message="本轮 Codex 任务已结束",
                    )
            self._schedule_release()
        elif method in {"error", "bridge/disconnected"}:
            if method == "error" and params.get("willRetry"):
                self.ledger.set_codex_runtime(message="连接暂时中断，Codex 正在重试")
                self._resume_progress("running", "连接暂时中断，Codex 正在重试")
                return
            error = params.get("error") if isinstance(params.get("error"), dict) else {}
            detail = str(error.get("message") or params.get("message") or "Codex 后台连接已关闭")
            current = self.ledger.codex_runtime()
            if current.get("state") in self.ACTIVE_STATES:
                self._resume_progress("failed", detail)
                self.ledger.set_codex_runtime(state="failed", message=detail, pending_request=None)
                self._activity_update(
                    self.ledger.automation(), state="failed", stage="failed", message=detail
                )
                self._schedule_release()

    def _resume_progress(self, status: str, message: str) -> None:
        if self._resume_request_id:
            self.ledger.update_profile_import_progress(self._resume_request_id, status=status, message=message)

    def _activity_update(self, automation: dict[str, Any], **changes: Any) -> None:
        run_id = str(automation.get("activity_run_id") or "")
        if not run_id:
            return
        try:
            self.ledger.update_activity(run_id, **changes)
        except (KeyError, ValueError):
            pass

    @staticmethod
    def _normalize_server_request(
        rpc_id: int | str, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        if method == "item/tool/requestUserInput":
            kind = "user_input"
            questions = params.get("questions") or []
            message = str(questions[0].get("question") if questions else "Codex 需要你的回答")
            safe_params = {"questions": questions}
        elif method == "item/commandExecution/requestApproval":
            kind = "command"
            message = str(params.get("reason") or "Codex 请求执行一项本地操作")
            safe_params = {}
        elif method == "item/fileChange/requestApproval":
            kind = "file_change"
            message = str(params.get("reason") or "Codex 请求修改本地文件")
            safe_params = {}
        elif method == "item/permissions/requestApproval":
            kind = "permissions"
            message = str(params.get("reason") or "Codex 请求额外权限")
            safe_params = {"permissions": params.get("permissions") or {}}
        else:
            kind = "other"
            message = "Codex 需要你确认后继续"
            safe_params = {}
        return {
            "id": str(rpc_id),
            "rpc_id": rpc_id,
            "kind": kind,
            "method": method,
            "message": message,
            "params": safe_params,
        }

    def close(self) -> None:
        with self._run_lock:
            self.client.close()
        if self._release_thread and self._release_thread is not threading.current_thread():
            self._release_thread.join(timeout=5)
