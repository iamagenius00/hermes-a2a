"""A2A dashboard plugin — backend API routes.

Mounted at /api/plugins/a2a/ by the dashboard plugin system.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import APIRouter

router = APIRouter()
logger = logging.getLogger(__name__)

_CONV_DIR = Path.home() / ".hermes" / "a2a_conversations"
_CONFIG_PATH = Path.home() / ".hermes" / "config.yaml"
_META_PATH = Path.home() / ".hermes" / "a2a_dashboard_meta.json"
_meta_lock = Lock()

_health_cache: dict[str, dict] = {}
_health_cache_ts: float = 0
_HEALTH_CACHE_TTL = 30
_USER_AGENT = "Hermes-A2A/1.0"

_card_cache: dict[str, dict] = {}

_pending_sends: dict[str, dict] = {}
_PENDING_MAX = 100
_PENDING_TTL = 300
_SEND_TIMEOUT = 600
_summary_cache: dict[str, dict] = {}


def _empty_meta() -> dict:
    return {"hidden_tasks": {}, "pinned_agents": {}, "unread_agents": {}}


def _load_meta() -> dict:
    with _meta_lock:
        try:
            data = json.loads(_META_PATH.read_text(encoding="utf-8"))
        except Exception:
            return _empty_meta()

    if not isinstance(data, dict):
        return _empty_meta()
    data.setdefault("hidden_tasks", {})
    data.setdefault("pinned_agents", {})
    data.setdefault("unread_agents", {})
    if not isinstance(data["hidden_tasks"], dict):
        data["hidden_tasks"] = {}
    if not isinstance(data["pinned_agents"], dict):
        data["pinned_agents"] = {}
    if not isinstance(data["unread_agents"], dict):
        data["unread_agents"] = {}
    return data


def _save_meta(meta: dict) -> None:
    with _meta_lock:
        _META_PATH.parent.mkdir(parents=True, exist_ok=True)
        _META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _hidden_tasks(meta: dict, agent_name: str) -> set[str]:
    hidden = meta.get("hidden_tasks", {}).get(_safe_name(agent_name), [])
    return set(hidden if isinstance(hidden, list) else [])


def _filter_hidden_messages(agent_name: str, messages: list[dict], meta: dict) -> list[dict]:
    hidden = _hidden_tasks(meta, agent_name)
    if not hidden:
        return messages
    return [m for m in messages if m.get("task_id") not in hidden]


def _all_task_ids(agent_name: str) -> list[str]:
    safe_agent = _safe_name(agent_name)
    agent_dir = _CONV_DIR / safe_agent
    if not agent_dir.is_dir():
        return []

    task_ids: list[str] = []
    seen: set[str] = set()
    for filepath in sorted(agent_dir.glob("*.md")):
        for msg in _parse_conversation_file(filepath):
            task_id = msg.get("task_id")
            if task_id and task_id not in seen:
                seen.add(task_id)
                task_ids.append(task_id)
    return task_ids


def _cleanup_meta(meta: dict, agents: dict[str, Path]) -> bool:
    """Drop hidden task ids whose conversation block no longer exists."""
    changed = False
    hidden_by_agent = meta.get("hidden_tasks", {})
    for agent, hidden in list(hidden_by_agent.items()):
        if not isinstance(hidden, list):
            hidden_by_agent[agent] = []
            changed = True
            continue
        agent_dir = agents.get(agent)
        if not agent_dir or not agent_dir.is_dir():
            continue
        existing: set[str] = set()
        for filepath in agent_dir.glob("*.md"):
            for msg in _parse_conversation_file(filepath):
                if msg.get("task_id"):
                    existing.add(msg["task_id"])
        kept = [task_id for task_id in hidden if task_id in existing]
        if kept != hidden:
            hidden_by_agent[agent] = kept
            changed = True
    return changed




def _load_agents() -> list[dict]:
    try:
        from hermes_cli.config import load_config
        return load_config().get("a2a", {}).get("agents", [])
    except Exception:
        return []


def _safe_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name.lower())


def _conversation_agents() -> dict[str, Path]:
    if not _CONV_DIR.is_dir():
        return {}
    return {d.name: d for d in _CONV_DIR.iterdir() if d.is_dir()}


def _last_contact(agent_dir: Path) -> str | None:
    files = sorted(agent_dir.glob("*.md"), reverse=True)
    return files[0].stem if files else None


def _last_visible_contact(agent_name: str, agent_dir: Path, meta: dict) -> str | None:
    files = sorted(agent_dir.glob("*.md"), reverse=True)
    for filepath in files:
        messages = _filter_hidden_messages(agent_name, _parse_conversation_file(filepath), meta)
        if messages:
            return filepath.stem
    return None


async def _fetch_agent_card(url: str, auth_token: str = "") -> dict:
    import urllib.request
    import urllib.error

    def _fetch():
        headers = {"User-Agent": _USER_AGENT}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        req = urllib.request.Request(
            f"{url.rstrip('/')}/.well-known/agent.json", headers=headers, method="GET"
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                import json as _json
                return _json.loads(resp.read().decode())
        except Exception:
            return {}

    return await asyncio.to_thread(_fetch)


async def _check_health(url: str, auth_token: str = "") -> bool:
    import urllib.request
    import urllib.error

    def _ping():
        headers = {"User-Agent": _USER_AGENT}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        req = urllib.request.Request(
            f"{url.rstrip('/')}/health", headers=headers, method="GET"
        )
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False

    return await asyncio.to_thread(_ping)


@router.get("/friends")
async def friends():
    import time

    global _health_cache, _health_cache_ts

    configured = _load_agents()
    conv_dirs = _conversation_agents()
    meta = _load_meta()
    if _cleanup_meta(meta, conv_dirs):
        _save_meta(meta)
    pinned = meta.get("pinned_agents", {})
    unread = meta.get("unread_agents", {})

    config_by_safe = {}
    for a in configured:
        sn = _safe_name(a.get("name", ""))
        config_by_safe[sn] = a

    all_agents: dict[str, dict] = {}

    visible_contacts = {
        sn: _last_visible_contact(sn, d, meta)
        for sn, d in conv_dirs.items()
    }

    for a in configured:
        sn = _safe_name(a.get("name", ""))
        all_agents[sn] = {
            "name": a.get("name", sn),
            "safe_name": sn,
            "url": a.get("url", ""),
            "description": a.get("description", ""),
            "has_auth": bool(a.get("auth_token")),
            "configured": True,
            "contacted": bool(visible_contacts.get(sn)),
            "last_contact": None,
            "online": None,
            "avatar_url": a.get("avatar", ""),
        }

    for sn, d in conv_dirs.items():
        visible_last = visible_contacts.get(sn)
        if not visible_last and sn not in all_agents:
            continue
        if sn not in all_agents:
            all_agents[sn] = {
                "name": sn,
                "safe_name": sn,
                "url": "",
                "description": "",
                "has_auth": False,
                "configured": False,
                "contacted": True,
                "last_contact": None,
                "online": None,
                "avatar_url": "",
            }
        all_agents[sn]["last_contact"] = visible_last

    now = time.time()
    need_refresh = now - _health_cache_ts > _HEALTH_CACHE_TTL

    if need_refresh:
        checks = []
        card_fetches = []
        check_keys = []
        for sn, info in all_agents.items():
            if info["url"]:
                token = config_by_safe.get(sn, {}).get("auth_token", "")
                checks.append(_check_health(info["url"], token))
                card_fetches.append(_fetch_agent_card(info["url"], token))
                check_keys.append(sn)

        if checks:
            health_results, card_results = await asyncio.gather(
                asyncio.gather(*checks, return_exceptions=True),
                asyncio.gather(*card_fetches, return_exceptions=True),
            )
            new_cache = {}
            for key, health, card in zip(check_keys, health_results, card_results):
                online = health if isinstance(health, bool) else False
                avatar = ""
                if isinstance(card, dict):
                    avatar = card.get("avatar", "") or card.get("avatar_url", "")
                    if len(_card_cache) >= 100:
                        _card_cache.pop(next(iter(_card_cache)))
                    _card_cache[key] = card
                new_cache[key] = {"online": online, "avatar_url": avatar}
            _health_cache = new_cache
            _health_cache_ts = now

    for sn, info in all_agents.items():
        cached = _health_cache.get(sn)
        if cached is not None:
            info["online"] = cached["online"]
            if cached.get("avatar_url") and not info["avatar_url"]:
                info["avatar_url"] = cached["avatar_url"]
        info["pinned"] = bool(pinned.get(sn))
        info["unread"] = bool(unread.get(sn))

    agents_list = sorted(
        all_agents.values(),
        key=lambda a: (1 if a.get("pinned") else 0, a["last_contact"] or ""),
        reverse=True,
    )

    return {"friends": agents_list}


def _parse_conversation_file(filepath: Path) -> list[dict]:
    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception:
        return []

    entries = []
    header_re = re.compile(
        r"^## (\d{2}:\d{2}:\d{2}) \| task:(\S+)"
        r"(?:\s*\|\s*(\S+))?"
        r"(?:\s*\|\s*reply_to:(\S+))?",
    )

    current: dict[str, Any] | None = None
    section = None
    buffer: list[str] = []
    date_str = filepath.stem

    def _flush():
        if current and section and buffer:
            text = "\n".join(buffer).strip()
            if section == "inbound":
                current["inbound"] = text
            elif section == "outbound":
                current["outbound"] = text

    for line in content.split("\n"):
        m = header_re.match(line)
        if m:
            _flush()
            if current:
                entries.append(current)

            current = {
                "timestamp": f"{date_str}T{m.group(1)}Z",
                "task_id": m.group(2),
                "intent": m.group(3) or "",
                "reply_to": m.group(4) or "",
                "inbound": "",
                "outbound": "",
                "direction": "",
            }
            section = None
            buffer = []
            continue

        if current is None:
            continue

        if line.startswith("**←"):
            _flush()
            if current and not current["direction"]:
                current["direction"] = "inbound"
            section = "inbound"
            text_after = re.sub(r"^\*\*← \S+:\*\*\s*", "", line)
            buffer = [text_after] if text_after else []
        elif line.startswith("**→"):
            _flush()
            if current and not current["direction"]:
                current["direction"] = "outbound"
            section = "outbound"
            text_after = re.sub(r"^\*\*→ \S+:\*\*\s*", "", line)
            buffer = [text_after] if text_after else []
        elif line.strip() == "---":
            _flush()
            if current:
                entries.append(current)
                current = None
            section = None
            buffer = []
        else:
            if section:
                buffer.append(line)

    _flush()
    if current:
        entries.append(current)

    return entries


@router.get("/conversations/{agent_name}")
async def conversations(agent_name: str, days: int = 30):
    agent_name = _safe_name(agent_name)
    agent_dir = _CONV_DIR / agent_name
    if not agent_dir.is_dir():
        return {"agent": agent_name, "days": [], "total_messages": 0}

    meta = _load_meta()
    today = datetime.now(timezone.utc).date()
    result_days = []
    total = 0
    latest_mtime = 0.0

    for i in range(days):
        date = today - timedelta(days=i)
        date_str = date.strftime("%Y-%m-%d")
        filepath = agent_dir / f"{date_str}.md"
        if filepath.exists():
            latest_mtime = max(latest_mtime, filepath.stat().st_mtime)
            messages = _filter_hidden_messages(agent_name, _parse_conversation_file(filepath), meta)
            if messages:
                result_days.append({"date": date_str, "messages": messages})
                total += len(messages)

    return {"agent": agent_name, "days": result_days, "total_messages": total, "mtime": latest_mtime}


@router.get("/conversations/{agent_name}/check")
async def check_new(agent_name: str, since: str = ""):
    agent_name = _safe_name(agent_name)
    agent_dir = _CONV_DIR / agent_name
    if not agent_dir.is_dir():
        return {"new_messages": 0, "mtime": 0}

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    filepath = agent_dir / f"{today}.md"
    if not filepath.exists():
        return {"new_messages": 0, "mtime": 0}

    meta = _load_meta()
    messages = _filter_hidden_messages(agent_name, _parse_conversation_file(filepath), meta)
    mtime = filepath.stat().st_mtime
    if not since:
        return {"new_messages": 0, "latest": messages[-1]["timestamp"] if messages else "", "mtime": mtime}

    new_msgs = [m for m in messages if m["timestamp"] > since]
    return {"new_messages": len(new_msgs), "messages": new_msgs, "mtime": mtime}


@router.post("/conversations/{agent_name}/hide")
async def hide_conversation_task(agent_name: str, data: dict):
    agent_name = _safe_name(agent_name)
    task_id = (data.get("task_id") or "").strip()
    hidden = bool(data.get("hidden", True))
    if not task_id:
        return {"error": "task_id required"}

    meta = _load_meta()
    hidden_by_agent = meta.setdefault("hidden_tasks", {})
    tasks = hidden_by_agent.get(agent_name, [])
    if not isinstance(tasks, list):
        tasks = []

    if hidden:
        if task_id not in tasks:
            tasks.append(task_id)
    else:
        tasks = [t for t in tasks if t != task_id]

    hidden_by_agent[agent_name] = tasks
    _save_meta(meta)
    return {"agent": agent_name, "task_id": task_id, "hidden": hidden}


@router.post("/conversations/{agent_name}/clear")
async def clear_conversation_history(agent_name: str):
    agent_name = _safe_name(agent_name)
    task_ids = _all_task_ids(agent_name)

    meta = _load_meta()
    hidden_by_agent = meta.setdefault("hidden_tasks", {})
    existing = hidden_by_agent.get(agent_name, [])
    if not isinstance(existing, list):
        existing = []

    merged = list(dict.fromkeys(existing + task_ids))
    hidden_by_agent[agent_name] = merged
    _save_meta(meta)
    return {"agent": agent_name, "hidden_count": len(task_ids)}


@router.post("/friends/{agent_name}/pin")
async def pin_friend(agent_name: str, data: dict):
    agent_name = _safe_name(agent_name)
    pinned = bool(data.get("pinned", True))

    meta = _load_meta()
    pinned_agents = meta.setdefault("pinned_agents", {})
    if pinned:
        pinned_agents[agent_name] = True
    else:
        pinned_agents.pop(agent_name, None)
    _save_meta(meta)
    return {"agent": agent_name, "pinned": pinned}


@router.post("/friends/{agent_name}/unread")
async def mark_friend_unread(agent_name: str, data: dict):
    agent_name = _safe_name(agent_name)
    unread = bool(data.get("unread", True))

    meta = _load_meta()
    unread_agents = meta.setdefault("unread_agents", {})
    if unread:
        unread_agents[agent_name] = True
    else:
        unread_agents.pop(agent_name, None)
    _save_meta(meta)
    return {"agent": agent_name, "unread": unread}


def _get_dashboard_route() -> tuple[str, str]:
    """Return the internal dashboard webhook URL and secret."""
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        webhook_cfg = cfg.get("platforms", {}).get("webhook", {}).get("extra", {})
        if not webhook_cfg:
            webhook_cfg = cfg.get("webhook", {}).get("extra", {})
        port = int(webhook_cfg.get("port", 8644))
        routes = webhook_cfg.get("routes", {})
        route = routes.get("a2a_dashboard", {})
        secret = route.get("secret") or webhook_cfg.get("secret", "")
        return f"http://127.0.0.1:{port}/webhooks/a2a_dashboard", secret
    except Exception:
        return "http://127.0.0.1:8644/webhooks/a2a_dashboard", ""


def _find_exchange(agent_name: str, task_id: str, days: int = 3) -> dict | None:
    """Find a persisted A2A exchange by task id."""
    safe_agent = _safe_name(agent_name)
    agent_dir = _CONV_DIR / safe_agent
    if not agent_dir.is_dir():
        return None

    today = datetime.now(timezone.utc).date()
    for i in range(days):
        filepath = agent_dir / f"{(today - timedelta(days=i)).strftime('%Y-%m-%d')}.md"
        if not filepath.exists():
            continue
        for msg in _parse_conversation_file(filepath):
            if msg.get("task_id") == task_id:
                return msg
    return None


def _parse_message_time(timestamp: str) -> float:
    if not timestamp:
        return 0
    try:
        if timestamp.endswith("Z"):
            timestamp = timestamp[:-1] + "+00:00"
        return datetime.fromisoformat(timestamp).timestamp()
    except Exception:
        return 0


def _find_exchange_by_outbound(agent_name: str, message: str, created_at: float, days: int = 3) -> dict | None:
    """Find a persisted exchange by exact outbound text when task ids differ."""
    safe_agent = _safe_name(agent_name)
    agent_dir = _CONV_DIR / safe_agent
    if not agent_dir.is_dir():
        return None

    needle = message.strip()
    if not needle:
        return None

    matches = []
    today = datetime.now(timezone.utc).date()
    for i in range(days):
        filepath = agent_dir / f"{(today - timedelta(days=i)).strftime('%Y-%m-%d')}.md"
        if not filepath.exists():
            continue
        for msg in _parse_conversation_file(filepath):
            if (msg.get("outbound") or "").strip() != needle:
                continue
            msg_ts = _parse_message_time(msg.get("timestamp", ""))
            if msg_ts and created_at and msg_ts < created_at - 120:
                continue
            matches.append((msg_ts, msg))

    if not matches:
        return None
    matches.sort(key=lambda item: item[0], reverse=True)
    return matches[0][1]


def _exchange_has_reply(exchange: dict | None) -> bool:
    if not exchange:
        return False
    inbound = (exchange.get("inbound") or "").strip()
    if not inbound:
        return False
    placeholders = {
        "(waiting for reply…)",
        "(waiting for reply...)",
    }
    return inbound not in placeholders


def _send_via_session(target_agent: str, message: str, task_id: str) -> dict:
    """Inject a dashboard message into the user's Hermes session via webhook."""
    webhook_url, secret = _get_dashboard_route()
    if not secret:
        return {"error": {"message": "a2a_dashboard webhook route is not configured"}}

    payload = json.dumps(
        {
            "event_type": "a2a_dashboard_message",
            "task_id": task_id,
            "target_agent": target_agent,
            "message": message,
        },
        ensure_ascii=False,
    ).encode()
    signature = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
            "X-Hub-Signature-256": signature,
            "X-Request-ID": task_id,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        return {"error": {"message": f"Webhook HTTP {e.code}: {detail[:200]}"}}
    except urllib.error.URLError as e:
        return {"error": {"message": f"Webhook not reachable: {e.reason}"}}
    except Exception as e:
        return {"error": {"message": f"Send failed: {e}"}}


@router.post("/send")
async def send_message(data: dict):
    agent_name = data.get("name", "")
    message = data.get("message", "")

    if not agent_name or not message:
        return {"error": "name and message required"}

    import time as _time
    now = _time.time()
    expired = [k for k, v in _pending_sends.items()
               if v.get("status") != "pending" and now - v.get("completed_at", now) > _PENDING_TTL]
    for k in expired:
        del _pending_sends[k]
    if len(_pending_sends) >= _PENDING_MAX:
        oldest = next(iter(_pending_sends))
        del _pending_sends[oldest]

    task_id = f"dashboard-{uuid.uuid4().hex[:12]}"
    _pending_sends[task_id] = {
        "status": "pending",
        "phase": "queued",
        "agent": agent_name,
        "message": message,
        "created_at": now,
        "response": None,
    }

    async def _do_send():
        import time as _time

        result = await asyncio.to_thread(_send_via_session, agent_name, message, task_id)
        if "error" in result:
            err = result["error"]
            err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            _pending_sends[task_id] = {
                "status": "failed",
                "phase": "webhook_failed",
                "agent": agent_name,
                "message": message,
                "created_at": now,
                "response": {"error": err_msg},
                "completed_at": _time.time(),
            }
        else:
            entry = _pending_sends.get(task_id, {})
            entry.update({
                "status": "pending",
                "phase": "submitted",
                "agent": agent_name,
                "message": message,
                "created_at": entry.get("created_at", now),
                "response": {
                    "task_id": task_id,
                    "state": result.get("status", "accepted"),
                    "response": "Submitted to Hermes session",
                    "source": result.get("source", ""),
                },
                "submitted_at": _time.time(),
            })
            _pending_sends[task_id] = entry

    fut = asyncio.ensure_future(_do_send())
    fut.add_done_callback(lambda f: logger.warning("send task error: %s", f.exception()) if f.exception() else None)

    return {"task_id": task_id, "status": "pending"}


@router.get("/send/{task_id}/status")
async def send_status(task_id: str):
    import time as _time

    entry = _pending_sends.get(task_id)
    if not entry:
        return {"error": "task not found"}

    if entry.get("status") != "pending":
        return entry

    exchange = _find_exchange(entry.get("agent", ""), task_id)
    if not exchange:
        exchange = _find_exchange_by_outbound(
            entry.get("agent", ""),
            entry.get("message", ""),
            entry.get("created_at", 0),
        )
    if _exchange_has_reply(exchange):
        entry = {
            **entry,
            "status": "completed",
            "phase": "completed",
            "response": {
                "task_id": task_id,
                "state": "completed",
                "reply": exchange.get("inbound", ""),
                "exchange": exchange,
            },
            "completed_at": _time.time(),
        }
        _pending_sends[task_id] = entry
        return entry

    age = _time.time() - entry.get("created_at", _time.time())
    if age > _SEND_TIMEOUT:
        entry = {
            **entry,
            "status": "timeout",
            "phase": "timeout",
            "response": {
                "task_id": task_id,
                "state": "timeout",
                "error": "Timed out waiting for agent reply",
            },
            "completed_at": _time.time(),
        }
        _pending_sends[task_id] = entry
        return entry

    return entry


@router.get("/summary/{agent_name}")
async def conversation_summary(agent_name: str):
    import time

    agent_name = _safe_name(agent_name)

    cached = _summary_cache.get(agent_name)
    if cached and time.time() - cached.get("ts", 0) < 1800:
        return {"summary": cached["text"]}

    agent_dir = _CONV_DIR / agent_name
    if not agent_dir.is_dir():
        return {"summary": ""}

    today = datetime.now(timezone.utc).date()
    all_text = []
    for i in range(30):
        date = today - timedelta(days=i)
        filepath = agent_dir / f"{date.strftime('%Y-%m-%d')}.md"
        if filepath.exists():
            messages = _parse_conversation_file(filepath)
            for m in messages:
                if m.get("inbound"):
                    all_text.append(f"Them: {m['inbound'][:200]}")
                if m.get("outbound"):
                    all_text.append(f"Me: {m['outbound'][:200]}")

    if not all_text:
        return {"summary": ""}

    # Build a simple local summary from recent messages
    recent = all_text[-6:]
    snippets = []
    for line in recent:
        prefix = "Them: " if line.startswith("Them: ") else "Me: "
        text = line[len(prefix):]
        clean = text.replace("\n", " ").strip()
        if len(clean) > 60:
            clean = clean[:57] + "…"
        snippets.append(clean)

    summary = " → ".join(snippets[-3:]) if snippets else ""
    if len(summary) > 120:
        summary = summary[:117] + "…"

    if len(_summary_cache) >= 50:
        oldest = next(iter(_summary_cache))
        del _summary_cache[oldest]
    _summary_cache[agent_name] = {"text": summary, "ts": time.time()}
    return {"summary": summary}
