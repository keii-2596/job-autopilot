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

async function loadSourceJobs() {
  const query = encodeURIComponent(state.sourceJobQuery || "");
  const direction = encodeURIComponent(state.sourceJobDirection || "");
  const [sourceJobs, sourceJobsSummary] = await Promise.all([
    api(`/api/source-jobs?q=${query}&direction=${direction}&limit=500`),
    api(`/api/source-jobs/summary?q=${query}&direction=${direction}`),
  ]);
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
  const run = activity || (runtime.state ? runtime : automation);
  const active = ["requested", "starting", "running", "waiting", "awaiting_input"].includes(run.state);
  const labels = {
    idle: ["尚未启动", "从这里发起找岗、继续投递或简历解析任务。"],
    requested: ["任务已发起", run.message || "正在启动 Codex"],
    starting: ["正在启动 Codex", run.message || "正在准备后台任务"],
    running: ["Autopilot 正在运行", run.message || "Codex 正在理解页面并推进任务"],
    waiting: ["需要你的确认", run.message || "确认后 Codex 会继续"],
    awaiting_input: ["需要你的确认", run.message || "确认后 Codex 会继续"],
    paused: ["任务已暂停", run.message || "可以随时继续"],
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
  const externalRun = Boolean(activity && active && activity.source !== "web_app_server");
  button.classList.toggle("is-active", active);
  button.disabled = externalRun;
  document.querySelector("#autopilot-button-label").textContent = externalRun ? "由 Codex 对话执行中" : (active ? "暂停 Autopilot" : "启动 Autopilot");
  renderApproval();
}

function renderApproval() {
  const panel = document.querySelector("#codex-approval");
  const pending = state.codex?.pending_request;
  panel.hidden = !pending;
  if (!pending) return;
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
    form.innerHTML = '<div class="approval-actions"><button class="button decline" type="button" data-decision="decline">拒绝</button><button class="button approve" type="button" data-decision="accept">允许并继续</button></div>';
    form.querySelectorAll("[data-decision]").forEach((button) => button.addEventListener("click", () => respondToCodex(button.dataset.decision)));
  }
}

async function respondToCodex(decision = "") {
  const pending = state.codex?.pending_request;
  if (!pending) return;
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
    state.codex = await api("/api/codex/respond", { method: "POST", body: JSON.stringify({ request_id: pending.id, answers, decision }) });
    renderAutomation(); toast("已确认，Codex 继续执行");
  } catch (error) { toast(error.message); }
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
  const path = state.profile.resume_path || current.resume_path || "";
  document.querySelector("#resume-file-label").textContent = path || "先在下方填写简历绝对路径";
  const target = document.querySelector("#resume-suggestions");
  if (current.status === "requested") {
    target.innerHTML = '<div class="callout"><strong>AI 解析请求已发起</strong><span>提取完成后，候选字段会出现在这里供你确认。</span></div>';
    return;
  }
  const suggestions = current.suggestions || [];
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
}

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
  const path = document.querySelector('input[name="resume_path"]').value.trim();
  if (!path) { toast("请先填写简历绝对路径"); return; }
  try {
    state.profile.resume_path = path;
    await api("/api/profile", { method: "PUT", body: JSON.stringify({ resume_path: path }) });
    const result = await api("/api/profile/resume-request", { method: "POST", body: JSON.stringify({ resume_path: path }) });
    state.profileImport = result.profile_import;
    state.automation = result.automation;
    state.codex = result.codex;
    renderProfile(); renderAutomation();
    toast("Codex 正在解析简历");
  } catch (error) { toast(error.message); }
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
  const domains = document.querySelector("#allowed-domains").value.split(/\s+/).map((value) => value.trim()).filter(Boolean);
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
if (["overview", "jobs", "applications", "sites", "profile"].includes(initialView)) showView(initialView);
loadData();
setInterval(async () => {
  if (!document.querySelector("#connection-error").hidden) { await loadData(); return; }
  try {
    const [codex, automation, activity, applications] = await Promise.all([api("/api/codex"), api("/api/automation"), api("/api/activity"), api("/api/applications")]);
    state.codex = codex;
    state.automation = automation;
    state.activity = activity;
    const applicationsChanged = JSON.stringify(state.applications) !== JSON.stringify(applications);
    state.applications = applications;
    state.applicationsDirty ||= applicationsChanged;
    if (state.applicationsDirty && !document.activeElement?.matches("select[data-app-id]")) {
      renderApplications(); state.applicationsDirty = false;
    }
    renderOverview(); renderAutomation();
  } catch (_) { document.querySelector("#connection-error").hidden = false; }
}, 1800);
