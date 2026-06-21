const form = document.querySelector("#prompt-form");
const promptInput = document.querySelector("#prompt");
const submitButton = document.querySelector("#submit");
const formStatus = document.querySelector("#form-status");
const jobsContainer = document.querySelector("#jobs");
const jobCount = document.querySelector("#job-count");
const refreshButton = document.querySelector("#refresh");

const activeStatuses = new Set(["queued", "running"]);

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

function setFormState(message, disabled = false) {
  formStatus.textContent = message;
  submitButton.disabled = disabled;
}

function renderJobs(jobs) {
  jobCount.textContent = String(jobs.length);

  if (jobs.length === 0) {
    jobsContainer.innerHTML = '<p class="empty">No jobs yet.</p>';
    return;
  }

  jobsContainer.replaceChildren(
    ...jobs.map((job) => {
      const item = document.createElement("article");
      item.className = "job";

      const body = document.createElement("div");
      const preview = document.createElement("p");
      preview.textContent = job.prompt_preview || "(empty prompt)";

      const meta = document.createElement("div");
      meta.className = "job-meta";
      meta.textContent = [
        `created ${formatDate(job.created_at)}`,
        job.started_at ? `started ${formatDate(job.started_at)}` : "",
        job.finished_at ? `finished ${formatDate(job.finished_at)}` : "",
      ]
        .filter(Boolean)
        .join(" | ");

      body.append(preview, meta);

      const status = document.createElement("span");
      status.className = `status status-${job.status}`;
      status.textContent = job.status;

      item.append(body, status);

      if (job.error || job.result) {
        const output = document.createElement("section");
        output.className = "job-output";

        if (job.error) {
          const error = document.createElement("div");
          error.className = "job-error";
          error.textContent = job.error;
          output.append(error);
        }

        if (job.result) {
          const result = document.createElement("pre");
          result.textContent = job.result;
          output.append(result);
        }

        item.append(output);
      }

      return item;
    }),
  );
}

async function loadJobs() {
  const response = await fetch("/api/jobs");
  if (!response.ok) {
    throw new Error("Could not load jobs.");
  }

  const jobs = await response.json();
  renderJobs(jobs);
  return jobs;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const prompt = promptInput.value.trim();
  if (!prompt) {
    setFormState("Enter a prompt first.");
    promptInput.focus();
    return;
  }

  setFormState("Submitting...", true);

  try {
    const response = await fetch("/api/jobs", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ prompt }),
    });

    if (!response.ok) {
      throw new Error("Prompt was rejected.");
    }

    promptInput.value = "";
    setFormState("Submitted.");
    await loadJobs();
  } catch (error) {
    setFormState(error.message || "Submit failed.");
  } finally {
    submitButton.disabled = false;
  }
});

refreshButton.addEventListener("click", async () => {
  try {
    await loadJobs();
    setFormState("Refreshed.");
  } catch (error) {
    setFormState(error.message || "Refresh failed.");
  }
});

async function poll() {
  try {
    const jobs = await loadJobs();
    const hasActiveJob = jobs.some((job) => activeStatuses.has(job.status));
    window.setTimeout(poll, hasActiveJob ? 1500 : 5000);
  } catch {
    window.setTimeout(poll, 5000);
  }
}

poll();
