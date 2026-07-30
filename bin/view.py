#!/usr/bin/env python3
"""View Cursor token-usage logs (per-chat consecutive totals)."""

from __future__ import annotations

import argparse
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


def resolve_chat_id(query: Optional[str]) -> Optional[str]:
    chats = load_index().get("chats") or {}
    if not chats:
        return None
    if not query:
        # Most recently updated
        ordered = sorted(
            chats.values(),
            key=lambda c: c.get("updated_at") or "",
            reverse=True,
        )
        return ordered[0].get("conversation_id") if ordered else None

    if query in chats:
        return query
    matches = [
        cid
        for cid, meta in chats.items()
        if cid.startswith(query) or (meta.get("short_id") or "").startswith(query)
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


def cmd_summary(rows: list[dict], day: Optional[str], workspace: Optional[str]) -> None:
    stops = filter_records(rows, day=day, event="stop", workspace=workspace)
    prompts = filter_records(rows, day=day, event="prompt", workspace=workspace)

    prompt_sum = 0
    completion_sum = 0
    total_sum = 0
    with_tokens = 0
    without_tokens = 0
    by_model: dict[str, dict[str, int]] = defaultdict(
        lambda: {"turns": 0, "prompt": 0, "output": 0, "total": 0}
    )
    by_workspace: dict[str, dict[str, int]] = defaultdict(lambda: {"turns": 0, "total": 0})
    by_chat: dict[str, dict[str, int]] = defaultdict(lambda: {"turns": 0, "total": 0})

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

        model = row.get("model_id") or row.get("model") or "unknown"
        by_model[model]["turns"] += 1
        by_model[model]["prompt"] += p or 0
        by_model[model]["output"] += o or 0
        by_model[model]["total"] += t or ((p or 0) + (o or 0))

        ws = row.get("workspace") or "unknown"
        by_workspace[ws]["turns"] += 1
        by_workspace[ws]["total"] += t or ((p or 0) + (o or 0))

        cid = row.get("conversation_id") or "unknown"
        by_chat[cid]["turns"] += 1
        by_chat[cid]["total"] += t or ((p or 0) + (o or 0))

    scope = day or "all time"
    if workspace:
        scope += f" · workspace={workspace}"
    print(f"Token usage ({scope})")
    print(f"  prompts logged : {len(prompts)}")
    print(f"  agent turns    : {len(stops)}  (with tokens: {with_tokens}, missing: {without_tokens})")
    print(f"  prompt tokens  : {fmt_int(prompt_sum)}")
    print(f"  output tokens  : {fmt_int(completion_sum)}")
    print(f"  total tokens   : {fmt_int(total_sum)}")
    print()
    print("By chat:")
    for cid, stats in sorted(by_chat.items(), key=lambda x: x[1]["total"], reverse=True)[:15]:
        print(
            f"  {cid[:8]:8}  turns={stats['turns']:<4} total={fmt_int(stats['total']):>10}"
        )
    print()
    print("By model:")
    for model, stats in sorted(by_model.items(), key=lambda x: x[1]["total"], reverse=True):
        print(
            f"  {model:40} turns={stats['turns']:<4} "
            f"in={fmt_int(stats['prompt']):>10} out={fmt_int(stats['output']):>10} "
            f"total={fmt_int(stats['total']):>10}"
        )
    print()
    print("By workspace:")
    for ws, stats in sorted(by_workspace.items(), key=lambda x: x[1]["total"], reverse=True):
        print(f"  {ws:40} turns={stats['turns']:<4} total={fmt_int(stats['total']):>10}")


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
        print(
            f"{ts}  {ws:16}  chat={cid}  {turn_label:<4}  {model:20}  "
            f"+{fmt_int(row.get('total_tokens')):>8}  "
            f"chatΣ={fmt_int(chat_total):>8}  "
            f"{preview}"
        )


def cmd_chats(n: int) -> None:
    chats = list((load_index().get("chats") or {}).values())
    if not chats and CHATS_DIR.exists():
        for path in CHATS_DIR.glob("*.json"):
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
    print(f"{'chat':8}  {'workspace':16}  {'turns':>5}  {'total':>12}  prior  updated")
    for meta in chats[:n]:
        prior = "YES" if meta.get("prior_untracked") else "-"
        print(
            f"{(meta.get('short_id') or (meta.get('conversation_id') or '')[:8]):8}  "
            f"{(meta.get('workspace') or '-'):16}  "
            f"{meta.get('turn_count', 0):5}  "
            f"{fmt_int(meta.get('total_tokens')):>12}  "
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
    brief = (
        f"prompt นี้ใช้ไป {fmt_int(last.get('total_tokens'))} tokens · "
        f"รวม {fmt_int(totals.get('total_tokens'))} tokens"
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
        print(
            f"  {i:2}. +{fmt_int(turn.get('total_tokens')):>8}  "
            f"in={fmt_int(turn.get('prompt_tokens')):>8}  "
            f"out={fmt_int(turn.get('output_tokens')):>8}  "
            f"{turn.get('ts', '')}  {preview}"
        )


def cmd_latest(*, expand: bool = False, markdown: bool = False) -> None:
    brief = None
    detail = None
    if LATEST_JSON_PATH.exists():
        try:
            data = json.loads(LATEST_JSON_PATH.read_text(encoding="utf-8"))
            brief = data.get("brief")
            detail = data.get("detail")
        except (OSError, json.JSONDecodeError):
            pass
    if brief is None and LATEST_PATH.exists():
        brief = LATEST_PATH.read_text(encoding="utf-8").rstrip()
    if detail is None and LATEST_DETAIL_PATH.exists():
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
        ],
    )
    parser.add_argument("chat_id", nargs="?", help="chat id / prefix for `chat`")
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
    args = parser.parse_args(argv)

    if args.command == "path":
        print(LOG_PATH)
        return 0
    if args.command == "debug":
        cmd_debug()
        return 0
    if args.command == "latest":
        cmd_latest(expand=args.expand, markdown=args.markdown)
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
