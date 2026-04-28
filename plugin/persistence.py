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
    direction: str = "inbound",
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

    if direction == "outbound":
        entry_lines.append(f"**→ me:** {outbound_text}")
        entry_lines.append("")
        entry_lines.append(f"**← {safe_name}:** {inbound_text}")
    else:
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


def update_exchange(
    agent_name: str,
    task_id: str,
    inbound_text: str,
) -> bool:
    """Update the inbound text of an existing exchange (e.g. replace 'waiting' with actual reply)."""
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in agent_name.lower())
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    filepath = _CONV_DIR / safe_name / f"{today}.md"

    if not filepath.exists():
        return False

    with _lock:
        content = filepath.read_text(encoding="utf-8")
        # Find the entry with this task_id and replace the waiting placeholder
        marker = f"task:{task_id}"
        start = content.find(marker)
        if start == -1:
            return False
        block_start = content.rfind("## ", 0, start)
        if block_start == -1:
            return False
        block_end = content.find("\n---\n", block_start)
        if block_end == -1:
            block_end = len(content)
        else:
            block_end += len("\n---\n")

        block = content[block_start:block_end]
        updated_block = block.replace(
            f"**← {safe_name}:** (waiting for reply…)",
            f"**← {safe_name}:** {inbound_text}",
            1,
        )
        if updated_block == block:
            return False
        updated = content[:block_start] + updated_block + content[block_end:]
        filepath.write_text(updated, encoding="utf-8")
    return True
