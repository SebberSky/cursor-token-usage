#!/usr/bin/env bash
# Install Cursor Token Usage (hooks + CLI + status bar extension).
#
# Channels (default: stable):
#   curl -fsSL …/install.sh | bash
#   curl -fsSL …/install.sh | TOKEN_USAGE_CHANNEL=beta bash
#   curl -fsSL …/install.sh | TOKEN_USAGE_REF=v0.1.0 bash
#
# From a local checkout:
#   ./install.sh
#   TOKEN_USAGE_CHANNEL=beta ./install.sh
#
set -euo pipefail

REPO="${TOKEN_USAGE_REPO:-SebberSky/cursor-token-usage}"
# Channel manifests always resolve from this branch (pointers live on main).
CHANNEL_SOURCE_REF="${TOKEN_USAGE_CHANNEL_SOURCE_REF:-main}"
CHANNEL="${TOKEN_USAGE_CHANNEL:-stable}"
CHANNEL_EXPLICIT=0
if [[ -n "${TOKEN_USAGE_CHANNEL:-}" ]]; then
  CHANNEL_EXPLICIT=1
fi
# Explicit pin wins over channel.
PINNED_REF="${TOKEN_USAGE_REF:-}"
CURSOR_HOME="${HOME}/.cursor"
HOOKS_DIR="${CURSOR_HOME}/hooks"
PLUGIN_DIR="${CURSOR_HOME}/plugins/token-usage"
COMMANDS_DIR="${CURSOR_HOME}/commands"
HOOKS_JSON="${CURSOR_HOME}/hooks.json"
PYTHON_BIN="${TOKEN_USAGE_PYTHON:-/usr/bin/python3}"
CLEANUP_ROOT=0
ROOT=""
INSTALL_MODE="local"
RESOLVED_REF=""
RESOLVED_VERSION=""
RESOLVED_CHANNEL=""
RESOLVED_EXTENSION_VERSION=""
CHANNEL_MANIFEST_JSON=""

if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="$(command -v python3 || true)"
fi
if [[ -z "${PYTHON_BIN}" ]]; then
  echo "python3 not found" >&2
  exit 1
fi

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

fetch_url() {
  local url="$1"
  if have_cmd curl; then
    curl -fsSL "${url}"
  elif have_cmd wget; then
    wget -qO- "${url}"
  else
    echo "curl or wget required" >&2
    exit 1
  fi
}

archive_url_for_ref() {
  local ref="$1"
  # Tags: v1.2.3, v1.2.3-beta.1
  if [[ "${ref}" == v[0-9]* ]]; then
    echo "https://github.com/${REPO}/archive/refs/tags/${ref}.tar.gz"
    return
  fi
  # Short/full commit SHA
  if [[ "${ref}" =~ ^[0-9a-fA-F]{7,40}$ ]]; then
    echo "https://github.com/${REPO}/archive/${ref}.tar.gz"
    return
  fi
  echo "https://github.com/${REPO}/archive/refs/heads/${ref}.tar.gz"
}

download_repo() {
  local dest="$1"
  local ref="$2"
  local url
  url="$(archive_url_for_ref "${ref}")"

  echo "→ Downloading ${REPO}@${ref}"
  echo "  ${url}"
  mkdir -p "${dest}"
  if have_cmd curl; then
    if ! curl -fsSL "${url}" | tar -xz -C "${dest}" --strip-components=1; then
      # Tag miss → try heads (channel may still point at a branch)
      if [[ "${url}" == *"/refs/tags/"* ]]; then
        url="https://github.com/${REPO}/archive/refs/heads/${ref}.tar.gz"
        echo "  tag not found, trying branch: ${url}"
        curl -fsSL "${url}" | tar -xz -C "${dest}" --strip-components=1
      else
        return 1
      fi
    fi
  elif have_cmd wget; then
    if ! wget -qO- "${url}" | tar -xz -C "${dest}" --strip-components=1; then
      if [[ "${url}" == *"/refs/tags/"* ]]; then
        url="https://github.com/${REPO}/archive/refs/heads/${ref}.tar.gz"
        echo "  tag not found, trying branch: ${url}"
        wget -qO- "${url}" | tar -xz -C "${dest}" --strip-components=1
      else
        return 1
      fi
    fi
  else
    echo "curl or wget required for remote install" >&2
    exit 1
  fi
}

normalize_channel() {
  local raw
  raw="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  case "${raw}" in
    stable|prod|production|release) echo "stable" ;;
    beta|preview|next) echo "beta" ;;
    *)
      echo "Unknown channel '${1}' (use stable or beta)" >&2
      exit 1
      ;;
  esac
}

resolve_channel_manifest() {
  local channel="$1"
  local url="https://raw.githubusercontent.com/${REPO}/${CHANNEL_SOURCE_REF}/channels/${channel}.json"
  echo "→ Resolving channel '${channel}' from ${CHANNEL_SOURCE_REF}"
  echo "  ${url}"
  CHANNEL_MANIFEST_JSON="$(fetch_url "${url}")"
  export TOKEN_USAGE_CHANNEL_MANIFEST_JSON="${CHANNEL_MANIFEST_JSON}"
  eval "$(
    "${PYTHON_BIN}" - <<'PY'
import json, os, shlex
data = json.loads(os.environ["TOKEN_USAGE_CHANNEL_MANIFEST_JSON"])
ref = data.get("ref") or data.get("tag") or ""
version = data.get("version") or ""
channel = data.get("channel") or ""
ext = data.get("extension_version") or ""
if not ref:
    raise SystemExit("channel manifest missing ref/tag")
print(f"RESOLVED_REF={shlex.quote(str(ref))}")
print(f"RESOLVED_VERSION={shlex.quote(str(version))}")
print(f"RESOLVED_CHANNEL={shlex.quote(str(channel))}")
print(f"RESOLVED_EXTENSION_VERSION={shlex.quote(str(ext))}")
PY
  )"
}

read_local_version() {
  local path="$1/version.json"
  if [[ ! -f "${path}" ]]; then
    RESOLVED_VERSION="${RESOLVED_VERSION:-unknown}"
    RESOLVED_CHANNEL="${RESOLVED_CHANNEL:-local}"
    return
  fi
  eval "$(
    TOKEN_USAGE_VERSION_JSON_PATH="${path}" "${PYTHON_BIN}" - <<'PY'
import json, os, shlex
from pathlib import Path
data = json.loads(Path(os.environ["TOKEN_USAGE_VERSION_JSON_PATH"]).read_text(encoding="utf-8"))
print(f"RESOLVED_VERSION={shlex.quote(str(data.get('version') or 'unknown'))}")
print(f"RESOLVED_CHANNEL={shlex.quote(str(data.get('channel') or 'local'))}")
PY
  )"
}

resolve_root() {
  local script_path="${BASH_SOURCE[0]:-}"
  if [[ -n "${script_path}" && "${script_path}" != "bash" && -f "${script_path}" ]]; then
    local script_dir
    script_dir="$(cd "$(dirname "${script_path}")" && pwd)"
    if [[ -f "${script_dir}/hooks/token-usage-logger.py" ]]; then
      ROOT="${script_dir}"
      INSTALL_MODE="local"
      if [[ -n "${PINNED_REF}" ]]; then
        echo "→ Local checkout install (ignoring TOKEN_USAGE_REF=${PINNED_REF})"
      fi
      read_local_version "${ROOT}"
      if [[ "${CHANNEL_EXPLICIT}" -eq 1 ]]; then
        RESOLVED_CHANNEL="$(normalize_channel "${CHANNEL}")"
      fi
      RESOLVED_REF="$(git -C "${ROOT}" rev-parse --short HEAD 2>/dev/null || echo local)"
      return
    fi
  fi

  INSTALL_MODE="remote"
  if [[ -n "${PINNED_REF}" ]]; then
    RESOLVED_REF="${PINNED_REF}"
    RESOLVED_CHANNEL="pinned"
    RESOLVED_VERSION="${TOKEN_USAGE_VERSION:-${PINNED_REF}}"
  else
    RESOLVED_CHANNEL="$(normalize_channel "${CHANNEL}")"
    resolve_channel_manifest "${RESOLVED_CHANNEL}"
  fi

  ROOT="$(mktemp -d "${TMPDIR:-/tmp}/cursor-token-usage.XXXXXX")"
  CLEANUP_ROOT=1
  download_repo "${ROOT}" "${RESOLVED_REF}"
  if [[ ! -f "${ROOT}/hooks/token-usage-logger.py" ]]; then
    echo "Download succeeded but sources are missing — check REPO/REF/channel" >&2
    exit 1
  fi
  # Prefer version.json from the downloaded tree when present.
  if [[ -f "${ROOT}/version.json" ]]; then
    read_local_version "${ROOT}"
    # keep channel from manifest unless pinned
    if [[ -z "${PINNED_REF}" ]]; then
      RESOLVED_CHANNEL="$(normalize_channel "${CHANNEL}")"
    fi
  fi
}

write_install_meta() {
  export TOKEN_USAGE_META_PATH="${PLUGIN_DIR}/installed.json"
  export TOKEN_USAGE_META_VERSION="${RESOLVED_VERSION}"
  export TOKEN_USAGE_META_CHANNEL="${RESOLVED_CHANNEL}"
  export TOKEN_USAGE_META_REF="${RESOLVED_REF}"
  export TOKEN_USAGE_META_REPO="${REPO}"
  export TOKEN_USAGE_META_MODE="${INSTALL_MODE}"
  export TOKEN_USAGE_META_EXT="${RESOLVED_EXTENSION_VERSION}"
  "${PYTHON_BIN}" - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

path = Path(os.environ["TOKEN_USAGE_META_PATH"])
path.parent.mkdir(parents=True, exist_ok=True)
meta = {
    "name": "cursor-token-usage",
    "version": os.environ.get("TOKEN_USAGE_META_VERSION") or "unknown",
    "channel": os.environ.get("TOKEN_USAGE_META_CHANNEL") or "unknown",
    "ref": os.environ.get("TOKEN_USAGE_META_REF") or "",
    "repo": os.environ.get("TOKEN_USAGE_META_REPO") or "",
    "install_mode": os.environ.get("TOKEN_USAGE_META_MODE") or "",
    "extension_version": os.environ.get("TOKEN_USAGE_META_EXT") or "",
    "installed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}
path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
print(f"  wrote {path}")
print(f"  version {meta['version']} ({meta['channel']}) ref={meta['ref']}")
PY
}

cleanup() {
  if [[ "${CLEANUP_ROOT}" -eq 1 && -n "${ROOT}" && -d "${ROOT}" ]]; then
    rm -rf "${ROOT}"
  fi
}
trap cleanup EXIT

# Optional CLI: ./install.sh --channel beta
while [[ $# -gt 0 ]]; do
  case "$1" in
    --channel)
      CHANNEL="${2:-}"
      CHANNEL_EXPLICIT=1
      shift 2
      ;;
    --channel=*)
      CHANNEL="${1#*=}"
      CHANNEL_EXPLICIT=1
      shift
      ;;
    --ref)
      PINNED_REF="${2:-}"
      shift 2
      ;;
    --ref=*)
      PINNED_REF="${1#*=}"
      shift
      ;;
    --help|-h)
      cat <<'EOF'
Install Cursor Token Usage

Usage:
  ./install.sh [--channel stable|beta] [--ref <tag|branch|sha>]
  TOKEN_USAGE_CHANNEL=beta ./install.sh
  TOKEN_USAGE_REF=v0.1.0 ./install.sh

Env:
  TOKEN_USAGE_CHANNEL              stable (default) | beta
  TOKEN_USAGE_REF                  pin exact git tag/branch/sha (skips channel)
  TOKEN_USAGE_REPO                 GitHub owner/repo
  TOKEN_USAGE_CHANNEL_SOURCE_REF   branch for channel manifests (default: main)
EOF
      exit 0
      ;;
    *)
      echo "Unknown argument: $1 (try --help)" >&2
      exit 1
      ;;
  esac
done

resolve_root
EXT_SRC="${ROOT}/extension"

echo "→ Install target: version=${RESOLVED_VERSION:-unknown} channel=${RESOLVED_CHANNEL:-unknown} ref=${RESOLVED_REF:-local}"

mkdir -p "${HOOKS_DIR}" "${PLUGIN_DIR}" "${COMMANDS_DIR}" "${CURSOR_HOME}/extensions"

echo "→ Installing hook script"
cp "${ROOT}/hooks/token-usage-logger.py" "${HOOKS_DIR}/token-usage-logger.py"
chmod +x "${HOOKS_DIR}/token-usage-logger.py"

echo "→ Installing CLI viewer + pricing table"
cp "${ROOT}/bin/view.py" "${PLUGIN_DIR}/view.py"
if [[ -f "${ROOT}/bin/token_usage_cost.py" ]]; then
  cp "${ROOT}/bin/token_usage_cost.py" "${PLUGIN_DIR}/token_usage_cost.py"
fi
if [[ -f "${ROOT}/bin/token_usage_update.py" ]]; then
  cp "${ROOT}/bin/token_usage_update.py" "${PLUGIN_DIR}/token_usage_update.py"
fi
if [[ -f "${ROOT}/bin/pricing.json" ]]; then
  cp "${ROOT}/bin/pricing.json" "${PLUGIN_DIR}/pricing.json"
fi
if [[ -f "${ROOT}/version.json" ]]; then
  cp "${ROOT}/version.json" "${PLUGIN_DIR}/version.json"
fi
chmod +x "${PLUGIN_DIR}/view.py"
cp "${ROOT}/README.md" "${PLUGIN_DIR}/README.md" 2>/dev/null || true

echo "→ Installing Cursor command"
cp "${ROOT}/commands/token-usage.md" "${COMMANDS_DIR}/token-usage.md"

echo "→ Writing install metadata"
write_install_meta

echo "→ Merging ~/.cursor/hooks.json"
export TOKEN_USAGE_HOOKS_JSON="${HOOKS_JSON}"
export TOKEN_USAGE_PYTHON_BIN="${PYTHON_BIN}"
export TOKEN_USAGE_HOOK_SCRIPT="${HOOKS_DIR}/token-usage-logger.py"
"${PYTHON_BIN}" - <<'PY'
import json
import os
from pathlib import Path

hooks_path = Path(os.environ["TOKEN_USAGE_HOOKS_JSON"])
python_bin = os.environ["TOKEN_USAGE_PYTHON_BIN"]
hook_script = os.environ["TOKEN_USAGE_HOOK_SCRIPT"]
# Absolute path — Cursor resolves relative commands from $HOME, not ~/.cursor.
example = {
    "version": 1,
    "hooks": {
        "sessionStart": [
            {
                "command": f"{python_bin} {hook_script} sessionStart",
                "timeout": 10,
            }
        ],
        "beforeSubmitPrompt": [
            {
                "command": f"{python_bin} {hook_script} beforeSubmitPrompt",
                "timeout": 10,
            }
        ],
        "stop": [
            {
                "command": f"{python_bin} {hook_script} stop",
                "timeout": 10,
                "loop_limit": None,
            }
        ],
        "sessionEnd": [
            {
                "command": f"{python_bin} {hook_script} sessionEnd",
                "timeout": 10,
            }
        ],
    },
}

def is_ours(entry):
    cmd = str((entry or {}).get("command") or "")
    return "token-usage-logger.py" in cmd

if hooks_path.exists():
    data = json.loads(hooks_path.read_text(encoding="utf-8"))
else:
    data = {"version": 1, "hooks": {}}

if not isinstance(data, dict):
    data = {"version": 1, "hooks": {}}
data.setdefault("version", 1)
hooks = data.setdefault("hooks", {})
if not isinstance(hooks, dict):
    hooks = {}
    data["hooks"] = hooks

for event, entries in example["hooks"].items():
    existing = hooks.get(event) or []
    if not isinstance(existing, list):
        existing = []
    existing = [e for e in existing if not is_ours(e)]
    existing.extend(entries)
    hooks[event] = existing

hooks_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
print(f"  wrote {hooks_path}")
print(f"  hook   {hook_script}")
PY

echo "→ Packaging status bar extension"
TMP="$(mktemp -d)"
VSIX="${TMP}/token-usage-statusbar.vsix"
mkdir -p "${TMP}/vsix/extension"
cp "${EXT_SRC}/package.json" "${EXT_SRC}/extension.js" "${TMP}/vsix/extension/"
cat > "${TMP}/vsix/[Content_Types].xml" <<'EOF'
<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="json" ContentType="application/json"/>
  <Default Extension="js" ContentType="application/javascript"/>
  <Default Extension="vsixmanifest" ContentType="text/xml"/>
</Types>
EOF
VERSION="$(${PYTHON_BIN} -c 'import json,pathlib;print(json.loads(pathlib.Path("'"${EXT_SRC}"'/package.json").read_text())["version"])')"
if [[ -n "${RESOLVED_EXTENSION_VERSION}" ]]; then
  VERSION="${RESOLVED_EXTENSION_VERSION}"
fi
cat > "${TMP}/vsix/extension.vsixmanifest" <<EOF
<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011" xmlns:d="http://schemas.microsoft.com/developer/vsx-schema-design/2011">
  <Metadata>
    <Identity Language="en-US" Id="token-usage-statusbar" Version="${VERSION}" Publisher="guyrocker"/>
    <DisplayName>Token Usage Status Bar</DisplayName>
    <Description>Shows per-repo Cursor Agent token usage on the status bar</Description>
    <Categories>Other</Categories>
    <GalleryFlags>Public</GalleryFlags>
    <Properties>
      <Property Id="Microsoft.VisualStudio.Code.Engine" Value="^1.80.0"/>
    </Properties>
  </Metadata>
  <Installation>
    <InstallationTarget Id="Microsoft.VisualStudio.Code"/>
  </Installation>
  <Dependencies/>
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true"/>
  </Assets>
</PackageManifest>
EOF
(
  cd "${TMP}/vsix"
  zip -qr "${VSIX}" "[Content_Types].xml" extension.vsixmanifest extension
)

CURSOR_BIN="${CURSOR_BIN:-}"
if [[ -z "${CURSOR_BIN}" ]]; then
  if [[ -x "/Applications/Cursor.app/Contents/Resources/app/bin/cursor" ]]; then
    CURSOR_BIN="/Applications/Cursor.app/Contents/Resources/app/bin/cursor"
  elif command -v cursor >/dev/null 2>&1; then
    CURSOR_BIN="$(command -v cursor)"
  fi
fi

if [[ -n "${CURSOR_BIN}" ]]; then
  echo "→ Installing extension via Cursor CLI (v${VERSION})"
  "${CURSOR_BIN}" --install-extension "${VSIX}" --force
else
  echo "⚠ Cursor CLI not found — extension sources are at:"
  echo "   ${EXT_SRC}"
  echo "   Package a VSIX from that folder, or install Cursor CLI and re-run."
fi

rm -rf "${TMP}"

echo
echo "Done · cursor-token-usage ${RESOLVED_VERSION:-unknown} (${RESOLVED_CHANNEL:-unknown})"
echo "Next: in Cursor run “Developer: Reload Window”, then finish one Agent turn."
echo "CLI:  ${PYTHON_BIN} ${PLUGIN_DIR}/view.py latest"
echo "Meta: ${PLUGIN_DIR}/installed.json"
