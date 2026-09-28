(() => {
  "use strict";

  const state = {
    view: "overview",
    overview: null,
    pages: { tasks: 1, nodes: 1, bindings: 1 },
    pageSize: 20,
    promptVersions: [],
    selectedPrompt: null,
    promptDraftContent: null,
    selectedPromptStepCode: null,
  };
  const viewTitles = { overview: "运行总览", tasks: "任务中心", nodes: "节点资源", bindings: "流绑定", prompts: "AI评课提示词" };
  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => Array.from(document.querySelectorAll(selector));
  const promptEditorSelectors = [
    "#prompt-subtitle",
    "#prompt-step-tabs",
    "#prompt-step-name",
    "#prompt-step-code",
    "#prompt-step-model",
    "#prompt-step-temperature",
    "#prompt-step-max-tokens",
    "#prompt-step-system",
    "#prompt-step-user",
  ];
  const apiKey = $("#api-key");
  apiKey.value = sessionStorage.getItem("media-admin-api-key") || "";

  function escapeHtml(value) {
    return String(value ?? "—").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
  }

  function showNotice(message, error = false) {
    const notice = $("#notice");
    notice.textContent = message;
    notice.classList.toggle("error", error);
    notice.classList.add("show");
    window.clearTimeout(showNotice.timer);
    showNotice.timer = window.setTimeout(() => notice.classList.remove("show"), 3600);
  }

  function setSyncTime() {
    $("#last-sync").textContent = `最近同步 ${new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;
  }

  async function request(path, options = {}) {
    const headers = { Accept: "application/json", ...(options.headers || {}) };
    const key = apiKey.value.trim();
    if (key) headers["X-API-Key"] = key;
    const response = await fetch(path, { ...options, headers });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try { detail = (await response.json()).detail || detail; } catch (_) { /* plain response */ }
      throw new Error(detail);
    }
    return response.json();
  }

  function statusClass(status) {
    const normalized = String(status || "").toLowerCase();
    if (["online", "ready", "active", "completed", "published"].includes(normalized)) return "good";
    if (["processing", "post_processing", "pending", "claimed", "draining"].includes(normalized)) return "warn";
    if (["failed", "offline", "disabled", "maintenance"].includes(normalized)) return "bad";
    return "";
  }

  function statusPill(status) { return `<span class="status-pill ${statusClass(status)}">${escapeHtml(status)}</span>`; }

  function renderPagination(targetId, kind, data, onPage) {
    const target = $(targetId);
    const total = Number(data.total || 0);
    const page = Number(data.page || 1);
    const pageSize = Number(data.page_size || state.pageSize);
    const pages = Math.max(1, Math.ceil(total / pageSize));
    state.pages[kind] = page;
    if (!total) { target.innerHTML = ""; return; }
    target.innerHTML = `<span class="pagination-info">共 ${total} 条</span><button class="pagination-button" data-page-kind="${kind}" data-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>上一页</button><span class="pagination-current">${page} / ${pages}</span><button class="pagination-button" data-page-kind="${kind}" data-page="${page + 1}" ${page >= pages ? "disabled" : ""}>下一页</button>`;
    target.querySelectorAll("button[data-page]").forEach((button) => button.addEventListener("click", () => onPage(Number(button.dataset.page))));
  }

  function renderNodeHealth(nodes) {
    const items = nodes.slice(0, 4);
    if (!items.length) {
      $("#node-health-list").innerHTML = `<div class="empty-message">暂无节点心跳数据</div>`;
      return;
    }
    $("#node-health-list").classList.remove("empty-state");
    $("#node-health-list").innerHTML = items.map((node) => {
      const offline = String(node.status || "").toLowerCase() !== "online";
      return `<div class="node-health"><i class="node-dot ${offline ? "offline" : ""}"></i><div><div class="node-name">${escapeHtml(node.node_name || node.node_code)}</div><div class="node-sub">${escapeHtml(node.node_type)} · ${node.active_tasks || 0} 个执行任务 · ${node.active_bindings || 0} 个绑定</div></div><span class="node-status ${offline ? "offline" : ""}">${escapeHtml(node.status)}</span></div>`;
    }).join("");
  }

  function go(view) {
    state.view = view;
    $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === view));
    $$(".view").forEach((panel) => panel.classList.toggle("active-view", panel.id === `view-${view}`));
    $("#breadcrumb-title").textContent = viewTitles[view];
    if (view === "overview") loadOverview();
    if (view === "nodes") loadNodes();
    if (view === "tasks") loadTasks();
    if (view === "bindings") loadBindings();
    if (view === "prompts") loadPrompts();
  }

  function renderOverview(data) {
    state.overview = data;
    const tasks = data.tasks_by_status || {};
    const nodes = data.nodes_by_status || {};
    const queues = data.queues || [];
    const active = Object.entries(tasks).filter(([key]) => ["processing", "post_processing"].includes(key.toLowerCase())).reduce((sum, [, value]) => sum + Number(value || 0), 0);
    const online = Object.entries(nodes).filter(([key]) => key.toLowerCase() === "online").reduce((sum, [, value]) => sum + Number(value || 0), 0);
    const queueDepth = queues.reduce((sum, item) => sum + Number(item.message_count || 0), 0);
    $("#metric-active").textContent = active;
    $("#metric-online").textContent = online;
    $("#metric-bindings").textContent = "—";
    $("#metric-queue").textContent = queueDepth;
    $("#task-total").textContent = `${Object.values(tasks).reduce((sum, value) => sum + Number(value || 0), 0)} 条任务`;
    $("#queue-updated").textContent = queues.length ? `${queues.length} 个队列` : "暂无队列快照";
    const max = Math.max(1, ...Object.values(tasks).map(Number));
    const chart = Object.entries(tasks).sort(([, a], [, b]) => Number(b) - Number(a)).map(([name, value]) => `<div class="bar-row"><span class="bar-name">${escapeHtml(name)}</span><span class="bar-track"><span class="bar-fill" style="width:${Math.max(3, Number(value) / max * 100)}%"></span></span><strong class="bar-value">${Number(value || 0)}</strong></div>`).join("");
    $("#task-status-chart").classList.remove("empty-state");
    $("#task-status-chart").innerHTML = chart || `<div class="empty-message">暂无任务状态数据</div>`;
    renderQueues(queues);
    loadNodes(true);
  }

  function renderQueues(queues) {
    if (!queues.length) { $("#queue-table").innerHTML = `<div class="empty-message">RabbitMQ 当前没有可展示的队列快照</div>`; return; }
    $("#queue-table").innerHTML = `<table><thead><tr><th>队列</th><th>状态</th><th>待处理消息</th><th>消费者</th><th>说明</th></tr></thead><tbody>${queues.map((item) => `<tr><td class="mono">${escapeHtml(item.queue_name)}</td><td>${statusPill(item.status)}</td><td><strong>${item.message_count ?? "—"}</strong></td><td>${item.consumer_count ?? "—"}</td><td>${escapeHtml(item.error_message || "正常读取")}</td></tr>`).join("")}</tbody></table>`;
  }

  function renderNodes(data) {
    const nodes = data.items || [];
    $("#node-result-count").textContent = `共 ${data.total || 0} 个节点 · 第 ${data.page || 1} 页`;
    if (!nodes.length) { $("#nodes-table").innerHTML = `<div class="empty-message">暂无节点心跳数据</div>`; renderPagination("#nodes-pagination", "nodes", data, loadNodesPage); return; }
    $("#nodes-table").classList.remove("empty-state");
    $("#nodes-table").innerHTML = `<table><thead><tr><th>节点</th><th>类型</th><th>状态</th><th>最后心跳</th><th>绑定</th><th>执行任务</th><th>能力</th><th>容量快照</th></tr></thead><tbody>${nodes.map((node) => { const capacity = node.capacity || {}; const load = Math.min(100, Number(capacity.processing_tasks || node.active_tasks || 0) * 20); return `<tr><td><strong>${escapeHtml(node.node_name || node.node_code)}</strong><div class="mono">${escapeHtml(node.node_code)}</div></td><td>${escapeHtml(node.node_type)}</td><td>${statusPill(node.status)}<div class="node-sub">${escapeHtml(node.readiness_status)}</div></td><td>${formatDate(node.last_heartbeat_at)}</td><td>${node.active_bindings}</td><td>${node.active_tasks}</td><td>${escapeHtml((node.capabilities || []).slice(0, 3).join(" · ") || "—")}</td><td><div class="capacity"><span class="capacity-label">处理中</span><span class="capacity-track"><span class="capacity-fill" style="width:${load}%"></span></span><span class="capacity-value">${capacity.processing_tasks ?? 0}</span></div></td></tr>`; }).join("")}</tbody></table>`;
    renderPagination("#nodes-pagination", "nodes", data, loadNodesPage);
  }

  function renderTasks(data) {
    const items = data.items || [];
    $("#task-result-count").textContent = `共 ${data.total || 0} 条 · 第 ${data.page || 1} 页`;
    if (!items.length) { $("#tasks-table").innerHTML = `<div class="empty-message">没有匹配的任务记录</div>`; renderPagination("#tasks-pagination", "tasks", data, loadTasksPage); return; }
    $("#tasks-table").classList.remove("empty-state");
    $("#tasks-table").innerHTML = `<table><thead><tr><th>任务</th><th>类型</th><th>状态</th><th>发布</th><th>进度</th><th>执行节点</th><th>创建时间</th><th>操作</th></tr></thead><tbody>${items.map((task) => { const retry = ["failed", "cancelled"].includes(String(task.status).toLowerCase()) && task.task_type !== "record.stream"; const callback = ["completed", "failed"].includes(String(task.status).toLowerCase()); return `<tr><td class="mono">${escapeHtml(task.task_id)}</td><td>${escapeHtml(task.task_type)}</td><td>${statusPill(task.status)}</td><td>${statusPill(task.publish_status)}</td><td>${Number(task.progress || 0).toFixed(0)}%</td><td class="mono">${escapeHtml(task.executor_node_id)}</td><td>${formatDate(task.created_at)}</td><td><div class="table-actions">${retry ? `<button class="table-action" data-action="retry" data-task-id="${escapeHtml(task.task_id)}">重试</button>` : ""}${callback ? `<button class="table-action" data-action="callback" data-task-id="${escapeHtml(task.task_id)}">补发回调</button>` : ""}${!retry && !callback ? "—" : ""}</div></td></tr>`; }).join("")}</tbody></table>`;
    renderPagination("#tasks-pagination", "tasks", data, loadTasksPage);
  }

  function renderBindings(data) {
    const bindings = data.items || [];
    $("#binding-result-count").textContent = `共 ${data.total || 0} 条绑定 · 第 ${data.page || 1} 页`;
    if (!bindings.length) { $("#bindings-table").innerHTML = `<div class="empty-message">没有匹配的绑定关系</div>`; renderPagination("#bindings-pagination", "bindings", data, loadBindingsPage); return; }
    $("#bindings-table").classList.remove("empty-state");
    $("#bindings-table").innerHTML = `<table><thead><tr><th>类型</th><th>空间</th><th>节点</th><th>流</th><th>模式</th><th>状态</th><th>版本</th><th>最近活跃</th></tr></thead><tbody>${bindings.map((item) => `<tr><td><strong>${escapeHtml(item.resource_type)}</strong></td><td>${escapeHtml(item.space_id)}</td><td><strong>${escapeHtml(item.node_code)}</strong><div class="mono">${escapeHtml(item.node_id)}</div></td><td><strong>${escapeHtml(item.stream_name)}</strong><div class="mono">${escapeHtml(item.app)} / ${escapeHtml(item.stream_id)}</div></td><td>${escapeHtml(item.stream_mode)}</td><td>${statusPill(item.status)}</td><td>v${item.version}</td><td>${formatDate(item.last_active_at)}</td></tr>`).join("")}</tbody></table>`;
    renderPagination("#bindings-pagination", "bindings", data, loadBindingsPage);
  }

  function renderPrompts(items) {
    state.promptVersions = items || [];
    $("#prompt-result-count").textContent = `共 ${state.promptVersions.length} 个版本`;
    if (!state.promptVersions.length) {
      state.selectedPrompt = null;
      state.promptDraftContent = null;
      state.selectedPromptStepCode = null;
      $("#prompts-table").innerHTML = `<div class="empty-message">暂无提示词版本</div>`;
      $("#prompt-editor-title").textContent = "选择版本查看";
      $("#prompt-editor-mode").textContent = "—";
      $("#prompt-editor-empty").hidden = false;
      $("#prompt-editor-fields").hidden = true;
      ["#prompt-new", "#prompt-save", "#prompt-publish"].forEach((selector) => { $(selector).disabled = true; });
      return;
    }
    $("#prompts-table").classList.remove("empty-state");
    $("#prompts-table").innerHTML = `<table><thead><tr><th>版本</th><th>状态</th><th>修改人</th><th>更新时间</th><th>操作</th></tr></thead><tbody>${state.promptVersions.map((item) => `<tr><td><strong>v${item.version}</strong><div class="mono">${escapeHtml(item.id)}</div></td><td>${statusPill(item.status)}</td><td>${escapeHtml(item.updated_by)}</td><td>${formatDate(item.updated_at)}</td><td><div class="table-actions"><button class="table-action" data-prompt-action="edit" data-prompt-id="${escapeHtml(item.id)}">查看${item.status === "DRAFT" ? " / 编辑" : ""}</button><button class="table-action" data-prompt-action="clone" data-prompt-id="${escapeHtml(item.id)}">复制草稿</button></div></td></tr>`).join("")}</tbody></table>`;
  }

  function assertPromptEditorDom() {
    const missing = promptEditorSelectors.filter((selector) => !$(selector));
    if (missing.length) {
      throw new Error("页面静态资源版本不一致，请按 Ctrl+F5 强制刷新后重试");
    }
  }

  function selectPrompt(item) {
    assertPromptEditorDom();
    state.selectedPrompt = item;
    state.promptDraftContent = JSON.parse(JSON.stringify(item.content || {}));
    const steps = Array.isArray(state.promptDraftContent.steps) ? state.promptDraftContent.steps : [];
    steps.sort((left, right) => Number(left.order || 0) - Number(right.order || 0));
    state.selectedPromptStepCode = steps[0]?.code || null;
    $("#prompt-editor-title").textContent = `v${item.version} · ${item.status}`;
    $("#prompt-editor-mode").textContent = item.status === "DRAFT" ? "草稿可编辑" : "只读版本";
    $("#prompt-editor-empty").hidden = true;
    $("#prompt-editor-fields").hidden = false;
    $("#prompt-subtitle").value = state.promptDraftContent.subtitle_edit_prompt || "";
    renderPromptStepTabs();
    renderPromptStep();
    setPromptEditorAccess(item.status === "DRAFT");
    $("#prompt-new").disabled = false;
    $("#prompt-save").disabled = item.status !== "DRAFT";
    $("#prompt-publish").textContent = item.status === "ARCHIVED" ? "重新发布此版本" : item.status === "DRAFT" ? "保存并发布" : "当前已发布";
    $("#prompt-publish").disabled = item.status === "PUBLISHED";
  }

  function setPromptEditorAccess(editable) {
    ["#prompt-subtitle", "#prompt-step-name", "#prompt-step-model", "#prompt-step-temperature", "#prompt-step-max-tokens", "#prompt-step-system", "#prompt-step-user"].forEach((selector) => { $(selector).readOnly = !editable; });
  }

  function promptSteps() {
    return Array.isArray(state.promptDraftContent?.steps) ? state.promptDraftContent.steps : [];
  }

  function currentPromptStep() {
    return promptSteps().find((step) => step.code === state.selectedPromptStepCode) || null;
  }

  function renderPromptStepTabs() {
    const steps = promptSteps();
    $("#prompt-step-tabs").innerHTML = steps.map((step) => `<button type="button" role="tab" class="prompt-step-tab ${step.code === state.selectedPromptStepCode ? "active" : ""}" data-prompt-step="${escapeHtml(step.code)}" aria-selected="${step.code === state.selectedPromptStepCode}"><span>${Number(step.order || 0)}</span><strong>${escapeHtml(step.name)}</strong></button>`).join("");
  }

  function renderPromptStep() {
    const step = currentPromptStep();
    if (!step) return;
    $("#prompt-step-name").value = step.name || "";
    $("#prompt-step-code").value = step.code || "";
    $("#prompt-step-model").value = step.model || "";
    $("#prompt-step-temperature").value = step.temperature ?? 0.5;
    $("#prompt-step-max-tokens").value = step.max_tokens ?? 32768;
    $("#prompt-step-system").value = step.system_prompt || "";
    $("#prompt-step-user").value = step.user_prompt || "";
    const metadata = Array.isArray(step.required_metadata) && step.required_metadata.length ? `必需元数据：${step.required_metadata.join("、")}` : "无必需元数据";
    $("#prompt-step-flags").innerHTML = `<span>步骤 ${Number(step.order || 0)} / 8</span><span>${escapeHtml(metadata)}</span>${step.critical ? "<span class=\"critical\">关键步骤，失败时终止任务</span>" : ""}`;
  }

  function syncPromptEditor() {
    if (!state.promptDraftContent) throw new Error("请先选择提示词版本");
    state.promptDraftContent.subtitle_edit_prompt = $("#prompt-subtitle").value;
    const step = currentPromptStep();
    if (step && state.selectedPrompt?.status === "DRAFT") {
      step.name = $("#prompt-step-name").value.trim();
      step.model = $("#prompt-step-model").value.trim();
      step.temperature = Number($("#prompt-step-temperature").value);
      step.max_tokens = Number($("#prompt-step-max-tokens").value);
      step.system_prompt = $("#prompt-step-system").value;
      step.user_prompt = $("#prompt-step-user").value;
    }
    return JSON.parse(JSON.stringify(state.promptDraftContent));
  }

  function formatDate(value) { return value ? new Date(value).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—"; }
  function formatDateTimeLocal(date, time) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}T${time}`;
  }

  function setDefaultTaskDateRange() {
    const today = new Date();
    const form = $("#task-filters");
    form.elements.created_from.value = formatDateTimeLocal(today, "00:00");
    form.elements.created_to.value = formatDateTimeLocal(today, "23:59");
  }

  function queryString(form) { const params = new URLSearchParams(); new FormData(form).forEach((value, key) => { if (value) params.set(key, value); }); return params.toString(); }

  async function loadOverview() { try { renderOverview(await request("/api/v1/admin/overview")); setSyncTime(); } catch (error) { showNotice(`总览读取失败：${error.message}`, true); } }
  async function loadNodes(silent = false) { return loadNodesPage(1, silent); }
  async function loadNodesPage(page = state.pages.nodes, silent = false) {
    state.pages.nodes = page;
    try {
      const data = await request(`/api/v1/admin/nodes/page?page=${page}&page_size=${state.pageSize}`);
      renderNodes(data);
      renderNodeHealth(data.items || []);
      if (!silent) setSyncTime();
    } catch (error) {
      if (!silent) showNotice(`节点读取失败：${error.message}`, true);
    }
  }
  async function loadTasks() { return loadTasksPage(1); }
  async function loadTasksPage(page = state.pages.tasks) {
    state.pages.tasks = page;
    const params = new URLSearchParams(queryString($("#task-filters")));
    params.set("page", page);
    params.set("page_size", state.pageSize);
    try { renderTasks(await request(`/api/v1/admin/tasks?${params.toString()}`)); setSyncTime(); } catch (error) { showNotice(`任务读取失败：${error.message}`, true); }
  }
  async function loadBindings() { return loadBindingsPage(1); }
  async function loadBindingsPage(page = state.pages.bindings) {
    state.pages.bindings = page;
    const params = new URLSearchParams(queryString($("#binding-filters")));
    params.set("page", page);
    params.set("page_size", state.pageSize);
    try { renderBindings(await request(`/api/v1/admin/bindings/page?${params.toString()}`)); setSyncTime(); } catch (error) { showNotice(`绑定读取失败：${error.message}`, true); }
  }

  async function loadPrompts() {
    const schoolCode = $("#prompt-filters").elements.school_code.value.trim() || "GLOBAL";
    try { const items = await request(`/api/v1/admin/content-prompts?school_code=${encodeURIComponent(schoolCode)}`); renderPrompts(items); if (items.length) selectPrompt(items[0]); setSyncTime(); } catch (error) { showNotice(`提示词读取失败：${error.message}`, true); }
  }

  function promptBody(content) {
    const form = $("#prompt-filters");
    return { school_code: form.elements.school_code.value.trim() || "GLOBAL", operator: form.elements.operator.value.trim() || "admin-console", content };
  }

  async function promptRequest(path, method, body) {
    return request(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  }

  async function promptAction(action, id) {
    const item = state.promptVersions.find((entry) => entry.id === id);
    try {
      if (action === "edit") { selectPrompt(item); return; }
      if (action === "clone") { const created = await promptRequest(`/api/v1/admin/content-prompts/${encodeURIComponent(id)}/clone`, "POST", { school_code: $("#prompt-filters").elements.school_code.value.trim() || "GLOBAL", operator: $("#prompt-filters").elements.operator.value.trim() || "admin-console" }); showNotice("已复制为新草稿"); await loadPrompts(); selectPrompt(created); }
    } catch (error) { showNotice(`提示词操作失败：${error.message}`, true); }
  }

  async function taskAction(action, taskId) { try { await request(`/api/v1/admin/tasks/${encodeURIComponent(taskId)}/${action === "retry" ? "retry" : "callback/retry"}`, { method: "POST" }); showNotice(action === "retry" ? "任务已重置为待发布" : "业务回调已重新发送"); loadTasks(); } catch (error) { showNotice(`操作失败：${error.message}`, true); } }

  $$(".nav-item").forEach((item) => item.addEventListener("click", () => go(item.dataset.view)));
  $$('[data-go="nodes"]').forEach((item) => item.addEventListener("click", () => go("nodes")));
  $("#refresh-all").addEventListener("click", () => go(state.view));
  $("#save-api-key").addEventListener("click", () => { sessionStorage.setItem("media-admin-api-key", apiKey.value.trim()); showNotice("API Key 已保存到当前浏览器会话"); go(state.view); });
  $("#task-filters").addEventListener("submit", (event) => { event.preventDefault(); state.pages.tasks = 1; loadTasks(); });
  $("#binding-filters").addEventListener("submit", (event) => { event.preventDefault(); state.pages.bindings = 1; loadBindings(); });
  $("#prompt-filters").addEventListener("submit", (event) => { event.preventDefault(); loadPrompts(); });
  $("#tasks-table").addEventListener("click", (event) => { const button = event.target.closest("button[data-action]"); if (button) taskAction(button.dataset.action, button.dataset.taskId); });
  $("#prompts-table").addEventListener("click", (event) => { const button = event.target.closest("button[data-prompt-action]"); if (button) promptAction(button.dataset.promptAction, button.dataset.promptId); });
  $("#prompt-step-tabs")?.addEventListener("click", (event) => { const button = event.target.closest("button[data-prompt-step]"); if (!button) return; syncPromptEditor(); state.selectedPromptStepCode = button.dataset.promptStep; renderPromptStepTabs(); renderPromptStep(); setPromptEditorAccess(state.selectedPrompt?.status === "DRAFT"); });
  $("#prompt-new").addEventListener("click", async () => { try { const content = syncPromptEditor(); const created = await promptRequest("/api/v1/admin/content-prompts/drafts", "POST", promptBody(content)); showNotice("已复制为新草稿，可分别修改八个步骤"); await loadPrompts(); selectPrompt(created); } catch (error) { showNotice(`新建草稿失败：${error.message}`, true); } });
  $("#prompt-save").addEventListener("click", async () => { if (!state.selectedPrompt || state.selectedPrompt.status !== "DRAFT") { showNotice("只有草稿版本允许保存", true); return; } try { const content = syncPromptEditor(); const saved = await promptRequest(`/api/v1/admin/content-prompts/${encodeURIComponent(state.selectedPrompt.id)}`, "PUT", { operator: $("#prompt-filters").elements.operator.value.trim() || "admin-console", content }); showNotice("八步提示词草稿已保存"); await loadPrompts(); selectPrompt(saved); } catch (error) { showNotice(`保存失败：${error.message}`, true); } });
  $("#prompt-publish").addEventListener("click", async () => { if (!state.selectedPrompt || state.selectedPrompt.status === "PUBLISHED") return; try { const operator = $("#prompt-filters").elements.operator.value.trim() || "admin-console"; if (state.selectedPrompt.status === "DRAFT") { const content = syncPromptEditor(); await promptRequest(`/api/v1/admin/content-prompts/${encodeURIComponent(state.selectedPrompt.id)}`, "PUT", { operator, content }); } await promptRequest(`/api/v1/admin/content-prompts/${encodeURIComponent(state.selectedPrompt.id)}/publish`, "POST", { operator }); showNotice(state.selectedPrompt.status === "ARCHIVED" ? "历史版本已重新发布，回滚完成" : "草稿已保存并发布"); loadPrompts(); } catch (error) { showNotice(`发布失败：${error.message}`, true); } });
  $("#overview-date").textContent = new Date().toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" });
  setDefaultTaskDateRange();
  loadOverview();
})();
