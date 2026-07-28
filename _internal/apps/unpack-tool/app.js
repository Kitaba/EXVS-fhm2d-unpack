const els = {
  sourceState: document.querySelector("#sourceState"),
  sourcePath: document.querySelector("#sourcePath"),
  packageCount: document.querySelector("#packageCount"),
  supportedCount: document.querySelector("#supportedCount"),
  textureCount: document.querySelector("#textureCount"),
  mappingCount: document.querySelector("#mappingCount"),
  freeSpace: document.querySelector("#freeSpace"),
  workspacePath: document.querySelector("#workspacePath"),
  taskTitle: document.querySelector("#taskTitle"),
  cancelButton: document.querySelector("#cancelButton"),
  progressBar: document.querySelector("#progressBar"),
  stageLabel: document.querySelector("#stageLabel"),
  timeLabel: document.querySelector("#timeLabel"),
  logOutput: document.querySelector("#logOutput"),
  resultLabel: document.querySelector("#resultLabel"),
  copyLogButton: document.querySelector("#copyLogButton"),
  toast: document.querySelector("#toast"),
};

let statusData = null;
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
  toast.timer = setTimeout(() => els.toast.classList.remove("visible"), 2600);
}

function elapsed(started, finished) {
  if (!started) return "-";
  const end = finished ? new Date(finished) : new Date();
  const seconds = Math.max(0, Math.floor((end - new Date(started)) / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return hours ? `${hours}时 ${minutes}分` : `${minutes}分 ${seconds % 60}秒`;
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
  els.freeSpace.textContent = `${data.free_gib} GiB`;
  els.workspacePath.textContent = data.workspace;
  els.workspacePath.title = data.workspace;
  els.taskTitle.textContent = data.label || "等待操作";
  els.cancelButton.disabled = !data.running;
  document.querySelectorAll("[data-action]").forEach(
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
    render(data);
  } catch (error) {
    toast(error.message, true);
  } finally {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(poll, statusData?.running ? 900 : 2500);
  }
}

async function start(action) {
  try {
    const data = await api("/api/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
    render(data);
    toast("任务已开始");
    poll();
  } catch (error) {
    toast(error.message, true);
  }
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-action]");
  if (button) start(button.dataset.action);
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

poll();
