"""A2A security utilities — prompt injection filtering, redaction, rate limiting, audit."""

from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Dict, Optional

logger = logging.getLogger(__name__)

INJECTION_PATTERNS = [
    re.compile(r"(?i)<\s*system\s*>.*?<\s*/\s*system\s*>", re.DOTALL),
    re.compile(r"(?i)\[INST\].*?\[/INST\]", re.DOTALL),
    re.compile(r"(?i)ignore\s+(all\s+)?previous\s+instructions?"),
    re.compile(r"(?i)you\s+are\s+now\s+"),
    re.compile(r"(?i)new\s+system\s+prompt"),
    re.compile(r"(?i)disregard\s+(all\s+)?(prior|earlier|above)"),
    re.compile(r"(?i)override\s+(your\s+)?(instructions?|rules?|guidelines?)"),
    re.compile(r"<\|im_(start|end)\|>"),
    re.compile(r"(?m)^(Human|Assistant|System)\s*:", re.MULTILINE),
]


def sanitize_inbound(text: str, max_length: int = 50_000) -> str:
    if len(text) > max_length:
        text = text[:max_length] + "\n[... message truncated for safety]"
    for pattern in INJECTION_PATTERNS:
        if pattern.search(text):
            logger.warning("Prompt injection pattern detected in A2A message")
            text = pattern.sub("[FILTERED]", text)
    return text


SENSITIVE_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|password|token|credential)\s*[:=]\s*\S+"),
    re.compile(r"(?i)(sk-[a-zA-Z0-9]{20,})"),
    re.compile(r"(?i)(ghp_[a-zA-Z0-9]{20,})"),
    re.compile(r"(?i)(xoxb-[a-zA-Z0-9-]+)"),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
]


def filter_outbound(text: str) -> str:
    for pattern in SENSITIVE_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text.strip()


class RateLimiter:
    def __init__(self, max_requests: int = 20, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._buckets: Dict[str, list] = defaultdict(list)
        self._lock = Lock()

    def allow(self, client_id: str) -> bool:
        now = time.time()
        with self._lock:
            bucket = self._buckets[client_id]
            self._buckets[client_id] = [ts for ts in bucket if ts > now - self.window]
            if len(self._buckets[client_id]) >= self.max_requests:
                return False
            self._buckets[client_id].append(now)
            return True


_AUDIT_MAX_SIZE = 10 * 1024 * 1024  # 10 MB


class AuditLogger:
    def __init__(self, log_path: Optional[Path] = None):
        if log_path is None:
            log_path = Path.home() / ".hermes" / "a2a_audit.jsonl"
        self.log_path = log_path
        self._lock = Lock()

    def _rotate_if_needed(self) -> None:
        try:
            if self.log_path.exists() and self.log_path.stat().st_size > _AUDIT_MAX_SIZE:
                rotated = self.log_path.with_suffix(".jsonl.old")
                if rotated.exists():
                    rotated.unlink()
                self.log_path.rename(rotated)
        except Exception:
            pass

    def log(self, event_type: str, data: dict) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            **data,
        }
        try:
            with self._lock:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                self._rotate_if_needed()
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            logger.debug("Failed to write A2A audit log", exc_info=True)


audit = AuditLogger()
