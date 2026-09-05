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
  // Smoothed sim-seconds-per-wall-second, and the sample the current remaining-time estimate
  // was computed from -- see updateRemainingEstimate. Recomputed only when a real new .out
  // sample arrives, not on every 1s display tick, which is what keeps the countdown from
  // jumping around between samples.
  simRateEma: null,
  lastProgressSample: null, // {wallMs, simTime}
  remainingEstimateS: null,
  remainingEstimateAtMs: null,
  // Last .out sample, so a rebuilt job card can be refilled without waiting for the next one.
  lastOut: null,
  // Recent time step sizes and the anomalies derived from them; a collapsing time step is the
  // earliest visible sign that a run is in trouble.
  stepSizes: [],
  anomalies: [],
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
  // Whether this instance is managed by systemd, plus its revision -- drives the service
  // section of the settings dialog.
  serviceStatus: null,
  // Queue, history and archive are one list with a filter, not three panels. The archive is
  // not part of the regular job payload and is fetched when its filter is picked.
  filter: "all",
  search: "",
  archivedJobs: [],
  nodes: [],
  projectFilter: "",
  currentUser: null,
  bootedApp: false,
  compareSelection: new Set(),
  compareJobs: [],
  deepLinkJobId: (() => {
    const m = location.pathname.match(/^\/job\/([^/]+)$/);
    return m ? decodeURIComponent(m[1]) : null;
  })(),
  deepLinkHandled: false,
};

const modalState = {
  selectedFilePath: null,
  // Whether the selected file was just uploaded (then the working directory was chosen here)
  // or picked on the server (then it is the file's own directory).
  selectedFromUpload: false,
  meshInfo: null,
  // Where an upload creates its working directory: the configured upload_dir, or the directory
  // currently open in the server browser.
  uploadRootDir: null,
  browsePath: null,
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

/** Same, but for a canvas the stylesheet already sizes (width: 100%).
 *
 *  Only the pixel buffer is touched here. Writing the measured size back into style.width is
 *  what made the plots shrink on every redraw: with `box-sizing: border-box` the measured
 *  clientWidth excludes the 1px border on each side, so assigning it as the border-box width
 *  took two more pixels off the content box every time a sample came in.
 */
function fitCanvas(canvas, fallbackWidth = 100, fallbackHeight = 22) {
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.clientWidth || fallbackWidth;
  const height = canvas.clientHeight || fallbackHeight;
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, width, height };
}

/** Read a design token so canvas drawings follow the active theme like the DOM does. */
function token(name, fallback) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

// ---------- API helpers ----------

async function apiGet(path) {
  const res = await fetch(path);
  if (!res.ok) {
    if (res.status === 401) handleUnauthorized();
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
    if (res.status === 401) handleUnauthorized();
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `${path}: ${res.status}`);
  }
  return res.json();
}

// ---------- Auth ----------

function handleUnauthorized() {
  showLoginOverlay(false);
}

function showLoginOverlay(bootstrap) {
  el("login-overlay").hidden = false;
  el("login-title").textContent = bootstrap ? t("loginBootstrapTitle") : t("loginTitle");
  el("login-intro").textContent = bootstrap ? t("loginBootstrapIntro") : t("loginIntro");
  el("login-submit").textContent = bootstrap ? t("loginBootstrapSubmit") : t("loginSubmit");
  el("login-display-name-field").hidden = !bootstrap;
  el("login-error").hidden = true;
  el("login-overlay").dataset.bootstrap = bootstrap ? "1" : "";
}

function hideLoginOverlay() {
  el("login-overlay").hidden = true;
  el("login-username").value = "";
  el("login-password").value = "";
  el("login-display-name").value = "";
}

function updateUserBadge(user) {
  state.currentUser = user;
  const badge = el("user-badge");
  if (!user) {
    badge.hidden = true;
    return;
  }
  badge.hidden = false;
  setText("user-name", user.display_name || user.username);
}

async function submitLogin() {
  const bootstrap = el("login-overlay").dataset.bootstrap === "1";
  const username = el("login-username").value.trim();
  const password = el("login-password").value;
  const errorEl = el("login-error");
  errorEl.hidden = true;
  if (!username || !password) {
    errorEl.textContent = t("loginMissingFields");
    errorEl.hidden = false;
    return;
  }
  try {
    if (bootstrap) {
      await apiSend("/api/auth/register", "POST", {
        username,
        password,
        display_name: el("login-display-name").value.trim() || undefined,
      });
    } else {
      await apiSend("/api/auth/login", "POST", { username, password });
    }
    hideLoginOverlay();
    const session = await apiGet("/api/auth/session");
    onAuthenticated(session.user);
  } catch (e) {
    errorEl.textContent = e.message || t("loginFailed");
    errorEl.hidden = false;
  }
}

async function logout() {
  try {
    await apiSend("/api/auth/logout", "POST");
  } catch (e) {
    // even if the request fails, forget the local user state and show the login screen again
  }
  updateUserBadge(null);
  showLoginOverlay(false);
}

/** Boots the rest of the app once we know a request will not be met with a 401 -- either an
 *  account is logged in, or no account exists yet (bootstrap mode is fully open). Only runs the
 *  actual startup sequence once: called again after a post-401 re-login, when jobs/websocket are
 *  already running. */
function onAuthenticated(user) {
  hideLoginOverlay();
  updateUserBadge(user);
  if (state.bootedApp) return;
  state.bootedApp = true;
  refreshNodeStatus();
  refreshJobs();
  apiGet("/api/queue/state").then((s) => applyAutoAdvanceState(s.auto_advance)).catch(() => {});
  connectWebSocket();
}

async function boot() {
  let session;
  try {
    session = await apiGet("/api/auth/session");
  } catch (e) {
    return; // backend unreachable -- nothing to boot yet; a retry happens on the next reload
  }
  if (session.bootstrap || session.authenticated) {
    onAuthenticated(session.user);
  } else {
    showLoginOverlay(false);
  }
}

// ---------- Node status ----------

async function refreshNodeStatus() {
  try {
    state.nodes = await apiGet("/api/nodes");
  } catch (e) {
    state.nodes = [];
    el("node-status").innerHTML = `<span class="dot"></span>${t("nodeUnreachable")}`;
    el("node-list-panel").hidden = true;
    return;
  }
  renderNodeStatusSummary();
  renderNodeList();
}

/** The header line: a single machine's own summary (unchanged from before clustering existed),
 *  or an at-a-glance online/total count once more than one node has ever registered. */
function renderNodeStatusSummary() {
  const nodes = state.nodes;
  if (nodes.length === 0) {
    el("node-status").innerHTML = `<span class="dot"></span>${t("nodeUnreachable")}`;
    return;
  }
  if (nodes.length === 1) {
    const node = nodes[0];
    el("node-status").innerHTML =
      `<span class="dot"></span>` +
      esc(
        t("nodeSummary", {
          hostname: node.hostname,
          cores: node.cpu_cores,
          ram: Math.round(node.ram_total_mb / 1024),
        })
      );
    return;
  }
  const online = nodes.filter((n) => n.online).length;
  el("node-status").innerHTML = `<span class="dot"></span>` + esc(t("nodeClusterSummary", { online, total: nodes.length }));
}

/** Device overview -- every node that has ever registered, always shown (including the local
 *  machine on its own, before any agent ever joins), so it doubles as "is this machine even
 *  usable for runs?" at a glance rather than only appearing once a cluster exists. */
function renderNodeList() {
  const container = el("node-list");
  const nodes = state.nodes;
  el("node-list-panel").hidden = nodes.length === 0;
  if (nodes.length === 0) return;

  const runningByNode = new Map();
  for (const job of state.jobs) {
    if (job.status === "running" && job.node_id) runningByNode.set(job.node_id, job);
  }

  container.innerHTML = nodes
    .map((node) => {
      const running = runningByNode.get(node.id);
      const jobLine = running ? esc(t("nodeRunning", { name: running.name })) : esc(t("nodeIdle"));
      // Online but not configured to run FDS at all (e.g. a Controller-only install with no
      // local fds_binary) -- worth calling out, since the scheduler will never hand it a job.
      const readyBadge = node.fds_ready
        ? ""
        : `<span class="node-badge" title="${esc(t("nodeNotReadyTitle"))}">${esc(t("nodeNotReady"))}</span>`;
      return `<div class="node-row ${node.online ? "online" : "offline"}">
        <span class="dot"></span>
        <span class="node-name">${esc(node.hostname)}</span>
        <span class="node-meta">${esc(
          t("nodeCoresRam", { cores: node.cpu_cores, ram: Math.round(node.ram_total_mb / 1024) })
        )}</span>
        ${readyBadge}
        <span class="node-job">${jobLine}</span>
      </div>`;
    })
    .join("");
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
  modalState.selectedFromUpload = false;
  modalState.meshInfo = null;
  el("selected-file-name").textContent = t("noFileSelected");
  el("selected-file-info").textContent = "";
  el("mpi-row").hidden = true;
  el("new-job-submit").disabled = true;
  el("upload-input").value = "";
  el("upload-btn").disabled = true;
  el("upload-status").textContent = t("uploadHint");
  el("upload-folder-name").value = "";
  el("new-job-project").value = "";
  el("new-job-deadline").value = "";
  renderCaseFindings([]);
  updateWorkingDirStep();
  el("new-job-overlay").hidden = false;
  loadUploadRoot();
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
  modalState.browsePath = data.path;
  updateUploadTargetHint();

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

async function selectFile(path, liEl = null, fromUpload = false) {
  modalState.selectedFilePath = path;
  modalState.selectedFromUpload = fromUpload;
  updateWorkingDirStep();
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

    renderCaseFindings(info.findings || []);
    el("mpi-row").hidden = false;
    el("new-job-submit").disabled = false;
  } catch (e) {
    el("selected-file-info").textContent = e.message;
    renderCaseFindings([]);
  }
}

/** The pre-flight report: what will go wrong before the case is ever started.
 *
 *  Errors do not block enqueueing -- the operator may be about to fix the file, or may know
 *  something the check does not -- but the primary button says what it is doing then. */
function renderCaseFindings(findings) {
  const list = el("case-findings");
  list.innerHTML = "";
  list.hidden = findings.length === 0;

  for (const finding of findings) {
    const li = document.createElement("li");
    li.className = `finding ${finding.level}`;
    const text = t(`check_${finding.code}`, { detail: finding.detail || "" });
    li.innerHTML = `<span class="finding-level">${esc(t(`checkLevel_${finding.level}`))}</span><span>${esc(text)}</span>`;
    list.appendChild(li);
  }

  const hasError = findings.some((f) => f.level === "error");
  const submit = el("new-job-submit");
  submit.textContent = hasError ? t("enqueueAnyway") : t("enqueue");
  submit.classList.toggle("danger", hasError);
}

/** Step 2 has two truths: for an uploaded case the operator picks the working directory, for a
 *  file already on the server it is that file's own directory and cannot be chosen. */
function updateWorkingDirStep() {
  const picked = modalState.selectedFilePath;
  const fromUpload = picked && modalState.selectedFromUpload;
  const showFields = !picked || fromUpload;
  el("upload-target-fields").hidden = !showFields;
  const fixed = el("fixed-target-path");
  fixed.hidden = showFields;
  if (!showFields) {
    const dir = picked.slice(0, picked.lastIndexOf("/")) || "/";
    fixed.textContent = t("workingDirFromFile", { path: dir });
  }
  if (showFields) updateUploadTargetHint();
}

/** The configured upload_dir, shown as the default location for the new working directory. */
async function loadUploadRoot() {
  try {
    modalState.uploadRootDir = (await apiGet("/api/upload/target")).upload_dir;
  } catch (e) {
    modalState.uploadRootDir = null;
  }
  updateUploadTargetHint();
}

function uploadParentDir() {
  return el("upload-parent-select").value === "browsed" ? modalState.browsePath : modalState.uploadRootDir;
}

/** Spell out the directory the upload will create, so it is clear before anything is sent. */
function updateUploadTargetHint() {
  const parent = uploadParentDir();
  const hint = el("upload-target-path");
  if (!parent) {
    hint.textContent = "";
    return;
  }
  const folder = el("upload-folder-name").value.trim();
  hint.textContent = t("uploadTargetHint", {
    path: folder ? `${parent}/${folder}` : `${parent}/${t("uploadTargetAuto")}`,
  });
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
  const folderName = el("upload-folder-name").value.trim();
  if (folderName) form.append("folder_name", folderName);
  // Only sent for the browsed directory -- an empty value keeps the backend's own default.
  if (el("upload-parent-select").value === "browsed" && modalState.browsePath) {
    form.append("parent_dir", modalState.browsePath);
  }

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
    await selectFile(result.fds_file_path, null, true);
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
    const deadlineValue = el("new-job-deadline").value;
    await apiSend("/api/jobs", "POST", {
      fds_file_path: modalState.selectedFilePath,
      mpi_processes: mpiProcesses,
      project: el("new-job-project").value.trim() || undefined,
      scheduled_stop_at: deadlineValue ? new Date(deadlineValue).toISOString() : undefined,
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
    el("notify-webhook-url").value = s.notify_webhook_url || "";
    el("notify-email-to").value = s.notify_email_to || "";
    el("notify-smtp-host").value = s.notify_email_smtp_host || "";
    el("notify-smtp-port").value = s.notify_email_smtp_port || "";
    el("notify-smtp-user").value = s.notify_email_smtp_user || "";
    el("notify-smtp-password").value = s.notify_email_smtp_password || "";
    el("notify-email-from").value = s.notify_email_from || "";
    const events = (s.notify_events || "done,failed,cancelled").split(",").map((e) => e.trim());
    Array.from(el("notify-events").options).forEach((opt) => (opt.selected = events.includes(opt.value)));
  } catch (e) {
    // settings are optional -- an unreachable backend just leaves the form empty
  }
  el("notify-test-result").textContent = "";
  el("settings-overlay").hidden = false;
}

function closeSettingsModal() {
  el("settings-overlay").hidden = true;
}

function openOperationsModal() {
  loadServiceStatus();
  loadClusterInfo();
  loadEditableConfig();
  el("operations-overlay").hidden = false;
}

/** What another machine's `fdsrouter agent` setup needs -- shown so the operator can read the
 *  token off the screen instead of grepping config.yaml on the server. */
async function loadClusterInfo() {
  el("cluster-address").textContent = "–";
  el("cluster-token").textContent = "–";
  el("cluster-warning").hidden = true;
  try {
    const info = await apiGet("/api/service/cluster-info");
    el("cluster-address").textContent = `${info.hostname}:${info.port}`;
    el("cluster-token").textContent = info.cluster_token || t("unknownValue");
    // Two different problems look the same from "an agent can't find me" -- but need different
    // fixes, so they get different messages: not reachable at all (fix: host: "0.0.0.0") vs.
    // reachable but discovery specifically turned off (fix: discovery_enabled: true).
    if (!info.lan_reachable) {
      el("cluster-warning").hidden = false;
      el("cluster-warning-text").textContent = t("clusterNotLanReachableHint");
    } else if (!info.discovery_active) {
      el("cluster-warning").hidden = false;
      el("cluster-warning-text").textContent = t("clusterDiscoveryOffHint");
    }
  } catch (e) {
    // best effort only -- the rest of the Operations dialog stays usable
  }
}

async function copyClusterToken() {
  const button = el("cluster-token-copy");
  const token = el("cluster-token").textContent;
  const restore = () => setTimeout(() => (button.textContent = t("copyLog")), 1500);
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(token);
    } else if (!copyViaTextarea(token)) {
      throw new Error("execCommand copy rejected");
    }
    button.textContent = t("copyLogDone");
  } catch (e) {
    button.textContent = t("copyLogFailed");
  }
  restore();
}

async function loadEditableConfig() {
  el("config-save-result").textContent = "";
  try {
    const cfg = await apiGet("/api/service/config");
    el("config-host").value = cfg.host || "";
    el("config-port").value = cfg.port ?? "";
    el("config-open-browser").checked = !!cfg.open_browser;
    el("config-fds-binary").value = cfg.fds_binary || "";
    el("config-mpi-executable").value = cfg.mpi_executable || "";
    el("config-default-mpi").value = cfg.default_mpi_processes ?? "";
    el("config-temperature").checked = !!cfg.temperature_enabled;
    el("config-discovery").checked = !!cfg.discovery_enabled;
    el("config-max-upload").value = cfg.max_upload_mb ?? "";
  } catch (e) {
    // best effort only -- the rest of the Operations dialog stays usable
  }
}

async function saveEditableConfig() {
  const resultEl = el("config-save-result");
  resultEl.textContent = "…";
  try {
    const result = await apiSend("/api/service/config", "PUT", {
      host: el("config-host").value.trim() || undefined,
      port: el("config-port").value ? parseInt(el("config-port").value, 10) : undefined,
      open_browser: el("config-open-browser").checked,
      fds_binary: el("config-fds-binary").value.trim() || null,
      mpi_executable: el("config-mpi-executable").value.trim() || null,
      default_mpi_processes: el("config-default-mpi").value ? parseInt(el("config-default-mpi").value, 10) : undefined,
      temperature_enabled: el("config-temperature").checked,
      discovery_enabled: el("config-discovery").checked,
      max_upload_mb: el("config-max-upload").value ? parseInt(el("config-max-upload").value, 10) : undefined,
    });
    resultEl.textContent = result.restart_required ? t("configSavedRestartNeeded") : t("configSavedApplied");
  } catch (e) {
    resultEl.textContent = t("settingsSaveFailed", { error: e.message });
  }
}

function closeOperationsModal() {
  el("operations-overlay").hidden = true;
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
      notify_webhook_url: el("notify-webhook-url").value || null,
      notify_email_to: el("notify-email-to").value || null,
      notify_email_smtp_host: el("notify-smtp-host").value || null,
      notify_email_smtp_port: el("notify-smtp-port").value ? parseInt(el("notify-smtp-port").value, 10) : null,
      notify_email_smtp_user: el("notify-smtp-user").value || null,
      notify_email_smtp_password: el("notify-smtp-password").value || null,
      notify_email_from: el("notify-email-from").value || null,
      notify_events: Array.from(el("notify-events").selectedOptions).map((o) => o.value).join(",") || null,
    });
    closeSettingsModal();
  } catch (e) {
    alert(t("settingsSaveFailed", { error: e.message }));
  }
}

// ---------- Service control (settings modal) ----------

const SERVICE_BUTTON_IDS = ["service-update-btn", "service-restart-btn", "service-stop-btn"];

const SERVICE_REASON_KEYS = {
  no_systemd: "serviceNoSystemd",
  no_unit: "serviceNoUnit",
  needs_root: "serviceNeedsRoot",
  no_git_checkout: "serviceNoGitCheckout",
};

/** Fetch what this instance is (systemd-managed? which revision?) and set the buttons up. */
async function loadServiceStatus() {
  el("service-result").textContent = "";
  try {
    state.serviceStatus = await apiGet("/api/service");
  } catch (e) {
    state.serviceStatus = null;
  }
  const status = state.serviceStatus;

  if (!status) {
    el("service-version").textContent = t("unknownValue");
    setServiceButtonsDisabled(true);
    return;
  }

  const scopeLabel = status.scope === "user" ? t("serviceScopeUser") : status.scope === "system" ? t("serviceScopeSystem") : null;
  const revision = status.revision
    ? [status.revision, status.revision_date].filter(Boolean).join(" · ")
    : t("unknownValue");
  el("service-version").textContent = [revision, scopeLabel].filter(Boolean).join(" · ");

  el("service-restart-btn").disabled = !status.controllable;
  el("service-stop-btn").disabled = !status.controllable;
  el("service-update-btn").disabled = !status.can_update;

  if (!status.controllable) {
    el("service-result").textContent = t(SERVICE_REASON_KEYS[status.reason] || "serviceNoSystemd");
  } else if (!status.can_update) {
    el("service-result").textContent = t("serviceNoGitCheckout");
  }
}

function setServiceButtonsDisabled(disabled) {
  SERVICE_BUTTON_IDS.forEach((id) => {
    el(id).disabled = disabled;
  });
}

function runningJobName() {
  const job = state.jobs.find((j) => j.id === state.runningJobId);
  return job ? job.name : null;
}

const SERVICE_ACTION_TEXTS = {
  restart: { confirm: "serviceRestartConfirm", confirmRunning: "serviceRestartConfirmRunning", pending: "serviceRestarting" },
  stop: { confirm: "serviceStopConfirm", confirmRunning: "serviceStopConfirmRunning", pending: "serviceStopping" },
  update: { confirm: "serviceUpdateConfirm", confirmRunning: "serviceUpdateConfirmRunning", pending: "serviceUpdating" },
};

/** Restart, stop or update the service this UI is served by. */
async function serviceAction(kind) {
  const texts = SERVICE_ACTION_TEXTS[kind];
  const running = runningJobName();
  const message = running ? t(texts.confirmRunning, { name: running }) : t(texts.confirm);
  if (!confirm(message)) return;

  const resultEl = el("service-result");
  resultEl.textContent = t(texts.pending);
  setServiceButtonsDisabled(true);

  try {
    // force is only sent once the user has been warned about the running job by name; without
    // it the backend refuses, which catches a stale tab that does not know about the run yet.
    const result = await apiSend(`/api/service/${kind}`, "POST", { force: Boolean(running) });

    if (kind === "stop") {
      resultEl.textContent = t("serviceStopped");
      return;
    }
    if (kind === "update") {
      resultEl.textContent = result.changed
        ? t("serviceUpdated", { before: result.revision_before || "?", after: result.revision_after || "?" })
        : t("serviceUpdateNoChange", { revision: result.revision_after || "?" });
      if (!result.restarted) {
        setServiceButtonsDisabled(false);
        return;
      }
    }
    await waitForServiceBack(resultEl);
  } catch (e) {
    // Restarting and stopping tear the server down, so a dropped request is the expected
    // ending rather than a failure -- only a real error response says something went wrong.
    if (e instanceof TypeError) {
      if (kind === "stop") resultEl.textContent = t("serviceStopped");
      else await waitForServiceBack(resultEl);
      return;
    }
    // The backend answers an impossible action with the same reason key the status uses.
    const reasonKey = SERVICE_REASON_KEYS[e.message];
    resultEl.textContent = e.message === "running_job"
      ? t("serviceBlockedByJob")
      : reasonKey
        ? t(reasonKey)
        : t("serviceActionFailed", { error: e.message });
    setServiceButtonsDisabled(false);
    if (e.message === "running_job") loadServiceStatus();
  }
}

const SERVICE_WAIT_TIMEOUT_MS = 90000;
const SERVICE_WAIT_INTERVAL_MS = 2000;

/** Poll until the restarted service answers again, and say so.
 *
 *  Restarting means the answer to "did it work?" cannot come from the request itself -- the
 *  process handling it is the one being replaced. So the dialog waits for the new one instead
 *  of leaving "restarting..." standing forever.
 */
async function waitForServiceBack(resultEl) {
  resultEl.textContent = t("serviceRestarting");
  const deadline = Date.now() + SERVICE_WAIT_TIMEOUT_MS;

  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, SERVICE_WAIT_INTERVAL_MS));
    try {
      const status = await apiGet("/api/service");
      await loadServiceStatus();
      resultEl.textContent = t("serviceBackUp", { revision: status.revision || t("unknownValue") });
      return;
    } catch (e) {
      // Still down -- which is exactly what we are waiting through.
    }
  }

  resultEl.textContent = t("serviceStillDown");
  setServiceButtonsDisabled(false);
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

/** Sends the test unconditionally with whatever is on disk -- unsaved changes must be saved
 *  first, since the test reads settings from the backend, not from the open form. */
async function testNotification() {
  const resultEl = el("notify-test-result");
  resultEl.textContent = "…";
  try {
    const result = await apiSend("/api/settings/test-notification", "POST");
    if (!result.webhook_configured && !result.email_configured) {
      resultEl.textContent = t("notifyTestUnconfigured");
    } else {
      const parts = [];
      if (result.webhook_configured) parts.push(result.webhook_ok ? t("notifyTestWebhookOk") : t("notifyTestWebhookFailed"));
      if (result.email_configured) parts.push(result.email_ok ? t("notifyTestEmailOk") : t("notifyTestEmailFailed"));
      resultEl.textContent = parts.join(" · ");
    }
  } catch (e) {
    resultEl.textContent = t("notifyTestFailed", { error: e.message });
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

function setRunFilter(filter) {
  state.filter = filter;
  document.querySelectorAll("#status-filter .chip").forEach((chip) => {
    chip.classList.toggle("selected", chip.dataset.filter === filter);
  });
  if (filter === "archive") refreshArchivedJobs();
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
    if (state.filter === "archive") await refreshArchivedJobs();
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

/** Which runs the current filter and search let through. */
function visibleJobs() {
  const source = state.filter === "archive" ? state.archivedJobs : state.jobs;
  const byStatus = {
    all: () => true,
    queued: (job) => job.status === "queued",
    running: (job) => job.status === "running",
    done: (job) => job.status === "done",
    failed: (job) => ["failed", "cancelled"].includes(job.status),
    archive: () => true,
  }[state.filter];

  const needle = state.search.trim().toLowerCase();
  return source.filter(
    (job) =>
      byStatus(job) &&
      (!state.projectFilter || job.project === state.projectFilter) &&
      (!needle ||
        job.name.toLowerCase().includes(needle) ||
        (job.fds_file_path || "").toLowerCase().includes(needle))
  );
}

/** Rebuild the project dropdown from whatever projects are currently known, keeping the
 *  selection if it still exists -- same "diff before touching the DOM" care as
 *  syncPlotSignalOptions, so an open dropdown isn't rebuilt out from under a click. */
function syncProjectFilterOptions() {
  const select = el("project-filter");
  const projects = Array.from(
    new Set([...state.jobs, ...state.archivedJobs].map((j) => j.project).filter(Boolean))
  ).sort();
  const wanted = state.projectFilter;
  const values = ["", ...projects];
  const unchanged =
    select.options.length === values.length &&
    values.every((value, i) => select.options[i].value === value);
  if (unchanged) {
    select.value = values.includes(wanted) ? wanted : "";
    return;
  }
  select.innerHTML = `<option value="">${esc(t("projectFilterAll"))}</option>`;
  for (const project of projects) {
    const option = document.createElement("option");
    option.value = project;
    option.textContent = project;
    select.appendChild(option);
  }
  if (!values.includes(wanted)) state.projectFilter = "";
  select.value = state.projectFilter;
}

/** Reordering rewrites the whole queue order, so it is only safe while every waiting run is
 *  actually on screen -- otherwise a hidden job would silently lose its place. */
function reorderAllowed() {
  return state.filter !== "archive" && !state.search.trim();
}

function renderJobs() {
  syncProjectFilterOptions();
  const running = state.jobs.find((j) => j.status === "running");
  const previousRunning = state.runningJobId;
  state.runningJobId = running ? running.id : null;
  if (state.runningJobId !== previousRunning) {
    state.lastSimTime = null;
    state.simRateEma = null;
    state.lastProgressSample = null;
    state.remainingEstimateS = null;
    state.remainingEstimateAtMs = null;
  }

  // Position in the queue is a property of the queue, not of the current filter -- numbering
  // the filtered rows would tell a searching operator the wrong place in line.
  const queueOrder = state.jobs
    .filter((j) => j.status === "queued")
    .sort((a, b) => a.queue_position - b.queue_position)
    .map((j) => j.id);

  const visible = visibleJobs();
  const runningVisible = visible.filter((j) => j.status === "running");
  const queued = visible
    .filter((j) => j.status === "queued")
    .sort((a, b) => a.queue_position - b.queue_position);
  const finished = visible
    .filter((j) => ["done", "failed", "cancelled"].includes(j.status))
    .sort((a, b) => (b.finished_at || "").localeCompare(a.finished_at || ""));

  const list = el("runs-list");
  list.innerHTML = "";
  setText("runs-count", String(visible.length));

  for (const job of runningVisible) list.appendChild(renderRunningCard(job));

  if (queued.length) {
    list.appendChild(groupHeading(t("groupWaiting", { count: queued.length }), !reorderAllowed() ? t("reorderLocked") : null));
    queued.forEach((job) =>
      list.appendChild(renderQueuedCard(job, !running && queueOrder[0] === job.id, queueOrder.indexOf(job.id) + 1))
    );
  }

  if (finished.length) {
    const label = state.filter === "archive" ? t("groupArchived", { count: finished.length }) : t("groupFinished", { count: finished.length });
    list.appendChild(groupHeading(label, null));
    for (const job of finished) list.appendChild(renderHistoryCard(job));
  }

  if (!list.children.length) list.appendChild(renderEmptyState());
  if (running && runningVisible.length) {
    drawLivePlot();
    // The card was just rebuilt from scratch; without this every live readout would fall back
    // to its placeholder until the next sample arrives.
    if (state.lastOut) applyLiveOut(state.lastOut);
    renderAnomalies();
    if (state.lastSimTime != null) updateProgress(state.lastSimTime);
  }

  const archiveBtn = el("archive-btn");
  const archivable = state.jobs.filter((j) => ["done", "failed", "cancelled"].includes(j.status));
  archiveBtn.hidden = state.filter === "archive";
  archiveBtn.disabled = archivable.length === 0;

  // A selected job can vanish from view for reasons other than its own checkbox (archived,
  // filtered out, deleted from the server) -- re-derive the button from the surviving selection
  // on every render rather than only reacting to a checkbox click.
  const stillPresent = new Set([...state.jobs, ...state.archivedJobs].map((j) => j.id));
  for (const id of state.compareSelection) {
    if (!stillPresent.has(id)) state.compareSelection.delete(id);
  }
  updateCompareButton();
  renderNodeList();

  updateDashboardVisibility();
}

function groupHeading(label, note) {
  const li = document.createElement("li");
  li.className = "group-head";
  li.innerHTML = `<span>${esc(label)}</span>${note ? `<span class="group-note">${esc(note)}</span>` : ""}`;
  return li;
}

/** An empty list should say what to do next, not just that it is empty. */
function renderEmptyState() {
  const li = document.createElement("li");
  li.className = "empty-state";
  const filtered = state.search.trim() || state.filter !== "all";
  li.innerHTML = `<p>${esc(filtered ? t("emptyFiltered") : t("emptyNoRuns"))}</p>`;

  const button = document.createElement("button");
  button.className = filtered ? "secondary small" : "small";
  button.textContent = filtered ? t("emptyClearFilter") : t("emptyAddRun");
  button.onclick = () => {
    if (filtered) {
      el("runs-search").value = "";
      state.search = "";
      setRunFilter("all");
    } else {
      openNewJobModal();
    }
  };
  li.appendChild(button);
  return li;
}

/** "· endet spätestens um HH:MM" appended to a job-meta line when a deadline is set. */
function scheduledStopMetaSuffix(job) {
  if (!job.scheduled_stop_at) return "";
  return " · " + esc(t("scheduledStopMeta", { clock: formatClock(new Date(job.scheduled_stop_at)) }));
}

/** "YYYY-MM-DDTHH:mm" in local time, as a <input type="datetime-local"> expects -- the input has
 *  no timezone concept of its own, so both directions go through the browser's local time. */
function toDatetimeLocalValue(isoString) {
  const d = new Date(isoString);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** A small toggle button plus an inline form to set/clear a job's deadline -- shared between the
 *  queued and running cards, since a deadline is just as meaningful before a run starts as
 *  during it. */
function attachScheduledStopControl(li, job) {
  const actions = li.querySelector(".job-actions");
  if (!actions) return;

  const btn = document.createElement("button");
  btn.className = "secondary small";
  btn.textContent = "⏱";
  btn.title = t("scheduledStopButtonTitle");

  const form = document.createElement("div");
  form.className = "deadline-form";
  form.hidden = true;
  form.innerHTML = `
    <input type="datetime-local" class="deadline-input" />
    <button class="small deadline-save">${esc(t("save"))}</button>
    <button class="secondary small deadline-clear">${esc(t("scheduledStopClear"))}</button>`;
  if (job.scheduled_stop_at) {
    form.querySelector(".deadline-input").value = toDatetimeLocalValue(job.scheduled_stop_at);
  }

  btn.onclick = (ev) => {
    ev.stopPropagation();
    form.hidden = !form.hidden;
  };
  form.querySelector(".deadline-save").onclick = async (ev) => {
    ev.stopPropagation();
    const value = form.querySelector(".deadline-input").value;
    if (!value) return;
    try {
      await apiSend(`/api/jobs/${job.id}`, "PATCH", { scheduled_stop_at: new Date(value).toISOString() });
      form.hidden = true;
    } catch (e) {
      alert(t("scheduledStopFailed", { error: e.message }));
    }
  };
  form.querySelector(".deadline-clear").onclick = async (ev) => {
    ev.stopPropagation();
    try {
      await apiSend(`/api/jobs/${job.id}`, "PATCH", { scheduled_stop_at: null });
      form.hidden = true;
    } catch (e) {
      alert(t("scheduledStopFailed", { error: e.message }));
    }
  };

  actions.appendChild(btn);
  li.appendChild(form);
}

function renderRunningCard(job) {
  const li = document.createElement("li");
  li.className = "job running";
  li.dataset.jobId = job.id;
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
    }))}${scheduledStopMetaSuffix(job)}</div>
    <div class="verdict" id="run-verdict">${esc(t("verdictStarting"))}</div>
    <div class="anomalies" id="run-anomalies"></div>
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
      <div id="hrr-plot" class="plot-area"></div>
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
  attachScheduledStopControl(li, job);

  // The card is rebuilt on every queue update, so the select is repopulated from state here.
  const select = li.querySelector("#plot-signal");
  select.addEventListener("change", (ev) => onPlotSignalChange(ev.target.value));
  syncPlotSignalOptions(state.knownDevices);

  return li;
}

function renderQueuedCard(job, isNext, position) {
  const li = document.createElement("li");
  li.className = "job queued";
  li.draggable = reorderAllowed();
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
    }))}${
      // Only worth a line once a second node exists -- in a solo install every job is
      // trivially "assigned" to the only node the moment it's queued, so this would just add
      // noise to the one case that's still by far the most common.
      state.nodes.length > 1
        ? " · " + esc(job.node_hostname ? t("nodeAssigned", { name: job.node_hostname }) : t("nodeAssigning"))
        : ""
    }${scheduledStopMetaSuffix(job)}</div>
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

  attachScheduledStopControl(li, job);
  attachDragHandlers(li);
  return li;
}

function renderHistoryCard(job) {
  const li = document.createElement("li");
  li.className = `job ${job.status}`;
  li.dataset.jobId = job.id;
  const isExpanded = state.expandedHistoryIds.has(job.id);
  const canCompare = ["done", "failed"].includes(job.status);
  li.innerHTML = `
    <div class="job-head">
      ${canCompare ? `<input type="checkbox" class="compare-check" title="${esc(t("compareSelect"))}" />` : ""}
      <span class="job-name">${esc(job.name)}</span>
      ${renderStatus(job.status)}
    </div>
    <div class="job-meta">${esc(t("jobMetaDone", { duration: fmtDuration(job.actual_duration_s) }))}${
      job.project ? " · " + esc(job.project) : ""
    }${
      job.archived_at
        ? " · " + esc(t("archivedMeta", { date: new Date(job.archived_at).toLocaleDateString() }))
        : ""
    }</div>
    ${job.exit_message && job.status !== "done"
      ? `<div class="failure">
           <div class="failure-head">${esc(t("failureTitle"))}</div>
           <pre class="failure-message">${esc(job.exit_message)}</pre>
           <div class="failure-hint">${esc(t("failureHint"))}</div>
         </div>`
      : ""}
    <div class="job-actions">
      <button class="secondary small details-toggle">${isExpanded ? t("hideDetails") : t("showDetails")}</button>
    </div>
    <div class="job-details" ${isExpanded ? "" : "hidden"}></div>`;

  if (canCompare) {
    const checkbox = li.querySelector(".compare-check");
    checkbox.checked = state.compareSelection.has(job.id);
    checkbox.onclick = (ev) => ev.stopPropagation();
    checkbox.onchange = () => {
      if (checkbox.checked) state.compareSelection.add(job.id);
      else state.compareSelection.delete(job.id);
      updateCompareButton();
    };
  }

  li.querySelector(".job-actions").appendChild(renderLogButton(job.id));
  li.querySelector(".job-actions").appendChild(renderResultsButton(job.id));
  if (job.status !== "done") li.querySelector(".job-actions").appendChild(renderRequeueButton(job));
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

/** Queue the same case again -- the normal next step after fixing the input file. */
function renderRequeueButton(job) {
  const button = document.createElement("button");
  button.className = "secondary small";
  button.textContent = t("requeue");
  button.onclick = async () => {
    try {
      await apiSend("/api/jobs", "POST", {
        fds_file_path: job.fds_file_path,
        name: job.name,
        mpi_processes: job.mpi_process_count || undefined,
      });
    } catch (e) {
      alert(t("requeueFailed", { error: e.message }));
    }
  };
  return button;
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
      [t("detailCreatedBy"), job.created_by || t("unknownValue")],
      [t("detailStarted"), job.started_at ? new Date(job.started_at).toLocaleString() : "–"],
      [t("detailFinished"), job.finished_at ? new Date(job.finished_at).toLocaleString() : "–"],
    ];
    if (job.energy_kwh != null) {
      rows.push([t("detailEnergy"), `${job.energy_kwh.toFixed(2)} kWh`]);
      if (job.energy_cost_eur != null) rows.push([t("detailCost"), `${job.energy_cost_eur.toFixed(2)} €`]);
    }
    if (job.exit_message) rows.push([t("detailExitMessage"), job.exit_message]);

    let audit = [];
    try {
      audit = (await apiGet(`/api/jobs/${job.id}/audit`)).entries || [];
    } catch (e) {
      // audit history is a nice-to-have -- an unavailable log must not hide the rest of the details
    }

    container.innerHTML = `
      <dl class="spec">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
      <div class="sub-head">${esc(t("projectNotesTitle"))}</div>
      <div class="field">
        <label for="job-project-${job.id}">${esc(t("projectLabel"))}</label>
        <input id="job-project-${job.id}" type="text" value="${esc(job.project || "")}" />
      </div>
      <div class="field">
        <label for="job-notes-${job.id}">${esc(t("notesLabel"))}</label>
        <textarea id="job-notes-${job.id}" rows="3">${esc(job.notes || "")}</textarea>
      </div>
      <div class="field">
        <label></label>
        <button class="secondary small" id="job-save-meta-${job.id}">${esc(t("save"))}</button>
        <span class="hint" id="job-save-meta-result-${job.id}"></span>
      </div>
      ${audit.length
        ? `<div class="sub-head">${esc(t("auditTitle"))}</div>
           <ul class="audit-list">${audit
             .map(
               (e) =>
                 `<li><span class="mono">${esc(new Date(e.timestamp).toLocaleString())}</span> · ${esc(
                   e.username || t("auditSystemActor")
                 )} · ${esc(auditActionLabel(e.action))}${e.detail ? ` (${esc(e.detail)})` : ""}</li>`
             )
             .join("")}</ul>`
        : ""}`;

    container.querySelector(`#job-save-meta-${job.id}`).onclick = async () => {
      const resultEl = container.querySelector(`#job-save-meta-result-${job.id}`);
      try {
        const updated = await apiSend(`/api/jobs/${job.id}`, "PATCH", {
          project: container.querySelector(`#job-project-${job.id}`).value.trim() || null,
          notes: container.querySelector(`#job-notes-${job.id}`).value.trim() || null,
        });
        Object.assign(job, updated);
        resultEl.textContent = t("saveDone");
        const metaEl = container.closest("li.job")?.querySelector(".job-meta");
        if (metaEl && job.project) {
          metaEl.textContent = t("jobMetaDone", { duration: fmtDuration(job.actual_duration_s) }) + " · " + job.project;
        }
      } catch (e) {
        resultEl.textContent = t("saveFailed", { error: e.message });
      }
    };
  } catch (e) {
    container.innerHTML = `<div class="note">${t("detailUnavailable")}</div>`;
  }
}

function auditActionLabel(action) {
  const key = "audit_" + action;
  const label = t(key);
  return label === key ? action : label;
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

    if (!reorderAllowed()) return;
    const cards = Array.from(el("runs-list").querySelectorAll("li.job.queued"));
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
  const deviceNames = Object.keys(devices);
  if (deviceNames.length !== state.knownDevices.length) refreshDeviceOptions(msg.job_id);
  renderDeviceTable(devices);

  if (msg.out) {
    state.lastOut = msg.out;
    state.anomalies = detectAnomalies(msg.out, msg.processes || []);
    applyLiveOut(msg.out);
    renderAnomalies();

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
/** The collapsible DEVC panel: every measurement point with its live value, and a click to
 *  put that device on the plot -- the same thing the signal select does, but from the list the
 *  operator is already reading. */
/** Fill the running card's readouts from one .out sample.
 *
 *  Separate from the metrics handler because the card is rebuilt on every queue update: without
 *  re-applying the last sample, simulation time, HRR and the verdict would fall back to their
 *  placeholders for up to one polling interval. */
const STEP_HISTORY = 12;
const STEP_COLLAPSE_FACTOR = 0.2;
const IDLE_PROCESS_PERCENT = 5;

/** What is worth interrupting the operator about while a run is going.
 *
 *  Each of these is something an experienced user spots by watching the numbers: the time step
 *  falling off a cliff (the solver is fighting something), FDS logging warnings, or an MPI
 *  process that stopped consuming CPU while its siblings keep going.
 */
function detectAnomalies(out, processes) {
  const found = [];

  if (out.step_size_s != null && out.step_size_s > 0) {
    const history = state.stepSizes;
    if (history.length >= 6) {
      const sorted = history.slice().sort((a, b) => a - b);
      const median = sorted[Math.floor(sorted.length / 2)];
      if (median > 0 && out.step_size_s < median * STEP_COLLAPSE_FACTOR) {
        found.push({ code: "timestep", detail: out.step_size_s.toExponential(1) });
      }
    }
    history.push(out.step_size_s);
    if (history.length > STEP_HISTORY) history.shift();
  }

  if (out.warnings_count) found.push({ code: "warnings", detail: String(out.warnings_count) });

  const idle = (processes || []).filter((p) => p.cpu_percent < IDLE_PROCESS_PERCENT);
  if (idle.length && processes.length > 1) {
    found.push({ code: "idleProcess", detail: idle.map((p) => p.pid).join(", ") });
  }
  return found;
}

function renderAnomalies() {
  const host = el("run-anomalies");
  if (!host) return;
  host.innerHTML = state.anomalies
    .map((a) => `<span class="anomaly">${esc(t(`anomaly_${a.code}`, { detail: a.detail }))}</span>`)
    .join("");
}

function applyLiveOut(out) {
  setText("run-simtime", out.simulation_time_s != null ? out.simulation_time_s.toFixed(2) : "–");
  setText("run-hrr", out.total_hrr_kw != null ? out.total_hrr_kw.toFixed(1) : "–");
  setText(
    "limiting-mesh",
    out.limiting_mesh != null ? t("limitingMeshValue", { mesh: out.limiting_mesh }) : t("unknownValue")
  );
}

function renderDeviceTable(devices) {
  const tbody = el("devc-tbody");
  if (!tbody) return;
  const names = Object.keys(devices);
  setText("devc-count", String(names.length));

  if (!names.length) {
    tbody.innerHTML = `<tr><td colspan="2" class="empty">${t("noDevices")}</td></tr>`;
    return;
  }

  const selected = state.plotSignal.kind === "devc" ? state.plotSignal.device : null;
  const unitOf = (name) => {
    const known = state.knownDevices.find((d) => d.name === name);
    return known && known.unit ? known.unit : "";
  };
  tbody.innerHTML = names
    .map((name) => {
      const classes = `pick${name === selected ? " selected" : ""}`;
      // A reading without its unit is not a measurement -- the unit comes from the same
      // CHID_devc.csv header row the values do.
      return (
        `<tr class="${classes}" data-device="${esc(name)}">` +
        `<td>${esc(name)}</td>` +
        `<td class="n">${devices[name].toFixed(1)}<span class="u">${esc(unitOf(name))}</span></td></tr>`
      );
    })
    .join("");
}

const DEVC_PANEL_OPEN_KEY = "fdsrouter.devcPanelOpen";

function restoreDevicePanelState() {
  const panel = el("devc-panel");
  if (!panel) return;
  try {
    panel.open = localStorage.getItem(DEVC_PANEL_OPEN_KEY) === "true";
  } catch (e) {
    panel.open = false;
  }
  panel.addEventListener("toggle", () => {
    try {
      localStorage.setItem(DEVC_PANEL_OPEN_KEY, String(panel.open));
    } catch (e) {
      // best effort only
    }
  });
}

function appendSample(series, time, value) {
  const last = series[series.length - 1];
  if (last && Math.abs(last.t - time) < 1e-9) {
    last.v = value;
    return;
  }
  series.push({ t: time, v: value });
  if (series.length > MAX_PLOT_SAMPLES) series.shift();
}

// EMA weight for the sim-seconds/wall-second rate: high enough to track a run settling into a
// steady pace within a few samples, low enough that one noisy sample doesn't swing the ETA.
const SIM_RATE_EMA_ALPHA = 0.3;

/** Called once per *real* new .out sample (not on every 1s display tick). Recomputes the
 *  remaining-time estimate from a smoothed recent rate rather than "total elapsed / total
 *  fraction so far" -- the old approach baked slow startup overhead into the average for the
 *  whole run and recomputed a fresh linear extrapolation on every tick even when simulation
 *  time hadn't moved, which is exactly what made the countdown creep up and then jump back down
 *  between samples. */
function updateRemainingEstimate(job, simTime, now = Date.now()) {
  const prev = state.lastProgressSample;

  if (!prev) {
    // First sample since the job started (or since we started watching it) -- nothing to take
    // a rate from yet, just anchor the next delta. Leaves any existing estimate (e.g. seeded
    // from history in loadJobHistoryForChart) as-is rather than blanking it.
    state.lastProgressSample = { wallMs: now, simTime };
    return;
  }

  if (simTime <= prev.simTime) {
    // A duplicate poll before FDS wrote a new step -- not real progress. Deliberately leave
    // both lastProgressSample and the current estimate untouched: the next real advance is
    // measured over the true interval, and the local countdown (currentRemainingEstimateS)
    // keeps ticking smoothly through this call instead of being reset to a stale value.
    return;
  }

  const wallDeltaS = (now - prev.wallMs) / 1000;
  if (wallDeltaS > 0) {
    const instantRate = (simTime - prev.simTime) / wallDeltaS;
    state.simRateEma = state.simRateEma == null ? instantRate : SIM_RATE_EMA_ALPHA * instantRate + (1 - SIM_RATE_EMA_ALPHA) * state.simRateEma;
  }
  state.lastProgressSample = { wallMs: now, simTime };

  if (state.simRateEma != null && state.simRateEma > 0) {
    const remainingSimTime = Math.max(0, job.sim_end_time_s - simTime);
    state.remainingEstimateS = remainingSimTime / state.simRateEma;
    state.remainingEstimateAtMs = now;
  }
}

/** The remaining-time estimate ticked down locally by however long it's been since it was last
 *  actually recomputed -- a smooth per-second countdown between samples instead of recomputing
 *  (and thus jumping) on every display tick. */
function currentRemainingEstimateS() {
  if (state.remainingEstimateS == null) return null;
  const elapsedSinceEstimateS = (Date.now() - state.remainingEstimateAtMs) / 1000;
  return Math.max(0, state.remainingEstimateS - elapsedSinceEstimateS);
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
    updateRemainingEstimate(job, simTime);
    renderRemainingTime();
  }
}

function renderRemainingTime() {
  const remainingS = currentRemainingEstimateS();
  setText("run-remaining", remainingS != null ? fmtDuration(remainingS) : "–");
  updateRunVerdict(remainingS);
}

/** A clock time, with the date attached once the run reaches into another day. */
function formatClock(date) {
  const time = date.toLocaleTimeString(getLang(), { hour: "2-digit", minute: "2-digit" });
  const sameDay = date.toDateString() === new Date().toDateString();
  return sameDay ? time : `${date.toLocaleDateString(getLang(), { day: "2-digit", month: "2-digit" })} ${time}`;
}

/** The one line that answers "how is it going?" before any of the detail readouts.
 *
 *  Expressed as a point in time, because that is the decision hanging on it -- "done by 17:40"
 *  needs no arithmetic, "3 h 12 min left" does. */
function updateRunVerdict(remainingS) {
  const verdict = el("run-verdict");
  if (!verdict) return;
  if (remainingS == null || !isFinite(remainingS)) {
    verdict.textContent = t("verdictStarting");
    return;
  }
  const end = new Date(Date.now() + remainingS * 1000);
  verdict.textContent = t("verdictEta", { clock: formatClock(end), remaining: fmtDuration(remainingS) });
}

function tickElapsedAndRemaining() {
  const job = currentRunningJob();
  if (!job || !job.started_at) {
    setText("run-elapsed", "–");
    return;
  }
  const elapsedS = (Date.now() - Date.parse(job.started_at)) / 1000;
  setText("run-elapsed", fmtDuration(elapsedS));
  // Ticks the already-computed estimate down by a second rather than recomputing it from
  // state.lastSimTime (which hasn't changed since the last real sample) -- see
  // updateRemainingEstimate for why recomputing here was the source of the jumpiness.
  if (state.remainingEstimateS != null) renderRemainingTime();
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
  const fanEl = el("sys-fan");
  if (fanEl) {
    // Why the value is missing belongs on the readout itself -- "no sensors" is a very
    // different situation from "this platform cannot read fans at all".
    const reasonKey = { no_sensors: "fanNoSensors", unsupported_platform: "fanUnsupported" }[msg.fan_status];
    fanEl.title = msg.fan_rpm != null || !reasonKey ? "" : t(reasonKey);
    fanEl.classList.toggle("unavailable", msg.fan_rpm == null);
  }

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
  const { ctx, width: w, height: h } = fitCanvas(canvas);
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

// ---------- Live plot (uPlot) ----------
//
// Drawn with uPlot rather than by hand: it keeps a crisp HiDPI canvas, brings a cursor readout
// ("what was the HRR at t = 180 s?") and stays fast with the thousands of samples a long run
// produces. The library is vendored under static/vendor -- no CDN at runtime, no build step.

const LIVE_PLOT_HEIGHT = 160;

let livePlot = null; // uPlot instance
let livePlotHost = null; // the container element it is mounted in
let livePlotKey = null; // signal/theme/axis the instance was built for

function livePlotData() {
  const samples = plotSeries().slice();
  // HRR starts at zero by definition, so the curve is anchored there; a device reading has no
  // such baseline and is plotted only over the samples that actually exist.
  if (state.plotSignal.kind === "hrr" && (samples.length === 0 || samples[0].t > 0.001)) {
    samples.unshift({ t: 0, v: 0 });
  }
  return [samples.map((d) => d.t), samples.map((d) => d.v)];
}

function destroyLivePlot() {
  if (livePlot) livePlot.destroy();
  livePlot = null;
  livePlotHost = null;
  livePlotKey = null;
}

function livePlotSeriesLabel() {
  const unit = state.plotSignal.unit ? ` (${state.plotSignal.unit})` : "";
  return state.plotSignal.kind === "hrr" ? `${t("liveHrr")}${unit}` : `${state.plotSignal.device}${unit}`;
}

function livePlotOptions(width, simEndTime) {
  const line = token("--line", "#232a37");
  const faint = token("--text-faint", "#7b8494");
  const accent = token("--accent", "#ff6a3d");
  const [r, g, b] = parseHexColor(accent, [255, 106, 61]);
  const font = "10px " + token("--font-mono", "monospace");
  const axis = {
    stroke: faint,
    font,
    grid: { stroke: line, width: 1, dash: [1, 3] },
    ticks: { stroke: line, width: 1, size: 3 },
  };

  return {
    width,
    height: LIVE_PLOT_HEIGHT,
    padding: [10, 12, 0, 0],
    // The cursor readout is the point of the legend here; series toggling would only let the
    // single curve be switched off by accident.
    legend: { show: true, live: true, isolate: true },
    cursor: { drag: { x: false, y: false }, points: { size: 6 } },
    scales: {
      // Fixed time axis where the case declares T_END: the curve then grows across a stable
      // axis instead of the axis rescaling under it on every sample.
      x: { time: false, range: simEndTime ? [0, simEndTime] : undefined },
      y: state.plotSignal.kind === "hrr" ? { range: (self, min, max) => [0, max > 0 ? max : 1] } : {},
    },
    axes: [{ ...axis, size: 26 }, { ...axis, size: 48 }],
    series: [
      { label: t("simTimeAxisLabel") },
      {
        label: livePlotSeriesLabel(),
        stroke: accent,
        width: 1.6,
        fill: `rgba(${r}, ${g}, ${b}, 0.10)`,
        points: { show: false },
      },
    ],
  };
}

function drawLivePlot() {
  const host = el("hrr-plot");
  if (!host) {
    destroyLivePlot(); // the running job's card is gone
    return;
  }

  const job = currentRunningJob();
  const simEndTime = (job && job.sim_end_time_s) || null;
  // Everything that is baked into the options rather than into the data: rebuilding on a
  // change is cheaper and simpler than patching a live instance.
  const key = [state.plotSignal.kind, state.plotSignal.device, state.plotSignal.unit, getTheme(), simEndTime].join("|");
  const width = Math.max(240, host.clientWidth || 0);
  const data = livePlotData();

  if (!livePlot || livePlotHost !== host || livePlotKey !== key) {
    destroyLivePlot();
    host.innerHTML = "";
    livePlot = new uPlot(livePlotOptions(width, simEndTime), data, host);
    livePlotHost = host;
    livePlotKey = key;
    return;
  }

  if (livePlot.width !== width) livePlot.setSize({ width, height: LIVE_PLOT_HEIGHT });
  livePlot.setData(data);
}

async function loadJobHistoryForChart(jobId) {
  // A new job means new devices -- start from HRR, which every case has.
  state.hrrHistory = [];
  state.deviceHistory = [];
  state.lastOut = null;
  state.stepSizes = [];
  state.anomalies = [];
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
    seedRemainingEstimateFromHistory(metrics.out_file_metrics);
  } catch (e) {
    // best effort only
  }
}

/** Opening a tab onto an already-running job (or reconnecting) would otherwise show "–" for the
 *  remaining time until the next live sample -- feed the two most recent history samples through
 *  the same rate estimator a live update uses, so the countdown starts warm instead of cold. */
function seedRemainingEstimateFromHistory(outFileMetrics) {
  const job = currentRunningJob();
  if (!job || !job.sim_end_time_s) return;
  const withTime = (outFileMetrics || []).filter((m) => m.simulation_time_s != null && m.timestamp);
  if (withTime.length < 2) return;
  const [first, last] = [withTime[0], withTime[withTime.length - 1]];
  state.lastProgressSample = { wallMs: Date.parse(first.timestamp), simTime: first.simulation_time_s };
  updateRemainingEstimate(job, last.simulation_time_s, Date.parse(last.timestamp));
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

// ---------- Compare runs (Stufe 4) ----------

let comparePlot = null;

function findJobById(id) {
  return state.jobs.find((j) => j.id === id) || state.archivedJobs.find((j) => j.id === id);
}

function updateCompareButton() {
  const btn = el("compare-btn");
  const count = state.compareSelection.size;
  btn.hidden = count === 0;
  btn.disabled = count < 2;
  btn.textContent = count > 0 ? `${t("compareButton")} (${count})` : t("compareButton");
}

const COMPARE_COLORS = ["--accent", "--data", "--ok", "--warn"];

async function openCompareOverlay() {
  const ids = Array.from(state.compareSelection);
  const jobs = ids.map(findJobById).filter(Boolean);
  const metricsById = new Map();
  await Promise.all(
    ids.map(async (id) => {
      try {
        let metrics = state.jobMetricsCache.get(id);
        if (!metrics) {
          metrics = await apiGet(`/api/jobs/${id}/metrics`);
          state.jobMetricsCache.set(id, metrics);
        }
        metricsById.set(id, metrics.out_file_metrics || []);
      } catch (e) {
        metricsById.set(id, []);
      }
    })
  );
  state.compareJobs = jobs.map((job) => ({ job, series: metricsById.get(job.id) || [] }));
  el("compare-threshold").value = "";
  el("compare-overlay").hidden = false;
  renderComparePlot();
  renderCompareKpiTable();
}

function closeCompareOverlay() {
  el("compare-overlay").hidden = true;
  if (comparePlot) {
    comparePlot.destroy();
    comparePlot = null;
  }
}

function comparePlotOptions(width) {
  const line = token("--line", "#232a37");
  const faint = token("--text-faint", "#7b8494");
  const font = "10px " + token("--font-mono", "monospace");
  const axis = { stroke: faint, font, grid: { stroke: line, width: 1, dash: [1, 3] }, ticks: { stroke: line, width: 1, size: 3 } };
  const series = [{ label: t("simTimeAxisLabel") }];
  state.compareJobs.forEach(({ job }, i) => {
    const color = token(COMPARE_COLORS[i % COMPARE_COLORS.length], "#4c9aff");
    series.push({ label: job.name, stroke: color, width: 1.6, points: { show: false } });
  });
  return {
    width,
    height: 240,
    padding: [10, 12, 0, 0],
    legend: { show: true, live: false },
    cursor: { drag: { x: false, y: false } },
    scales: { x: { time: false } },
    axes: [{ ...axis, size: 26 }, { ...axis, size: 48 }],
    series,
  };
}

/** uPlot needs one shared x-axis for every series -- build the union of every run's sample
 *  times (rounded to damp float jitter between independently-sampled runs) and align each
 *  run's HRR values onto it, leaving a gap (null) wherever that run has no sample at that time. */
function comparePlotData() {
  const xSet = new Set();
  for (const { series } of state.compareJobs) {
    for (const sample of series) {
      if (sample.simulation_time_s != null) xSet.add(Math.round(sample.simulation_time_s * 10) / 10);
    }
  }
  const xs = Array.from(xSet).sort((a, b) => a - b);
  const columns = [xs];
  for (const { series } of state.compareJobs) {
    const byX = new Map(series.map((s) => [Math.round((s.simulation_time_s ?? -1) * 10) / 10, s.total_hrr_kw]));
    columns.push(xs.map((x) => (byX.has(x) ? byX.get(x) : null)));
  }
  return columns;
}

function renderComparePlot() {
  const host = el("compare-plot");
  host.innerHTML = "";
  if (comparePlot) {
    comparePlot.destroy();
    comparePlot = null;
  }
  const width = Math.max(240, host.clientWidth || 600);
  comparePlot = new uPlot(comparePlotOptions(width), comparePlotData(), host);
}

function peakHrr(series) {
  return series.reduce((max, s) => (s.total_hrr_kw != null && s.total_hrr_kw > max ? s.total_hrr_kw : max), 0);
}

/** First sample time at which a run's HRR reaches the given threshold, or null if it never does
 *  (or the run has no HRR data at all). */
function timeToThreshold(series, thresholdKw) {
  const hit = series.find((s) => s.total_hrr_kw != null && s.total_hrr_kw >= thresholdKw);
  return hit ? hit.simulation_time_s : null;
}

function renderCompareKpiTable() {
  const thresholdRaw = el("compare-threshold").value;
  const threshold = thresholdRaw ? parseFloat(thresholdRaw) : null;
  el("compare-threshold-col").hidden = threshold == null;
  document.querySelectorAll("#compare-kpi-table .compare-threshold-cell").forEach((c) => c.remove());

  const tbody = el("compare-kpi-tbody");
  tbody.innerHTML = state.compareJobs
    .map(({ job, series }) => {
      const thresholdCell =
        threshold != null
          ? `<td class="n compare-threshold-cell">${
              timeToThreshold(series, threshold) != null ? `${timeToThreshold(series, threshold).toFixed(1)} s` : "–"
            }</td>`
          : "";
      return `<tr>
        <td>${esc(job.name)}</td>
        <td>${esc(job.project || "–")}</td>
        <td class="n">${series.length ? `${peakHrr(series).toFixed(1)} kW` : "–"}</td>
        ${threshold != null ? thresholdCell : ""}
        <td class="n">${fmtDuration(job.actual_duration_s)}</td>
        <td class="n">${job.energy_kwh != null ? `${job.energy_kwh.toFixed(2)} kWh` : "–"}</td>
        <td class="n">${job.energy_cost_eur != null ? `${job.energy_cost_eur.toFixed(2)} €` : "–"}</td>
      </tr>`;
    })
    .join("");
}

function exportCompareCsv() {
  const threshold = el("compare-threshold").value ? parseFloat(el("compare-threshold").value) : null;
  const header = ["name", "project", "peak_hrr_kw", "duration_s", "energy_kwh", "energy_cost_eur"];
  if (threshold != null) header.push("time_to_threshold_s");
  const lines = [header.join(",")];
  for (const { job, series } of state.compareJobs) {
    const row = [
      job.name,
      job.project || "",
      series.length ? peakHrr(series).toFixed(1) : "",
      job.actual_duration_s ?? "",
      job.energy_kwh ?? "",
      job.energy_cost_eur ?? "",
    ];
    if (threshold != null) row.push(timeToThreshold(series, threshold) ?? "");
    lines.push(row.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(","));
  }
  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "fdsrouter_vergleich.csv";
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
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
  handleDeepLink();
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
el("devc-tbody").addEventListener("click", (ev) => {
  const row = ev.target.closest("tr[data-device]");
  if (!row) return;
  const value = `devc:${row.dataset.device}`;
  const select = el("plot-signal");
  if (select) select.value = value;
  onPlotSignalChange(value);
});
restoreDevicePanelState();

el("upload-folder-name").addEventListener("input", updateUploadTargetHint);
el("upload-parent-select").addEventListener("change", updateUploadTargetHint);
el("upload-input").addEventListener("change", (ev) => {
  const files = Array.from(ev.target.files || []);
  // Suggest the case's own name for the working directory; the operator can still rename it.
  const fdsFile = files.find((file) => file.name.toLowerCase().endsWith(".fds"));
  if (fdsFile && !el("upload-folder-name").value.trim()) {
    el("upload-folder-name").value = fdsFile.name.replace(/\.fds$/i, "");
  }
  updateUploadTargetHint();
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
el("service-update-btn").addEventListener("click", () => serviceAction("update"));
el("service-restart-btn").addEventListener("click", () => serviceAction("restart"));
el("service-stop-btn").addEventListener("click", () => serviceAction("stop"));
el("ha-test-btn").addEventListener("click", testEnergyConnection);
el("notify-test-btn").addEventListener("click", testNotification);

el("console-close").addEventListener("click", closeConsoleLog);
el("console-copy").addEventListener("click", copyConsoleLog);
el("console-download").addEventListener("click", downloadConsoleLog);
el("console-overlay").addEventListener("click", (ev) => {
  if (ev.target === el("console-overlay")) closeConsoleLog();
});

el("auto-advance-toggle").addEventListener("change", (ev) => onAutoAdvanceToggle(ev.target.checked));
el("archive-btn").addEventListener("click", archiveFinishedJobs);

document.querySelectorAll("#status-filter .chip").forEach((chip) => {
  chip.addEventListener("click", () => setRunFilter(chip.dataset.filter));
});

el("runs-search").addEventListener("input", (ev) => {
  state.search = ev.target.value;
  renderJobs();
});

el("project-filter").addEventListener("change", (ev) => {
  state.projectFilter = ev.target.value;
  renderJobs();
});

el("compare-btn").addEventListener("click", openCompareOverlay);
el("compare-close").addEventListener("click", closeCompareOverlay);
el("compare-overlay").addEventListener("click", (ev) => {
  if (ev.target === el("compare-overlay")) closeCompareOverlay();
});
el("compare-threshold").addEventListener("input", renderCompareKpiTable);
el("compare-export-csv").addEventListener("click", exportCompareCsv);
el("compare-print").addEventListener("click", () => window.print());

// ---------- Deep links (/job/<id>) ----------

el("runs-list").addEventListener("click", (ev) => {
  const nameEl = ev.target.closest(".job-name");
  if (!nameEl) return;
  const li = nameEl.closest("li.job");
  if (!li || !li.dataset.jobId) return;
  if (location.pathname !== `/job/${li.dataset.jobId}`) {
    history.pushState(null, "", `/job/${li.dataset.jobId}`);
  }
});

window.addEventListener("popstate", () => {
  const m = location.pathname.match(/^\/job\/([^/]+)$/);
  state.deepLinkJobId = m ? decodeURIComponent(m[1]) : null;
  state.deepLinkHandled = false;
  handleDeepLink();
});

/** Scroll to and expand the job named in the URL, once the job list is loaded. Falls back to a
 *  direct fetch for an archived job that isn't in the default (non-archived) list. */
async function handleDeepLink() {
  if (!state.deepLinkJobId || state.deepLinkHandled) return;
  const id = state.deepLinkJobId;
  let job = findJobById(id);
  if (!job) {
    try {
      job = await apiGet(`/api/jobs/${id}`);
    } catch (e) {
      state.deepLinkHandled = true;
      return;
    }
  }
  if (["done", "failed", "cancelled"].includes(job.status)) {
    state.expandedHistoryIds.add(id);
    if (job.archived_at && state.filter !== "archive") {
      state.filter = "archive";
      document.querySelectorAll("#status-filter .chip").forEach((chip) => chip.classList.toggle("selected", chip.dataset.filter === "archive"));
      await refreshArchivedJobs();
    } else {
      renderJobs();
    }
  }
  state.deepLinkHandled = true;
  requestAnimationFrame(() => {
    const li = el("runs-list").querySelector(`li.job[data-job-id="${CSS.escape(id)}"]`);
    if (li) li.scrollIntoView({ behavior: "smooth", block: "center" });
  });
}

el("login-submit").addEventListener("click", submitLogin);
el("login-password").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") submitLogin();
});
el("logout-btn").addEventListener("click", logout);

el("operations-btn").addEventListener("click", openOperationsModal);
el("cluster-token-copy").addEventListener("click", copyClusterToken);
el("config-save-btn").addEventListener("click", saveEditableConfig);
el("operations-close").addEventListener("click", closeOperationsModal);
el("operations-overlay").addEventListener("click", (ev) => {
  if (ev.target === el("operations-overlay")) closeOperationsModal();
});

setTheme(getTheme());
applyStaticTranslations();
boot();
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
