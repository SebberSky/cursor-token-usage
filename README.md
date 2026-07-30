# Cursor Token Usage

Track **per-prompt / per-chat / per-repo** token usage for [Cursor](https://cursor.com) Agent — locally, with no cloud sync.

After each Agent turn finishes, usage is recorded from Cursor’s `stop` hook payload and shown on the **IDE status bar** for the repo you currently have open.

```text
+277.4k · chat Σ1.75M · repo Σ1.75M
```

## Features

- **Per-turn logging** from Cursor `stop` hook (`input_tokens`, `output_tokens`, cache fields)
- **Consecutive chat totals** — continuing the same chat accumulates usage
- **Per-repo isolation** — status bar only shows the open workspace
- **Prior-history warning** when you continue a chat that existed before the hook was installed (transcript has older prompts that cannot be backfilled)
- **CLI viewer** + optional `/token-usage` Cursor command
- **Local only** — data stays under `~/.cursor/token-usage/`

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

```bash
git clone git@github.com:SebberSky/cursor-token-usage.git
cd cursor-token-usage
./install.sh
```

Then in Cursor:

1. **Reload Window** — `Cmd+Shift+P` → `Developer: Reload Window`
2. Confirm hooks are enabled (Cursor Settings → Hooks, if shown)
3. Send an Agent prompt, wait for the turn to finish
4. Check the bottom-right status bar

### What `install.sh` does

| Piece | Destination |
| --- | --- |
| Hook script | `~/.cursor/hooks/token-usage-logger.py` |
| Hook config | merges into `~/.cursor/hooks.json` |
| CLI viewer | `~/.cursor/plugins/token-usage/view.py` |
| Slash command | `~/.cursor/commands/token-usage.md` |
| Status bar extension | installs VSIX into Cursor |

Existing unrelated hooks in `~/.cursor/hooks.json` are preserved.

## Status bar

Scoped to the **current folder name** (workspace root basename):

| Open folder | Shows |
| --- | --- |
| `~/git/trueid-office` | trueid-office usage only |
| `~/git/proxyGuy` | proxyGuy usage only |

Click the status bar item to open details in the **Token Usage** output channel.

Format:

```text
+<this turn> · chat Σ<this chat> · repo Σ<all chats in repo>
```

## CLI

```bash
python3 ~/.cursor/plugins/token-usage/view.py latest
python3 ~/.cursor/plugins/token-usage/view.py latest --expand
python3 ~/.cursor/plugins/token-usage/view.py latest --markdown
python3 ~/.cursor/plugins/token-usage/view.py chats
python3 ~/.cursor/plugins/token-usage/view.py chat            # most recent chat
python3 ~/.cursor/plugins/token-usage/view.py chat 6067cff6   # by id prefix
python3 ~/.cursor/plugins/token-usage/view.py today
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
  workspaces/<repo>/          # status bar snapshots (per repo)
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

Example:

```bash
export TOKEN_USAGE_NOTIFY=0
```

## Limitations

- **No historical backfill** for chats that started before the hook was installed — Cursor transcripts do not include token counts
- **User-level hooks** apply to local Cursor; Cloud Agents use project/team hooks instead
- Token fields depend on Cursor sending them on `stop` (current Cursor builds do)
- Status bar updates after the Agent turn completes (`stop`), not mid-stream

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
  hooks/
    token-usage-logger.py
    hooks.json.example
  bin/
    view.py
  extension/
    package.json
    extension.js
  commands/
    token-usage.md
```

## License

MIT
