const loginView = document.querySelector("#login-form");
const usernameInput = document.querySelector("#username");
const passwordInput = document.querySelector("#password");
const loginButton = document.querySelector("#login-submit");
const loginStatus = document.querySelector("#login-status");
const accountPanel = document.querySelector("#account-panel");
const accountName = document.querySelector("#account-name");
const logoutButton = document.querySelector("#logout");
const topNav = document.querySelector(".top-nav");
const form = document.querySelector("#prompt-form");
const taskNameInput = document.querySelector("#task-name");
const promptInput = document.querySelector("#prompt");
const imageInput = document.querySelector("#images");
const imageList = document.querySelector("#image-list");
const submitButton = document.querySelector("#submit");
const formStatus = document.querySelector("#form-status");
const jobsView = document.querySelector("#jobs-view");
const jobView = document.querySelector("#job-view");
const accountView = document.querySelector("#account-view");
const passwordForm = document.querySelector("#password-form");
const currentPasswordInput = document.querySelector("#current-password");
const newPasswordInput = document.querySelector("#new-password");
const confirmPasswordInput = document.querySelector("#confirm-password");
const passwordButton = document.querySelector("#password-submit");
const passwordStatus = document.querySelector("#password-status");
const adminUsersPanel = document.querySelector("#admin-users-panel");
const userForm = document.querySelector("#user-form");
const newUsernameInput = document.querySelector("#new-username");
const newUserPasswordInput = document.querySelector("#new-user-password");
const userButton = document.querySelector("#user-submit");
const userStatus = document.querySelector("#user-status");
const usersRefreshButton = document.querySelector("#users-refresh");
const usersList = document.querySelector("#users-list");
const jobsContainer = document.querySelector("#jobs");
const jobDetailContainer = document.querySelector("#job-detail");
const jobCount = document.querySelector("#job-count");
const jobTitle = document.querySelector("#job-title");
const jobOwnerFilterPanel = document.querySelector("#job-owner-filter-panel");
const jobOwnerFilter = document.querySelector("#job-owner-filter");
const refreshButton = document.querySelector("#refresh");
const toast = document.querySelector("#toast");

const activeStatuses = new Set(["queued", "running"]);
const maxImageCount = 8;
const maxImageBytes = 15 * 1024 * 1024;
const allowedImageTypes = new Set(["image/gif", "image/jpeg", "image/png", "image/webp"]);
const statusLabels = {
  queued: "排队中",
  running: "运行中",
  succeeded: "已完成",
  failed: "失败",
};
const views = [loginView, form, jobsView, jobView, accountView];
let pollTimer = null;
let toastTimer = null;
let imagePreviewUrls = [];
let currentUser = null;
let notificationAudioContext = null;
let adminUsersCache = null;
const knownJobStatuses = new Map();
const notifiedJobIds = new Set();

class UnauthorizedError extends Error {}

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

function displayFileName(file) {
  const lowerFile = file.toLowerCase();
  if (lowerFile.endsWith(".scene")) {
    return "scene文件";
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
  if (lowerFile.startsWith("input_images/")) {
    return "输入图片";
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

function selectedJobOwner() {
  return new URLSearchParams(window.location.search).get("owner")?.trim() || "";
}

function jobsUrl(owner = "") {
  const params = new URLSearchParams();
  if (owner) {
    params.set("owner", owner);
  }

  const query = params.toString();
  return query ? `/jobs?${query}` : "/jobs";
}

function statusLabel(status) {
  return statusLabels[status] || status;
}

function displayModelText(value) {
  return String(value || "").replaceAll(/codex/gi, "画图大模型");
}

function jobDisplayName(job) {
  return job.display_name?.trim() || "未命名任务";
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

  if (parts[0] === "account") {
    return { name: "account" };
  }

  return { name: "create" };
}

function showView(activeView) {
  views.forEach((view) => {
    view.hidden = view !== activeView;
  });
}

function renderAccount() {
  const isAuthenticated = Boolean(currentUser);
  accountPanel.hidden = !isAuthenticated;
  topNav.hidden = !isAuthenticated;
  refreshButton.hidden = !isAuthenticated;
  accountName.textContent = isAuthenticated
    ? `${currentUser.username}${currentUser.is_admin ? " · 管理员" : ""}`
    : "";
}

function setLoginState(message, disabled = false) {
  loginStatus.textContent = displayModelText(message);
  loginButton.disabled = disabled;
}

function showLogin(message = "") {
  window.clearTimeout(pollTimer);
  currentUser = null;
  adminUsersCache = null;
  knownJobStatuses.clear();
  notifiedJobIds.clear();
  renderAccount();
  showView(loginView);
  setLoginState(message);
  passwordInput.focus();
}

function handleUnauthorized(response) {
  if (response.status !== 401) {
    return;
  }

  showLogin("请先登录。");
  throw new UnauthorizedError();
}

function setActiveNav() {
  const route = getRoute();
  document.querySelectorAll(".top-nav [data-route]").forEach((link) => {
    const isJobsLink = link.getAttribute("href") === "/jobs";
    const isCreateLink = link.getAttribute("href") === "/";
    const isAccountLink = link.getAttribute("href") === "/account";
    link.removeAttribute("aria-current");

    if (
      (route.name === "create" && isCreateLink) ||
      ((route.name === "jobs" || route.name === "job") && isJobsLink) ||
      (route.name === "account" && isAccountLink)
    ) {
      link.setAttribute("aria-current", "page");
    }
  });
}

function setFormState(message, disabled = false) {
  formStatus.textContent = displayModelText(message);
  submitButton.disabled = disabled;
}

function setPasswordState(message, disabled = false) {
  passwordStatus.textContent = displayModelText(message);
  passwordButton.disabled = disabled;
}

function setUserState(message, disabled = false) {
  userStatus.textContent = displayModelText(message);
  userButton.disabled = disabled;
}

function selectedImages() {
  return Array.from(imageInput.files || []);
}

function clearImagePreviewUrls() {
  imagePreviewUrls.forEach((url) => URL.revokeObjectURL(url));
  imagePreviewUrls = [];
}

function renderImageList() {
  clearImagePreviewUrls();
  const images = selectedImages();
  imageList.replaceChildren();

  images.forEach((image) => {
    const previewUrl = URL.createObjectURL(image);
    imagePreviewUrls.push(previewUrl);

    const item = document.createElement("div");
    item.className = "image-chip";

    const preview = document.createElement("img");
    preview.src = previewUrl;
    preview.alt = image.name;

    const name = document.createElement("span");
    name.textContent = image.name;

    item.append(preview, name);
    imageList.append(item);
  });
}

function validateImages(images) {
  if (images.length > maxImageCount) {
    return `最多只能上传 ${maxImageCount} 张图片。`;
  }

  const unsupported = images.find((image) => !allowedImageTypes.has(image.type));
  if (unsupported) {
    return `不支持的图片类型：${unsupported.name}`;
  }

  const oversized = images.find((image) => image.size > maxImageBytes);
  if (oversized) {
    return `单张图片不能超过 ${Math.floor(maxImageBytes / 1024 / 1024)} MB：${oversized.name}`;
  }

  return "";
}

function errorMessageFromResponse(response, fallback) {
  return response
    .json()
    .then((payload) => displayModelText(payload.detail || fallback))
    .catch(() => displayModelText(fallback));
}

function showToast(message, duration = 2600) {
  window.clearTimeout(toastTimer);
  toast.textContent = displayModelText(message);
  toast.hidden = false;
  toast.classList.add("toast-visible");

  toastTimer = window.setTimeout(() => {
    toast.classList.remove("toast-visible");
    toast.hidden = true;
  }, duration);
}

function requestNotificationPermission() {
  if (!("Notification" in window) || Notification.permission !== "default") {
    return;
  }

  const permissionRequest = Notification.requestPermission();
  permissionRequest?.catch?.(() => {});
}

function unlockNotificationSound() {
  if (!("AudioContext" in window || "webkitAudioContext" in window)) {
    return;
  }

  const AudioContextConstructor = window.AudioContext || window.webkitAudioContext;
  if (!notificationAudioContext) {
    notificationAudioContext = new AudioContextConstructor();
  }
  const resumeRequest = notificationAudioContext.resume();
  resumeRequest?.catch?.(() => {});
}

function playCompletionSound() {
  if (!("AudioContext" in window || "webkitAudioContext" in window)) {
    return;
  }

  const AudioContextConstructor = window.AudioContext || window.webkitAudioContext;
  const audioContext = notificationAudioContext || new AudioContextConstructor();
  notificationAudioContext = audioContext;

  Promise.resolve(audioContext.resume()).then(() => {
    const startTime = audioContext.currentTime;
    const gain = audioContext.createGain();
    gain.gain.setValueAtTime(0.0001, startTime);
    gain.gain.exponentialRampToValueAtTime(0.18, startTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, startTime + 0.55);
    gain.connect(audioContext.destination);

    [660, 880].forEach((frequency, index) => {
      const oscillator = audioContext.createOscillator();
      oscillator.type = "sine";
      oscillator.frequency.setValueAtTime(frequency, startTime + index * 0.18);
      oscillator.connect(gain);
      oscillator.start(startTime + index * 0.18);
      oscillator.stop(startTime + index * 0.18 + 0.24);
    });
  }).catch(() => {});
}

function showSystemNotification(title, body, path) {
  if (!("Notification" in window) || Notification.permission !== "granted") {
    return;
  }

  try {
    const notification = new Notification(title, {
      body,
      tag: title,
      renotify: true,
    });

    notification.addEventListener("click", () => {
      window.focus();
      routeTo(path);
      notification.close();
    });
    window.setTimeout(() => notification.close(), 8000);
  } catch (error) {
    console.warn("无法显示系统通知。", error);
  }
}

function notifyJobCompletion(job) {
  if (notifiedJobIds.has(job.id)) {
    return;
  }

  notifiedJobIds.add(job.id);
  const title = `${jobDisplayName(job)} 已完成`;
  const body = job.finished_at ? `完成时间 ${formatDate(job.finished_at)}` : "scene文件已生成。";
  playCompletionSound();
  showToast(title, 6000);
  showSystemNotification(title, body, jobUrl(job.id));
}

function observeJobStatuses(jobs) {
  jobs.forEach((job) => {
    const previousStatus = knownJobStatuses.get(job.id);
    if (previousStatus && activeStatuses.has(previousStatus) && job.status === "succeeded") {
      notifyJobCompletion(job);
    }

    knownJobStatuses.set(job.id, job.status);
  });
}

function routeTo(path) {
  window.history.pushState({}, "", path);
  loadCurrentRoute();
}

async function loadSession() {
  const response = await fetch("/api/session");
  if (response.status === 401) {
    showLogin();
    return false;
  }

  if (!response.ok) {
    throw new Error("无法读取登录状态。");
  }

  currentUser = await response.json();
  renderAccount();
  return true;
}

function appendPaths(container, job) {
  const paths = document.createElement("dl");
  paths.className = "job-paths";
  const hasScene = job.generated_files?.some((file) => file.toLowerCase().endsWith(".scene"));
  [
    ["任务文件夹", "已保存"],
    ["scene文件", hasScene ? "已生成" : "等待生成"],
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
  const sceneFiles = job.generated_files?.filter((file) => file.toLowerCase().endsWith(".scene")) || [];
  if (!sceneFiles.length) {
    return;
  }

  const files = document.createElement("section");
  files.className = "job-files";
  const filesTitle = document.createElement("h4");
  filesTitle.textContent = "生成文件";
  const list = document.createElement("ul");

  sceneFiles.forEach((file) => {
    const row = document.createElement("li");
    const link = document.createElement("a");
    link.href = downloadUrl(job.id, file);
    link.download = `${job.id}.scene`;
    link.textContent = displayFileName(file);
    row.append(link);
    list.append(row);
  });

  files.append(filesTitle, list);
  container.append(files);
}

function inputImageFiles(job) {
  return job.generated_files?.filter((file) => file.toLowerCase().startsWith("input_images/")) || [];
}

function appendInputImages(container, job) {
  const images = inputImageFiles(job);
  if (!images.length) {
    return;
  }

  const section = document.createElement("section");
  section.className = "job-images";

  const title = document.createElement("h4");
  title.textContent = "输入图片";

  const grid = document.createElement("div");
  grid.className = "job-image-grid";

  images.forEach((file) => {
    const link = document.createElement("a");
    link.className = "job-image-link";
    link.href = downloadUrl(job.id, file);
    link.target = "_blank";
    link.rel = "noreferrer";

    const image = document.createElement("img");
    image.src = downloadUrl(job.id, file);
    image.alt = file.split("/").pop() || "输入图片";

    link.append(image);
    grid.append(link);
  });

  section.append(title, grid);
  container.append(section);
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
    handleUnauthorized(response);
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
    if (error instanceof UnauthorizedError) {
      return;
    }

    host.textContent = displayModelText(error.message || "无法加载三视图预览。");
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
    error.textContent = displayModelText(job.error);
    output.append(error);
  }

  if (job.result) {
    const details = document.createElement("details");
    details.className = "job-output-details";
    const summary = document.createElement("summary");
    summary.textContent = "画图大模型输出";
    const result = document.createElement("pre");
    result.textContent = displayModelText(job.result);
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
  const displayName = jobDisplayName(job);

  if (detail) {
    title.textContent = displayName;
  } else {
    const link = document.createElement("a");
    link.href = jobUrl(job.id);
    link.dataset.route = "";
    link.textContent = displayName;
    title.append(link);
  }

  const preview = document.createElement("p");
  preview.textContent = job.prompt_preview || "（空需求）";

  const meta = document.createElement("div");
  meta.className = "job-meta";
  meta.textContent = [
    currentUser?.is_admin && job.owner ? `创建者 ${job.owner}` : "",
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
  cardActions.append(status);

  item.append(body, cardActions);
  if (detail) {
    appendInputImages(item, job);
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

function renderJobOwnerFilter(users = []) {
  const isAdmin = Boolean(currentUser?.is_admin);
  jobOwnerFilterPanel.hidden = !isAdmin;
  if (!isAdmin) {
    jobOwnerFilter.replaceChildren(new Option("全部账号", ""));
    return;
  }

  const selectedOwner = selectedJobOwner();
  const options = [new Option("全部账号", "")];
  users.forEach((user) => {
    options.push(new Option(user.username, user.username));
  });

  if (selectedOwner && !users.some((user) => user.username === selectedOwner)) {
    options.push(new Option(selectedOwner, selectedOwner));
  }

  jobOwnerFilter.replaceChildren(...options);
  jobOwnerFilter.value = selectedOwner;
}

function renderJob(job) {
  showView(jobView);
  setActiveNav();
  jobTitle.textContent = jobDisplayName(job);
  jobDetailContainer.replaceChildren(createJobCard(job, { detail: true }));
}

function renderJobError(message) {
  showView(jobView);
  setActiveNav();
  jobTitle.textContent = "任务";
  const empty = document.createElement("p");
  empty.className = "empty";
  empty.textContent = displayModelText(message);
  jobDetailContainer.replaceChildren(empty);
}

function renderUsers(users) {
  if (!users.length) {
    usersList.innerHTML = '<p class="empty">暂无账号。</p>';
    return;
  }

  usersList.replaceChildren(
    ...users.map((user) => {
      const item = document.createElement("article");
      item.className = "user-row";

      const name = document.createElement("strong");
      name.textContent = user.username;

      const role = document.createElement("span");
      role.className = user.is_admin ? "role role-admin" : "role";
      role.textContent = user.is_admin ? "管理员" : "普通账号";

      const created = document.createElement("span");
      created.className = "user-created";
      created.textContent = `创建 ${formatDate(user.created_at)}`;

      item.append(name, role, created);
      return item;
    }),
  );
}

async function fetchUsers({ force = false } = {}) {
  if (!currentUser?.is_admin) {
    return [];
  }
  if (adminUsersCache && !force) {
    return adminUsersCache;
  }

  const response = await fetch("/api/users");
  handleUnauthorized(response);
  if (response.status === 403) {
    adminUsersPanel.hidden = true;
    return [];
  }
  if (!response.ok) {
    throw new Error(await errorMessageFromResponse(response, "无法加载账号列表。"));
  }

  adminUsersCache = await response.json();
  return adminUsersCache;
}

async function loadUsers(options) {
  const users = await fetchUsers(options);
  renderUsers(users);
  return users;
}

async function renderAccountView() {
  showView(accountView);
  setActiveNav();
  setPasswordState("");
  adminUsersPanel.hidden = !currentUser?.is_admin;

  if (currentUser?.is_admin) {
    try {
      await loadUsers();
    } catch (error) {
      showToast(error.message || "无法加载账号列表。");
    }
  }
}

async function loadJobs() {
  const users = currentUser?.is_admin
    ? await fetchUsers().catch((error) => {
        if (error instanceof UnauthorizedError) {
          throw error;
        }

        showToast(error.message || "无法加载账号筛选。");
        return [];
      })
    : [];
  const owner = currentUser?.is_admin ? selectedJobOwner() : "";
  const response = await fetch(owner ? `/api/jobs?owner=${encodeURIComponent(owner)}` : "/api/jobs");
  handleUnauthorized(response);
  if (!response.ok) {
    throw new Error("无法加载任务列表。");
  }

  const jobs = await response.json();
  observeJobStatuses(jobs);
  renderJobOwnerFilter(users);
  renderJobs(jobs);
  return jobs;
}

async function loadJob(jobId) {
  const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
  handleUnauthorized(response);
  if (response.status === 404) {
    renderJobError("找不到该任务。");
    return null;
  }

  if (!response.ok) {
    throw new Error("无法加载任务。");
  }

  const job = await response.json();
  observeJobStatuses([job]);
  renderJob(job);
  return job;
}

async function loadCurrentRoute() {
  window.clearTimeout(pollTimer);
  if (!currentUser) {
    showLogin();
    return;
  }

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

    if (route.name === "account") {
      await renderAccountView();
      return;
    }

    showView(form);
    setActiveNav();
  } catch (error) {
    if (error instanceof UnauthorizedError) {
      return;
    }

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

  const taskName = taskNameInput.value.trim();
  const prompt = promptInput.value.trim();
  if (!taskName) {
    setFormState("请先输入任务名称。");
    taskNameInput.focus();
    return;
  }

  if (!prompt) {
    setFormState("请先输入需求。");
    promptInput.focus();
    return;
  }

  const images = selectedImages();
  const imageError = validateImages(images);
  if (imageError) {
    setFormState(imageError);
    imageInput.focus();
    return;
  }

  const formData = new FormData();
  formData.append("task_name", taskName);
  formData.append("prompt", prompt);
  images.forEach((image) => {
    formData.append("images", image, image.name);
  });

  setFormState("正在创建任务...", true);
  unlockNotificationSound();
  requestNotificationPermission();

  try {
    const response = await fetch("/api/jobs", {
      method: "POST",
      body: formData,
    });
    handleUnauthorized(response);

    if (!response.ok) {
      throw new Error(await errorMessageFromResponse(response, "需求提交被拒绝。"));
    }

    form.reset();
    renderImageList();
    const payload = await response.json();
    const detailPath = jobUrl(payload.job_id);
    knownJobStatuses.set(payload.job_id, payload.status);
    const displayName = payload.display_name || taskName;
    setFormState(`${displayName} 已创建。`);
    showToast(`${displayName} 创建成功。`);
    window.setTimeout(() => routeTo(detailPath), 650);
  } catch (error) {
    if (error instanceof UnauthorizedError) {
      return;
    }

    setFormState(error.message || "提交失败。");
  } finally {
    submitButton.disabled = false;
  }
});

loginView.addEventListener("submit", async (event) => {
  event.preventDefault();

  const username = usernameInput.value.trim();
  const password = passwordInput.value;
  if (!username || !password) {
    setLoginState("请输入账号和密码。");
    (username ? passwordInput : usernameInput).focus();
    return;
  }

  setLoginState("正在登录...", true);

  try {
    const response = await fetch("/api/login", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ username, password }),
    });

    if (!response.ok) {
      throw new Error(await errorMessageFromResponse(response, "登录失败。"));
    }

    currentUser = await response.json();
    passwordInput.value = "";
    renderAccount();
    showToast(`已登录：${currentUser.username}`);
    await loadCurrentRoute();
  } catch (error) {
    setLoginState(error.message || "登录失败。");
  } finally {
    loginButton.disabled = false;
  }
});

logoutButton.addEventListener("click", async () => {
  try {
    await fetch("/api/logout", { method: "POST" });
  } finally {
    currentUser = null;
    renderAccount();
    window.history.pushState({}, "", "/");
    showLogin("已退出登录。");
  }
});

passwordForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  const currentPassword = currentPasswordInput.value;
  const newPassword = newPasswordInput.value;
  const confirmPassword = confirmPasswordInput.value;
  if (!currentPassword || !newPassword || !confirmPassword) {
    setPasswordState("请完整填写密码。");
    return;
  }
  if (newPassword !== confirmPassword) {
    setPasswordState("两次输入的新密码不一致。");
    confirmPasswordInput.focus();
    return;
  }

  setPasswordState("正在保存...", true);

  try {
    const response = await fetch("/api/account/password", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
      }),
    });
    handleUnauthorized(response);

    if (!response.ok) {
      throw new Error(await errorMessageFromResponse(response, "密码修改失败。"));
    }

    passwordForm.reset();
    setPasswordState("密码已修改。");
    showToast("密码已修改。");
  } catch (error) {
    if (error instanceof UnauthorizedError) {
      return;
    }
    setPasswordState(error.message || "密码修改失败。");
  } finally {
    passwordButton.disabled = false;
  }
});

userForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  const username = newUsernameInput.value.trim();
  const password = newUserPasswordInput.value;
  if (!username || !password) {
    setUserState("请填写账号和初始密码。");
    (username ? newUserPasswordInput : newUsernameInput).focus();
    return;
  }

  setUserState("正在添加账号...", true);

  try {
    const response = await fetch("/api/users", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ username, password }),
    });
    handleUnauthorized(response);

    if (!response.ok) {
      throw new Error(await errorMessageFromResponse(response, "账号添加失败。"));
    }

    userForm.reset();
    setUserState("账号已添加。");
    showToast(`账号 ${username} 已添加。`);
    await loadUsers({ force: true });
  } catch (error) {
    if (error instanceof UnauthorizedError) {
      return;
    }
    setUserState(error.message || "账号添加失败。");
  } finally {
    userButton.disabled = false;
  }
});

usersRefreshButton.addEventListener("click", async () => {
  try {
    await loadUsers({ force: true });
    showToast("账号列表已刷新。");
  } catch (error) {
    showToast(error.message || "刷新失败。");
  }
});

jobOwnerFilter.addEventListener("change", () => {
  routeTo(jobsUrl(jobOwnerFilter.value));
});

imageInput.addEventListener("change", () => {
  renderImageList();
  const imageError = validateImages(selectedImages());
  setFormState(imageError);
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
  routeTo(`${link.pathname}${link.search}`);
});

window.addEventListener("popstate", loadCurrentRoute);
window.addEventListener("beforeunload", clearImagePreviewUrls);

async function initializeApp() {
  renderAccount();

  try {
    if (await loadSession()) {
      await loadCurrentRoute();
    }
  } catch (error) {
    showLogin("无法读取登录状态，请重新登录。");
    showToast(error.message || "初始化失败。");
  }
}

initializeApp();
