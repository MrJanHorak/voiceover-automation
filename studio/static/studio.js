const byId = (id) => document.getElementById(id);
const script = byId("script");
const scriptLimit = Number(document.body.dataset.scriptLimit);
const chunkLimit = Number(document.body.dataset.chunkLimit);
const statusNames = { queued: "Queued", running: "Generating", ready: "Ready", failed: "Failed" };

let selected = null;
let state = { voice: null, jobs: [] };
let job = null;
let jobSignature = "";
let historySignature = "";
let voiceSignature = "";
let jobRequest = 0;
let initialLoad = true;
let polling = false;
let submitting = false;
const sectionDrafts = new Map();

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function errorAt(id, message = "") {
  const target = byId(id);
  target.textContent = message;
  target.hidden = !message;
}

async function request(url, options = {}) {
  const response = await fetch(url, options);
  if (response.status === 204) return null;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.detail || "The studio could not complete the request.");
    error.status = response.status;
    throw error;
  }
  return data;
}

function change(url, { method = "POST", value } = {}) {
  const headers = { "X-Studio-Request": "1" };
  let body = value;
  if (value !== undefined && !(value instanceof FormData)) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(value);
  }
  return request(url, { method, headers, body });
}

function updateEditor() {
  byId("script-count").textContent = `${script.value.length.toLocaleString()} / ${scriptLimit.toLocaleString()}`;
  byId("generate").disabled = submitting || !state.voice || !script.value.trim();
}

function renderVoice() {
  const signature = JSON.stringify([state.voice, state.reference_notice]);
  if (signature === voiceSignature) return;
  voiceSignature = signature;
  const voice = state.voice;
  const audio = byId("voice-audio");
  byId("voice-name").textContent = voice ? voice.label : "No voice reference yet";
  byId("voice-duration").textContent = voice ? `${voice.duration.toFixed(1)} seconds · WAV` : "Add a recording of your voice to get started.";
  byId("choose-voice").textContent = voice ? "Replace reference" : "Add reference WAV";
  audio.hidden = !voice;
  if (voice) {
    audio.src = `/api/voice/current/audio?v=${voice.id}`;
  } else if (audio.hasAttribute("src")) {
    audio.removeAttribute("src");
    audio.load();
  }
  errorAt("voice-error", state.reference_notice);
}

function badge(node, status) {
  node.dataset.status = status;
  node.textContent = statusNames[status];
}

function renderHistory() {
  const signature = JSON.stringify([selected, state.jobs]);
  if (historySignature === signature) return;
  historySignature = signature;
  byId("history-empty").hidden = state.jobs.length > 0;
  const items = state.jobs.map((entry) => {
    const row = element("li");
    const open = element("button", "button button-quiet history-select");
    open.type = "button";
    open.setAttribute("aria-current", String(entry.id === selected));
    open.addEventListener("click", () => selectJob(entry.id));
    const status = element("span", "status");
    badge(status, entry.status);
    open.append(element("span", "history-name", entry.title), status);
    row.append(open);
    const remove = element("button", "button button-quiet remove-job", "Delete");
    remove.type = "button";
    remove.setAttribute("aria-label", `Delete ${entry.title}`);
    remove.disabled = ["queued", "running"].includes(entry.status);
    remove.addEventListener("click", async () => {
      if (!window.confirm(`Delete “${entry.title}” and its audio files?`)) return;
      remove.disabled = true;
      try {
        await change(`/api/jobs/${entry.id}`, { method: "DELETE" });
        state.jobs = state.jobs.filter((saved) => saved.id !== entry.id);
        if (selected === entry.id) await selectJob(state.jobs[0]?.id ?? null);
        renderHistory();
        await refresh();
      } catch (error) {
        errorAt("result-error", error.message);
        remove.disabled = false;
      }
    });
    row.append(remove);
    return row;
  });
  byId("history").replaceChildren(...items);
}

function updateExport(entry) {
  const wrapper = byId("export");
  const audio = byId("result-audio");
  const version = entry?.export ? `${entry.id}/${entry.export}` : "";
  wrapper.hidden = !version;
  if ((audio.dataset.version || "") === version) return;
  audio.dataset.version = version;
  if (!version) {
    audio.removeAttribute("src");
    audio.load();
    return;
  }
  const route = `/api/jobs/${entry.id}/export`;
  audio.src = `${route}/wav?v=${entry.export}`;
  byId("download-wav").href = `${route}/wav?download=true`;
  byId("download-mp3").href = `${route}/mp3?download=true`;
}

function renderSections(entry) {
  const container = byId("sections");
  const expanded = new Set([...container.querySelectorAll("details[open]")].map((node) => node.dataset.key));
  byId("review").hidden = !entry;
  if (!entry) {
    container.replaceChildren();
    return;
  }
  const sections = entry.sections.map((section) => {
    const box = element("details", "section");
    const key = `${entry.id}-${section.number}`;
    box.dataset.key = key;
    box.open = expanded.has(key);
    const suffix = section.pending ? " · Pending" : "";
    box.append(element("summary", "", `Section ${section.number}${suffix}`));
    if (section.audio) {
      const audio = element("audio");
      audio.controls = true;
      audio.preload = "none";
      audio.setAttribute("aria-label", `Section ${section.number} audio`);
      audio.src = `/api/jobs/${entry.id}/sections/${section.number}/audio?v=${section.audio}`;
      box.append(audio);
    }
    const label = element("label", "", "Text to speak");
    label.htmlFor = `section-text-${section.number}`;
    const field = element("textarea");
    field.id = label.htmlFor;
    field.rows = 2;
    field.maxLength = chunkLimit;
    field.value = sectionDrafts.get(key) ?? section.text;
    field.readOnly = entry.status !== "ready";
    const redo = element("button", "button button-quiet", "Regenerate section");
    redo.type = "button";
    redo.disabled = field.readOnly || !field.value.trim();
    field.addEventListener("input", () => {
      sectionDrafts.set(key, field.value);
      redo.disabled = !field.value.trim();
    });
    redo.addEventListener("click", async () => {
      redo.disabled = true;
      errorAt("result-error");
      try {
        await change(`/api/jobs/${entry.id}/sections/${section.number}`, { value: { text: field.value } });
        sectionDrafts.delete(key);
        await refreshJob();
      } catch (error) {
        errorAt("result-error", error.message);
        redo.disabled = false;
      }
    });
    box.append(label, field, redo);
    return box;
  });
  container.replaceChildren(...sections);
}

function renderJob(entry) {
  const status = byId("result-status");
  const message = byId("result-message");
  status.hidden = !entry;
  byId("retry").hidden = entry?.status !== "failed";
  byId("retry").disabled = false;
  errorAt("result-error", entry?.error);
  if (!entry) {
    message.textContent = "Generate a voice-over to play and download it here.";
  } else {
    badge(status, entry.status);
    const remaining = entry.sections.filter((section) => section.pending).length;
    const next = entry.sections.find((section) => section.pending)?.number;
    const total = entry.sections.length;
    const phrases = {
      queued: `Waiting to generate · ${total - remaining} of ${total} sections complete`,
      running: next ? `Generating section ${next} of ${total}…` : "Preparing WAV and MP3…",
      ready: `${total} ${total === 1 ? "section" : "sections"} · ready to review`,
      failed: entry.export ? "Previous export available" : "Generation paused",
    };
    message.textContent = `${entry.title} · ${phrases[entry.status]}`;
  }
  updateExport(entry);
  renderSections(entry);
}

async function refreshJob() {
  const id = selected;
  if (!id) return;
  const version = ++jobRequest;
  try {
    const entry = await request(`/api/jobs/${id}`);
    if (version !== jobRequest || selected !== id) return;
    const signature = JSON.stringify(entry);
    if (signature === jobSignature) return;
    job = entry;
    jobSignature = signature;
    renderJob(entry);
  } catch (error) {
    if (version !== jobRequest || selected !== id) return;
    if (error.status === 404) {
      selected = null;
      job = null;
      jobSignature = "";
      renderJob(null);
    } else {
      errorAt("result-error", error.message);
    }
  }
}

async function selectJob(id) {
  if (selected !== id) {
    selected = id;
    job = null;
    jobSignature = "";
    renderJob(null);
    if (id) byId("result-message").textContent = "Loading recording…";
    renderHistory();
  }
  await refreshJob();
}

async function refresh() {
  if (polling) return;
  polling = true;
  try {
    state = await request("/api/state");
    renderVoice();
    const pending = state.jobs.filter((row) => ["queued", "running"].includes(row.status)).length;
    byId("queue-note").textContent = pending ? `${pending} ${pending === 1 ? "recording" : "recordings"} in progress` : "";
    updateEditor();
    if (initialLoad && !selected) await selectJob(state.jobs[0]?.id ?? null);
    else await refreshJob();
    initialLoad = false;
    renderHistory();
  } catch (error) {
    if (initialLoad) errorAt("script-error", "Could not reach the local studio. Check that it is running.");
  } finally {
    polling = false;
  }
}

script.addEventListener("input", updateEditor);
byId("script-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (submitting) return;
  submitting = true;
  updateEditor();
  errorAt("script-error");
  try {
    const created = await change("/api/jobs", { value: { title: byId("recording-title").value, script: script.value } });
    await selectJob(created.id);
    await refresh();
  } catch (error) {
    errorAt("script-error", error.message);
  } finally {
    submitting = false;
    updateEditor();
  }
});

byId("import-script").addEventListener("click", () => byId("script-file").click());
byId("script-file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file) return;
  errorAt("script-error");
  try {
    if (!file.name.toLowerCase().endsWith(".txt") || file.size > scriptLimit * 4 + 3) {
      throw new Error("Choose a UTF-8 .txt script within the character limit.");
    }
    let imported;
    try {
      imported = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer()).trim();
    } catch {
      throw new Error("This file could not be read as UTF-8 text.");
    }
    if (!imported || imported.length > scriptLimit) {
      throw new Error(`Use a script of 1 to ${scriptLimit.toLocaleString()} characters.`);
    }
    script.value = imported;
    byId("imported-file").textContent = file.name;
    if (!byId("recording-title").value.trim()) byId("recording-title").value = file.name.slice(0, -4).slice(0, 100);
    updateEditor();
    script.focus();
  } catch (error) {
    errorAt("script-error", error.message);
  }
});

byId("choose-voice").addEventListener("click", () => byId("voice-file").click());
byId("voice-file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file) return;
  const button = byId("choose-voice");
  button.disabled = true;
  errorAt("voice-error");
  try {
    if (file.size > 16 * 1024 * 1024) throw new Error("Choose a WAV file under 16 MB.");
    const data = new FormData();
    data.append("audio", file);
    const voice = await change("/api/voice", { value: data });
    state.voice = voice;
    state.reference_notice = null;
    renderVoice();
    updateEditor();
  } catch (error) {
    errorAt("voice-error", error.message);
  } finally {
    button.disabled = false;
  }
});

byId("retry").addEventListener("click", async (event) => {
  const id = selected;
  event.target.disabled = true;
  try {
    await change(`/api/jobs/${id}/retry`);
    await refreshJob();
  } catch (error) {
    errorAt("result-error", error.message);
    event.target.disabled = false;
  }
});

byId("reuse-script").addEventListener("click", () => {
  if (!job) return;
  byId("recording-title").value = job.title;
  byId("imported-file").textContent = "";
  script.value = job.script;
  updateEditor();
  script.focus();
});

updateEditor();
refresh();
window.setInterval(refresh, 1600);
