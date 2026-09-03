"use strict";

const state = {
  jobs: [],
  runningJobId: null,
  hrrHistory: [], // [{t: simulation_time_s, v: total_hrr_kw}]
  // Which signal the plot under the running job shows: HRR, or one DEVC device.
  plotSignal: { kind: "hrr", device: null, unit: "kW" },
  deviceHistory: [], // [{t: simulation_time_s, v: value}] for plotSignal.device
  knownDevices: [], // [{name, unit}] from the job's CHID_devc.csv
  deviceListPending: false,
  lastSimTime: null,
  expandedHistoryIds: new Set(),
  jobMetricsCache: new Map(),
  autoAdvance: false,
  consoleLogJobId: null,
  // Kept separately from the <pre>, which also shows placeholder text ("Log ist leer.") that
  // must never end up in the clipboard or in a saved file.
  consoleLogText: "",
  knownCoreCount: 0,
  cpuTotalHistory: [],
  coreHistory: [],
  externalJobs: [],
  // The history panel shows either the current runs or the archive; archived runs are not
  // part of the regular job payload and are fetched on demand.
  showArchive: false,
  archivedJobs: [],
};

const modalState = {
  selectedFilePath: null,
  meshInfo: null,
};

const el = (id) => document.getElementById(id);

/** Job names, exit messages and device IDs come from user files -- escape before innerHTML. */
function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

/** Size a canvas for the display it is on and return a context that takes CSS pixels.
 *
 *  A canvas has two sizes: the CSS box it occupies and the pixel buffer behind it. Setting only
 *  the buffer to the CSS size draws at 1x and lets the browser upscale it, which on a HiDPI
 *  screen (devicePixelRatio 2) is exactly what makes lines and tick labels look soft next to
 *  the crisp DOM text around them. So the buffer is allocated at device resolution and the
 *  context is scaled back, letting every drawing routine keep working in CSS pixels.
 */
function prepareCanvas(canvas, cssWidth, cssHeight) {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(cssWidth * dpr);
  canvas.height = Math.round(cssHeight * dpr);
  canvas.style.width = `${cssWidth}px`;
  canvas.style.height = `${cssHeight}px`;
  const ctx = canvas.getContext("2d");
  // Assigning width/height resets the context, so this both clears and re-applies the scale.
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return ctx;
}

/** Read a design token so canvas drawings follow the active theme like the DOM does. */
function token(name, fallback) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

// ---------- API helpers ----------

async function apiGet(path) {
  const res = await fetch(path);
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `${path}: ${res.status}`);
  }
  return res.json();
}

async function apiSend(path, method, body) {
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `${path}: ${res.status}`);
  }
  return res.json();
}

// ---------- Node status ----------

async function refreshNodeStatus() {
  try {
    const nodes = await apiGet("/api/nodes");
    const node = nodes[0];
    if (!node) return;
    el("node-status").innerHTML =
      `<span class="dot"></span>` +
      esc(
        t("nodeSummary", {
          hostname: node.hostname,
          cores: node.cpu_cores,
          ram: Math.round(node.ram_total_mb / 1024),
        })
      );
  } catch (e) {
    el("node-status").innerHTML = `<span class="dot"></span>${t("nodeUnreachable")}`;
  }
}

// ---------- New Job modal: file browser + mesh/MPI preview ----------

const LAST_BROWSE_PATH_KEY = "fdsrouter.lastBrowsePath";

function getLastBrowsePath() {
  try {
    return localStorage.getItem(LAST_BROWSE_PATH_KEY);
  } catch (e) {
    return null;
  }
}

function setLastBrowsePath(path) {
  try {
    localStorage.setItem(LAST_BROWSE_PATH_KEY, path);
  } catch (e) {
    // best effort only
  }
}

function openNewJobModal() {
  modalState.selectedFilePath = null;
  modalState.meshInfo = null;
  el("selected-file-name").textContent = t("noFileSelected");
  el("selected-file-info").textContent = "";
  el("mpi-row").hidden = true;
  el("new-job-submit").disabled = true;
  el("upload-input").value = "";
  el("upload-btn").disabled = true;
  el("upload-status").textContent = t("uploadHint");
  el("new-job-overlay").hidden = false;
  browse(getLastBrowsePath());
}

function closeNewJobModal() {
  el("new-job-overlay").hidden = true;
}

async function browse(path) {
  const url = path ? `/api/browse?path=${encodeURIComponent(path)}` : "/api/browse";
  let data;
  try {
    data = await apiGet(url);
  } catch (e) {
    // the remembered path may no longer exist (moved/deleted) -- fall back to the default
    data = await apiGet("/api/browse");
  }
  setLastBrowsePath(data.path);

  el("browser-path").textContent = data.path;
  const list = el("browser-list");
  list.innerHTML = "";

  if (data.parent) {
    const up = document.createElement("li");
    up.className = "browser-up";
    up.textContent = "..";
    up.title = t("browserUp");
    up.onclick = () => browse(data.parent);
    list.appendChild(up);
  }

  for (const entry of data.entries) {
    const li = document.createElement("li");
    li.className = entry.is_dir ? "is-dir" : "is-file";
    li.textContent = entry.is_dir ? `${entry.name}/` : entry.name;
    if (entry.is_dir) {
      li.onclick = () => browse(entry.path);
    } else {
      li.onclick = () => selectFile(entry.path, li);
    }
    if (modalState.selectedFilePath === entry.path) li.classList.add("selected");
    list.appendChild(li);
  }
}

async function selectFile(path, liEl = null) {
  modalState.selectedFilePath = path;
  document.querySelectorAll("#browser-list li.selected").forEach((n) => n.classList.remove("selected"));
  if (liEl) liEl.classList.add("selected");

  el("selected-file-name").textContent = path.split("/").pop();
  el("selected-file-name").title = path;
  el("selected-file-info").textContent = "…";
  el("new-job-submit").disabled = true;
  el("mpi-row").hidden = true;

  try {
    const info = await apiGet(`/api/jobs/inspect?path=${encodeURIComponent(path)}`);
    modalState.meshInfo = info;
    el("selected-file-info").textContent = t("meshInfo", {
      meshes: info.mesh_count,
      cells: info.mesh_cell_count ?? t("unknownValue"),
    });

    const mpiInput = el("mpi-processes");
    mpiInput.value = info.default_mpi_processes;
    mpiInput.min = 1;
    mpiInput.max = info.mesh_count || 9999;
    el("mpi-hint").textContent = info.mesh_count ? t("mpiProcessesHint", { max: info.mesh_count }) : "";

    el("mpi-row").hidden = false;
    el("new-job-submit").disabled = false;
  } catch (e) {
    el("selected-file-info").textContent = e.message;
  }
}

/** POST the case with XHR rather than fetch: only XHR reports upload progress, and a case
 *  copied to a compute server over the office network is big enough to need it. */
function uploadCase(formData, onProgress) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", "/api/upload");
    request.upload.addEventListener("progress", (ev) => {
      if (ev.lengthComputable) onProgress((ev.loaded / ev.total) * 100);
    });
    request.addEventListener("load", () => {
      let body = {};
      try {
        body = JSON.parse(request.responseText);
      } catch (e) {
        // a non-JSON error page -- fall through to the status code below
      }
      if (request.status >= 200 && request.status < 300) resolve(body);
      else reject(new Error(body.detail || `HTTP ${request.status}`));
    });
    request.addEventListener("error", () => reject(new Error(t("nodeUnreachable"))));
    request.send(formData);
  });
}

async function startUpload() {
  const input = el("upload-input");
  const files = Array.from(input.files || []);
  if (files.length === 0) return;

  const form = new FormData();
  for (const file of files) form.append("files", file);

  const status = el("upload-status");
  const button = el("upload-btn");
  button.disabled = true;
  status.textContent = t("uploadRunning", { percent: "0" });
  try {
    const result = await uploadCase(form, (percent) => {
      status.textContent = t("uploadRunning", { percent: percent.toFixed(0) });
    });
    status.textContent = t("uploadDone", { dir: result.case_dir });
    // Continue exactly as if the uploaded file had been picked in the server browser.
    await selectFile(result.fds_file_path);
    await browse(result.case_dir);
  } catch (e) {
    status.textContent = t("uploadFailed", { error: e.message });
  } finally {
    button.disabled = files.length === 0;
  }
}

async function submitNewJob() {
  if (!modalState.selectedFilePath) return;
  const mpiProcesses = parseInt(el("mpi-processes").value, 10) || undefined;
  el("new-job-submit").disabled = true;
  try {
    await apiSend("/api/jobs", "POST", {
      fds_file_path: modalState.selectedFilePath,
      mpi_processes: mpiProcesses,
    });
    closeNewJobModal();
  } catch (e) {
    alert(t("enqueueFailed", { error: e.message }));
  } finally {
    el("new-job-submit").disabled = false;
  }
}

// ---------- Settings modal ----------

async function openSettingsModal() {
  el("language-select").value = getLang();
  el("theme-select").value = getTheme();
  el("ha-test-result").textContent = "";
  try {
    const s = await apiGet("/api/settings");
    el("ha-base-url").value = s.ha_base_url || "";
    el("ha-token").value = s.ha_token || "";
    el("ha-entity-id").value = s.ha_entity_id || "";
    el("electricity-price").value = s.electricity_price_eur_per_kwh || "";
    el("solar-powered").checked = s.solar_powered === "true";
  } catch (e) {
    // settings are optional -- an unreachable backend just leaves the form empty
  }
  el("settings-overlay").hidden = false;
}

function closeSettingsModal() {
  el("settings-overlay").hidden = true;
}

function onLanguageChange(lang) {
  setLang(lang);
  applyStaticTranslations();
  refreshNodeStatus();
  renderJobs();
}

function onThemeChange(theme) {
  setTheme(theme);
  // Canvas pixels don't restyle themselves -- redraw everything that reads theme tokens.
  redrawCanvases();
}

async function saveSettings() {
  try {
    await apiSend("/api/settings", "PUT", {
      ha_base_url: el("ha-base-url").value || null,
      ha_token: el("ha-token").value || null,
      ha_entity_id: el("ha-entity-id").value || null,
      electricity_price_eur_per_kwh: el("electricity-price").value ? parseFloat(el("electricity-price").value) : null,
      solar_powered: el("solar-powered").checked,
    });
    closeSettingsModal();
  } catch (e) {
    alert(t("settingsSaveFailed", { error: e.message }));
  }
}

async function testEnergyConnection() {
  const resultEl = el("ha-test-result");
  resultEl.textContent = "…";
  try {
    const result = await apiSend("/api/settings/test-energy-connection", "POST");
    resultEl.textContent = result.ok ? t("haTestSuccess", { watts: result.watts.toFixed(0) }) : t("haTestFailure");
  } catch (e) {
    resultEl.textContent = t("haTestFailure");
  }
}

// ---------- Console log modal ----------

async function openConsoleLog(jobId) {
  state.consoleLogJobId = jobId;
  state.consoleLogText = "";
  const pre = el("console-log-content");
  pre.textContent = t("detailLoading");
  updateConsoleActions();
  el("console-overlay").hidden = false;
  try {
    const data = await apiGet(`/api/jobs/${jobId}/log`);
    state.consoleLogText = data.log || "";
    pre.textContent = state.consoleLogText || t("logEmpty");
    pre.scrollTop = pre.scrollHeight;
  } catch (e) {
    pre.textContent = t("logUnavailable");
  }
  updateConsoleActions();
}

function closeConsoleLog() {
  state.consoleLogJobId = null;
  state.consoleLogText = "";
  el("console-overlay").hidden = true;
}

/** Append a line arriving over the WebSocket while this job's log is open. */
function appendConsoleLogLine(line) {
  state.consoleLogText += (state.consoleLogText ? "\n" : "") + line;
  const pre = el("console-log-content");
  pre.textContent = state.consoleLogText;
  pre.scrollTop = pre.scrollHeight;
  updateConsoleActions();
}

function updateConsoleActions() {
  const empty = !state.consoleLogText;
  el("console-copy").disabled = empty;
  el("console-download").disabled = empty;
}

/** Filesystem-safe stem built from the job name, so the saved file is recognisable. */
function logFileName(jobId) {
  const job = state.jobs.find((j) => j.id === jobId);
  const name = (job ? job.name : "fdsrouter").replace(/[^A-Za-z0-9._-]+/g, "_");
  const started = job && job.started_at ? new Date(job.started_at) : new Date();
  const stamp = [
    started.getFullYear(),
    String(started.getMonth() + 1).padStart(2, "0"),
    String(started.getDate()).padStart(2, "0"),
  ].join("-");
  return `${name}_${stamp}_konsole.txt`;
}

async function copyConsoleLog() {
  const button = el("console-copy");
  const restore = () => setTimeout(() => (button.textContent = t("copyLog")), 1500);
  try {
    // navigator.clipboard needs a secure context -- given on localhost, but not when the
    // service is bound to a LAN address over plain http, hence the textarea fallback.
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(state.consoleLogText);
    } else if (!copyViaTextarea(state.consoleLogText)) {
      throw new Error("execCommand copy rejected");
    }
    button.textContent = t("copyLogDone");
  } catch (e) {
    button.textContent = t("copyLogFailed");
  }
  restore();
}

function copyViaTextarea(text) {
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch (e) {
    ok = false;
  }
  document.body.removeChild(area);
  return ok;
}

function downloadConsoleLog() {
  const blob = new Blob([state.consoleLogText], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = logFileName(state.consoleLogJobId);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

/** Ask what the archive would contain before sending the browser to the download, so an
 *  empty or cleaned-up case gives a readable message instead of a raw 404 page. */
async function downloadResults(jobId) {
  try {
    const manifest = await apiGet(`/api/jobs/${jobId}/results/manifest`);
    if (!manifest.files.length) {
      alert(t("noResults"));
      return;
    }
  } catch (e) {
    alert(t("noResults"));
    return;
  }
  const link = document.createElement("a");
  link.href = `/api/jobs/${jobId}/results`;
  link.download = "";
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

function renderResultsButton(jobId) {
  const btn = document.createElement("button");
  btn.className = "secondary small";
  btn.textContent = t("downloadResults");
  btn.title = t("downloadResultsTitle");
  btn.onclick = (ev) => {
    ev.stopPropagation();
    downloadResults(jobId);
  };
  return btn;
}

function renderLogButton(jobId) {
  const btn = document.createElement("button");
  btn.className = "secondary small";
  btn.textContent = "Log";
  btn.title = t("viewLog");
  btn.onclick = (ev) => {
    ev.stopPropagation();
    openConsoleLog(jobId);
  };
  return btn;
}

// ---------- Auto-advance toggle ----------

function applyAutoAdvanceState(enabled) {
  state.autoAdvance = enabled;
  el("auto-advance-toggle").checked = enabled;
}

async function onAutoAdvanceToggle(enabled) {
  try {
    await apiSend("/api/queue/auto-advance", "POST", { enabled });
  } catch (e) {
    alert(t("startJobFailed", { error: e.message }));
    applyAutoAdvanceState(!enabled); // revert the checkbox on failure
  }
}

// ---------- Live plot signal (HRR or a DEVC device) ----------

const HRR_SIGNAL = "hrr";

function plotSignalValue() {
  return state.plotSignal.kind === "devc" ? `devc:${state.plotSignal.device}` : HRR_SIGNAL;
}

function plotSignalLabel() {
  return state.plotSignal.kind === "hrr" ? t("liveHrr") : state.plotSignal.device;
}

/** Samples currently plotted, oldest first. */
function plotSeries() {
  return state.plotSignal.kind === "hrr" ? state.hrrHistory : state.deviceHistory;
}

/** Rebuild the option list only when the device set actually changed, so the running
 *  selection survives the 2s metric ticks, and so an open dropdown isn't torn down mid-click. */
function syncPlotSignalOptions(devices) {
  const select = el("plot-signal");
  state.knownDevices = devices;
  if (!select) return;

  const wanted = plotSignalValue();
  const values = [HRR_SIGNAL, ...devices.map((d) => `devc:${d.name}`)];
  const unchanged =
    select.options.length === values.length &&
    values.every((value, i) => select.options[i].value === value);
  if (unchanged) {
    select.value = wanted;
    return;
  }

  select.innerHTML = "";
  const hrrOption = document.createElement("option");
  hrrOption.value = HRR_SIGNAL;
  hrrOption.textContent = t("liveHrr");
  select.appendChild(hrrOption);
  for (const device of devices) {
    const option = document.createElement("option");
    option.value = `devc:${device.name}`;
    option.textContent = device.name;
    select.appendChild(option);
  }

  // A selected device disappears when a different case starts -- fall back to HRR.
  if (!values.includes(wanted)) {
    state.plotSignal = { kind: "hrr", device: null, unit: "kW" };
    state.deviceHistory = [];
    updatePlotCaption();
  }
  select.value = plotSignalValue();
}

/** The device list comes from the case's CHID_devc.csv, which also carries each device's unit.
 *  It is empty until FDS writes its first output step, hence the refresh on device changes
 *  reported by the live metrics. */
async function refreshDeviceOptions(jobId) {
  if (!jobId || state.deviceListPending) return;
  state.deviceListPending = true;
  try {
    const data = await apiGet(`/api/jobs/${jobId}/devices`);
    if (state.runningJobId === jobId) syncPlotSignalOptions(data.devices || []);
  } catch (e) {
    // no devc file yet -- retried on the next tick that reports a different device set
  } finally {
    state.deviceListPending = false;
  }
}

async function onPlotSignalChange(value) {
  if (value === HRR_SIGNAL) {
    state.plotSignal = { kind: "hrr", device: null, unit: "kW" };
    state.deviceHistory = [];
    updatePlotCaption();
    drawLivePlot();
    return;
  }

  const device = value.slice("devc:".length);
  const known = state.knownDevices.find((d) => d.name === device);
  state.plotSignal = { kind: "devc", device, unit: known ? known.unit : "" };
  state.deviceHistory = [];
  updatePlotCaption();
  drawLivePlot();
  await loadDeviceSeries(device);
}

/** Load a device's history from its CHID_devc.csv, so switching signals mid-run shows the
 *  whole curve instead of only what arrives from here on. */
async function loadDeviceSeries(device) {
  const jobId = state.runningJobId;
  if (!jobId) return;
  try {
    const data = await apiGet(
      `/api/jobs/${jobId}/devices/series?device=${encodeURIComponent(device)}`
    );
    // A slower request for a signal the user has already switched away from must not win.
    if (state.plotSignal.kind !== "devc" || state.plotSignal.device !== device) return;
    state.plotSignal.unit = data.unit || "";
    state.deviceHistory = data.samples.map(([time, value]) => ({ t: time, v: value }));
  } catch (e) {
    // No devc file yet (FDS has not reached its first output step) -- the live ticks below
    // will fill the curve from now on.
  }
  updatePlotCaption();
  drawLivePlot();
}

function updatePlotCaption() {
  setText("plot-y-unit", state.plotSignal.unit || "");
}

// ---------- History archive ----------

async function refreshArchivedJobs() {
  try {
    state.archivedJobs = await apiGet("/api/jobs/archived");
  } catch (e) {
    state.archivedJobs = [];
  }
  renderJobs();
}

function onArchiveViewToggle(enabled) {
  state.showArchive = enabled;
  if (enabled) refreshArchivedJobs();
  else renderJobs();
}

async function archiveFinishedJobs() {
  const finished = state.jobs.filter((j) => ["done", "failed", "cancelled"].includes(j.status));
  if (finished.length === 0) {
    alert(t("archiveNothing"));
    return;
  }
  // Archiving sweeps the whole history at once, so it asks first -- the runs stay readable
  // under "Archiv", but there is no undo button for the sweep itself.
  if (!confirm(t("archiveConfirm", { count: finished.length }))) return;
  try {
    await apiSend("/api/jobs/archive", "POST");
    if (state.showArchive) await refreshArchivedJobs();
  } catch (e) {
    alert(t("archiveFailed", { error: e.message }));
  }
}

// ---------- Queue rendering ----------

function fmtDuration(seconds) {
  if (seconds == null || !isFinite(seconds)) return "–";
  seconds = Math.max(0, Math.round(seconds));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function renderStatus(status) {
  return `<span class="status ${status}">${esc(statusLabel(status))}</span>`;
}

function statusLabel(status) {
  const key = { queued: "statusQueued", running: "statusRunning",
    done: "statusDone", failed: "statusFailed", cancelled: "statusCancelled" }[status];
  return key ? t(key) : status;
}

function renderJobs() {
  const jobs = state.jobs;
  const running = jobs.find((j) => j.status === "running");
  const queued = jobs.filter((j) => j.status === "queued").sort((a, b) => a.queue_position - b.queue_position);
  const finished = jobs.filter((j) => ["done", "failed", "cancelled"].includes(j.status));
  const history = state.showArchive ? state.archivedJobs : finished.slice(0, 15);

  const previousRunning = state.runningJobId;
  state.runningJobId = running ? running.id : null;
  if (state.runningJobId !== previousRunning) state.lastSimTime = null;

  const queueList = el("queue-list");
  queueList.innerHTML = "";
  if (running) queueList.appendChild(renderRunningCard(running));
  if (queued.length === 0 && !running) {
    queueList.innerHTML = `<li class="note">${t("queueEmpty")}</li>`;
  } else {
    queued.forEach((job, i) => queueList.appendChild(renderQueuedCard(job, !running && i === 0, i + 1)));
  }
  if (running) drawLivePlot();

  const historyList = el("history-list");
  historyList.innerHTML = "";
  if (history.length === 0) {
    historyList.innerHTML = `<li class="note">${t(state.showArchive ? "archiveEmpty" : "historyEmpty")}</li>`;
  } else {
    for (const job of history) historyList.appendChild(renderHistoryCard(job));
  }

  // Nothing in the archive view can be archived again, so the action hides there.
  const archiveBtn = el("archive-btn");
  archiveBtn.hidden = state.showArchive;
  archiveBtn.disabled = finished.length === 0;

  updateDashboardVisibility();
}

function renderRunningCard(job) {
  const li = document.createElement("li");
  li.className = "job running";
  const endTime = job.sim_end_time_s != null ? `${job.sim_end_time_s.toFixed(0)} s` : t("unknownValue");
  li.innerHTML = `
    <div class="job-head">
      <span class="job-name">${esc(job.name)}</span>
      ${renderStatus("running")}
    </div>
    <div class="job-meta">${esc(t("jobMetaRunning", {
      machine: job.node_hostname ?? t("unknownValue"),
      mpi: job.mpi_process_count,
      cells: job.mesh_cell_count ?? t("unknownValue"),
    }))}</div>
    <div class="live-row">
      <span class="live-item"><span class="k">${t("liveElapsed")}</span><span class="v" id="run-elapsed">–</span></span>
      <span class="live-item"><span class="k">${t("liveRemaining")}</span><span class="v" id="run-remaining">–</span></span>
      <span class="live-item"><span class="k">${t("liveSimTime")}</span><span class="v" id="run-simtime">–</span><span class="u">s</span></span>
      <span class="live-item"><span class="k">${t("liveHrr")}</span><span class="v" id="run-hrr">–</span><span class="u">kW</span></span>
      <span class="live-item" id="run-energy-stat" hidden><span class="k">${t("detailEnergy")}</span><span class="v" id="run-energy">–</span></span>
    </div>
    <div class="gauge">
      <div class="gauge-track"><div class="gauge-fill" id="running-progress-bar" style="width:0%"></div></div>
      <div class="gauge-scale">
        <span>0 s</span>
        <span id="running-progress-pct">0 %</span>
        <span>${esc(endTime)}</span>
      </div>
    </div>
    <div class="plot">
      <div class="plot-caption">
        <span class="plot-signal">
          <select id="plot-signal" class="plot-select" title="${t("plotSignalLabel")}"></select>
          <span id="plot-y-unit">${esc(state.plotSignal.unit)}</span>
        </span>
        <span>${t("simTimeAxisLabel")}</span>
      </div>
      <canvas id="hrr-canvas"></canvas>
    </div>
    <div class="job-actions">
      <button class="danger small" id="stop-btn">${t("stopJob")}</button>
    </div>`;

  li.querySelector("#stop-btn").onclick = async () => {
    if (!confirm(t("stopJobConfirm", { name: job.name }))) return;
    try {
      await apiSend(`/api/jobs/${job.id}/stop`, "POST");
    } catch (e) {
      alert(t("stopFailed", { error: e.message }));
    }
  };
  li.querySelector(".job-actions").appendChild(renderLogButton(job.id));
  li.querySelector(".job-actions").appendChild(renderResultsButton(job.id));

  // The card is rebuilt on every queue update, so the select is repopulated from state here.
  const select = li.querySelector("#plot-signal");
  select.addEventListener("change", (ev) => onPlotSignalChange(ev.target.value));
  syncPlotSignalOptions(state.knownDevices);

  return li;
}

function renderQueuedCard(job, isNext, position) {
  const li = document.createElement("li");
  li.className = "job queued";
  li.draggable = true;
  li.dataset.jobId = job.id;
  li.innerHTML = `
    <div class="job-head">
      <span class="job-name"><span class="job-index">${String(position).padStart(2, "0")}</span>${esc(job.name)}</span>
      ${renderStatus("queued")}
    </div>
    <div class="job-meta">${esc(t("jobMetaQueued", {
      duration: fmtDuration(job.estimated_duration_s),
      cells: job.mesh_cell_count ?? t("unknownValue"),
      mpi: job.mpi_process_count,
    }))}</div>
    <div class="job-actions">
      ${isNext ? `<button class="small" id="start-btn">${t("startJob")}</button>` : ""}
      <button class="secondary small cancel-btn">${t("removeJob")}</button>
    </div>`;

  li.querySelector(".cancel-btn").onclick = async (ev) => {
    ev.stopPropagation();
    try {
      await apiSend(`/api/jobs/${job.id}/cancel`, "POST");
    } catch (e) {
      alert(t("removeFailed", { error: e.message }));
    }
  };

  if (isNext) {
    li.querySelector("#start-btn").onclick = async (ev) => {
      ev.stopPropagation();
      try {
        await apiSend(`/api/jobs/${job.id}/start`, "POST");
      } catch (e) {
        alert(t("startJobFailed", { error: e.message }));
      }
    };
  }

  attachDragHandlers(li);
  return li;
}

function renderHistoryCard(job) {
  const li = document.createElement("li");
  li.className = `job ${job.status}`;
  const isExpanded = state.expandedHistoryIds.has(job.id);
  li.innerHTML = `
    <div class="job-head">
      <span class="job-name">${esc(job.name)}</span>
      ${renderStatus(job.status)}
    </div>
    <div class="job-meta">${esc(t("jobMetaDone", { duration: fmtDuration(job.actual_duration_s) }))}${
      job.exit_message ? " · " + esc(job.exit_message) : ""
    }${
      job.archived_at
        ? " · " + esc(t("archivedMeta", { date: new Date(job.archived_at).toLocaleDateString() }))
        : ""
    }</div>
    <div class="job-actions">
      <button class="secondary small details-toggle">${isExpanded ? t("hideDetails") : t("showDetails")}</button>
    </div>
    <div class="job-details" ${isExpanded ? "" : "hidden"}></div>`;

  li.querySelector(".job-actions").appendChild(renderLogButton(job.id));
  li.querySelector(".job-actions").appendChild(renderResultsButton(job.id));
  const detailsEl = li.querySelector(".job-details");
  const toggleBtn = li.querySelector(".details-toggle");

  toggleBtn.onclick = async () => {
    const willExpand = detailsEl.hidden;
    detailsEl.hidden = !willExpand;
    toggleBtn.textContent = willExpand ? t("hideDetails") : t("showDetails");
    if (willExpand) {
      state.expandedHistoryIds.add(job.id);
      await loadHistoryDetails(job, detailsEl);
    } else {
      state.expandedHistoryIds.delete(job.id);
    }
  };

  if (isExpanded) loadHistoryDetails(job, detailsEl);

  return li;
}

async function loadHistoryDetails(job, container) {
  container.innerHTML = `<div class="note">${t("detailLoading")}</div>`;
  try {
    let metrics = state.jobMetricsCache.get(job.id);
    if (!metrics) {
      metrics = await apiGet(`/api/jobs/${job.id}/metrics`);
      state.jobMetricsCache.set(job.id, metrics);
    }
    const outMetrics = metrics.out_file_metrics || [];
    const last = outMetrics[outMetrics.length - 1];
    const peakHrr = outMetrics.reduce(
      (max, m) => (m.total_hrr_kw != null && m.total_hrr_kw > max ? m.total_hrr_kw : max),
      0
    );

    const rows = [
      [t("detailNode"), job.node_hostname ?? t("unknownValue")],
      [t("detailMeshCells"), job.mesh_cell_count ?? t("unknownValue")],
      [t("detailMpi"), job.mpi_process_count],
      [t("detailEstimated"), fmtDuration(job.estimated_duration_s)],
      [t("detailActual"), fmtDuration(job.actual_duration_s)],
      [t("detailFinalSimTime"), last?.simulation_time_s != null ? `${last.simulation_time_s.toFixed(2)} s` : t("unknownValue")],
      [t("detailPeakHrr"), outMetrics.length ? `${peakHrr.toFixed(1)} kW` : t("unknownValue")],
      [t("detailWarnings"), last?.warnings_count ?? 0],
      [t("detailStarted"), job.started_at ? new Date(job.started_at).toLocaleString() : "–"],
      [t("detailFinished"), job.finished_at ? new Date(job.finished_at).toLocaleString() : "–"],
    ];
    if (job.energy_kwh != null) {
      rows.push([t("detailEnergy"), `${job.energy_kwh.toFixed(2)} kWh`]);
      if (job.energy_cost_eur != null) rows.push([t("detailCost"), `${job.energy_cost_eur.toFixed(2)} €`]);
    }
    if (job.exit_message) rows.push([t("detailExitMessage"), job.exit_message]);

    container.innerHTML = `<dl class="spec">${rows
      .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`)
      .join("")}</dl>`;
  } catch (e) {
    container.innerHTML = `<div class="note">${t("detailUnavailable")}</div>`;
  }
}

// ---------- Drag & drop reordering (queued jobs only) ----------

function attachDragHandlers(li) {
  li.addEventListener("dragstart", (ev) => {
    li.classList.add("dragging");
    ev.dataTransfer.setData("text/plain", li.dataset.jobId);
    ev.dataTransfer.effectAllowed = "move";
  });
  li.addEventListener("dragend", () => li.classList.remove("dragging"));
  li.addEventListener("dragover", (ev) => {
    ev.preventDefault();
    li.classList.add("drag-over");
  });
  li.addEventListener("dragleave", () => li.classList.remove("drag-over"));
  li.addEventListener("drop", async (ev) => {
    ev.preventDefault();
    li.classList.remove("drag-over");
    const draggedId = ev.dataTransfer.getData("text/plain");
    if (!draggedId || draggedId === li.dataset.jobId) return;

    const queueList = el("queue-list");
    const cards = Array.from(queueList.querySelectorAll("li.job.queued"));
    const orderedIds = cards.map((c) => c.dataset.jobId);
    const fromIdx = orderedIds.indexOf(draggedId);
    const toIdx = orderedIds.indexOf(li.dataset.jobId);
    if (fromIdx === -1 || toIdx === -1) return;
    orderedIds.splice(toIdx, 0, orderedIds.splice(fromIdx, 1)[0]);

    try {
      await apiSend("/api/jobs/reorder", "PATCH", { ordered_job_ids: orderedIds });
    } catch (e) {
      alert(t("reorderFailed", { error: e.message }));
      await refreshJobs();
    }
  });
}

// ---------- Live dashboard ----------

function updateDashboardVisibility() {
  const hasRunning = !!state.runningJobId;
  el("dashboard-empty").hidden = hasRunning;
  el("dashboard").hidden = !hasRunning;
}

function currentRunningJob() {
  return state.jobs.find((j) => j.id === state.runningJobId);
}

function setText(id, value) {
  const node = document.getElementById(id);
  if (node) node.textContent = value;
}

function handleJobMetrics(msg) {
  if (msg.job_id !== state.runningJobId) return;

  const procTbody = el("proc-tbody");
  procTbody.innerHTML = "";
  for (const p of msg.processes || []) {
    const tr = document.createElement("tr");
    const core = p.core != null ? p.core : t("notAvailable");
    tr.innerHTML =
      `<td>${p.pid}</td><td>${esc(core)}</td>` +
      `<td class="n">${p.cpu_percent.toFixed(1)}</td><td class="n">${p.ram_percent.toFixed(1)}</td>`;
    procTbody.appendChild(tr);
  }

  const devices = msg.devices || {};
  const devcTbody = el("devc-tbody");
  const deviceNames = Object.keys(devices);
  if (deviceNames.length !== state.knownDevices.length) refreshDeviceOptions(msg.job_id);
  devcTbody.innerHTML = deviceNames.length
    ? deviceNames
        .map((name) => `<tr><td>${esc(name)}</td><td class="n">${devices[name].toFixed(1)}</td></tr>`)
        .join("")
    : `<tr><td colspan="2" class="empty">${t("noDevices")}</td></tr>`;

  if (msg.out) {
    setText("run-simtime", msg.out.simulation_time_s != null ? msg.out.simulation_time_s.toFixed(2) : "–");
    setText("run-hrr", msg.out.total_hrr_kw != null ? msg.out.total_hrr_kw.toFixed(1) : "–");
    setText(
      "limiting-mesh",
      msg.out.limiting_mesh != null ? t("limitingMeshValue", { mesh: msg.out.limiting_mesh }) : t("unknownValue")
    );

    const simTime = msg.out.simulation_time_s;
    if (simTime != null && msg.out.total_hrr_kw != null) {
      appendSample(state.hrrHistory, simTime, msg.out.total_hrr_kw);
    }
    // The selected device's live value rides along on the same tick as the .out values, so
    // both curves stay on one simulation-time axis.
    const signal = state.plotSignal;
    if (signal.kind === "devc" && simTime != null && devices[signal.device] != null) {
      appendSample(state.deviceHistory, simTime, devices[signal.device]);
    }
    drawLivePlot();

    state.lastSimTime = msg.out.simulation_time_s;
    updateProgress(msg.out.simulation_time_s);
  }
}

const MAX_PLOT_SAMPLES = 2000;

/** Append a sample, replacing the last one when simulation time has not advanced -- FDS polls
 *  faster than it writes output, so otherwise the curve piles up points on the same x. */
function appendSample(series, time, value) {
  const last = series[series.length - 1];
  if (last && Math.abs(last.t - time) < 1e-9) {
    last.v = value;
    return;
  }
  series.push({ t: time, v: value });
  if (series.length > MAX_PLOT_SAMPLES) series.shift();
}

function updateProgress(simTime) {
  const job = currentRunningJob();
  const bar = el("running-progress-bar");
  if (!job || !job.sim_end_time_s || simTime == null) {
    if (bar) bar.style.width = "0%";
    setText("running-progress-pct", "–");
    setText("run-remaining", "–");
    return;
  }
  const fraction = Math.min(1, simTime / job.sim_end_time_s);
  if (bar) bar.style.width = `${(fraction * 100).toFixed(1)}%`;
  setText("running-progress-pct", `${(fraction * 100).toFixed(1)} %`);

  if (job.started_at && fraction > 0.001) {
    const elapsedS = (Date.now() - Date.parse(job.started_at)) / 1000;
    const remainingS = (elapsedS * (1 - fraction)) / fraction;
    setText("run-remaining", fmtDuration(remainingS));
  }
}

function tickElapsedAndRemaining() {
  const job = currentRunningJob();
  if (!job || !job.started_at) {
    setText("run-elapsed", "–");
    return;
  }
  const elapsedS = (Date.now() - Date.parse(job.started_at)) / 1000;
  setText("run-elapsed", fmtDuration(elapsedS));
  if (state.lastSimTime != null) updateProgress(state.lastSimTime);
}

// ---------- Permanent system panel (independent of any job) ----------

const CPU_HISTORY_LENGTH = 90; // 90 samples @ 2s = 3 min rolling window

function handleSystemMetrics(msg) {
  setText("sys-cpu-percent", msg.cpu_percent_total != null ? msg.cpu_percent_total.toFixed(1) : "–");
  setText(
    "sys-ram-combined",
    msg.ram_percent != null && msg.ram_used_mb != null && msg.ram_total_mb != null
      ? `${msg.ram_percent.toFixed(0)}% · ${(msg.ram_used_mb / 1024).toFixed(1)} / ${(msg.ram_total_mb / 1024).toFixed(1)} GB`
      : "–"
  );
  setText("sys-temp", msg.cpu_temperature_c != null ? msg.cpu_temperature_c.toFixed(1) : t("notAvailable"));
  setText("sys-fan", msg.fan_rpm != null ? msg.fan_rpm.toFixed(0) : t("notAvailable"));

  if (msg.cpu_percent_total != null) {
    state.cpuTotalHistory.push(msg.cpu_percent_total);
    if (state.cpuTotalHistory.length > CPU_HISTORY_LENGTH) state.cpuTotalHistory.shift();
    drawSparkline("cpu-sparkline", state.cpuTotalHistory);
  }

  const cores = msg.cpu_percent_per_core || [];
  if (cores.length !== state.knownCoreCount) {
    state.knownCoreCount = cores.length;
    state.coreHistory = [];
  }
  state.coreHistory.push(cores);
  if (state.coreHistory.length > CPU_HISTORY_LENGTH) state.coreHistory.shift();
  drawCoreHeatmap();
}

function drawSparkline(canvasId, values) {
  const canvas = el(canvasId);
  if (!canvas) return;
  const w = canvas.clientWidth || 100;
  const h = canvas.clientHeight || 22;
  const ctx = prepareCanvas(canvas, w, h);
  ctx.clearRect(0, 0, w, h);
  if (values.length < 2) return;

  // Right-aligned like the per-core heatmap next to it: newest sample at the right edge,
  // so both readings share one time axis running left (old) to right (now).
  const step = w / (CPU_HISTORY_LENGTH - 1);
  const x0 = w - (values.length - 1) * step;
  const pointX = (i) => x0 + i * step;
  const pointY = (v) => h - 1 - (Math.min(100, Math.max(0, v)) / 100) * (h - 2);

  ctx.beginPath();
  values.forEach((v, i) => (i === 0 ? ctx.moveTo(pointX(i), pointY(v)) : ctx.lineTo(pointX(i), pointY(v))));
  ctx.strokeStyle = token("--data", "#4c9aff");
  ctx.lineWidth = 1.25;
  ctx.stroke();

  ctx.lineTo(pointX(values.length - 1), h);
  ctx.lineTo(pointX(0), h);
  ctx.closePath();
  ctx.globalAlpha = 0.14;
  ctx.fillStyle = token("--data", "#4c9aff");
  ctx.fill();
  ctx.globalAlpha = 1;
}

function parseHexColor(hex, fallback) {
  const match = /^#?([0-9a-f]{6})$/i.exec(hex || "");
  if (!match) return fallback;
  const value = parseInt(match[1], 16);
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

/** Load ramp: idle cores stay at the panel background, busy cores rise to the accent. */
function coreIntensityColor(pct, from, to) {
  const f = Math.max(0, Math.min(1, pct / 100));
  const mix = from.map((c, i) => Math.round(c + f * (to[i] - c)));
  return `rgb(${mix[0]},${mix[1]},${mix[2]})`;
}

function drawCoreHeatmap() {
  const canvas = el("core-heatmap");
  if (!canvas || state.knownCoreCount === 0) return;
  const rowH = 12;
  const labelW = 54;
  // Fill the panel width: the column width follows from it, so the plot spans the full
  // 3-minute window instead of ending in dead space.
  const wrap = el("core-heatmap-wrap");
  const available = Math.max(240, (wrap ? wrap.clientWidth : 400) - 8);
  const colW = Math.max(2, (available - labelW) / CPU_HISTORY_LENGTH);
  const w = Math.round(labelW + CPU_HISTORY_LENGTH * colW);
  const h = state.knownCoreCount * rowH;

  const ctx = prepareCanvas(canvas, w, h);
  ctx.clearRect(0, 0, w, h);
  ctx.font = "9px " + token("--font-mono", "monospace");
  ctx.textBaseline = "middle";

  const faint = token("--text-faint", "#7b8494");
  const textColor = token("--text", "#dfe4ec");
  const rampFrom = parseHexColor(token("--surface-2", "#161b25"), [22, 27, 37]);
  const rampTo = parseHexColor(token("--accent", "#ff6a3d"), [255, 106, 61]);

  const latest = state.coreHistory[state.coreHistory.length - 1] || [];
  for (let core = 0; core < state.knownCoreCount; core++) {
    const pct = latest[core];
    const y = core * rowH + rowH / 2;
    ctx.fillStyle = faint;
    ctx.fillText(`C${String(core).padStart(2, "0")}`, 2, y);
    ctx.fillStyle = textColor;
    ctx.fillText(pct != null ? `${String(Math.round(pct)).padStart(3, " ")}%` : "  –", 26, y);
  }

  const offset = CPU_HISTORY_LENGTH - state.coreHistory.length;
  state.coreHistory.forEach((sample, col) => {
    for (let core = 0; core < state.knownCoreCount; core++) {
      ctx.fillStyle = coreIntensityColor(sample[core] ?? 0, rampFrom, rampTo);
      ctx.fillRect(labelW + (offset + col) * colW, core * rowH, Math.ceil(colW), rowH - 1);
    }
  });
}

/** Scale [lo, hi] to ticks at 1/2/5 x 10^n, the way a plotted axis is normally scaled.
 *  Works for ranges that do not start at zero -- a thermocouple sits at ambient, not at 0. */
function niceAxisRange(lo, hi, count, exactBounds = false) {
  if (!isFinite(lo) || !isFinite(hi)) return { ticks: [0, 1], min: 0, max: 1, step: 1 };
  if (hi - lo < 1e-9) hi = lo + Math.max(Math.abs(lo) * 0.1, 1);
  const rawStep = (hi - lo) / count;
  const magnitude = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const normalized = rawStep / magnitude;
  const step = (normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10) * magnitude;
  // exactBounds keeps the given range and only puts the ticks on nice values -- used for the
  // time axis, which has to end exactly at T_END so the plot matches the progress gauge.
  const min = exactBounds ? lo : Math.floor(lo / step) * step;
  const max = exactBounds ? hi : Math.ceil(hi / step) * step;
  const ticks = [];
  // Indexed rather than accumulated, so the ticks don't drift on non-integer steps.
  // Tolerance only absorbs float noise (step * 1e-6); a half-step would admit a whole extra
  // tick beyond the axis, which is what put a "1000" label on a 900 s axis.
  const tolerance = step * 1e-6;
  for (let i = Math.ceil(min / step - 1e-6); i * step <= max + tolerance; i++) {
    ticks.push(i * step);
  }
  if (!ticks.length) ticks.push(min, max);
  return { ticks, min, max, step };
}

/** Tick labels share the decimal count implied by the step, so an axis reads 0.9/1.0/1.1
 *  instead of rounding three distinct ticks to the same "1". */
function formatTick(value, step) {
  const abs = Math.abs(value);
  if (abs >= 1e6) return value.toExponential(0).replace("e+", "e");
  const decimals = step >= 1 ? 0 : Math.min(6, Math.ceil(-Math.log10(step)));
  return value.toFixed(decimals);
}

function drawLivePlot() {
  const canvas = el("hrr-canvas");
  if (!canvas) return;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight || 120;
  const ctx = prepareCanvas(canvas, w, h);
  ctx.clearRect(0, 0, w, h);

  const data = plotSeries().slice();
  // HRR starts at zero by definition, so the curve is anchored there; a device reading has no
  // such baseline and is plotted only over the samples that actually exist.
  if (state.plotSignal.kind === "hrr" && (data.length === 0 || data[0].t > 0.001)) {
    data.unshift({ t: 0, v: 0 });
  }
  if (data.length === 0) return;

  const job = currentRunningJob();
  // Fixed x-axis at T_END where the case declares one: the curve then grows across a stable
  // axis instead of the axis rescaling under it on every sample.
  const xAxis = niceAxisRange(0, (job && job.sim_end_time_s) || data[data.length - 1].t || 1, 5, true);
  const values = data.map((d) => d.v);
  const yAxis = niceAxisRange(
    state.plotSignal.kind === "hrr" ? 0 : Math.min(...values),
    Math.max(...values),
    4
  );

  const padLeft = 42;
  const padBottom = 15;
  const padTop = 6;
  const padRight = 6;
  const plotW = w - padLeft - padRight;
  const plotH = h - padTop - padBottom;
  if (plotW <= 0 || plotH <= 0) return;

  const span = (axis) => axis.max - axis.min || 1;
  const clamp = (value, axis) => Math.min(Math.max(value, axis.min), axis.max);
  const xOf = (value) => padLeft + ((clamp(value, xAxis) - xAxis.min) / span(xAxis)) * plotW;
  const yOf = (value) => padTop + plotH - ((clamp(value, yAxis) - yAxis.min) / span(yAxis)) * plotH;

  const line = token("--line", "#232a37");
  const faint = token("--text-faint", "#7b8494");
  ctx.font = "9px " + token("--font-mono", "monospace");
  ctx.lineWidth = 1;

  // Dotted gridlines + tick labels, solid frame on the two axes carrying the scale.
  ctx.strokeStyle = line;
  ctx.fillStyle = faint;
  ctx.setLineDash([1, 3]);
  ctx.textBaseline = "middle";
  ctx.textAlign = "right";
  for (const value of yAxis.ticks) {
    const y = Math.round(yOf(value)) + 0.5;
    ctx.beginPath();
    ctx.moveTo(padLeft, y);
    ctx.lineTo(w - padRight, y);
    ctx.stroke();
    ctx.fillText(formatTick(value, yAxis.step), padLeft - 5, y);
  }
  ctx.textBaseline = "alphabetic";
  xAxis.ticks.forEach((value) => {
    const x = Math.round(xOf(value)) + 0.5;
    ctx.beginPath();
    ctx.moveTo(x, padTop);
    ctx.lineTo(x, padTop + plotH);
    ctx.stroke();
    // Align by position, not by index: only a label actually sitting at the canvas edge needs
    // to be pulled inwards, and with an exact axis the last tick is usually not at the edge.
    const EDGE_PX = 14;
    ctx.textAlign = x - padLeft < EDGE_PX ? "left" : w - padRight - x < EDGE_PX ? "right" : "center";
    ctx.fillText(formatTick(value, xAxis.step), x, h - 4);
  });
  ctx.setLineDash([]);
  ctx.beginPath();
  ctx.moveTo(padLeft + 0.5, padTop);
  ctx.lineTo(padLeft + 0.5, padTop + plotH + 0.5);
  ctx.lineTo(w - padRight, padTop + plotH + 0.5);
  ctx.stroke();

  ctx.strokeStyle = token("--accent", "#ff6a3d");
  ctx.lineWidth = 1.4;
  ctx.beginPath();
  data.forEach((d, i) => (i === 0 ? ctx.moveTo(xOf(d.t), yOf(d.v)) : ctx.lineTo(xOf(d.t), yOf(d.v))));
  ctx.stroke();
}

async function loadJobHistoryForChart(jobId) {
  // A new job means new devices -- start from HRR, which every case has.
  state.hrrHistory = [];
  state.deviceHistory = [];
  state.plotSignal = { kind: "hrr", device: null, unit: "kW" };
  syncPlotSignalOptions([]);
  updatePlotCaption();
  refreshDeviceOptions(jobId);
  try {
    const metrics = await apiGet(`/api/jobs/${jobId}/metrics`);
    for (const m of metrics.out_file_metrics) {
      if (m.simulation_time_s != null && m.total_hrr_kw != null) {
        appendSample(state.hrrHistory, m.simulation_time_s, m.total_hrr_kw);
      }
      state.lastSimTime = m.simulation_time_s ?? state.lastSimTime;
    }
    drawLivePlot();
  } catch (e) {
    // best effort only
  }
}

// ---------- External (unmanaged) FDS runs -- read-only ----------

function renderExternalJobs(jobs) {
  state.externalJobs = jobs;
  const panel = el("external-jobs-panel");
  panel.hidden = jobs.length === 0;
  if (jobs.length === 0) return;

  const list = el("external-jobs-list");
  list.innerHTML = jobs
    .map((j) => {
      const simTime = j.simulation_time_s != null ? `${j.simulation_time_s.toFixed(1)} s` : t("unknownValue");
      const hrr = j.total_hrr_kw != null ? `${j.total_hrr_kw.toFixed(1)} kW` : t("unknownValue");
      return `
        <li class="job">
          <div class="job-head"><span class="job-name">${esc(j.chid)}</span></div>
          <div class="job-meta">${esc(
            t("externalJobMeta", { pid: j.pid, caseDir: j.case_dir, simTime, hrr })
          )}</div>
        </li>`;
    })
    .join("");
}

// ---------- Energy / cost ----------

function handleEnergyUpdate(msg) {
  if (msg.job_id !== state.runningJobId) return;
  const stat = el("run-energy-stat");
  if (stat) stat.hidden = false;
  const label = msg.solar_powered
    ? t("solarPoweredBadge")
    : msg.energy_cost_eur != null
    ? `${msg.energy_kwh.toFixed(2)} kWh · ${msg.energy_cost_eur.toFixed(2)} €`
    : `${msg.energy_kwh.toFixed(2)} kWh`;
  setText("run-energy", label);
}

// ---------- Polling / WebSocket ----------

async function refreshJobs() {
  const jobs = await apiGet("/api/jobs");
  const previousRunning = state.runningJobId;
  state.jobs = jobs;
  renderJobs();
  if (state.runningJobId && state.runningJobId !== previousRunning) {
    loadJobHistoryForChart(state.runningJobId);
  }
}

function connectWebSocket() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);

  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "queue_update") {
      const previousRunning = state.runningJobId;
      state.jobs = msg.jobs;
      applyAutoAdvanceState(msg.auto_advance);
      renderJobs();
      if (state.runningJobId && state.runningJobId !== previousRunning) {
        loadJobHistoryForChart(state.runningJobId);
      }
    } else if (msg.type === "job_metrics") {
      handleJobMetrics(msg);
    } else if (msg.type === "system_metrics") {
      handleSystemMetrics(msg);
    } else if (msg.type === "external_jobs") {
      renderExternalJobs(msg.jobs);
    } else if (msg.type === "energy_update") {
      handleEnergyUpdate(msg);
    } else if (msg.type === "log_line" && msg.job_id === state.consoleLogJobId) {
      appendConsoleLogLine(msg.line);
    }
  };

  ws.onclose = () => setTimeout(connectWebSocket, 2000);
  ws.onerror = () => ws.close();
}

// ---------- Init ----------

el("new-job-btn").addEventListener("click", openNewJobModal);
el("new-job-cancel").addEventListener("click", closeNewJobModal);
el("new-job-submit").addEventListener("click", submitNewJob);
el("upload-btn").addEventListener("click", startUpload);
el("upload-input").addEventListener("change", (ev) => {
  el("upload-btn").disabled = (ev.target.files || []).length === 0;
});
el("new-job-overlay").addEventListener("click", (ev) => {
  if (ev.target === el("new-job-overlay")) closeNewJobModal();
});

el("settings-btn").addEventListener("click", openSettingsModal);
el("settings-close").addEventListener("click", closeSettingsModal);
el("settings-overlay").addEventListener("click", (ev) => {
  if (ev.target === el("settings-overlay")) closeSettingsModal();
});
el("language-select").addEventListener("change", (ev) => onLanguageChange(ev.target.value));
el("theme-select").addEventListener("change", (ev) => onThemeChange(ev.target.value));
el("settings-save").addEventListener("click", saveSettings);
el("ha-test-btn").addEventListener("click", testEnergyConnection);

el("console-close").addEventListener("click", closeConsoleLog);
el("console-copy").addEventListener("click", copyConsoleLog);
el("console-download").addEventListener("click", downloadConsoleLog);
el("console-overlay").addEventListener("click", (ev) => {
  if (ev.target === el("console-overlay")) closeConsoleLog();
});

el("auto-advance-toggle").addEventListener("change", (ev) => onAutoAdvanceToggle(ev.target.checked));
el("archive-btn").addEventListener("click", archiveFinishedJobs);
el("archive-view-toggle").addEventListener("change", (ev) => onArchiveViewToggle(ev.target.checked));

setTheme(getTheme());
applyStaticTranslations();
refreshNodeStatus();
refreshJobs();
apiGet("/api/queue/state").then((s) => applyAutoAdvanceState(s.auto_advance)).catch(() => {});
connectWebSocket();
function redrawCanvases() {
  drawSparkline("cpu-sparkline", state.cpuTotalHistory);
  drawCoreHeatmap();
  drawLivePlot();
}

window.addEventListener("resize", redrawCanvases);

// Dragging the window to a display with a different pixel ratio doesn't necessarily resize it,
// but the buffers then have the wrong resolution -- watch the ratio itself and re-arm, since
// the query only matches the ratio it was created with.
function watchPixelRatio() {
  const query = window.matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`);
  query.addEventListener("change", () => {
    redrawCanvases();
    watchPixelRatio();
  }, { once: true });
}
watchPixelRatio();
setInterval(refreshNodeStatus, 15000);
setInterval(tickElapsedAndRemaining, 1000);
