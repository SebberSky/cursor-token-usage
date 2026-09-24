# Cursor Token Usage

Track **per-prompt / per-chat / per-repo** token usage for [Cursor](https://cursor.com) Agent — locally, with no cloud sync.

After each Agent turn finishes, usage is recorded from Cursor’s `stop` hook payload and shown on the **IDE status bar** for the repo you currently have open.

```text
+277.4k · chat 1.75M · repo 1.75M
```

## Features

- **Per-turn logging** from Cursor `stop` hook (`input_tokens`, `output_tokens`, cache fields)
- **Consecutive chat totals** — continuing the same chat accumulates usage
- **Per-repo isolation** — status bar only shows the open workspace
- **Prior-history warning** when you continue a chat that existed before the hook was installed (transcript has older prompts that cannot be backfilled)
- **CLI viewer** + optional `/token-usage` Cursor command
- **Local only** — data stays under `~/.cursor/token-usage/`
- **Estimated USD cost** from Cursor list rates (Auto Cost + third-party models)

## How it works

```text
  You send a prompt
        │
        ▼
  beforeSubmitPrompt  ──► pending prompt metadata
        │
        ▼
  Agent runs …
        │
        ▼
  stop hook  ──► append turn to chat ledger
             ──► update repo snapshot
             ──► refresh status bar files
```

Cursor does **not** expose token counts inside chat transcripts. The `stop` hook payload is the source of truth.

> **Note:** The `stop` hook cannot inject text into the chat transcript. This project surfaces usage on the status bar instead (dialogs are off by default).

## Requirements

- Cursor Desktop (hooks + extensions)
- Python 3.9+
- macOS recommended for optional Notification Center banners (`terminal-notifier` optional)

## Install

Installers follow a **release channel** (`stable` default, or `beta`). Channel pointers live in [`channels/`](channels/) on `main`.

### Stable (default)

```bash
curl -fsSL https://raw.githubusercontent.com/SebberSky/cursor-token-usage/main/install.sh | bash
```

### Beta

```bash
curl -fsSL https://raw.githubusercontent.com/SebberSky/cursor-token-usage/main/install.sh \
  | TOKEN_USAGE_CHANNEL=beta bash
```

### Pin a version / ref

```bash
# exact tag
curl -fsSL https://raw.githubusercontent.com/SebberSky/cursor-token-usage/main/install.sh \
  | TOKEN_USAGE_REF=v0.1.0 bash

# or flags when running from a checkout
./install.sh --channel beta
./install.sh --ref v0.2.0-beta.1
```

### From a clone

```bash
git clone https://github.com/SebberSky/cursor-token-usage.git
cd cursor-token-usage
./install.sh                 # installs this tree as-is
./install.sh --channel beta  # records channel label; files still from local tree
```

Then in Cursor:

1. **Reload Window** — `Cmd+Shift+P` → `Developer: Reload Window`
2. Confirm hooks are enabled (Cursor Settings → Hooks, if shown)
3. Send an Agent prompt, wait for the turn to finish
4. Check the bottom-right status bar

### Channels & release versions

| Channel | Manifest | Install picks |
| --- | --- | --- |
| `stable` | [`channels/stable.json`](channels/stable.json) | production tags (`vX.Y.Z`) |
| `beta` | [`channels/beta.json`](channels/beta.json) | pre-release tags (`vX.Y.Z-beta.N`) |

| Variable / flag | Meaning |
| --- | --- |
| `TOKEN_USAGE_CHANNEL` / `--channel` | `stable` (default) or `beta` |
| `TOKEN_USAGE_REF` / `--ref` | pin tag/branch/sha (skips channel resolution) |
| `TOKEN_USAGE_CHANNEL_SOURCE_REF` | branch used to load channel manifests (default `main`) |
| `TOKEN_USAGE_REPO` | GitHub `owner/repo` |

After install, metadata is written to `~/.cursor/plugins/token-usage/installed.json`.

```bash
python3 ~/.cursor/plugins/token-usage/view.py version
python3 ~/.cursor/plugins/token-usage/view.py check-update
```

### Updates & alerts

The status bar extension checks the active channel pointer on GitHub (default every **12 hours**):

- status bar shows `$(cloud-download)` when an update is available
- toast once per newer version: **Install now** / **Dismiss** (runs `install.sh` for you)
- menu: **Check for updates** (or **Update available…**)
- command palette: `Token Usage: Check for Updates`

| Setting | Default | Meaning |
| --- | --- | --- |
| `tokenUsage.checkForUpdates` | `true` | enable background checks |
| `tokenUsage.updateCheckIntervalHours` | `12` | min hours between automatic checks |
| `tokenUsage.channelSourceRef` | `main` | branch used to read `channels/*.json` |

CLI: `TOKEN_USAGE_CHECK_UPDATES=0` disables Python/CLI checks. Dismiss: `view.py check-update --dismiss`.

### Cut a release (maintainers)

```bash
./scripts/release.sh 0.2.0 stable           # commit + tag v0.2.0, update channels/stable.json
./scripts/release.sh 0.3.0-beta.1 beta      # pre-release on beta channel
./scripts/release.sh 0.2.0 stable --push    # also push commit + tag
./scripts/release.sh 0.2.0 stable --dry-run
```

Tree version lives in [`version.json`](version.json). Extension `package.json` version is kept semver-compatible for VSIX.

### What `install.sh` does

| Piece | Destination |
| --- | --- |
| Hook script | `~/.cursor/hooks/token-usage-logger.py` |
| Hook config | merges into `~/.cursor/hooks.json` |
| CLI viewer | `~/.cursor/plugins/token-usage/view.py` |
| Pricing table | `~/.cursor/plugins/token-usage/pricing.json` (+ `token_usage_cost.py`) |
| Install meta | `~/.cursor/plugins/token-usage/installed.json` |
| Update check | `~/.cursor/plugins/token-usage/update-check.json` (+ `token_usage_update.py`) |
| Slash command | `~/.cursor/commands/token-usage.md` |
| Status bar extension | installs VSIX into Cursor |

Existing unrelated hooks in `~/.cursor/hooks.json` are preserved.

## Status bar

Scoped to the **current folder name** (workspace root basename):

| Open folder | Shows |
| --- | --- |
| `~/git/trueid-office` | trueid-office usage only |
| `~/git/proxyGuy` | proxyGuy usage only |

Click the status bar item for one menu:

- **Unit** — Tokens only · USD only · Tokens + USD
- **Check for updates** / **Update available…**
- Show details / copy / installed version

Format:

```text
+<this turn> · ~$0.12 · chat <short-id> <this chat total> · ~$1.40 · repo <all chats in repo>
```

`$` amounts are **estimates** from [Cursor Models & Pricing](https://cursor.com/docs/models-and-pricing). They are not invoices.

Closing a chat (`sessionEnd`) does **not** overwrite the status bar — only a completed agent `stop` updates the active snapshot for that workspace.

## CLI

```bash
python3 ~/.cursor/plugins/token-usage/view.py latest
python3 ~/.cursor/plugins/token-usage/view.py latest --workspace trueid-ios-v3
python3 ~/.cursor/plugins/token-usage/view.py latest --expand
python3 ~/.cursor/plugins/token-usage/view.py latest --markdown
python3 ~/.cursor/plugins/token-usage/view.py latest 4a51dab0   # specific chat
python3 ~/.cursor/plugins/token-usage/view.py chats
python3 ~/.cursor/plugins/token-usage/view.py chat            # most recent chat
python3 ~/.cursor/plugins/token-usage/view.py chat 6067cff6   # by id prefix
python3 ~/.cursor/plugins/token-usage/view.py today
python3 ~/.cursor/plugins/token-usage/view.py version
python3 ~/.cursor/plugins/token-usage/view.py check-update
python3 ~/.cursor/plugins/token-usage/view.py debug
```

Compact line example:

```text
prompt นี้ใช้ไป 277,365 tokens · รวม 1,748,052 tokens
```

## Data layout

All local:

```text
~/.cursor/token-usage/
  usage.jsonl                 # append-only event log
  chats-index.json
  chats/<conversation_id>.json
  chats/<conversation_id>.latest.json   # per-chat prompt/chat snapshot
  workspaces/<repo>/          # status bar snapshots (last stop in this repo)
    latest-status.txt
    latest.txt
    latest-detail.txt
    latest.json
  last-stop-payload.json      # raw stop payload (debug)
  meta.json
```

Nothing is uploaded. Delete the folder anytime to reset.

## Configuration

Environment variables (optional):

| Variable | Default | Meaning |
| --- | --- | --- |
| `TOKEN_USAGE_NOTIFY` | `1` | macOS banner notifications |
| `TOKEN_USAGE_ALERT` | `0` | blocking alert dialog (not recommended) |
| `TOKEN_USAGE_FULL_PROMPT` | `0` | store full prompt text (default stores a short preview only) |
| `TOKEN_USAGE_PRICING` | `1` | estimate USD from published list rates |
| `TOKEN_USAGE_DEFAULT_RATE` | `auto_cost` | rate key when model is `default` / unknown (`off` to skip) |
| `TOKEN_USAGE_CURSOR_TOKEN_RATE` | `0` | add Teams/Enterprise `$0.25/M` on third-party models |
| `TOKEN_USAGE_PRICING_PATH` | (plugin `pricing.json`) | override pricing table path |

Example:

```bash
export TOKEN_USAGE_NOTIFY=0
```

## Limitations

- **No historical backfill** for chats that started before the hook was installed — Cursor transcripts do not include token counts
- **User-level hooks** apply to local Cursor; Cloud Agents use project/team hooks instead
- Token fields depend on Cursor sending them on `stop` (current Cursor builds do)
- Status bar updates after the Agent turn completes (`stop`), not mid-stream
- Cost is an **estimate**: Auto routes as `default` use Auto Cost list rates; first-party models (Grok / Composer) have no public $/token list so cost is omitted; promotions / Max Mode / residency uplift may not match

## Uninstall

1. Remove hook entries for `token-usage-logger.py` from `~/.cursor/hooks.json`
2. Delete `~/.cursor/hooks/token-usage-logger.py`
3. Uninstall extension **Token Usage Status Bar** from Cursor
4. Optionally delete `~/.cursor/token-usage/` and `~/.cursor/plugins/token-usage/`

## Project layout

```text
cursor-token-usage/
  README.md
  install.sh
  version.json
  channels/
    stable.json
    beta.json
  scripts/
    release.sh
  hooks/
    token-usage-logger.py
    hooks.json.example
  bin/
    view.py
    token_usage_cost.py
    token_usage_update.py
    pricing.json
  extension/
    package.json
    extension.js
  commands/
    token-usage.md
```

## License

MIT
