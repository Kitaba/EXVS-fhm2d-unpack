const state = {
  meta: null,
  category: "outgame_navigator",
  query: "",
  modifiedOnly: false,
  page: 1,
  pages: 1,
  selectedId: null,
  composition: null,
  selectedStates: {},
  targetTextureId: null,
  previewMode: "current",
  zoom: null,
  groupsAbort: null,
  patch: null,
};

const els = {
  mappingMeta: document.querySelector("#mappingMeta"),
  replacementStatus: document.querySelector("#replacementStatus"),
  categoryTabs: document.querySelector("#categoryTabs"),
  searchInput: document.querySelector("#searchInput"),
  modifiedOnly: document.querySelector("#modifiedOnly"),
  resultCount: document.querySelector("#resultCount"),
  gallery: document.querySelector("#gallery"),
  previousPage: document.querySelector("#previousPage"),
  nextPage: document.querySelector("#nextPage"),
  pageLabel: document.querySelector("#pageLabel"),
  refreshButton: document.querySelector("#refreshButton"),
  selectionCategory: document.querySelector("#selectionCategory"),
  selectionName: document.querySelector("#selectionName"),
  canvasViewport: document.querySelector("#canvasViewport"),
  emptyState: document.querySelector("#emptyState"),
  canvas: document.querySelector("#previewCanvas"),
  canvasInfo: document.querySelector("#canvasInfo"),
  renderInfo: document.querySelector("#renderInfo"),
  zoomOut: document.querySelector("#zoomOut"),
  zoomIn: document.querySelector("#zoomIn"),
  fitButton: document.querySelector("#fitButton"),
  zoomLabel: document.querySelector("#zoomLabel"),
  downloadButton: document.querySelector("#downloadButton"),
  modifiedBadge: document.querySelector("#modifiedBadge"),
  inspectorEmpty: document.querySelector("#inspectorEmpty"),
  inspectorContent: document.querySelector("#inspectorContent"),
  targetCard: document.querySelector("#targetCard"),
  fileInput: document.querySelector("#fileInput"),
  uploadButton: document.querySelector("#uploadButton"),
  restoreButton: document.querySelector("#restoreButton"),
  dropZone: document.querySelector("#dropZone"),
  uploadRule: document.querySelector("#uploadRule"),
  layerSections: document.querySelector("#layerSections"),
  toast: document.querySelector("#toast"),
  patchSummary: document.querySelector("#patchSummary"),
  patchMessage: document.querySelector("#patchMessage"),
  patchPackages: document.querySelector("#patchPackages"),
  patchLogButton: document.querySelector("#patchLogButton"),
  buildPatchButton: document.querySelector("#buildPatchButton"),
  deployPatchButton: document.querySelector("#deployPatchButton"),
  restoreBackupButton: document.querySelector("#restoreBackupButton"),
  patchDialog: document.querySelector("#patchDialog"),
  patchDialogTitle: document.querySelector("#patchDialogTitle"),
  patchDialogMessage: document.querySelector("#patchDialogMessage"),
  patchDialogLog: document.querySelector("#patchDialogLog"),
  patchDialogCancel: document.querySelector("#patchDialogCancel"),
  patchDialogConfirm: document.querySelector("#patchDialogConfirm"),
};

const roleLabels = {
  body: "人物主体",
  mouth: "嘴部",
  mouth_1: "嘴部 1",
  mouth_2: "嘴部 2",
  face_eyes: "眼睛与面部",
  expression: "表情",
};

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `请求失败 (${response.status})`);
  }
  return payload;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function showToast(message, error = false) {
  els.toast.textContent = message;
  els.toast.classList.toggle("error", error);
  els.toast.classList.add("visible");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(
    () => els.toast.classList.remove("visible"),
    2800,
  );
}

async function loadMeta() {
  state.meta = await api("/api/meta");
  els.mappingMeta.textContent =
    `${state.meta.group_count.toLocaleString()} 组 · ` +
    `${state.meta.layer_count.toLocaleString()} 个图层`;
  els.replacementStatus.textContent =
    `${state.meta.replacement_count} 张替换 · ` +
    `${state.meta.modified_group_count} 组已修改`;
  renderCategoryTabs();
}

function renderPatchStatus(data) {
  const previous = state.patch;
  state.patch = data;
  const count = data.replacement_count || 0;
  const packageCount = data.affected_package_count || 0;
  els.patchSummary.textContent = data.plan_error
    ? "补丁准备失败"
    : `${count} 张替换图 · ${packageCount} 个 FHM2D 包`;
  els.patchMessage.textContent = data.running
    ? `${data.label}：${data.message}`
    : ["deploying", "rollback_failed"].includes(
        data.latest_deployment?.status,
      )
      ? `检测到中断部署 · 请恢复 ${data.latest_deployment.package_count} 个包`
      : data.latest_deployment?.status === "deployed"
        ? `补丁已部署 · ${data.latest_deployment.package_count} 个包`
      : data.latest_build?.current
        ? `补丁已构建 · ${data.latest_build.package_count} 个包`
        : data.plan_error || data.message || "等待操作";
  els.patchPackages.textContent = data.affected_packages?.length
    ? data.affected_packages.join("、")
    : "没有待构建包";
  els.buildPatchButton.disabled = data.running || !data.can_build;
  els.deployPatchButton.disabled = data.running || !data.can_deploy;
  els.restoreBackupButton.disabled = data.running || !data.can_restore;
  els.patchLogButton.disabled = !data.log_lines?.length;
  els.uploadButton.disabled = Boolean(data.running);
  const target = targetLayer();
  els.restoreButton.disabled =
    Boolean(data.running) || !target?.replaced;

  if (previous?.running && !data.running) {
    showToast(
      data.error ? data.error : `${previous.label}完成`,
      Boolean(data.error),
    );
    loadMeta();
    loadGroups();
    if (state.selectedId) reloadComposition();
  }
}

async function loadPatchStatus() {
  try {
    renderPatchStatus(await api("/api/patch/status"));
  } catch (error) {
    els.patchSummary.textContent = "无法读取补丁状态";
    els.patchMessage.textContent = error.message;
  } finally {
    window.clearTimeout(loadPatchStatus.timer);
    loadPatchStatus.timer = window.setTimeout(
      loadPatchStatus,
      state.patch?.running ? 800 : 2500,
    );
  }
}

async function startPatchAction(action) {
  try {
    const result = await api(`/api/patch/${action}`, { method: "POST" });
    renderPatchStatus(result);
    showToast(`${result.label}已开始`);
  } catch (error) {
    showToast(error.message, true);
  }
}

function openPatchConfirmation(action) {
  const packageCount = state.patch?.affected_package_count || 0;
  const packages = state.patch?.affected_packages?.join("、") || "-";
  els.patchDialog.dataset.action = action;
  els.patchDialog.returnValue = "";
  els.patchDialogLog.hidden = true;
  els.patchDialogMessage.hidden = false;
  els.patchDialogCancel.hidden = false;
  els.patchDialogConfirm.hidden = false;
  if (action === "deploy") {
    els.patchDialogTitle.textContent = "备份并部署补丁";
    els.patchDialogMessage.textContent =
      `将关闭状态下的游戏资源替换为最新构建，并先建立可恢复备份。\n` +
      `涉及 ${packageCount} 个包：${packages}`;
    els.patchDialogConfirm.textContent = "备份并部署";
  } else {
    const deployed = state.patch?.latest_deployment;
    els.patchDialogTitle.textContent = "恢复最近备份";
    els.patchDialogMessage.textContent =
      `将恢复部署批次 ${deployed?.id || "-"} 中的原始游戏文件。\n` +
      `涉及 ${deployed?.package_count || 0} 个包。`;
    els.patchDialogConfirm.textContent = "恢复备份";
  }
  els.patchDialog.showModal();
}

function openPatchLog() {
  els.patchDialog.dataset.action = "";
  els.patchDialog.returnValue = "";
  els.patchDialogTitle.textContent = state.patch?.label || "补丁日志";
  els.patchDialogMessage.hidden = true;
  els.patchDialogLog.hidden = false;
  els.patchDialogLog.textContent =
    state.patch?.log_lines?.join("\n") || "暂无日志";
  els.patchDialogCancel.textContent = "关闭";
  els.patchDialogCancel.hidden = false;
  els.patchDialogConfirm.hidden = true;
  els.patchDialog.showModal();
}

function renderCategoryTabs() {
  const order = [
    "outgame_navigator",
    "ingame_navigator",
    "combat_portrait",
  ];
  els.categoryTabs.innerHTML = order.map((category) => `
    <button class="category-tab ${state.category === category ? "active" : ""}"
      data-category="${category}" role="tab">
      ${escapeHtml(state.meta.category_labels[category])}<br>
      ${state.meta.category_counts[category]}
    </button>
  `).join("");
}

async function loadGroups() {
  if (state.groupsAbort) state.groupsAbort.abort();
  state.groupsAbort = new AbortController();
  els.gallery.innerHTML = '<div class="gallery-message">正在读取立绘...</div>';
  const params = new URLSearchParams({
    category: state.category,
    q: state.query,
    modified: state.modifiedOnly ? "1" : "0",
    page: String(state.page),
    page_size: "48",
  });
  try {
    const data = await api(`/api/groups?${params}`, {
      signal: state.groupsAbort.signal,
    });
    state.pages = data.pages;
    els.resultCount.textContent = `${data.total} 组`;
    els.pageLabel.textContent = `${data.page} / ${data.pages}`;
    els.previousPage.disabled = data.page <= 1;
    els.nextPage.disabled = data.page >= data.pages;
    if (!data.items.length) {
      els.gallery.innerHTML =
        '<div class="gallery-message">没有符合条件的立绘</div>';
      return;
    }
    els.gallery.innerHTML = data.items.map((item) => `
      <button class="portrait-card ${item.id === state.selectedId ? "active" : ""}"
        data-id="${escapeHtml(item.id)}">
        <img class="portrait-thumb" src="${escapeHtml(item.preview_url)}"
          alt="" loading="lazy">
        ${item.modified ? '<span class="card-modified">已修改</span>' : ""}
        <span class="portrait-details">
          <span class="portrait-name">${escapeHtml(item.package)} / ${escapeHtml(item.group)}</span>
          <span class="portrait-meta">${item.canvas[0]}×${item.canvas[1]} · ${item.state_count} 状态</span>
        </span>
      </button>
    `).join("");
  } catch (error) {
    if (error.name !== "AbortError") {
      els.gallery.innerHTML =
        `<div class="gallery-message">${escapeHtml(error.message)}</div>`;
    }
  }
}

async function selectComposition(identifier) {
  try {
    const composition = await api(
      `/api/composition?id=${encodeURIComponent(identifier)}`,
    );
    state.selectedId = identifier;
    state.composition = composition;
    state.selectedStates = {};
    for (const family of composition.families) {
      const baseline = family.states.find(
        (item) => item.texture_id === family.baseline_texture_id,
      );
      state.selectedStates[family.family] =
        (baseline || family.states[0]).texture_id;
    }
    state.targetTextureId = composition.body.texture_id;
    state.zoom = null;
    els.selectionCategory.textContent = composition.category_label;
    els.selectionName.textContent =
      `${composition.package} / ${composition.group}`;
    els.canvasInfo.textContent =
      `画布 ${composition.canvas.width} × ${composition.canvas.height}`;
    els.renderInfo.textContent =
      `${composition.blend.replaceAll("_", " ")} · ${composition.families.length} 个表情族`;
    els.emptyState.hidden = true;
    els.canvas.hidden = false;
    els.downloadButton.disabled = false;
    els.inspectorEmpty.hidden = true;
    els.inspectorContent.hidden = false;
    renderInspector();
    updateModifiedBadge();
    await renderCanvas();
    loadGroups();
  } catch (error) {
    showToast(error.message, true);
  }
}

function allLayers() {
  if (!state.composition) return [];
  return [
    state.composition.body,
    ...state.composition.families.flatMap((family) => family.states),
  ];
}

function targetLayer() {
  return allLayers().find(
    (layer) => layer.texture_id === state.targetTextureId,
  );
}

function renderInspector() {
  const composition = state.composition;
  const body = composition.body;
  const bodyTargeted = body.texture_id === state.targetTextureId;
  let html = `
    <section class="layer-section">
      <div class="layer-header">
        <strong>人物主体</strong>
        <span>1 个图层</span>
      </div>
      <div class="body-row">
        <button class="body-button ${bodyTargeted ? "targeted" : ""}"
          data-target="${escapeHtml(body.texture_id)}">
          <span class="state-button" aria-hidden="true">
            <img class="state-thumb" src="${escapeHtml(body.current_url)}" alt="">
            ${body.replaced ? '<i class="replacement-mark"></i>' : ""}
          </span>
          <span>${body.width}×${body.height}<br>${body.replaced ? "使用替换图" : "使用原图"}</span>
        </button>
      </div>
    </section>
  `;
  for (const family of composition.families) {
    const selectedId = state.selectedStates[family.family];
    html += `
      <section class="layer-section">
        <div class="layer-header">
          <strong>${escapeHtml(roleLabels[family.role] || family.role)}</strong>
          <span>${family.states.length} 个状态 · ${family.dimensions[0]}×${family.dimensions[1]}</span>
        </div>
        <div class="state-grid">
          ${family.states.map((item) => `
            <button class="state-button
              ${selectedId === item.texture_id ? "selected" : ""}
              ${state.targetTextureId === item.texture_id ? "targeted" : ""}"
              data-family="${escapeHtml(family.family)}"
              data-state="${escapeHtml(item.texture_id)}"
              title="状态 ${item.state_index}">
              <img class="state-thumb" src="${escapeHtml(item.current_url)}" alt="">
              <span class="state-index">${item.state_index}</span>
              ${item.replaced ? '<i class="replacement-mark"></i>' : ""}
            </button>
          `).join("")}
        </div>
      </section>
    `;
  }
  els.layerSections.innerHTML = html;
  renderTarget();
}

function renderTarget() {
  const target = targetLayer();
  if (!target) return;
  els.targetCard.innerHTML = `
    <img class="target-thumb" src="${escapeHtml(target.current_url)}" alt="">
    <div>
      <div class="target-name">${escapeHtml(roleLabels[target.role] || target.role)}</div>
      <div class="target-meta">${target.width}×${target.height} · index ${target.embedded_index}</div>
      <div class="target-meta">${target.replaced ? "当前使用替换图" : "当前使用原图"}</div>
    </div>
  `;
  els.uploadRule.textContent =
    `要求：RGBA PNG，${target.width}×${target.height}，不自动缩放`;
  els.restoreButton.disabled =
    Boolean(state.patch?.running) || !target.replaced;
}

function updateModifiedBadge() {
  const modified = allLayers().some((layer) => layer.replaced);
  els.modifiedBadge.hidden = !modified;
}

function selectedRenderLayers() {
  const layers = [state.composition.body];
  for (const family of state.composition.families) {
    const selectedId = state.selectedStates[family.family];
    const selected = family.states.find(
      (item) => item.texture_id === selectedId,
    );
    if (selected) {
      layers.push({
        ...selected,
        anchor_x: family.anchor.x,
        anchor_y: family.anchor.y,
      });
    }
  }
  return layers;
}

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("无法读取预览图层"));
    image.src = `${url}${url.includes("?") ? "&" : "?"}t=${Date.now()}`;
  });
}

async function renderCanvas() {
  if (!state.composition) return;
  const token = Symbol();
  renderCanvas.token = token;
  const layers = selectedRenderLayers();
  try {
    const images = await Promise.all(layers.map((layer) =>
      loadImage(state.previewMode === "source" ? layer.source_url : layer.current_url)
    ));
    if (renderCanvas.token !== token) return;
    const { width, height } = state.composition.canvas;
    els.canvas.width = width;
    els.canvas.height = height;
    const context = els.canvas.getContext("2d");
    context.clearRect(0, 0, width, height);
    context.imageSmoothingEnabled = true;
    images.forEach((image, index) => {
      const layer = layers[index];
      context.drawImage(
        image,
        Number(layer.anchor_x || 0),
        Number(layer.anchor_y || 0),
      );
    });
    applyZoom();
  } catch (error) {
    showToast(error.message, true);
  }
}

function fitScale() {
  if (!state.composition) return 1;
  const availableWidth = Math.max(100, els.canvasViewport.clientWidth - 56);
  const availableHeight = Math.max(100, els.canvasViewport.clientHeight - 56);
  return Math.min(
    1,
    availableWidth / state.composition.canvas.width,
    availableHeight / state.composition.canvas.height,
  );
}

function applyZoom() {
  if (!state.composition) return;
  const scale = state.zoom ?? fitScale();
  els.canvas.style.width = `${Math.round(state.composition.canvas.width * scale)}px`;
  els.canvas.style.height = `${Math.round(state.composition.canvas.height * scale)}px`;
  els.zoomLabel.textContent = state.zoom === null
    ? "适合"
    : `${Math.round(scale * 100)}%`;
}

async function uploadReplacement(file) {
  const target = targetLayer();
  if (!target) return;
  if (file.type !== "image/png") {
    showToast("只接受 PNG 文件", true);
    return;
  }
  els.uploadButton.disabled = true;
  try {
    await api(
      `/api/replacement?texture_id=${encodeURIComponent(target.texture_id)}`,
      {
        method: "POST",
        headers: { "Content-Type": "image/png" },
        body: file,
      },
    );
    await reloadComposition();
    await loadMeta();
    await loadGroups();
    await loadPatchStatus();
    showToast(`已替换 ${roleLabels[target.role] || target.role}`);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    els.uploadButton.disabled = false;
    els.fileInput.value = "";
  }
}

async function restoreTarget() {
  const target = targetLayer();
  if (!target || !target.replaced) return;
  try {
    await api(
      `/api/replacement?texture_id=${encodeURIComponent(target.texture_id)}`,
      { method: "DELETE" },
    );
    await reloadComposition();
    await loadMeta();
    await loadGroups();
    await loadPatchStatus();
    showToast("已恢复原始图片");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function reloadComposition() {
  const selected = { ...state.selectedStates };
  const targetId = state.targetTextureId;
  state.composition = await api(
    `/api/composition?id=${encodeURIComponent(state.selectedId)}`,
  );
  state.selectedStates = selected;
  state.targetTextureId = targetId;
  renderInspector();
  updateModifiedBadge();
  await renderCanvas();
}

function downloadPreview() {
  if (!state.composition) return;
  const link = document.createElement("a");
  link.download =
    `${state.composition.package}_${state.composition.group}_preview.png`;
  link.href = els.canvas.toDataURL("image/png");
  link.click();
}

let searchTimer;
els.searchInput.addEventListener("input", () => {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => {
    state.query = els.searchInput.value;
    state.page = 1;
    loadGroups();
  }, 220);
});

els.categoryTabs.addEventListener("click", (event) => {
  const button = event.target.closest("[data-category]");
  if (!button) return;
  state.category = button.dataset.category;
  state.page = 1;
  renderCategoryTabs();
  loadGroups();
});

els.gallery.addEventListener("click", (event) => {
  const card = event.target.closest("[data-id]");
  if (card) selectComposition(card.dataset.id);
});

els.layerSections.addEventListener("click", (event) => {
  const body = event.target.closest("[data-target]");
  if (body) {
    state.targetTextureId = body.dataset.target;
    renderInspector();
    return;
  }
  const stateButton = event.target.closest("[data-state]");
  if (!stateButton) return;
  state.selectedStates[stateButton.dataset.family] = stateButton.dataset.state;
  state.targetTextureId = stateButton.dataset.state;
  renderInspector();
  renderCanvas();
});

els.modifiedOnly.addEventListener("change", () => {
  state.modifiedOnly = els.modifiedOnly.checked;
  state.page = 1;
  loadGroups();
});

els.previousPage.addEventListener("click", () => {
  if (state.page > 1) {
    state.page -= 1;
    loadGroups();
  }
});

els.nextPage.addEventListener("click", () => {
  if (state.page < state.pages) {
    state.page += 1;
    loadGroups();
  }
});

els.refreshButton.addEventListener("click", async () => {
  try {
    await api("/api/rescan", { method: "POST" });
    await loadMeta();
    await loadGroups();
    if (state.selectedId) await reloadComposition();
    showToast("替换目录已重新扫描");
  } catch (error) {
    showToast(error.message, true);
  }
});

document.querySelectorAll(".mode-button").forEach((button) => {
  button.addEventListener("click", () => {
    state.previewMode = button.dataset.mode;
    document.querySelectorAll(".mode-button").forEach((item) =>
      item.classList.toggle("active", item === button)
    );
    renderCanvas();
  });
});

els.zoomIn.addEventListener("click", () => {
  state.zoom = Math.min(2, (state.zoom ?? fitScale()) * 1.2);
  applyZoom();
});

els.zoomOut.addEventListener("click", () => {
  state.zoom = Math.max(0.12, (state.zoom ?? fitScale()) / 1.2);
  applyZoom();
});

els.fitButton.addEventListener("click", () => {
  state.zoom = null;
  applyZoom();
});

els.downloadButton.addEventListener("click", downloadPreview);
els.uploadButton.addEventListener("click", () => els.fileInput.click());
els.fileInput.addEventListener("change", () => {
  if (els.fileInput.files[0]) uploadReplacement(els.fileInput.files[0]);
});
els.restoreButton.addEventListener("click", restoreTarget);
els.buildPatchButton.addEventListener(
  "click", () => startPatchAction("build"),
);
els.deployPatchButton.addEventListener(
  "click", () => openPatchConfirmation("deploy"),
);
els.restoreBackupButton.addEventListener(
  "click", () => openPatchConfirmation("restore"),
);
els.patchLogButton.addEventListener("click", openPatchLog);
els.patchDialog.addEventListener("close", () => {
  const action = els.patchDialog.dataset.action;
  els.patchDialogCancel.textContent = "取消";
  if (els.patchDialog.returnValue === "confirm" && action) {
    startPatchAction(action);
  }
});

["dragenter", "dragover"].forEach((name) => {
  els.dropZone.addEventListener(name, (event) => {
    event.preventDefault();
    els.dropZone.classList.add("dragging");
  });
});

["dragleave", "drop"].forEach((name) => {
  els.dropZone.addEventListener(name, (event) => {
    event.preventDefault();
    els.dropZone.classList.remove("dragging");
  });
});

els.dropZone.addEventListener("drop", (event) => {
  if (event.dataTransfer.files[0]) {
    uploadReplacement(event.dataTransfer.files[0]);
  }
});

window.addEventListener("resize", () => {
  if (state.zoom === null) applyZoom();
});

async function start() {
  try {
    await loadMeta();
    await loadPatchStatus();
    await loadGroups();
    const initialId = new URLSearchParams(location.search).get("id");
    if (initialId) {
      const category = initialId.split("/")[0];
      if (state.meta.category_labels[category]) {
        state.category = category;
        renderCategoryTabs();
        await loadGroups();
      }
      await selectComposition(initialId);
    }
  } catch (error) {
    showToast(error.message, true);
    els.gallery.innerHTML =
      `<div class="gallery-message">${escapeHtml(error.message)}</div>`;
  }
}

start();
