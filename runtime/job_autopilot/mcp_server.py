from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .db import Ledger, VALID_STATUSES


UI_URI = "ui://job-autopilot/console.html"
UI_PATH = Path(__file__).with_name("web_static") / "embedded.html"
PROTOCOL_VERSION = "2025-06-18"

REQUIRED_PROFILE_FIELDS = (
    "name",
    "phone",
    "email",
    "school",
    "degree",
    "major",
    "graduation_year",
    "resume_path",
)


def _text_schema(description: str = "") -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "string"}
    if description:
        schema["description"] = description
    return schema


class JobAutopilotMCP:
    def __init__(self, ledger: Ledger | None = None, ui_path: Path | None = None):
        self.ledger = ledger or Ledger()
        self.ui_path = ui_path or UI_PATH

    def state(self) -> dict[str, Any]:
        applications = self.ledger.list_applications(limit=40)
        profile = self.ledger.profile()
        custom_fields = []
        for field in profile.get("custom_fields") or []:
            custom_fields.append(
                {
                    "id": field.get("id"),
                    "key": field.get("key"),
                    "label": field.get("label"),
                    "aliases": field.get("aliases") or [],
                    "scope": field.get("scope") or "global",
                }
            )
        missing = [key for key in REQUIRED_PROFILE_FIELDS if not profile.get(key)]
        profile_import = self.ledger.profile_import()
        settings = self.ledger.settings()
        return {
            "summary": self.ledger.summary(),
            "source_jobs_summary": self.ledger.source_jobs_summary(),
            "automation": self.ledger.automation(),
            "activity": self.ledger.activity_snapshot(),
            "applications": [
                {
                    "id": item.get("id"),
                    "title": item.get("title"),
                    "company": item.get("company"),
                    "status": item.get("status"),
                    "locations": item.get("locations") or [],
                    "site_domain": item.get("site_domain"),
                    "updated_at": item.get("updated_at"),
                }
                for item in applications
            ],
            "profile": {
                "filled_required": len(REQUIRED_PROFILE_FIELDS) - len(missing),
                "required_total": len(REQUIRED_PROFILE_FIELDS),
                "missing": missing,
                "resume_configured": bool(profile.get("resume_path")),
                "custom_fields": custom_fields,
            },
            "profile_import": {
                "status": profile_import.get("status") or "idle",
                "message": profile_import.get("message") or "",
                "suggestion_count": len(profile_import.get("suggestions") or []),
            },
            "settings": {
                "submission_mode": settings.get("submission_mode") or "review",
                "allowed_domains_count": len(settings.get("allowed_domains") or []),
            },
        }

    @staticmethod
    def tool_definitions() -> list[dict[str, Any]]:
        read_only = {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
        local_write = {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        }
        open_meta = {
            "ui": {"resourceUri": UI_URI},
            "openai/outputTemplate": UI_URI,
            "openai/widgetAccessible": True,
            "openai/toolInvocation/invoking": "正在打开求职控制台…",
            "openai/toolInvocation/invoked": "求职控制台已打开",
        }
        return [
            {
                "name": "open_job_autopilot",
                "title": "打开 Job Autopilot",
                "description": "打开 Job Autopilot 嵌入式控制台，查看投递概况并从界面发起任务。",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                "annotations": read_only,
                "_meta": open_meta,
            },
            {
                "name": "get_job_autopilot_state",
                "title": "读取 Job Autopilot 状态",
                "description": "读取 27 届秋招职位库、当前岗位队列、投递漏斗、资料完整度和运行状态。",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                "annotations": read_only,
            },
            {
                "name": "start_job_autopilot",
                "title": "启动 Job Autopilot",
                "description": "在本地账本中创建一个由 Codex 接管的找岗、队列处理或简历解析请求。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["discover_and_apply", "process_queue", "parse_resume"],
                            "default": "process_queue",
                        },
                        "keywords": {"type": "array", "items": _text_schema(), "default": []},
                        "locations": {"type": "array", "items": _text_schema(), "default": []},
                        "recruitment_type": {
                            "type": "string",
                            "enum": ["campus", "intern", "social"],
                            "default": "campus",
                        },
                    },
                    "additionalProperties": False,
                },
                "annotations": local_write,
            },
            {
                "name": "pause_job_autopilot",
                "title": "暂停 Job Autopilot",
                "description": "暂停当前 Job Autopilot 请求，不删除队列或投递记录。",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                "annotations": local_write,
            },
            {
                "name": "request_resume_parse",
                "title": "请求 AI 解析简历",
                "description": "使用已保存的简历路径，或指定本机简历绝对路径，创建 AI 资料解析请求。",
                "inputSchema": {
                    "type": "object",
                    "properties": {"resume_path": _text_schema("可选；本机简历的绝对路径")},
                    "additionalProperties": False,
                },
                "annotations": local_write,
            },
            {
                "name": "save_profile_field",
                "title": "记住已确认的资料字段",
                "description": "仅在用户确认后，把招聘表单的新问题和值保存为可复用资料字段。不得保存密码或验证码。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "key": _text_schema("稳定的英文键名，可省略"),
                        "label": _text_schema("字段的人类可读名称"),
                        "value": {},
                        "aliases": {"type": "array", "items": _text_schema(), "default": []},
                        "scope": _text_schema("global、company:<name> 或 site:<domain>"),
                    },
                    "required": ["label", "value"],
                    "additionalProperties": False,
                },
                "annotations": local_write,
            },
            {
                "name": "update_application_status",
                "title": "更新投递状态",
                "description": "更新一个本地岗位记录的处理状态和非敏感备注。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "application_id": {"type": "integer", "minimum": 1},
                        "status": {"type": "string", "enum": sorted(VALID_STATUSES)},
                        "notes": _text_schema("可选的非敏感处理备注"),
                    },
                    "required": ["application_id", "status"],
                    "additionalProperties": False,
                },
                "annotations": local_write,
            },
        ]

    def _result(self, message: str, *, include_ui: bool = False) -> dict[str, Any]:
        state = self.state()
        result: dict[str, Any] = {
            "content": [{"type": "text", "text": message}],
            "structuredContent": state,
            "isError": False,
            "_meta": {"jobAutopilotState": state},
        }
        if include_ui:
            result["_meta"].update(
                {
                    "ui": {"resourceUri": UI_URI},
                    "openai/outputTemplate": UI_URI,
                }
            )
        return result

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "open_job_autopilot":
            return self._result("Job Autopilot 控制台已就绪。", include_ui=True)
        if name == "get_job_autopilot_state":
            return self._result("已读取 Job Autopilot 当前状态。")
        if name == "start_job_autopilot":
            action = str(arguments.get("action") or "process_queue")
            keywords = arguments.get("keywords") or []
            locations = arguments.get("locations") or []
            if not isinstance(keywords, list) or not isinstance(locations, list):
                raise ValueError("keywords and locations must be arrays")
            self.ledger.request_automation(
                action,
                {
                    "keywords": [str(value).strip() for value in keywords if str(value).strip()],
                    "locations": [str(value).strip() for value in locations if str(value).strip()],
                    "recruitment_type": str(arguments.get("recruitment_type") or "campus"),
                },
                source="mcp_conversation",
            )
            return self._result("任务请求已保存。请让当前 Codex 任务接管并执行。")
        if name == "pause_job_autopilot":
            self.ledger.set_automation_state("paused", "已从嵌入式控制台暂停")
            return self._result("Job Autopilot 已暂停。")
        if name == "request_resume_parse":
            profile = self.ledger.profile()
            resume_path = str(arguments.get("resume_path") or profile.get("resume_path") or "").strip()
            if not resume_path:
                raise ValueError("请先在完整控制台保存简历绝对路径")
            self.ledger.request_profile_import(resume_path, source="mcp_conversation")
            return self._result("简历解析请求已保存。请让当前 Codex 任务接管并生成资料建议。")
        if name == "save_profile_field":
            self.ledger.upsert_profile_field(arguments)
            return self._result("已记住用户确认的资料字段和相近问法。")
        if name == "update_application_status":
            self.ledger.update_application(
                int(arguments["application_id"]),
                status=str(arguments["status"]),
                notes=arguments.get("notes"),
            )
            return self._result("投递状态已更新。")
        raise ValueError(f"unknown tool: {name}")

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        request_id = message.get("id")
        if request_id is None:
            return None
        if method == "initialize":
            requested = (message.get("params") or {}).get("protocolVersion")
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": requested or PROTOCOL_VERSION,
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {"subscribe": False, "listChanged": False},
                    },
                    "serverInfo": {"name": "job-autopilot", "version": __version__},
                    "instructions": (
                        "Use this server for local Job Autopilot state and actions. "
                        "A requested run still requires the current Codex task to claim and execute it. "
                        "Never store passwords or one-time codes, and require user confirmation before external submission."
                    ),
                },
            }
        try:
            if method == "ping":
                result: dict[str, Any] = {}
            elif method == "tools/list":
                result = {"tools": self.tool_definitions()}
            elif method == "tools/call":
                params = message.get("params") or {}
                arguments = params.get("arguments") or {}
                if not isinstance(arguments, dict):
                    raise ValueError("tool arguments must be an object")
                result = self.call_tool(str(params.get("name") or ""), arguments)
            elif method == "resources/list":
                result = {
                    "resources": [
                        {
                            "uri": UI_URI,
                            "name": "Job Autopilot embedded console",
                            "title": "Job Autopilot",
                            "description": "Compact MCP Apps control surface for Job Autopilot.",
                            "mimeType": "text/html;profile=mcp-app",
                        }
                    ]
                }
            elif method == "resources/read":
                uri = str((message.get("params") or {}).get("uri") or "")
                if uri != UI_URI:
                    raise ValueError(f"unknown resource: {uri}")
                result = {
                    "contents": [
                        {
                            "uri": UI_URI,
                            "mimeType": "text/html;profile=mcp-app",
                            "text": self.ui_path.read_text(encoding="utf-8"),
                            "_meta": {"ui": {"prefersBorder": False}},
                        }
                    ]
                }
            elif method == "resources/templates/list":
                result = {"resourceTemplates": []}
            elif method == "prompts/list":
                result = {"prompts": []}
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as error:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32602, "message": str(error)},
            }


def serve() -> None:
    server = JobAutopilotMCP()
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
            if not isinstance(message, dict):
                raise ValueError("JSON-RPC message must be an object")
            response = server.handle(message)
        except Exception as error:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": str(error)},
            }
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    serve()
