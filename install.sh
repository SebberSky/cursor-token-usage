#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
CURSOR_HOME="${HOME}/.cursor"
HOOKS_DIR="${CURSOR_HOME}/hooks"
PLUGIN_DIR="${CURSOR_HOME}/plugins/token-usage"
COMMANDS_DIR="${CURSOR_HOME}/commands"
HOOKS_JSON="${CURSOR_HOME}/hooks.json"
EXT_SRC="${ROOT}/extension"
PYTHON_BIN="${TOKEN_USAGE_PYTHON:-/usr/bin/python3}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="$(command -v python3 || true)"
fi
if [[ -z "${PYTHON_BIN}" ]]; then
  echo "python3 not found" >&2
  exit 1
fi

mkdir -p "${HOOKS_DIR}" "${PLUGIN_DIR}" "${COMMANDS_DIR}" "${CURSOR_HOME}/extensions"

echo "→ Installing hook script"
cp "${ROOT}/hooks/token-usage-logger.py" "${HOOKS_DIR}/token-usage-logger.py"
chmod +x "${HOOKS_DIR}/token-usage-logger.py"

echo "→ Installing CLI viewer"
cp "${ROOT}/bin/view.py" "${PLUGIN_DIR}/view.py"
chmod +x "${PLUGIN_DIR}/view.py"
cp "${ROOT}/README.md" "${PLUGIN_DIR}/README.md" 2>/dev/null || true

echo "→ Installing Cursor command"
cp "${ROOT}/commands/token-usage.md" "${COMMANDS_DIR}/token-usage.md"

echo "→ Merging ~/.cursor/hooks.json"
export TOKEN_USAGE_HOOKS_JSON="${HOOKS_JSON}"
export TOKEN_USAGE_PYTHON_BIN="${PYTHON_BIN}"
"${PYTHON_BIN}" - <<'PY'
import json
import os
from pathlib import Path

hooks_path = Path(os.environ["TOKEN_USAGE_HOOKS_JSON"])
python_bin = os.environ["TOKEN_USAGE_PYTHON_BIN"]
example = {
    "version": 1,
    "hooks": {
        "beforeSubmitPrompt": [
            {
                "command": f"{python_bin} ./hooks/token-usage-logger.py beforeSubmitPrompt",
                "timeout": 10,
            }
        ],
        "stop": [
            {
                "command": f"{python_bin} ./hooks/token-usage-logger.py stop",
                "timeout": 10,
                "loop_limit": None,
            }
        ],
        "sessionEnd": [
            {
                "command": f"{python_bin} ./hooks/token-usage-logger.py sessionEnd",
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
  echo "→ Installing extension via Cursor CLI"
  "${CURSOR_BIN}" --install-extension "${VSIX}"
else
  echo "⚠ Cursor CLI not found — copy extension manually:"
  echo "   ${EXT_SRC}"
  echo "   or install the VSIX: ${VSIX}"
fi

rm -rf "${TMP}"

echo
echo "Done."
echo "Next: in Cursor run “Developer: Reload Window”, then finish one Agent turn."
echo "CLI:  ${PYTHON_BIN} ${PLUGIN_DIR}/view.py latest"
