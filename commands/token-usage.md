---
description: Show Cursor token usage logged by the local token-usage hook
---

Run this and paste the output to the user **exactly** (keep the `<details>` block so they can expand).

Prefer the **current workspace** snapshot (last agent stop in this folder), not the global last-closed chat:

```bash
/usr/bin/python3 "$HOME/.cursor/plugins/token-usage/view.py" latest --markdown --workspace "$(basename "$PWD")"
```

Do not rephrase the brief line. The first line is the compact summary; details stay inside `<details>`.
To inspect a specific chat: `view.py latest --markdown <chat-id-prefix>` or `view.py chat <id> --expand`.
