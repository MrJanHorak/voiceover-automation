const byId = (id) => document.getElementById(id);
const script = byId("script");
const scriptLimit = Number(document.body.dataset.scriptLimit);
const chunkLimit = Number(document.body.dataset.chunkLimit);
const maxPause = Number(document.body.dataset.maxPause);
const directedAvailable = document.body.dataset.directedAvailable === "true";
const statusNames = { queued: "Queued", running: "Generating", ready: "Ready", failed: "Failed" };

let selected = null;
let state = { voice: null, voices: [], jobs: [] };
let job = null;
let jobSignature = "";
let historySignature = "";
let voiceSignature = "";
let jobRequest = 0;
let initialLoad = true;
let polling = false;
let submitting = false;
let referenceBusy = false;
let voiceRevision = 0;
let prepared = null;
const sectionDrafts = new Map();
const pauseDrafts = new Map();
const instructionDrafts = new Map();

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
  const directed = Boolean(scriptDirections.field.value.trim());
  scriptSounds.disabled = directed;
  const ready = prepared ? prepared.every((part) => part.text.trim() && (!part.instructions.trim() || directedAvailable))
    : Boolean(script.value.trim()) && (!directed || directedAvailable);
  byId("generate").disabled = submitting || referenceBusy || !state.voice || !ready;
}

function cuePicker(field, errorId) {
  const picker = byId("delivery-cues").content.firstElementChild.cloneNode(true);
  picker.disabled = field.readOnly;
  picker.addEventListener("change", () => {
    const cue = picker.value;
    picker.value = "";
    if (!cue) return;
    const position = field.selectionStart ?? field.value.length;
    const prefix = position && !/\s/.test(field.value[position - 1]) ? " " : "";
    const next = field.value[position];
    const suffix = !next || !/[\s.,!?;:]/.test(next) ? " " : "";
    const insertion = `${prefix}${cue}${suffix}`;
    if (field.value.length + insertion.length > field.maxLength) {
      errorAt(errorId, "Shorten the text to make room for the sound cue.");
      return;
    }
    errorAt(errorId);
    field.setRangeText(insertion, position, position, "end");
    field.dispatchEvent(new Event("input", { bubbles: true }));
    field.focus();
  });
  return picker;
}

function instructionsEditor(id, value, readOnly = false) {
  const box = byId("delivery-instructions").content.firstElementChild.cloneNode(true);
  const field = box.querySelector("textarea");
  const picker = box.querySelector("select");
  field.id = id;
  field.value = value;
  field.readOnly = readOnly;
  picker.disabled = readOnly;
  box.querySelector("label").htmlFor = id;
  picker.addEventListener("change", () => {
    if (!picker.value) return;
    field.value = picker.value;
    picker.value = "";
    field.dispatchEvent(new Event("input", { bubbles: true }));
    field.focus();
  });
  return { box, field };
}

function renderPrepared() {
  const container = byId("prepared-sections");
  container.replaceChildren();
  prepared.forEach((part, index) => {
    const box = element("div", "section");
    const title = element("strong", "", `Section ${index + 1}`);
    const label = element("label", "", "Words to speak");
    const field = element("textarea");
    field.id = `prepared-text-${index}`;
    field.rows = 3;
    field.maxLength = chunkLimit;
    field.value = part.text;
    label.htmlFor = field.id;
    const sounds = cuePicker(field, "script-error");
    const directions = instructionsEditor(`prepared-instructions-${index}`, part.instructions);
    const refreshControls = () => {
      sounds.disabled = Boolean(directions.field.value.trim());
      updateEditor();
    };
    field.addEventListener("input", () => { part.text = field.value; refreshControls(); });
    directions.field.addEventListener("input", () => { part.instructions = directions.field.value; refreshControls(); });
    const remove = element("button", "button button-quiet", "Remove section");
    remove.type = "button";
    remove.disabled = prepared.length === 1;
    remove.addEventListener("click", () => {
      if (!window.confirm("Remove this section from the draft?")) return;
      prepared.splice(index, 1);
      renderPrepared();
      updateEditor();
    });
    box.append(title, label, field, sounds, directions.box, remove);
    container.append(box);
    refreshControls();
  });
}

function setPrepared(value) {
  prepared = value;
  byId("prepared-editor").hidden = !value;
  script.readOnly = Boolean(value);
  byId("import-script").disabled = Boolean(value);
  byId("prepare-sections").hidden = Boolean(value);
  byId("script-cue").hidden = Boolean(value);
  byId("script-instructions").hidden = Boolean(value);
  if (value) renderPrepared();
  updateEditor();
}

function renderVoice() {
  const signature = JSON.stringify([state.voice, state.voices, state.reference_notice]);
  if (signature === voiceSignature) return;
  voiceSignature = signature;
  const voice = state.voice;
  const audio = byId("voice-audio");
  const menu = byId("voice-selection");
  byId("voice-name").textContent = voice ? voice.label : "No voice reference yet";
  byId("voice-duration").textContent = voice ? `${voice.duration.toFixed(1)} seconds · WAV` : "Add a recording of your voice to get started.";
  byId("saved-references").hidden = state.voices.length < 2;
  menu.replaceChildren(...state.voices.map((item) => new Option(item.label, item.id)));
  if (voice) menu.value = voice.id;
  audio.hidden = !voice;
  if (voice) {
    const source = `/api/voice/${voice.id}/audio`;
    if (audio.getAttribute("src") !== source) audio.src = source;
  } else if (audio.hasAttribute("src")) {
    audio.removeAttribute("src");
    audio.load();
  }
  errorAt("voice-error", state.reference_notice);
}

async function changeReference(url, options) {
  if (referenceBusy) return false;
  referenceBusy = true;
  byId("voice-selection").disabled = true;
  byId("choose-voice").disabled = true;
  updateEditor();
  errorAt("voice-error");
  try {
    const voice = await change(url, options);
    voiceRevision++;
    state.voice = voice;
    if (!state.voices.some((item) => item.id === voice.id)) state.voices.unshift(voice);
    state.reference_notice = null;
    renderVoice();
    return true;
  } catch (error) {
    byId("voice-selection").value = state.voice?.id ?? "";
    errorAt("voice-error", error.message);
    return false;
  } finally {
    referenceBusy = false;
    byId("voice-selection").disabled = false;
    byId("choose-voice").disabled = false;
    updateEditor();
  }
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

function pauseEditor(entry, section, key) {
  const form = element("form", "section-pause");
  const label = element("label", "", "Extra pause after section (ms)");
  const input = element("input");
  input.id = `pause-after-${section.number}`;
  label.htmlFor = input.id;
  input.type = "number";
  input.min = "0";
  input.max = String(maxPause);
  input.step = "1";
  input.required = true;
  input.value = pauseDrafts.get(key) ?? section.pause_ms;
  input.readOnly = entry.status !== "ready";
  const save = element("button", "button button-quiet", "Update pause");
  save.type = "submit";
  const update = () => {
    save.disabled = input.readOnly || !input.validity.valid || input.valueAsNumber === section.pause_ms;
  };
  input.addEventListener("input", () => {
    pauseDrafts.set(key, input.value);
    update();
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (save.disabled) return;
    save.disabled = true;
    errorAt("result-error");
    try {
      await change(`/api/jobs/${entry.id}/sections/${section.number}/pause`, {
        method: "PUT", value: { pause_ms: input.valueAsNumber },
      });
      pauseDrafts.delete(key);
      await refreshJob();
    } catch (error) {
      errorAt("result-error", error.message);
      update();
    }
  });
  update();
  form.append(label, input, save);
  return form;
}

function renderSections(entry) {
  const container = byId("sections");
  const expanded = new Set([...container.querySelectorAll("details[open]")].map((node) => node.dataset.key));
  byId("review").hidden = !entry;
  if (!entry) {
    container.replaceChildren();
    return;
  }
  const sections = entry.sections.map((section, index) => {
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
    const sounds = cuePicker(field, "result-error");
    const directions = instructionsEditor(`section-instructions-${section.number}`,
      instructionDrafts.get(key) ?? section.instructions ?? "", field.readOnly);
    const updateActions = () => {
      const directed = Boolean(directions.field.value.trim());
      sounds.disabled = field.readOnly || directed;
      redo.disabled = field.readOnly || !field.value.trim() || (directed && !directedAvailable);
    };
    field.addEventListener("input", () => {
      sectionDrafts.set(key, field.value);
      updateActions();
    });
    directions.field.addEventListener("input", () => {
      instructionDrafts.set(key, directions.field.value);
      updateActions();
    });
    redo.addEventListener("click", async () => {
      redo.disabled = true;
      errorAt("result-error");
      try {
        await change(`/api/jobs/${entry.id}/sections/${section.number}`, {
          value: { text: field.value, instructions: directions.field.value },
        });
        sectionDrafts.delete(key);
        instructionDrafts.delete(key);
        await refreshJob();
      } catch (error) {
        errorAt("result-error", error.message);
        updateActions();
      }
    });
    const actions = element("div", "section-actions");
    actions.append(sounds, redo);
    updateActions();
    box.append(label, field, directions.box, actions);
    if (index + 1 < entry.sections.length) box.append(pauseEditor(entry, section, key));
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
  byId("balance").hidden = entry?.status !== "ready" || !entry?.export;
  byId("balance").disabled = false;
  errorAt("result-error", entry?.error);
  if (!entry) {
    message.textContent = "Generate a voice-over to play and download it here.";
  } else {
    badge(status, entry.status);
    const remaining = entry.sections.filter((section) => section.pending).length;
    const next = entry.sections.find((section) => section.pending)?.number;
    const total = entry.sections.length;
    const phrases = {
      queued: remaining ? `Waiting to generate · ${total - remaining} of ${total} sections complete` : "Waiting to rebuild exports…",
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
    const revision = voiceRevision;
    const snapshot = await request("/api/state");
    state = revision === voiceRevision ? snapshot : { ...state, jobs: snapshot.jobs };
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

const scriptSounds = cuePicker(script, "script-error");
const scriptDirections = instructionsEditor("recording-instructions", "");
byId("script-cue").append(scriptSounds);
byId("script-instructions").append(scriptDirections.box);

script.addEventListener("input", updateEditor);
scriptDirections.field.addEventListener("input", updateEditor);
byId("prepare-sections").addEventListener("click", async () => {
  errorAt("script-error");
  try {
    const result = await change("/api/sections/preview", { value: { script: script.value } });
    setPrepared(result.sections.map((text) => ({ text, instructions: scriptDirections.field.value })));
  } catch (error) {
    errorAt("script-error", error.message);
  }
});
byId("add-section").addEventListener("click", () => {
  prepared.push({ text: "", instructions: "" });
  renderPrepared();
  byId(`prepared-text-${prepared.length - 1}`).focus();
});
byId("back-to-script").addEventListener("click", () => {
  if (prepared.some((part) => part.instructions.trim()) && !window.confirm("Return to the script editor? Per-section instructions will be cleared.")) return;
  script.value = prepared.map((part) => part.text).join("\n\n");
  scriptDirections.field.value = "";
  setPrepared(null);
});
byId("script-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (submitting) return;
  submitting = true;
  updateEditor();
  errorAt("script-error");
  try {
    const created = await change("/api/jobs", {
      value: prepared
        ? { title: byId("recording-title").value, script: prepared.map((part) => part.text).join("\n\n"), sections: prepared }
        : { title: byId("recording-title").value, script: script.value, instructions: scriptDirections.field.value },
    });
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
byId("voice-selection").addEventListener("change", (event) => {
  changeReference(`/api/voice/${event.target.value}/active`, { method: "PUT" });
});
byId("voice-file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file) return;
  if (file.size > 16 * 1024 * 1024) {
    errorAt("voice-error", "Choose a WAV file under 16 MB.");
    return;
  }
  const data = new FormData();
  data.append("audio", file);
  data.append("name", byId("voice-label").value.trim());
  if (await changeReference("/api/voice", { value: data })) {
    byId("voice-label").value = "";
    byId("new-reference").open = false;
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

byId("balance").addEventListener("click", async (event) => {
  const id = selected;
  event.target.disabled = true;
  try {
    await change(`/api/jobs/${id}/balance`);
    await refreshJob();
  } catch (error) {
    errorAt("result-error", error.message);
    event.target.disabled = false;
  }
});

byId("reuse-script").addEventListener("click", () => {
  if (!job) return;
  if (prepared && !window.confirm("Replace the section draft with this recording's script?")) return;
  setPrepared(null);
  byId("recording-title").value = job.title;
  byId("imported-file").textContent = "";
  script.value = job.script;
  scriptDirections.field.value = job.instructions ?? "";
  updateEditor();
  script.focus();
});

updateEditor();
refresh();
window.setInterval(refresh, 1600);
