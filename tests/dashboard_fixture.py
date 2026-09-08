"""Isolated demo server for dashboard.e2e.cjs; never opens the user's database."""
import tempfile
from pathlib import Path
from job_autopilot.db import Ledger
from job_autopilot.web import DashboardServer

with tempfile.TemporaryDirectory(prefix="job-autopilot-e2e-") as temporary:
    ledger = Ledger(Path(temporary) / "test.db")
    for index, status in enumerate(["submitted", "auth_required", "form_filling", "ready_for_review"], 1):
        item = ledger.record_application(title="AI应用、算法、产品等招聘公告", company=f"示例公司 {index}", url=f"https://example.com/jobs/{index}", locations=["上海", "杭州"], status=status)
        if index == 1:
            ledger.update_application(item["id"], actual_title="AI全栈工程师（上海）", actual_locations=["上海"])
    ledger.update_settings({"codex_project_path": str(Path(temporary).resolve()), "job_preferences": {"locations": ["上海", "杭州"], "keywords": ["Agent应用开发", "后端开发"]}})
    server = DashboardServer(("127.0.0.1", 0), ledger)
    print(f"E2E_URL=http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
