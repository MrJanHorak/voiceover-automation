const byId = (id) => document.getElementById(id);
const script = byId("script");
const scriptLimit = Number(document.body.dataset.scriptLimit);
const chunkLimit = Number(document.body.dataset.chunkLimit);
const maxPause = Number(document.body.dataset.maxPause);
const directedAvailable = document.body.dataset.directedAvailable === "true";
const trainedVoice = document.body.dataset.trainedVoice || "";
// Customers see only the recording workflow; the server enforces the same split.
const isAdmin = document.body.dataset.admin === "true";
const authEnabled = document.body.dataset.auth === "true";
const houseMatch = document.body.dataset.houseMatch || "standard";
const usesVoxcpm = (match) => match !== "standard";
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
let scriptMode = "edit";
let styleMode = "whole";
let paragraphs = [];
let paragraphsFor = null;
let spokenRequest = 0;
const sectionDrafts = new Map();
const pauseDrafts = new Map();
const instructionDrafts = new Map();
const turboDrafts = new Map();
const matchDrafts = new Map();
let transcriptVoice = null;
let transcriptDirty = false;
let pronunciationSignature = "";
let currentView = "create";
let currentStep = 1;
let reviewFilter = "all";
const sectionSelection = new Map();
const takeSelection = new Map();
const openMore = new Set();
const views = isAdmin
  ? ["create", "recordings", ...(authEnabled ? ["voices"] : []), "voice", "training", ...(authEnabled ? ["customers"] : []), "guide"]
  : ["create", "recordings", "guide"];

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
  if (response.status === 401 && authEnabled) {
    window.location.replace("/login");
  }
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

function showView(name, { remember = true } = {}) {
  currentView = views.includes(name) ? name : "create";
  for (const view of views) byId(`view-${view}`).hidden = view !== currentView;
  for (const link of document.querySelectorAll(".nav-link")) {
    if (link.dataset.view === currentView) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  if (remember && location.hash !== `#${currentView}`) history.replaceState(null, "", `#${currentView}`);
  if (currentView === "training") {
    loadDataset();
    loadComparisons();
  }
  if (currentView === "customers") loadCustomers();
  if (currentView === "voices") loadVoices();
  window.scrollTo({ top: 0 });
}

function setStep(step) {
  currentStep = step;
  byId("step-1").hidden = step !== 1;
  byId("step-2").hidden = step !== 2;
  if (step === 2 && styleMode === "paragraphs") refreshParagraphs();
  for (const button of document.querySelectorAll(".step")) {
    if (Number(button.dataset.step) === step) button.setAttribute("aria-current", "step");
    else button.removeAttribute("aria-current");
  }
  updateEditor();
}

function matchReady(instructions, match) {
  if (!isAdmin) return Boolean(state.voice);  // Customers always use the studio voice.
  // A trained voice also applies to sections with a delivery style; maximum resemblance does not.
  if (match === "trained" || match === "trained_reference") return directedAvailable && Boolean(trainedVoice);
  return instructions.trim() || match !== "maximum" || (directedAvailable && Boolean(state.voice?.transcript));
}

function updateEditor() {
  byId("script-count").textContent = `${script.value.length.toLocaleString()} / ${scriptLimit.toLocaleString()}`;
  const directed = Boolean(scriptDirections.field.value.trim());
  const match = scriptEngine.field.value;
  scriptSounds.disabled = directed || usesVoxcpm(match);
  const perParagraph = styleMode === "paragraphs";
  const ready = perParagraph
    ? paragraphs.length > 0 && paragraphsFor === script.value
      && paragraphs.every((item) => (!item.style.trim() || directedAvailable) && matchReady(item.style, match))
    : Boolean(script.value.trim()) && (!directed || directedAvailable) && matchReady(scriptDirections.field.value, match);
  const needsTranscript = !directed && match === "maximum" && directedAvailable && state.voice && !state.voice.transcript;
  errorAt("engine-note", needsTranscript ? "Add the reference transcript under Voice reference to use maximum resemblance." : "");
  const overridden = match === "maximum" && (perParagraph ? paragraphs.some((item) => item.style.trim()) : directed);
  byId("engine-override").hidden = !overridden;
  byId("generate").disabled = submitting || referenceBusy || !state.voice || !ready || !scriptAdvanced.valid();
}

function voiceMatchEditor(id, value, readOnly = false) {
  const box = byId("voice-engine").content.firstElementChild.cloneNode(true);
  const field = box.querySelector("select");
  field.id = id;
  field.value = value || (isAdmin ? "standard" : houseMatch);
  // An engine this server cannot run (for example Chatterbox without a GPU) falls back to the first one it can.
  if (field.selectedOptions[0]?.disabled) field.value = [...field.options].find((option) => !option.disabled)?.value ?? field.value;
  field.disabled = readOnly;
  if (!isAdmin) {
    field.value = houseMatch;
    box.hidden = true;
  }
  box.querySelector("label").htmlFor = id;
  return { box, field };
}

function advancedEditor(prefix, values, onChange, readOnly = false) {
  const box = element("details", "advanced-settings");
  box.hidden = !isAdmin;
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
  // Sound cues work only with Chatterbox; customers never see a control that cannot work for them.
  picker.hidden = !isAdmin;
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

function renderVoice() {
  const signature = JSON.stringify([state.voice, state.voices, state.reference_notice, state.catalog]);
  if (signature === voiceSignature) return;
  voiceSignature = signature;
  if (!isAdmin) {
    renderVoicePicker(state.catalog || []);
    return;
  }
  const voice = state.voice;
  const audio = byId("voice-audio");
  const menu = byId("voice-selection");
  byId("voice-name").textContent = voice ? voice.label : "No voice reference yet";
  byId("settings-voice").textContent = voice
    ? `${voice.label}${voice.transcript ? " · transcript saved" : ""}` : "None yet. Add one under References.";
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
  renderTranscript();
  byId("mix-add").disabled = !state.voices.length || byId("mix-excerpts").children.length >= 20;
}

function renderTranscript() {
  const voice = state.voice;
  const field = byId("voice-transcript");
  byId("reference-transcript").hidden = !voice;
  if (!voice) return;
  if (transcriptVoice !== voice.id || !transcriptDirty) {
    if (transcriptVoice !== voice.id) byId("transcript-status").textContent = "";
    transcriptVoice = voice.id;
    transcriptDirty = false;
    field.value = voice.transcript || "";
  }
  byId("transcript-state").textContent = voice.transcript ? "· Saved" : "· Needed for maximum resemblance";
  byId("save-transcript").disabled = !transcriptDirty;
}

byId("voice-transcript").addEventListener("input", () => {
  transcriptDirty = true;
  byId("transcript-status").textContent = "";
  byId("save-transcript").disabled = false;
});
byId("save-transcript").addEventListener("click", async () => {
  const button = byId("save-transcript");
  const voiceId = transcriptVoice;
  button.disabled = true;
  errorAt("voice-error");
  try {
    const voice = await change(`/api/voice/${voiceId}/transcript`, { method: "PUT", value: { transcript: byId("voice-transcript").value } });
    voiceRevision++;
    state.voices = state.voices.map((item) => item.id === voice.id ? voice : item);
    if (state.voice?.id === voice.id) state.voice = voice;
    if (transcriptVoice === voice.id) {
      transcriptDirty = false;
      byId("transcript-status").textContent = "Transcript saved.";
    }
    voiceSignature = "";
    renderVoice();
    updateEditor();
    if (job) renderSections(job);
  } catch (error) {
    errorAt("voice-error", error.message);
    button.disabled = false;
  }
});

function renderPronunciations() {
  const words = state.pronunciations || [];
  const signature = JSON.stringify(words);
  if (signature === pronunciationSignature) return;
  pronunciationSignature = signature;
  const list = byId("pronunciation-list");
  list.replaceChildren(...words.map((word) => {
    const item = element("li");
    item.append(element("span", "", `${word.written} → ${word.spoken}`));
    const edit = element("button", "button button-quiet", "Edit");
    edit.type = "button";
    edit.addEventListener("click", () => {
      byId("pronunciation-written").value = word.written;
      byId("pronunciation-spoken").value = word.spoken;
      byId("pronunciation-spoken").focus();
    });
    const remove = element("button", "button button-quiet", "Remove");
    remove.type = "button";
    remove.setAttribute("aria-label", `Remove pronunciation for ${word.written}`);
    remove.addEventListener("click", async () => {
      remove.disabled = true;
      errorAt("pronunciation-error");
      try {
        await change(`/api/pronunciations/${word.id}`, { method: "DELETE" });
        state.pronunciations = (state.pronunciations || []).filter((item) => item.id !== word.id);
        renderPronunciations();
        jobSignature = "";
        await refreshJob();
      } catch (error) {
        errorAt("pronunciation-error", error.message);
        remove.disabled = false;
      }
    });
    item.append(edit, remove);
    return item;
  }));
  list.hidden = !words.length;
  renderScriptWords();
}

async function savePronunciation() {
  const written = byId("pronunciation-written");
  const spoken = byId("pronunciation-spoken");
  if (!written.value.trim() || !spoken.value.trim()) {
    errorAt("pronunciation-error", "Enter the written word and how to say it.");
    return;
  }
  const button = byId("pronunciation-add");
  button.disabled = true;
  errorAt("pronunciation-error");
  try {
    const word = await change("/api/pronunciations", { value: { written: written.value, spoken: spoken.value } });
    state.pronunciations = [...(state.pronunciations || []).filter((item) => item.id !== word.id), word]
      .sort((a, b) => a.written.localeCompare(b.written, undefined, { sensitivity: "base" }));
    written.value = "";
    spoken.value = "";
    renderPronunciations();
    jobSignature = "";
    await refreshJob();
    written.focus();
  } catch (error) {
    errorAt("pronunciation-error", error.message);
  } finally {
    button.disabled = false;
  }
}

byId("pronunciation-add").addEventListener("click", savePronunciation);
for (const id of ["pronunciation-written", "pronunciation-spoken"]) {
  // These fields sit inside the script form; Enter saves the word instead of generating.
  byId(id).addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); savePronunciation(); }
  });
}
// ---- Script step: edit or preview the spoken words, and pronunciations ----

function setScriptMode(mode) {
  scriptMode = mode;
  byId("script-edit").hidden = mode !== "edit";
  byId("spoken-view").hidden = mode !== "preview";
  byId("mode-edit").setAttribute("aria-pressed", String(mode === "edit"));
  byId("mode-preview").setAttribute("aria-pressed", String(mode === "preview"));
  byId("import-script").disabled = mode !== "edit";
  if (mode === "preview") loadSpoken();
  else script.focus();
}

async function loadSpoken() {
  const output = byId("spoken-text");
  errorAt("word-error");
  if (!script.value.trim()) {
    output.replaceChildren(element("p", "muted", "Write or import a script to preview how it will be spoken."));
    return;
  }
  const version = ++spokenRequest;
  try {
    const result = await change("/api/spoken-preview", { value: { script: script.value } });
    if (version !== spokenRequest) return;
    renderSpoken(result.paragraphs || []);
  } catch (error) {
    errorAt("word-error", error.message);
  }
}

function wordButton(label, written, className = "word") {
  const button = element("button", className, label);
  button.type = "button";
  button.addEventListener("click", () => fillWord(written));
  return button;
}

function renderSpoken(paragraphs) {
  const output = byId("spoken-text");
  output.replaceChildren(...paragraphs.map((segments) => {
    const paragraph = element("p");
    segments.forEach((segment, index) => {
      if (index) paragraph.append(" ");
      if (segment.spoken === undefined) {
        // Words read as written: each one can be selected to set a pronunciation.
        segment.text.split(" ").forEach((word, position) => {
          if (position) paragraph.append(" ");
          if (/^\[[^\]]*\]$/.test(word) || !/[A-Za-z]/.test(word)) paragraph.append(word);
          else paragraph.append(wordButton(word, word));
        });
        return;
      }
      const changed = wordButton(segment.spoken || "(silent)", segment.text, `word changed ${segment.kind}`);
      changed.dataset.written = segment.text;
      changed.setAttribute("aria-label", `${segment.spoken}, written as ${segment.text}`);
      changed.title = `Written: ${segment.text}`;
      paragraph.append(changed);
    });
    return paragraph;
  }));
}

function cleanWord(word) {
  return word.replace(/^[^\w]+|[^\w]+$/g, "");
}

function fillWord(written) {
  // Offer the first word of a selection; existing pronunciations open for editing.
  const word = cleanWord(written.split(" ").find((part) => /[A-Za-z]/.test(part)) || written);
  const saved = (state.pronunciations || []).find((item) => item.written.toLowerCase() === word.toLowerCase());
  byId("word-written").value = saved ? saved.written : word;
  byId("word-spoken").value = saved ? saved.spoken : "";
  byId("word-spoken").focus();
  byId("script-words-box").scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function wordsInScript() {
  const text = script.value.toLowerCase();
  return (state.pronunciations || []).filter((item) => {
    const escaped = item.written.toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return new RegExp(`(^|[^\\w])${escaped}([^\\w]|$)`).test(text);
  });
}

function renderScriptWords() {
  const list = byId("script-words");
  const words = wordsInScript();
  list.replaceChildren(...words.map((word) => {
    const item = element("li", "word-chip");
    const edit = element("button", "chip-label", `${word.written} → ${word.spoken}`);
    edit.type = "button";
    edit.title = "Edit this pronunciation";
    edit.addEventListener("click", () => fillWord(word.written));
    const remove = element("button", "chip-remove", "×");
    remove.type = "button";
    remove.setAttribute("aria-label", `Remove pronunciation for ${word.written}`);
    remove.addEventListener("click", () => deleteWord(word, "word-error"));
    item.append(edit, remove);
    return item;
  }));
  list.hidden = !words.length;
}

async function saveWord(written, spoken, errorId) {
  if (!written.trim() || !spoken.trim()) {
    errorAt(errorId, "Enter the word as written and how to say it.");
    return false;
  }
  errorAt(errorId);
  try {
    const word = await change("/api/pronunciations", { value: { written, spoken } });
    state.pronunciations = [...(state.pronunciations || []).filter((item) => item.id !== word.id), word]
      .sort((a, b) => a.written.localeCompare(b.written, undefined, { sensitivity: "base" }));
    pronunciationsChanged();
    return true;
  } catch (error) {
    errorAt(errorId, error.message);
    return false;
  }
}

async function deleteWord(word, errorId) {
  errorAt(errorId);
  try {
    await change(`/api/pronunciations/${word.id}`, { method: "DELETE" });
    state.pronunciations = (state.pronunciations || []).filter((item) => item.id !== word.id);
    pronunciationsChanged();
  } catch (error) {
    errorAt(errorId, error.message);
  }
}

function pronunciationsChanged() {
  pronunciationSignature = "";
  renderPronunciations();
  renderScriptWords();
  if (scriptMode === "preview") loadSpoken();
  jobSignature = "";
  refreshJob();
}

byId("mode-edit").addEventListener("click", () => setScriptMode("edit"));
byId("mode-preview").addEventListener("click", () => setScriptMode("preview"));
byId("word-save").addEventListener("click", async () => {
  if (await saveWord(byId("word-written").value, byId("word-spoken").value, "word-error")) {
    byId("word-written").value = "";
    byId("word-spoken").value = "";
  }
});
for (const id of ["word-written", "word-spoken"]) {
  byId(id).addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); byId("word-save").click(); }
  });
}

// ---- Settings step: one delivery style, or one per paragraph ----

const styleExamples = [...byId("delivery-instructions").content.querySelectorAll("select option")]
  .filter((option) => option.value).map((option) => ({ value: option.value, label: option.textContent }));

function setStyleMode(mode) {
  styleMode = mode;
  byId("style-whole").setAttribute("aria-pressed", String(mode === "whole"));
  byId("style-paragraphs").setAttribute("aria-pressed", String(mode === "paragraphs"));
  byId("script-instructions").hidden = mode !== "whole";
  byId("paragraph-styles").hidden = mode !== "paragraphs";
  if (mode === "paragraphs") refreshParagraphs();
  updateEditor();
}

async function refreshParagraphs() {
  if (paragraphsFor === script.value) return renderParagraphStyles();
  const text = script.value;
  if (!text.trim()) {
    paragraphs = [];
    paragraphsFor = text;
    return renderParagraphStyles();
  }
  try {
    const result = await change("/api/sections/preview", { value: { script: text } });
    const previous = new Map(paragraphs.map((item) => [item.text, item.style]));
    const grouped = [];
    let current = [];
    result.sections.forEach((sectionText, index) => {
      const pause = result.pauses?.[index];
      current.push({ text: sectionText, pause_ms: pause });
      if (pause === undefined || pause >= 750 || index === result.sections.length - 1) {
        grouped.push(current);
        current = [];
      }
    });
    paragraphs = grouped.map((sections) => {
      const joined = sections.map((item) => item.text).join(" ");
      return { text: joined, sections, style: previous.get(joined) ?? "" };
    });
    paragraphsFor = text;
    renderParagraphStyles();
  } catch (error) {
    errorAt("script-error", error.message);
  }
}

function renderParagraphStyles() {
  const list = byId("paragraph-styles");
  if (!paragraphs.length) {
    list.replaceChildren(element("li", "muted", "Write a script in step 1 to choose a style per paragraph."));
    updateEditor();
    return;
  }
  list.replaceChildren(...paragraphs.map((item, index) => {
    const row = element("li", "paragraph-style");
    row.append(element("span", "row-number", String(index + 1)), element("span", "row-text", item.text));
    const select = element("select");
    select.setAttribute("aria-label", `Delivery style for paragraph ${index + 1}`);
    const custom = item.style && !styleExamples.some((example) => example.value === item.style);
    select.append(new Option("Default", ""), ...styleExamples.map((example) => new Option(example.label, example.value)),
      new Option("Custom…", "__custom"));
    select.value = custom ? "__custom" : item.style;
    const input = element("input");
    input.type = "text";
    input.maxLength = 240;
    input.placeholder = "Describe the delivery, like calm and reassuring";
    input.setAttribute("aria-label", `Custom delivery style for paragraph ${index + 1}`);
    input.value = custom ? item.style : "";
    input.hidden = !custom;
    select.addEventListener("change", () => {
      input.hidden = select.value !== "__custom";
      item.style = select.value === "__custom" ? input.value : select.value;
      if (!input.hidden) input.focus();
      updateEditor();
    });
    input.addEventListener("input", () => { item.style = input.value; updateEditor(); });
    row.append(select, input);
    return row;
  }));
  updateEditor();
}

byId("style-whole").addEventListener("click", () => setStyleMode("whole"));
byId("style-paragraphs").addEventListener("click", () => setStyleMode("paragraphs"));

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

// Admins switch the list between their own recordings and customers' recordings.
let historyScope = "mine";
let customerJobs = [];

async function loadCustomerJobs() {
  if (historyScope !== "customers") return;
  try {
    customerJobs = (await request("/api/admin/recordings")).recordings;
  } catch (error) {
    errorAt("result-error", error.message);
  }
}

async function setHistoryScope(scope) {
  historyScope = scope;
  byId("scope-mine")?.setAttribute("aria-pressed", String(scope === "mine"));
  byId("scope-customers")?.setAttribute("aria-pressed", String(scope === "customers"));
  await loadCustomerJobs();
  renderHistory();
}

byId("scope-mine")?.addEventListener("click", () => setHistoryScope("mine"));
byId("scope-customers")?.addEventListener("click", () => setHistoryScope("customers"));

function renderHistory() {
  const listed = historyScope === "customers" ? customerJobs : state.jobs;
  const signature = JSON.stringify([selected, historyScope, listed]);
  if (historySignature === signature) return;
  historySignature = signature;
  byId("history-empty").hidden = listed.length > 0;
  byId("history-empty").textContent = historyScope === "customers" ? "No customer recordings yet." : "Your recordings will appear here.";
  const items = listed.map((entry) => {
    const row = element("li");
    const open = element("button", "button button-quiet history-select");
    open.type = "button";
    open.setAttribute("aria-current", String(entry.id === selected));
    open.addEventListener("click", () => selectJob(entry.id));
    const status = element("span", "status");
    badge(status, entry.status);
    const name = element("span", "history-name", entry.title);
    if (entry.email) name.append(element("span", "history-owner", entry.email));
    open.append(name, status);
    if (entry.awaiting_approval) open.append(element("span", "approval-tag", "Approve"));
    row.append(open);
    const remove = element("button", "button button-quiet remove-job", "×");
    remove.type = "button";
    remove.setAttribute("aria-label", `Delete ${entry.title}`);
    remove.disabled = ["queued", "running"].includes(entry.status);
    remove.addEventListener("click", async () => {
      const whose = entry.email ? ` by ${entry.email}` : "";
      if (!window.confirm(`Delete “${entry.title}”${whose} and its audio files? The script stays in the review log.`)) return;
      remove.disabled = true;
      try {
        await change(`/api/jobs/${entry.id}`, { method: "DELETE" });
        state.jobs = state.jobs.filter((saved) => saved.id !== entry.id);
        customerJobs = customerJobs.filter((saved) => saved.id !== entry.id);
        if (selected === entry.id) await selectJob((historyScope === "customers" ? customerJobs : state.jobs)[0]?.id ?? null);
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

function updateLoudness(entry) {
  const control = byId("loudness-control");
  const select = byId("loudness-target");
  control.hidden = !entry?.export;
  if (!entry) return;
  if (document.activeElement !== select) select.value = entry.loudness_target === null ? "" : String(entry.loudness_target);
  select.disabled = entry.status !== "ready" || Boolean(entry.read_only);
  const measured = entry.export_loudness;
  const rate = `${(entry.master_rate || 24000) / 1000} kHz master`;
  const format = (value) => Number(value).toFixed(1).replace("-", "−");
  byId("loudness-result").textContent = measured
    ? `Exported at ${format(measured.integrated)} LUFS, true peak ${format(measured.true_peak)} dBTP · ${rate}`
    : entry.loudness_target === null ? `No final loudness pass · ${rate}` : `Applies when the exports are rebuilt · ${rate}`;
}

byId("loudness-target").addEventListener("change", async (event) => {
  if (!job) return;
  const select = event.target;
  select.disabled = true;
  errorAt("result-error");
  try {
    await change(`/api/jobs/${job.id}/loudness`, { method: "PUT", value: { target: select.value === "" ? null : Number(select.value) } });
    await refreshJob();
  } catch (error) {
    errorAt("result-error", error.message);
    select.disabled = false;
  }
});

function updateExport(entry) {
  updateLoudness(entry);
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
  input.readOnly = entry.status !== "ready" || Boolean(entry.read_only);
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

function checkSummary(check) {
  if (!check) return null;
  if (check.status === "error") return { className: "check-review", text: "Automatic check could not run." };
  const parts = [`${Math.round((check.word_error ?? 0) * 100)}% word differences`];
  if (check.similarity !== null && check.similarity !== undefined) parts.push(`voice match ${check.similarity.toFixed(2)}`);
  if (check.status === "pass") return { className: "check-pass", text: `Check passed · ${parts.join(" · ")}` };
  return { className: "check-review", text: `Needs review: ${(check.reasons || []).join("; ")} · Heard: \u201c${check.heard || ""}\u201d` };
}

function sectionState(section) {
  if (section.pending) return "pending";
  if (!section.check) return "unchecked";
  return section.check.status === "pass" ? "pass" : "review";
}

const stateLabels = { pass: "Check passed", review: "Needs review", pending: "Pending", unchecked: "Not checked" };
let playingButton = null;

function playSection(entry, section, button) {
  // One shared player for quick listening from the list; the editor has full controls.
  const player = byId("section-player");
  const source = `/api/jobs/${entry.id}/sections/${section.number}/audio?v=${section.audio}`;
  const restart = player.dataset.source !== source || player.paused;
  player.pause();
  if (playingButton) { playingButton.textContent = "▶"; playingButton.setAttribute("aria-label", playingButton.dataset.label); }
  playingButton = null;
  if (!restart) return;
  player.dataset.source = source;
  player.src = source;
  playingButton = button;
  button.textContent = "■";
  button.setAttribute("aria-label", `Stop section ${section.number}`);
  player.play().catch(() => {});
}

byId("section-player").addEventListener("ended", () => {
  if (playingButton) { playingButton.textContent = "▶"; playingButton.setAttribute("aria-label", playingButton.dataset.label); }
  playingButton = null;
});

function selectSection(entry, number) {
  sectionSelection.set(entry.id, number);
  renderSections(entry);
}

function renderSections(entry) {
  const list = byId("sections");
  const editor = byId("section-editor");
  byId("review").hidden = !entry;
  if (!entry) {
    list.replaceChildren();
    editor.replaceChildren();
    return;
  }
  const states = new Map(entry.sections.map((section) => [section.number, sectionState(section)]));
  const reviewCount = [...states.values()].filter((value) => value === "review").length;
  if (!reviewCount && reviewFilter === "review") reviewFilter = "all";
  byId("review-filter").replaceChildren(...[["all", `All · ${entry.sections.length}`], ["review", `Needs review · ${reviewCount}`]]
    .filter(([value]) => value === "all" || reviewCount)
    .map(([value, label]) => {
      const chip = element("button", `chip${value === "review" ? " chip-review" : ""}`, label);
      chip.type = "button";
      chip.setAttribute("aria-pressed", String(reviewFilter === value));
      chip.addEventListener("click", () => { reviewFilter = value; renderSections(entry); });
      return chip;
    }));

  let selectedNumber = sectionSelection.get(entry.id);
  if (!entry.sections.some((section) => section.number === selectedNumber)) {
    // Start with the first section that needs attention.
    selectedNumber = (entry.sections.find((section) => states.get(section.number) === "review") || entry.sections[0]).number;
    sectionSelection.set(entry.id, selectedNumber);
  }
  const visible = entry.sections.filter((section) => reviewFilter === "all" || states.get(section.number) === "review");
  list.replaceChildren(...visible.map((section) => {
    const state = states.get(section.number);
    const row = element("li", `section-row${section.number === selectedNumber ? " selected" : ""}`);
    const open = element("button", "row-select");
    open.type = "button";
    open.setAttribute("aria-current", String(section.number === selectedNumber));
    const dot = element("span", "state-dot");
    dot.dataset.state = state;
    dot.title = stateLabels[state];
    dot.setAttribute("role", "img");
    dot.setAttribute("aria-label", stateLabels[state]);
    open.append(dot, element("span", "row-number", String(section.number)), element("span", "row-text", section.text));
    open.addEventListener("click", () => selectSection(entry, section.number));
    row.append(open);
    if (section.audio) {
      const play = element("button", "row-play", "▶");
      play.type = "button";
      play.dataset.label = `Play section ${section.number}`;
      play.setAttribute("aria-label", play.dataset.label);
      play.addEventListener("click", () => playSection(entry, section, play));
      row.append(play);
    }
    return row;
  }));
  const section = entry.sections.find((item) => item.number === selectedNumber);
  editor.replaceChildren(...sectionEditor(entry, section, states.get(section.number)));
}

function sectionEditor(entry, section, state) {
  const key = `${entry.id}-${section.number}`;
  const nodes = [];
  const heading = element("div", "editor-heading");
  heading.append(element("h3", "", `Section ${section.number} of ${entry.sections.length}`));
  const index = entry.sections.indexOf(section);
  for (const [label, target] of [["Previous", entry.sections[index - 1]], ["Next", entry.sections[index + 1]]]) {
    const move = element("button", "button button-quiet", label);
    move.type = "button";
    move.disabled = !target;
    if (target) move.addEventListener("click", () => selectSection(entry, target.number));
    heading.append(move);
  }
  nodes.push(heading);

  if (section.audio) {
    const audio = element("audio");
    audio.controls = true;
    audio.preload = "none";
    audio.setAttribute("aria-label", `Section ${section.number} audio`);
    audio.src = `/api/jobs/${entry.id}/sections/${section.number}/audio?v=${section.audio}`;
    nodes.push(audio);
    const summary = checkSummary(section.check);
    if (summary) nodes.push(element("p", `check-result ${summary.className}`, summary.text));
  } else {
    nodes.push(element("p", "muted", state === "pending" ? "This section is waiting to be generated." : "No audio yet."));
  }
  const shownSpoken = section.pending ? section.spoken : section.takes?.find((take) => take.current)?.spoken;
  if (shownSpoken && shownSpoken !== section.text) {
    nodes.push(element("p", "muted section-metadata", `${section.pending ? "Will be spoken as" : "Spoken as"}: ${shownSpoken}`));
  }

  const label = element("label", "", "Text to speak");
  label.htmlFor = `section-text-${section.number}`;
  const field = element("textarea");
  field.id = label.htmlFor;
  field.rows = 3;
  field.maxLength = chunkLimit;
  field.value = sectionDrafts.get(key) ?? section.text;
  field.readOnly = entry.status !== "ready" || Boolean(entry.read_only);
  const redo = element("button", "button button-primary", "Regenerate section");
  redo.type = "button";
  const sounds = cuePicker(field, "result-error");
  const engine = voiceMatchEditor(`section-engine-${section.number}`, matchDrafts.get(key) ?? section.voice_match, field.readOnly);
  const directions = instructionsEditor(`section-instructions-${section.number}`,
    instructionDrafts.get(key) ?? section.instructions ?? "", field.readOnly);
  const overrideNote = element("p", "muted override-note",
    "A delivery style takes priority: this section uses VoxCPM2's directed mode without the reference transcript. Clear the style to use maximum resemblance.");
  const advanced = advancedEditor(`section-advanced-${section.number}`,
    turboDrafts.get(key) ?? { ...turboDefaults, ...(section.turbo_settings || {}), seed: 0 },
    () => { turboDrafts.set(key, advanced.value()); updateActions(); }, field.readOnly);
  const updateActions = () => {
    const directed = Boolean(directions.field.value.trim());
    const maximum = !directed && engine.field.value === "maximum";
    sounds.disabled = field.readOnly || directed || usesVoxcpm(engine.field.value);
    redo.disabled = field.readOnly || entry.voice_available === false || !field.value.trim() || (directed && !directedAvailable) || !advanced.valid()
      || (maximum && (!directedAvailable || !entry.voice_transcript_ready))
      || (engine.field.value.startsWith("trained") && !(directedAvailable && (entry.trained_voice || trainedVoice)));
    redo.title = maximum && !entry.voice_transcript_ready ? "Add the transcript for this recording's voice reference first." : "";
    overrideNote.hidden = !(directed && engine.field.value === "maximum");
  };
  field.addEventListener("input", () => { sectionDrafts.set(key, field.value); updateActions(); });
  directions.field.addEventListener("input", () => { instructionDrafts.set(key, directions.field.value); updateActions(); });
  engine.field.addEventListener("change", () => { matchDrafts.set(key, engine.field.value); updateActions(); });
  redo.addEventListener("click", async () => {
    redo.disabled = true;
    errorAt("result-error");
    try {
      await change(`/api/jobs/${entry.id}/sections/${section.number}`, {
        value: { text: field.value, instructions: directions.field.value, turbo_settings: advanced.value(),
          voice_match: engine.field.value },
      });
      for (const drafts of [sectionDrafts, instructionDrafts, turboDrafts, matchDrafts]) drafts.delete(key);
      await refreshJob();
    } catch (error) {
      errorAt("result-error", error.message);
      updateActions();
    }
  });
  const actions = element("div", "section-actions");
  actions.append(sounds, redo);
  if (entry.read_only) {
    // Admins review customer recordings without editing controls; the server refuses changes anyway.
    for (const node of [engine.box, directions.box, overrideNote, actions]) node.hidden = true;
  }
  nodes.push(label, field, engine.box, directions.box, overrideNote, actions);
  updateActions();

  // With a single take, the check result and words above already describe it.
  if (section.takes?.length > 1 || (section.pending && section.takes?.length)) nodes.push(takeChooser(entry, section, key));

  const more = element("details", "more-options");
  more.open = openMore.has(key);
  more.addEventListener("toggle", () => { if (more.open) openMore.add(key); else openMore.delete(key); });
  more.append(element("summary", "", "More options"));
  const details = section.generation || {};
  if (section.audio && isAdmin) {
    const source = details.engine
      ? [details.engine, details.device?.toUpperCase(), details.runtime].filter(Boolean).join(" · ")
      : "Engine details unavailable";
    const metadata = element("p", "muted section-metadata", `Voice reference: ${entry.voice_reference || "Unknown"} · ${source}`);
    if (details.model_sha256) {
      metadata.textContent += ` · Model ${details.model_sha256.slice(0, 12)}`;
      metadata.title = `Primary model SHA-256: ${details.model_sha256}`;
    }
    more.append(metadata);
  }
  if (index + 1 < entry.sections.length) more.append(pauseEditor(entry, section, key));
  more.append(advanced.box);
  nodes.push(more);
  return nodes;
}

function takeChooser(entry, section, key) {
  // Takes as small tabs; the chosen tab shows its words, check result and a restore button.
  const box = element("div", "takes");
  box.append(element("label", "", `Takes · ${section.takes.length}`));
  const tabs = element("div", "take-tabs");
  const detail = element("div", "take-detail");
  const currentId = section.takes.find((take) => take.current)?.id;
  let shown = takeSelection.get(key) ?? currentId ?? section.takes[0].id;
  if (!section.takes.some((take) => take.id === shown)) shown = section.takes[0].id;
  const show = (takeId) => {
    takeSelection.set(key, takeId);
    for (const tab of tabs.children) tab.setAttribute("aria-pressed", String(tab.dataset.take === takeId));
    const takeIndex = section.takes.findIndex((take) => take.id === takeId);
    const take = section.takes[takeIndex];
    const selectedTake = take.current && !section.pending;
    detail.replaceChildren();
    const info = [selectedTake ? "Current take" : `Take ${section.takes.length - takeIndex}`, take.engine, take.device?.toUpperCase()]
      .filter(Boolean).join(" · ");
    detail.append(element("p", "muted", info));
    const player = element("audio");
    player.controls = true;
    player.preload = "none";
    player.setAttribute("aria-label", `Section ${section.number} take ${section.takes.length - takeIndex}`);
    player.src = `/api/jobs/${entry.id}/sections/${section.number}/takes/${take.id}/audio`;
    detail.append(player);
    const takeCheck = checkSummary(take.check);
    if (takeCheck) detail.append(element("p", `check-result ${takeCheck.className}`, takeCheck.text));
    detail.append(element("p", "take-text", take.text ?? "Words for this older take are unavailable."));
    if (take.instructions) detail.append(element("p", "muted", `Instructions: ${take.instructions}`));
    if (take.turbo_settings && !take.instructions && !usesVoxcpm(take.voice_match || "standard")) {
      const setting = take.turbo_settings;
      detail.append(element("p", "muted", `Seed ${take.seed} · Temperature ${setting.temperature} · Top P ${setting.top_p} · Top K ${setting.top_k} · Repetition ${setting.repetition_penalty} · Reference normalization ${setting.normalize_reference ? "on" : "off"}`));
    }
    if (!selectedTake) {
      const restore = element("button", "button button-quiet", "Use this take");
      restore.type = "button";
      restore.disabled = !take.can_restore || !["ready", "failed"].includes(entry.status) || Boolean(entry.read_only);
      if (!take.can_restore) restore.title = "The words for this older take are unknown.";
      restore.addEventListener("click", async () => {
        restore.disabled = true;
        errorAt("result-error");
        try {
          await change(`/api/jobs/${entry.id}/sections/${section.number}/takes/${take.id}/restore`);
          for (const drafts of [sectionDrafts, instructionDrafts, turboDrafts, matchDrafts, takeSelection]) drafts.delete(key);
          await refreshJob();
        } catch (error) {
          errorAt("result-error", error.message);
          restore.disabled = false;
        }
      });
      detail.append(restore);
    }
  };
  section.takes.forEach((take, takeIndex) => {
    const status = take.check ? (take.check.status === "pass" ? " · passed" : " · review") : "";
    const tab = element("button", "take-tab", `${take.current && !section.pending ? "Current" : `Take ${section.takes.length - takeIndex}`}${status}`);
    tab.type = "button";
    tab.dataset.take = take.id;
    tab.addEventListener("click", () => show(take.id));
    tabs.append(tab);
  });
  box.append(tabs, detail);
  show(shown);
  return box;
}

function renderJob(entry) {
  const status = byId("result-status");
  const message = byId("result-message");
  const readOnly = Boolean(entry?.read_only);
  status.hidden = !entry;
  byId("owner-banner").hidden = !entry?.owner;
  if (entry?.owner) {
    byId("owner-name").textContent = entry.owner.name ? `${entry.owner.name} · ${entry.owner.email}` : entry.owner.email;
    byId("owner-disabled").hidden = !entry.owner.disabled;
  }
  byId("retry").hidden = entry?.status !== "failed" || readOnly;
  byId("retry").disabled = false;
  byId("balance").hidden = entry?.status !== "ready" || !entry?.export || readOnly;
  byId("balance").disabled = false;
  byId("check-recording").hidden = !(entry?.checks_available && entry?.status === "ready") || readOnly;
  byId("reuse-script").hidden = !entry || readOnly;
  byId("check-recording").disabled = false;
  const deliverable = isAdmin && !readOnly && entry?.status === "ready" && Boolean(entry?.export);
  byId("approval").hidden = !deliverable;
  byId("approve").hidden = !deliverable || entry.approved;
  byId("approve").disabled = false;
  byId("approve").textContent = entry?.source === "studio" ? "Send to outbox" : "Approve and send to outbox";
  byId("approval-note").textContent = !deliverable ? ""
    : entry.approved ? `Approved · in the outbox as ${(entry.delivered?.files || []).join(" and ")}`
    : entry.source !== "studio" ? `From the ${entry.source === "api" ? "local API" : "inbox"} · listen, then approve to deliver.`
    : "";
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
    const review = entry.sections.filter((section) => !section.pending && section.check && section.check.status !== "pass").length;
    const reviewNote = review ? ` · ${review} ${review === 1 ? "section needs" : "sections need"} review` : "";
    const voiceNote = entry.voice_name ? ` · ${entry.voice_name}` : "";
    message.textContent = `${entry.title}${voiceNote} · ${phrases[entry.status]}${reviewNote}`;
    if (entry.voice_available === false) {
      message.textContent += " · This voice is no longer available, so sections cannot be regenerated. Downloads still work.";
    }
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
    state = revision === voiceRevision ? snapshot : { ...state, jobs: snapshot.jobs, pronunciations: snapshot.pronunciations };
    renderVoice();
    renderPronunciations();
    const pending = state.jobs.filter((row) => ["queued", "running"].includes(row.status)).length;
    byId("queue-note").textContent = pending ? `${pending} ${pending === 1 ? "recording" : "recordings"} in progress` : "";
    byId("nav-queue").textContent = pending ? `${pending} in progress` : "";
    byId("nav-queue").hidden = !pending;
    updateEditor();
    await loadCustomerJobs();
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
// The owner's blind comparisons (2026-11-20) preferred the trained voice with the reference clip.
const scriptEngine = voiceMatchEditor("recording-engine", !isAdmin ? houseMatch : trainedVoice ? "trained_reference" : "standard");
const scriptAdvanced = advancedEditor("script-advanced-setting", turboDefaults, updateEditor);
byId("script-cue").append(scriptSounds);
byId("script-engine").append(scriptEngine.box);
const engineNote = element("p", "error");
engineNote.id = "engine-note";
engineNote.hidden = true;
scriptEngine.box.append(engineNote);
const engineOverride = element("p", "muted instructions-hint",
  "Sections with a delivery style use VoxCPM2's directed mode instead, without the reference transcript.");
engineOverride.id = "engine-override";
engineOverride.hidden = true;
scriptEngine.box.append(engineOverride);
byId("script-instructions").append(scriptDirections.box);
byId("script-advanced").append(scriptAdvanced.box);

let wordsTimer = null;
script.addEventListener("input", () => {
  updateEditor();
  window.clearTimeout(wordsTimer);
  wordsTimer = window.setTimeout(renderScriptWords, 300);
});
scriptDirections.field.addEventListener("input", updateEditor);
scriptEngine.field.addEventListener("change", () => {
  if (styleMode === "paragraphs") renderParagraphStyles();
  updateEditor();
});
byId("script-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (currentStep === 1) {
    setStep(2);
    return;
  }
  if (submitting) return;
  submitting = true;
  updateEditor();
  errorAt("script-error");
  try {
    const created = await change("/api/jobs", {
      value: styleMode === "paragraphs"
        ? { title: byId("recording-title").value, script: script.value,
            sections: paragraphs.flatMap((item) => item.sections.map((part) => ({ text: part.text, instructions: item.style.trim(), pause_ms: part.pause_ms }))),
            turbo_settings: scriptAdvanced.value(), voice_match: scriptEngine.field.value, auto_retries: Number(byId("auto-retries").value),
            voice_profile: isAdmin ? undefined : selectedProfile || undefined }
        : { title: byId("recording-title").value, script: script.value, instructions: scriptDirections.field.value,
            turbo_settings: scriptAdvanced.value(), voice_match: scriptEngine.field.value, auto_retries: Number(byId("auto-retries").value),
            voice_profile: isAdmin ? undefined : selectedProfile || undefined },
    });
    sectionSelection.delete(created.id);
    reviewFilter = "all";
    await selectJob(created.id);
    showView("recordings");
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
    if (script.value !== original) {
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
    button.disabled = scriptMode !== "edit";
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

byId("check-recording").addEventListener("click", async (event) => {
  const id = selected;
  event.target.disabled = true;
  try {
    await change(`/api/jobs/${id}/check`);
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

byId("approve").addEventListener("click", async (event) => {
  const id = selected;
  event.target.disabled = true;
  try {
    await change(`/api/jobs/${id}/approve`);
    await refreshJob();
    await refresh();
  } catch (error) {
    errorAt("result-error", error.message);
    event.target.disabled = false;
  }
});

byId("reuse-script").addEventListener("click", () => {
  if (!job) return;
  setStyleMode("whole");
  setScriptMode("edit");
  byId("recording-title").value = job.title;
  byId("imported-file").textContent = "";
  byId("import-notice").hidden = true;
  script.value = job.script;
  scriptDirections.field.value = job.instructions ?? "";
  showView("create");
  setStep(1);
  script.focus();
});

// ---- Voice training: record prompts for a fine-tuning dataset ----

let dataset = null;
let promptIndex = 0;
let recorder = null;
let meterFrame = 0;

const recorderWorklet = `
class Capture extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) this.port.postMessage(channel.slice(0));
    return true;
  }
}
registerProcessor("capture", Capture);
`;

async function loadDataset({ keepPosition = true } = {}) {
  try {
    const previous = dataset?.prompts[promptIndex]?.id;
    dataset = await request("/api/dataset");
    const found = keepPosition && previous ? dataset.prompts.findIndex((item) => item.id === previous) : -1;
    promptIndex = found >= 0 ? found : Math.max(0, dataset.prompts.findIndex((item) => !item.take));
    renderTraining();
  } catch (error) {
    errorAt("record-error", error.message);
  }
}

function renderTraining() {
  if (!dataset) return;
  const total = dataset.prompts.length;
  const prompt = dataset.prompts[promptIndex];
  byId("prompt-position").textContent = total ? `Prompt ${promptIndex + 1} of ${total}${prompt?.source === "custom" ? " · your script" : ""}` : "No prompts";
  byId("prompt-text").textContent = prompt ? prompt.text : "Add a script to create prompts.";
  byId("prompt-read-as").hidden = !prompt?.read_as;
  byId("prompt-read-as").textContent = prompt?.read_as ? `Read as: ${prompt.read_as}` : "";
  byId("prompt-previous").disabled = promptIndex <= 0;
  byId("prompt-next").disabled = promptIndex >= total - 1;
  byId("prompt-unrecorded").disabled = !dataset.prompts.some((item) => !item.take);
  byId("prompt-delete").hidden = prompt?.source !== "custom";
  const take = prompt?.take;
  byId("take-box").hidden = !take;
  if (take) {
    const audio = byId("take-audio");
    const source = `/api/dataset/takes/${take.id}/audio`;
    if (audio.getAttribute("src") !== source) audio.src = source;
    byId("take-kept").checked = take.kept;
    const check = take.check || {};
    const node = byId("take-check");
    node.className = `check-result ${check.status === "review" ? "check-review" : "check-pass"}`;
    const level = check.peak_db !== undefined ? ` · peak ${check.peak_db} dB · noise ${check.noise_db} dB` : "";
    node.textContent = check.status === "review"
      ? `${check.reasons.join(" ")}${check.heard ? ` Heard: “${check.heard}”` : ""}`
      : `${check.status === "pass" ? "Good take" : "Saved (words not checked)"} · ${take.duration.toFixed(1)} s${level}`;
  }
  const minutes = dataset.kept_minutes;
  byId("progress-bar").style.width = `${Math.min(100, (minutes / 60) * 100)}%`;
  byId("progress-text").textContent =
    `${minutes} ${minutes === 1 ? "minute" : "minutes"} kept · ${dataset.recorded} of ${total} prompts recorded`;
}

function movePrompt(index) {
  if (recorder) return;
  promptIndex = Math.max(0, Math.min(index, (dataset?.prompts.length || 1) - 1));
  byId("record-timer").textContent = "Press Record or the space bar, wait a moment, then read.";
  errorAt("record-error");
  renderTraining();
}

async function listMicrophones() {
  try {
    const select = byId("mic-select");
    const chosen = select.value;
    const devices = (await navigator.mediaDevices.enumerateDevices()).filter((device) => device.kind === "audioinput");
    select.replaceChildren(new Option("Default microphone", ""),
      ...devices.filter((device) => device.deviceId && device.deviceId !== "default")
        .map((device, index) => new Option(device.label || `Microphone ${index + 1}`, device.deviceId)));
    select.value = chosen;
  } catch (error) { /* Device names appear after microphone permission. */ }
}

function encodeWav(chunks, rate) {
  const length = chunks.reduce((total, chunk) => total + chunk.length, 0);
  const buffer = new ArrayBuffer(44 + length * 2);
  const view = new DataView(buffer);
  const text = (offset, value) => [...value].forEach((character, index) => view.setUint8(offset + index, character.charCodeAt(0)));
  text(0, "RIFF"); view.setUint32(4, 36 + length * 2, true); text(8, "WAVE"); text(12, "fmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  text(36, "data"); view.setUint32(40, length * 2, true);
  let offset = 44;
  for (const chunk of chunks) {
    for (let index = 0; index < chunk.length; index++, offset += 2) {
      const sample = Math.max(-1, Math.min(1, chunk[index]));
      view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    }
  }
  return new Blob([buffer], { type: "audio/wav" });
}

async function startRecording() {
  errorAt("record-error");
  if (!navigator.mediaDevices?.getUserMedia) {
    errorAt("record-error", "This browser cannot record audio. Open the studio in a current Chrome, Edge or Firefox.");
    return;
  }
  const deviceId = byId("mic-select").value;
  let stream;
  try {
    // Raw input: no echo cancellation, noise suppression or automatic gain.
    stream = await navigator.mediaDevices.getUserMedia({ audio: {
      deviceId: deviceId ? { exact: deviceId } : undefined, channelCount: 1,
      echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
  } catch (error) {
    errorAt("record-error", "Allow microphone access for this page, then try again.");
    return;
  }
  listMicrophones();
  const context = new AudioContext();
  const url = URL.createObjectURL(new Blob([recorderWorklet], { type: "text/javascript" }));
  await context.audioWorklet.addModule(url);
  URL.revokeObjectURL(url);
  const source = context.createMediaStreamSource(stream);
  const capture = new AudioWorkletNode(context, "capture");
  const analyser = context.createAnalyser();
  analyser.fftSize = 1024;
  const chunks = [];
  capture.port.onmessage = (event) => chunks.push(event.data);
  source.connect(analyser);
  source.connect(capture);
  recorder = { stream, context, chunks, started: performance.now(), prompt: dataset.prompts[promptIndex] };
  byId("record-button").setAttribute("aria-pressed", "true");
  byId("record-label").textContent = "Stop";
  const samples = new Float32Array(analyser.fftSize);
  const tick = () => {
    if (!recorder) return;
    analyser.getFloatTimeDomainData(samples);
    const peak = samples.reduce((max, value) => Math.max(max, Math.abs(value)), 0);
    byId("level-bar").style.width = `${Math.min(100, peak * 100)}%`;
    byId("level-bar").dataset.hot = String(peak > 0.95);
    const seconds = (performance.now() - recorder.started) / 1000;
    byId("record-timer").textContent = `Recording · ${seconds.toFixed(1)} s${seconds > 40 ? " · keep it under 45 s" : ""}`;
    if (seconds > 45) { stopRecording(); return; }
    meterFrame = requestAnimationFrame(tick);
  };
  tick();
}

async function stopRecording() {
  if (!recorder) return;
  const { stream, context, chunks, prompt } = recorder;
  recorder = null;
  cancelAnimationFrame(meterFrame);
  stream.getTracks().forEach((track) => track.stop());
  const rate = context.sampleRate;
  await context.close();
  byId("record-button").setAttribute("aria-pressed", "false");
  byId("record-label").textContent = "Record";
  byId("level-bar").style.width = "0";
  const blob = encodeWav(chunks, rate);
  const seconds = chunks.reduce((total, chunk) => total + chunk.length, 0) / rate;
  if (seconds < 1) {
    byId("record-timer").textContent = "That was too short. Press Record, wait a moment, then read the prompt.";
    return;
  }
  byId("record-timer").textContent = "Checking your take…";
  byId("record-button").disabled = true;
  try {
    const data = new FormData();
    data.append("audio", blob, "take.wav");
    await change(`/api/dataset/prompts/${prompt.id}/take`, { value: data });
    await loadDataset();
    const saved = dataset.prompts[promptIndex]?.take;
    byId("record-timer").textContent = saved?.check?.status === "review"
      ? "Have a listen. Record again if needed, or keep it anyway."
      : "Saved. Press Next unrecorded or the right arrow key for the next prompt.";
  } catch (error) {
    errorAt("record-error", error.message);
    byId("record-timer").textContent = "";
  } finally {
    byId("record-button").disabled = false;
  }
}

byId("record-button").addEventListener("click", () => (recorder ? stopRecording() : startRecording()));
byId("prompt-previous").addEventListener("click", () => movePrompt(promptIndex - 1));
byId("prompt-next").addEventListener("click", () => movePrompt(promptIndex + 1));
byId("prompt-unrecorded").addEventListener("click", () => {
  const prompts = dataset?.prompts || [];
  const after = prompts.findIndex((item, index) => index > promptIndex && !item.take);
  movePrompt(after >= 0 ? after : prompts.findIndex((item) => !item.take));
});
byId("take-kept").addEventListener("change", async (event) => {
  const take = dataset?.prompts[promptIndex]?.take;
  if (!take) return;
  try {
    await change(`/api/dataset/takes/${take.id}/kept`, { method: "PUT", value: { kept: event.target.checked } });
    await loadDataset();
  } catch (error) {
    errorAt("record-error", error.message);
  }
});
byId("prompt-delete").addEventListener("click", async () => {
  const prompt = dataset?.prompts[promptIndex];
  if (!prompt || !window.confirm("Delete this prompt and its recording?")) return;
  try {
    await change(`/api/dataset/prompts/${prompt.id}`, { method: "DELETE" });
    await loadDataset({ keepPosition: false });
  } catch (error) {
    errorAt("record-error", error.message);
  }
});
byId("prompt-add").addEventListener("click", async () => {
  const field = byId("prompt-import");
  if (!field.value.trim()) {
    byId("prompt-add-status").textContent = "Paste a script first.";
    return;
  }
  try {
    const result = await change("/api/dataset/prompts", { value: { text: field.value } });
    field.value = "";
    byId("prompt-add-status").textContent = `${result.added} prompts added at the end of the list.`;
    await loadDataset();
  } catch (error) {
    byId("prompt-add-status").textContent = error.message;
  }
});
byId("dataset-export").addEventListener("click", async (event) => {
  const output = byId("export-result");
  event.target.disabled = true;
  output.textContent = "Exporting…";
  try {
    const result = await change("/api/dataset/export");
    output.textContent = `Exported ${result.clips} clips (${result.minutes} minutes) to ${result.folder}`;
  } catch (error) {
    output.textContent = error.message;
  } finally {
    event.target.disabled = false;
  }
});
document.addEventListener("keydown", (event) => {
  // Space records and stops; the arrow keys move between prompts. Ignored while typing.
  if (currentView !== "training" || event.target.closest("input, textarea, select, button, audio")) return;
  if (event.code === "Space") { event.preventDefault(); byId("record-button").click(); }
  else if (event.key === "ArrowRight") movePrompt(promptIndex + 1);
  else if (event.key === "ArrowLeft") movePrompt(promptIndex - 1);
});

for (const link of document.querySelectorAll("[data-view]")) {
  link.addEventListener("click", () => showView(link.dataset.view));
}
for (const button of document.querySelectorAll(".step")) {
  button.addEventListener("click", () => {
    const step = Number(button.dataset.step);
    if (step === 3) showView("recordings");
    else setStep(step);
  });
}
byId("to-settings").addEventListener("click", () => setStep(2));
byId("back-to-step-1").addEventListener("click", () => setStep(1));
window.addEventListener("hashchange", () => showView(location.hash.slice(1), { remember: false }));

// Blind comparison: one sentence, two voice engines in random order, revealed after a choice.
const engineNames = {
  standard: "Chatterbox Turbo", maximum: "Maximum resemblance",
  trained: "Trained voice", trained_reference: "Trained voice + reference",
};
let comparisonSignature = "";
let comparisonTimer = null;

async function loadComparisons() {
  window.clearTimeout(comparisonTimer);
  let data;
  try {
    data = await request("/api/comparisons");
  } catch (error) {
    errorAt("compare-error", error.message);
    return;
  }
  const trained = data.trained_voice;
  byId("compare-voice").textContent = trained
    ? `Trained voice: ${trained.name}${trained.step != null ? ` · step ${trained.step}` : ""}`
    : "No trained voice yet";
  byId("compare-start").disabled = !trained || !directedAvailable;
  if (!directedAvailable) errorAt("compare-error", "Comparisons need the local VoxCPM2 setup described in the README.");
  const signature = JSON.stringify(data.comparisons);
  if (signature !== comparisonSignature) {
    comparisonSignature = signature;
    renderComparisons(data.comparisons);
  }
  const waiting = data.comparisons.some((item) => item.status === "queued" || item.status === "running");
  if (waiting && currentView === "training") comparisonTimer = window.setTimeout(loadComparisons, 2500);
}

function renderComparisons(items) {
  const list = byId("compare-list");
  list.replaceChildren();
  const decided = items.filter((item) => item.choice && item.engines);
  if (decided.length) {
    const wins = {};
    let same = 0;
    for (const item of decided) {
      if (item.choice === "same") same += 1;
      else wins[item.engines[item.choice]] = (wins[item.engines[item.choice]] || 0) + 1;
    }
    const parts = Object.entries(wins).map(([engine, count]) => `${engineNames[engine] || engine} ${count}`);
    if (same) parts.push(`no difference ${same}`);
    byId("compare-tally").textContent = `Your picks so far: ${parts.join(" · ")} (of ${decided.length})`;
  } else {
    byId("compare-tally").textContent = items.length ? "" : "No comparisons yet. Make a pair, listen to A and B, then pick.";
  }
  for (const item of items) {
    const row = element("li", "compare-item");
    row.append(element("p", "compare-sentence", item.text));
    if (item.status === "failed") {
      row.append(element("p", "error", item.error || "This comparison could not finish."));
    } else if (!item.ready) {
      row.append(element("p", "muted", item.status === "running" ? "Generating both clips…" : "Waiting in the queue…"));
    } else {
      const players = element("div", "compare-players");
      for (const [label, number] of [["A", 1], ["B", 2]]) {
        const box = element("div", "compare-clip");
        box.append(element("strong", "", `Clip ${label}`));
        const audio = element("audio");
        audio.controls = true;
        audio.preload = "none";
        audio.src = `/api/jobs/${item.id}/sections/${number}/audio`;
        audio.setAttribute("aria-label", `Clip ${label}`);
        box.append(audio);
        if (item.engines) {
          const picked = item.choice === label;
          box.append(element("span", picked ? "compare-engine picked" : "compare-engine",
            `${engineNames[item.engines[label]] || item.engines[label]}${picked ? " · your pick" : ""}`));
        }
        players.append(box);
      }
      row.append(players);
      if (!item.choice) {
        const actions = element("div", "form-footer compact");
        for (const [choice, text] of [["A", "A sounds more like the speaker"], ["B", "B sounds more like the speaker"], ["same", "Can't tell"]]) {
          const button = element("button", "button button-quiet", text);
          button.type = "button";
          button.addEventListener("click", async () => {
            try {
              await change(`/api/comparisons/${item.id}`, { method: "PUT", value: { choice } });
              errorAt("compare-error");
              await loadComparisons();
            } catch (error) {
              errorAt("compare-error", error.message);
            }
          });
          actions.append(button);
        }
        row.append(actions);
      } else if (item.choice === "same") {
        row.append(element("p", "muted", "You heard no difference."));
      }
    }
    list.append(row);
  }
}

byId("compare-start").addEventListener("click", async () => {
  const button = byId("compare-start");
  const baseline = byId("compare-baseline").value;
  const contender = byId("compare-contender").value;
  if (baseline === contender) {
    errorAt("compare-error", "Choose two different voice engines.");
    return;
  }
  button.disabled = true;
  errorAt("compare-error");
  try {
    await change("/api/comparisons", { value: { text: byId("compare-text").value, baseline, contender } });
    await loadComparisons();
  } catch (error) {
    errorAt("compare-error", error.message);
  } finally {
    button.disabled = false;
  }
});

// ---- Customer voice picker ----
let selectedProfile = null;

function renderVoicePicker(catalog) {
  const picker = byId("voice-picker");
  if (!picker) return;
  if (!catalog.some((voice) => voice.id === selectedProfile)) selectedProfile = catalog[0]?.id ?? null;
  byId("settings-voice").textContent = catalog.length ? "" : "No voices are available yet. Please check back soon.";
  picker.replaceChildren(...catalog.map((voice) => {
    const card = element("div", "voice-card");
    card.setAttribute("role", "radio");
    card.tabIndex = 0;
    const choose = () => {
      selectedProfile = voice.id;
      for (const other of picker.children) other.setAttribute("aria-checked", String(other === card));
    };
    card.setAttribute("aria-checked", String(voice.id === selectedProfile));
    card.addEventListener("click", (event) => { if (event.target.tagName !== "AUDIO") choose(); });
    card.addEventListener("keydown", (event) => {
      if (event.key === " " || event.key === "Enter") { event.preventDefault(); choose(); }
    });
    card.append(element("strong", "", voice.name));
    if (voice.description) card.append(element("p", "", voice.description));
    if (voice.sample) {
      const audio = element("audio");
      audio.controls = true;
      audio.preload = "none";
      audio.src = voice.sample;
      audio.setAttribute("aria-label", `Sample of ${voice.name}`);
      audio.addEventListener("play", choose);
      card.append(audio);
    }
    return card;
  }));
}

// ---- Voices (admin): catalog, consent and trained voices ----
const engineLabels = {
  trained_reference: "Trained voice + reference", maximum: "Maximum resemblance", standard: "Chatterbox Turbo",
};
let voiceData = null;
let editingVoice = null;
let voicesTimer = null;

async function loadVoices() {
  window.clearTimeout(voicesTimer);
  try {
    voiceData = await request("/api/voices");
    errorAt("voices-error");
    renderVoiceCatalog();
    renderTrainedVoices();
    if (voiceData.voices.some((voice) => ["queued", "running"].includes(voice.sample_status)) && currentView === "voices") {
      voicesTimer = window.setTimeout(loadVoices, 3000);
    }
  } catch (error) {
    errorAt("voices-error", error.message);
  }
}

function voiceAction(label, handler, quiet = true) {
  const button = element("button", quiet ? "button button-quiet" : "button button-primary", label);
  button.type = "button";
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await handler();
      await loadVoices();
    } catch (error) {
      errorAt("voices-error", error.message);
      button.disabled = false;
    }
  });
  return button;
}

function renderVoiceCatalog() {
  const list = byId("voice-list");
  if (!voiceData.voices.length) {
    list.replaceChildren(element("li", "muted", "No voices yet. Select New voice to add the first one."));
    return;
  }
  list.replaceChildren(...voiceData.voices.map((voice) => {
    const item = element("li", "voice-item");
    const title = element("h3", "", voice.name);
    const stateLabel = voice.consent_revoked_at ? ["revoked", "Consent revoked"] : voice.published ? ["published", "Published"] : ["", "Draft"];
    title.append(element("span", `voice-state ${stateLabel[0]}`, stateLabel[1]));
    item.append(title);
    if (voice.description) item.append(element("p", "muted", voice.description));
    const parts = [
      `Reference: ${voice.voice_label || "missing"}`,
      voice.trained_voice ? `Trained: ${voice.trained_voice}${voice.trained_missing ? " (missing)" : ""}` : "No trained voice",
      engineLabels[voice.engine],
      `${voice.recordings} ${voice.recordings === 1 ? "recording" : "recordings"}`,
    ];
    item.append(element("p", "muted", parts.join(" · ")));
    const consent = voice.consent_type === "self" ? "Own voice"
      : voice.consent_type === "written" ? `Written consent from ${voice.speaker || "?"}${voice.consent_date ? ` (${voice.consent_date})` : ""}`
      : "No consent recorded";
    const warn = voice.problem && !voice.consent_revoked_at;
    const consentLine = element("p", warn ? "error" : "muted", warn ? `${consent} · ${voice.problem}` : consent);
    if (voice.has_consent_file) {
      consentLine.append(" · ");
      const link = element("a", "", "view document");
      link.href = `/api/admin/voices/${voice.id}/consent`;
      link.target = "_blank";
      link.rel = "noopener";
      consentLine.append(link);
    }
    item.append(consentLine);
    if (voice.sample) {
      const audio = element("audio");
      audio.controls = true;
      audio.preload = "none";
      audio.src = voice.sample;
      item.append(audio);
    } else if (["queued", "running"].includes(voice.sample_status)) {
      item.append(element("p", "muted", "Making the sample…"));
    }
    const actions = element("div", "form-footer compact");
    if (!voice.consent_revoked_at) {
      actions.append(voiceAction(voice.published ? "Unpublish" : "Publish",
        () => change(`/api/admin/voices/${voice.id}/published`, { method: "PUT", value: { published: !voice.published } }),
        voice.published));
      actions.append(voiceAction("Edit", async () => openVoiceForm(voice)));
      actions.append(voiceAction(voice.sample ? "Remake sample" : "Make sample",
        () => change(`/api/admin/voices/${voice.id}/sample`, { value: {} })));
      actions.append(voiceAction("Revoke consent", async () => {
        if (!window.confirm(`Revoke consent for “${voice.name}”? It is unpublished for good, queued recordings with it stop, and customers can no longer regenerate with it.`)) return;
        await change(`/api/admin/voices/${voice.id}/revoke`);
      }));
    }
    if (!voice.published && !voice.recordings) {
      actions.append(voiceAction("Delete", async () => {
        if (!window.confirm(`Delete “${voice.name}”? The consent document stays on file.`)) return;
        await change(`/api/admin/voices/${voice.id}`, { method: "DELETE" });
      }));
    }
    item.append(actions);
    return item;
  }));
}

function renderTrainedVoices() {
  const trained = voiceData.trained;
  byId("trained-list").replaceChildren(...(trained.length
    ? trained.map((voice) => element("li", "", `${voice.name}${voice.step != null ? ` · step ${voice.step}` : ""}`))
    : [element("li", "", "None yet.")]));
}

function setConsentMode(mode) {
  for (const radio of document.querySelectorAll('input[name="consent"]')) radio.checked = radio.value === mode;
  byId("written-consent").hidden = mode !== "written";
}

function openVoiceForm(voice = null) {
  editingVoice = voice;
  byId("voice-form-panel").hidden = false;
  byId("voice-form-heading").textContent = voice ? `Edit ${voice.name}` : "New voice";
  byId("profile-name").value = voice?.name ?? "";
  byId("profile-description").value = voice?.description ?? "";
  byId("profile-reference").replaceChildren(...voiceData.references.map((reference) =>
    new Option(`${reference.label}${reference.has_transcript ? " · transcript saved" : ""}`, reference.id)));
  if (voice) byId("profile-reference").value = voice.voice_id;
  byId("profile-trained").replaceChildren(new Option("None", ""), ...voiceData.trained.map((trained) =>
    new Option(`${trained.name}${trained.step != null ? ` · step ${trained.step}` : ""}`, trained.name)));
  byId("profile-trained").value = voice ? voice.trained_voice ?? "" : voiceData.trained[0]?.name ?? "";
  setConsentMode(voice?.consent_type ?? "");
  byId("profile-speaker").value = voice?.speaker ?? "";
  byId("profile-consent-date").value = voice?.consent_date ?? "";
  byId("profile-consent-file").value = "";
  byId("consent-file-state").textContent = voice?.has_consent_file ? "A signed consent is on file. Choose a file only to replace it." : "";
  byId("profile-notes").value = voice?.consent_notes ?? "";
  errorAt("voice-form-error");
  byId("voice-form-panel").scrollIntoView({ behavior: "smooth", block: "start" });
  byId("profile-name").focus();
}

for (const radio of document.querySelectorAll('input[name="consent"]')) {
  radio.addEventListener("change", () => setConsentMode(radio.value));
}
byId("voice-new")?.addEventListener("click", () => {
  if (!voiceData?.references.length) {
    errorAt("voices-error", "Add a reference recording under References first.");
    return;
  }
  openVoiceForm();
});
byId("voice-cancel")?.addEventListener("click", () => { byId("voice-form-panel").hidden = true; editingVoice = null; });

byId("voice-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const save = byId("voice-save");
  const consent = document.querySelector('input[name="consent"]:checked')?.value;
  const value = {
    name: byId("profile-name").value, description: byId("profile-description").value,
    voice_id: byId("profile-reference").value, trained_voice: byId("profile-trained").value,
    consent_notes: byId("profile-notes").value,
  };
  if (consent) value.consent_type = consent;
  if (consent === "written") {
    value.speaker = byId("profile-speaker").value;
    if (byId("profile-consent-date").value) value.consent_date = byId("profile-consent-date").value;
  }
  save.disabled = true;
  errorAt("voice-form-error");
  try {
    const saved = editingVoice
      ? await change(`/api/admin/voices/${editingVoice.id}`, { method: "PUT", value })
      : await change("/api/admin/voices", { value });
    const file = byId("profile-consent-file").files[0];
    if (file && consent === "written") {
      const form = new FormData();
      form.append("file", file);
      await change(`/api/admin/voices/${saved.id}/consent`, { value: form });
    }
    byId("voice-form-panel").hidden = true;
    editingVoice = null;
    await loadVoices();
  } catch (error) {
    errorAt("voice-form-error", error.message);
  } finally {
    save.disabled = false;
  }
});

byId("trained-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = byId("trained-upload");
  const config = byId("trained-config").files[0];
  const weights = byId("trained-weights").files[0];
  if (!byId("trained-name").value.trim() || !config || !weights) {
    errorAt("trained-error", "Enter a name and choose both files.");
    return;
  }
  const form = new FormData();
  form.append("name", byId("trained-name").value.trim());
  form.append("config", config);
  form.append("weights", weights);
  button.disabled = true;
  errorAt("trained-error");
  byId("trained-status").textContent = `Uploading ${(weights.size / 1048576).toFixed(0)} MB…`;
  try {
    await change("/api/admin/trained", { value: form });
    byId("trained-form").reset();
    byId("trained-status").textContent = "Uploaded. Choose it when you create or edit a voice.";
    await loadVoices();
  } catch (error) {
    errorAt("trained-error", error.message);
    byId("trained-status").textContent = "";
  } finally {
    button.disabled = false;
  }
});

// ---- Accounts ----
byId("sign-out")?.addEventListener("click", async () => {
  try { await change("/api/auth/logout"); } finally { window.location.replace("/login"); }
});

// ---- Customers (admin): accounts and misuse review ----
let reviewUser = null;

byId("owner-account").addEventListener("click", () => {
  if (!job?.owner) return;
  reviewUser = { id: job.owner.id, email: job.owner.email };
  showView("customers");
});

function shortDate(value) {
  return value ? new Date(value).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "—";
}

async function loadCustomers() {
  try {
    const query = reviewUser ? `?user=${reviewUser.id}` : "";
    const [accounts, review] = await Promise.all([
      request("/api/admin/accounts"),
      reviewUser ? request(`/api/admin/accounts/${reviewUser.id}/usage`) : request(`/api/admin/recordings${query}`),
    ]);
    errorAt("customers-error");
    renderAccounts(accounts.accounts);
    renderReview(reviewUser ? review.usage : review.recordings);
  } catch (error) {
    errorAt("customers-error", error.message);
  }
}

function renderAccounts(accounts) {
  const customers = accounts.filter((account) => account.role === "customer");
  byId("accounts-summary").textContent = `${customers.length} ${customers.length === 1 ? "customer" : "customers"} · ${accounts.length - customers.length} admin`;
  const rows = accounts.map((account) => {
    const row = element("tr", account.disabled ? "disabled-row" : "");
    const who = element("td");
    who.append(element("strong", "", account.name || account.email));
    if (account.name) who.append(element("div", "muted", account.email));
    if (account.role === "admin") who.append(element("span", "admin-tag", "admin"));
    if (account.disabled) who.append(element("div", "error", "Disabled"));
    row.append(who, element("td", "", shortDate(account.created_at)), element("td", "", shortDate(account.last_login_at)),
      element("td", "", String(account.recordings)), element("td", "", String(account.generations)));
    const actions = element("td");
    const box = element("div", "form-footer compact");
    if (account.role === "customer") {
      const view = element("button", "button button-quiet", "Review scripts");
      view.type = "button";
      view.addEventListener("click", () => { reviewUser = account; loadCustomers(); });
      box.append(view);
    }
    const toggle = element("button", "button button-quiet", account.disabled ? "Enable" : "Disable");
    toggle.type = "button";
    toggle.addEventListener("click", () => updateAccount(account, { disabled: !account.disabled },
      account.disabled ? null : `Disable ${account.email}? They are signed out and their queued recordings stop.`));
    const role = element("button", "button button-quiet", account.role === "admin" ? "Make customer" : "Make admin");
    role.type = "button";
    role.addEventListener("click", () => updateAccount(account, { role: account.role === "admin" ? "customer" : "admin" },
      account.role === "admin" ? null : `Give ${account.email} full admin access, including voices and all customer files?`));
    box.append(toggle, role);
    actions.append(box);
    row.append(actions);
    return row;
  });
  byId("accounts-table").querySelector("tbody").replaceChildren(...rows);
}

async function updateAccount(account, value, question) {
  if (question && !window.confirm(question)) return;
  try {
    await change(`/api/admin/accounts/${account.id}`, { method: "PUT", value });
    await loadCustomers();
  } catch (error) {
    errorAt("customers-error", error.message);
  }
}

function renderReview(items) {
  const filter = byId("review-scope");
  filter.replaceChildren();
  if (reviewUser) {
    filter.append(`Showing ${reviewUser.email} · `);
    const all = element("button", "link-button", "Show all customers");
    all.type = "button";
    all.addEventListener("click", () => { reviewUser = null; loadCustomers(); });
    filter.append(all);
  }
  const list = byId("review-list");
  if (!items.length) {
    list.replaceChildren(element("li", "muted", "No customer scripts yet."));
    return;
  }
  list.replaceChildren(...items.map((item) => {
    const row = element("li", "compare-item");
    const heading = element("div", "form-footer compact");
    const label = reviewUser
      ? `${item.kind === "create" ? "New recording" : "Section edit"} · ${shortDate(item.created_at)}`
      : `${item.title} · ${item.email} · ${shortDate(item.created_at)}`;
    heading.append(element("strong", "", label));
    const jobId = item.job_id || item.id;
    if (!reviewUser || item.still_exists) {
      const open = element("button", "button button-quiet", "Open recording");
      open.type = "button";
      open.addEventListener("click", async () => {
        await setHistoryScope("customers");
        showView("recordings");
        await selectJob(jobId);
      });
      heading.append(open);
    } else {
      heading.append(element("span", "muted", "Recording deleted by the customer"));
    }
    row.append(heading, element("p", "review-script", item.text ?? item.script));
    return row;
  }));
}

// First visit: a short welcome that points to the guide. Browser storage may be unavailable.
try {
  byId("welcome").hidden = localStorage.getItem("studio-welcome-dismissed") === "1";
} catch (error) {
  byId("welcome").hidden = false;
}
byId("welcome-dismiss").addEventListener("click", () => {
  byId("welcome").hidden = true;
  try { localStorage.setItem("studio-welcome-dismissed", "1"); } catch (error) { /* Session only. */ }
});

showView(location.hash.slice(1) || "create", { remember: false });
setStep(1);
refresh();
window.setInterval(refresh, 1600);
