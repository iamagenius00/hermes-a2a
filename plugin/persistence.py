"""A2A conversation persistence — stores interactions to disk so compaction can't erase them.

Format matches ~/inbox/conversations/{agent}/{date}.md for consistency.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

_CONV_DIR = Path.home() / ".hermes" / "a2a_conversations"
_lock = Lock()


def save_exchange(
    agent_name: str,
    task_id: str,
    inbound_text: str,
    outbound_text: str,
    metadata: dict | None = None,
) -> Path:
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    timestamp = now.strftime("%H:%M:%S")
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in agent_name.lower())
    directory = _CONV_DIR / safe_name
    filepath = directory / f"{today}.md"

    intent = (metadata or {}).get("intent", "")
    reply_to = (metadata or {}).get("reply_to_task_id", "")

    entry_lines = [f"## {timestamp} | task:{task_id}"]
    if intent:
        entry_lines[0] += f" | {intent}"
    if reply_to:
        entry_lines[0] += f" | reply_to:{reply_to}"
    entry_lines.append("")
    entry_lines.append(f"**← {safe_name}:** {inbound_text}")
    entry_lines.append("")
    entry_lines.append(f"**→ reply:** {outbound_text}")
    entry_lines.append("")
    entry_lines.append("---")
    entry_lines.append("")

    new_content = "\n".join(entry_lines)

    with _lock:
        directory.mkdir(parents=True, exist_ok=True)
        existing = filepath.read_text(encoding="utf-8") if filepath.exists() else ""
        tmp_path = filepath.with_name(filepath.name + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(existing + new_content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, filepath)

    return filepath
