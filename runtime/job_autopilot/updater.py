"""Opt-in, local-only updater for the fixed public Job Autopilot repository."""
from __future__ import annotations

import ast
import fcntl
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath
from urllib.request import Request, urlopen

from .db import Ledger, utc_now

REPOSITORY = "keii-2596/job-autopilot"
BRANCH = "main"
BASE_COMMIT = "dd36cd051505884c57f70ecc71c6b7417c38df95"
CHECK_INTERVAL = 6 * 60 * 60
PRIVATE_FILES = {"runtime/job_autopilot/baigua.py", "tests/test_baigua.py"}
ROOT_FILES = {"README.md", "LICENSE", "SECURITY.md", ".gitignore", ".mcp.json", ".codex-plugin/plugin.json"}


class UpdateConflict(RuntimeError):
    pass


class UpdateBusy(RuntimeError):
    pass


def managed_path(name: str) -> bool:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        raise ValueError("更新包包含不安全的路径")
    if name in PRIVATE_FILES or any(p.startswith(".") for p in path.parts) and name not in ROOT_FILES:
        return False
    if path.suffix in {".db", ".sqlite", ".sqlite3", ".pyc"} or "__pycache__" in path.parts:
        return False
    return name in ROOT_FILES or path.parts[0] in {"runtime", "scripts", "skills", "assets", "data", "tests", "docs"}


def read_archive(payload: bytes) -> dict[str, bytes]:
    files = {}
    total = 0
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        roots = {entry.filename.split("/")[0] for entry in archive.infolist()}
        if len(roots) != 1 or len(archive.infolist()) > 5000:
            raise ValueError("更新包结构不正确")
        for entry in archive.infolist():
            if entry.is_dir():
                continue
            name = entry.filename.partition("/")[2]
            if not name or stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError("更新包不允许符号链接或无根路径")
            total += entry.file_size
            if total > 120_000_000 or entry.file_size > 30_000_000:
                raise ValueError("更新包过大")
            if managed_path(name):
                if name in files:
                    raise ValueError("更新包包含重复文件")
                files[name] = archive.read(entry)
    manifest = json.loads(files.get(".codex-plugin/plugin.json", b"{}"))
    if manifest.get("name") != "job-autopilot" or not manifest.get("version"):
        raise ValueError("更新包不是有效的 Job Autopilot 插件")
    return files


def merge_content(name: str, local: bytes | None, base: bytes | None, remote: bytes | None) -> bytes | None:
    """Three-way merge public changes without deleting private/local-only content."""
    if remote == base or local == remote:
        return local
    if local == base:
        return remote
    if None in (local, base, remote) or any(b"\0" in value for value in (local, base, remote)):
        raise UpdateConflict(f"本地修改冲突：{name}")
    # A local cachebuster must not conflict with an upstream version bump.
    if name == ".codex-plugin/plugin.json":
        values = [json.loads(value) for value in (local, base, remote)]
        for value in values:
            value["version"] = value["version"].split("+", 1)[0]
        local, base, remote = [json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n" for value in values]
    with tempfile.TemporaryDirectory(prefix="job-update-merge-") as directory:
        paths = [Path(directory) / key for key in ("local", "base", "remote")]
        for path, content in zip(paths, (local, base, remote)):
            path.write_bytes(content)
        result = subprocess.run(["git", "merge-file", "-p", *map(str, paths)], capture_output=True, timeout=15)
        if result.returncode:
            raise UpdateConflict(f"本地修改冲突：{name}；未覆盖，请先人工合并。")
        return result.stdout


class GitHubUpdater:
    def __init__(self, ledger: Ledger, plugin_root: Path, busy, restart=None, execution_lock=None, guard=None):
        self.ledger = ledger
        self.plugin_root = plugin_root
        self.current_version = json.loads((plugin_root / ".codex-plugin/plugin.json").read_text())["version"]
        self.busy = busy
        self.guard = guard
        self.restart = restart
        self.execution_lock = execution_lock or threading.RLock()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._worker = None
        self._scheduler = None
        self.installing = False
        self.storage = ledger.path.parent / "updates"
        self._state_lock = threading.RLock()
        current = self.status()
        if current["state"] in {"checking", "installing", "restarting"}:
            self._save(state="installed" if current["state"] == "restarting" else "failed",
                       message="更新已生效" if current["state"] == "restarting" else "上次更新被中断，请重新检查；备份仍保留。")

    def status(self):
        current = self.ledger.get_kv("github_update", {})
        result = {"state": "idle", "auto_update": False, "installed_commit": BASE_COMMIT,
                "message": "尚未检查远程更新", **current,
                "repository": REPOSITORY, "branch": BRANCH,
                "repository_url": f"https://github.com/{REPOSITORY}",
                "current_version": self.current_version, "interval_hours": 6}
        if self.guard:
            guard = self.guard()
            result["task_guard"] = guard
            if result["state"] == "waiting":
                if not guard["blocked"]:
                    result.update(state="available" if result.get("available") else "up_to_date",
                                  message="任务阻挡已解除；自动更新已开启，将在空闲检查时安装。" if result["auto_update"] else "当前没有任务阻挡，可以点击安装更新；无需连接 Codex。")
                elif all(item["kind"] == "unverified_activity" for item in guard["blockers"]):
                    result.update(state="needs_review", message="需要核实下方历史活动记录，并非当前网页任务仍在运行。")
                else:
                    result["message"] = "暂不能安装：" + "；".join(item["label"] for item in guard["blockers"])
        return result

    def _save(self, **changes):
        with self._state_lock:
            current = self.ledger.get_kv("github_update", {})
            current.update(changes)
            current["updated_at"] = utc_now()
            self.ledger.set_kv("github_update", current)
        return self.status()

    @staticmethod
    def _fetch(url: str, limit=5_000_000) -> bytes:
        if shutil.which("curl"):
            # System curl uses the platform's TLS/proxy setup, unlike some bundled Pythons.
            result = subprocess.run(
                ["curl", "--fail", "--silent", "--show-error", "--location",
                 "--proto", "=https", "--proto-redir", "=https", "--connect-timeout", "8",
                 "--max-time", "30", "--max-filesize", str(limit),
                 "--header", "Accept: application/vnd.github+json",
                 "--user-agent", "Job-Autopilot-Updater", url],
                capture_output=True, timeout=35,
            )
            if result.returncode:
                raise RuntimeError("GitHub 连接失败：" + result.stderr.decode(errors="replace")[:250])
            if len(result.stdout) > limit:
                raise ValueError("GitHub 响应过大")
            return result.stdout
        request = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "Job-Autopilot-Updater"})
        with urlopen(request, timeout=20) as response:
            content = response.read(limit + 1)
        if len(content) > limit:
            raise ValueError("GitHub 响应过大")
        return content

    def _api(self, path):
        return json.loads(self._fetch(f"https://api.github.com/repos/{REPOSITORY}/{path}"))

    def _archive(self, sha):
        if not re.fullmatch(r"[a-f0-9]{40}", sha):
            raise ValueError("远程提交编号不正确")
        return read_archive(self._fetch(f"https://codeload.github.com/{REPOSITORY}/zip/{sha}", 40_000_000))

    def check(self):
        self._save(state="checking", message="正在连接 GitHub 检查更新")
        latest = self._api(f"commits/{BRANCH}")
        sha = latest["sha"]
        if not re.fullmatch(r"[a-f0-9]{40}", sha):
            raise ValueError("GitHub 返回了无效提交编号")
        current = self.status()
        base = current["installed_commit"]
        available = sha != base
        if available:
            comparison = self._api(f"compare/{base}...{sha}")
            if comparison.get("status") != "ahead":
                raise UpdateConflict("远程历史已回退或分叉，已阻止自动降级；请人工核对仓库。")
        return self._save(state="available" if available else "up_to_date", available=available,
                          latest_commit=sha, checked_at=utc_now(), checked_epoch=time.time(),
                          release_note=str(latest.get("commit", {}).get("message", ""))[:1000],
                          message="发现远程新提交，可以安装" if available else "远程暂无新提交；本地改动不会回退")

    def configure(self, enabled):
        if not isinstance(enabled, bool):
            raise ValueError("自动更新开关必须是布尔值")
        return self._save(auto_update=enabled, checked_epoch=0 if enabled else self.status().get("checked_epoch", 0))

    def request(self, action, *, automatic=False):
        if action not in {"check", "install"}:
            raise ValueError("不支持的更新操作")
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("正在检查或安装更新，请稍候")
        self._save(state="checking", message="正在检查远程更新")
        self._worker = threading.Thread(target=self._run, args=(action, automatic), daemon=True)
        self._worker.start()
        return self.status()

    def _run(self, action, automatic=False):
        try:
            self.storage.mkdir(mode=0o700, parents=True, exist_ok=True)
            with (self.storage / "update.lock").open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise RuntimeError("另一网页后台正在更新此插件")
                current = self.check()
                permitted = not automatic or self.status()["auto_update"]
                if action == "install" and current.get("available") and not self._stop.is_set() and permitted:
                    with self.execution_lock:
                        if self.busy():
                            self._save(state="waiting", message="任务正在运行，未中断；空闲后再安装更新。")
                        else:
                            self.installing = True
                            self.install(current["latest_commit"])
        except UpdateBusy as error:
            self._save(state="waiting", message=str(error))
        except UpdateConflict as error:
            self._save(state="conflict", message=str(error))
        except Exception as error:
            self._save(state="failed", message=f"更新未完成：{error}"[:500])
        finally:
            self.installing = self.status()["state"] == "restarting"
            self._lock.release()

    def _installation(self):
        skill = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "skills/.system/plugin-creator/scripts"
        helper = skill / "read_marketplace_name.py"
        if not helper.is_file():
            raise RuntimeError("缺少本机插件更新工具，请从 Codex 中更新插件")
        result = subprocess.run([sys.executable, str(helper)], capture_output=True, text=True, timeout=10, check=True)
        marketplace = result.stdout.strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", marketplace):
            raise ValueError("插件市场名称不正确")
        result = subprocess.run(["codex", "plugin", "list", "--marketplace", marketplace, "--json"], capture_output=True, text=True, timeout=30, check=True)
        matches = [item for item in json.loads(result.stdout).get("installed", []) if item.get("name") == "job-autopilot"]
        if len(matches) != 1 or matches[0].get("source", {}).get("source") != "local":
            raise RuntimeError("仅支持已安装到本地插件市场的 Job Autopilot；未修改其他安装来源。")
        source = Path(matches[0]["source"]["path"])
        if not source.is_absolute() or source.is_symlink() or source.name != "job-autopilot":
            raise ValueError("本地插件目录不正确")
        source = source.resolve()
        if source == Path.home() or source == source.parent or not source.is_dir():
            raise ValueError("本地插件目录不安全")
        if json.loads((source / ".codex-plugin/plugin.json").read_text()).get("name") != "job-autopilot":
            raise ValueError("本地插件名称不匹配")
        return source, marketplace, skill

    @staticmethod
    def _local_path(source, name):
        source = source.resolve()
        path = source / name
        if not managed_path(name) or source not in path.resolve().parents:
            raise ValueError("更新目标不在插件目录中")
        if any(part.is_symlink() for part in [path, *path.parents] if part != source and source in part.parents):
            raise ValueError("不能更新经过符号链接的文件")
        return path

    @staticmethod
    def _write(path, content, mode):
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".update-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
            os.chmod(temporary, mode)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def install(self, sha):
        source, marketplace, skill = self._installation()
        base = self._archive(self.status()["installed_commit"])
        remote = self._archive(sha)
        plan = {}
        for name in sorted(base.keys() | remote.keys()):
            if base.get(name) == remote.get(name):
                continue
            path = self._local_path(source, name)
            local = path.read_bytes() if path.is_file() else None
            merged = merge_content(name, local, base.get(name), remote.get(name))
            if merged != local:
                if merged and name.endswith(".py"):
                    ast.parse(merged, filename=name)
                plan[name] = (local, merged, stat.S_IMODE(path.stat().st_mode) if path.exists() else (0o755 if name.startswith("scripts/") else 0o644))
        # The manifest is also backed up, because the reinstall helper changes its suffix.
        manifest_name = ".codex-plugin/plugin.json"
        if manifest_name not in plan:
            content = (source / manifest_name).read_bytes()
            plan[manifest_name] = (content, content, 0o644)
        if self.busy():
            raise UpdateBusy("准备更新期间有任务开始，尚未改动文件；请处理下方任务后重试。")
        backup = Path(tempfile.mkdtemp(prefix="backup-", dir=self.storage))
        originals = {}
        for name, (old, _, mode) in plan.items():
            originals[name] = {"existed": old is not None, "mode": mode}
            if old is not None:
                destination = backup / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(old)
        (backup / "restore.json").write_text(json.dumps({"source": str(source), "files": originals}, indent=2))
        self._save(state="installing", backup_path=str(backup), message=f"已备份，正在安装 {sha[:8]}（{len(plan)} 个文件）")
        for name, (old, new, mode) in plan.items():
            path = self._local_path(source, name)
            if (path.read_bytes() if path.is_file() else None) != old:
                raise UpdateConflict(f"安装前检测到文件被修改：{name}")
        written = {}
        install_attempted = False
        try:
            for name, (_, new, mode) in plan.items():
                path = self._local_path(source, name)
                if new is None:
                    path.unlink(missing_ok=True)
                else:
                    self._write(path, new, mode)
                written[name] = new
            for helper in ("validate_plugin.py", "update_plugin_cachebuster.py"):
                subprocess.run([sys.executable, str(skill / helper), str(source)], capture_output=True, text=True, timeout=30, check=True)
                written[manifest_name] = (source / manifest_name).read_bytes()
            # Import smoke test uses a temporary empty ledger, never the user's database.
            with tempfile.TemporaryDirectory(prefix="job-update-smoke-") as directory:
                environment = {**os.environ, "PYTHONPATH": str(source / "runtime"), "JOB_AUTOPILOT_HOME": directory}
                subprocess.run([sys.executable, "-c", "from pathlib import Path; import os; from job_autopilot.db import Ledger; from job_autopilot.web import DashboardServer; s=DashboardServer(('127.0.0.1',0),Ledger(Path(os.environ['JOB_AUTOPILOT_HOME'])/'smoke.db')); s.server_close()"], env=environment, cwd=directory, capture_output=True, timeout=30, check=True)
            install_attempted = True
            subprocess.run(["codex", "plugin", "add", f"job-autopilot@{marketplace}"], capture_output=True, text=True, timeout=90, check=True)
        except Exception as error:
            manual = []
            for name, expected in written.items():
                old, _, mode = plan[name]
                path = self._local_path(source, name)
                if (path.read_bytes() if path.is_file() else None) != expected:
                    manual.append(name)
                    continue
                if old is None:
                    path.unlink(missing_ok=True)
                else:
                    self._write(path, old, mode)
            note = f"；以下文件被其他进程改动，未覆盖：{', '.join(manual)}" if manual else "，源文件已恢复"
            if install_attempted and not manual:
                try:
                    subprocess.run(["codex", "plugin", "add", f"job-autopilot@{marketplace}"], capture_output=True, text=True, timeout=90, check=True)
                except Exception:
                    note += "；插件缓存恢复未成功，请在 Codex 中重新安装本地插件"
            raise RuntimeError(f"安装失败（{type(error).__name__}）{note}；备份位于 {backup}")
        self._save(state="restarting" if self.restart else "installed", installed_commit=sha, available=False,
                   installed_at=utc_now(), message="更新已安装，正在重启后台；请稍后刷新页面" if self.restart else "更新已安装，重启后台后生效")
        if self.restart:
            self.restart(source)

    def tick(self):
        current = self.status()
        if not current["auto_update"] or self._lock.locked():
            return
        if self.ledger.get_kv("github_update", {}).get("state") == "waiting" and not self.busy():
            self.request("install", automatic=True)
        elif time.time() - current.get("checked_epoch", 0) >= CHECK_INTERVAL:
            # Also rate-limit failed checks to avoid retry storms.
            self._save(checked_epoch=time.time())
            self.request("install", automatic=True)

    def start(self):
        def loop():
            while not self._stop.wait(30):
                try:
                    self.tick()
                except Exception as error:
                    self._save(state="failed", message=str(error)[:500])
        self._scheduler = threading.Thread(target=loop, daemon=True)
        self._scheduler.start()

    def close(self):
        self._stop.set()
        if self._scheduler:
            self._scheduler.join(timeout=2)
