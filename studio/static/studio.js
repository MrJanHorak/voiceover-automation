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
let mixRevision = 0;
let mixPreviewUrl = null;
let prepared = null;
const sectionDrafts = new Map();
const pauseDrafts = new Map();
const instructionDrafts = new Map();
const turboDrafts = new Map();

const turboDefaults = { seed: 35, temperature: 0.8, top_p: 0.95, top_k: 1000,
  repetition_penalty: 1.2, normalize_reference: true };

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
  byId("generate").disabled = submitting || referenceBusy || !state.voice || !ready || !scriptAdvanced.valid();
}

function advancedEditor(prefix, values, onChange, readOnly = false) {
  const box = element("details", "advanced-settings");
  box.append(element("summary", "", "Advanced Chatterbox settings"));
  box.append(element("p", "muted advanced-intro", "Applies to Chatterbox sections only. Sections with special instructions use VoxCPM2. Change one setting at a time and listen to the result."));
  const controls = {};
  const definitions = [
    ["seed", "Seed", "number", 0, 2147483647, 1, "Use 0 for a random variation. The same nonzero seed and settings can help repeat a take; results can still differ across devices or model versions."],
    ["temperature", "Temperature", "range", 0.05, 2, 0.05, "Lower values tend to make delivery more predictable. Higher values add variation and may introduce artifacts. This is not a direct emotion control."],
    ["top_p", "Top P", "range", 0.01, 1, 0.01, "Limits choices to the most likely sounds. Lower values narrow the choices; 1 considers the full set."],
    ["top_k", "Top K", "range", 0, 1000, 1, "Limits how many possible sounds are considered at each step. Lower values narrow the choices; 0 disables this limit."],
    ["repetition_penalty", "Repetition penalty", "range", 1, 2, 0.05, "Discourages repeating the same sounds. Higher values may reduce loops but can also affect natural delivery."],
    ["normalize_reference", "Normalize reference loudness", "checkbox", null, null, null, "Adjusts the reference recording toward −27 LUFS before Chatterbox analyzes it. This can change the clone; it is separate from the studio's output volume balancing."],
  ];
  for (const [key, labelText, kind, min, max, step, explanation] of definitions) {
    const row = element("div", "advanced-row");
    const heading = element("div", "advanced-heading");
    const label = element("label", "", labelText);
    const field = document.createElement("input");
    field.type = kind;
    if (kind !== "checkbox") field.required = true;
    field.id = `${prefix}-${key}`;
    label.htmlFor = field.id;
    if (min !== null) { field.min = min; field.max = max; field.step = step; }
    if (kind === "checkbox") field.checked = values[key];
    else field.value = values[key];
    field.disabled = readOnly;
    controls[key] = field;
    const help = element("details", "setting-help");
    const helpButton = element("summary", "", "?");
    helpButton.setAttribute("aria-label", `About ${labelText}`);
    help.append(helpButton, element("p", "", explanation));
    heading.append(label, help);
    if (kind === "range") {
      const output = element("output", "advanced-value", field.value);
      output.htmlFor = field.id;
      field.addEventListener("input", () => { output.value = field.value; onChange(); });
      heading.append(output);
    } else {
      field.addEventListener("input", onChange);
      field.addEventListener("change", onChange);
    }
    row.append(heading, field);
    box.append(row);
  }
  box.append(element("p", "muted advanced-note", "Min P is not supported by this installed Turbo version. Output loudness is balanced separately by the studio."));
  return {
    box,
    valid: () => Object.values(controls).every((field) => field.validity.valid),
    value: () => Object.fromEntries(Object.entries(controls).map(([key, field]) =>
      [key, field.type === "checkbox" ? field.checked : field.valueAsNumber])),
  };
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
  byId("mix-add").disabled = !state.voices.length || byId("mix-excerpts").children.length >= 20;
}

function invalidateMix() {
  mixRevision++;
  byId("mix-result").hidden = true;
  byId("mix-audio").pause();
  byId("mix-audio").removeAttribute("src");
  if (mixPreviewUrl) URL.revokeObjectURL(mixPreviewUrl);
  mixPreviewUrl = null;
  errorAt("mix-error");
}

function addMixExcerpt() {
  if (!state.voices.length || byId("mix-excerpts").children.length >= 20) return;
  invalidateMix();
  const row = element("div", "section mix-excerpt");
  const label = element("label", "", "Saved reference");
  const select = document.createElement("select");
  select.setAttribute("aria-label", "Saved reference for excerpt");
  select.replaceChildren(...state.voices.map((voice) => new Option(`${voice.label} · ${voice.duration.toFixed(1)} s`, voice.id)));
  const player = element("audio");
  player.controls = true;
  player.preload = "metadata";
  player.setAttribute("aria-label", "Listen to source reference");
  const fields = element("div", "mix-times");
  const startLabel = element("label", "", "Start (seconds)");
  const endLabel = element("label", "", "End (seconds)");
  const start = document.createElement("input");
  const end = document.createElement("input");
  for (const field of [start, end]) {
    field.type = "number"; field.min = "0"; field.max = "30"; field.step = "0.01"; field.required = true;
    field.addEventListener("input", invalidateMix);
  }
  start.value = "0";
  function selectVoice() {
    player.src = `/api/voice/${select.value}/audio`;
    const voice = state.voices.find((item) => item.id === select.value);
    end.value = Math.min(5, Math.floor(voice.duration * 10) / 10);
    start.value = "0";
    invalidateMix();
  }
  select.addEventListener("change", selectVoice);
  startLabel.append(start); endLabel.append(end); fields.append(startLabel, endLabel);
  const remove = element("button", "button button-quiet", "Remove excerpt");
  remove.type = "button";
  remove.addEventListener("click", () => { row.remove(); invalidateMix(); byId("mix-add").disabled = false; });
  label.append(select);
  row.append(label, player, fields, remove);
  byId("mix-excerpts").append(row);
  selectVoice();
  byId("mix-add").disabled = byId("mix-excerpts").children.length >= 20;
}

function mixPayload() {
  return { name: byId("mix-name").value.trim(), excerpts: [...byId("mix-excerpts").children].map((row) => {
    const fields = row.querySelectorAll("input");
    return { voice_id: row.querySelector("select").value, start: fields[0].valueAsNumber, end: fields[1].valueAsNumber };
  }) };
}

byId("mix-add").addEventListener("click", addMixExcerpt);
byId("reference-builder").addEventListener("toggle", () => {
  if (byId("reference-builder").open && !byId("mix-excerpts").children.length && state.voices.length) addMixExcerpt();
});
byId("mix-preview").addEventListener("click", async () => {
  invalidateMix();
  const revision = mixRevision;
  const button = byId("mix-preview");
  button.disabled = true;
  try {
    const response = await fetch("/api/voice/combine/preview", { method: "POST", headers: { "X-Studio-Request": "1", "Content-Type": "application/json" }, body: JSON.stringify(mixPayload()) });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || "Could not preview the combined reference.");
    }
    const blob = await response.blob();
    if (revision !== mixRevision) return;
    mixPreviewUrl = URL.createObjectURL(blob);
    byId("mix-audio").src = mixPreviewUrl;
    byId("mix-result").hidden = false;
  } catch (error) {
    if (revision === mixRevision) errorAt("mix-error", error.message);
  } finally { button.disabled = false; }
});
byId("mix-save").addEventListener("click", async () => {
  const button = byId("mix-save");
  button.disabled = true;
  const saved = await changeReference("/api/voice/combine", { value: mixPayload() });
  if (saved) {
    invalidateMix();
    byId("mix-excerpts").replaceChildren();
    byId("mix-name").value = "";
    byId("reference-builder").open = false;
    byId("mix-add").disabled = false;
  } else errorAt("mix-error", byId("voice-error").textContent);
  button.disabled = false;
});

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
      const details = section.generation || {};
      const source = details.engine
        ? [details.engine, details.device?.toUpperCase(), details.runtime].filter(Boolean).join(" · ")
        : "Engine details unavailable";
      const metadata = element("p", "muted section-metadata", `Voice reference: ${entry.voice_reference || "Unknown"} · ${source}`);
      if (details.model_sha256) {
        metadata.textContent += ` · Model ${details.model_sha256.slice(0, 12)}`;
        metadata.title = `Primary model SHA-256: ${details.model_sha256}`;
      }
      box.append(metadata);
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
    const advanced = advancedEditor(`section-advanced-${section.number}`,
      turboDrafts.get(key) ?? { ...turboDefaults, ...(section.turbo_settings || {}), seed: 0 },
      () => { turboDrafts.set(key, advanced.value()); updateActions(); }, field.readOnly);
    const updateActions = () => {
      const directed = Boolean(directions.field.value.trim());
      sounds.disabled = field.readOnly || directed;
      redo.disabled = field.readOnly || !field.value.trim() || (directed && !directedAvailable) || !advanced.valid();
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
          value: { text: field.value, instructions: directions.field.value, turbo_settings: advanced.value() },
        });
        sectionDrafts.delete(key);
        instructionDrafts.delete(key);
        turboDrafts.delete(key);
        await refreshJob();
      } catch (error) {
        errorAt("result-error", error.message);
        updateActions();
      }
    });
    const actions = element("div", "section-actions");
    actions.append(sounds, redo);
    updateActions();
    box.append(label, field, directions.box, advanced.box, actions);
    if ((section.takes?.length || 0) > 1 || (section.pending && section.takes?.length)) {
      const history = element("details", "take-history");
      history.append(element("summary", "", `All takes (${section.takes.length})`));
      section.takes.forEach((take, takeIndex) => {
        const item = element("div", "take-item");
        const selectedTake = take.current && !section.pending;
        item.append(element("strong", "", selectedTake ? "Current take" : `Take ${section.takes.length - takeIndex}`));
        const info = [take.engine, take.device?.toUpperCase()].filter(Boolean).join(" · ");
        if (info) item.append(element("p", "muted", info));
        item.append(element("p", "take-text", take.text ?? "Words for this older take are unavailable."));
        if (take.instructions) item.append(element("p", "muted", `Instructions: ${take.instructions}`));
        if (take.turbo_settings && !take.instructions) {
          const setting = take.turbo_settings;
          item.append(element("p", "muted", `Seed ${take.seed} · Temperature ${setting.temperature} · Top P ${setting.top_p} · Top K ${setting.top_k} · Repetition ${setting.repetition_penalty} · Reference normalization ${setting.normalize_reference ? "on" : "off"}`));
        }
        const player = element("audio");
        player.controls = true;
        player.preload = "none";
        player.setAttribute("aria-label", `Section ${section.number} take ${section.takes.length - takeIndex}`);
        player.src = `/api/jobs/${entry.id}/sections/${section.number}/takes/${take.id}/audio`;
        item.append(player);
        if (!selectedTake) {
          const restore = element("button", "button button-quiet", "Use this take");
          restore.type = "button";
          restore.disabled = !take.can_restore || !["ready", "failed"].includes(entry.status);
          if (!take.can_restore) restore.title = "The words for this older take are unknown.";
          restore.addEventListener("click", async () => {
            restore.disabled = true;
            errorAt("result-error");
            try {
              await change(`/api/jobs/${entry.id}/sections/${section.number}/takes/${take.id}/restore`);
              sectionDrafts.delete(key);
              instructionDrafts.delete(key);
              turboDrafts.delete(key);
              await refreshJob();
            } catch (error) {
              errorAt("result-error", error.message);
              restore.disabled = false;
            }
          });
          item.append(restore);
        }
        history.append(item);
      });
      box.append(history);
    }
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
const scriptAdvanced = advancedEditor("script-advanced-setting", turboDefaults, updateEditor);
byId("script-cue").append(scriptSounds);
byId("script-instructions").append(scriptDirections.box);
byId("script-advanced").append(scriptAdvanced.box);

script.addEventListener("input", updateEditor);
scriptDirections.field.addEventListener("input", updateEditor);
byId("prepare-sections").addEventListener("click", async () => {
  errorAt("script-error");
  try {
    const result = await change("/api/sections/preview", { value: { script: script.value } });
    setPrepared(result.sections.map((text, index) => ({ text, instructions: scriptDirections.field.value, pause_ms: result.pauses?.[index] })));
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
        ? { title: byId("recording-title").value, script: prepared.map((part) => part.text).join("\n\n"), sections: prepared, turbo_settings: scriptAdvanced.value() }
        : { title: byId("recording-title").value, script: script.value, instructions: scriptDirections.field.value, turbo_settings: scriptAdvanced.value() },
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
  if (script.value.trim() && !window.confirm("Replace the current script with this file?")) return;
  const original = script.value;
  const button = byId("import-script");
  button.disabled = true;
  try {
    if (!/\.(txt|docx|pdf)$/i.test(file.name)) throw new Error("Choose a .txt, .docx, or .pdf script.");
    if (file.size > 10 * 1024 * 1024) throw new Error("Choose a script file under 10 MB.");
    const data = new FormData();
    data.append("file", file);
    const result = await change("/api/scripts/import", { value: data });
    const imported = result.text;
    if (!imported || imported.length > scriptLimit) {
      throw new Error(`Use a script of 1 to ${scriptLimit.toLocaleString()} characters.`);
    }
    if (script.value !== original || prepared) {
      throw new Error("The draft changed during import. Your draft was kept. Import the file again when ready.");
    }
    script.value = imported;
    byId("imported-file").textContent = file.name;
    const notice = byId("import-notice");
    notice.textContent = result.warnings.join(" ");
    notice.hidden = !result.warnings.length;
    if (!byId("recording-title").value.trim()) byId("recording-title").value = file.name.replace(/\.(txt|docx|pdf)$/i, "").slice(0, 100);
    updateEditor();
    script.focus();
  } catch (error) {
    errorAt("script-error", error.message);
  } finally {
    button.disabled = Boolean(prepared);
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
  byId("import-notice").hidden = true;
  script.value = job.script;
  scriptDirections.field.value = job.instructions ?? "";
  updateEditor();
  script.focus();
});

updateEditor();
refresh();
window.setInterval(refresh, 1600);
