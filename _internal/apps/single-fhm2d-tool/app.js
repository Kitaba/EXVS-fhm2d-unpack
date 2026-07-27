const els = {
  sourcePath: document.querySelector("#sourcePath"),
  projectRoot: document.querySelector("#projectRoot"),
  extractOverwrite: document.querySelector("#extractOverwrite"),
  projectPath: document.querySelector("#projectPath"),
  projectName: document.querySelector("#projectName"),
  projectMeta: document.querySelector("#projectMeta"),
  outputPath: document.querySelector("#outputPath"),
  buildOverwrite: document.querySelector("#buildOverwrite"),
  taskTitle: document.querySelector("#taskTitle"),
  cancelButton: document.querySelector("#cancelButton"),
  progressBar: document.querySelector("#progressBar"),
  logOutput: document.querySelector("#logOutput"),
  resultText: document.querySelector("#resultText"),
  toast: document.querySelector("#toast"),
  textureList: document.querySelector("#textureList"),
  textureImage: document.querySelector("#textureImage"),
  textureTitle: document.querySelector("#textureTitle"),
  textureMeta: document.querySelector("#textureMeta"),
  textureFile: document.querySelector("#textureFile"),
  uploadTextureButton: document.querySelector("#uploadTextureButton"),
};

let statusData = null;
let projectData = null;
let pollTimer = null;
let textures = [];
let selectedTexture = null;

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

function render(data) {
  statusData = data;
  if (!els.projectRoot.value) els.projectRoot.value = data.default_project_root;
  els.taskTitle.textContent = data.label || "等待操作";
  els.resultText.textContent = data.message;
  els.cancelButton.disabled = !data.running;
  document.querySelectorAll(".primary").forEach(
    (button) => button.disabled = data.running,
  );
  els.progressBar.style.width = data.running ? "55%" : data.return_code === 0 ? "100%" : "0";
  if (data.log_lines.length) {
    els.logOutput.textContent = data.log_lines.join("\n");
    els.logOutput.scrollTop = els.logOutput.scrollHeight;
  }
}

async function poll() {
  try {
    const previous = statusData?.running;
    const data = await api("/api/status");
    render(data);
    if (previous && !data.running) {
      toast(data.return_code === 0 ? "任务已完成" : data.message, data.return_code !== 0);
      if (data.return_code === 0 && data.action === "extract") {
        els.projectPath.value = data.result_path;
        await inspectProject();
      }
    }
  } catch (error) {
    toast(error.message, true);
  } finally {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(poll, statusData?.running ? 800 : 2400);
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
    return data.path;
  } catch (error) {
    toast(error.message, true);
    return "";
  }
}

async function inspectProject() {
  if (!els.projectPath.value.trim()) return;
  try {
    projectData = await api("/api/project-info", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: els.projectPath.value }),
    });
    els.projectPath.value = projectData.project;
    els.projectName.textContent = projectData.source_name;
    els.projectMeta.textContent = `${projectData.texture_count} 张纹理`;
    els.outputPath.value = projectData.default_output;
    await loadTextures();
  } catch (error) {
    projectData = null;
    els.projectName.textContent = "工程不可用";
    els.projectMeta.textContent = error.message;
    toast(error.message, true);
  }
}

function textureUrl(item) {
  return `/api/texture?project=${encodeURIComponent(projectData.project)}` +
    `&texture_index=${item.texture_index}&t=${Date.now()}`;
}

async function loadTextures(preferredIndex = null) {
  const data = await api("/api/textures", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project: projectData.project }),
  });
  textures = data.textures;
  els.textureList.innerHTML = "";
  for (const item of textures) {
    const button = document.createElement("button");
    button.className = "texture-item";
    button.innerHTML = `
      <img src="${textureUrl(item)}" alt="">
      <span><strong>#${item.texture_index} · ${item.png_file}</strong>
      <span>${item.width}×${item.height} · 0x${item.format.toString(16).toUpperCase()}</span>
      ${item.modified ? '<span class="modified">已修改</span>' : ""}</span>`;
    button.addEventListener("click", () => selectTexture(item, button));
    els.textureList.appendChild(button);
    if (preferredIndex === item.texture_index) selectTexture(item, button);
  }
  if (!textures.length) {
    els.textureList.innerHTML = "<p>该工程没有可编辑纹理</p>";
  } else if (preferredIndex === null) {
    selectTexture(textures[0], els.textureList.firstElementChild);
  }
}

function selectTexture(item, button) {
  selectedTexture = item;
  document.querySelectorAll(".texture-item").forEach(
    element => element.classList.remove("active"),
  );
  button?.classList.add("active");
  els.textureImage.src = textureUrl(item);
  els.textureTitle.textContent = `纹理 #${item.texture_index} · ${item.png_file}`;
  els.textureMeta.textContent =
    `${item.width}×${item.height} · FHM2D 0x${item.format.toString(16).toUpperCase()}` +
    (item.modified ? " · 已修改" : " · 原始");
  els.uploadTextureButton.disabled = Boolean(statusData?.running);
}

async function uploadTexture(file) {
  if (!projectData || !selectedTexture) return;
  const index = selectedTexture.texture_index;
  els.uploadTextureButton.disabled = true;
  try {
    const result = await api(
      `/api/texture?project=${encodeURIComponent(projectData.project)}` +
      `&texture_index=${index}`,
      { method: "PUT", headers: { "Content-Type": "application/octet-stream" }, body: file },
    );
    const resized = result.resized
      ? `，已缩放并透明补边至 ${result.normalized_size.join("×")}`
      : "";
    const alpha = result.alpha_added ? "，已添加透明通道" : "";
    toast(`已导入 ${result.source_format}${resized}${alpha}`);
    await loadTextures(index);
  } catch (error) {
    toast(error.message, true);
  } finally {
    els.textureFile.value = "";
    els.uploadTextureButton.disabled = !selectedTexture || Boolean(statusData?.running);
  }
}

async function start(payload) {
  try {
    render(await api("/api/start", {
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

async function openFolder(path) {
  if (!path) return toast("目录尚未生成", true);
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

document.querySelector("#browseSource").addEventListener(
  "click", () => browse("fhm2d", els.sourcePath),
);
document.querySelector("#browseProjectRoot").addEventListener(
  "click", () => browse("folder", els.projectRoot),
);
document.querySelector("#browseProject").addEventListener("click", async () => {
  if (await browse("folder", els.projectPath)) await inspectProject();
});
els.projectPath.addEventListener("change", inspectProject);

document.querySelector("#extractButton").addEventListener("click", () => start({
  action: "extract",
  source: els.sourcePath.value,
  output_root: els.projectRoot.value,
  overwrite: els.extractOverwrite.checked,
}));

document.querySelector("#buildButton").addEventListener("click", () => start({
  action: "build",
  project: els.projectPath.value,
  output: els.outputPath.value,
  overwrite: els.buildOverwrite.checked,
}));

document.querySelector("#openPngButton").addEventListener(
  "click", () => openFolder(projectData?.png_directory),
);
document.querySelector("#openBuildButton").addEventListener("click", () => {
  const output = els.outputPath.value.trim();
  const split = Math.max(output.lastIndexOf("\\"), output.lastIndexOf("/"));
  openFolder(split > 0 ? output.slice(0, split) : "");
});

els.cancelButton.addEventListener("click", async () => {
  try {
    render(await api("/api/cancel", { method: "POST" }));
  } catch (error) {
    toast(error.message, true);
  }
});

document.querySelector("#copyLogButton").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText((statusData?.log_lines || []).join("\n"));
    toast("日志已复制");
  } catch {
    toast("浏览器未允许访问剪贴板", true);
  }
});

els.uploadTextureButton.addEventListener("click", () => els.textureFile.click());
els.textureFile.addEventListener("change", () => {
  if (els.textureFile.files[0]) uploadTexture(els.textureFile.files[0]);
});

poll();
