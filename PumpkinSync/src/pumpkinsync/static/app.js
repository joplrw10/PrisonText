const state = {
  project: null,
  cues: [],
  waveform: [],
  template: null,
  selected: 0,
  saveTimer: null,
  animationFrame: null,
  currentFrameUrl: null,
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

const shapeDescriptions = {
  A: 'Closed / impact consonants such as P, B, and M',
  B: 'Narrow mouth for E sounds and light consonants',
  C: 'Moderately open vowel',
  D: 'Wide-open A or emphatic syllable',
  E: 'Rounded O / ER mouth',
  F: 'Small rounded OO / W mouth',
  G: 'F / V-style narrow articulation',
  H: 'L / tongue-style articulation',
  X: 'Rest / silence',
};

function formatTime(value, precise = false) {
  const safe = Number.isFinite(value) ? Math.max(0, value) : 0;
  const minutes = Math.floor(safe / 60);
  const seconds = safe - minutes * 60;
  return `${minutes}:${seconds.toFixed(precise ? 2 : 0).padStart(precise ? 5 : 2, '0')}`;
}

function setStatus(element, message, error = false) {
  element.textContent = message;
  element.classList.toggle('error', error);
}

async function loadHealth() {
  try {
    const response = await fetch('/api/health');
    const health = await response.json();
    const extras = [health.rhubarb ? 'Rhubarb ready' : 'built-in analyzer', health.demucs ? 'Demucs ready' : 'Demucs optional'];
    $('#capabilityBadge').textContent = `Local · ${extras.join(' · ')}`;
  } catch {
    $('#capabilityBadge').textContent = 'Local service unavailable';
  }
}

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

uploadForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!fileInput.files.length) return;
  const button = $('#analyzeButton');
  button.disabled = true;
  setStatus($('#uploadStatus'), separateVocals.checked ? 'Uploading, isolating vocals, and analyzing… this can take several minutes.' : 'Uploading and analyzing locally…');
  const form = new FormData(uploadForm);
  if (!separateVocals.checked) form.set('separate_vocals', 'false');
  try {
    const response = await fetch('/api/projects', { method: 'POST', body: form });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || 'Analysis failed');
    loadProject(payload);
    loadRecentProjects();
    setStatus($('#uploadStatus'), payload.warnings.length ? payload.warnings.join(' ') : `Ready: ${payload.cues.length} cues generated with ${payload.analyzer}.`);
  } catch (error) {
    setStatus($('#uploadStatus'), error.message, true);
  } finally {
    button.disabled = false;
  }
});

async function loadRecentProjects() {
  const container = $('#recentProjects');
  try {
    const response = await fetch('/api/projects');
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || 'Could not load recent projects');
    container.innerHTML = '';
    if (!payload.projects.length) {
      const empty = document.createElement('p');
      empty.className = 'empty-recent';
      empty.textContent = 'No analyzed projects yet.';
      container.appendChild(empty);
      return;
    }
    payload.projects.forEach((project) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'recent-project';
      const name = document.createElement('strong');
      name.textContent = project.original_name;
      const details = document.createElement('span');
      details.textContent = `${project.mode === 'song' ? 'Song' : 'Skit'} · ${formatTime(project.duration, true)} · ${project.analyzer}`;
      button.append(name, details);
      button.addEventListener('click', async () => {
        button.disabled = true;
        try {
          const projectResponse = await fetch(project.project_url);
          const projectPayload = await projectResponse.json();
          if (!projectResponse.ok) throw new Error(projectPayload.detail || 'Could not reopen project');
          loadProject(projectPayload);
        } catch (error) {
          setStatus($('#uploadStatus'), error.message, true);
        } finally {
          button.disabled = false;
        }
      });
      container.appendChild(button);
    });
  } catch (error) {
    container.innerHTML = '';
    const failure = document.createElement('p');
    failure.className = 'empty-recent';
    failure.textContent = error.message;
    container.appendChild(failure);
  }
}

function loadProject(project) {
  state.project = project;
  state.cues = project.cues;
  state.waveform = project.waveform;
  state.template = project.template;
  state.selected = 0;
  state.currentFrameUrl = null;
  audio.src = project.audio_url;
  $('#durationLabel').textContent = formatTime(project.duration, true);
  workspace.classList.remove('hidden');
  buildShapeGrid();
  renderCueTable();
  selectCue(0, false);
  drawWaveform();
  workspace.scrollIntoView({ behavior: 'smooth', block: 'start' });
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
  }
}

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
  return Math.max(0, Math.min(state.cues.length - 1, low));
}

function selectCue(index, seek = true) {
  if (!state.cues.length) return;
  state.selected = Math.max(0, Math.min(state.cues.length - 1, index));
  const cue = state.cues[state.selected];
  if (seek) audio.currentTime = cue.start;
  setFaceFrame(cue, state.selected);
  $('#selectedShape').textContent = cue.shape;
  $('#selectedRange').textContent = `${cue.start.toFixed(2)}–${cue.end.toFixed(2)} s`;
  shapeHelp.textContent = shapeDescriptions[cue.shape];
  document.querySelectorAll('.shape-button').forEach((button) => button.classList.toggle('active', button.dataset.shape === cue.shape));
  document.querySelectorAll('#cueTable tr').forEach((row) => row.classList.toggle('selected', Number(row.dataset.index) === state.selected));
  const selectedRow = cueTable.querySelector(`tr[data-index="${state.selected}"]`);
  if (selectedRow && seek) selectedRow.scrollIntoView({ block: 'nearest' });
  drawWaveform();
}

function buildShapeGrid() {
  shapeGrid.innerHTML = '';
  for (const shape of [...'ABCDEFGH', 'X']) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'shape-button';
    button.dataset.shape = shape;
    button.textContent = shape;
    button.title = shapeDescriptions[shape];
    button.addEventListener('click', () => changeShape(shape));
    shapeGrid.appendChild(button);
  }
}

function changeShape(shape) {
  if (!state.cues.length) return;
  state.cues[state.selected].shape = shape;
  $('#selectedShape').textContent = shape;
  shapeHelp.textContent = shapeDescriptions[shape];
  setFaceFrame(state.cues[state.selected], state.selected);
  const row = cueTable.querySelector(`tr[data-index="${state.selected}"] td:nth-child(3)`);
  if (row) row.textContent = shape;
  document.querySelectorAll('.shape-button').forEach((button) => button.classList.toggle('active', button.dataset.shape === shape));
  scheduleSave();
  drawWaveform();
}

function scheduleSave() {
  $('#saveStatus').textContent = 'Unsaved…';
  $('#saveStatus').style.color = 'var(--orange-bright)';
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(saveCues, 350);
}

async function saveCues() {
  try {
    const response = await fetch(`/api/projects/${state.project.id}/cues`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cues: state.cues }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || 'Save failed');
    $('#saveStatus').textContent = 'Saved';
    $('#saveStatus').style.color = 'var(--success)';
    return true;
  } catch (error) {
    $('#saveStatus').textContent = error.message;
    $('#saveStatus').style.color = 'var(--danger)';
    return false;
  }
}

function renderCueTable() {
  cueTable.innerHTML = '';
  const fragment = document.createDocumentFragment();
  state.cues.forEach((cue, index) => {
    const row = document.createElement('tr');
    row.dataset.index = index;
    row.innerHTML = `<td>${index + 1}</td><td>${cue.start.toFixed(2)}–${cue.end.toFixed(2)}</td><td>${cue.shape}</td><td>${Math.round((cue.energy || 0) * 100)}%</td>`;
    row.addEventListener('click', () => selectCue(index));
    fragment.appendChild(row);
  });
  cueTable.appendChild(fragment);
  $('#cueCount').textContent = `${state.cues.length} cues`;
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

function drawWaveform() {
  if (!state.project) return;
  const { width, height } = resizeCanvas();
  const context = canvas.getContext('2d');
  context.clearRect(0, 0, width, height);
  context.fillStyle = '#0d0b0a';
  context.fillRect(0, 0, width, height);
  const duration = state.project.duration || 1;
  const selected = state.cues[state.selected];
  if (selected) {
    context.fillStyle = 'rgba(255, 122, 24, 0.13)';
    context.fillRect((selected.start / duration) * width, 0, ((selected.end - selected.start) / duration) * width, height);
  }
  context.strokeStyle = 'rgba(255, 178, 91, 0.55)';
  context.lineWidth = Math.max(1, window.devicePixelRatio || 1);
  context.beginPath();
  state.waveform.forEach((peak, index) => {
    const x = (index / Math.max(1, state.waveform.length - 1)) * width;
    const half = peak * height * 0.42;
    context.moveTo(x, height / 2 - half);
    context.lineTo(x, height / 2 + half);
  });
  context.stroke();
  context.strokeStyle = 'rgba(255,255,255,.09)';
  context.beginPath();
  state.cues.forEach((cue) => {
    const x = (cue.start / duration) * width;
    context.moveTo(x, 0);
    context.lineTo(x, height);
  });
  context.stroke();
  const playhead = (audio.currentTime / duration) * width;
  context.strokeStyle = '#fff3e5';
  context.lineWidth = Math.max(1.5, 1.5 * (window.devicePixelRatio || 1));
  context.beginPath();
  context.moveTo(playhead, 0);
  context.lineTo(playhead, height);
  context.stroke();
}

canvas.addEventListener('click', (event) => {
  if (!state.project) return;
  const rect = canvas.getBoundingClientRect();
  const time = ((event.clientX - rect.left) / rect.width) * state.project.duration;
  audio.currentTime = Math.max(0, Math.min(state.project.duration, time));
  selectCue(cueIndexAt(audio.currentTime), false);
});

function tick() {
  if (state.project) {
    const index = cueIndexAt(audio.currentTime);
    if (index !== state.selected) selectCue(index, false);
    else setFaceFrame(state.cues[index], index);
    $('#currentTime').textContent = formatTime(audio.currentTime, true);
    drawWaveform();
  }
  state.animationFrame = requestAnimationFrame(tick);
}

audio.addEventListener('loadedmetadata', () => drawWaveform());
$('#previousCue').addEventListener('click', () => selectCue(state.selected - 1));
$('#nextCue').addEventListener('click', () => selectCue(state.selected + 1));
window.addEventListener('resize', drawWaveform);
window.addEventListener('keydown', (event) => {
  if (event.target.matches('input, textarea, select')) return;
  const key = event.key.toUpperCase();
  if ('ABCDEFGHX'.includes(key)) {
    event.preventDefault();
    changeShape(key);
  } else if (event.key === 'ArrowLeft') {
    event.preventDefault(); selectCue(state.selected - 1);
  } else if (event.key === 'ArrowRight') {
    event.preventDefault(); selectCue(state.selected + 1);
  }
});

async function startExport(quality) {
  if (!state.project) return;
  const status = $('#exportStatus');
  const link = $('#downloadLink');
  link.classList.add('hidden');
  setStatus(status, quality === 'full' ? 'Starting full 1080p render…' : 'Starting quick proof render…');
  try {
    const saved = await saveCues();
    if (!saved) throw new Error('Cue changes could not be saved; export was not started.');
    const response = await fetch(`/api/projects/${state.project.id}/export`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ quality }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || 'Could not start export');
    pollJob(payload.job_url);
  } catch (error) {
    setStatus(status, error.message, true);
  }
}

async function pollJob(url) {
  const status = $('#exportStatus');
  try {
    const response = await fetch(url);
    const job = await response.json();
    if (!response.ok) throw new Error(job.detail || 'Could not read export status');
    setStatus(status, job.message || job.status, job.status === 'error');
    if (job.status === 'complete') {
      const link = $('#downloadLink');
      link.href = job.download_url;
      link.download = job.output_name;
      link.textContent = `Download ${job.output_name}`;
      link.classList.remove('hidden');
      return;
    }
    if (job.status === 'error') throw new Error(job.error || 'Export failed');
    setTimeout(() => pollJob(url), 650);
  } catch (error) {
    setStatus(status, error.message, true);
  }
}

$('#previewExport').addEventListener('click', () => startExport('preview'));
$('#fullExport').addEventListener('click', () => startExport('full'));
$('#refreshProjects').addEventListener('click', loadRecentProjects);

updateMode();
loadHealth();
loadRecentProjects();
state.animationFrame = requestAnimationFrame(tick);
