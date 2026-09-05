from __future__ import annotations

import json
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
            threading.Thread(target=self._read_stdout, daemon=True).start()
            threading.Thread(target=self._drain_stderr, daemon=True).start()
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
        current = self.ledger.codex_runtime()
        if current.get("state") in self.ACTIVE_STATES:
            self.ledger.set_codex_runtime(
                state="paused",
                turn_id="",
                pending_request=None,
                message="后台服务已重启，可以继续上次任务",
            )

    def status(self) -> dict[str, Any]:
        current = self.ledger.codex_runtime()
        current["available"] = shutil.which("codex") is not None
        current["connected"] = self.client.running
        return current

    def start_run(self, automation: dict[str, Any]) -> dict[str, Any]:
        if not shutil.which("codex"):
            raise RuntimeError("未找到 Codex CLI，无法从网页启动任务")
        current = self.ledger.codex_runtime()
        if current.get("state") in self.ACTIVE_STATES and self.client.running:
            raise RuntimeError("已有 Job Autopilot 任务正在运行")
        self.ledger.set_codex_runtime(
            state="starting",
            turn_id="",
            message="正在启动 Codex 后台任务",
            last_agent_message="",
            pending_request=None,
        )
        self._activity_update(
            automation,
            state="running",
            stage="preparing",
            message="正在启动 Codex 后台任务",
        )
        threading.Thread(target=self._begin_run, args=(automation,), daemon=True).start()
        return self.status()

    def _begin_run(self, automation: dict[str, Any]) -> None:
        if not self._run_lock.acquire(blocking=False):
            return
        try:
            self.client.start()
            current = self.ledger.codex_runtime()
            thread_id = str(current.get("thread_id") or "")
            if thread_id:
                try:
                    resumed = self.client.request("thread/resume", {"threadId": thread_id})
                    thread_id = str((resumed or {}).get("thread", {}).get("id") or thread_id)
                except Exception:
                    thread_id = ""
            if not thread_id:
                created = self.client.request(
                    "thread/start",
                    {
                        "cwd": str(self.plugin_root),
                        "sandbox": "workspace-write",
                        "approvalPolicy": "on-request",
                        "approvalsReviewer": "user",
                        "personality": "friendly",
                        "serviceName": "job_autopilot",
                    },
                    timeout=30,
                )
                thread_id = str((created or {}).get("thread", {}).get("id") or "")
                if not thread_id:
                    raise RuntimeError("Codex 没有返回任务 ID")
                try:
                    self.client.request(
                        "thread/name/set",
                        {"threadId": thread_id, "name": "Job Autopilot"},
                    )
                except Exception:
                    pass

            prompt = self._prompt_for(automation)
            inputs: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
            if self.skill_path.is_file():
                inputs.append(
                    {
                        "type": "skill",
                        "name": "job-autopilot:job-autopilot",
                        "path": str(self.skill_path),
                    }
                )
            started = self.client.request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": inputs,
                    "cwd": str(self.plugin_root),
                    "approvalPolicy": "on-request",
                    "approvalsReviewer": "user",
                    "sandboxPolicy": {
                        "type": "workspaceWrite",
                        "writableRoots": [str(self.plugin_root), str(state_dir().resolve())],
                        "networkAccess": True,
                    },
                    "summary": "concise",
                    "personality": "friendly",
                },
                timeout=30,
            )
            turn_id = str((started or {}).get("turn", {}).get("id") or "")
            self.ledger.set_codex_runtime(
                state="running",
                thread_id=thread_id,
                turn_id=turn_id,
                message="Codex 正在执行",
            )
            self._activity_update(
                automation, state="running", stage="preparing", message="Codex 已开始执行"
            )
        except Exception as error:
            message = f"Codex 启动失败：{error}"
            self.ledger.set_codex_runtime(state="failed", message=message, pending_request=None)
            self.ledger.set_automation_state("failed", message)
        finally:
            self._run_lock.release()

    def _prompt_for(self, automation: dict[str, Any]) -> str:
        request_id = str(automation.get("request_id") or "")
        activity_run_id = str(automation.get("activity_run_id") or "")
        action = str(automation.get("action") or "process_queue")
        return (
            "$job-autopilot:job-autopilot\n"
            f"接管 Job Autopilot 网页刚刚发起的任务。request_id={request_id}，"
            f"activity_run_id={activity_run_id}，action={action}。"
            "用户已明确要求从网页启动本轮工作。先读取 run-state 并确认 request_id 一致，再按技能流程认领和执行。"
            "不要修改 Job Autopilot 插件源码。需要个人事实、验证码、敏感信息传输或最终提交确认时，"
            "使用 request_user_input 等待用户回答；完成或失败后务必更新本地 run-state。"
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
        self._activity_update(
            self.ledger.automation(),
            state="paused",
            stage="awaiting_confirmation",
            message="任务已由用户暂停",
        )
        return self.status()

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
        self._activity_update(
            self.ledger.automation(),
            state="running",
            message="用户已确认，任务继续执行",
        )
        return self.status()

    def _handle_message(self, message: dict[str, Any]) -> None:
        method = str(message.get("method") or "")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if message.get("id") is not None and method:
            pending = self._normalize_server_request(message.get("id"), method, params)
            self.ledger.set_codex_runtime(
                state="awaiting_input",
                message=pending["message"],
                pending_request=pending,
            )
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
        elif method == "item/completed":
            item = params.get("item") if isinstance(params.get("item"), dict) else {}
            if item.get("type") == "agentMessage" and item.get("text"):
                self.ledger.set_codex_runtime(last_agent_message=str(item["text"])[-6000:])
        elif method == "turn/completed":
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            status = str(turn.get("status") or "completed")
            error = turn.get("error") if isinstance(turn.get("error"), dict) else {}
            current = self.ledger.codex_runtime()
            if status == "failed":
                detail = str(error.get("message") or "Codex 执行失败")
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
        elif method in {"error", "bridge/disconnected"}:
            error = params.get("error") if isinstance(params.get("error"), dict) else {}
            detail = str(error.get("message") or params.get("message") or "Codex 后台连接已关闭")
            current = self.ledger.codex_runtime()
            if current.get("state") in self.ACTIVE_STATES:
                self.ledger.set_codex_runtime(state="failed", message=detail, pending_request=None)
                self._activity_update(
                    self.ledger.automation(), state="failed", stage="failed", message=detail
                )

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
        self.client.close()
