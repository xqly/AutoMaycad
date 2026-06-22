const form = document.querySelector("#prompt-form");
const promptInput = document.querySelector("#prompt");
const submitButton = document.querySelector("#submit");
const formStatus = document.querySelector("#form-status");
const jobsView = document.querySelector("#jobs-view");
const jobView = document.querySelector("#job-view");
const jobsContainer = document.querySelector("#jobs");
const jobDetailContainer = document.querySelector("#job-detail");
const jobCount = document.querySelector("#job-count");
const jobTitle = document.querySelector("#job-title");
const refreshButton = document.querySelector("#refresh");
const toast = document.querySelector("#toast");

const activeStatuses = new Set(["queued", "running"]);
const statusLabels = {
  queued: "排队中",
  running: "运行中",
  succeeded: "已完成",
  failed: "失败",
};
const views = [form, jobsView, jobView];
let pollTimer = null;
let toastTimer = null;

function pathLabel(path) {
  if (!path) {
    return "";
  }

  return path.replaceAll("\\", "/");
}

function formatDate(value) {
  if (!value) {
    return "";
  }

  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

function downloadUrl(jobId, file) {
  const encodedFile = file.split("/").map(encodeURIComponent).join("/");
  return `/api/jobs/${encodeURIComponent(jobId)}/files/${encodedFile}`;
}

function downloadAllUrl(jobId) {
  return `/api/jobs/${encodeURIComponent(jobId)}/download-all`;
}

function displayFileName(file) {
  const lowerFile = file.toLowerCase();
  if (lowerFile.endsWith(".scene")) {
    return "场景文件";
  }
  if (lowerFile.endsWith("_spec.json")) {
    return "规格参数文件";
  }
  if (lowerFile.endsWith("_summary.json")) {
    return "摘要文件";
  }
  if (lowerFile.endsWith("_three_views.html")) {
    return "三视图页面";
  }
  if (lowerFile.endsWith(".json")) {
    return "数据文件";
  }
  if (lowerFile.endsWith(".html")) {
    return "页面文件";
  }
  if (lowerFile.endsWith(".log")) {
    return "日志文件";
  }
  return "生成文件";
}

function previewUrl(jobId, file) {
  const encodedFile = file.split("/").map(encodeURIComponent).join("/");
  return `/api/jobs/${encodeURIComponent(jobId)}/preview/${encodedFile}`;
}

function jobUrl(jobId) {
  return `/jobs/${encodeURIComponent(jobId)}`;
}

function statusLabel(status) {
  return statusLabels[status] || status;
}

function translatePreviewText(text) {
  return text
    .replaceAll("Convenience Store Aluminum Display Shelf", "便利店铝型材展示货架")
    .replaceAll("AutoMaycad Shelf Task", "AutoMaycad 货架任务")
    .replaceAll("three views", "三视图")
    .replaceAll("Three views", "三视图")
    .replaceAll("Finished size:", "成品尺寸：")
    .replaceAll("Shelves:", "层数：")
    .replaceAll("Nominal load:", "额定载荷：")
    .replaceAll("kg per shelf", "kg/层")
    .replaceAll("Front view", "正视图")
    .replaceAll("Top view", "俯视图")
    .replaceAll("Side view", "侧视图")
    .replaceAll("Assumptions", "建模假设")
    .replaceAll("finished_mm is treated as the finished outer shelf envelope", "finished_mm 按成品外包络尺寸处理")
    .replaceAll("4040 aluminum profile is used for posts, shelf frames, and bracing", "立柱、层框和支撑默认使用 4040 铝型材")
    .replaceAll("shelf boards are modeled as aluminum-colored 18 mm panel geometry", "层板按铝色 18 mm 板件几何建模")
    .replaceAll("shelf levels are adjustable in concept; exact hole pattern and hardware are not modeled", "层高按可调设计处理，具体孔位和五金未建模")
    .replaceAll("L ", "长 ")
    .replaceAll("D ", "深 ")
    .replaceAll("H ", "高 ");
}

function translatePreviewDocument(doc) {
  const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
  const textNodes = [];
  while (walker.nextNode()) {
    textNodes.push(walker.currentNode);
  }

  textNodes.forEach((node) => {
    node.nodeValue = translatePreviewText(node.nodeValue);
  });
}

function preparePreviewLayout(doc) {
  doc.querySelectorAll(".views .view").forEach((view, index) => {
    view.classList.add(["preview-view-front", "preview-view-top", "preview-view-side"][index] || "preview-view-extra");
  });
}

function getRoute() {
  const parts = window.location.pathname.split("/").filter(Boolean);
  if (parts[0] === "jobs" && parts[1]) {
    return { name: "job", jobId: decodeURIComponent(parts[1]) };
  }

  if (parts[0] === "jobs") {
    return { name: "jobs" };
  }

  return { name: "create" };
}

function showView(activeView) {
  views.forEach((view) => {
    view.hidden = view !== activeView;
  });
}

function setActiveNav() {
  const route = getRoute();
  document.querySelectorAll(".top-nav [data-route]").forEach((link) => {
    const isJobsLink = link.getAttribute("href") === "/jobs";
    const isCreateLink = link.getAttribute("href") === "/";
    link.removeAttribute("aria-current");

    if ((route.name === "create" && isCreateLink) || (route.name !== "create" && isJobsLink)) {
      link.setAttribute("aria-current", "page");
    }
  });
}

function setFormState(message, disabled = false) {
  formStatus.textContent = message;
  submitButton.disabled = disabled;
}

function showToast(message) {
  window.clearTimeout(toastTimer);
  toast.textContent = message;
  toast.hidden = false;
  toast.classList.add("toast-visible");

  toastTimer = window.setTimeout(() => {
    toast.classList.remove("toast-visible");
    toast.hidden = true;
  }, 2600);
}

function routeTo(path) {
  window.history.pushState({}, "", path);
  loadCurrentRoute();
}

function appendPaths(container, job) {
  const paths = document.createElement("dl");
  paths.className = "job-paths";
  const hasScene = job.generated_files?.some((file) => file.toLowerCase().endsWith(".scene"));
  [
    ["任务文件夹", "已保存"],
    ["场景文件", hasScene ? "已生成" : "等待生成"],
  ].forEach(([label, value]) => {
    const term = document.createElement("dt");
    term.textContent = label;
    const description = document.createElement("dd");
    description.textContent = value;
    paths.append(term, description);
  });
  container.append(paths);
}

function appendFiles(container, job) {
  if (!job.generated_files?.length) {
    return;
  }

  const files = document.createElement("section");
  files.className = "job-files";
  const filesTitle = document.createElement("h4");
  filesTitle.textContent = "生成文件";
  const list = document.createElement("ul");

  job.generated_files.forEach((file) => {
    const row = document.createElement("li");
    const link = document.createElement("a");
    link.href = downloadUrl(job.id, file);
    link.download = file.split("/").pop() || file;
    link.textContent = displayFileName(file);
    row.append(link);
    list.append(row);
  });

  files.append(filesTitle, list);
  container.append(files);
}

function threeViewFile(job) {
  return job.generated_files?.find((file) => file.toLowerCase().endsWith("_three_views.html"));
}

function sanitizePreviewDocument(doc) {
  doc.querySelectorAll("script, iframe, object, embed, link, meta, base, form, input, button").forEach((node) =>
    node.remove(),
  );

  doc.querySelectorAll("*").forEach((node) => {
    [...node.attributes].forEach((attribute) => {
      const name = attribute.name.toLowerCase();
      if (name.startsWith("on") || name === "srcdoc") {
        node.removeAttribute(attribute.name);
      }
    });
  });

  return doc;
}

async function loadThreeViewPreview(host, job, file) {
  try {
    const response = await fetch(previewUrl(job.id, file));
    if (!response.ok) {
      throw new Error("无法加载三视图预览。");
    }

    const html = await response.text();
    const doc = sanitizePreviewDocument(new DOMParser().parseFromString(html, "text/html"));
    translatePreviewDocument(doc);
    preparePreviewLayout(doc);
    const shadowRoot = host.attachShadow({ mode: "open" });

    const baseStyle = document.createElement("style");
    baseStyle.textContent = `
      :host { display: block; }
      * { box-sizing: border-box; }
      :host {
        color: #1f2937;
        font-family: Arial, sans-serif;
      }
      .sheet {
        max-width: none;
        margin: 0;
        border: 0;
        padding: 0;
        background: transparent;
      }
      svg {
        max-width: 100%;
      }
    `;
    shadowRoot.append(baseStyle);

    doc.querySelectorAll("style").forEach((style) => {
      shadowRoot.append(style.cloneNode(true));
    });

    const previewStyle = document.createElement("style");
    previewStyle.textContent = `
      .sheet {
        width: 100% !important;
        max-width: 720px !important;
        margin: 0 auto !important;
        border: 0 !important;
        padding: 0 !important;
        background: transparent !important;
      }
      h1 {
        margin: 0 0 8px !important;
        font-size: 18px !important;
        line-height: 1.25 !important;
      }
      .note {
        margin: 0 0 10px !important;
        font-size: 12px !important;
        line-height: 1.45 !important;
      }
      .views {
        display: grid !important;
        grid-template-columns: minmax(0, 1fr) minmax(0, 0.74fr) !important;
        gap: 12px 16px !important;
        align-items: start !important;
      }
      .view {
        min-width: 0 !important;
      }
      .preview-view-front {
        grid-column: 1 !important;
        grid-row: 1 / 3 !important;
      }
      .preview-view-top {
        grid-column: 2 !important;
        grid-row: 1 !important;
      }
      .preview-view-side {
        grid-column: 2 !important;
        grid-row: 2 !important;
      }
      .view h2 {
        margin: 0 0 6px !important;
        font-size: 13px !important;
      }
      .view svg {
        display: block !important;
        max-width: 100% !important;
        margin: 0 auto !important;
      }
      .preview-view-front svg {
        width: auto !important;
        height: 420px !important;
      }
      .preview-view-top svg {
        width: 100% !important;
        height: auto !important;
      }
      .preview-view-side svg {
        width: auto !important;
        height: 300px !important;
      }
      .sheet > h2 {
        margin: 14px 0 6px !important;
        font-size: 14px !important;
      }
      ul.note {
        padding-left: 18px !important;
      }
      @media (max-width: 720px) {
        .sheet {
          max-width: 520px !important;
        }
        .views {
          grid-template-columns: 1fr !important;
        }
        .preview-view-front,
        .preview-view-top,
        .preview-view-side {
          grid-column: auto !important;
          grid-row: auto !important;
        }
        .preview-view-front svg {
          height: 360px !important;
        }
        .preview-view-side svg {
          height: 300px !important;
        }
      }
    `;
    shadowRoot.append(previewStyle);

    const sheet = doc.querySelector(".sheet");
    if (sheet) {
      shadowRoot.append(sheet.cloneNode(true));
    } else {
      [...doc.body.children].forEach((child) => {
        if (child.tagName.toLowerCase() !== "style") {
          shadowRoot.append(child.cloneNode(true));
        }
      });
    }
  } catch (error) {
    host.textContent = error.message || "无法加载三视图预览。";
    host.classList.add("job-preview-error");
  }
}

function appendThreeViewPreview(container, job) {
  const file = threeViewFile(job);
  if (!file) {
    return;
  }

  const preview = document.createElement("section");
  preview.className = "job-preview";
  const previewTitle = document.createElement("h4");
  previewTitle.textContent = "三视图预览";
  const inlinePreview = document.createElement("div");
  inlinePreview.className = "job-preview-inline";
  inlinePreview.textContent = "正在加载三视图预览...";
  preview.append(previewTitle, inlinePreview);
  container.append(preview);
  loadThreeViewPreview(inlinePreview, job, file);
}

function appendOutput(container, job) {
  if (!job.error && !job.result) {
    return;
  }

  const output = document.createElement("section");
  output.className = "job-output";

  if (job.error) {
    const error = document.createElement("div");
    error.className = "job-error";
    error.textContent = job.error;
    output.append(error);
  }

  if (job.result) {
    const details = document.createElement("details");
    details.className = "job-output-details";
    const summary = document.createElement("summary");
    summary.textContent = "模型输出";
    const result = document.createElement("pre");
    result.textContent = job.result;
    details.append(summary, result);
    output.append(details);
  }

  container.append(output);
}

function createJobCard(job, { detail = false } = {}) {
  const item = document.createElement("article");
  item.className = detail ? "job job-detail-card" : "job";

  const body = document.createElement("div");
  const title = document.createElement("h3");

  if (detail) {
    title.textContent = `任务 ${job.id}`;
  } else {
    const link = document.createElement("a");
    link.href = jobUrl(job.id);
    link.dataset.route = "";
    link.textContent = `任务 ${job.id}`;
    title.append(link);
  }

  const preview = document.createElement("p");
  preview.textContent = job.prompt_preview || "（空需求）";

  const meta = document.createElement("div");
  meta.className = "job-meta";
  meta.textContent = [
    `创建 ${formatDate(job.created_at)}`,
    job.started_at ? `开始 ${formatDate(job.started_at)}` : "",
    job.finished_at ? `完成 ${formatDate(job.finished_at)}` : "",
  ]
    .filter(Boolean)
    .join(" | ");

  body.append(title, preview);
  appendPaths(body, job);
  body.append(meta);

  const status = document.createElement("span");
  status.className = `status status-${job.status}`;
  status.textContent = statusLabel(job.status);

  const cardActions = document.createElement("div");
  cardActions.className = "job-actions";
  const downloadAll = document.createElement("a");
  downloadAll.className = "download-all";
  downloadAll.href = downloadAllUrl(job.id);
  downloadAll.download = `${job.id}_files.zip`;
  downloadAll.textContent = "全部下载";
  cardActions.append(status, downloadAll);

  item.append(body, cardActions);
  if (detail) {
    appendThreeViewPreview(item, job);
  }
  appendFiles(item, job);
  appendOutput(item, job);

  return item;
}

function renderJobs(jobs) {
  showView(jobsView);
  setActiveNav();
  jobCount.textContent = String(jobs.length);

  if (jobs.length === 0) {
    jobsContainer.innerHTML = '<p class="empty">暂无任务。</p>';
    return;
  }

  jobsContainer.replaceChildren(...jobs.map((job) => createJobCard(job)));
}

function renderJob(job) {
  showView(jobView);
  setActiveNav();
  jobTitle.textContent = `任务 ${job.id}`;
  jobDetailContainer.replaceChildren(createJobCard(job, { detail: true }));
}

function renderJobError(message) {
  showView(jobView);
  setActiveNav();
  jobTitle.textContent = "任务";
  const empty = document.createElement("p");
  empty.className = "empty";
  empty.textContent = message;
  jobDetailContainer.replaceChildren(empty);
}

async function loadJobs() {
  const response = await fetch("/api/jobs");
  if (!response.ok) {
    throw new Error("无法加载任务列表。");
  }

  const jobs = await response.json();
  renderJobs(jobs);
  return jobs;
}

async function loadJob(jobId) {
  const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
  if (response.status === 404) {
    renderJobError("找不到该任务。");
    return null;
  }

  if (!response.ok) {
    throw new Error("无法加载任务。");
  }

  const job = await response.json();
  renderJob(job);
  return job;
}

async function loadCurrentRoute() {
  window.clearTimeout(pollTimer);
  const route = getRoute();

  try {
    if (route.name === "jobs") {
      const jobs = await loadJobs();
      const hasActiveJob = jobs.some((job) => activeStatuses.has(job.status));
      if (hasActiveJob) {
        schedulePoll(1500);
      }
      return;
    }

    if (route.name === "job") {
      const job = await loadJob(route.jobId);
      if (job && activeStatuses.has(job.status)) {
        schedulePoll(1500);
      }
      return;
    }

    showView(form);
    setActiveNav();
  } catch (error) {
    showToast(error.message || "无法加载页面。");
    schedulePoll(5000);
  }
}

function schedulePoll(delay) {
  window.clearTimeout(pollTimer);
  pollTimer = window.setTimeout(loadCurrentRoute, delay);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const prompt = promptInput.value.trim();
  if (!prompt) {
    setFormState("请先输入需求。");
    promptInput.focus();
    return;
  }

  setFormState("正在创建任务...", true);

  try {
    const response = await fetch("/api/jobs", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ prompt }),
    });

    if (!response.ok) {
      throw new Error("需求提交被拒绝。");
    }

    promptInput.value = "";
    const payload = await response.json();
    const detailPath = jobUrl(payload.job_id);
    setFormState(`任务 ${payload.job_id} 已创建。`);
    showToast(`任务 ${payload.job_id} 创建成功。`);
    window.setTimeout(() => routeTo(detailPath), 650);
  } catch (error) {
    setFormState(error.message || "提交失败。");
  } finally {
    submitButton.disabled = false;
  }
});

refreshButton.addEventListener("click", async () => {
  try {
    await loadCurrentRoute();
    showToast("已刷新。");
  } catch (error) {
    showToast(error.message || "刷新失败。");
  }
});

document.addEventListener("click", (event) => {
  if (!(event.target instanceof Element)) {
    return;
  }

  const link = event.target.closest("a[data-route]");
  if (!link || link.origin !== window.location.origin) {
    return;
  }

  event.preventDefault();
  routeTo(link.pathname);
});

window.addEventListener("popstate", loadCurrentRoute);

loadCurrentRoute();
