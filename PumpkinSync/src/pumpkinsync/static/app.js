'use strict';

const SHAPES = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'X'];
const FRAME_SECONDS = 1 / 30;
const MIN_CUE_SECONDS = 0.02;
const HISTORY_LIMIT = 200;
const SHAPE_COLORS = {
  A: '#8e7cc3', B: '#5fa8d3', C: '#f6a04d', D: '#ff5d3b', E: '#e9c46a',
  F: '#2a9d8f', G: '#c76b98', H: '#9bc53d', X: '#3a322c',
};
const shapeDescriptions = {
  A: 'Closed lips for P, B, and M',
  B: 'Slightly open — most consonants and the E in "bed"',
  C: 'Open vowel such as the E in "men" or A in "cat"',
  D: 'Wide open — the A in "father" or an emphatic syllable',
  E: 'Rounded — O as in "go" or ER as in "bird"',
  F: 'Puckered — OO as in "you" and W',
  G: 'Upper teeth on the lip — F and V',
  H: 'Tongue raised — L sounds',
  X: 'Rest / silence',
};

const state = {
  health: null,
  project: null,
  cues: [],
  waveform: [],
  template: null,
  lead: 0,
  selected: 0,
  history: [],
  future: [],
  saveTimer: null,
  saving: null,
  savePending: false,
  zoom: 1,
  viewStart: 0,
  drag: null,
  needsDraw: true,
  lastTime: -1,
  currentFrameUrl: null,
  frameCache: new Map(),
  analysisWatch: null,
};

const $ = (selector) => document.querySelector(selector);
const uploadForm = $('#uploadForm');
const fileInput = $('#audioFile');
const fileLabel = $('#fileLabel');
const dropZone = $('#dropZone');
const intensity = $('#intensity');
const intensityValue = $('#intensityValue');
const vocalOption = $('#vocalOption');
const separateVocals = $('#separateVocals');
const workspace = $('#workspace');
const audio = $('#audioPlayer');
const face = $('#facePreview');
const canvas = $('#waveform');
const cueTable = $('#cueTable');
const shapeGrid = $('#shapeGrid');
const shapeHelp = $('#shapeHelp');
const playButton = $('#playButton');

// ------------------------------------------------------------------ utilities

function formatTime(value, precise = false) {
  const safe = Number.isFinite(value) ? Math.max(0, value) : 0;
  const minutes = Math.floor(safe / 60);
  const seconds = safe - minutes * 60;
  return `${minutes}:${seconds.toFixed(precise ? 2 : 0).padStart(precise ? 5 : 2, '0')}`;
}

function formatBytes(bytes) {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function clamp(value, low, high) {
  return Math.max(low, Math.min(high, value));
}

function setStatus(element, message, error = false) {
  element.textContent = message;
  element.classList.toggle('error', error);
}

function setProgress(container, fraction) {
  if (fraction === null || fraction === undefined) {
    container.classList.add('hidden');
    return;
  }
  container.classList.remove('hidden');
  container.querySelector('.progress-bar').style.width = `${Math.round(clamp(fraction, 0, 1) * 100)}%`;
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const detail = payload && payload.detail;
    throw new Error(typeof detail === 'string' ? detail : `Request failed (${response.status})`);
  }
  return payload;
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function pollJob(url, onUpdate) {
  for (;;) {
    const job = await api(url);
    onUpdate(job);
    if (job.status === 'complete' || job.status === 'error') return job;
    await sleep(600);
  }
}

// --------------------------------------------------------------------- health

async function loadHealth() {
  try {
    const health = await api('/api/health');
    state.health = health;
    const extras = [
      health.ffmpeg ? 'FFmpeg ready' : 'FFmpeg missing',
      health.rhubarb ? 'Rhubarb ready' : 'built-in analyzer',
      health.demucs ? 'Demucs ready' : 'Demucs optional',
    ];
    const badge = $('#capabilityBadge');
    badge.textContent = `Local · ${extras.join(' · ')}`;
    badge.classList.toggle('warning', !health.ffmpeg);
    $('#ffmpegBanner').classList.toggle('hidden', health.ffmpeg);
    renderHealthPanel(health);
    fillTemplateSelect($('#templateSelect'), health.templates, health.template.id);
    const defaultTemplate = health.templates.find((item) => item.default) || health.templates[0];
    if (defaultTemplate && defaultTemplate.preview_url) $('#heroPreview').src = defaultTemplate.preview_url;
  } catch {
    $('#capabilityBadge').textContent = 'Local service unavailable';
  }
}

function renderHealthPanel(health) {
  const panel = $('#healthPanel');
  panel.replaceChildren();
  const rows = [
    ['FFmpeg / FFprobe', health.ffmpeg, health.ffmpeg ? 'Found' : 'Missing — required for analysis and export'],
    ['Rhubarb Lip Sync', health.rhubarb, health.rhubarb ? 'Found — used by the Auto analyzer' : 'Optional — run install_rhubarb_windows.ps1 or install_rhubarb_linux.sh'],
    ['Demucs vocal isolation', health.demucs, health.demucs ? 'Installed' : 'Optional — run setup_windows.ps1 -WithDemucs'],
  ];
  const list = document.createElement('ul');
  for (const [name, ok, detail] of rows) {
    const item = document.createElement('li');
    const dot = document.createElement('span');
    dot.className = `dot ${ok ? 'ok' : 'off'}`;
    const label = document.createElement('strong');
    label.textContent = name;
    const text = document.createElement('span');
    text.textContent = detail;
    item.append(dot, label, text);
    list.appendChild(item);
  }
  const templates = document.createElement('li');
  const tDot = document.createElement('span');
  tDot.className = 'dot ok';
  const tLabel = document.createElement('strong');
  tLabel.textContent = 'Face templates';
  const tText = document.createElement('span');
  tText.textContent = health.templates.map((item) => `${item.name}${item.default ? ' (default)' : ''}`).join(', ');
  templates.append(tDot, tLabel, tText);
  list.appendChild(templates);
  panel.appendChild(list);
  if (health.template_errors.length) {
    const errors = document.createElement('p');
    errors.className = 'error-text';
    errors.textContent = health.template_errors.join(' ');
    panel.appendChild(errors);
  }
  const version = document.createElement('p');
  version.className = 'muted';
  version.textContent = `PumpkinSync ${health.version}`;
  panel.appendChild(version);
}

function fillTemplateSelect(select, templates, selectedId) {
  select.replaceChildren();
  for (const template of templates) {
    const option = document.createElement('option');
    option.value = template.id;
    option.textContent = template.name;
    option.selected = template.id === selectedId;
    select.appendChild(option);
  }
}

$('#capabilityBadge').addEventListener('click', () => {
  const panel = $('#healthPanel');
  const open = panel.classList.toggle('hidden') === false;
  $('#capabilityBadge').setAttribute('aria-expanded', String(open));
});

// --------------------------------------------------------------------- import

function updateMode() {
  const song = document.querySelector('input[name="mode"]:checked').value === 'song';
  vocalOption.classList.toggle('enabled', song);
  separateVocals.disabled = !song;
  if (!song) separateVocals.checked = false;
}

document.querySelectorAll('input[name="mode"]').forEach((item) => item.addEventListener('change', updateMode));
intensity.addEventListener('input', () => { intensityValue.textContent = `${Number(intensity.value).toFixed(2)}×`; });
fileInput.addEventListener('change', () => { fileLabel.textContent = fileInput.files[0]?.name || 'Drop an audio file here'; });
['dragenter', 'dragover'].forEach((eventName) => dropZone.addEventListener(eventName, (event) => { event.preventDefault(); dropZone.classList.add('dragging'); }));
['dragleave', 'drop'].forEach((eventName) => dropZone.addEventListener(eventName, (event) => { event.preventDefault(); dropZone.classList.remove('dragging'); }));
dropZone.addEventListener('drop', (event) => {
  if (event.dataTransfer.files.length) {
    fileInput.files = event.dataTransfer.files;
    fileLabel.textContent = fileInput.files[0].name;
  }
});

function uploadWithProgress(url, form, onProgress) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open('POST', url);
    request.responseType = 'json';
    request.upload.addEventListener('progress', (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    });
    request.addEventListener('load', () => {
      const payload = request.response || {};
      if (request.status >= 200 && request.status < 300) resolve(payload);
      else reject(new Error(typeof payload.detail === 'string' ? payload.detail : `Upload failed (${request.status})`));
    });
    request.addEventListener('error', () => reject(new Error('The upload was interrupted. Is PumpkinSync still running?')));
    request.send(form);
  });
}

uploadForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!fileInput.files.length) return;
  const button = $('#analyzeButton');
  const status = $('#uploadStatus');
  const progress = $('#uploadProgress');
  button.disabled = true;
  const form = new FormData(uploadForm);
  if (!separateVocals.checked) form.set('separate_vocals', 'false');
  try {
    setProgress(progress, 0);
    setStatus(status, 'Uploading…');
    const started = await uploadWithProgress('/api/projects', form, (fraction) => {
      setProgress(progress, fraction * 0.25);
      setStatus(status, `Uploading… ${Math.round(fraction * 100)}%`);
    });
    loadProject(started.project);
    loadRecentProjects();
    const job = await watchAnalysis(started.job, started.project.id, (update) => {
      setProgress(progress, 0.25 + update.progress * 0.75);
      setStatus(status, update.message);
    });
    if (job.status === 'complete') {
      const warnings = (job.result && job.result.warnings) || [];
      setStatus(status, warnings.length ? warnings.join(' ') : job.message);
      fileInput.value = '';
      fileLabel.textContent = 'Drop an audio file here';
    } else {
      setStatus(status, job.error || 'Analysis failed', true);
    }
  } catch (error) {
    setStatus(status, error.message, true);
  } finally {
    setProgress(progress, null);
    button.disabled = false;
  }
});

// -------------------------------------------------------------- recent projects

async function loadRecentProjects() {
  const container = $('#recentProjects');
  try {
    const payload = await api('/api/projects');
    container.replaceChildren();
    if (!payload.projects.length) {
      const empty = document.createElement('p');
      empty.className = 'empty-recent';
      empty.textContent = 'No analyzed projects yet.';
      container.appendChild(empty);
      return;
    }
    payload.projects.forEach((project) => container.appendChild(recentCard(project)));
  } catch (error) {
    const failure = document.createElement('p');
    failure.className = 'empty-recent';
    failure.textContent = error.message;
    container.replaceChildren(failure);
  }
}

function recentCard(project) {
  const card = document.createElement('div');
  card.className = `recent-project status-${project.status}`;
  if (state.project && state.project.id === project.id) card.classList.add('current');
  const open = document.createElement('button');
  open.type = 'button';
  open.className = 'recent-open';
  const name = document.createElement('strong');
  name.textContent = project.original_name;
  const details = document.createElement('span');
  const statusText = project.status === 'analyzing' ? 'Analyzing…' : project.status === 'error' ? 'Needs attention' : project.analyzer;
  details.textContent = `${project.mode === 'song' ? 'Song' : 'Skit'} · ${formatTime(project.duration, true)} · ${statusText}`;
  open.append(name, details);
  open.title = project.error || `Open ${project.original_name}`;
  open.addEventListener('click', () => openProject(project.id));
  const remove = document.createElement('button');
  remove.type = 'button';
  remove.className = 'recent-delete';
  remove.setAttribute('aria-label', `Delete ${project.original_name}`);
  remove.title = 'Delete project';
  remove.textContent = '✕';
  remove.addEventListener('click', () => deleteProject(project));
  card.append(open, remove);
  return card;
}

async function deleteProject(project) {
  if (!window.confirm(`Delete "${project.original_name}" and all of its exports from this computer?`)) return;
  try {
    await api(`/api/projects/${project.id}`, { method: 'DELETE' });
    if (state.project && state.project.id === project.id) closeProject();
    loadRecentProjects();
  } catch (error) {
    setStatus($('#uploadStatus'), error.message, true);
  }
}

$('#refreshProjects').addEventListener('click', loadRecentProjects);

// -------------------------------------------------------------------- project

async function openProject(projectId) {
  try {
    const project = await api(`/api/projects/${projectId}`);
    loadProject(project);
    const active = project.active_jobs.find((job) => job.kind === 'analyze');
    if (active) {
      await watchAnalysis(active, project.id);
    }
    const exportJob = project.active_jobs.find((job) => job.kind === 'export');
    if (exportJob) followExport(exportJob);
  } catch (error) {
    setStatus($('#uploadStatus'), error.message, true);
    if (window.location.hash.includes(projectId)) history.replaceState(null, '', window.location.pathname);
  }
}

async function watchAnalysis(job, projectId, onUpdate = () => {}) {
  const token = {};
  state.analysisWatch = token;
  showOverlay('Analyzing…', job.message, job.progress);
  const final = await pollJob(job.job_url, (update) => {
    if (state.analysisWatch === token && state.project && state.project.id === projectId) {
      showOverlay('Analyzing…', update.message, update.progress);
    }
    onUpdate(update);
  });
  if (state.analysisWatch === token) {
    state.analysisWatch = null;
    if (state.project && state.project.id === projectId) {
      const project = await api(`/api/projects/${projectId}`);
      loadProject(project, { scroll: false });
    }
  }
  loadRecentProjects();
  return final;
}

function showOverlay(title, message, progress) {
  const overlay = $('#stageOverlay');
  overlay.classList.remove('hidden');
  $('#stageOverlayTitle').textContent = title;
  $('#stageOverlayMessage').textContent = message || '';
  overlay.querySelector('.progress-bar').style.width = `${Math.round(clamp(progress || 0, 0, 1) * 100)}%`;
}

function hideOverlay() {
  $('#stageOverlay').classList.add('hidden');
}

function loadProject(project, { scroll = true } = {}) {
  const switching = !state.project || state.project.id !== project.id;
  state.project = project;
  state.cues = project.cues.map((cue) => ({ ...cue }));
  state.waveform = project.waveform;
  state.template = project.template;
  state.lead = (project.settings.lead_ms || 0) / 1000;
  state.selected = 0;
  state.currentFrameUrl = null;
  state.needsDraw = true;
  if (switching) {
    state.history = [];
    state.future = [];
    state.zoom = 1;
    state.viewStart = 0;
    $('#zoomRange').value = '0';
    audio.pause();
    audio.src = project.audio_url;
  }
  if (!audio.src.endsWith(project.audio_url)) audio.src = project.audio_url;
  $('#projectTitle').textContent = project.original_name;
  const created = project.created_at ? new Date(project.created_at).toLocaleString() : '';
  $('#projectMeta').textContent = `${project.mode === 'song' ? 'Song' : 'Skit'} · ${formatTime(project.duration, true)} · ${project.analyzer} analyzer${created ? ` · imported ${created}` : ''}`;
  $('#durationLabel').textContent = formatTime(project.duration, true);
  renderNotice(project);
  fillProjectSettings(project);
  workspace.classList.remove('hidden');
  workspace.classList.toggle('no-cues', !state.cues.length);
  if (project.status === 'analyzing') showOverlay('Analyzing…', 'Waiting for analysis to start', 0);
  else hideOverlay();
  buildShapeGrid();
  preloadFrames(project.template);
  renderCueTable();
  if (state.cues.length) selectCue(0, false);
  renderExports(project.exports);
  $('#downloadTsv').href = `/api/projects/${project.id}/cuesheet?format=tsv`;
  $('#downloadJson').href = `/api/projects/${project.id}/cuesheet?format=json`;
  updateHistoryButtons();
  setSaveState('Saved', 'var(--success)');
  if (window.location.hash !== `#project=${project.id}`) history.replaceState(null, '', `#project=${project.id}`);
  if (switching && scroll) workspace.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function renderNotice(project) {
  const notice = $('#projectNotice');
  const messages = [];
  if (project.error) messages.push(project.status === 'error' ? `Analysis failed: ${project.error}` : project.error);
  messages.push(...project.warnings);
  notice.textContent = messages.join(' ');
  notice.classList.toggle('hidden', !messages.length);
  notice.classList.toggle('danger', project.status === 'error');
}

function fillProjectSettings(project) {
  const templates = (state.health && state.health.templates) || [project.template];
  fillTemplateSelect($('#projectTemplate'), templates, project.settings.template_id);
  $('#leadRange').value = String(project.settings.lead_ms || 0);
  $('#leadValue').textContent = `${Math.round(project.settings.lead_ms || 0)} ms`;
  $('#reMode').value = project.settings.mode;
  $('#reAnalyzer').value = project.settings.analyzer;
  $('#reIntensity').value = String(project.settings.intensity);
  $('#reIntensityValue').textContent = `${Number(project.settings.intensity).toFixed(2)}×`;
  $('#reSeparateVocals').checked = project.settings.separate_vocals;
  $('#reTranscript').value = project.transcript || '';
  updateReMode();
  $('#reanalyzePanel').open = project.status === 'error';
}

function closeProject() {
  audio.pause();
  audio.removeAttribute('src');
  audio.load();
  state.project = null;
  state.cues = [];
  state.analysisWatch = null;
  workspace.classList.add('hidden');
  history.replaceState(null, '', window.location.pathname);
  loadRecentProjects();
}

$('#closeProject').addEventListener('click', closeProject);

// ------------------------------------------------------------------ face frames

function preloadFrames(template) {
  const urls = [];
  for (const expressions of Object.values(template.frames)) {
    for (const poses of Object.values(expressions)) urls.push(...Object.values(poses));
  }
  for (const url of urls) {
    if (state.frameCache.has(url)) continue;
    const image = new Image();
    image.decoding = 'async';
    image.src = url;
    state.frameCache.set(url, image);
  }
}

function expressionFor(cue, index) {
  if (cue.shape === 'X') return 'normal';
  if (cue.shape === 'D' || cue.energy >= 0.82) return 'wide';
  if (cue.energy >= 0.52 && index % 5 === 0) return 'squint';
  return 'normal';
}

function poseFor(cue, index) {
  if (cue.shape === 'X') return 'center';
  return ['center', 'up', 'center', 'down'][index % 4];
}

function frameFor(cue, index) {
  const shape = state.template.frames[cue.shape] || state.template.frames.X;
  const expression = expressionFor(cue, index);
  const group = shape[expression] || shape.normal;
  const pose = poseFor(cue, index);
  return group[pose] || group.center;
}

function setFaceFrame(cue, index) {
  const url = frameFor(cue, index);
  if (url !== state.currentFrameUrl) {
    state.currentFrameUrl = url;
    face.src = url;
    $('#stageShape').textContent = cue.shape;
  }
}

// ----------------------------------------------------------------- cue helpers

function cueIndexAt(time) {
  let low = 0;
  let high = state.cues.length - 1;
  while (low <= high) {
    const middle = Math.floor((low + high) / 2);
    const cue = state.cues[middle];
    if (time < cue.start) high = middle - 1;
    else if (time >= cue.end && middle < state.cues.length - 1) low = middle + 1;
    else return middle;
  }
  return clamp(low, 0, state.cues.length - 1);
}

function editable() {
  return Boolean(state.project && state.project.status === 'ready' && state.cues.length);
}

function selectCue(index, seek = true) {
  if (!state.cues.length) return;
  state.selected = clamp(index, 0, state.cues.length - 1);
  const cue = state.cues[state.selected];
  if (seek) audio.currentTime = cue.start;
  setFaceFrame(cue, state.selected);
  $('#selectedShape').textContent = cue.shape;
  $('#selectedShape').style.color = SHAPE_COLORS[cue.shape] === SHAPE_COLORS.X ? 'var(--muted)' : SHAPE_COLORS[cue.shape];
  $('#selectedRange').textContent = `${cue.start.toFixed(2)}–${cue.end.toFixed(2)} s · ${Math.round((cue.end - cue.start) * 1000)} ms`;
  shapeHelp.textContent = shapeDescriptions[cue.shape];
  shapeGrid.querySelectorAll('.shape-button').forEach((button) => button.classList.toggle('active', button.dataset.shape === cue.shape));
  const previous = cueTable.querySelector('tr.selected');
  if (previous) previous.classList.remove('selected');
  const row = cueTable.rows[state.selected];
  if (row) {
    row.classList.add('selected');
    if (seek) row.scrollIntoView({ block: 'nearest' });
  }
  // While playing, the Follow option decides how the view moves.
  if (audio.paused) ensureVisible(cue.start, cue.end);
  state.needsDraw = true;
}

function snapshot() {
  return JSON.stringify(state.cues);
}

function pushHistory(saved = snapshot()) {
  state.history.push(saved);
  if (state.history.length > HISTORY_LIMIT) state.history.shift();
  state.future = [];
  updateHistoryButtons();
}

function updateHistoryButtons() {
  $('#undoButton').disabled = !state.history.length;
  $('#redoButton').disabled = !state.future.length;
}

function restore(from, to) {
  if (!from.length || !editable()) return;
  to.push(snapshot());
  state.cues = JSON.parse(from.pop());
  afterStructureChange();
}

function afterStructureChange({ save = true } = {}) {
  state.selected = clamp(state.selected, 0, state.cues.length - 1);
  state.currentFrameUrl = null;
  renderCueTable();
  selectCue(state.selected, false);
  updateHistoryButtons();
  if (save) scheduleSave();
}

function changeShape(shape) {
  if (!editable()) return;
  const cue = state.cues[state.selected];
  if (cue.shape === shape) return;
  pushHistory();
  cue.shape = shape;
  if (shape === 'X') cue.energy = 0;
  state.currentFrameUrl = null;
  const row = cueTable.rows[state.selected];
  if (row) {
    row.cells[2].textContent = shape;
    row.cells[2].style.color = shape === 'X' ? '' : SHAPE_COLORS[shape];
  }
  selectCue(state.selected, false);
  scheduleSave();
}

function splitAtPlayhead() {
  if (!editable()) return;
  const time = audio.currentTime;
  const index = cueIndexAt(time);
  const cue = state.cues[index];
  if (time - cue.start < MIN_CUE_SECONDS || cue.end - time < MIN_CUE_SECONDS) {
    setSaveState('Move the playhead inside a cue to split it', 'var(--muted)');
    return;
  }
  pushHistory();
  const tail = { ...cue, start: time, confidence: 1 };
  cue.end = time;
  state.cues.splice(index + 1, 0, tail);
  state.selected = index + 1;
  afterStructureChange();
}

function mergeWithNext() {
  if (!editable() || state.selected >= state.cues.length - 1) return;
  pushHistory();
  const cue = state.cues[state.selected];
  const next = state.cues[state.selected + 1];
  cue.end = next.end;
  cue.energy = Math.max(cue.energy || 0, next.energy || 0);
  state.cues.splice(state.selected + 1, 1);
  afterStructureChange();
}

function removeCue() {
  if (!editable() || state.cues.length < 2) return;
  pushHistory();
  const index = state.selected;
  const cue = state.cues[index];
  if (index === 0) {
    state.cues[1].start = 0;
  } else {
    state.cues[index - 1].end = cue.end;
    state.selected = index - 1;
  }
  state.cues.splice(index, 1);
  afterStructureChange();
}

function nudgeStart(delta) {
  if (!editable() || state.selected === 0) return;
  const cue = state.cues[state.selected];
  const previous = state.cues[state.selected - 1];
  const start = clamp(cue.start + delta, previous.start + MIN_CUE_SECONDS, cue.end - MIN_CUE_SECONDS);
  if (Math.abs(start - cue.start) < 1e-6) return;
  pushHistory();
  cue.start = start;
  previous.end = start;
  updateRowTimes(state.selected - 1);
  updateRowTimes(state.selected);
  selectCue(state.selected, false);
  audio.currentTime = start;
  scheduleSave();
}

// ----------------------------------------------------------------------- save

function setSaveState(text, color) {
  const element = $('#saveStatus');
  element.textContent = text;
  element.style.color = color;
}

function scheduleSave() {
  setSaveState('Unsaved…', 'var(--orange-bright)');
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(saveCues, 400);
}

async function saveCues() {
  clearTimeout(state.saveTimer);
  if (!state.project) return false;
  if (state.saving) {
    state.savePending = true;
    return state.saving;
  }
  const projectId = state.project.id;
  const body = JSON.stringify({
    cues: state.cues.map((cue) => ({
      start: Number(cue.start.toFixed(6)),
      end: Number(cue.end.toFixed(6)),
      shape: cue.shape,
      energy: clamp(Number(cue.energy) || 0, 0, 1),
      confidence: clamp(Number(cue.confidence ?? 1), 0, 1),
    })),
  });
  state.saving = (async () => {
    try {
      await api(`/api/projects/${projectId}/cues`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body,
      });
      if (state.project && state.project.id === projectId) setSaveState('Saved', 'var(--success)');
      return true;
    } catch (error) {
      setSaveState(error.message, 'var(--danger)');
      return false;
    }
  })();
  const result = await state.saving;
  state.saving = null;
  if (state.savePending) {
    state.savePending = false;
    return saveCues();
  }
  return result;
}

window.addEventListener('beforeunload', (event) => {
  if ($('#saveStatus').textContent.startsWith('Unsaved')) {
    saveCues();
    event.preventDefault();
  }
});

// ---------------------------------------------------------------------- table

function rowTimes(cue) {
  return `${cue.start.toFixed(2)}–${cue.end.toFixed(2)}`;
}

function renderCueTable() {
  const rows = state.cues.map((cue, index) => {
    const color = cue.shape === 'X' ? '' : ` style="color:${SHAPE_COLORS[cue.shape]}"`;
    return `<tr data-index="${index}"><td>${index + 1}</td><td>${rowTimes(cue)}</td><td${color}>${cue.shape}</td><td>${Math.round((cue.energy || 0) * 100)}%</td></tr>`;
  });
  cueTable.innerHTML = rows.join('');
  $('#cueCount').textContent = `${state.cues.length} cues`;
}

function updateRowTimes(index) {
  const row = cueTable.rows[index];
  if (row) row.cells[1].textContent = rowTimes(state.cues[index]);
}

cueTable.addEventListener('click', (event) => {
  const row = event.target.closest('tr');
  if (row) selectCue(Number(row.dataset.index));
});

// ----------------------------------------------------------------- shape grid

function buildShapeGrid() {
  shapeGrid.replaceChildren();
  for (const shape of SHAPES) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'shape-button';
    button.dataset.shape = shape;
    button.title = `${shape}: ${shapeDescriptions[shape]}`;
    const thumb = document.createElement('span');
    thumb.className = 'shape-thumb';
    thumb.style.backgroundImage = `url("${state.template.frames[shape].normal.center}")`;
    const letter = document.createElement('span');
    letter.className = 'shape-letter';
    letter.textContent = shape;
    letter.style.color = shape === 'X' ? '' : SHAPE_COLORS[shape];
    button.append(thumb, letter);
    button.addEventListener('click', () => changeShape(shape));
    shapeGrid.appendChild(button);
  }
}

// ------------------------------------------------------------------- waveform

function viewSpan() {
  const duration = state.project ? state.project.duration || 1 : 1;
  return duration / state.zoom;
}

function clampView() {
  const duration = state.project ? state.project.duration || 1 : 1;
  state.viewStart = clamp(state.viewStart, 0, Math.max(0, duration - viewSpan()));
}

function ensureVisible(start, end) {
  const span = viewSpan();
  if (start < state.viewStart || end > state.viewStart + span) {
    state.viewStart = end - start > span ? start : (start + end) / 2 - span / 2;
    clampView();
  }
}

function setZoom(zoom, anchorTime) {
  const span = viewSpan();
  const anchor = anchorTime ?? state.viewStart + span / 2;
  const ratio = (anchor - state.viewStart) / span;
  state.zoom = clamp(zoom, 1, 64);
  state.viewStart = anchor - ratio * viewSpan();
  clampView();
  $('#zoomRange').value = String(Math.log2(state.zoom));
  state.needsDraw = true;
}

function resizeCanvas() {
  const ratio = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(1, Math.round(rect.width * ratio));
  const height = Math.max(1, Math.round(rect.height * ratio));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  return { ratio, width, height };
}

function tickInterval(span) {
  const steps = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300];
  return steps.find((step) => span / step <= 10) || 600;
}

function drawWaveform() {
  if (!state.project) return;
  const { ratio, width, height } = resizeCanvas();
  const context = canvas.getContext('2d');
  const duration = state.project.duration || 1;
  const span = viewSpan();
  const start = state.viewStart;
  const end = start + span;
  const toX = (time) => ((time - start) / span) * width;
  const rulerHeight = 16 * ratio;
  const stripHeight = 26 * ratio;
  const waveTop = rulerHeight;
  const waveHeight = height - rulerHeight - stripHeight;
  const middle = waveTop + waveHeight / 2;

  context.fillStyle = '#0d0b0a';
  context.fillRect(0, 0, width, height);

  // Selected cue highlight.
  const selected = state.cues[state.selected];
  if (selected) {
    context.fillStyle = 'rgba(255, 122, 24, 0.13)';
    context.fillRect(toX(selected.start), waveTop, toX(selected.end) - toX(selected.start), waveHeight);
  }

  // Waveform peaks, one column per device pixel.
  const points = state.waveform.length;
  if (points) {
    context.strokeStyle = 'rgba(255, 178, 91, 0.6)';
    context.lineWidth = 1;
    context.beginPath();
    for (let x = 0; x < width; x += 1) {
      const t0 = start + (x / width) * span;
      const t1 = start + ((x + 1) / width) * span;
      const i0 = clamp(Math.floor((t0 / duration) * points), 0, points - 1);
      const i1 = clamp(Math.ceil((t1 / duration) * points), i0 + 1, points);
      let peak = 0;
      for (let i = i0; i < i1; i += 1) peak = Math.max(peak, state.waveform[i]);
      const half = Math.max(0.5, peak * waveHeight * 0.46);
      context.moveTo(x + 0.5, middle - half);
      context.lineTo(x + 0.5, middle + half);
    }
    context.stroke();
  }

  // Time ruler.
  const interval = tickInterval(span);
  context.fillStyle = 'rgba(255,255,255,.45)';
  context.font = `${10 * ratio}px system-ui, sans-serif`;
  context.textBaseline = 'top';
  context.strokeStyle = 'rgba(255,255,255,.12)';
  context.beginPath();
  for (let tick = Math.ceil(start / interval) * interval; tick <= end; tick += interval) {
    const x = Math.round(toX(tick)) + 0.5;
    context.moveTo(x, 0);
    context.lineTo(x, rulerHeight * 0.55);
    const label = interval < 1 ? tick.toFixed(1) : formatTime(tick);
    context.fillText(label, x + 3 * ratio, 2 * ratio);
  }
  context.stroke();

  // Cue boundaries and the shape strip.
  if (state.cues.length) {
    const first = cueIndexAt(start);
    const stripTop = height - stripHeight;
    context.textAlign = 'center';
    context.textBaseline = 'middle';
    context.font = `700 ${11 * ratio}px system-ui, sans-serif`;
    for (let index = first; index < state.cues.length; index += 1) {
      const cue = state.cues[index];
      if (cue.start > end) break;
      const x0 = toX(cue.start);
      const x1 = toX(cue.end);
      context.fillStyle = SHAPE_COLORS[cue.shape] || '#555';
      context.globalAlpha = index === state.selected ? 1 : 0.72;
      context.fillRect(x0, stripTop + 2 * ratio, Math.max(1, x1 - x0 - 1), stripHeight - 4 * ratio);
      context.globalAlpha = 1;
      if (x1 - x0 > 12 * ratio) {
        context.fillStyle = cue.shape === 'X' ? 'rgba(255,255,255,.5)' : '#140900';
        context.fillText(cue.shape, (Math.max(x0, 0) + Math.min(x1, width)) / 2, stripTop + stripHeight / 2);
      }
      if (index > 0) {
        const edge = Math.round(x0) + 0.5;
        const active = state.drag && state.drag.index === index;
        context.strokeStyle = active ? '#fff3e5' : index === state.selected || index === state.selected + 1 ? 'rgba(255,178,91,.7)' : 'rgba(255,255,255,.1)';
        context.lineWidth = active ? 2 * ratio : 1;
        context.beginPath();
        context.moveTo(edge, waveTop);
        context.lineTo(edge, stripTop);
        context.stroke();
      }
    }
    context.textAlign = 'start';
  }

  // Playhead.
  const playhead = toX(audio.currentTime);
  if (playhead >= 0 && playhead <= width) {
    context.strokeStyle = '#fff3e5';
    context.lineWidth = Math.max(1.5, 1.5 * ratio);
    context.beginPath();
    context.moveTo(playhead, 0);
    context.lineTo(playhead, height);
    context.stroke();
  }
}

function canvasTime(event) {
  const rect = canvas.getBoundingClientRect();
  return state.viewStart + ((event.clientX - rect.left) / rect.width) * viewSpan();
}

function boundaryNear(event) {
  if (!editable()) return -1;
  const rect = canvas.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const span = viewSpan();
  const first = cueIndexAt(state.viewStart);
  for (let index = Math.max(1, first); index < state.cues.length; index += 1) {
    const cue = state.cues[index];
    if (cue.start > state.viewStart + span) break;
    const edge = ((cue.start - state.viewStart) / span) * rect.width;
    if (Math.abs(edge - x) <= 6) return index;
  }
  return -1;
}

canvas.addEventListener('pointerdown', (event) => {
  if (!state.project || !state.cues.length) return;
  const boundary = boundaryNear(event);
  if (boundary > 0) {
    canvas.setPointerCapture(event.pointerId);
    state.drag = { index: boundary, before: snapshot(), moved: false };
    state.selected = boundary;
    selectCue(boundary, false);
    return;
  }
  const time = clamp(canvasTime(event), 0, state.project.duration);
  audio.currentTime = time;
  selectCue(cueIndexAt(time), false);
});

canvas.addEventListener('pointermove', (event) => {
  if (!state.drag) {
    canvas.style.cursor = boundaryNear(event) > 0 ? 'col-resize' : 'crosshair';
    return;
  }
  const { index } = state.drag;
  const previous = state.cues[index - 1];
  const cue = state.cues[index];
  const time = clamp(canvasTime(event), previous.start + MIN_CUE_SECONDS, cue.end - MIN_CUE_SECONDS);
  if (!state.drag.moved) {
    pushHistory(state.drag.before);
    state.drag.moved = true;
  }
  previous.end = time;
  cue.start = time;
  $('#selectedRange').textContent = `${cue.start.toFixed(2)}–${cue.end.toFixed(2)} s · ${Math.round((cue.end - cue.start) * 1000)} ms`;
  state.needsDraw = true;
});

function endDrag() {
  if (!state.drag) return;
  const { index, moved } = state.drag;
  state.drag = null;
  if (moved) {
    updateRowTimes(index - 1);
    updateRowTimes(index);
    audio.currentTime = state.cues[index].start;
    selectCue(index, false);
    scheduleSave();
  }
  state.needsDraw = true;
}

canvas.addEventListener('pointerup', endDrag);
canvas.addEventListener('pointercancel', endDrag);

canvas.addEventListener('wheel', (event) => {
  if (!state.project) return;
  if (event.ctrlKey || event.metaKey) {
    event.preventDefault();
    setZoom(state.zoom * (event.deltaY < 0 ? 1.25 : 0.8), canvasTime(event));
  } else if (state.zoom > 1) {
    event.preventDefault();
    const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
    state.viewStart += (delta / canvas.getBoundingClientRect().width) * viewSpan();
    clampView();
    state.needsDraw = true;
  }
}, { passive: false });

$('#zoomRange').addEventListener('input', (event) => {
  const anchor = audio.currentTime >= state.viewStart && audio.currentTime <= state.viewStart + viewSpan() ? audio.currentTime : undefined;
  setZoom(2 ** Number(event.target.value), anchor);
});

// ------------------------------------------------------------------- playback

function togglePlay() {
  if (!state.project || !state.cues.length) return;
  if (audio.paused) audio.play().catch((error) => setSaveState(`Playback failed: ${error.message}`, 'var(--danger)'));
  else audio.pause();
}

audio.addEventListener('play', () => { playButton.textContent = '❚❚'; playButton.setAttribute('aria-label', 'Pause'); });
audio.addEventListener('pause', () => { playButton.textContent = '▶'; playButton.setAttribute('aria-label', 'Play'); state.needsDraw = true; });
audio.addEventListener('ended', () => { state.needsDraw = true; });
audio.addEventListener('loadedmetadata', () => { state.needsDraw = true; });
playButton.addEventListener('click', togglePlay);
$('#backButton').addEventListener('click', () => { audio.currentTime = Math.max(0, audio.currentTime - 2); });
$('#speedSelect').addEventListener('change', (event) => { audio.playbackRate = Number(event.target.value); });

function tick() {
  if (state.project && state.cues.length) {
    const time = audio.currentTime;
    if (time !== state.lastTime) {
      state.lastTime = time;
      if (!state.drag) {
        const index = cueIndexAt(time);
        if (index !== state.selected && !audio.paused) selectCue(index, false);
      }
      if (!audio.paused) {
        // Mouth lead: show the shape that is about to be heard.
        const shown = cueIndexAt(time + state.lead);
        setFaceFrame(state.cues[shown], shown);
        if ($('#followPlayhead').checked && state.zoom > 1) {
          const span = viewSpan();
          if (time > state.viewStart + span * 0.9 || time < state.viewStart) {
            state.viewStart = time - span * 0.1;
            clampView();
          }
        }
      } else {
        const cue = state.cues[state.selected];
        if (cue) setFaceFrame(cue, state.selected);
      }
      $('#currentTime').textContent = formatTime(time, true);
      state.needsDraw = true;
    }
    if (state.needsDraw) {
      state.needsDraw = false;
      drawWaveform();
    }
  }
  requestAnimationFrame(tick);
}

window.addEventListener('resize', () => { state.needsDraw = true; });

// ---------------------------------------------------------------- edit buttons

$('#previousCue').addEventListener('click', () => selectCue(state.selected - 1));
$('#nextCue').addEventListener('click', () => selectCue(state.selected + 1));
$('#splitCue').addEventListener('click', splitAtPlayhead);
$('#mergeCue').addEventListener('click', mergeWithNext);
$('#undoButton').addEventListener('click', () => restore(state.history, state.future));
$('#redoButton').addEventListener('click', () => restore(state.future, state.history));
$('#nudgeEarlier').addEventListener('click', () => nudgeStart(-FRAME_SECONDS));
$('#nudgeLater').addEventListener('click', () => nudgeStart(FRAME_SECONDS));

window.addEventListener('keydown', (event) => {
  if (!state.project || workspace.classList.contains('hidden')) return;
  const target = event.target;
  if (target.matches('input, textarea, select') || target.isContentEditable) return;
  const key = event.key;
  const upper = key.length === 1 ? key.toUpperCase() : key;
  if (event.ctrlKey || event.metaKey) {
    if (upper === 'Z') {
      event.preventDefault();
      if (event.shiftKey) restore(state.future, state.history);
      else restore(state.history, state.future);
    } else if (upper === 'Y') {
      event.preventDefault();
      restore(state.future, state.history);
    }
    return;
  }
  if (event.altKey) return;
  if (SHAPES.includes(upper)) {
    event.preventDefault();
    changeShape(upper);
  } else if (key === ' ') {
    event.preventDefault();
    if (document.activeElement instanceof HTMLButtonElement) document.activeElement.blur();
    togglePlay();
  } else if (key === 'ArrowLeft' || key === 'ArrowRight') {
    event.preventDefault();
    const direction = key === 'ArrowLeft' ? -1 : 1;
    if (event.shiftKey) nudgeStart(direction * FRAME_SECONDS);
    else selectCue(state.selected + direction);
  } else if (upper === 'S') {
    event.preventDefault();
    splitAtPlayhead();
  } else if (upper === 'M') {
    event.preventDefault();
    mergeWithNext();
  } else if (key === 'Delete' || key === 'Backspace') {
    event.preventDefault();
    removeCue();
  } else if (key === '+' || key === '=') {
    setZoom(state.zoom * 1.5, audio.currentTime);
  } else if (key === '-' || key === '_') {
    setZoom(state.zoom / 1.5, audio.currentTime);
  } else if (key === '?') {
    const help = $('#keyboardHelp');
    help.open = !help.open;
  }
});

// ------------------------------------------------------------ project settings

$('#projectTemplate').addEventListener('change', async (event) => {
  try {
    const project = await api(`/api/projects/${state.project.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ template_id: event.target.value }),
    });
    state.project.settings = project.settings;
    state.project.template = project.template;
    state.template = project.template;
    state.currentFrameUrl = null;
    buildShapeGrid();
    preloadFrames(project.template);
    selectCue(state.selected, false);
  } catch (error) {
    setSaveState(error.message, 'var(--danger)');
  }
});

$('#leadRange').addEventListener('input', (event) => {
  state.lead = Number(event.target.value) / 1000;
  $('#leadValue').textContent = `${event.target.value} ms`;
});

$('#leadRange').addEventListener('change', async (event) => {
  try {
    const project = await api(`/api/projects/${state.project.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lead_ms: Number(event.target.value) }),
    });
    state.project.settings = project.settings;
  } catch (error) {
    setSaveState(error.message, 'var(--danger)');
  }
});

function updateReMode() {
  const song = $('#reMode').value === 'song';
  $('#reVocalOption').classList.toggle('enabled', song);
  $('#reSeparateVocals').disabled = !song;
  if (!song) $('#reSeparateVocals').checked = false;
}

$('#reMode').addEventListener('change', updateReMode);
$('#reIntensity').addEventListener('input', (event) => { $('#reIntensityValue').textContent = `${Number(event.target.value).toFixed(2)}×`; });

$('#reanalyzeButton').addEventListener('click', async () => {
  if (!state.project) return;
  const replacing = state.cues.length ? ' Your current cue edits will be replaced (you can export a cue sheet first to keep a copy).' : '';
  if (!window.confirm(`Re-analyze "${state.project.original_name}"?${replacing}`)) return;
  const button = $('#reanalyzeButton');
  button.disabled = true;
  try {
    await saveCues();
    const started = await api(`/api/projects/${state.project.id}/analyze`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        mode: $('#reMode').value,
        analyzer: $('#reAnalyzer').value,
        intensity: Number($('#reIntensity').value),
        separate_vocals: $('#reSeparateVocals').checked,
        transcript: $('#reTranscript').value,
      }),
    });
    state.project.status = 'analyzing';
    const job = await watchAnalysis(started.job, state.project.id);
    if (job.status === 'error') setSaveState(job.error || 'Analysis failed', 'var(--danger)');
  } catch (error) {
    setSaveState(error.message, 'var(--danger)');
  } finally {
    button.disabled = false;
  }
});

// --------------------------------------------------------------------- export

function renderExports(exports) {
  const list = $('#exportList');
  list.replaceChildren();
  for (const item of exports || []) {
    const row = document.createElement('li');
    const link = document.createElement('a');
    link.href = item.download_url;
    link.download = item.name;
    link.textContent = item.name;
    const meta = document.createElement('span');
    meta.textContent = `${formatBytes(item.size)} · ${new Date(item.modified).toLocaleString()}`;
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'text-button';
    remove.textContent = 'Delete';
    remove.addEventListener('click', async () => {
      if (!window.confirm(`Delete ${item.name}?`)) return;
      try {
        await api(item.download_url, { method: 'DELETE' });
        refreshExports();
      } catch (error) {
        setStatus($('#exportStatus'), error.message, true);
      }
    });
    row.append(link, meta, remove);
    list.appendChild(row);
  }
}

async function refreshExports() {
  if (!state.project) return;
  const payload = await api(`/api/projects/${state.project.id}/exports`);
  renderExports(payload.exports);
}

async function startExport(quality) {
  if (!state.project) return;
  const status = $('#exportStatus');
  setStatus(status, quality === 'full' ? 'Starting full 1080p render…' : 'Starting quick proof render…');
  try {
    const saved = await saveCues();
    if (!saved) throw new Error('Cue changes could not be saved; export was not started.');
    const payload = await api(`/api/projects/${state.project.id}/export`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ quality }),
    });
    await followExport(payload.job);
  } catch (error) {
    setStatus(status, error.message, true);
    setProgress($('#exportProgress'), null);
  }
}

async function followExport(job) {
  const status = $('#exportStatus');
  const progress = $('#exportProgress');
  const projectId = job.project_id;
  const final = await pollJob(job.job_url, (update) => {
    if (!state.project || state.project.id !== projectId) return;
    setProgress(progress, update.progress);
    setStatus(status, update.message || update.status, update.status === 'error');
  });
  setProgress(progress, null);
  if (!state.project || state.project.id !== projectId) return;
  if (final.status === 'complete') {
    setStatus(status, `Export complete: ${final.output_name}`);
    await refreshExports();
  } else {
    setStatus(status, final.error || 'Export failed', true);
  }
}

$('#previewExport').addEventListener('click', () => startExport('preview'));
$('#fullExport').addEventListener('click', () => startExport('full'));

$('#importCues').addEventListener('change', async (event) => {
  const file = event.target.files[0];
  event.target.value = '';
  if (!file || !state.project) return;
  const form = new FormData();
  form.append('file', file);
  try {
    await saveCues();
    const before = snapshot();
    const payload = await api(`/api/projects/${state.project.id}/cuesheet`, { method: 'POST', body: form });
    pushHistory(before);
    state.cues = payload.cues;
    state.selected = 0;
    afterStructureChange({ save: false });
    setSaveState(`Imported ${payload.cues.length} cues from ${file.name}`, 'var(--success)');
  } catch (error) {
    setSaveState(error.message, 'var(--danger)');
  }
});

// ---------------------------------------------------------------------- start

updateMode();
loadHealth().then(() => {
  const match = window.location.hash.match(/project=([0-9a-f]{32})/);
  if (match) openProject(match[1]);
});
loadRecentProjects();
requestAnimationFrame(tick);
