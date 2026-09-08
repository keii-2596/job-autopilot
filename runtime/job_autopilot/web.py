from __future__ import annotations

import json
import mimetypes
import threading
import time
import os
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .codex_bridge import CodexRunController
from .db import Ledger
from .updater import GitHubUpdater
from .job_library_sync import GitHubJobLibrary
from .update_guard import UpdateTaskGuard


STATIC_ROOT = Path(__file__).with_name("web_static")


def _load_static_assets(root: Path) -> dict[str, bytes]:
    """Load UI files once so a running server survives plugin cache replacement."""
    assets = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    if "index.html" not in assets or "embedded.html" not in assets:
        raise RuntimeError("Job Autopilot dashboard assets are incomplete")
    return assets


STATIC_ASSETS = _load_static_assets(STATIC_ROOT)


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "JobAutopilot/0.1"

    @property
    def ledger(self) -> Ledger:
        return self.server.ledger  # type: ignore[attr-defined]

    @property
    def codex(self) -> CodexRunController:
        return self.server.codex  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[dashboard] {self.address_string()} {fmt % args}")

    def _json(self, value: Any, status: int = 200) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, error: Exception, status: int = 400) -> None:
        self._json({"success": False, "error": str(error)}, status)

    def _resume_events(self) -> None:
        """Replay the latest snapshot on reconnect, then stream changed imports."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        previous = ""
        deadline = time.monotonic() + 25
        try:
            self.wfile.write(b"retry: 1000\n\n")
            while not self.server.stopping.is_set() and time.monotonic() < deadline:
                payload = json.dumps(self.ledger.profile_import(), ensure_ascii=False)
                if payload != previous:
                    self.wfile.write(f"event: profile\ndata: {payload}\n\n".encode("utf-8"))
                    previous = payload
                else:
                    self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
                self.server.stopping.wait(0.35)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 10_000_000:
            raise ValueError("request body is too large")
        raw = self.rfile.read(length) if length else b"{}"
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def _static(self, path: str) -> None:
        if path == "/embedded":
            relative = "embedded.html"
        else:
            relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        candidate = (STATIC_ROOT / relative).resolve()
        if STATIC_ROOT.resolve() not in candidate.parents and candidate != STATIC_ROOT.resolve():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        asset_name = relative if relative in STATIC_ASSETS else "index.html"
        content = STATIC_ASSETS[asset_name]
        content_type = mimetypes.guess_type(asset_name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        if asset_name == "embedded.html":
            policy = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'"
        else:
            policy = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'"
        self.send_header("Content-Security-Policy", policy)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        path = parts.path
        query = parse_qs(parts.query)
        try:
            if path == "/api/health":
                self._json({"success": True, "database": str(self.ledger.path)})
            elif path == "/api/summary":
                self._json(self.ledger.summary())
            elif path == "/api/applications":
                status = query.get("status", [None])[0]
                self._json(self.ledger.list_applications(status=status))
            elif path.startswith("/api/applications/") and path.endswith("/events"):
                app_id = int(path.split("/")[3])
                self._json(self.ledger.events(app_id))
            elif path.startswith("/api/applications/"):
                app_id = int(path.rsplit("/", 1)[1])
                item = self.ledger.get_application(app_id)
                if not item:
                    self._json({"success": False, "error": "not found"}, 404)
                else:
                    self._json(item)
            elif path == "/api/sites":
                self._json(self.ledger.list_sites())
            elif path == "/api/profile":
                self._json(self.ledger.profile())
            elif path == "/api/profile/import":
                self._json(self.ledger.profile_import())
            elif path == "/api/profile/import/events":
                self._resume_events()
            elif path == "/api/settings":
                self._json(self.ledger.settings())
            elif path == "/api/policy/check":
                self._json(self.ledger.check_domain_policy(str(query.get("url", [""])[0])))
            elif path == "/api/automation":
                self._json(self.ledger.automation())
            elif path == "/api/codex":
                self._json(self.codex.status())
            elif path == "/api/codex/projects":
                self._json(self.codex.projects())
            elif path == "/api/updates":
                self._json(self.server.updater.status())
            elif path == "/api/job-library-sync":
                self._json(self.server.job_library_sync.status())
            elif path == "/api/activity":
                self._json(self.ledger.activity_snapshot())
            elif path == "/api/source-jobs/summary":
                summary = self.ledger.source_jobs_summary(
                    query=str(query.get("q", [""])[0]),
                    direction=str(query.get("direction", [""])[0]),
                )
                self._json(summary)
            elif path == "/api/source-jobs":
                self._json(
                    self.ledger.list_source_jobs(
                        query=str(query.get("q", [""])[0]),
                        direction=str(query.get("direction", [""])[0]),
                        limit=int(query.get("limit", [200])[0]),
                        offset=int(query.get("offset", [0])[0]),
                    )
                )
            else:
                self._static(path)
        except Exception as error:
            self._error(error)

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        try:
            body = self._body()
            if path.startswith("/api/updates/") or path.startswith("/api/job-library-sync/"):
                # Update endpoints cannot be invoked by another website's form/fetch.
                origin = self.headers.get("Origin")
                expected = f"http://{self.headers.get('Host')}"
                authority = urlsplit(expected)
                if (authority.hostname not in {"localhost", "127.0.0.1", "::1"}
                        or authority.username or (origin and origin != expected)
                        or self.headers.get("X-Job-Autopilot") != "dashboard"):
                    self._error(ValueError("请从本机控制台操作更新"), 403)
                    return
                if path == "/api/job-library-sync/settings":
                    self._json(self.server.job_library_sync.configure(body.get("auto_sync")))
                elif path == "/api/job-library-sync/start":
                    self._json(self.server.job_library_sync.request(), 202)
                elif path == "/api/updates/settings":
                    self._json(self.server.updater.configure(body.get("auto_update")))
                elif path == "/api/updates/reconcile":
                    if self.server.updater.installing:
                        raise RuntimeError("正在安装更新，请稍后整理任务状态")
                    result = self.server.update_guard.reconcile(body.get("entries"), confirmed=body.get("confirmed", False))
                    self._json({**self.server.updater.status(), "reconciled": result["reconciled"]})
                elif path in {"/api/updates/check", "/api/updates/install"}:
                    self._json(self.server.updater.request(path.rsplit("/", 1)[1]), 202)
                else:
                    self._json({"error": "not found"}, 404)
                return
            if self.server.updater.installing:
                self._error(RuntimeError("正在安装更新，请稍后操作；原有任务记录已保留。"), 409)
                return
            if path == "/api/applications":
                self._json(
                    self.ledger.record_application(
                        title=str(body.get("title", "")).strip(),
                        company=str(body.get("company", "")).strip(),
                        url=str(body.get("url", "")).strip(),
                        jdwatch_id=body.get("jdwatch_id"),
                        locations=body.get("locations") or [],
                        recruitment_channel=body.get("recruitment_channel"),
                        status=body.get("status") or "queued",
                        notes=str(body.get("notes", "")),
                    ),
                    201,
                )
            elif path == "/api/automation/start":
                result = self.codex.request_run(
                    str(body.get("action") or "process_queue"),
                    body.get("criteria") if isinstance(body.get("criteria"), dict) else {},
                )
                self._json(result, 202)
            elif path == "/api/automation/stop":
                automation = self.ledger.set_automation_state("paused", "已由用户暂停")
                self._json({"automation": automation, "codex": self.codex.pause()})
            elif path == "/api/codex/respond":
                answers = body.get("answers") if isinstance(body.get("answers"), dict) else {}
                self._json(
                    self.codex.answer(
                        str(body.get("request_id") or ""),
                        answers,
                        str(body.get("decision") or ""),
                    )
                )
            elif path == "/api/codex/message":
                self._json(self.codex.send_message(str(body.get("message") or "")), 202)
            elif path == "/api/codex/release":
                self._json(self.codex.release())
            elif path == "/api/profile/resume-request":
                self._json(self.codex.request_resume(str(body.get("resume_path") or "")), 202)
            elif path == "/api/profile/fields":
                self._json(self.ledger.upsert_profile_field(body), 201)
            elif path == "/api/profile/import/apply":
                suggestion_ids = body.get("suggestion_ids") or []
                if not isinstance(suggestion_ids, list):
                    raise ValueError("suggestion_ids must be a list")
                self._json(self.ledger.apply_profile_import([str(item) for item in suggestion_ids]))
            elif path.startswith("/api/source-jobs/") and path.endswith("/queue"):
                job_id = int(path.split("/")[3])
                self._json(self.ledger.queue_source_job(job_id), 201)
            else:
                self._json({"success": False, "error": "not found"}, 404)
        except Exception as error:
            self._error(error)

    def do_PATCH(self) -> None:
        path = urlsplit(self.path).path
        try:
            body = self._body()
            if path.startswith("/api/applications/"):
                app_id = int(path.rsplit("/", 1)[1])
                self._json(
                    self.ledger.update_application(
                        app_id,
                        status=body.get("status"),
                        notes=body.get("notes"),
                        confirmation_ref=body.get("confirmation_ref"),
                        actual_title=body.get("actual_title"), actual_url=body.get("actual_url"),
                        actual_locations=body.get("actual_locations"), archived=body.get("archived"),
                    )
                )
            else:
                self._json({"success": False, "error": "not found"}, 404)
        except KeyError as error:
            self._error(error, 404)
        except Exception as error:
            self._error(error)

    def do_PUT(self) -> None:
        path = urlsplit(self.path).path
        try:
            body = self._body()
            if path == "/api/profile":
                self._json(self.ledger.update_profile(body))
            elif path == "/api/settings":
                self._json(self.ledger.update_settings(body))
            else:
                self._json({"success": False, "error": "not found"}, 404)
        except Exception as error:
            self._error(error)


class DashboardServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], ledger: Ledger):
        self.ledger = ledger
        self.stopping = threading.Event()
        self.codex = CodexRunController(ledger)
        self.restart_source = None
        self.update_guard = UpdateTaskGuard(ledger, self.codex,
            syncing=lambda: hasattr(self, "job_library_sync") and self.job_library_sync._lock.locked())
        self.updater = GitHubUpdater(ledger, Path(__file__).resolve().parents[2], self._update_busy,
                                     execution_lock=self.codex._submission_lock, guard=self.update_guard.snapshot)
        self.job_library_sync = GitHubJobLibrary(ledger, installing=lambda: self.updater.installing)
        super().__init__(address, DashboardHandler)
        self.update_guard.reconcile()

    def _update_busy(self):
        return self.update_guard.snapshot()["blocked"]

    def request_restart(self, source):
        self.restart_source = source
        # shutdown must run outside the serve_forever thread.
        threading.Thread(target=self.shutdown, daemon=True).start()

    def server_close(self) -> None:
        self.stopping.set()
        self.updater.close()
        self.job_library_sync.close()
        if hasattr(self, "codex"):
            self.codex.close()
        super().server_close()


def serve(ledger: Ledger, host: str = "127.0.0.1", port: int = 8765) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("dashboard may only bind to a loopback address")
    server = DashboardServer((host, port), ledger)
    server.updater.restart = server.request_restart
    server.updater.start()
    server.job_library_sync.start()
    print(f"Job Autopilot dashboard: http://{host}:{port}/")
    print(f"Ledger: {ledger.path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    if server.restart_source:
        os.environ["PYTHONPATH"] = str(server.restart_source / "runtime")
        os.execv(sys.executable, [sys.executable, "-m", "job_autopilot", "serve", "--host", host, "--port", str(port)])
