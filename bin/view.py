#!/usr/bin/env python3
"""View Cursor token-usage logs (per-chat consecutive totals)."""

from __future__ import annotations

import argparse
import importlib.util
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

DATA_DIR = Path.home() / ".cursor" / "token-usage"
LOG_PATH = DATA_DIR / "usage.jsonl"
LAST_STOP_PATH = DATA_DIR / "last-stop-payload.json"
LATEST_PATH = DATA_DIR / "latest.txt"
LATEST_DETAIL_PATH = DATA_DIR / "latest-detail.txt"
LATEST_JSON_PATH = DATA_DIR / "latest.json"
INDEX_PATH = DATA_DIR / "chats-index.json"
CHATS_DIR = DATA_DIR / "chats"


def _load_cost_module():
    candidates = [
        Path(__file__).resolve().parent / "token_usage_cost.py",
        Path.home() / ".cursor" / "plugins" / "token-usage" / "token_usage_cost.py",
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


def _load_update_module():
    candidates = [
        Path(__file__).resolve().parent / "token_usage_update.py",
        Path.home() / ".cursor" / "plugins" / "token-usage" / "token_usage_update.py",
        Path(__file__).resolve().parent.parent / "bin" / "token_usage_update.py",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            spec = importlib.util.spec_from_file_location("token_usage_update", path)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
        except Exception:
            continue
    return None


_UPDATE = _load_update_module()


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


def fmt_usd(value: Optional[float]) -> str:
    if value is None:
        return "-"
    if _COST is not None:
        try:
            return _COST.fmt_usd(value)
        except Exception:
            pass
    return f"${float(value):,.2f}"


def load_records() -> list[dict]:
    if not LOG_PATH.exists():
        return []
    rows: list[dict] = []
    with LOG_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def parse_day(ts: Optional[str]) -> Optional[str]:
    if not ts:
        return None
    return ts[:10]


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def fmt_int(n: Optional[int]) -> str:
    if n is None:
        return "-"
    return f"{n:,}"


def filter_records(
    rows: Iterable[dict],
    *,
    day: Optional[str] = None,
    event: Optional[str] = None,
    workspace: Optional[str] = None,
) -> list[dict]:
    out = []
    for row in rows:
        if day and parse_day(row.get("ts")) != day:
            continue
        if event and row.get("event") != event:
            continue
        if workspace and row.get("workspace") != workspace:
            continue
        out.append(row)
    return out


def load_index() -> dict:
    try:
        data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"chats": {}}
    except (OSError, json.JSONDecodeError):
        return {"chats": {}}


def load_chat_file(conversation_id: str) -> Optional[dict]:
    index = load_index().get("chats") or {}
    meta = index.get(conversation_id)
    path = None
    if isinstance(meta, dict) and meta.get("path"):
        path = Path(meta["path"])
    if path is None:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in conversation_id)
        path = CHATS_DIR / f"{safe}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def is_chat_data_file(path: Path) -> bool:
    """True for per-conversation data files; false for sidecar snapshots."""
    name = path.name
    return name.endswith(".json") and not name.endswith(".latest.json")


def resolve_chat_id(query: Optional[str]) -> Optional[str]:
    chats = load_index().get("chats") or {}
    if not chats:
        if not CHATS_DIR.exists():
            return None
        files = sorted(
            (p for p in CHATS_DIR.glob("*.json") if is_chat_data_file(p)),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not query:
            return files[0].stem if files else None
        matches = [p.stem for p in files if p.stem.startswith(query)]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            print(f"No chat found for '{query}'")
            return None
        print(f"Ambiguous chat id '{query}'. Matches:")
        for cid in matches[:15]:
            print(f"  {cid}")
        return None
    if not query:
        ranked = sorted(
            chats.values(),
            key=lambda c: c.get("updated_at") or "",
            reverse=True,
        )
        if not ranked:
            return None
        return ranked[0].get("conversation_id")
    if query in chats:
        return query
    matches = [
        cid
        for cid in chats
        if cid.startswith(query) or (chats[cid].get("short_id") or "").startswith(query)
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print(f"Ambiguous chat id '{query}'. Matches:")
        for cid in matches:
            print(f"  {cid}")
        return None
    print(f"No chat found for '{query}'")
    return None


def dedupe_stops_by_generation(stops: list[dict]) -> list[dict]:
    """Keep the strongest stop per generation_id (avoids abort+complete double-count)."""
    best: dict[str, dict] = {}
    order: list[str] = []
    passthrough: list[dict] = []
    for row in stops:
        gen = row.get("generation_id")
        if not gen:
            passthrough.append(row)
            continue
        key = str(gen)
        prev = best.get(key)
        if prev is None:
            best[key] = row
            order.append(key)
            continue
        prev_tokens = int(prev.get("total_tokens") or 0)
        new_tokens = int(row.get("total_tokens") or 0)
        prev_completed = (prev.get("status") or "") == "completed"
        new_completed = (row.get("status") or "") == "completed"
        if new_tokens > prev_tokens or (new_completed and not prev_completed):
            best[key] = row
    return [best[k] for k in order] + passthrough


def cmd_summary(rows: list[dict], day: Optional[str], workspace: Optional[str]) -> None:
    stops = dedupe_stops_by_generation(
        filter_records(rows, day=day, event="stop", workspace=workspace)
    )
    prompts = filter_records(rows, day=day, event="prompt", workspace=workspace)

    prompt_sum = 0
    completion_sum = 0
    total_sum = 0
    cost_sum = 0.0
    cost_known = 0
    with_tokens = 0
    without_tokens = 0
    by_model: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "turns": 0,
            "prompt": 0,
            "output": 0,
            "total": 0,
            "cost": 0.0,
            "cost_turns": 0,
        }
    )
    by_workspace: dict[str, dict[str, float]] = defaultdict(
        lambda: {"turns": 0, "total": 0, "cost": 0.0, "cost_turns": 0}
    )
    by_chat: dict[str, dict[str, float]] = defaultdict(
        lambda: {"turns": 0, "total": 0, "cost": 0.0, "cost_turns": 0}
    )

    for row in stops:
        p = row.get("prompt_tokens")
        o = row.get("output_tokens")
        t = row.get("total_tokens")
        if row.get("tokens_present"):
            with_tokens += 1
            prompt_sum += p or 0
            completion_sum += o or 0
            total_sum += t or ((p or 0) + (o or 0))
        else:
            without_tokens += 1

        est = row.get("estimated_cost")
        if not isinstance(est, dict):
            est = estimate_turn(row)
        usd = None
        if isinstance(est, dict) and est.get("known") and est.get("usd") is not None:
            usd = float(est["usd"])
            cost_sum += usd
            cost_known += 1

        model = row.get("model_id") or row.get("model") or "unknown"
        by_model[model]["turns"] += 1
        by_model[model]["prompt"] += p or 0
        by_model[model]["output"] += o or 0
        by_model[model]["total"] += t or ((p or 0) + (o or 0))
        if usd is not None:
            by_model[model]["cost"] += usd
            by_model[model]["cost_turns"] += 1

        ws = row.get("workspace") or "unknown"
        by_workspace[ws]["turns"] += 1
        by_workspace[ws]["total"] += t or ((p or 0) + (o or 0))
        if usd is not None:
            by_workspace[ws]["cost"] += usd
            by_workspace[ws]["cost_turns"] += 1

        cid = row.get("conversation_id") or "unknown"
        by_chat[cid]["turns"] += 1
        by_chat[cid]["total"] += t or ((p or 0) + (o or 0))
        if usd is not None:
            by_chat[cid]["cost"] += usd
            by_chat[cid]["cost_turns"] += 1

    scope = day or "all time"
    if workspace:
        scope += f" · workspace={workspace}"
    print(f"Token usage ({scope})")
    print(f"  prompts logged : {len(prompts)}")
    print(f"  agent turns    : {len(stops)}  (with tokens: {with_tokens}, missing: {without_tokens})")
    print(f"  prompt tokens  : {fmt_int(prompt_sum)}")
    print(f"  output tokens  : {fmt_int(completion_sum)}")
    print(f"  total tokens   : {fmt_int(total_sum)}")
    if cost_known:
        print(f"  est. cost USD  : {fmt_usd(cost_sum)}  ({cost_known}/{len(stops)} turns priced)")
    print()
    print("By chat:")
    for cid, stats in sorted(by_chat.items(), key=lambda x: x[1]["total"], reverse=True)[:15]:
        cost_part = f"  ~{fmt_usd(stats['cost'])}" if stats["cost_turns"] else ""
        print(
            f"  {cid[:8]:8}  turns={int(stats['turns']):<4} total={fmt_int(int(stats['total'])):>10}"
            f"{cost_part}"
        )
    print()
    print("By model:")
    for model, stats in sorted(by_model.items(), key=lambda x: x[1]["total"], reverse=True):
        cost_part = f"  ~{fmt_usd(stats['cost'])}" if stats["cost_turns"] else ""
        print(
            f"  {model:40} turns={int(stats['turns']):<4} "
            f"in={fmt_int(int(stats['prompt'])):>10} out={fmt_int(int(stats['output'])):>10} "
            f"total={fmt_int(int(stats['total'])):>10}"
            f"{cost_part}"
        )
    print()
    print("By workspace:")
    for ws, stats in sorted(by_workspace.items(), key=lambda x: x[1]["total"], reverse=True):
        cost_part = f"  ~{fmt_usd(stats['cost'])}" if stats["cost_turns"] else ""
        print(
            f"  {ws:40} turns={int(stats['turns']):<4} total={fmt_int(int(stats['total'])):>10}"
            f"{cost_part}"
        )


def cmd_tail(rows: list[dict], n: int, workspace: Optional[str]) -> None:
    stops = filter_records(rows, event="stop", workspace=workspace)
    for row in stops[-n:]:
        ts = row.get("ts", "?")
        model = row.get("model_id") or row.get("model") or "?"
        ws = row.get("workspace") or "?"
        cid = (row.get("conversation_id") or "?")[:8]
        turn = row.get("chat_turn")
        chat_total = row.get("chat_total_tokens")
        preview = row.get("prompt_preview") or ""
        if len(preview) > 60:
            preview = preview[:59] + "…"
        turn_label = f"t{turn}" if turn else "t?"
        est = row.get("estimated_cost")
        if not isinstance(est, dict):
            est = estimate_turn(row)
        print(
            f"{ts}  {ws:16}  chat={cid}  {turn_label:<4}  {model:20}  "
            f"+{fmt_int(row.get('total_tokens')):>8}"
            f"{cost_text(est)}  "
            f"chatΣ={fmt_int(chat_total):>8}  "
            f"{preview}"
        )


def cmd_chats(n: int) -> None:
    chats = list((load_index().get("chats") or {}).values())
    if not chats and CHATS_DIR.exists():
        for path in CHATS_DIR.glob("*.json"):
            if not is_chat_data_file(path):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            chats.append(
                {
                    "conversation_id": data.get("conversation_id"),
                    "short_id": (data.get("conversation_id") or "")[:8],
                    "workspace": data.get("workspace"),
                    "model": data.get("model_id") or data.get("model"),
                    "turn_count": data.get("turn_count", 0),
                    "total_tokens": (data.get("totals") or {}).get("total_tokens", 0),
                    "updated_at": data.get("updated_at"),
                }
            )
    chats = sorted(chats, key=lambda c: c.get("updated_at") or "", reverse=True)
    if not chats:
        print("No chats recorded yet.")
        return
    print(
        f"{'chat':8}  {'workspace':16}  {'turns':>5}  {'total':>12}  "
        f"{'est.$':>10}  prior  updated"
    )
    for meta in chats[:n]:
        prior = "YES" if meta.get("prior_untracked") else "-"
        cost = meta.get("estimated_cost_usd")
        if cost is None:
            chat = load_chat_file(meta.get("conversation_id") or "")
            if chat:
                est = estimate_turns(list(chat.get("turns") or []))
                cost = (est or {}).get("usd")
        print(
            f"{(meta.get('short_id') or (meta.get('conversation_id') or '')[:8]):8}  "
            f"{(meta.get('workspace') or '-'):16}  "
            f"{meta.get('turn_count', 0):5}  "
            f"{fmt_int(meta.get('total_tokens')):>12}  "
            f"{fmt_usd(cost) if cost is not None else '-':>10}  "
            f"{prior:5}  "
            f"{meta.get('updated_at') or '-'}"
        )


def cmd_chat(query: Optional[str], *, expand: bool = False) -> None:
    cid = resolve_chat_id(query)
    if not cid:
        return
    chat = load_chat_file(cid)
    if not chat:
        print(f"Chat file missing for {cid}")
        return
    totals = chat.get("totals") or {}
    turns = chat.get("turns") or []
    last = turns[-1] if turns else {}
    last_cost = estimate_turn(last) if last else None
    chat_cost = estimate_turns(turns)
    brief = (
        f"prompt นี้ใช้ไป {fmt_int(last.get('total_tokens'))} tokens"
        f"{cost_text(last_cost)}"
        f" · รวม {fmt_int(totals.get('total_tokens'))} tokens"
        f"{cost_text(chat_cost)}"
    )
    if chat.get("prior_untracked"):
        brief += " · มีประวัติก่อน hook"
    print(brief)

    if not expand:
        print("(ใช้ --expand เพื่อดูรายละเอียด)")
        return

    print()
    print(f"Chat {cid}")
    print(f"  workspace : {chat.get('workspace') or '-'}")
    print(f"  model     : {chat.get('model_id') or chat.get('model') or '-'}")
    print(f"  turns     : {chat.get('turn_count', 0)} tracked")
    print(f"  started   : {chat.get('started_at') or '-'}")
    print(f"  updated   : {chat.get('updated_at') or '-'}")
    print(
        f"  totals    : {fmt_int(totals.get('total_tokens'))} "
        f"(in {fmt_int(totals.get('prompt_tokens'))} / out {fmt_int(totals.get('output_tokens'))})"
        f"{cost_text(chat_cost)}"
    )
    if chat.get("prior_untracked"):
        n = chat.get("prior_untracked_turns") or "?"
        print(
            "  warning   : แชทนี้มีมาก่อน install hook — "
            f"ไม่สามารถคำนวณ token ย้อนหลังได้ (prior prompts ≈ {n})"
        )
    print()
    print("Turns:")
    for i, turn in enumerate(turns, start=1):
        preview = turn.get("prompt_preview") or ""
        if len(preview) > 70:
            preview = preview[:69] + "…"
        est = estimate_turn(turn)
        print(
            f"  {i:2}. +{fmt_int(turn.get('total_tokens')):>8}  "
            f"in={fmt_int(turn.get('prompt_tokens')):>8}  "
            f"out={fmt_int(turn.get('output_tokens')):>8}"
            f"{cost_text(est)}  "
            f"{turn.get('ts', '')}  {preview}"
        )


def workspace_latest_path(workspace: Optional[str]) -> Optional[Path]:
    if not workspace:
        return None
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in workspace)
    if not safe:
        return None
    return DATA_DIR / "workspaces" / safe / "latest.json"


def chat_latest_path(conversation_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in conversation_id)
    return CHATS_DIR / f"{safe}.latest.json"


def rebuild_latest_from_chat(chat: dict) -> Optional[dict]:
    """Build a latest-style payload from a chat file (last turn = this prompt)."""
    turns = list(chat.get("turns") or [])
    if not turns:
        return None
    last = turns[-1]
    totals = chat.get("totals") or {}
    last_cost = estimate_turn(last)
    chat_cost = estimate_turns(turns)
    brief = (
        f"prompt นี้ใช้ไป {fmt_int(last.get('total_tokens'))} tokens"
        f"{cost_text(last_cost)}"
        f" · รวม {fmt_int(totals.get('total_tokens'))} tokens"
        f"{cost_text(chat_cost)}"
    )
    if chat.get("prior_untracked"):
        brief += " · มีประวัติก่อน hook"
    cid = chat.get("conversation_id")
    detail_lines = [
        f"Token usage · {chat.get('workspace') or 'workspace'} · chat {(cid or '')[:8]}",
        (
            f"Turn {chat.get('turn_count', 0)}: "
            f"+{fmt_int(last.get('total_tokens'))} "
            f"(in {fmt_int(last.get('prompt_tokens'))} / out {fmt_int(last.get('output_tokens'))})"
            f"{cost_text(last_cost)}"
        ),
        (
            f"Chat total: {fmt_int(totals.get('total_tokens'))} "
            f"across {chat.get('turn_count', 0)} tracked turn(s)"
            f"{cost_text(chat_cost)}"
        ),
        f"model: {chat.get('model_id') or chat.get('model') or '-'}",
    ]
    preview = last.get("prompt_preview")
    if preview:
        detail_lines.append(f"prompt: {preview}")
    return {
        "ts": chat.get("updated_at") or last.get("ts"),
        "event": "stop",
        "brief": brief,
        "detail": "\n".join(detail_lines),
        "status": brief,
        "summary": brief,
        "conversation_id": cid,
        "turn": last,
        "turn_cost": last_cost,
        "chat_cost": chat_cost,
        "chat_totals": totals,
        "turn_count": chat.get("turn_count"),
        "prior_untracked": bool(chat.get("prior_untracked")),
        "prior_untracked_turns": chat.get("prior_untracked_turns") or 0,
        "workspace": chat.get("workspace"),
    }


def load_latest_payload(
    *,
    chat_query: Optional[str] = None,
    workspace: Optional[str] = None,
) -> Optional[dict]:
    """Resolve the correct latest snapshot for a chat or workspace.

    Priority:
      1. Explicit chat id / prefix → that chat's file (authoritative)
      2. Workspace latest.json from last agent stop → rebuild from that chat
      3. Most recently updated chat in the workspace (index → chat file)
      4. Global latest.json (skip sessionEnd-only leftovers when possible)
    """
    if chat_query:
        cid = resolve_chat_id(chat_query)
        if not cid:
            return None
        chat = load_chat_file(cid)
        if chat:
            rebuilt = rebuild_latest_from_chat(chat)
            if rebuilt:
                return rebuilt
        snap_path = chat_latest_path(cid)
        if snap_path.exists():
            try:
                data = json.loads(snap_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("brief"):
                    return data
            except (OSError, json.JSONDecodeError):
                pass
        return None

    def _is_session_end_snapshot(data: dict) -> bool:
        if data.get("event") == "sessionEnd":
            return True
        brief = data.get("brief") or ""
        return brief.startswith("ปิดแชท")

    def _from_conversation(cid: Optional[str]) -> Optional[dict]:
        if not cid:
            return None
        chat = load_chat_file(cid)
        if chat:
            return rebuild_latest_from_chat(chat)
        return None

    ws = workspace or Path.cwd().name
    ws_path = workspace_latest_path(ws)
    if ws_path and ws_path.exists():
        try:
            data = json.loads(ws_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("brief") and not _is_session_end_snapshot(data):
                rebuilt = _from_conversation(data.get("conversation_id"))
                if rebuilt:
                    return rebuilt
                return data
        except (OSError, json.JSONDecodeError):
            pass

    chats = load_index().get("chats") or {}
    ranked = sorted(
        (
            m
            for m in chats.values()
            if isinstance(m, dict) and (not ws or m.get("workspace") == ws)
        ),
        key=lambda c: c.get("updated_at") or "",
        reverse=True,
    )
    for meta in ranked:
        rebuilt = _from_conversation(meta.get("conversation_id"))
        if rebuilt:
            return rebuilt

    if LATEST_JSON_PATH.exists():
        try:
            data = json.loads(LATEST_JSON_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("brief") and not _is_session_end_snapshot(data):
                rebuilt = _from_conversation(data.get("conversation_id"))
                if rebuilt:
                    return rebuilt
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return None


def cmd_latest(
    *,
    expand: bool = False,
    markdown: bool = False,
    chat_query: Optional[str] = None,
    workspace: Optional[str] = None,
) -> None:
    data = load_latest_payload(chat_query=chat_query, workspace=workspace)
    brief = (data or {}).get("brief") if data else None
    detail = (data or {}).get("detail") if data else None

    if brief is None and not chat_query and LATEST_PATH.exists():
        brief = LATEST_PATH.read_text(encoding="utf-8").rstrip()
    if detail is None and not chat_query and LATEST_DETAIL_PATH.exists():
        detail = LATEST_DETAIL_PATH.read_text(encoding="utf-8").rstrip()

    if not brief:
        print("No latest summary yet. Finish one agent turn first.")
        return

    if markdown:
        print(brief)
        if detail:
            print()
            print("<details>")
            print("<summary>ขยายดูรายละเอียด</summary>")
            print()
            print("```")
            print(detail)
            print("```")
            print()
            print("</details>")
        return

    print(brief)
    if expand:
        if detail:
            print()
            print(detail)
        return
    print("(ใช้ --expand เพื่อดูรายละเอียด)")


def cmd_raw(n: int) -> None:
    if not LOG_PATH.exists():
        print(f"No log yet at {LOG_PATH}")
        return
    lines = LOG_PATH.read_text(encoding="utf-8").splitlines()
    for line in lines[-n:]:
        print(line)


def cmd_debug() -> None:
    print(f"log      : {LOG_PATH}  ({'exists' if LOG_PATH.exists() else 'missing'})")
    print(f"chats dir : {CHATS_DIR}  ({'exists' if CHATS_DIR.exists() else 'missing'})")
    print(f"latest   : {LATEST_PATH}  ({'exists' if LATEST_PATH.exists() else 'missing'})")
    print(f"last stop: {LAST_STOP_PATH}  ({'exists' if LAST_STOP_PATH.exists() else 'missing'})")
    print(f"cost mod : {'loaded' if _COST is not None else 'missing'}")
    print(f"update   : {'loaded' if _UPDATE is not None else 'missing'}")
    installed = Path.home() / ".cursor" / "plugins" / "token-usage" / "installed.json"
    version_file = Path(__file__).resolve().parent / "version.json"
    if not version_file.exists():
        version_file = Path.home() / ".cursor" / "plugins" / "token-usage" / "version.json"
    if installed.exists():
        try:
            meta = json.loads(installed.read_text(encoding="utf-8"))
            print(
                f"installed : {meta.get('version')} "
                f"({meta.get('channel')}) ref={meta.get('ref')} "
                f"at {meta.get('installed_at')}"
            )
        except (OSError, json.JSONDecodeError):
            print(f"installed : {installed} (unreadable)")
    elif version_file.exists():
        try:
            meta = json.loads(version_file.read_text(encoding="utf-8"))
            print(f"version  : {meta.get('version')} ({meta.get('channel')})")
        except (OSError, json.JSONDecodeError):
            pass
    if _COST is not None:
        try:
            path = getattr(_COST, "_find_pricing_path", lambda: None)()
            print(f"pricing  : {path}")
        except Exception:
            pass
    if LAST_STOP_PATH.exists():
        data = json.loads(LAST_STOP_PATH.read_text(encoding="utf-8"))
        keys = sorted(data.keys())
        print(f"last-stop keys ({len(keys)}): {', '.join(keys)}")
        interesting = [
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "total_tokens",
            "model",
            "model_id",
            "status",
            "generation_id",
            "conversation_id",
            "session_id",
        ]
        print("token-related fields:")
        for key in interesting:
            if key in data:
                print(f"  {key}: {data[key]!r}")


def cmd_version() -> None:
    candidates = [
        Path.home() / ".cursor" / "plugins" / "token-usage" / "installed.json",
        Path(__file__).resolve().parent / "version.json",
        Path.home() / ".cursor" / "plugins" / "token-usage" / "version.json",
        Path(__file__).resolve().parent.parent / "version.json",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        version = data.get("version") or "unknown"
        channel = data.get("channel") or "unknown"
        ref = data.get("ref")
        print(f"cursor-token-usage {version} ({channel})")
        if ref:
            print(f"ref: {ref}")
        if data.get("installed_at"):
            print(f"installed_at: {data['installed_at']}")
        print(f"source: {path}")
        return
    print("cursor-token-usage (version unknown — not installed?)")


def cmd_check_update(*, force: bool = True, dismiss: bool = False, install: bool = False) -> int:
    if _UPDATE is None:
        print("Update checker module missing. Re-run install.sh.")
        return 1
    if dismiss:
        state = _UPDATE.mark_dismissed()
        latest = state.get("dismissed_version") or state.get("latest_version")
        print(f"Dismissed update alert for {latest or 'current latest'}")
        return 0
    try:
        state = _UPDATE.check_for_update(force=force)
    except Exception as exc:
        print(f"Update check failed: {exc}")
        return 1
    print(_UPDATE.format_check_message(state))
    update_path = Path.home() / ".cursor" / "plugins" / "token-usage" / "update-check.json"
    if update_path.exists():
        print(f"state: {update_path}")
    if install:
        if not state.get("ok"):
            return 1
        if not state.get("update_available"):
            print("Nothing to install (already up to date).")
            return 0
        return int(_UPDATE.run_install_command(state))
    if state.get("update_available"):
        return 2
    return 0 if state.get("ok") or state.get("skipped") else 1


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="View Cursor per-chat token usage")
    parser.add_argument(
        "command",
        nargs="?",
        default="summary",
        choices=[
            "summary",
            "today",
            "tail",
            "raw",
            "debug",
            "path",
            "chats",
            "chat",
            "latest",
            "version",
            "check-update",
        ],
    )
    parser.add_argument("chat_id", nargs="?", help="chat id / prefix for `chat` or `latest`")
    parser.add_argument("-n", type=int, default=20, help="rows for tail/raw/chats")
    parser.add_argument("--workspace", help="filter by workspace folder name")
    parser.add_argument("--day", help="UTC day YYYY-MM-DD (summary)")
    parser.add_argument(
        "--expand",
        action="store_true",
        help="show detailed breakdown (latest/chat)",
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="print latest as brief + <details> expand block",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="with check-update: ignore cache interval",
    )
    parser.add_argument(
        "--dismiss",
        action="store_true",
        help="with check-update: dismiss current update alert",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="with check-update: run the install one-liner when an update is available",
    )
    args = parser.parse_args(argv)

    if args.command == "path":
        print(LOG_PATH)
        return 0
    if args.command == "debug":
        cmd_debug()
        return 0
    if args.command == "version":
        cmd_version()
        return 0
    if args.command == "check-update":
        return cmd_check_update(
            force=args.force or True,
            dismiss=args.dismiss,
            install=args.install,
        )
    if args.command == "latest":
        cmd_latest(
            expand=args.expand,
            markdown=args.markdown,
            chat_query=args.chat_id,
            workspace=args.workspace,
        )
        return 0
    if args.command == "chats":
        cmd_chats(args.n)
        return 0
    if args.command == "chat":
        cmd_chat(args.chat_id, expand=args.expand)
        return 0

    rows = load_records()
    if not rows and args.command != "raw":
        print(f"No usage recorded yet.\nLog path: {LOG_PATH}")
        print("Send an Agent prompt, wait for the turn to finish, then re-run.")
        return 0

    if args.command == "summary":
        cmd_summary(rows, day=args.day, workspace=args.workspace)
    elif args.command == "today":
        cmd_summary(rows, day=today_utc(), workspace=args.workspace)
    elif args.command == "tail":
        cmd_tail(rows, n=args.n, workspace=args.workspace)
    elif args.command == "raw":
        cmd_raw(args.n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
