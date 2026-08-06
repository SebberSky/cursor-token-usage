#!/usr/bin/env python3
"""Cursor hook: per-chat consecutive token usage + notify after each turn."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

HOME = Path.home()
DATA_DIR = HOME / ".cursor" / "token-usage"
CHATS_DIR = DATA_DIR / "chats"
WORKSPACES_DIR = DATA_DIR / "workspaces"
LOG_PATH = DATA_DIR / "usage.jsonl"
PENDING_PATH = DATA_DIR / "pending.json"
LAST_STOP_PATH = DATA_DIR / "last-stop-payload.json"
LATEST_PATH = DATA_DIR / "latest.txt"
INDEX_PATH = DATA_DIR / "chats-index.json"
META_PATH = DATA_DIR / "meta.json"
PROMPT_PREVIEW_CHARS = 240
PRIOR_WARNING = (
    "แชทนี้มีมาก่อน install hook — ไม่สามารถคำนวณ token ย้อนหลังได้"
)


def _load_cost_module():
    """Load shared cost estimator (plugin dir, next to this file, or repo bin/)."""
    import importlib.util

    candidates = [
        HOME / ".cursor" / "plugins" / "token-usage" / "token_usage_cost.py",
        Path(__file__).resolve().parent / "token_usage_cost.py",
        Path(__file__).resolve().parent.parent / "bin" / "token_usage_cost.py",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            spec = importlib.util.spec_from_file_location("token_usage_cost", path)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
        except Exception:
            continue
    return None


_COST = _load_cost_module()


def estimate_turn(turn: dict) -> Optional[dict]:
    if _COST is None:
        return None
    try:
        return _COST.estimate_turn_cost(turn)
    except Exception:
        return None


def estimate_turns(turns: list) -> Optional[dict]:
    if _COST is None:
        return None
    try:
        return _COST.estimate_turns_cost(turns)
    except Exception:
        return None


def cost_text(est: Optional[dict], *, compact: bool = False) -> str:
    if _COST is None or not est:
        return ""
    try:
        return _COST.cost_suffix(est, compact=compact)
    except Exception:
        return ""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CHATS_DIR.mkdir(parents=True, exist_ok=True)
    WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)


def workspace_dir(name: Optional[str]) -> Optional[Path]:
    if not name:
        return None
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
    if not safe:
        return None
    path = WORKSPACES_DIR / safe
    path.mkdir(parents=True, exist_ok=True)
    return path


def repo_totals_for_workspace(workspace: Optional[str]) -> dict:
    """Sum tracked totals across all chats in a workspace."""
    totals = empty_totals()
    chat_count = 0
    if not workspace:
        return {"chat_count": 0, "totals": totals}
    index = read_json(INDEX_PATH, {"chats": {}})
    chats = index.get("chats") if isinstance(index, dict) else {}
    if not isinstance(chats, dict):
        return {"chat_count": 0, "totals": totals}
    for meta in chats.values():
        if not isinstance(meta, dict):
            continue
        if meta.get("workspace") != workspace:
            continue
        chat_count += 1
        path = meta.get("path")
        chat = read_json(Path(path), None) if path else None
        if isinstance(chat, dict):
            totals = add_totals(totals, chat.get("totals") or empty_totals())
        else:
            totals["total_tokens"] = int(totals.get("total_tokens") or 0) + int(
                meta.get("total_tokens") or 0
            )
    return {"chat_count": chat_count, "totals": totals}


def read_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def atomic_write_json(path: Path, data: Any) -> None:
    ensure_data_dir()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def append_jsonl(record: dict) -> None:
    ensure_data_dir()
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def first_int(payload: dict, *keys: str) -> Optional[int]:
    for key in keys:
        if key in payload and payload[key] is not None:
            try:
                return int(payload[key])
            except (TypeError, ValueError):
                continue
    return None


def first_str(payload: dict, *keys: str) -> Optional[str]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def workspace_label(payload: dict) -> Optional[str]:
    roots = payload.get("workspace_roots") or []
    if not roots:
        return None
    root = roots[0]
    return Path(root).name if root else None


def prompt_preview(prompt: str) -> str:
    text = " ".join(prompt.split())
    if len(text) <= PROMPT_PREVIEW_CHARS:
        return text
    return text[: PROMPT_PREVIEW_CHARS - 1] + "…"


def fmt(n: Optional[int]) -> str:
    if n is None:
        return "-"
    return f"{n:,}"


def short_id(value: Optional[str]) -> str:
    if not value:
        return "unknown"
    return value[:8]


def chat_path(conversation_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in conversation_id)
    return CHATS_DIR / f"{safe}.json"


def ensure_meta() -> dict:
    meta = read_json(META_PATH, None)
    if isinstance(meta, dict) and meta.get("installed_at"):
        return meta
    installed_at = utc_now()
    if LOG_PATH.exists():
        try:
            with LOG_PATH.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    ts = row.get("ts")
                    if isinstance(ts, str) and ts:
                        installed_at = ts
                        break
        except (OSError, json.JSONDecodeError):
            pass
    meta = {
        "installed_at": installed_at,
        "version": 1,
    }
    atomic_write_json(META_PATH, meta)
    return meta


def count_transcript_user_prompts(transcript_path: Optional[str]) -> Optional[int]:
    if not transcript_path:
        return None
    path = Path(transcript_path)
    if not path.is_file():
        return None
    count = 0
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                role = row.get("role")
                if role == "user":
                    count += 1
                    continue
                message = row.get("message")
                if isinstance(message, dict) and message.get("role") == "user":
                    count += 1
    except OSError:
        return None
    return count


def apply_prior_history_warning(chat: dict, payload: dict) -> dict:
    """Detect pre-hook chat history once, then keep the warning sticky."""
    ensure_meta()
    transcript_path = first_str(payload, "transcript_path", "transcriptPath")
    if transcript_path:
        chat["transcript_path"] = transcript_path

    user_prompts = count_transcript_user_prompts(transcript_path)
    if user_prompts is not None:
        chat["transcript_user_prompts"] = user_prompts

    if chat.get("prior_checked"):
        return chat

    tracked = int(chat.get("turn_count") or 0)
    prior_turns = 0
    if user_prompts is not None:
        # After stop has recorded this turn, transcript users beyond tracked turns
        # are pre-hook (or otherwise untracked) history.
        # Allow 1-message slack for an in-progress prompt already flushed to transcript.
        delta = user_prompts - tracked
        if tracked <= 1:
            # First tracked turn: every extra user prompt is prior history.
            prior_turns = max(0, user_prompts - max(tracked, 1))
        elif delta >= 2:
            prior_turns = delta

    chat["prior_checked"] = True
    if prior_turns > 0:
        chat["prior_untracked"] = True
        chat["prior_untracked_turns"] = prior_turns
    else:
        chat["prior_untracked"] = False
        chat["prior_untracked_turns"] = 0
    return chat


def detect_prior_on_submit(chat: dict, payload: dict) -> dict:
    """Before the first tracked turn, warn if transcript already has older prompts."""
    if chat.get("prior_checked") or int(chat.get("turn_count") or 0) > 0:
        return chat
    transcript_path = first_str(payload, "transcript_path", "transcriptPath")
    user_prompts = count_transcript_user_prompts(transcript_path)
    if user_prompts is None:
        return chat
    # Current prompt may or may not be in the transcript yet; need >=2 to be sure.
    if user_prompts >= 2:
        chat["prior_untracked"] = True
        chat["prior_untracked_turns"] = user_prompts - 1
        chat["transcript_path"] = transcript_path
        chat["transcript_user_prompts"] = user_prompts
    return chat


def prior_warning_text(chat: dict) -> Optional[str]:
    if not chat.get("prior_untracked"):
        return None
    n = chat.get("prior_untracked_turns")
    if isinstance(n, int) and n > 0:
        return f"{PRIOR_WARNING} (มีอย่างน้อย {n} prompt ก่อนหน้าไม่ถูกนับ)"
    return PRIOR_WARNING


def load_pending() -> dict:
    data = read_json(PENDING_PATH, {})
    return data if isinstance(data, dict) else {}


def save_pending(data: dict) -> None:
    atomic_write_json(PENDING_PATH, data)


def pop_pending(generation_id: Optional[str], conversation_id: Optional[str]) -> Optional[dict]:
    pending = load_pending()
    key = None
    if generation_id and generation_id in pending:
        key = generation_id
    elif conversation_id:
        candidates = [
            (k, v)
            for k, v in pending.items()
            if isinstance(v, dict) and v.get("conversation_id") == conversation_id
        ]
        if candidates:
            key = sorted(candidates, key=lambda item: item[1].get("ts") or "")[-1][0]
    if key is None:
        return None
    entry = pending.pop(key, None)
    save_pending(pending)
    return entry if isinstance(entry, dict) else None


def empty_totals() -> dict:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "prompt_tokens": 0,
        "total_tokens": 0,
    }


def add_totals(totals: dict, turn: dict) -> dict:
    out = dict(totals)
    for key in empty_totals():
        out[key] = int(out.get(key) or 0) + int(turn.get(key) or 0)
    return out


def load_chat(conversation_id: str) -> dict:
    path = chat_path(conversation_id)
    data = read_json(path, None)
    if isinstance(data, dict) and data.get("conversation_id"):
        return data
    return {
        "conversation_id": conversation_id,
        "started_at": utc_now(),
        "updated_at": utc_now(),
        "workspace": None,
        "model": None,
        "model_id": None,
        "turn_count": 0,
        "totals": empty_totals(),
        "turns": [],
    }


def update_index(chat: dict) -> None:
    index = read_json(INDEX_PATH, {"chats": {}})
    if not isinstance(index, dict):
        index = {"chats": {}}
    chats = index.get("chats")
    if not isinstance(chats, dict):
        chats = {}
    cid = chat["conversation_id"]
    chat_cost = estimate_turns(list(chat.get("turns") or []))
    chats[cid] = {
        "conversation_id": cid,
        "short_id": short_id(cid),
        "workspace": chat.get("workspace"),
        "model": chat.get("model_id") or chat.get("model"),
        "turn_count": chat.get("turn_count", 0),
        "total_tokens": (chat.get("totals") or {}).get("total_tokens", 0),
        "estimated_cost_usd": (chat_cost or {}).get("usd"),
        "updated_at": chat.get("updated_at"),
        "started_at": chat.get("started_at"),
        "prior_untracked": bool(chat.get("prior_untracked")),
        "prior_untracked_turns": chat.get("prior_untracked_turns") or 0,
        "path": str(chat_path(cid)),
    }
    index["chats"] = chats
    index["updated_at"] = utc_now()
    atomic_write_json(INDEX_PATH, index)


def repo_cost_for_workspace(workspace: Optional[str]) -> Optional[dict]:
    if not workspace or _COST is None:
        return None
    index = read_json(INDEX_PATH, {"chats": {}})
    chats = index.get("chats") if isinstance(index, dict) else {}
    if not isinstance(chats, dict):
        return None
    total = 0.0
    known = 0
    unknown = 0
    for meta in chats.values():
        if not isinstance(meta, dict) or meta.get("workspace") != workspace:
            continue
        path = meta.get("path")
        chat = read_json(Path(path), None) if path else None
        if not isinstance(chat, dict):
            continue
        est = estimate_turns(list(chat.get("turns") or []))
        if not est:
            continue
        if est.get("known") and est.get("usd") is not None:
            total += float(est["usd"])
            known += int(est.get("known_turns") or 0)
        unknown += int(est.get("unknown_turns") or 0)
    if known <= 0:
        return {"usd": None, "known": False, "known_turns": 0, "unknown_turns": unknown}
    return {
        "usd": round(total, 6),
        "known": True,
        "known_turns": known,
        "unknown_turns": unknown,
    }


def build_brief_text(chat: dict, turn: dict) -> str:
    """Compact one-liner for notifications and default display."""
    turn_total = turn.get("total_tokens")
    chat_total = (chat.get("totals") or {}).get("total_tokens")
    turn_cost = estimate_turn(turn)
    chat_cost = estimate_turns(list(chat.get("turns") or []))
    text = (
        f"prompt นี้ใช้ไป {fmt(turn_total)} tokens"
        f"{cost_text(turn_cost)}"
        f" · รวม {fmt(chat_total)} tokens"
        f"{cost_text(chat_cost)}"
    )
    if chat.get("prior_untracked"):
        text += " · มีประวัติก่อน hook"
    return text


def fmt_compact(n: Optional[int]) -> str:
    if n is None:
        return "-"
    n = int(n)
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.2f}M".rstrip("0").rstrip(".")
    if abs(n) >= 1_000:
        return f"{n / 1_000:.1f}k".rstrip("0").rstrip(".")
    return str(n)


def build_status_text(
    chat: dict,
    turn: dict,
    repo_total: Optional[int] = None,
    repo_cost: Optional[dict] = None,
) -> str:
    """Short label for the IDE status bar (current repo only)."""
    turn_total = turn.get("total_tokens")
    chat_total = (chat.get("totals") or {}).get("total_tokens")
    turn_cost = estimate_turn(turn)
    chat_cost = estimate_turns(list(chat.get("turns") or []))
    text = f"+{fmt_compact(turn_total)}"
    text += cost_text(turn_cost, compact=True)
    text += f" · chat {fmt_compact(chat_total)}"
    text += cost_text(chat_cost, compact=True)
    if repo_total is not None:
        text += f" · repo {fmt_compact(repo_total)}"
        text += cost_text(repo_cost, compact=True)
    if chat.get("prior_untracked"):
        text += " · prior"
    return text


def build_detail_text(chat: dict, turn: dict, repo_info: Optional[dict] = None) -> str:
    ws = chat.get("workspace") or "workspace"
    turn_n = chat.get("turn_count", 0)
    turn_total = turn.get("total_tokens")
    chat_total = (chat.get("totals") or {}).get("total_tokens")
    turn_cost = estimate_turn(turn)
    chat_cost = estimate_turns(list(chat.get("turns") or []))
    lines = [
        f"Token usage · {ws} · chat {short_id(chat.get('conversation_id'))}",
        (
            f"Turn {turn_n}: "
            f"+{fmt(turn_total)} "
            f"(in {fmt(turn.get('prompt_tokens'))} / out {fmt(turn.get('output_tokens'))})"
            f"{cost_text(turn_cost)}"
        ),
        (
            f"  input={fmt(turn.get('input_tokens'))}  "
            f"cache_read={fmt(turn.get('cache_read_tokens'))}  "
            f"cache_write={fmt(turn.get('cache_write_tokens'))}"
        ),
        (
            f"Chat total: {fmt(chat_total)} "
            f"across {turn_n} tracked turn{'s' if turn_n != 1 else ''} "
            f"(in {fmt((chat.get('totals') or {}).get('prompt_tokens'))} / "
            f"out {fmt((chat.get('totals') or {}).get('output_tokens'))})"
            f"{cost_text(chat_cost)}"
        ),
        f"model: {chat.get('model_id') or chat.get('model') or '-'}",
    ]
    if turn_cost:
        label = turn_cost.get("label") or turn_cost.get("model_key") or "-"
        if turn_cost.get("known"):
            lines.append(f"est. rate: {label} (list $/M tokens · approximate)")
            parts = turn_cost.get("parts_usd") or {}
            if parts:
                lines.append(
                    "  est. parts: "
                    f"in={parts.get('input')}  "
                    f"out={parts.get('output')}  "
                    f"cache_read={parts.get('cache_read')}  "
                    f"cache_write={parts.get('cache_write')}"
                )
            if turn_cost.get("assumed_default"):
                lines.append("  note: model=default → estimating with Auto Cost rates")
        else:
            reason = turn_cost.get("reason") or "no rate"
            lines.append(f"est. cost: unavailable ({label}: {reason})")
        lines.append("  source: cursor.com/docs/models-and-pricing (not invoice)")
    preview = turn.get("prompt_preview")
    if preview:
        lines.append(f"prompt: {preview}")
    warning = prior_warning_text(chat)
    if warning:
        lines.append(f"⚠ {warning}")
    if repo_info:
        repo_totals = repo_info.get("totals") or {}
        repo_cost = repo_cost_for_workspace(chat.get("workspace"))
        lines.append(
            f"repo total: {fmt(repo_totals.get('total_tokens'))} "
            f"across {repo_info.get('chat_count', 0)} chat(s)"
            f"{cost_text(repo_cost)}"
        )
    return "\n".join(lines)


def build_summary_text(chat: dict, turn: dict, repo_info: Optional[dict] = None) -> str:
    """Default spit-out: brief first, details under an expand marker for UIs that support it."""
    brief = build_brief_text(chat, turn)
    detail = build_detail_text(chat, turn, repo_info=repo_info)
    return (
        f"{brief}\n"
        f"\n"
        f"<details>\n"
        f"<summary>ขยายดูรายละเอียด</summary>\n"
        f"\n"
        f"```\n"
        f"{detail}\n"
        f"```\n"
        f"\n"
        f"</details>"
    )


def notify_macos(title: str, message: str) -> None:
    if os.environ.get("TOKEN_USAGE_NOTIFY", "1").strip().lower() in {"0", "false", "no", "off"}:
        return

    ensure_data_dir()
    try:
        with (DATA_DIR / "notify.log").open("a", encoding="utf-8") as f:
            f.write(f"{utc_now()} | {title} | {message}\n")
    except OSError:
        pass

    # Prefer terminal-notifier (more reliable banners than osascript alone).
    tn = Path("/usr/local/bin/terminal-notifier")
    if not tn.exists():
        tn = Path("/opt/homebrew/bin/terminal-notifier")
    if tn.exists():
        try:
            subprocess.run(
                [
                    str(tn),
                    "-title",
                    title,
                    "-subtitle",
                    "Cursor token usage",
                    "-message",
                    message,
                    "-sound",
                    "Glass",
                    "-group",
                    "cursor-token-usage",
                    "-sender",
                    "com.todesktop.230313mzl4w4u92",
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    # Fallback / extra: Notification Center via osascript + sound.
    script = (
        f"display notification {json.dumps(message)} "
        f"with title {json.dumps(title)} "
        f"subtitle {json.dumps('Cursor token usage')} "
        f'sound name "Glass"'
    )
    try:
        subprocess.run(
            ["osascript", "-e", script],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass

    # Optional auto-dismiss dialog — OFF by default (blocks the UI).
    # Enable with TOKEN_USAGE_ALERT=1 if you want a popup.
    alert = os.environ.get("TOKEN_USAGE_ALERT", "0").strip().lower()
    if alert in {"1", "true", "yes", "on"}:
        dialog = (
            f"display alert {json.dumps(title)} "
            f"message {json.dumps(message)} "
            f'buttons {{"OK"}} default button "OK" '
            f"giving up after 6"
        )
        try:
            subprocess.run(
                ["osascript", "-e", dialog],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


def write_latest(text: str, chat: dict, turn: dict) -> None:
    ensure_data_dir()
    workspace = chat.get("workspace")
    repo_info = repo_totals_for_workspace(workspace)
    repo_total = (repo_info.get("totals") or {}).get("total_tokens")
    turn_cost = estimate_turn(turn)
    chat_cost = estimate_turns(list(chat.get("turns") or []))
    repo_cost = repo_cost_for_workspace(workspace)
    brief = build_brief_text(chat, turn)
    detail = build_detail_text(chat, turn, repo_info=repo_info)
    status = build_status_text(
        chat, turn, repo_total=repo_total, repo_cost=repo_cost
    )
    payload = {
        "ts": utc_now(),
        "brief": brief,
        "detail": detail,
        "status": status,
        "summary": text,
        "conversation_id": chat.get("conversation_id"),
        "turn": turn,
        "turn_cost": turn_cost,
        "chat_cost": chat_cost,
        "chat_totals": chat.get("totals"),
        "turn_count": chat.get("turn_count"),
        "prior_untracked": bool(chat.get("prior_untracked")),
        "prior_untracked_turns": chat.get("prior_untracked_turns") or 0,
        "workspace": workspace,
        "repo_chat_count": repo_info.get("chat_count", 0),
        "repo_totals": repo_info.get("totals"),
        "repo_cost": repo_cost,
    }

    # Global latest (debugging / last event anywhere).
    LATEST_PATH.write_text(brief + "\n", encoding="utf-8")
    (DATA_DIR / "latest-detail.txt").write_text(detail + "\n", encoding="utf-8")
    (DATA_DIR / "latest-status.txt").write_text(status + "\n", encoding="utf-8")
    atomic_write_json(DATA_DIR / "latest.json", payload)

    # Per-repo snapshot used by the status bar extension.
    ws_dir = workspace_dir(workspace)
    if ws_dir is not None:
        (ws_dir / "latest.txt").write_text(brief + "\n", encoding="utf-8")
        (ws_dir / "latest-detail.txt").write_text(detail + "\n", encoding="utf-8")
        (ws_dir / "latest-status.txt").write_text(status + "\n", encoding="utf-8")
        atomic_write_json(ws_dir / "latest.json", payload)


def handle_before_submit(payload: dict) -> dict:
    ensure_meta()
    generation_id = first_str(payload, "generation_id", "generationId") or utc_now()
    conversation_id = first_str(payload, "conversation_id", "conversationId", "session_id")
    prompt = payload.get("prompt") or ""
    store_full = os.environ.get("TOKEN_USAGE_FULL_PROMPT", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }

    entry = {
        "ts": utc_now(),
        "event": "prompt",
        "conversation_id": conversation_id,
        "generation_id": generation_id,
        "model": first_str(payload, "model"),
        "model_id": first_str(payload, "model_id", "modelId"),
        "workspace": workspace_label(payload),
        "workspace_roots": payload.get("workspace_roots") or [],
        "prompt_preview": prompt_preview(prompt) if isinstance(prompt, str) else "",
        "prompt_chars": len(prompt) if isinstance(prompt, str) else 0,
        "attachment_count": len(payload.get("attachments") or []),
    }
    if store_full and isinstance(prompt, str):
        entry["prompt"] = prompt

    pending = load_pending()
    pending[generation_id] = entry
    if len(pending) > 200:
        for old_key in sorted(pending.keys())[: len(pending) - 200]:
            pending.pop(old_key, None)
    save_pending(pending)
    append_jsonl(entry)

    # Early warning when continuing a pre-hook chat (before tokens arrive on stop).
    out: dict[str, Any] = {"continue": True}
    if conversation_id:
        chat = load_chat(conversation_id)
        chat = detect_prior_on_submit(chat, payload)
        if chat.get("prior_untracked"):
            atomic_write_json(chat_path(conversation_id), chat)
            update_index(chat)
            warning = prior_warning_text(chat)
            if warning:
                out["user_message"] = f"⚠ {warning}"
                if not chat.get("prior_warning_notified_on_submit"):
                    chat["prior_warning_notified_on_submit"] = True
                    atomic_write_json(chat_path(conversation_id), chat)
                    notify_macos(
                        f"{chat.get('workspace') or 'Cursor'} · prior history",
                        warning,
                    )
    return out


def handle_stop(payload: dict) -> dict:
    ensure_data_dir()
    atomic_write_json(LAST_STOP_PATH, payload)

    generation_id = first_str(payload, "generation_id", "generationId")
    conversation_id = (
        first_str(payload, "conversation_id", "conversationId", "session_id", "sessionId")
        or "unknown"
    )
    pending = pop_pending(generation_id, conversation_id)

    input_tokens = first_int(payload, "input_tokens", "inputTokens") or 0
    output_tokens = first_int(payload, "output_tokens", "outputTokens") or 0
    cache_read = first_int(payload, "cache_read_tokens", "cacheReadTokens") or 0
    cache_write = first_int(payload, "cache_write_tokens", "cacheWriteTokens") or 0
    tokens_present = any(
        first_int(payload, k) is not None
        for k in (
            "input_tokens",
            "inputTokens",
            "output_tokens",
            "outputTokens",
            "cache_read_tokens",
            "cacheReadTokens",
            "cache_write_tokens",
            "cacheWriteTokens",
            "total_tokens",
            "totalTokens",
        )
    )

    prompt_total = input_tokens + cache_read + cache_write
    total_tokens = first_int(payload, "total_tokens", "totalTokens")
    if total_tokens is None:
        total_tokens = prompt_total + output_tokens

    turn = {
        "ts": utc_now(),
        "generation_id": generation_id or (pending or {}).get("generation_id"),
        "status": first_str(payload, "status") or "unknown",
        "model": first_str(payload, "model") or (pending or {}).get("model"),
        "model_id": first_str(payload, "model_id", "modelId") or (pending or {}).get("model_id"),
        "duration_ms": first_int(payload, "duration_ms", "durationMs"),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "prompt_tokens": prompt_total,
        "total_tokens": total_tokens,
        "tokens_present": tokens_present,
        "prompt_preview": (pending or {}).get("prompt_preview"),
        "prompt_chars": (pending or {}).get("prompt_chars"),
    }

    chat = load_chat(conversation_id)
    if not chat.get("started_at"):
        chat["started_at"] = turn["ts"]
    chat["updated_at"] = turn["ts"]
    chat["workspace"] = workspace_label(payload) or (pending or {}).get("workspace") or chat.get(
        "workspace"
    )
    chat["model"] = turn.get("model") or chat.get("model")
    chat["model_id"] = turn.get("model_id") or chat.get("model_id")
    chat["workspace_roots"] = (
        payload.get("workspace_roots")
        or (pending or {}).get("workspace_roots")
        or chat.get("workspace_roots")
        or []
    )
    chat["turns"] = list(chat.get("turns") or []) + [turn]
    chat["turn_count"] = len(chat["turns"])
    chat["totals"] = add_totals(chat.get("totals") or empty_totals(), turn)
    chat = apply_prior_history_warning(chat, payload)

    atomic_write_json(chat_path(conversation_id), chat)
    update_index(chat)

    turn_cost = estimate_turn(turn)
    record = {
        "ts": turn["ts"],
        "event": "stop",
        "conversation_id": conversation_id,
        "generation_id": turn.get("generation_id"),
        "model": turn.get("model"),
        "model_id": turn.get("model_id"),
        "workspace": chat.get("workspace"),
        "workspace_roots": chat.get("workspace_roots") or [],
        "loop_count": first_int(payload, "loop_count", "loopCount"),
        "duration_ms": turn.get("duration_ms"),
        "status": turn.get("status"),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "prompt_tokens": prompt_total,
        "total_tokens": total_tokens,
        "tokens_present": tokens_present,
        "prompt_preview": turn.get("prompt_preview"),
        "prompt_chars": turn.get("prompt_chars"),
        "chat_turn": chat["turn_count"],
        "chat_total_tokens": chat["totals"]["total_tokens"],
        "prior_untracked": bool(chat.get("prior_untracked")),
        "prior_untracked_turns": chat.get("prior_untracked_turns") or 0,
        "estimated_cost_usd": (turn_cost or {}).get("usd"),
        "estimated_cost": turn_cost,
    }
    append_jsonl(record)

    summary = build_summary_text(
        chat,
        turn,
        repo_info=repo_totals_for_workspace(chat.get("workspace")),
    )
    write_latest(summary, chat, turn)
    brief = build_brief_text(chat, turn)
    detail = build_detail_text(chat, turn)
    # Hooks output channel (Output → Hooks)
    print(brief, file=sys.stderr)
    print(detail, file=sys.stderr)

    notify_title = f"{chat.get('workspace') or 'Cursor'} · tokens"
    warning = prior_warning_text(chat)
    if warning and not chat.get("prior_warning_notified_on_stop"):
        chat["prior_warning_notified_on_stop"] = True
        atomic_write_json(chat_path(conversation_id), chat)
        notify_macos(
            f"{chat.get('workspace') or 'Cursor'} · prior history",
            warning,
        )
    notify_macos(notify_title, brief)

    # stop hook does not render user_message in chat; keep brief payload for
    # any Cursor build that surfaces it, plus Hooks channel / macOS alert.
    return {"user_message": brief}


def handle_session_end(payload: dict) -> dict:
    conversation_id = first_str(
        payload, "session_id", "sessionId", "conversation_id", "conversationId"
    )
    if not conversation_id:
        return {}

    chat = load_chat(conversation_id)
    if chat.get("turn_count", 0) <= 0:
        return {}

    totals = chat.get("totals") or {}
    last = (chat.get("turns") or [{}])[-1]
    chat_cost = estimate_turns(list(chat.get("turns") or []))
    brief = (
        f"ปิดแชท · รวม {fmt(totals.get('total_tokens'))} tokens"
        f"{cost_text(chat_cost)}"
        f" · {chat.get('turn_count')} turns"
    )
    detail = (
        f"Session ended · chat {short_id(conversation_id)} · "
        f"{chat.get('workspace') or 'workspace'}\n"
        f"Final total: {fmt(totals.get('total_tokens'))} tokens"
        f"{cost_text(chat_cost)} "
        f"over {chat.get('turn_count')} tracked turn(s)"
    )
    warning = prior_warning_text(chat)
    if warning:
        detail = f"{detail}\n⚠ {warning}"
        brief += " · มีประวัติก่อน hook"
    summary = (
        f"{brief}\n\n"
        f"<details>\n"
        f"<summary>ขยายดูรายละเอียด</summary>\n\n"
        f"```\n{detail}\n```\n\n"
        f"</details>"
    )
    # Preserve last turn numbers in latest brief/detail files.
    write_latest(summary, chat, last if last else {"total_tokens": 0})
    # Overwrite brief for session-end wording.
    LATEST_PATH.write_text(brief + "\n", encoding="utf-8")
    (DATA_DIR / "latest-detail.txt").write_text(detail + "\n", encoding="utf-8")
    print(brief, file=sys.stderr)
    notify_macos(f"{chat.get('workspace') or 'Cursor'} · chat closed", brief)
    return {"user_message": summary}


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}

    event = first_str(payload, "hook_event_name", "hookEventName") or ""
    if not event and len(sys.argv) > 1:
        event = sys.argv[1]

    try:
        if event in {"beforeSubmitPrompt", "before_submit_prompt"}:
            out = handle_before_submit(payload)
        elif event in {"stop", "Stop"}:
            out = handle_stop(payload)
        elif event in {"sessionEnd", "session_end"}:
            out = handle_session_end(payload)
        else:
            ensure_data_dir()
            append_jsonl(
                {
                    "ts": utc_now(),
                    "event": event or "unknown",
                    "raw_keys": sorted(payload.keys()),
                }
            )
            out = {}
    except Exception as exc:
        try:
            ensure_data_dir()
            append_jsonl(
                {
                    "ts": utc_now(),
                    "event": "logger_error",
                    "error": str(exc),
                    "hook_event_name": event,
                }
            )
        except Exception:
            pass
        out = {"continue": True} if event == "beforeSubmitPrompt" else {}

    sys.stdout.write(json.dumps(out) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
