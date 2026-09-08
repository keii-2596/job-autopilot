const state = { applications: [], sourceJobs: [], sourceJobsSummary: {}, sites: [], profile: {}, settings: {}, automation: {}, profileImport: {}, codex: {}, activity: {}, filter: "", sourceJobQuery: "", sourceJobDirection: "tech" };

const statusLabels = {
  discovered: "新发现",
  queued: "待处理",
  opened: "已打开",
  auth_required: "需要登录",
  form_filling: "填写中",
  ready_for_review: "待确认",
  submitted: "已投递",
  failed: "失败",
  withdrawn: "已撤回",
  ignored: "已忽略",
};

const esc = (value = "") => String(value)
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

async function api(path, options = {}) {
  const response = await fetch(path, {
    signal: AbortSignal.timeout(10000),
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `请求失败：${response.status}`);
  return payload;
}

function toast(message) {
  const element = document.querySelector("#toast");
  element.textContent = message;
  element.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.classList.remove("show"), 2300);
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}

function statusCount(statuses) {
  return state.applications.filter((item) => !item.archived && statuses.includes(item.status)).length;
}

function renderOverview() {
  const metrics = [
    ["全部岗位", state.applications.length],
    ["未完成", statusCount(["discovered", "queued", "opened", "auth_required", "form_filling"])],
    ["待确认", statusCount(["ready_for_review"])],
    ["已投递", statusCount(["submitted"])],
  ];
  document.querySelector("#metrics").innerHTML = metrics.map(([label, count]) => `
    <article class="metric"><em></em><span>${esc(label)}</span><strong>${count}</strong></article>
  `).join("");

  const pipeline = [
    ["新发现", statusCount(["discovered"])],
    ["未完成申请", statusCount(["queued", "opened", "auth_required", "form_filling"])],
    ["等待确认", statusCount(["ready_for_review"])],
    ["提交成功", statusCount(["submitted"])],
  ];
  const max = Math.max(1, ...pipeline.map((item) => item[1]));
  document.querySelector("#pipeline").innerHTML = pipeline.map(([label, count]) => `
    <div class="pipeline-row"><span>${esc(label)}</span><div class="track"><span style="width:${Math.max(count ? 6 : 0, count / max * 100)}%"></span></div><strong>${count}</strong></div>
  `).join("");

  renderActivity();
}

function renderActivity() {
  const current = state.activity?.current;
  const events = state.activity?.events || [];
  const sourceLabels = {
    web_app_server: "网页启动",
    mcp_conversation: "嵌入式对话",
    codex_conversation: "Codex 对话",
  };
  const stageLabels = {
    preparing: "准备中", discovering: "正在找岗", screening: "筛选岗位", queueing: "整理队列",
    opening: "访问官网", authenticating: "登录中", filling: "填写资料", uploading: "上传简历",
    reviewing: "检查表单", awaiting_confirmation: "等待确认", submitting: "提交中",
    completed: "已完成", failed: "失败",
  };
  document.querySelector("#activity-source").textContent = current ? (sourceLabels[current.source] || current.source) : "共享活动流";
  document.querySelector("#recent-list").innerHTML = events.length ? events.slice(0, 8).map((item) => {
    const progress = item.progress_total ? `<span class="activity-progress">${item.progress_current}/${item.progress_total}</span> · ` : "";
    const job = item.application_title ? `${esc(item.application_title)}${item.application_company ? ` · ${esc(item.application_company)}` : ""} · ` : "";
    const itemClass = item.state === "waiting" ? "is-waiting" : (["completed", "failed"].includes(item.state) ? "is-done" : "");
    return `<div class="recent-item activity-event ${itemClass}"><i></i><div><strong>${esc(item.message || stageLabels[item.stage] || "进度已更新")}</strong><small>${progress}${job}${esc(stageLabels[item.stage] || item.stage)} · ${formatTime(item.created_at)}</small></div></div>`;
  }).join("") : '<div class="empty">从任意 Codex 对话运行 Job Autopilot 后，进展会自动出现在这里。</div>';
}

function statusOptions(current) {
  return Object.entries(statusLabels).map(([value, label]) => `<option value="${value}" ${value === current ? "selected" : ""}>${label}</option>`).join("");
}

const pendingStatuses = ["discovered", "queued", "opened", "auth_required", "form_filling"];
const splitList = (value) => String(value || "").split(/[,，、\n]/).map(x => x.trim()).filter(Boolean);
const titleOf = (item) => item.actual_title || item.title;
const citiesOf = (item) => item.actual_title ? (item.actual_locations || []) : (item.locations || []);
const urlOf = (item) => item.actual_url || item.url;
function renderApplications() {
  const rows = state.applications.filter(item => {
    if (state.filter === "archived") return item.archived;
    if (item.archived) return false;
    if (state.filter === "pending") return pendingStatuses.includes(item.status);
    return !state.filter || item.status === state.filter;
  });
  document.querySelector("#applications-table").innerHTML = rows.length ? rows.map((item) => `
    <tr>
      <td class="job-cell"><button class="text-button role-title edit-application" data-id="${item.id}" title="${esc(titleOf(item))}">${esc(titleOf(item))}</button><small>${item.actual_title ? "实际申请岗位" : "待核验 · 招聘公告"} · #${item.id}</small></td>
      <td class="job-cell"><strong>${esc(item.company)}</strong><small class="truncate" title="${esc(item.site_domain)}">${esc(item.site_domain)}</small></td>
      <td><span class="clamp" title="${esc(citiesOf(item).join("、"))}">${esc(citiesOf(item).join("、") || "未记录")}</span></td>
      <td><select aria-label="${esc(item.company)}申请状态" class="status-badge status-${esc(item.status)}" data-app-id="${item.id}">${statusOptions(item.status)}</select>${item.archived ? '<small>已归档</small>' : ''}</td>
      <td class="time-cell">${formatTime(item.updated_at)}</td>
      <td><div class="row-actions"><a class="link-button" href="${esc(urlOf(item))}" target="_blank" rel="noopener noreferrer">官网 ↗</a><button type="button" class="text-button archive-application" data-id="${item.id}">${item.archived ? "恢复" : "归档"}</button></div></td>
    </tr>`).join("") : '<tr><td colspan="6" class="empty">这个分类还没有记录。</td></tr>';
  document.querySelectorAll("select[data-app-id]").forEach(select => select.addEventListener("change", async event => {
    try {
      await api(`/api/applications/${event.target.dataset.appId}`, {method: "PATCH", body: JSON.stringify({status: event.target.value})});
      await loadData(); toast("状态已更新");
    } catch (error) { toast(error.message); }
  }));
  document.querySelectorAll(".archive-application").forEach(button => button.addEventListener("click", async () => {
    const item = state.applications.find(x => x.id === Number(button.dataset.id));
    try {
      await api(`/api/applications/${item.id}`, {method: "PATCH", body: JSON.stringify({archived: !item.archived})});
      await loadData(); toast(item.archived ? "已恢复记录" : "已归档，官网申请状态不变");
    } catch (error) { toast(error.message); }
  }));
  document.querySelectorAll(".edit-application").forEach(button => button.addEventListener("click", () => {
    const item = state.applications.find(x => x.id === Number(button.dataset.id));
    const form = document.querySelector("#detail-form");
    form.elements.application_id.value = item.id;
    form.elements.actual_title.value = item.actual_title || "";
    form.elements.actual_locations.value = (item.actual_locations || []).join(", ");
    form.elements.actual_url.value = item.actual_url || "";
    document.querySelector("#application-source").textContent = `${item.title}\n${(item.locations || []).join("、")}\n${item.url}\n${item.notes || ""}\n${item.confirmation_ref || ""}`;
    document.querySelector("#detail-dialog").showModal();
  }));
}

function renderSourceJobs() {
  const summary = state.sourceJobsSummary || {};
  const sync = summary.sync || {};
  document.querySelector("#source-total").textContent = Number(summary.filtered_total ?? summary.total ?? 0).toLocaleString("zh-CN");
  const syncText = summary.last_seen_at ? `数据更新于 ${formatTime(summary.last_seen_at)}` : "尚无职位快照";
  document.querySelector("#source-sync-copy").textContent = `${syncText}。共 ${Number(summary.total || 0).toLocaleString("zh-CN")} 条招聘公告；匹配 ${state.sourceJobs.length} 条已加载记录（最多 500 条），可搜索缩小范围。`;
  const target = document.querySelector("#source-jobs-table");
  target.innerHTML = state.sourceJobs.length ? state.sourceJobs.map((item) => {
    const detailUrl = item.apply_url || item.announcement_url;
    const batches = (item.recruitment_batches || []).join("、") || "秋招";
    const education = (item.education_levels || []).join("、") || "学历不限";
    return `<tr>
      <td class="job-cell"><details class="source-title"><summary><span class="clamp">${esc(item.title)}</span></summary><div>${esc(item.title)}</div></details><small>${esc(item.industry || (item.source_key ? `白瓜 #${item.source_key}` : "招聘公告"))}</small></td>
      <td class="job-cell"><strong>${esc(item.company_name)}</strong><small>${esc(item.company_type || item.industry || "—")}</small></td>
      <td><span class="clamp" title="${esc((item.locations || []).join("、"))}">${esc((item.locations || []).join("、") || "—")}</span></td>
      <td><div class="job-tags"><span>${esc(batches)}</span><small>${esc(education)}</small></div></td>
      <td>${esc(item.deadline_text || item.deadline_date || "—")}</td>
      <td>${formatTime(item.source_updated_at || item.updated_at)}</td>
      <td><div class="job-actions">${detailUrl ? `<a class="link-button" href="${esc(detailUrl)}" target="_blank" rel="noopener noreferrer">查看 ↗</a>` : ""}<button class="text-button queue-source-job" data-source-job-id="${item.id}">加入队列</button></div></td>
    </tr>`;
  }).join("") : '<tr><td colspan="7" class="empty">没有匹配职位，请调整岗位方向或搜索条件。</td></tr>';
  document.querySelectorAll(".queue-source-job").forEach((button) => button.addEventListener("click", async () => {
    try {
      const result = await api(`/api/source-jobs/${button.dataset.sourceJobId}/queue`, { method: "POST", body: "{}" });
      state.applications = await api("/api/applications");
      renderOverview(); renderApplications();
      toast(result.duplicate ? "这个岗位已经在投递队列中" : "已加入投递队列");
    } catch (error) { toast(error.message); }
  }));
}

function renderLibrarySync() {
  const sync = state.librarySync || {};
  const active = ["checking", "downloading", "merging"].includes(sync.state);
  const button = document.querySelector("#sync-github-jobs");
  button.disabled = active || state.sendingLibrarySync;
  button.textContent = active ? "正在同步…" : "从 GitHub 增量同步";
  document.querySelector("#github-jobs-sync-message").textContent = sync.message || "尚未从 GitHub 同步职位";
  document.querySelector("#github-jobs-sync-version").textContent = sync.synced_commit ? `数据提交：${sync.synced_commit.slice(0, 8)}` : "数据来源：GitHub · data/jobs.json";
  document.querySelector("#github-jobs-sync-time").textContent = sync.checked_at ? `最近检查：${formatTime(sync.checked_at)}` : "尚未检查";
  const toggle = document.querySelector("#auto-sync-github-jobs");
  toggle.disabled = Boolean(state.sendingLibrarySync);
  if (!state.sendingLibrarySync) toggle.checked = Boolean(sync.auto_sync);
}

async function loadLibrarySync() {
  if (state.loadingLibrarySync || state.sendingLibrarySync) return;
  state.loadingLibrarySync = true;
  const revision = state.librarySyncRevision || 0;
  try {
    const previous = state.librarySync || {};
    const sync = await api("/api/job-library-sync");
    if (revision !== (state.librarySyncRevision || 0)) return;
    state.librarySync = sync; renderLibrarySync();
    if (sync.state === "completed" && (previous.synced_commit !== sync.synced_commit || ["checking", "downloading", "merging"].includes(previous.state))) await loadSourceJobs();
  } catch (error) { document.querySelector("#github-jobs-sync-message").textContent = `同步状态暂时不可用：${error.message}`; }
  finally { state.loadingLibrarySync = false; }
}

async function librarySyncAction(action, body = {}) {
  if (state.sendingLibrarySync) return;
  state.librarySyncRevision = (state.librarySyncRevision || 0) + 1;
  state.sendingLibrarySync = true; renderLibrarySync();
  try {
    state.librarySync = await api(`/api/job-library-sync/${action}`, {method: "POST", headers: {"X-Job-Autopilot": "dashboard"}, body: JSON.stringify(body)});
    if (action === "settings") toast(state.librarySync.auto_sync ? "已开启职位数据自动同步" : "职位数据自动同步已关闭");
    if (action === "start" && state.librarySync.state === "completed") await loadSourceJobs();
  } catch (error) { toast(error.message); }
  finally { state.sendingLibrarySync = false; state.librarySyncRevision += 1; renderLibrarySync(); }
}
document.querySelector("#sync-github-jobs").addEventListener("click", () => librarySyncAction("start"));
document.querySelector("#auto-sync-github-jobs").addEventListener("change", event => librarySyncAction("settings", {auto_sync: event.target.checked}));

async function loadSourceJobs() {
  const query = encodeURIComponent(state.sourceJobQuery || "");
  const direction = encodeURIComponent(state.sourceJobDirection || "");
  const [sourceJobs, sourceJobsSummary] = await Promise.all([
    api(`/api/source-jobs?q=${query}&direction=${direction}&limit=500`),
    api(`/api/source-jobs/summary?q=${query}&direction=${direction}`),
  ]);
  if (query !== encodeURIComponent(state.sourceJobQuery || "") || direction !== encodeURIComponent(state.sourceJobDirection || "")) return;
  Object.assign(state, { sourceJobs, sourceJobsSummary });
  renderSourceJobs();
}

function renderSites() {
  document.querySelector("#sites-grid").innerHTML = state.sites.length ? state.sites.map((site) => `
    <article class="site-card"><div class="site-icon">${esc(site.domain.slice(0, 1).toUpperCase())}</div><strong>${esc(site.domain)}</strong><small>最近访问：${formatTime(site.last_seen_at)}</small><small>登录方式：${esc(site.login_method || "尚未记录")}</small></article>
  `).join("") : '<div class="empty">访问招聘网站后，会自动在这里留下记录。</div>';
}

function renderAutomation() {
  const automation = state.automation || {};
  const runtime = state.codex || {};
  const activity = state.activity?.current;
  const runtimeActive = ["starting", "running", "awaiting_input"].includes(runtime.state);
  const runtimeRecent = runtime.updated_at && (Date.parse(runtime.updated_at) >= (Date.parse(activity?.updated_at || "") || 0));
  const run = runtimeActive || runtimeRecent ? runtime : (activity || (runtime.state ? runtime : automation));
  const active = ["requested", "starting", "running", "waiting", "awaiting_input"].includes(run.state);
  const labels = {
    idle: ["尚未启动", "从这里发起找岗、继续投递或简历解析任务。"],
    requested: ["任务已发起", run.message || "正在启动 Codex"],
    starting: ["正在启动 Codex", run.message || "正在准备后台任务"],
    running: ["Autopilot 正在运行", run.message || "Codex 正在理解页面并推进任务"],
    waiting: ["需要你的确认", run.message || "确认后 Codex 会继续"],
    awaiting_input: ["需要你的确认", run.message || "确认后 Codex 会继续"],
    paused: ["任务已暂停", run.message || "可以随时继续"],
    released: ["网页已释放会话", run.message || "可以在桌面继续"],
    completed: ["本轮已完成", run.message || "所有可执行步骤已处理"],
    failed: ["本轮需要处理", run.message || "查看错误后可重新启动"],
  };
  const [title, message] = labels[run.state] || labels.idle;
  document.querySelector("#run-console").classList.toggle("is-active", active);
  document.querySelector("#run-title").textContent = title;
  document.querySelector("#run-message").textContent = message;
  document.querySelector("#run-state-badge").textContent = String(run.state || "idle").toUpperCase();
  document.querySelector("#run-time").textContent = formatTime(run.updated_at);
  const button = document.querySelector("#autopilot-button");
  const externalRun = Boolean(run === activity && active && activity.source !== "web_app_server");
  button.classList.toggle("is-active", active);
  button.disabled = externalRun;
  document.querySelector("#autopilot-button-label").textContent = externalRun ? "由 Codex 对话执行中" : (active ? "暂停 Autopilot" : "启动 Autopilot");
  renderApproval();
  renderCodexChat();
}

function renderCodexChat() {
  const runtime = state.codex || {};
  const badge = document.querySelector("#codex-connection");
  const reply = document.querySelector("#codex-reply");
  const input = document.querySelector("#codex-chat-input");
  const button = document.querySelector("#codex-send-button");
  if (!badge || !reply || !input || !button) return;
  const current = !runtime.available ? "unavailable" : (runtime.state || "idle");
  const presentations = {
    idle: ["尚未运行", "发送消息后启动 Codex。"],
    starting: ["正在启动", "正在连接任务，请稍候。"],
    running: ["正在运行", "Codex 正在处理任务；发送消息会追加到本轮。"],
    awaiting_input: ["等待你的确认", "当前任务已停在确认处，回答后才会继续。"],
    paused: ["已暂停", "当前没有执行任务，可点击「继续」。"],
    released: ["已移交桌面", "网页已释放会话；桌面未占用时可从这里继续。"],
    completed: ["本轮已完成", "当前没有执行任务，可以发送新的指令。"],
    failed: ["运行失败", "查看错误后，可以重新发送指令。"],
    unavailable: ["Codex 不可用", "请检查本地 Codex 是否已安装。"],
  };
  const [title, hint] = presentations[current] || presentations.idle;
  document.querySelector("#codex-chat").dataset.state = current;
  document.querySelector("#codex-state-title").textContent = title;
  document.querySelector("#codex-state-hint").textContent = hint;
  badge.textContent = runtime.connected ? "Codex 已连接" : (runtime.state === "starting" ? "Codex 正在连接" : "Codex 按需连接");
  badge.title = runtime.connected ? "当前已连接 Codex 运行进程" : "发送指令或继续任务时建立 Codex 连接；GitHub 更新不需要连接 Codex。";
  badge.className = `codex-connection${runtime.connected ? " is-connected" : ""}`;
  reply.textContent = runtime.last_agent_message || runtime.message || "你可以从这里下达找岗、筛选、投递和资料整理指令。";
  const blocked = state.sendingMessage || state.responding || !runtime.available || runtime.state === "awaiting_input" || runtime.state === "starting";
  input.disabled = blocked;
  button.disabled = blocked;
  button.textContent = state.sendingMessage ? "正在发送…" : (runtime.state === "running" ? "追加指令" : (runtime.state === "starting" ? "正在启动…" : "发送并启动"));
  button.classList.toggle("is-steering", runtime.state === "running");
  document.querySelectorAll("[data-quick-message]").forEach(item => { item.disabled = blocked; });
  const pending = runtime.pending_request;
  const canAuthorize = runtime.state === "awaiting_input" && runtime.connected && ["command", "file_change", "permissions"].includes(pending?.kind);
  const authorize = document.querySelector("#codex-quick-authorize");
  authorize.disabled = !canAuthorize || state.responding;
  authorize.dataset.requestId = canAuthorize ? pending.id : "";
  document.querySelector("#quick-authorization-copy").textContent = canAuthorize
    ? `仅授权当前请求：${pending.message}（不包含未来请求；详细内容见上方确认区）`
    : (pending?.kind === "user_input" ? "这次需要你回答问题，请在上方确认区填写，不能用通用授权代替。" : "有明确权限请求时，才可授权本次操作。");
  document.querySelector("#codex-release-button").disabled = !runtime.connected || runtime.state === "starting" || state.releasing;
  document.querySelector("#codex-project-copy").textContent = runtime.project_path ? `会话项目目录：${runtime.project_path}` : "";
  renderResumeImport();
}

function renderApproval() {
  const panel = document.querySelector("#codex-approval");
  const pending = state.codex?.pending_request;
  panel.hidden = !pending;
  if (!pending) { panel.dataset.requestId = ""; return; }
  if (panel.dataset.requestId === String(pending.id)) return;
  panel.dataset.requestId = String(pending.id);
  document.querySelector("#approval-message").textContent = pending.message || "确认后任务才能继续。";
  const form = document.querySelector("#approval-form");
  if (pending.kind === "user_input") {
    const questions = pending.params?.questions || [];
    form.innerHTML = questions.map((question) => {
      const options = question.options || [];
      const type = "radio";
      const optionHtml = options.length ? `<div class="approval-options">${options.map((option, index) => {
        const label = typeof option === "string" ? option : (option.label || option.value || "");
        return `<label class="approval-option"><input type="${type}" name="${esc(question.id)}" value="${esc(label)}" ${index === 0 && type === "radio" ? "checked" : ""}>${esc(label)}</label>`;
      }).join("")}</div>${question.isOther ? `<input name="${esc(question.id)}__other" placeholder="其他回答（填写后优先使用）" ${question.isSecret ? 'type="password"' : ''} autocomplete="off">` : ""}` : `<input name="${esc(question.id)}" placeholder="请输入你的回答" ${question.isSecret ? 'type="password"' : ''} autocomplete="off">`;
      return `<label class="approval-question"><span>${esc(question.question || question.header || "请确认")}</span>${question.description ? `<small>${esc(question.description)}</small>` : ""}${optionHtml}</label>`;
    }).join("") + '<div class="approval-actions"><button class="button approve" type="submit">确认并继续</button></div>';
  } else {
    const details = pending.kind === "permissions" ? `<pre class="permission-details">${esc(JSON.stringify(pending.params?.permissions || {}, null, 2))}</pre>` : "";
    form.innerHTML = details + '<div class="approval-actions"><button class="button decline" type="button" data-decision="decline">拒绝</button><button class="button approve" type="button" data-decision="accept">允许并继续</button></div>';
    form.querySelectorAll("[data-decision]").forEach((button) => button.addEventListener("click", () => respondToCodex(button.dataset.decision, pending.id)));
  }
}

async function respondToCodex(decision = "", expectedId = state.codex?.pending_request?.id) {
  const pending = state.codex?.pending_request;
  if (!pending || state.responding) return;
  if (String(pending.id) !== String(expectedId)) { toast("确认项已变化，请重新查看后操作"); return; }
  const answers = {};
  if (pending.kind === "user_input") {
    const data = new FormData(document.querySelector("#approval-form"));
    for (const question of pending.params?.questions || []) {
      const values = data.getAll(String(question.id)).map(String).filter(Boolean);
      const other = String(data.get(`${question.id}__other`) || "").trim();
      answers[question.id] = other || (values.length > 1 ? values : (values[0] || ""));
    }
  }
  try {
    state.responding = true; renderCodexChat();
    document.querySelectorAll("#approval-form button").forEach(item => { item.disabled = true; });
    state.codex = await api("/api/codex/respond", { method: "POST", body: JSON.stringify({ request_id: pending.id, answers, decision }) });
    renderAutomation(); toast(decision === "decline" ? "已拒绝本次请求" : "已确认，Codex 继续执行");
  } catch (error) { toast(error.message); }
  finally {
    state.responding = false; renderCodexChat();
    document.querySelectorAll("#approval-form button").forEach(item => { item.disabled = false; });
  }
}

function renderDynamicFields() {
  const fields = state.profile.custom_fields || [];
  document.querySelector("#dynamic-fields-list").innerHTML = fields.length ? fields.map((field) => `
    <article class="dynamic-field">
      <strong>${esc(field.label)}</strong><span>${esc(field.value)}</span>
      <small>${esc(field.scope === "global" ? "全局复用" : field.scope)}${field.aliases?.length ? ` · 已理解：${esc(field.aliases.join("、"))}` : ""}</small>
    </article>
  `).join("") : '<div class="empty">还没有扩展字段。AI 会在遇到新问题并得到你确认后自动添加。</div>';
}

function renderResumeImport() {
  const current = state.profileImport || {};
  const path = current.resume_path || state.profile.resume_path || "";
  document.querySelector("#resume-file-label").textContent = path || "先在下方填写简历绝对路径";
  const target = document.querySelector("#resume-suggestions");
  const busy = ["starting", "running", "awaiting_input"].includes(state.codex?.state);
  const own = state.codex?.resume_request_id && state.codex.resume_request_id === current.request_id;
  const parseButton = document.querySelector("#parse-resume");
  parseButton.disabled = busy || state.requestingResume || !state.codex?.available;
  parseButton.textContent = state.requestingResume ? "正在发起…" : (busy && own ? "解析任务处理中" : (["paused", "failed"].includes(current.status) ? "重新解析简历" : "AI 解析简历"));
  document.querySelector("#resume-action-hint").textContent = busy && !own ? "Codex 正在处理另一项任务。请等待完成，或先在总览暂停，再解析简历。" : "解析结果只生成建议，确认后才写入资料。";
  const progress = document.querySelector("#resume-progress");
  progress.hidden = !current.request_id;
  progress.dataset.state = current.status || "idle";
  const names = {requested: "等待接管", starting: "正在启动解析", running: "Codex 正在解析", awaiting_input: "解析等待你的确认", paused: "解析已暂停", failed: "解析未完成", ready_for_review: "解析完成 · 请确认字段", applied: "已写入个人资料"};
  document.querySelector("#resume-progress-title").textContent = names[current.status] || "尚未解析";
  document.querySelector("#resume-progress-message").textContent = current.message || "";
  document.querySelector("#resume-progress-time").textContent = current.updated_at ? `更新于 ${formatTime(current.updated_at)}` : "";
  document.querySelector("#resume-stream-connection").textContent = state.resumeStreamConnected ? "实时同步" : "正在重连 · 定时刷新兜底";
  const events = current.progress_events || [];
  const timeline = document.querySelector("#resume-progress-events");
  const eventHtml = events.slice(-5).map(item => `<li><time>${esc(formatTime(item.at))}</time><span>${esc(item.message)}</span></li>`).join("");
  if (timeline.innerHTML !== eventHtml) timeline.innerHTML = eventHtml;
  const stream = document.querySelector("#resume-stream");
  const replyText = (current.agent_messages || []).map(item => item.text).join("\n\n") || "尚未收到 Codex 的回复。此处只显示这次简历解析的公开回复。";
  if (stream.textContent !== replyText) {
    const follow = stream.scrollHeight - stream.scrollTop - stream.clientHeight < 50;
    stream.textContent = replyText;
    if (follow) stream.scrollTop = stream.scrollHeight;
  }
  document.querySelector("#resume-show-approval").hidden = !(own && current.status === "awaiting_input" && state.codex?.pending_request);
  const suggestions = current.suggestions || [];
  // Do not rebuild checkboxes on each delta/poll: preserve the user's selections.
  const signature = JSON.stringify([current.request_id, current.status, suggestions]);
  if (target.dataset.signature === signature) return;
  target.dataset.signature = signature;
  if (current.status !== "ready_for_review" || !suggestions.length) {
    target.innerHTML = "";
    return;
  }
  target.innerHTML = `
    <div class="suggestions-head"><div><strong>请确认 ${suggestions.length} 个候选字段</strong><small>取消不想保存的项</small></div><button class="button primary" id="apply-suggestions">确认写入</button></div>
    ${suggestions.map((item) => `<label class="suggestion-card"><input type="checkbox" value="${esc(item.id)}" checked><strong>${esc(item.label)}</strong><span>${esc(item.value)}</span><em class="confidence">${item.confidence == null ? "AI 建议" : `${Math.round(Number(item.confidence) * 100)}%`}</em></label>`).join("")}
  `;
  document.querySelector("#apply-suggestions").addEventListener("click", applyResumeSuggestions);
}

function renderProfile() {
  const form = document.querySelector("#profile-form");
  [...form.elements].forEach((field) => {
    if (field.name) field.value = state.profile[field.name] || "";
  });
  document.querySelector("#allowed-domains").value = (state.settings.allowed_domains || []).join("\n");
  document.querySelector("#codex-project-path").value = state.settings.codex_project_path || "";
  const automatic = state.settings.submission_mode === "automatic";
  document.querySelector("#automatic-toggle").checked = automatic;
  document.querySelector("#mode-title").textContent = automatic ? "白名单自动推进" : "提交前确认";
  document.querySelector("#mode-copy").textContent = automatic ? "允许的网站会自动推进到最终确认页，并保存填写进度。" : "系统可以填写完整表单，最终提交由你确认。";
  const preferences = state.settings.job_preferences || {};
  for (const field of document.querySelector("#preferences-form").elements) {
    if (!field.name) continue;
    const value = preferences[field.name];
    if (field.type === "checkbox") field.checked = Boolean(value);
    else field.value = Array.isArray(value) ? value.join(", ") : (value || "");
  }
  renderDynamicFields();
  renderResumeImport();
}

async function loadData() {
  try {
    const sourceQuery = encodeURIComponent(state.sourceJobQuery || "");
    const sourceDirection = encodeURIComponent(state.sourceJobDirection || "");
    const [applications, sourceJobs, sourceJobsSummary, sites, profile, settings, automation, profileImport, codex, activity] = await Promise.all([
      api("/api/applications"), api(`/api/source-jobs?q=${sourceQuery}&direction=${sourceDirection}&limit=500`), api(`/api/source-jobs/summary?q=${sourceQuery}&direction=${sourceDirection}`), api("/api/sites"), api("/api/profile"), api("/api/settings"), api("/api/automation"), api("/api/profile/import"), api("/api/codex"), api("/api/activity"),
    ]);
    Object.assign(state, { applications, sourceJobs, sourceJobsSummary, sites, profile, settings, automation, profileImport, codex, activity });
    renderOverview(); renderSourceJobs(); renderApplications(); renderSites(); renderProfile(); renderAutomation();
    document.querySelector("#connection-error").hidden = true;
    return true;
  } catch (error) { document.querySelector("#connection-error").hidden = false; toast(error.message); return false; }
}

function showView(name) {
  document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active", view.id === `view-${name}`));
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === name));
  const titles = { overview: "今天继续向前。", jobs: "只看真正属于 27 届的秋招机会。", applications: "每一次申请，都有迹可循。", sites: "登录过的网站，不必重新摸索。", profile: "先把真实信息准备好。" };
  document.querySelector("#page-title").textContent = titles[name] || titles.overview;
  if (name === "updates") {
    document.querySelector("#page-title").textContent = "更新功能，保留你的进度。";
    loadUpdates();
  }
}

function renderUpdates() {
  const update = state.updates || {};
  const labels = {idle: "尚未检查", checking: "正在检查", available: "有可用更新", up_to_date: "远程已同步", waiting: "等待任务空闲", needs_review: "核实历史任务", installing: "正在安装", restarting: "正在重启", installed: "更新已安装", conflict: "本地修改冲突", failed: "更新未完成"};
  document.querySelector("#update-panel").dataset.state = update.state || "idle";
  document.querySelector("#update-state").textContent = labels[update.state] || "尚未检查";
  document.querySelector("#update-current-version").textContent = update.current_version || "—";
  document.querySelector("#update-base-commit").textContent = update.installed_commit?.slice(0, 8) || "—";
  document.querySelector("#update-latest-commit").textContent = update.latest_commit?.slice(0, 8) || "—";
  document.querySelector("#update-check-time").textContent = formatTime(update.checked_at);
  document.querySelector("#update-message").textContent = update.message || "尚未检查远程更新";
  document.querySelector("#update-release-note").textContent = update.release_note || "";
  document.querySelector("#update-backup").textContent = update.backup_path ? `最近源文件备份：${update.backup_path}` : "";
  const busy = state.updating || ["checking", "installing", "restarting"].includes(update.state);
  document.querySelector("#check-updates").disabled = busy;
  document.querySelector("#install-update").disabled = busy || !update.available || Boolean(update.task_guard?.blocked);
  document.querySelector("#auto-update").disabled = state.updating || update.state === "installing";
  if (!state.updating) document.querySelector("#auto-update").checked = Boolean(update.auto_update);
  const guard = update.task_guard || {};
  const target = document.querySelector("#update-task-guard");
  const entries = [...(guard.blockers || []), ...(guard.stale_web || [])];
  const markup = entries.map(item => `<div class="update-task" data-kind="${esc(item.kind)}"><div><strong>${esc(item.label)}</strong><small>${esc(item.detail)}</small>${item.updated_at ? `<small>最后记录：${esc(formatTime(item.updated_at))} · ${item.kind === "unverified_activity" ? "尚未核实存活状态" : "遗留网页记录"}</small>` : ""}</div>${item.clearable ? `<button class="button ghost" type="button" data-reconcile-run="${esc(item.run_id)}" ${busy ? "disabled" : ""}>${item.kind === "unverified_activity" ? "确认原任务已停止" : "整理遗留状态"}</button>` : '<button class="button ghost" type="button" data-update-overview>查看当前任务</button>'}</div>`).join("");
  if (target.innerHTML !== markup) target.innerHTML = markup;
  target.hidden = !entries.length;
}

async function loadUpdates() {
  if (state.loadingUpdates || state.updating) return;
  state.loadingUpdates = true;
  const revision = state.updateRevision || 0;
  try {
    const previous = state.updates?.state;
    const update = await api("/api/updates");
    if (revision !== (state.updateRevision || 0)) return;
    state.updates = update;
    if (["restarting", "installing"].includes(previous) && state.updates.state === "installed") {
      location.reload(); return;
    }
    renderUpdates();
  } catch (error) {
    document.querySelector("#update-message").textContent = state.updates?.state === "restarting" ? "后台正在重启，页面会自动重连…" : `暂时无法读取更新状态：${error.message}`;
  } finally { state.loadingUpdates = false; }
}

async function updateAction(action, body = {}) {
  if (state.updating) return;
  state.updateRevision = (state.updateRevision || 0) + 1;
  state.updating = true; renderUpdates();
  try {
    state.updates = await api(`/api/updates/${action}`, {method: "POST", headers: {"X-Job-Autopilot": "dashboard"}, body: JSON.stringify(body)});
    if (action === "settings") toast(state.updates.auto_update ? "已开启空闲时自动更新" : "自动更新已关闭");
    if (action === "reconcile") { toast(`已整理 ${state.updates.reconciled || 0} 条活动记录；投递进度保留`); await loadData(); }
  } catch (error) { toast(error.message); }
  finally { state.updating = false; state.updateRevision += 1; renderUpdates(); }
}
document.querySelector("#check-updates").addEventListener("click", () => updateAction("check"));
document.querySelector("#install-update").addEventListener("click", () => updateAction("install"));
document.querySelector("#auto-update").addEventListener("change", event => updateAction("settings", {auto_update: event.target.checked}));
document.querySelector("#update-task-guard").addEventListener("click", event => {
  if (event.target.closest("[data-update-overview]")) { showView("overview"); return; }
  const button = event.target.closest("[data-reconcile-run]");
  if (!button || state.updating) return;
  const guard = state.updates?.task_guard || {};
  const item = [...(guard.blockers || []), ...(guard.stale_web || [])].find(item => item.run_id === button.dataset.reconcileRun);
  if (!item) return;
  const external = item.kind === "unverified_activity";
  if (external && !window.confirm(`请先确认「${item.label}」已在原对话中停止。\n此操作只把本地活动记录改为暂停，不会中断任务，也不会删除投递记录。\n确认原任务已停止并整理状态？`)) return;
  updateAction("reconcile", {entries: [{run_id: item.run_id, revision: item.revision}], confirmed: external});
});

document.querySelectorAll(".nav-item").forEach((item) => item.addEventListener("click", () => showView(item.dataset.view)));
document.querySelectorAll("[data-target-view]").forEach((item) => item.addEventListener("click", () => showView(item.dataset.targetView)));
document.querySelector("#refresh-button").addEventListener("click", async () => { if (await loadData()) toast("已刷新"); });
document.querySelector("#add-button").addEventListener("click", () => document.querySelector("#application-dialog").showModal());
document.querySelector("#add-field").addEventListener("click", () => document.querySelector("#field-dialog").showModal());
let sourceSearchTimer;
document.querySelector("#source-job-query").addEventListener("input", (event) => {
  state.sourceJobQuery = event.target.value.trim();
  clearTimeout(sourceSearchTimer);
  sourceSearchTimer = setTimeout(() => loadSourceJobs().catch((error) => toast(error.message)), 250);
});

document.querySelector("#source-job-direction").addEventListener("change", (event) => {
  state.sourceJobDirection = event.target.value;
  loadSourceJobs().catch((error) => toast(error.message));
});

document.querySelector("#autopilot-button").addEventListener("click", async () => {
  const runtimeState = state.codex?.state;
  const active = runtimeState
    ? ["starting", "running", "awaiting_input"].includes(runtimeState)
    : ["requested", "running"].includes(state.automation.state);
  if (active) {
    try {
      const result = await api("/api/automation/stop", { method: "POST", body: "{}" });
      Object.assign(state, result);
      renderAutomation(); toast("Autopilot 已暂停");
    } catch (error) { toast(error.message); }
  } else {
    const preferences = state.settings.job_preferences || {};
    const form = document.querySelector("#run-form");
    form.elements.keywords.value = (preferences.keywords || []).join(", ");
    form.elements.locations.value = (preferences.locations || []).join(", ");
    form.elements.recruitment_type.value = preferences.recruitment_types?.[0] || "campus";
    document.querySelector("#run-dialog").showModal();
  }
});

document.querySelectorAll(".filter").forEach((item) => item.addEventListener("click", () => {
  document.querySelectorAll(".filter").forEach((button) => button.classList.remove("active"));
  item.classList.add("active"); state.filter = item.dataset.status; renderApplications();
}));

document.querySelector("#automatic-toggle").addEventListener("change", async (event) => {
  try {
    const mode = event.target.checked ? "automatic" : "review";
    state.settings = await api("/api/settings", { method: "PUT", body: JSON.stringify({ submission_mode: mode }) });
    renderProfile();
    toast(mode === "automatic" ? "自动推进已开启，仅对白名单生效" : "已切换为提交前确认");
  } catch (error) { event.target.checked = !event.target.checked; toast(error.message); }
});

document.querySelector("#profile-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.target).entries());
  try {
    state.profile = await api("/api/profile", { method: "PUT", body: JSON.stringify(data) });
    document.querySelector("#profile-message").textContent = "已保存在本机";
    toast("个人资料已保存");
  } catch (error) { toast(error.message); }
});

document.querySelector("#run-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const split = (value) => String(value || "").split(/[,，]/).map((item) => item.trim()).filter(Boolean);
  const body = {
    action: form.get("action"),
    criteria: {
      ...state.settings.job_preferences,
      keywords: split(form.get("keywords")),
      locations: split(form.get("locations")),
      recruitment_type: form.get("recruitment_type"),
      recruitment_types: [form.get("recruitment_type")],
    },
  };
  try {
    state.settings = await api("/api/settings", {method: "PUT", body: JSON.stringify({job_preferences: body.criteria})});
    const result = await api("/api/automation/start", { method: "POST", body: JSON.stringify(body) });
    Object.assign(state, result);
    document.querySelector("#run-dialog").close(); renderAutomation();
    toast("Codex 后台任务正在启动");
  } catch (error) { document.querySelector("#run-dialog-message").textContent = error.message; }
});

document.querySelector("#parse-resume").addEventListener("click", async () => {
  if (state.requestingResume) return;
  const path = document.querySelector('input[name="resume_path"]').value.trim();
  if (!path) { toast("请先填写简历绝对路径"); return; }
  try {
    state.requestingResume = true; renderResumeImport();
    const result = await api("/api/profile/resume-request", { method: "POST", body: JSON.stringify({ resume_path: path }) });
    state.profileImport = result.profile_import;
    state.automation = result.automation;
    state.codex = result.codex;
    renderResumeImport(); renderAutomation();
    toast("简历解析请求已接受，可在下方查看实时进展");
  } catch (error) { toast(error.message); }
  finally { state.requestingResume = false; renderResumeImport(); }
});

async function applyResumeSuggestions() {
  const ids = [...document.querySelectorAll('.suggestion-card input:checked')].map((item) => item.value);
  try {
    state.profile = await api("/api/profile/import/apply", { method: "POST", body: JSON.stringify({ suggestion_ids: ids }) });
    state.profileImport = await api("/api/profile/import");
    renderProfile(); toast("已写入你确认的资料");
  } catch (error) { toast(error.message); }
}

document.querySelector("#field-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const body = Object.fromEntries(new FormData(event.target).entries());
  body.aliases = String(body.aliases || "").split(/[,，]/).map((item) => item.trim()).filter(Boolean);
  try {
    await api("/api/profile/fields", { method: "POST", body: JSON.stringify(body) });
    state.profile = await api("/api/profile");
    document.querySelector("#field-dialog").close(); event.target.reset(); renderProfile();
    toast("已记住这个字段及相近问法");
  } catch (error) { document.querySelector("#field-dialog-message").textContent = error.message; }
});

document.querySelector("#save-policy").addEventListener("click", async () => {
  const domains = document.querySelector("#allowed-domains").value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
  try {
    state.settings = await api("/api/settings", { method: "PUT", body: JSON.stringify({ allowed_domains: domains }) });
    renderProfile(); toast("网站白名单已保存");
  } catch (error) { toast(error.message); }
});

document.querySelector("#application-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const body = Object.fromEntries(form.entries());
  body.locations = body.locations.split(/[,，]/).map((value) => value.trim()).filter(Boolean);
  if (body.jdwatch_id) body.jdwatch_id = Number(body.jdwatch_id); else delete body.jdwatch_id;
  try {
    const result = await api("/api/applications", { method: "POST", body: JSON.stringify(body) });
    document.querySelector("#application-dialog").close(); event.target.reset(); await loadData();
    toast(result.duplicate ? "这个岗位已经记录过" : "岗位已加入队列");
  } catch (error) { document.querySelector("#dialog-message").textContent = error.message; }
});

document.querySelector("#approval-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await respondToCodex();
});

document.querySelector("#codex-chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = document.querySelector("#codex-chat-input");
  await sendCodexMessage(input.value.trim(), true);
});

async function sendCodexMessage(message, clearDraft = false) {
  const input = document.querySelector("#codex-chat-input");
  if (!message || state.sendingMessage) return;
  const steering = state.codex.state === "running";
  state.sendingMessage = true;
  renderCodexChat();
  try {
    state.codex = await api("/api/codex/message", {
      method: "POST",
      body: JSON.stringify({ message }),
    });
    if (clearDraft && input.value.trim() === message) input.value = "";
    renderAutomation();
    toast(steering ? "补充指令已发送" : "消息已发送给 Codex");
  } catch (error) { toast(error.message); }
  finally { state.sendingMessage = false; renderCodexChat(); }
}

document.querySelectorAll("[data-quick-message]").forEach(button => {
  button.addEventListener("click", () => sendCodexMessage(button.dataset.quickMessage));
});
document.querySelector("#codex-quick-authorize").addEventListener("click", event => {
  respondToCodex("accept", event.currentTarget.dataset.requestId);
});
document.querySelector("#resume-show-approval").addEventListener("click", () => {
  document.querySelector('[data-view="overview"]').click();
  document.querySelector("#codex-approval").scrollIntoView({behavior: "smooth", block: "center"});
  document.querySelector("#approval-form input, #approval-form button")?.focus({preventScroll: true});
});

document.querySelector("#codex-release-button").addEventListener("click", async () => {
  state.releasing = true; renderCodexChat();
  try {
    state.codex = await api("/api/codex/release", {method: "POST", body: "{}"});
    toast("网页已释放会话，请在桌面任务中点击重试");
    renderAutomation();
  } catch (error) { toast(error.message); }
  finally { state.releasing = false; renderCodexChat(); }
});

document.querySelector("#policy-check-form").addEventListener("submit", async event => {
  event.preventDefault();
  const result = document.querySelector("#policy-check-result");
  try {
    const data = await api(`/api/policy/check?url=${encodeURIComponent(document.querySelector("#policy-check-url").value.trim())}`);
    result.textContent = data.allowed ? `${data.hostname} 匹配 ${data.matched_rule}${data.automatic ? "，可自动推进" : "；当前未开启自动推进"}` : `${data.hostname} 未匹配白名单`;
  } catch (error) { result.textContent = error.message; }
});

document.querySelector("#codex-project-form").addEventListener("submit", async event => {
  event.preventDefault();
  const result = document.querySelector("#codex-project-result");
  try {
    state.settings = await api("/api/settings", {method: "PUT", body: JSON.stringify({codex_project_path: document.querySelector("#codex-project-path").value.trim()})});
    state.codex = await api("/api/codex"); renderCodexChat();
    result.textContent = "已保存，下次网页发起任务时生效";
  } catch (error) { result.textContent = error.message; }
});

document.querySelector("#load-codex-projects").addEventListener("click", async () => {
  const result = document.querySelector("#codex-project-result");
  try {
    const projects = await api("/api/codex/projects");
    const picker = document.querySelector("#codex-project-picker");
    picker.replaceChildren(new Option("选择一个项目…", ""));
    for (const project of projects) {
      for (const root of project.roots || []) {
        picker.add(new Option(project.name + (project.roots.length > 1 ? ` · ${root.path}` : ""), root.path));
      }
    }
    picker.value = document.querySelector("#codex-project-path").value;
    document.querySelector("#codex-project-picker-label").hidden = false;
    result.textContent = projects.length ? "选择项目后点击保存" : "请先在 Codex 桌面添加项目";
  } catch (error) { result.textContent = error.message; }
});
document.querySelector("#codex-project-picker").addEventListener("change", event => {
  if (event.target.value) document.querySelector("#codex-project-path").value = event.target.value;
});

document.querySelectorAll("[data-close-dialog]").forEach(button => button.addEventListener("click", () => button.closest("dialog").close()));
document.querySelectorAll("dialog").forEach(dialog => {
  dialog.addEventListener("click", event => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
  });
  dialog.addEventListener("close", () => dialog.querySelectorAll('[id$="message"]').forEach(x => x.textContent = ""));
});
document.querySelector("#retry-connection").addEventListener("click", loadData);
document.querySelector("#preferences-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.target;
  const preferences = {};
  for (const key of ["keywords", "locations", "excluded_keywords", "excluded_companies", "recruitment_types"]) preferences[key] = splitList(form.elements[key].value);
  for (const key of ["prefer_phone_login", "skip_wechat_only", "prefer_known_companies"]) preferences[key] = form.elements[key].checked;
  for (const key of ["graduation_year", "employment_type"]) preferences[key] = form.elements[key].value.trim();
  try {
    state.settings = await api("/api/settings", {method: "PUT", body: JSON.stringify({job_preferences: preferences})});
    toast("求职偏好已保存，新任务将沿用这些条件");
  } catch (error) { toast(error.message); }
});
document.querySelector("#detail-form").addEventListener("submit", async event => {
  event.preventDefault();
  const body = Object.fromEntries(new FormData(event.target));
  body.actual_locations = splitList(body.actual_locations);
  try {
    await api(`/api/applications/${body.application_id}`, {method: "PATCH", body: JSON.stringify(body)});
    document.querySelector("#detail-dialog").close(); await loadData(); toast("实际投递信息已更新");
  } catch (error) { document.querySelector("#detail-message").textContent = error.message; }
});
const initialView = new URLSearchParams(window.location.search).get("view");
if (["overview", "jobs", "applications", "sites", "profile", "updates"].includes(initialView)) showView(initialView);
loadData();
loadUpdates();
loadLibrarySync();
setInterval(loadLibrarySync, 2500);
setInterval(loadUpdates, 2500);
// SSE carries actual agent deltas and request state; polling recovers if streaming is unavailable.
if (typeof EventSource !== "undefined") {
  const resumeEvents = new EventSource("/api/profile/import/events");
  resumeEvents.addEventListener("profile", event => {
    try {
      state.profileImport = JSON.parse(event.data);
      state.resumeStreamConnected = true;
      renderResumeImport();
    } catch (_) { state.resumeStreamConnected = false; }
  });
  resumeEvents.onopen = () => { state.resumeStreamConnected = true; renderResumeImport(); };
  resumeEvents.onerror = () => { state.resumeStreamConnected = false; renderResumeImport(); };
  window.addEventListener("pagehide", () => resumeEvents.close());
}
setInterval(async () => {
  if (state.polling) return;
  state.polling = true;
  try {
  if (!document.querySelector("#connection-error").hidden) { await loadData(); return; }
  try {
    const [codex, automation, activity, applications] = await Promise.all([api("/api/codex"), api("/api/automation"), api("/api/activity"), api("/api/applications")]);
    state.codex = codex;
    state.automation = automation;
    state.activity = activity;
    if (!state.resumeStreamConnected) state.profileImport = await api("/api/profile/import");
    const applicationsChanged = JSON.stringify(state.applications) !== JSON.stringify(applications);
    state.applications = applications;
    state.applicationsDirty ||= applicationsChanged;
    if (state.applicationsDirty && !document.activeElement?.matches("select[data-app-id]")) {
      renderApplications(); state.applicationsDirty = false;
    }
    renderOverview(); renderAutomation();
  } catch (_) { document.querySelector("#connection-error").hidden = false; }
  } finally { state.polling = false; }
}, 1800);
