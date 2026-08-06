const vscode = require("vscode");
const fs = require("fs");
const path = require("path");
const os = require("os");
const https = require("https");

const DATA_DIR = path.join(os.homedir(), ".cursor", "token-usage");
const WORKSPACES_DIR = path.join(DATA_DIR, "workspaces");
const PLUGIN_DIR = path.join(os.homedir(), ".cursor", "plugins", "token-usage");
const INSTALLED_META = path.join(PLUGIN_DIR, "installed.json");
const UPDATE_STATE = path.join(PLUGIN_DIR, "update-check.json");
const UNIT_STATE_KEY = "tokenUsage.statusBarUnit";
const UNIT_SETTING = "tokenUsage.statusBarUnit";
const DEFAULT_REPO = "SebberSky/cursor-token-usage";

/** @typedef {"tokens" | "cost" | "both"} StatusUnit */

const UNIT_OPTIONS = [
  {
    id: /** @type {StatusUnit} */ ("tokens"),
    label: "Tokens only",
    description: "Show token counts on the status bar",
  },
  {
    id: /** @type {StatusUnit} */ ("cost"),
    label: "USD only",
    description: "Show estimated cost only (list rates)",
  },
  {
    id: /** @type {StatusUnit} */ ("both"),
    label: "Tokens + USD",
    description: "Show tokens and estimated cost",
  },
];

/** @type {vscode.StatusBarItem} */
let statusBarItem;
/** @type {vscode.ExtensionContext | null} */
let extensionContext = null;
/** @type {fs.FSWatcher | null} */
let dirWatcher = null;
/** @type {fs.FSWatcher | null} */
let wsWatcher = null;
/** @type {vscode.OutputChannel | null} */
let outputChannel = null;
let refreshTimer = null;
let updateCheckTimer = null;
let updateCheckInFlight = false;
/** @type {any} */
let lastUpdateState = null;

function readText(filePath) {
  try {
    return fs.readFileSync(filePath, "utf8").trim();
  } catch {
    return "";
  }
}

function readJson(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return null;
  }
}

function writeJson(filePath, data) {
  try {
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
    fs.writeFileSync(filePath, `${JSON.stringify(data, null, 2)}\n`, "utf8");
  } catch {
    // ignore
  }
}

function versionSummary() {
  const meta = readJson(INSTALLED_META);
  if (!meta) {
    return "version unknown";
  }
  const version = meta.version || "unknown";
  const channel = meta.channel || "unknown";
  return `${version} (${channel})`;
}

function currentWorkspaceNames() {
  const folders = vscode.workspace.workspaceFolders || [];
  return folders.map((folder) => path.basename(folder.uri.fsPath)).filter(Boolean);
}

function workspaceSnapshotDir(name) {
  const safe = String(name).replace(/[^a-zA-Z0-9._-]/g, "_");
  return path.join(WORKSPACES_DIR, safe);
}

function loadWorkspaceSnapshot(names) {
  for (const name of names) {
    const dir = workspaceSnapshotDir(name);
    const status = readText(path.join(dir, "latest-status.txt"));
    const brief = readText(path.join(dir, "latest.txt"));
    const detail = readText(path.join(dir, "latest-detail.txt"));
    const data = readJson(path.join(dir, "latest.json"));
    if (status || brief || data) {
      return {
        workspace: name,
        status: status || (data && data.status) || "",
        brief: brief || (data && data.brief) || "",
        detail: detail || (data && data.detail) || "",
        data,
      };
    }
  }
  return null;
}

/**
 * @returns {StatusUnit}
 */
function getStatusUnit() {
  const cfg = vscode.workspace.getConfiguration("tokenUsage");
  const fromSetting = cfg.get("statusBarUnit");
  if (fromSetting === "tokens" || fromSetting === "cost" || fromSetting === "both") {
    return fromSetting;
  }
  if (extensionContext) {
    const stored = extensionContext.globalState.get(UNIT_STATE_KEY);
    if (stored === "tokens" || stored === "cost" || stored === "both") {
      return stored;
    }
  }
  return "both";
}

/**
 * @param {StatusUnit} unit
 */
async function setStatusUnit(unit) {
  if (extensionContext) {
    await extensionContext.globalState.update(UNIT_STATE_KEY, unit);
  }
  try {
    await vscode.workspace
      .getConfiguration("tokenUsage")
      .update(UNIT_SETTING.split(".")[1], unit, vscode.ConfigurationTarget.Global);
  } catch {
    // settings contribution may be absent on old installs — globalState still works
  }
}

/**
 * @param {number | null | undefined} n
 */
function fmtCompact(n) {
  if (n == null || Number.isNaN(Number(n))) {
    return "-";
  }
  const value = Number(n);
  const abs = Math.abs(value);
  if (abs >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(2)}M`.replace(/\.?0+M$/, "M").replace(/(\.\d)0M$/, "$1M");
  }
  if (abs >= 1_000) {
    return `${(value / 1_000).toFixed(1)}k`.replace(/\.0k$/, "k");
  }
  return String(Math.round(value));
}

/**
 * @param {number | null | undefined} n
 * @param {{ compact?: boolean }} [opts]
 */
function fmtUsd(n, opts = {}) {
  if (n == null || Number.isNaN(Number(n))) {
    return null;
  }
  const amount = Number(n);
  const compact = Boolean(opts.compact);
  if (compact) {
    if (Math.abs(amount) >= 100) {
      return `$${amount.toFixed(0)}`;
    }
    if (Math.abs(amount) >= 10) {
      return `$${amount.toFixed(1)}`;
    }
    if (Math.abs(amount) >= 1) {
      return `$${amount.toFixed(2)}`;
    }
    if (Math.abs(amount) >= 0.01) {
      return `$${amount.toFixed(2)}`;
    }
    if (Math.abs(amount) > 0) {
      return `$${amount.toFixed(3)}`;
    }
    return "$0";
  }
  return `$${amount.toFixed(2)}`;
}

/**
 * @param {any} data
 */
function extractMetrics(data) {
  if (!data || typeof data !== "object") {
    return null;
  }
  const turnTokens = data.turn && data.turn.total_tokens;
  const chatTokens = data.chat_totals && data.chat_totals.total_tokens;
  const repoTokens = data.repo_totals && data.repo_totals.total_tokens;
  const turnCost =
    data.turn_cost && data.turn_cost.known ? data.turn_cost.usd : null;
  const chatCost =
    data.chat_cost && data.chat_cost.known ? data.chat_cost.usd : null;
  const repoCost =
    data.repo_cost && data.repo_cost.known ? data.repo_cost.usd : null;
  return {
    turnTokens: turnTokens != null ? Number(turnTokens) : null,
    chatTokens: chatTokens != null ? Number(chatTokens) : null,
    repoTokens: repoTokens != null ? Number(repoTokens) : null,
    turnCost: turnCost != null ? Number(turnCost) : null,
    chatCost: chatCost != null ? Number(chatCost) : null,
    repoCost: repoCost != null ? Number(repoCost) : null,
    prior: Boolean(data.prior_untracked),
  };
}

/**
 * @param {ReturnType<typeof extractMetrics>} metrics
 * @param {StatusUnit} unit
 */
function formatStatusFromMetrics(metrics, unit) {
  if (!metrics) {
    return null;
  }
  const parts = [];
  const showTokens = unit === "tokens" || unit === "both";
  const showCost = unit === "cost" || unit === "both";

  if (showTokens && metrics.turnTokens != null) {
    parts.push(`+${fmtCompact(metrics.turnTokens)}`);
  }
  if (showCost) {
    const usd = fmtUsd(metrics.turnCost, { compact: true });
    if (usd) {
      parts.push(unit === "cost" ? `+${usd}` : `~${usd}`);
    } else if (unit === "cost") {
      parts.push("+—");
    }
  }

  if (showTokens && metrics.chatTokens != null) {
    parts.push(`chat Σ${fmtCompact(metrics.chatTokens)}`);
  }
  if (showCost) {
    const usd = fmtUsd(metrics.chatCost, { compact: true });
    if (usd) {
      parts.push(unit === "cost" ? `chat ${usd}` : `~${usd}`);
    } else if (unit === "cost" && metrics.chatTokens != null) {
      parts.push("chat —");
    }
  }

  if (metrics.repoTokens != null || metrics.repoCost != null) {
    if (showTokens && metrics.repoTokens != null) {
      parts.push(`repo Σ${fmtCompact(metrics.repoTokens)}`);
    }
    if (showCost) {
      const usd = fmtUsd(metrics.repoCost, { compact: true });
      if (usd) {
        parts.push(unit === "cost" ? `repo ${usd}` : `~${usd}`);
      } else if (unit === "cost" && metrics.repoTokens != null) {
        parts.push("repo —");
      }
    }
  }

  if (metrics.prior) {
    parts.push("prior");
  }

  if (!parts.length) {
    return null;
  }
  return parts.join(" · ");
}

/**
 * @param {string} status
 */
function stripCostFromStatus(status) {
  return status
    .replace(/\s*·\s*~\$[0-9.,]+k?/gi, "")
    .replace(/\s*~\$[0-9.,]+k?/gi, "")
    .replace(/\s{2,}/g, " ")
    .replace(/\s·\s·/g, " · ")
    .trim();
}

/**
 * @param {string} status
 */
function costOnlyFromStatus(status) {
  const matches = status.match(/~?\$[0-9.,]+k?/gi) || [];
  if (!matches.length) {
    return null;
  }
  return matches.join(" · ");
}

/**
 * @param {{ status?: string, brief?: string, data?: any }} snap
 * @param {StatusUnit} unit
 */
function buildStatusLabel(snap, unit) {
  const metrics = extractMetrics(snap.data);
  const fromMetrics = formatStatusFromMetrics(metrics, unit);
  if (fromMetrics) {
    return fromMetrics;
  }

  const raw = snap.status || snap.brief || "";
  if (!raw) {
    return "";
  }
  if (unit === "tokens") {
    return stripCostFromStatus(raw);
  }
  if (unit === "cost") {
    return costOnlyFromStatus(raw) || "cost —";
  }
  return raw;
}

function unitLabel(unit) {
  const found = UNIT_OPTIONS.find((o) => o.id === unit);
  return found ? found.label : unit;
}

function normalizeChannel(raw) {
  const value = String(raw || "stable").trim().toLowerCase();
  if (value === "beta" || value === "preview" || value === "next") {
    return "beta";
  }
  return "stable";
}

/**
 * @param {string | null | undefined} value
 * @returns {any[] | null}
 */
function parseVersion(value) {
  if (!value || typeof value !== "string") {
    return null;
  }
  let text = value.trim();
  if (text.startsWith("v") || text.startsWith("V")) {
    text = text.slice(1);
  }
  const match = text.match(/^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+.*)?$/);
  if (!match) {
    return null;
  }
  const major = Number(match[1]);
  const minor = Number(match[2]);
  const patch = Number(match[3]);
  const pre = match[4];
  if (!pre) {
    return [major, minor, patch, 1, []];
  }
  const parts = pre.split(".").map((piece) => {
    if (/^\d+$/.test(piece)) {
      return [0, Number(piece)];
    }
    return [1, piece.toLowerCase()];
  });
  return [major, minor, patch, 0, parts];
}

function compareVersionKeys(a, b) {
  for (let i = 0; i < 4; i += 1) {
    if (a[i] !== b[i]) {
      return a[i] - b[i];
    }
  }
  const ap = a[4] || [];
  const bp = b[4] || [];
  const n = Math.max(ap.length, bp.length);
  for (let i = 0; i < n; i += 1) {
    if (i >= ap.length) {
      return -1;
    }
    if (i >= bp.length) {
      return 1;
    }
    const x = ap[i];
    const y = bp[i];
    if (x[0] !== y[0]) {
      return x[0] - y[0];
    }
    if (x[1] < y[1]) {
      return -1;
    }
    if (x[1] > y[1]) {
      return 1;
    }
  }
  return 0;
}

function isNewer(latest, installed) {
  const a = parseVersion(latest);
  const b = parseVersion(installed);
  if (!a || !b) {
    return Boolean(latest && installed && latest !== installed);
  }
  return compareVersionKeys(a, b) > 0;
}

function installCommand(channel, ref, repo) {
  const ownerRepo = repo || DEFAULT_REPO;
  const base = `curl -fsSL https://raw.githubusercontent.com/${ownerRepo}/main/install.sh | `;
  if (ref) {
    return `${base}TOKEN_USAGE_REF=${ref} bash`;
  }
  if (channel === "beta") {
    return `${base}TOKEN_USAGE_CHANNEL=beta bash`;
  }
  return `${base}bash`;
}

function fetchText(url) {
  return new Promise((resolve, reject) => {
    const req = https.get(
      url,
      {
        headers: { "User-Agent": "cursor-token-usage-extension/1.0" },
        timeout: 8000,
      },
      (res) => {
        if (res.statusCode && res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
          fetchText(res.headers.location).then(resolve, reject);
          return;
        }
        if (res.statusCode !== 200) {
          reject(new Error(`HTTP ${res.statusCode} for ${url}`));
          res.resume();
          return;
        }
        const chunks = [];
        res.on("data", (c) => chunks.push(c));
        res.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
      }
    );
    req.on("error", reject);
    req.on("timeout", () => {
      req.destroy(new Error("timeout"));
    });
  });
}

function loadUpdateState() {
  return readJson(UPDATE_STATE) || lastUpdateState;
}

function updatesEnabled() {
  return vscode.workspace.getConfiguration("tokenUsage").get("checkForUpdates") !== false;
}

function updateCheckIntervalHours() {
  const n = Number(
    vscode.workspace.getConfiguration("tokenUsage").get("updateCheckIntervalHours")
  );
  if (Number.isNaN(n) || n < 0) {
    return 12;
  }
  return n;
}

function channelSourceRef() {
  return (
    String(
      vscode.workspace.getConfiguration("tokenUsage").get("channelSourceRef") || "main"
    ).trim() || "main"
  );
}

function shouldUseCached(state, channel, intervalHours) {
  if (!state || !state.checked_at || !state.ok) {
    return false;
  }
  if (state.channel !== channel) {
    return false;
  }
  if (intervalHours <= 0) {
    return false;
  }
  const checked = Date.parse(state.checked_at);
  if (Number.isNaN(checked)) {
    return false;
  }
  const ageH = (Date.now() - checked) / 3_600_000;
  return ageH < intervalHours;
}

async function checkForUpdate({ force = false, quiet = false } = {}) {
  if (updateCheckInFlight) {
    return loadUpdateState();
  }
  if (!force && !updatesEnabled()) {
    return {
      ok: false,
      skipped: true,
      reason: "checkForUpdates disabled",
      update_available: false,
    };
  }

  const installed = readJson(INSTALLED_META) || {};
  const channel = normalizeChannel(installed.channel);
  const repo = String(installed.repo || DEFAULT_REPO);
  const sourceRef = channelSourceRef();
  const intervalHours = updateCheckIntervalHours();
  const previous = loadUpdateState() || {};

  if (!force && shouldUseCached(previous, channel, intervalHours)) {
    lastUpdateState = { ...previous, cached: true };
    return lastUpdateState;
  }

  updateCheckInFlight = true;
  const result = {
    ok: false,
    cached: false,
    checked_at: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
    installed_version: installed.version || null,
    channel,
    repo,
    source_ref: sourceRef,
    update_available: false,
    latest_version: null,
    latest_ref: null,
    latest_tag: null,
    install_command: null,
    dismissed_version: previous.dismissed_version || null,
    alerted_version: previous.alerted_version || null,
  };

  try {
    const url = `https://raw.githubusercontent.com/${repo}/${sourceRef}/channels/${channel}.json`;
    const raw = await fetchText(url);
    const manifest = JSON.parse(raw);
    const latestVersion = String(manifest.version || "");
    const latestRef = String(manifest.ref || manifest.tag || "");
    result.ok = true;
    result.latest_version = latestVersion || null;
    result.latest_ref = latestRef || null;
    result.latest_tag = manifest.tag || null;
    result.latest_published_at = manifest.published_at || null;
    result.manifest_url = url;
    result.update_available = Boolean(
      installed.version && latestVersion && isNewer(latestVersion, installed.version)
    );
    result.install_command = installCommand(channel, latestRef || null, repo);
    writeJson(UPDATE_STATE, result);
    lastUpdateState = result;
    if (!quiet && result.update_available) {
      await maybeAlertUpdate(result);
    }
    refresh();
    return result;
  } catch (err) {
    result.error = err && err.message ? err.message : String(err);
    writeJson(UPDATE_STATE, result);
    lastUpdateState = result;
    if (!quiet) {
      vscode.window.showWarningMessage(
        `Token Usage update check failed: ${result.error}`
      );
    }
    return result;
  } finally {
    updateCheckInFlight = false;
  }
}

function shouldAlert(state) {
  if (!state || !state.ok || !state.update_available) {
    return false;
  }
  const latest = state.latest_version;
  if (!latest) {
    return false;
  }
  if (state.dismissed_version === latest) {
    return false;
  }
  if (state.alerted_version === latest) {
    return false;
  }
  return true;
}

async function maybeAlertUpdate(state) {
  if (!shouldAlert(state)) {
    return;
  }
  const latest = state.latest_version;
  const installed = state.installed_version || "unknown";
  const choice = await vscode.window.showInformationMessage(
    `Token Usage update available: ${installed} → ${latest} (${state.channel})`,
    "Copy install command",
    "Dismiss"
  );
  const next = { ...state, alerted_version: latest };
  if (choice === "Dismiss") {
    next.dismissed_version = latest;
  }
  if (choice === "Copy install command" && state.install_command) {
    await vscode.env.clipboard.writeText(state.install_command);
    vscode.window.setStatusBarMessage("Install command copied", 2500);
  }
  writeJson(UPDATE_STATE, next);
  lastUpdateState = next;
  refresh();
}

async function showUpdateDetails(state, { interactive = true } = {}) {
  if (!state) {
    state = await checkForUpdate({ force: true, quiet: !interactive });
  }
  if (!state.ok) {
    if (interactive) {
      vscode.window.showWarningMessage(
        state.skipped
          ? `Update checks disabled (${state.reason || "setting off"})`
          : `Update check failed: ${state.error || "unknown error"}`
      );
    }
    return;
  }
  if (!state.update_available) {
    if (interactive) {
      vscode.window.showInformationMessage(
        `Token Usage is up to date: ${state.installed_version || "unknown"} (${state.channel})`
      );
    }
    return;
  }

  const msg = `Update available: ${state.installed_version} → ${state.latest_version} (${state.channel})`;
  if (!interactive) {
    return;
  }
  const choice = await vscode.window.showInformationMessage(
    msg,
    "Copy install command",
    "Dismiss"
  );
  if (choice === "Copy install command" && state.install_command) {
    await vscode.env.clipboard.writeText(state.install_command);
    vscode.window.setStatusBarMessage("Install command copied", 2500);
  }
  if (choice === "Dismiss") {
    const next = {
      ...state,
      dismissed_version: state.latest_version,
      alerted_version: state.latest_version,
    };
    writeJson(UPDATE_STATE, next);
    lastUpdateState = next;
    refresh();
  }
}

function refresh() {
  if (!statusBarItem) {
    return;
  }

  const names = currentWorkspaceNames();
  const unit = getStatusUnit();
  const updateState = loadUpdateState();
  const updateReady = Boolean(updateState && updateState.update_available);
  const icon = updateReady ? "$(cloud-download)" : "$(dashboard)";

  if (!names.length) {
    statusBarItem.text = `${icon} tokens —`;
    statusBarItem.tooltip = updateReady
      ? `Update available · ${updateState.installed_version} → ${updateState.latest_version}`
      : "Open a folder to see repo token usage";
    statusBarItem.backgroundColor = updateReady
      ? new vscode.ThemeColor("statusBarItem.warningBackground")
      : undefined;
    statusBarItem.show();
    return;
  }

  const snap = loadWorkspaceSnapshot(names);
  if (!snap || !(snap.status || snap.brief || snap.data)) {
    statusBarItem.text = updateReady
      ? `${icon} update ${updateState.latest_version}`
      : `${icon} tokens —`;
    statusBarItem.tooltip = updateReady
      ? `Update available: ${updateState.installed_version} → ${updateState.latest_version}\nClick for menu`
      : `No token usage yet for ${names.join(", ")}`;
    statusBarItem.backgroundColor = updateReady
      ? new vscode.ThemeColor("statusBarItem.warningBackground")
      : undefined;
    statusBarItem.show();
    return;
  }

  const labelSource = buildStatusLabel(snap, unit);
  let label = labelSource.length > 64 ? `${labelSource.slice(0, 61)}…` : labelSource;
  if (updateReady) {
    label = `${label} · ↑`;
  }
  statusBarItem.text = `${icon} ${label || "tokens —"}`;
  statusBarItem.backgroundColor = updateReady
    ? new vscode.ThemeColor("statusBarItem.warningBackground")
    : undefined;
  statusBarItem.tooltip = [
    `repo: ${snap.workspace}`,
    `unit: ${unitLabel(unit)}`,
    `installed: ${versionSummary()}`,
    updateReady
      ? `UPDATE AVAILABLE: ${updateState.installed_version} → ${updateState.latest_version}`
      : updateState && updateState.ok
        ? `updates: up to date (${updateState.latest_version || "?"})`
        : "updates: not checked yet",
    snap.brief,
    "",
    snap.detail,
    "",
    "Click for menu / unit / updates",
  ]
    .filter((line, idx, arr) => !(line === "" && arr[idx - 1] === ""))
    .join("\n");
  statusBarItem.show();
}

function scheduleRefresh() {
  if (refreshTimer) {
    clearTimeout(refreshTimer);
  }
  refreshTimer = setTimeout(refresh, 150);
}

function getOutputChannel() {
  if (!outputChannel) {
    outputChannel = vscode.window.createOutputChannel("Token Usage");
  }
  return outputChannel;
}

function openDetails(snap) {
  if (!snap || !(snap.brief || snap.detail)) {
    return;
  }
  const detail = [snap.brief, "", snap.detail]
    .filter((line, idx, arr) => !(line === "" && arr[idx - 1] === ""))
    .join("\n");
  const channel = getOutputChannel();
  channel.clear();
  channel.appendLine(`repo: ${snap.workspace}`);
  channel.appendLine(`unit: ${unitLabel(getStatusUnit())}`);
  channel.appendLine(`installed: ${versionSummary()}`);
  const updateState = loadUpdateState();
  if (updateState && updateState.update_available) {
    channel.appendLine(
      `update: ${updateState.installed_version} → ${updateState.latest_version}`
    );
    if (updateState.install_command) {
      channel.appendLine(`install: ${updateState.install_command}`);
    }
  }
  channel.appendLine(detail);
  channel.show(true);
}

async function pickUnitAppearance() {
  const current = getStatusUnit();
  const items = UNIT_OPTIONS.map((opt) => ({
    label: `${opt.id === current ? "$(check) " : "$(circle-outline) "}${opt.label}`,
    description: opt.description,
    id: opt.id,
  }));
  const picked = await vscode.window.showQuickPick(items, {
    title: "Token Usage · unit appearance",
    placeHolder: "How should the status bar show usage?",
  });
  if (!picked) {
    return;
  }
  await setStatusUnit(picked.id);
  refresh();
  vscode.window.setStatusBarMessage(
    `Token Usage unit: ${unitLabel(picked.id)}`,
    2500
  );
}

async function showMenu() {
  const names = currentWorkspaceNames();
  const snap = loadWorkspaceSnapshot(names);
  const current = getStatusUnit();
  const updateState = loadUpdateState();
  const updateReady = Boolean(updateState && updateState.update_available);

  /** @type {Array<vscode.QuickPickItem & { action: string }>} */
  const items = [
    {
      label: "$(info) Show details",
      description: snap ? snap.workspace : undefined,
      action: "details",
    },
    {
      label: "$(clippy) Copy details",
      action: "copy",
    },
    {
      label: "$(symbol-ruler) Unit appearance…",
      description: unitLabel(current),
      detail: "Tokens only · USD only · Tokens + USD",
      action: "unit",
    },
    {
      label: updateReady
        ? "$(cloud-download) Update available…"
        : "$(sync) Check for updates",
      description: updateReady
        ? `${updateState.installed_version} → ${updateState.latest_version}`
        : versionSummary(),
      detail: updateReady
        ? "Copy install command or dismiss alert"
        : "Compare installed version to channel latest",
      action: "update",
    },
    {
      label: "$(versions) Installed version",
      description: versionSummary(),
      action: "version",
    },
    {
      label: "$(refresh) Refresh",
      action: "refresh",
    },
  ];

  const picked = await vscode.window.showQuickPick(items, {
    title: `Token Usage · ${versionSummary()}`,
    placeHolder: "Choose an action",
  });
  if (!picked) {
    return;
  }

  if (picked.action === "unit") {
    await pickUnitAppearance();
    return;
  }
  if (picked.action === "refresh") {
    refresh();
    return;
  }
  if (picked.action === "update") {
    if (updateReady) {
      await showUpdateDetails(updateState, { interactive: true });
    } else {
      const state = await checkForUpdate({ force: true, quiet: false });
      if (state && state.ok && !state.update_available) {
        vscode.window.showInformationMessage(
          `Token Usage is up to date: ${state.installed_version || "unknown"} (${state.channel})`
        );
      } else if (state && state.ok && state.update_available) {
        await showUpdateDetails(state, { interactive: true });
      }
    }
    return;
  }
  if (picked.action === "version") {
    const meta = readJson(INSTALLED_META) || {};
    const lines = [
      `cursor-token-usage ${meta.version || "unknown"} (${meta.channel || "unknown"})`,
      meta.ref ? `ref: ${meta.ref}` : "",
      meta.installed_at ? `installed_at: ${meta.installed_at}` : "",
      meta.repo ? `repo: ${meta.repo}` : "",
      updateReady
        ? `update: ${updateState.installed_version} → ${updateState.latest_version}`
        : "",
    ].filter(Boolean);
    vscode.window.showInformationMessage(lines[0], { modal: false }, "Copy").then((choice) => {
      if (choice === "Copy") {
        vscode.env.clipboard.writeText(lines.join("\n"));
      }
    });
    return;
  }

  if (!snap || !(snap.brief || snap.detail)) {
    vscode.window.showInformationMessage(
      names.length
        ? `No token usage recorded yet for ${names.join(", ")}.`
        : "Open a folder to see repo token usage."
    );
    return;
  }

  const detail = [snap.brief, "", snap.detail]
    .filter((line, idx, arr) => !(line === "" && arr[idx - 1] === ""))
    .join("\n");

  if (picked.action === "copy") {
    await vscode.env.clipboard.writeText(detail);
    vscode.window.setStatusBarMessage("Token usage details copied", 2000);
    return;
  }

  openDetails(snap);
}

/**
 * @param {vscode.ExtensionContext} context
 */
function activate(context) {
  extensionContext = context;
  statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 1000);
  statusBarItem.command = "tokenUsage.showLatest";
  context.subscriptions.push(statusBarItem);
  lastUpdateState = readJson(UPDATE_STATE);

  context.subscriptions.push(
    vscode.commands.registerCommand("tokenUsage.showLatest", showMenu),
    vscode.commands.registerCommand("tokenUsage.refresh", refresh),
    vscode.commands.registerCommand("tokenUsage.pickUnitAppearance", pickUnitAppearance),
    vscode.commands.registerCommand("tokenUsage.checkForUpdates", async () => {
      const state = await checkForUpdate({ force: true, quiet: false });
      if (state && state.ok && !state.update_available) {
        vscode.window.showInformationMessage(
          `Token Usage is up to date: ${state.installed_version || "unknown"} (${state.channel})`
        );
      } else if (state && state.ok && state.update_available) {
        await showUpdateDetails(state, { interactive: true });
      }
    }),
    vscode.workspace.onDidChangeWorkspaceFolders(() => scheduleRefresh()),
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (
        e.affectsConfiguration("tokenUsage.statusBarUnit") ||
        e.affectsConfiguration("tokenUsage.checkForUpdates") ||
        e.affectsConfiguration("tokenUsage.updateCheckIntervalHours") ||
        e.affectsConfiguration("tokenUsage.channelSourceRef")
      ) {
        scheduleRefresh();
        if (e.affectsConfiguration("tokenUsage.checkForUpdates")) {
          setTimeout(() => {
            checkForUpdate({ force: false, quiet: true });
          }, 500);
        }
      }
    })
  );

  try {
    fs.mkdirSync(WORKSPACES_DIR, { recursive: true });
  } catch {
    // ignore
  }

  const onFsEvent = (_event, filename) => {
    if (!filename) {
      scheduleRefresh();
      return;
    }
    const name = String(filename);
    if (
      name === "latest-status.txt" ||
      name === "latest.txt" ||
      name === "latest.json" ||
      name === "latest-detail.txt" ||
      name === "chats-index.json" ||
      name === "update-check.json"
    ) {
      scheduleRefresh();
    }
  };

  try {
    dirWatcher = fs.watch(DATA_DIR, { persistent: true }, onFsEvent);
    context.subscriptions.push({
      dispose: () => {
        if (dirWatcher) {
          dirWatcher.close();
          dirWatcher = null;
        }
      },
    });
  } catch {
    // ignore
  }

  try {
    if (fs.existsSync(PLUGIN_DIR)) {
      const pluginWatcher = fs.watch(PLUGIN_DIR, { persistent: true }, onFsEvent);
      context.subscriptions.push({
        dispose: () => pluginWatcher.close(),
      });
    }
  } catch {
    // ignore
  }

  try {
    wsWatcher = fs.watch(WORKSPACES_DIR, { persistent: true, recursive: true }, () =>
      scheduleRefresh()
    );
    context.subscriptions.push({
      dispose: () => {
        if (wsWatcher) {
          wsWatcher.close();
          wsWatcher = null;
        }
      },
    });
  } catch {
    // recursive watch may be unsupported; polling covers it
  }

  const poll = setInterval(refresh, 3000);
  context.subscriptions.push({ dispose: () => clearInterval(poll) });

  // Background update checks: soon after start, then hourly timer (honors interval hours).
  setTimeout(() => {
    checkForUpdate({ force: false, quiet: true });
  }, 4000);
  updateCheckTimer = setInterval(() => {
    checkForUpdate({ force: false, quiet: true });
  }, 60 * 60 * 1000);
  context.subscriptions.push({
    dispose: () => {
      if (updateCheckTimer) {
        clearInterval(updateCheckTimer);
        updateCheckTimer = null;
      }
    },
  });

  refresh();
}

function deactivate() {
  if (dirWatcher) {
    dirWatcher.close();
    dirWatcher = null;
  }
  if (wsWatcher) {
    wsWatcher.close();
    wsWatcher = null;
  }
  if (refreshTimer) {
    clearTimeout(refreshTimer);
  }
  if (updateCheckTimer) {
    clearInterval(updateCheckTimer);
    updateCheckTimer = null;
  }
  if (outputChannel) {
    outputChannel.dispose();
    outputChannel = null;
  }
}

module.exports = {
  activate,
  deactivate,
};
