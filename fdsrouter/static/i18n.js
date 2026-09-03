"use strict";

const STRINGS = {
  de: {
    connecting: "verbinde…",
    nodeUnreachable: "Node nicht erreichbar",
    nodeSummary: "{hostname} · {cores} Kerne · {ram} GB RAM",

    newJob: "Neuer Job",
    settings: "Einstellungen",

    queueTitle: "Warteschlange",
    queueLoading: "Lade…",
    queueEmpty: "Warteschlange ist leer.",
    historyTitle: "Historie",
    historyEmpty: "Noch keine abgeschlossenen Läufe.",
    archiveButton: "Archivieren",
    archiveViewLabel: "Archiv",
    archiveEmpty: "Archiv ist leer.",
    archiveConfirm: "{count} abgeschlossene Läufe archivieren? Sie bleiben unter „Archiv“ einsehbar.",
    archiveNothing: "Es gibt keine abgeschlossenen Läufe zum Archivieren.",
    archiveFailed: "Archivieren fehlgeschlagen: {error}",
    archivedMeta: "archiviert: {date}",

    statusQueued: "wartend",
    statusRunning: "läuft",
    statusDone: "fertig",
    statusFailed: "fehlgeschlagen",
    statusCancelled: "abgebrochen",

    jobMetaRunning: "{machine} · {mpi} MPI-Prozess(e) · Zellen: {cells}",
    jobMetaQueued: "geschätzt: {duration} · Zellen: {cells} · {mpi} MPI",
    jobMetaDone: "Dauer: {duration}",
    unknownValue: "?",

    stopJob: "Job beenden",
    stopJobConfirm: "Job „{name}“ wirklich sofort beenden? Das kann nicht rückgängig gemacht werden.",
    removeJob: "Entfernen",

    liveElapsed: "Laufzeit",
    liveRemaining: "Restzeit",
    liveSimTime: "Sim-Zeit",
    liveHrr: "HRR",
    hrrAxisLabel: "HRR (kW)",
    simTimeAxisLabel: "Simulationszeit (s)",
    plotSignalLabel: "Dargestellte Größe: HRR oder eine Messstelle aus der DEVC-Ausgabe",

    startJob: "Start",
    startJobFailed: "Konnte Job nicht starten: {error}",
    autoAdvanceLabel: "Automatisch fortsetzen",

    systemPanelTitle: "System",
    perCoreTitle: "CPU-Kerne (Verlauf)",
    cpuTotal: "CPU gesamt",
    ram: "RAM",
    cpuTemperature: "CPU-Temperatur",
    fanSpeed: "Lüfter",

    consoleLog: "Konsolen-Log",
    viewLog: "Log anzeigen",
    logEmpty: "Log ist leer.",
    logUnavailable: "Log nicht verfügbar.",
    copyLog: "Kopieren",
    copyLogDone: "Kopiert",
    copyLogFailed: "Kopieren nicht möglich",
    downloadLog: "Als .txt speichern",

    showDetails: "Details",
    hideDetails: "Details ausblenden",
    detailNode: "Maschine",
    detailMeshCells: "Zellen",
    detailMpi: "MPI-Prozesse",
    detailEstimated: "Geschätzte Dauer",
    detailActual: "Tatsächliche Dauer",
    detailFinalSimTime: "Finale Sim-Zeit",
    detailPeakHrr: "Spitzen-HRR",
    detailWarnings: "Warnungen",
    detailStarted: "Gestartet",
    detailFinished: "Beendet",
    detailExitMessage: "Meldung",
    detailEnergy: "Energie",
    detailCost: "Kosten",
    detailLoading: "Lade Details…",
    detailUnavailable: "Keine Detaildaten verfügbar.",
    solarPoweredBadge: "PV-Strom",

    processDetailsTitle: "Prozessdetails",
    mpiProcessesTitle: "MPI-Prozesse",
    tablePid: "PID",
    tableCore: "Kern",
    tableCpuPercent: "CPU %",
    tableRamPercent: "RAM %",
    limitingMeshTitle: "Limitierendes Mesh",
    limitingMeshValue: "Mesh {mesh}",
    thermocouplesTitle: "Thermoelemente",
    tableDevice: "Gerät",
    tableValue: "Wert",
    noDevices: "Keine Geräte im Fall definiert.",
    noActiveJob: "Kein Job aktiv.",
    notAvailable: "n/a",

    externalJobsTitle: "Erkannte externe FDS-Läufe",
    externalJobMeta: "PID {pid} · {caseDir} · Sim-Zeit: {simTime} · HRR: {hrr}",

    newJobModalTitle: "Neuer Job",
    uploadSectionTitle: "Vom Rechner hochladen",
    browseSectionTitle: "Auf dem Server auswählen",
    uploadButton: "Hochladen",
    uploadHint: "Genau eine .fds-Datei, weitere Falldateien optional.",
    uploadRunning: "Übertrage… {percent} %",
    uploadDone: "Hochgeladen nach {dir}",
    uploadFailed: "Upload fehlgeschlagen: {error}",
    downloadResults: "Ergebnisse",
    downloadResultsTitle: "Ergebnisverzeichnis als ZIP herunterladen",
    noResults: "Für diesen Lauf liegen keine Ergebnisdateien vor.",
    browserUp: "übergeordneter Ordner",
    selectedFile: "Ausgewählte Datei",
    noFileSelected: "Keine Datei ausgewählt.",
    meshInfo: "Meshes: {meshes} · Zellen: {cells}",
    mpiProcessesLabel: "MPI-Prozesse",
    mpiProcessesHint: "max. {max} (1 pro Mesh)",
    cancel: "Abbrechen",
    enqueue: "Einreihen",
    enqueueFailed: "Konnte Job nicht einreihen: {error}",
    removeFailed: "Konnte Job nicht entfernen: {error}",
    reorderFailed: "Umsortieren fehlgeschlagen: {error}",
    stopFailed: "Konnte Job nicht beenden: {error}",

    settingsModalTitle: "Einstellungen",
    appearanceSettingsTitle: "Darstellung",
    languageLabel: "Sprache",
    themeLabel: "Farbschema",
    themeDark: "Dunkel",
    themeLight: "Hell",
    energySettingsTitle: "Energie / Stromkosten",
    haBaseUrlLabel: "Home-Assistant-URL",
    haTokenLabel: "Access Token",
    haEntityLabel: "Entity-ID (Leistung, W)",
    electricityPriceLabel: "Strompreis (€/kWh)",
    solarPoweredLabel: "PV-Strom",
    haTestButton: "Verbindung testen",
    haTestSuccess: "Verbunden: {watts} W",
    haTestFailure: "Verbindung fehlgeschlagen.",
    settingsSaveFailed: "Einstellungen konnten nicht gespeichert werden: {error}",
    save: "Speichern",
    close: "Schließen",
  },

  en: {
    connecting: "connecting…",
    nodeUnreachable: "Node unreachable",
    nodeSummary: "{hostname} · {cores} cores · {ram} GB RAM",

    newJob: "New Job",
    settings: "Settings",

    queueTitle: "Queue",
    queueLoading: "Loading…",
    queueEmpty: "Queue is empty.",
    historyTitle: "History",
    historyEmpty: "No completed runs yet.",
    archiveButton: "Archive",
    archiveViewLabel: "Archive",
    archiveEmpty: "Archive is empty.",
    archiveConfirm: "Archive {count} completed runs? They stay available under \"Archive\".",
    archiveNothing: "There are no completed runs to archive.",
    archiveFailed: "Archiving failed: {error}",
    archivedMeta: "archived: {date}",

    statusQueued: "queued",
    statusRunning: "running",
    statusDone: "done",
    statusFailed: "failed",
    statusCancelled: "cancelled",

    jobMetaRunning: "{machine} · {mpi} MPI process(es) · cells: {cells}",
    jobMetaQueued: "estimated: {duration} · cells: {cells} · {mpi} MPI",
    jobMetaDone: "Duration: {duration}",
    unknownValue: "?",

    stopJob: "Stop job",
    stopJobConfirm: "Really stop job \"{name}\" now? This cannot be undone.",
    removeJob: "Remove",

    liveElapsed: "Elapsed",
    liveRemaining: "ETA",
    liveSimTime: "Sim time",
    liveHrr: "HRR",
    hrrAxisLabel: "HRR (kW)",
    simTimeAxisLabel: "Simulation time (s)",
    plotSignalLabel: "Plotted quantity: HRR or a device from the DEVC output",

    startJob: "Start",
    startJobFailed: "Could not start job: {error}",
    autoAdvanceLabel: "Auto-continue",

    systemPanelTitle: "System",
    perCoreTitle: "CPU cores (history)",
    cpuTotal: "CPU total",
    ram: "RAM",
    cpuTemperature: "CPU temperature",
    fanSpeed: "Fan",

    consoleLog: "Console log",
    viewLog: "View log",
    logEmpty: "Log is empty.",
    logUnavailable: "Log unavailable.",
    copyLog: "Copy",
    copyLogDone: "Copied",
    copyLogFailed: "Copy not possible",
    downloadLog: "Save as .txt",

    showDetails: "Details",
    hideDetails: "Hide details",
    detailNode: "Machine",
    detailMeshCells: "Cells",
    detailMpi: "MPI processes",
    detailEstimated: "Estimated duration",
    detailActual: "Actual duration",
    detailFinalSimTime: "Final sim time",
    detailPeakHrr: "Peak HRR",
    detailWarnings: "Warnings",
    detailStarted: "Started",
    detailFinished: "Finished",
    detailExitMessage: "Message",
    detailEnergy: "Energy",
    detailCost: "Cost",
    detailLoading: "Loading details…",
    detailUnavailable: "No detail data available.",
    solarPoweredBadge: "Solar power",

    processDetailsTitle: "Process details",
    mpiProcessesTitle: "MPI processes",
    tablePid: "PID",
    tableCore: "Core",
    tableCpuPercent: "CPU %",
    tableRamPercent: "RAM %",
    limitingMeshTitle: "Limiting mesh",
    limitingMeshValue: "Mesh {mesh}",
    thermocouplesTitle: "Thermocouples",
    tableDevice: "Device",
    tableValue: "Value",
    noDevices: "No devices defined in this case.",
    noActiveJob: "No active job.",
    notAvailable: "n/a",

    externalJobsTitle: "Detected external FDS runs",
    externalJobMeta: "PID {pid} · {caseDir} · sim time: {simTime} · HRR: {hrr}",

    newJobModalTitle: "New Job",
    uploadSectionTitle: "Upload from your computer",
    browseSectionTitle: "Choose on the server",
    uploadButton: "Upload",
    uploadHint: "Exactly one .fds file, further case files optional.",
    uploadRunning: "Uploading… {percent} %",
    uploadDone: "Uploaded to {dir}",
    uploadFailed: "Upload failed: {error}",
    downloadResults: "Results",
    downloadResultsTitle: "Download the result directory as a ZIP",
    noResults: "There are no result files for this run.",
    browserUp: "parent folder",
    selectedFile: "Selected file",
    noFileSelected: "No file selected.",
    meshInfo: "Meshes: {meshes} · cells: {cells}",
    mpiProcessesLabel: "MPI processes",
    mpiProcessesHint: "max. {max} (1 per mesh)",
    cancel: "Cancel",
    enqueue: "Enqueue",
    enqueueFailed: "Could not enqueue job: {error}",
    removeFailed: "Could not remove job: {error}",
    reorderFailed: "Reordering failed: {error}",
    stopFailed: "Could not stop job: {error}",

    settingsModalTitle: "Settings",
    appearanceSettingsTitle: "Appearance",
    languageLabel: "Language",
    themeLabel: "Colour scheme",
    themeDark: "Dark",
    themeLight: "Light",
    energySettingsTitle: "Energy / electricity cost",
    haBaseUrlLabel: "Home Assistant URL",
    haTokenLabel: "Access token",
    haEntityLabel: "Entity ID (power, W)",
    electricityPriceLabel: "Electricity price (€/kWh)",
    solarPoweredLabel: "Solar-powered",
    haTestButton: "Test connection",
    haTestSuccess: "Connected: {watts} W",
    haTestFailure: "Connection failed.",
    settingsSaveFailed: "Could not save settings: {error}",
    save: "Save",
    close: "Close",
  },
};

const LANG_STORAGE_KEY = "fdsrouter.lang";
const THEME_STORAGE_KEY = "fdsrouter.theme";

function getLang() {
  try {
    const stored = localStorage.getItem(LANG_STORAGE_KEY);
    if (stored && STRINGS[stored]) return stored;
  } catch (e) {
    // localStorage unavailable -- fall through to default
  }
  return "de";
}

function setLang(lang) {
  if (!STRINGS[lang]) return;
  try {
    localStorage.setItem(LANG_STORAGE_KEY, lang);
  } catch (e) {
    // best effort only
  }
}

function getTheme() {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch (e) {
    // localStorage unavailable -- fall through to default
  }
  return "dark";
}

function setTheme(theme) {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch (e) {
    // best effort only
  }
  document.documentElement.setAttribute("data-theme", theme);
}

function t(key, vars) {
  const lang = getLang();
  let template = (STRINGS[lang] && STRINGS[lang][key]) || STRINGS.de[key] || key;
  if (vars) {
    for (const [k, v] of Object.entries(vars)) {
      template = template.replaceAll(`{${k}}`, v);
    }
  }
  return template;
}

function applyStaticTranslations() {
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-title]").forEach((el) => {
    el.title = t(el.dataset.i18nTitle);
  });
}
