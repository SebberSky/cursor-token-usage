const vscode = require("vscode");
const fs = require("fs");
const path = require("path");
const os = require("os");

const DATA_DIR = path.join(os.homedir(), ".cursor", "token-usage");
const WORKSPACES_DIR = path.join(DATA_DIR, "workspaces");

/** @type {vscode.StatusBarItem} */
let statusBarItem;
/** @type {fs.FSWatcher | null} */
let dirWatcher = null;
/** @type {fs.FSWatcher | null} */
let wsWatcher = null;
let refreshTimer = null;

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

function refresh() {
  if (!statusBarItem) {
    return;
  }

  const names = currentWorkspaceNames();
  if (!names.length) {
    statusBarItem.text = "$(dashboard) tokens —";
    statusBarItem.tooltip = "Open a folder to see repo token usage";
    statusBarItem.show();
    return;
  }

  const snap = loadWorkspaceSnapshot(names);
  if (!snap || !(snap.status || snap.brief)) {
    statusBarItem.text = "$(dashboard) tokens —";
    statusBarItem.tooltip = `No token usage yet for ${names.join(", ")}`;
    statusBarItem.show();
    return;
  }

  const labelSource = snap.status || snap.brief;
  const label = labelSource.length > 60 ? `${labelSource.slice(0, 57)}…` : labelSource;
  statusBarItem.text = `$(dashboard) ${label}`;
  statusBarItem.tooltip = [
    `repo: ${snap.workspace}`,
    snap.brief,
    "",
    snap.detail,
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

function showDetails() {
  const names = currentWorkspaceNames();
  const snap = loadWorkspaceSnapshot(names);
  if (!snap || !(snap.brief || snap.detail)) {
    vscode.window.showInformationMessage(
      names.length
        ? `No token usage recorded yet for ${names.join(", ")}.`
        : "Open a folder to see repo token usage."
    );
    return;
  }

  const detail = [snap.brief, "", snap.detail].filter((line, idx, arr) => !(line === "" && arr[idx - 1] === "")).join("\n");
  vscode.window
    .showInformationMessage(snap.brief || "Token usage", { modal: false }, "Copy details")
    .then((choice) => {
      if (choice === "Copy details") {
        vscode.env.clipboard.writeText(detail);
      }
    });

  const channel = vscode.window.createOutputChannel("Token Usage");
  channel.clear();
  channel.appendLine(`repo: ${snap.workspace}`);
  channel.appendLine(detail);
  channel.show(true);
}

/**
 * @param {vscode.ExtensionContext} context
 */
function activate(context) {
  statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 1000);
  statusBarItem.command = "tokenUsage.showLatest";
  context.subscriptions.push(statusBarItem);

  context.subscriptions.push(
    vscode.commands.registerCommand("tokenUsage.showLatest", showDetails),
    vscode.commands.registerCommand("tokenUsage.refresh", refresh),
    vscode.workspace.onDidChangeWorkspaceFolders(() => scheduleRefresh())
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
      name === "chats-index.json"
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
}

module.exports = {
  activate,
  deactivate,
};
