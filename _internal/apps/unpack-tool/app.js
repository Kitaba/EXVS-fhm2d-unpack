const els = {
  sourceState: document.querySelector("#sourceState"),
  sourcePath: document.querySelector("#sourcePath"),
  packageCount: document.querySelector("#packageCount"),
  supportedCount: document.querySelector("#supportedCount"),
  textureCount: document.querySelector("#textureCount"),
  mappingCount: document.querySelector("#mappingCount"),
  freeSpace: document.querySelector("#freeSpace"),
  workspacePath: document.querySelector("#workspacePath"),
  databaseState: document.querySelector("#databaseState"),
  taskTitle: document.querySelector("#taskTitle"),
  cancelButton: document.querySelector("#cancelButton"),
  progressBar: document.querySelector("#progressBar"),
  stageLabel: document.querySelector("#stageLabel"),
  timeLabel: document.querySelector("#timeLabel"),
  logOutput: document.querySelector("#logOutput"),
  resultLabel: document.querySelector("#resultLabel"),
  copyLogButton: document.querySelector("#copyLogButton"),
  toast: document.querySelector("#toast"),
  librarySummary: document.querySelector("#librarySummary"),
  libraryOperations: document.querySelector("#libraryOperations"),
  singleOperations: document.querySelector("#singleOperations"),
  singleSource: document.querySelector("#singleSource"),
  singleOutputRoot: document.querySelector("#singleOutputRoot"),
  extractOverwrite: document.querySelector("#extractOverwrite"),
  packageChoices: document.querySelector("#packageChoices"),
  singleProject: document.querySelector("#singleProject"),
  projectChoices: document.querySelector("#projectChoices"),
  projectMeta: document.querySelector("#projectMeta"),
  singleBuildOutput: document.querySelector("#singleBuildOutput"),
  buildOverwrite: document.querySelector("#buildOverwrite"),
};

let statusData = null;
let projects = [];
let pollTimer = null;

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `请求失败 (${response.status})`);
  return payload;
}

function toast(message, error = false) {
  els.toast.textContent = message;
  els.toast.classList.toggle("error", error);
  els.toast.classList.add("visible");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => els.toast.classList.remove("visible"), 3000);
}

function elapsed(started, finished) {
  if (!started) return "-";
  const end = finished ? new Date(finished) : new Date();
  const seconds = Math.max(0, Math.floor((end - new Date(started)) / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return hours ? `${hours}时 ${minutes}分` : `${minutes}分 ${seconds % 60}秒`;
}

function selectedProject() {
  const path = els.singleProject.value.trim();
  return projects.find((item) => item.path === path);
}

function updateProjectFields() {
  const project = selectedProject();
  if (!project) {
    els.projectMeta.textContent = els.singleProject.value.trim()
      ? "自定义工程目录"
      : "尚未选择工程";
    return;
  }
  els.projectMeta.textContent =
    `${project.texture_count.toLocaleString()} 张纹理 · ${project.source_name}`;
  els.singleBuildOutput.value = project.default_output;
}

function render(data) {
  statusData = data;
  els.sourceState.textContent = data.source_exists ? "已找到" : "未找到";
  els.sourceState.style.color = data.source_exists ? "var(--good)" : "var(--bad)";
  els.sourcePath.textContent = data.source_root;
  els.sourcePath.title = data.source_root;
  els.packageCount.textContent = data.package_count.toLocaleString();
  els.supportedCount.textContent = `${data.supported_package_count.toLocaleString()} 个纹理包`;
  els.textureCount.textContent = data.texture_count.toLocaleString();
  els.mappingCount.textContent = data.mapped_group_count.toLocaleString();
  const database = data.prebuilt_databases?.find(
    (item) => item.game_version.toLowerCase() === "vsac29",
  ) || data.prebuilt_databases?.[0];
  els.databaseState.textContent = database
    ? `${database.game_version.toUpperCase()} · ${database.group_count.toLocaleString()} 组人物 · ${database.layer_count.toLocaleString()} 图层`
    : "未检测到内置映射数据库";
  els.freeSpace.textContent = `${data.free_gib} GiB`;
  els.workspacePath.textContent = data.workspace;
  els.workspacePath.title = data.workspace;
  if (!els.singleOutputRoot.value) els.singleOutputRoot.value = data.single_project_root;
  els.taskTitle.textContent = data.label || "等待操作";
  els.cancelButton.disabled = !data.running;
  document.querySelectorAll("[data-action], .command-primary").forEach(
    (button) => button.disabled = data.running,
  );

  const progress = data.stage_count
    ? ((data.stage_index - (data.running ? 1 : 0)) / data.stage_count) * 100
    : 0;
  els.progressBar.style.width = `${Math.max(0, Math.min(100, progress))}%`;
  els.stageLabel.textContent = data.stage
    ? `阶段 ${data.stage_index}/${data.stage_count} · ${data.stage}`
    : "尚未开始";
  els.timeLabel.textContent = elapsed(data.started_at, data.finished_at);
  els.resultLabel.textContent = data.message || "工作区已就绪";

  if (data.log_lines.length) {
    const nearBottom =
      els.logOutput.scrollHeight - els.logOutput.scrollTop - els.logOutput.clientHeight < 50;
    els.logOutput.textContent = data.log_lines.join("\n");
    if (nearBottom || data.running) els.logOutput.scrollTop = els.logOutput.scrollHeight;
  }
}

async function poll() {
  try {
    const data = await api("/api/status");
    const wasRunning = statusData?.running;
    render(data);
    if (wasRunning && !data.running && data.return_code === 0) {
      await loadProjects(false);
      toast("任务已完成");
    }
  } catch (error) {
    toast(error.message, true);
  } finally {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(poll, statusData?.running ? 900 : 2500);
  }
}

async function start(action) {
  try {
    render(await api("/api/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    }));
    toast("任务已开始");
    poll();
  } catch (error) {
    toast(error.message, true);
  }
}

async function startSingle(endpoint, payload) {
  try {
    render(await api(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }));
    toast("任务已开始");
    poll();
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadPackages(showToast = true) {
  try {
    const data = await api("/api/single/packages");
    els.packageChoices.replaceChildren(...data.packages.map((item) => {
      const option = document.createElement("option");
      option.value = item.path;
      option.label = item.name;
      return option;
    }));
    if (showToast) toast(`已读取 ${data.packages.length} 个候选包`);
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadProjects(showToast = true) {
  try {
    const previous = els.singleProject.value;
    const data = await api("/api/single/projects");
    projects = data.projects;
    els.projectChoices.replaceChildren(...projects.map((item) => {
      const option = document.createElement("option");
      option.value = item.path;
      option.label = `${item.name} · ${item.texture_count} 张`;
      return option;
    }));
    if (previous) els.singleProject.value = previous;
    else if (projects.length) els.singleProject.value = projects.at(-1).path;
    updateProjectFields();
    if (showToast) toast(`已读取 ${projects.length} 个单包工程`);
  } catch (error) {
    toast(error.message, true);
  }
}

async function openFolder(path) {
  if (!path) return toast("没有可打开的目录", true);
  try {
    await api("/api/open-folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
  } catch (error) {
    toast(error.message, true);
  }
}

async function browse(kind, target) {
  try {
    const data = await api("/api/browse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind }),
    });
    if (data.path) target.value = data.path;
  } catch (error) {
    toast(error.message, true);
  }
}

document.addEventListener("click", (event) => {
  const actionButton = event.target.closest("[data-action]");
  if (actionButton) start(actionButton.dataset.action);

  const viewButton = event.target.closest("[data-view]");
  if (viewButton) {
    document.querySelectorAll("[data-view]").forEach(
      (button) => button.classList.toggle("active", button === viewButton),
    );
    const single = viewButton.dataset.view === "single";
    els.librarySummary.classList.toggle("hidden", single);
    els.singleOperations.classList.toggle("hidden", !single);
    els.libraryOperations.classList.toggle("hidden", single);
  }
});

document.querySelector("#refreshPackages").addEventListener("click", () => loadPackages());
document.querySelector("#refreshProjects").addEventListener("click", () => loadProjects());
document.querySelector("#browseSource").addEventListener(
  "click", () => browse("fhm2d", els.singleSource),
);
document.querySelector("#browseOutputRoot").addEventListener(
  "click", () => browse("folder", els.singleOutputRoot),
);
document.querySelector("#browseProject").addEventListener(
  "click", async () => {
    await browse("folder", els.singleProject);
    updateProjectFields();
  },
);
els.singleProject.addEventListener("change", updateProjectFields);
els.singleProject.addEventListener("input", updateProjectFields);

document.querySelector("#extractSingle").addEventListener("click", () => {
  startSingle("/api/single/extract", {
    source: els.singleSource.value,
    output_root: els.singleOutputRoot.value,
    overwrite: els.extractOverwrite.checked,
  });
});

document.querySelector("#buildSingle").addEventListener("click", () => {
  startSingle("/api/single/build", {
    project: els.singleProject.value,
    output: els.singleBuildOutput.value,
    overwrite: els.buildOverwrite.checked,
  });
});

document.querySelector("#openPngDirectory").addEventListener("click", () => {
  const project = selectedProject();
  openFolder(project?.png_directory || `${els.singleProject.value}\\png_edit`);
});

document.querySelector("#openBuildDirectory").addEventListener("click", () => {
  const output = els.singleBuildOutput.value.trim();
  const separator = Math.max(output.lastIndexOf("\\"), output.lastIndexOf("/"));
  openFolder(separator > 0 ? output.slice(0, separator) : "");
});

els.cancelButton.addEventListener("click", async () => {
  try {
    render(await api("/api/cancel", { method: "POST" }));
    toast("已请求中止任务");
  } catch (error) {
    toast(error.message, true);
  }
});

els.copyLogButton.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText((statusData?.log_lines || []).join("\n"));
    toast("日志已复制");
  } catch {
    toast("浏览器未允许访问剪贴板", true);
  }
});

Promise.all([loadPackages(false), loadProjects(false)]);
poll();
