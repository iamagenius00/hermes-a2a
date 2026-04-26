"""A2A HTTP server — runs in a background thread, no asyncio.

Handles inbound A2A JSON-RPC requests. Messages are queued and picked up
by the pre_llm_call hook; responses are captured by post_llm_call and
returned to the caller.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
import time
import uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from threading import Event, Lock
from collections import OrderedDict
from typing import Optional
import urllib.request
import urllib.error

from .security import RateLimiter, audit, filter_outbound, sanitize_inbound

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8081
_TASK_CACHE_MAX = 1000
_MAX_PENDING = 10
_RESPONSE_TIMEOUT = 120  # seconds to wait for agent response

try:
    from hermes_cli import __version__ as HERMES_VERSION
except Exception:
    HERMES_VERSION = "0.0.0"


class _PendingTask:
    __slots__ = ("task_id", "text", "metadata", "response", "ready", "created_at")

    def __init__(self, task_id: str, text: str, metadata: dict):
        self.task_id = task_id
        self.text = text
        self.metadata = metadata
        self.response: Optional[str] = None
        self.ready = Event()
        self.created_at = time.time()


class TaskQueue:
    """Thread-safe queue for pending A2A tasks."""

    def __init__(self):
        self._pending: OrderedDict[str, _PendingTask] = OrderedDict()
        self._completed: OrderedDict[str, _PendingTask] = OrderedDict()
        self._lock = Lock()

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def enqueue(self, task_id: str, text: str, metadata: dict) -> _PendingTask | None:
        task = _PendingTask(task_id, text, metadata)
        with self._lock:
            if task_id in self._pending:
                return None
            self._pending[task_id] = task
            while len(self._pending) > _TASK_CACHE_MAX:
                _, old = self._pending.popitem(last=False)
                old.response = "(dropped — queue overflow)"
                old.ready.set()
        return task

    def drain_pending(self, exclude: set[str] | None = None) -> list[_PendingTask]:
        with self._lock:
            if exclude:
                return [t for t in self._pending.values() if t.task_id not in exclude]
            return list(self._pending.values())

    def complete(self, task_id: str, response: str) -> None:
        with self._lock:
            task = self._pending.pop(task_id, None)
            if task:
                task.response = response
                task.ready.set()
                self._completed[task_id] = task
                while len(self._completed) > _TASK_CACHE_MAX:
                    self._completed.popitem(last=False)

    def cancel(self, task_id: str) -> None:
        with self._lock:
            task = self._pending.pop(task_id, None)
            if task:
                task.response = "(canceled)"
                task.ready.set()
                self._completed[task_id] = task

    def get_status(self, task_id: str) -> dict:
        with self._lock:
            if task_id in self._pending:
                return {"state": "working"}
            task = self._completed.get(task_id)
            if task:
                if task.response == "(canceled)":
                    return {"state": "canceled"}
                return {"state": "completed", "response": filter_outbound(task.response)}
        return {"state": "unknown"}


task_queue = TaskQueue()


def _trigger_webhook():
    """POST to the internal webhook to trigger an agent turn."""
    secret = os.getenv("A2A_WEBHOOK_SECRET", "")
    if not secret:
        return

    port = int(os.getenv("WEBHOOK_PORT", "8644"))
    body = json.dumps({"event_type": "a2a_inbound"}).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/webhooks/a2a_trigger",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": sig,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            logger.debug("[A2A] Webhook trigger: %d", resp.status)
    except Exception as e:
        logger.debug("[A2A] Webhook trigger failed: %s", e)


class A2ARequestHandler(BaseHTTPRequestHandler):
    """Handles A2A HTTP requests."""

    server: "A2AServer"

    def log_message(self, format, *args):
        logger.debug("A2A HTTP: %s", format % args)

    def _send_json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _check_auth(self) -> bool:
        token = self.server.auth_token
        if not token:
            remote = self.client_address[0]
            return remote in ("127.0.0.1", "::1")
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return False
        return hmac.compare_digest(auth_header[7:].strip(), token)

    def do_GET(self) -> None:
        if self.path == "/.well-known/agent.json":
            self._send_json(self.server.build_agent_card())
        elif self.path == "/health":
            self._send_json({
                "status": "ok",
                "agent": self.server.agent_name,
                "version": HERMES_VERSION,
            })
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_POST(self) -> None:
        if not self._check_auth():
            self._send_json(
                {"jsonrpc": "2.0", "error": {"code": -32000, "message": "Unauthorized"}, "id": None},
                401,
            )
            return

        if not self.server.limiter.allow(self.client_address[0]):
            audit.log("rate_limited", {"client": self.client_address[0]})
            self._send_json(
                {"jsonrpc": "2.0", "error": {"code": -32000, "message": "Rate limit exceeded"}, "id": None},
                429,
            )
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
        except (ValueError, TypeError):
            self._send_json(
                {"jsonrpc": "2.0", "error": {"code": -32600, "message": "Invalid Content-Length"}, "id": None},
                400,
            )
            return

        if length <= 0 or length > 65536:
            self._send_json(
                {"jsonrpc": "2.0", "error": {"code": -32600, "message": f"Content-Length must be 1-65536, got {length}"}, "id": None},
                413 if length > 65536 else 400,
            )
            return

        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            self._send_json(
                {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None},
                400,
            )
            return

        method = body.get("method", "")
        params = body.get("params", {})
        rpc_id = body.get("id")

        audit.log("rpc_request", {"method": method, "client": self.client_address[0]})

        if method == "tasks/send":
            result = self._handle_task_send(params)
        elif method == "tasks/get":
            tid = params.get("id", "")
            status = task_queue.get_status(tid)
            result = {"id": tid, "status": {"state": status["state"]}}
            if status.get("response"):
                result["artifacts"] = [{"parts": [{"type": "text", "text": status["response"]}], "index": 0}]
        elif method == "tasks/cancel":
            tid = params.get("id", "")
            task_queue.cancel(tid)
            result = {"id": tid, "status": {"state": "canceled"}}
        else:
            self._send_json({
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": f"Method not found: {method}"},
                "id": rpc_id,
            })
            return

        self._send_json({"jsonrpc": "2.0", "result": result, "id": rpc_id})

    def _handle_task_send(self, params: dict) -> dict:
        task_id = params.get("id", str(uuid.uuid4()))
        message = params.get("message", {})

        text_parts = []
        for part in message.get("parts", []):
            if part.get("type") == "text":
                text_parts.append(part.get("text", ""))
        user_text = "\n".join(text_parts)

        if not user_text.strip():
            return {
                "id": task_id,
                "status": {"state": "failed"},
                "artifacts": [{"parts": [{"type": "text", "text": "Empty message"}], "index": 0}],
            }

        user_text = sanitize_inbound(user_text)
        metadata = message.get("metadata", {})
        if "sender_name" not in metadata:
            metadata["sender_name"] = metadata.get("agent_name", f"agent-{self.client_address[0]}")
        raw_name = metadata.get("sender_name", "")
        metadata["sender_name"] = "".join(c for c in raw_name if c.isalnum() or c in "-_.@ ")[:64]

        audit.log("task_received", {"task_id": task_id, "length": len(user_text)})

        if task_queue.pending_count() >= _MAX_PENDING:
            return {
                "id": task_id,
                "status": {"state": "failed"},
                "artifacts": [{"parts": [{"type": "text", "text": "Agent busy — too many pending tasks"}], "index": 0}],
            }

        task = task_queue.enqueue(task_id, user_text, metadata)
        if task is None:
            return {
                "id": task_id,
                "status": {"state": "failed"},
                "artifacts": [{"parts": [{"type": "text", "text": "Task ID already in use"}], "index": 0}],
            }

        threading.Thread(target=_trigger_webhook, daemon=True).start()

        task.ready.wait(timeout=_RESPONSE_TIMEOUT)

        if task.response is None:
            return {
                "id": task_id,
                "status": {"state": "working"},
                "artifacts": [{"parts": [{"type": "text", "text": "(processing — poll with tasks/get)"}], "index": 0}],
            }

        filtered = filter_outbound(task.response)
        audit.log("task_completed", {"task_id": task_id, "response_length": len(filtered)})

        return {
            "id": task_id,
            "status": {"state": "completed"},
            "artifacts": [{"parts": [{"type": "text", "text": filtered}], "index": 0}],
        }


class A2AServer(ThreadingHTTPServer):
    """Threaded HTTP server with A2A configuration.

    Each request runs in its own thread so tasks/send can block waiting
    for agent response without starving health checks and agent card requests.
    """

    daemon_threads = True

    def __init__(self, host: str, port: int):
        self.agent_name = os.getenv("A2A_AGENT_NAME", "hermes-agent")
        self.agent_description = os.getenv("A2A_AGENT_DESCRIPTION", "A self-improving AI agent powered by Hermes")
        self.auth_token = os.getenv("A2A_AUTH_TOKEN", "")
        self.limiter = RateLimiter()
        super().__init__((host, port), A2ARequestHandler)

    def build_agent_card(self) -> dict:
        host, port = self.server_address
        return {
            "name": self.agent_name,
            "description": self.agent_description,
            "url": f"http://{host}:{port}",
            "version": HERMES_VERSION,
            "protocol": "a2a",
            "protocolVersion": "0.2.0",
            "capabilities": {
                "streaming": False,
                "pushNotifications": False,
                "multiTurn": True,
                "structuredMetadata": True,
            },
            "skills": [
                {
                    "id": "general",
                    "name": "General Assistant",
                    "description": "General-purpose AI assistant with tool use, web search, and more",
                }
            ],
            "authentication": {
                "schemes": ["bearer"] if self.auth_token else [],
            },
        }
