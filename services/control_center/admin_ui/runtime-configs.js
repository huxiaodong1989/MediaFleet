(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const stepNames = { default: "具体模型", CLASSROOM_SUMMARY: "课堂摘要", FREQUENCY_ANALYSIS: "词频与语速分析", KEYEVENT_ANALYSIS: "关键事件分析", CONTENT_SUMMARY: "课堂总体评价与特征提炼", QA_ANALYSIS: "课堂问答类型与频次统计", TEACHING_METHOD: "讲授方式占比统计", KNOWLEDGE_GRAPH: "课堂知识图谱构建", CLASSROOM_SCORE: "课堂评分" };
  const escape = (value) => String(value ?? "—").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  let catalog = [], data = null, pending = null, sourceVersion = null, epoch = 0;
  const path = () => `/api/v1/admin/runtime-configs/${encodeURIComponent($("config-service").value)}/${encodeURIComponent($("config-category").value)}`;
  const requestId = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), (v) => v.toString(16).padStart(2, "0")).join("");
  const feedback = (message, error = false) => { $("config-feedback").textContent = message; $("config-feedback").classList.toggle("config-error", error); };
  $("config-admin-key").value = sessionStorage.getItem("media-config-admin-key") || "";

  async function api(url, method = "GET", body) {
    const response = await fetch(url, { method, headers: { "X-Config-Admin-Key": $("config-admin-key").value.trim(), "Content-Type": "application/json" }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    const payload = await response.json();
    if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "输入格式无效，请检查必填字段");
    return payload;
  }

  function invalidate() { pending = null; $("config-review").hidden = true; }
  function schema() { return catalog.find((entry) => entry.service === $("config-service").value && entry.category === $("config-category").value)?.schema; }

  function renderEditor(version = null, currentModels = false) {
    invalidate();
    const fields = schema()?.properties || {};
    const content = version?.content || {};
    $("config-fields").innerHTML = Object.entries(fields).filter(([name]) => name !== "schema_version").map(([name, field]) => {
      const value = content[name] ?? field.default ?? "";
      const control = field.type === "array"
        ? `<textarea id="config-field-${escape(name)}" data-config-field="${escape(name)}" rows="3" required>${escape(Array.isArray(value) ? value.join("\n") : value)}</textarea><small>每行一个模型名称或部署 ID</small>`
        : `<input id="config-field-${escape(name)}" data-config-field="${escape(name)}" type="${field.type === "string" ? (field.format === "uri" ? "url" : "text") : "number"}" value="${escape(value)}" ${field.minimum !== undefined ? `min="${field.minimum}"` : ""} ${field.maximum !== undefined ? `max="${field.maximum}"` : ""} step="${field.type === "integer" ? "1" : "any"}" required />`;
      return `<label><span>${escape(field.title || name)}</span>${control}</label>`;
    }).join("");
    const model = $("config-category").value === "model_connection";
    $("config-secret-fields").hidden = !model;
    $("config-new-key").value = "";
    $("config-key-action").value = version?.has_api_key ? "keep" : "replace";
    $("config-key-label").hidden = version?.has_api_key || !model;
    $("config-new-key").required = model && !version?.has_api_key;
    $("config-key-status").textContent = version?.has_api_key ? "密钥已配置，明文不会返回浏览器。" : "尚未配置密钥。";
    const effect = model ? "新建任务使用发布后的连接；已排队、执行中与重试任务继续锁定版本。" : "以下均为每实例额度。调低后等待在途任务完成，多个实例的额度需要合计核算。";
    $("config-effect").textContent = !version && !data.current_id ? `尚未导入配置，当前表单显示默认值，不代表实例实际参数。请先导入部署配置。${effect}` : effect;
    const previousModels = new Map((currentModels ? [] : version?.prompt_updates || []).map((p) => [p.school_code, p.models]));
    $("config-models").innerHTML = model ? (data.prompts || []).map((prompt, index) => {
      const values = previousModels.get(prompt.school_code) || prompt.models;
      return `<fieldset class="config-model-group"><legend>${escape(prompt.school_code)} · 模型选择</legend>${Object.keys(prompt.models).length > 1 ? `<div class="config-bulk"><input aria-label="${escape(prompt.school_code)}统一模型" data-bulk-value="${index}" placeholder="统一模型名称" /><button class="table-action" type="button" data-bulk="${index}">应用到全部步骤</button></div>` : ""}${Object.entries(prompt.models).map(([code, value]) => `<label><span>${escape(stepNames[code] || code)}</span><input data-prompt-index="${index}" data-step="${escape(code)}" value="${escape(values[code] || value)}" maxlength="128" required /></label>`).join("")}</fieldset>`;
    }).join("") : "";
    $("config-reason").value = version?.status === "DRAFT" ? version.reason : "";
  }

  function renderStatus(current) {
    $("config-current-version").textContent = current.current_id ? `当前发布修订 ${current.revision}` : "尚未发布配置";
    $("config-instance-note").textContent = current.current_id ? "配置通常 5～10 秒内加载；心跳展示可能延迟约 30 秒。离线实例不计为已应用。" : "首次接入请先导入现有部署配置；未发布配置时，刷新不会自动完成初始化。";
    const category = $("config-category").value;
    $("config-instances").innerHTML = current.instances.length ? current.instances.map((node) => {
      const state = node.runtime_config?.[category] || {};
      let label, tone = "warn";
      if (node.status !== "ONLINE") label = "离线";
      else if (state.error) { label = `应用失败：${state.error}`; tone = "bad"; }
      else if (!current.current_id) { label = "尚未导入配置，沿用部署参数"; tone = ""; }
      else if (node.runtime_config_supported === false) label = "尚未接入热更新";
      else if (state.applied_id === current.current_id) { label = "已应用"; tone = "good"; }
      else label = "等待应用";
      const revision = state.revision ? `实际修订 ${escape(state.revision)} · ${escape(state.applied_at)}` : "尚无已应用的配置版本";
      const stages = Object.entries(node.stages || {}).map(([name, value]) => `${name} ${value.active} / ${value.limit}${value.active > value.limit ? "（收缩中）" : ""}`).join(" · ");
      return `<div class="config-instance"><strong>${escape(node.node_code)}</strong><span class="status-pill ${tone}">${escape(label)}</span><small>${revision}</small><small>${escape(stages)}</small></div>`;
    }).join("") : '<p class="empty-message">暂无该服务实例心跳。</p>';
  }

  function renderHistory() {
    $("config-history").innerHTML = data.versions.length ? data.versions.map((v) => `<div class="config-history-row"><div><strong>${v.status === "DRAFT" ? "草稿" : `修订 ${escape(v.revision)}`}${v.id === data.current_id ? " · 当前" : ""}</strong><p>${escape(v.reason)}</p><small>${escape(v.created_at)} · ${escape(v.operator)}</small></div><button type="button" class="table-action" data-version="${escape(v.id)}">${v.status === "DRAFT" ? "查看草稿" : "以此版本编辑"}</button></div>`).join("") : '<p class="empty-message">尚未导入配置。请先导入当前部署参数，建立首版配置。</p>';
  }

  async function load() {
    if (!$("config-admin-key").value.trim()) return;
    const token = ++epoch;
    try {
      feedback("正在读取配置…");
      catalog = await api("/api/v1/admin/runtime-configs");
      if (token !== epoch) return;
      const selectedService = $("config-service").value, selectedCategory = $("config-category").value;
      $("config-service").innerHTML = [...new Map(catalog.map((entry) => [entry.service, entry])).values()].map((entry) => `<option value="${escape(entry.service)}">${escape(entry.service_name)}</option>`).join("");
      if (catalog.some((entry) => entry.service === selectedService)) $("config-service").value = selectedService;
      const categories = catalog.filter((entry) => entry.service === $("config-service").value);
      $("config-category").innerHTML = categories.map((entry) => `<option value="${escape(entry.category)}">${escape(entry.category_name)}</option>`).join("");
      if (categories.some((entry) => entry.category === selectedCategory)) $("config-category").value = selectedCategory;
      const loaded = await api(path());
      if (token !== epoch) return;
      data = loaded; sourceVersion = null;
      $("runtime-config-workspace").hidden = false;
      renderEditor(data.current, true); renderHistory(); renderStatus(data);
      feedback("配置已加载。编辑并保存草稿后可查看变更。" );
    } catch (error) { feedback(error.message, true); }
  }

  async function refreshStatus() {
    if (!data) return;
    const requestedPath = path();
    try { const fresh = await api(requestedPath); if (requestedPath === path()) renderStatus(fresh); }
    catch (error) { feedback(error.message, true); }
  }

  function review(version) {
    pending = version;
    const before = data.current?.content || {};
    const changes = Object.entries(version.content).filter(([key, value]) => key !== "schema_version" && JSON.stringify(before[key]) !== JSON.stringify(value));
    const model = $("config-category").value === "model_connection";
    const modelDetails = model ? `<p>模型设置：${escape(version.prompt_updates.map((p) => `${p.school_code}: ${[...new Set(Object.values(p.models))].join(", ")}`).join("；") || "无模型调整")}</p><p>密钥：${version.has_api_key ? "已配置（不回显）" : "未配置"}</p>` : "";
    $("config-diff").innerHTML = `<ul>${changes.map(([key, value]) => `<li>${escape(schema().properties[key]?.title || key)}：${escape(JSON.stringify(before[key] ?? "未配置"))} → ${escape(JSON.stringify(value))}</li>`).join("")}</ul>${modelDetails}<p>原因：${escape(version.reason)}</p>`;
    $("config-review-note").textContent = model ? "字段校验通过不代表模型协议或网络已验证。发布前请确认目标模型支持当前业务输入。" : "发布后调整各实例额度，正在执行的工作会自然完成。";
    $("config-review").hidden = false;
  }

  $("runtime-config-access").addEventListener("submit", (event) => { event.preventDefault(); sessionStorage.setItem("media-config-admin-key", $("config-admin-key").value.trim()); load(); });
  for (const id of ["config-service", "config-category"]) $(id).addEventListener("change", () => { data = null; invalidate(); load(); });
  $("runtime-config-form").addEventListener("input", invalidate);
  $("config-key-action").addEventListener("change", () => { const replace = $("config-key-action").value === "replace"; $("config-key-label").hidden = !replace; $("config-new-key").required = replace; invalidate(); });
  $("config-models").addEventListener("click", (event) => {
    const button = event.target.closest("[data-bulk]"); if (!button) return;
    const value = $("config-models").querySelector(`[data-bulk-value="${button.dataset.bulk}"]`).value.trim();
    if (!value) return;
    $("config-models").querySelectorAll(`[data-prompt-index="${button.dataset.bulk}"]`).forEach((input) => { input.value = value; }); invalidate();
  });
  $("runtime-config-form").addEventListener("submit", async (event) => {
    event.preventDefault(); if (!data) return;
    const editEpoch = epoch;
    $("config-save").disabled = true;
    try {
      const content = { schema_version: 1 };
      for (const input of document.querySelectorAll("[data-config-field]")) {
        const name = input.dataset.configField, type = schema().properties[name].type;
        content[name] = type === "array" ? input.value.split(/[\n,]/).map((v) => v.trim()).filter(Boolean) : type === "string" ? input.value.trim() : Number(input.value);
      }
      const prompt_updates = $("config-category").value === "model_connection" ? data.prompts.map((p, index) => {
        const models = {}; document.querySelectorAll(`[data-prompt-index="${index}"]`).forEach((input) => { models[input.dataset.step] = input.value.trim(); });
        return { ...p, models };
      }) : [];
      const body = { content, expected_revision: data.revision, request_id: requestId(), reason: $("config-reason").value.trim(), prompt_updates, source_version_id: sourceVersion };
      if ($("config-category").value === "model_connection" && $("config-key-action").value === "replace") body.api_key = $("config-new-key").value;
      const saved = await api(`${path()}/drafts`, "POST", body);
      if (editEpoch !== epoch) return;
      sourceVersion = saved.id;
      $("config-new-key").value = ""; $("config-new-key").required = false;
      $("config-key-action").value = "keep"; $("config-key-label").hidden = true;
      review(saved); feedback("草稿已保存。请核对变更后发布。" );
    } catch (error) { feedback(error.message, true); } finally { $("config-save").disabled = false; }
  });
  $("config-publish").addEventListener("click", async () => {
    if (!pending) return;
    $("config-publish").disabled = true;
    try { const model = $("config-category").value === "model_connection"; await api(`${path()}/${encodeURIComponent(pending.id)}/publish`, "POST"); await load(); feedback(model ? "配置已发布，等待在线实例应用。已有任务保持原配置。" : "配置已发布，等待在线实例调整额度。正在执行的工作会自然完成。"); }
    catch (error) { feedback(error.message, true); } finally { $("config-publish").disabled = false; }
  });
  $("config-history").addEventListener("click", (event) => {
    const button = event.target.closest("[data-version]"); if (!button || !data) return;
    const version = data.versions.find((v) => v.id === button.dataset.version);
    sourceVersion = version.id;
    renderEditor(version);
    if (version.status === "DRAFT") review(version);
    else { $("config-reason").value = `基于修订 ${version.revision} 恢复配置`; feedback("已载入历史值。保存草稿并发布会形成新修订，历史任务保持原配置。"); }
  });
  $("config-refresh-status").addEventListener("click", refreshStatus);
  window.setInterval(() => { if ($("view-runtime-configs").classList.contains("active-view")) refreshStatus(); }, 10000);
  window.runtimeConfigPage = { load };
})();
