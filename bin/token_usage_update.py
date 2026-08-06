"""Check for newer channel releases of cursor-token-usage."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

PLUGIN_DIR = Path.home() / ".cursor" / "plugins" / "token-usage"
INSTALLED_PATH = PLUGIN_DIR / "installed.json"
VERSION_PATH = PLUGIN_DIR / "version.json"
UPDATE_STATE_PATH = PLUGIN_DIR / "update-check.json"
DEFAULT_REPO = "SebberSky/cursor-token-usage"
DEFAULT_SOURCE_REF = "main"
USER_AGENT = "cursor-token-usage-update-check/1.0"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def updates_enabled() -> bool:
    raw = os.environ.get("TOKEN_USAGE_CHECK_UPDATES", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def load_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def normalize_channel(raw: Optional[str]) -> str:
    value = (raw or "stable").strip().lower()
    if value in {"beta", "preview", "next"}:
        return "beta"
    if value in {"stable", "prod", "production", "release"}:
        return "stable"
    if value in {"pinned", "local", "unknown", ""}:
        return "stable"
    return "stable"


def load_installed() -> dict[str, Any]:
    meta = load_json(INSTALLED_PATH) or {}
    if not meta.get("version"):
        fallback = load_json(VERSION_PATH) or {}
        if fallback.get("version"):
            meta = {
                "version": fallback.get("version"),
                "channel": fallback.get("channel") or "stable",
                "repo": DEFAULT_REPO,
            }
    return meta


def parse_version(value: Optional[str]) -> Optional[tuple]:
    """Return comparable key: (major, minor, patch, is_release, prerelease_parts)."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.startswith("v") or text.startswith("V"):
        text = text[1:]
    match = re.match(
        r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+.*)?$",
        text,
    )
    if not match:
        return None
    major, minor, patch = int(match.group(1)), int(match.group(2)), int(match.group(3))
    pre = match.group(4)
    if not pre:
        return (major, minor, patch, 1, ())
    parts: list[tuple] = []
    for piece in pre.split("."):
        if piece.isdigit():
            parts.append((0, int(piece)))
        else:
            parts.append((1, piece.lower()))
    return (major, minor, patch, 0, tuple(parts))


def is_newer(latest: Optional[str], installed: Optional[str]) -> bool:
    a = parse_version(latest)
    b = parse_version(installed)
    if a is None or b is None:
        return bool(latest and installed and latest != installed)
    return a > b


def fetch_url(url: str, *, timeout: float = 8.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def channel_manifest_url(repo: str, channel: str, source_ref: str) -> str:
    return (
        f"https://raw.githubusercontent.com/{repo}/{source_ref}/"
        f"channels/{channel}.json"
    )


def install_command(channel: str, *, ref: Optional[str] = None, repo: str = DEFAULT_REPO) -> str:
    base = (
        f"curl -fsSL https://raw.githubusercontent.com/{repo}/main/install.sh | "
    )
    if ref:
        return base + f"TOKEN_USAGE_REF={ref} bash"
    if channel == "beta":
        return base + "TOKEN_USAGE_CHANNEL=beta bash"
    return base + "bash"


def fetch_channel_manifest(
    channel: str,
    *,
    repo: str = DEFAULT_REPO,
    source_ref: str = DEFAULT_SOURCE_REF,
) -> dict[str, Any]:
    url = channel_manifest_url(repo, channel, source_ref)
    raw = fetch_url(url)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("channel manifest is not an object")
    data["_url"] = url
    return data


def check_for_update(
    *,
    force: bool = False,
    channel: Optional[str] = None,
    repo: Optional[str] = None,
    source_ref: Optional[str] = None,
    min_interval_hours: Optional[float] = None,
) -> dict[str, Any]:
    """
    Compare installed version to the latest channel pointer.

    Returns a status dict and always writes update-check.json when network succeeds
    (or force with error details on failure).
    """
    if not updates_enabled() and not force:
        return {
            "ok": False,
            "skipped": True,
            "reason": "checks disabled (TOKEN_USAGE_CHECK_UPDATES=0)",
            "update_available": False,
        }

    installed = load_installed()
    installed_version = str(installed.get("version") or "")
    use_channel = normalize_channel(channel or installed.get("channel"))
    use_repo = (repo or installed.get("repo") or DEFAULT_REPO).strip() or DEFAULT_REPO
    use_source = (
        source_ref
        or os.environ.get("TOKEN_USAGE_CHANNEL_SOURCE_REF")
        or DEFAULT_SOURCE_REF
    ).strip() or DEFAULT_SOURCE_REF

    interval = min_interval_hours
    if interval is None:
        try:
            interval = float(os.environ.get("TOKEN_USAGE_UPDATE_CHECK_HOURS", "12"))
        except ValueError:
            interval = 12.0

    previous = load_json(UPDATE_STATE_PATH) or {}
    if not force and previous.get("checked_at") and interval > 0:
        try:
            checked = datetime.strptime(
                str(previous["checked_at"]).replace("Z", "+0000"),
                "%Y-%m-%dT%H:%M:%S%z",
            )
            age_h = (datetime.now(timezone.utc) - checked.astimezone(timezone.utc)).total_seconds() / 3600.0
            if age_h < interval and previous.get("channel") == use_channel:
                cached = dict(previous)
                cached["ok"] = True
                cached["cached"] = True
                return cached
        except ValueError:
            pass

    result: dict[str, Any] = {
        "ok": False,
        "cached": False,
        "checked_at": utc_now(),
        "installed_version": installed_version or None,
        "channel": use_channel,
        "repo": use_repo,
        "source_ref": use_source,
        "update_available": False,
        "latest_version": None,
        "latest_ref": None,
        "latest_tag": None,
        "install_command": None,
        "dismissed_version": previous.get("dismissed_version"),
        "alerted_version": previous.get("alerted_version"),
    }

    try:
        manifest = fetch_channel_manifest(use_channel, repo=use_repo, source_ref=use_source)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        result["error"] = str(exc)
        write_json(UPDATE_STATE_PATH, result)
        return result

    latest_version = str(manifest.get("version") or "")
    latest_ref = str(manifest.get("ref") or manifest.get("tag") or "")
    result.update(
        {
            "ok": True,
            "latest_version": latest_version or None,
            "latest_ref": latest_ref or None,
            "latest_tag": manifest.get("tag"),
            "latest_published_at": manifest.get("published_at"),
            "manifest_url": manifest.get("_url"),
            "update_available": is_newer(latest_version, installed_version)
            if installed_version and latest_version
            else False,
            "install_command": install_command(
                use_channel, ref=latest_ref or None, repo=use_repo
            ),
        }
    )
    # Preserve dismiss/alert across successful checks when still same latest.
    if result.get("dismissed_version") and result.get("dismissed_version") != latest_version:
        result["dismissed_version"] = previous.get("dismissed_version")
    write_json(UPDATE_STATE_PATH, result)
    return result


def should_alert(state: dict[str, Any]) -> bool:
    if not state.get("ok") or not state.get("update_available"):
        return False
    latest = state.get("latest_version")
    if not latest:
        return False
    if state.get("dismissed_version") == latest:
        return False
    if state.get("alerted_version") == latest:
        return False
    return True


def mark_alerted(state: dict[str, Any]) -> dict[str, Any]:
    latest = state.get("latest_version")
    if latest:
        state = dict(state)
        state["alerted_version"] = latest
        write_json(UPDATE_STATE_PATH, state)
    return state


def mark_dismissed(latest_version: Optional[str] = None) -> dict[str, Any]:
    state = load_json(UPDATE_STATE_PATH) or {}
    version = latest_version or state.get("latest_version")
    if version:
        state["dismissed_version"] = version
        state["checked_at"] = state.get("checked_at") or utc_now()
        write_json(UPDATE_STATE_PATH, state)
    return state


def format_check_message(state: dict[str, Any]) -> str:
    if state.get("skipped"):
        return f"Update check skipped: {state.get('reason')}"
    if not state.get("ok"):
        err = state.get("error") or "unknown error"
        return f"Update check failed: {err}"
    installed = state.get("installed_version") or "unknown"
    channel = state.get("channel") or "stable"
    latest = state.get("latest_version") or "unknown"
    if state.get("update_available"):
        cmd = state.get("install_command") or ""
        return (
            f"Update available: {installed} → {latest} ({channel})\n"
            f"Install now:\n  {cmd}\n"
            f"Or: python3 ~/.cursor/plugins/token-usage/view.py check-update --install"
        )
    cached = " (cached)" if state.get("cached") else ""
    return f"Up to date: {installed} ({channel}) · latest {latest}{cached}"


def run_install_command(state: dict[str, Any]) -> int:
    import subprocess

    cmd = state.get("install_command")
    if not cmd:
        print("No install command available.")
        return 1
    print(f"Running:\n  {cmd}\n")
    completed = subprocess.run(cmd, shell=True)
    return int(completed.returncode or 0)
