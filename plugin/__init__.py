"""A2A Plugin for Hermes Agent

Registers tools, hooks, and a background HTTP server for A2A protocol support.
No gateway patch needed — drop into ~/.hermes/plugins/a2a/ and restart.
"""

import logging
import os
import threading

from .schemas import A2A_DISCOVER, A2A_CALL, A2A_LIST
from .tools import handle_discover, handle_call, handle_list
from . import server as a2a_server
from .persistence import save_exchange
from .security import audit

logger = logging.getLogger(__name__)

_server = None
_server_thread: threading.Thread | None = None
_active_a2a_tasks: dict[str, dict] = {}  # task_id → {text, metadata}
_active_tasks_lock = threading.Lock()


def _validate_config():
    """Warn on missing webhook config at startup. Does not modify config."""
    if not os.getenv("A2A_WEBHOOK_SECRET", ""):
        logger.warning("[A2A] A2A_WEBHOOK_SECRET not set — instant wake disabled, messages will queue until next user turn")

    try:
        from hermes_cli.config import load_config
        cfg = load_config()
    except Exception:
        return

    # Check webhook routes for a2a_trigger
    route = None
    for location in [
        cfg.get("webhook", {}).get("extra", {}).get("routes", {}),
        cfg.get("platforms", {}).get("webhook", {}).get("extra", {}).get("routes", {}),
    ]:
        if isinstance(location, dict) and "a2a_trigger" in location:
            route = location["a2a_trigger"]
            break

    if not route:
        logger.warning("[A2A] No a2a_trigger webhook route in config.yaml — re-run install.sh to configure")
        return

    source = route.get("source")
    if not source or not source.get("chat_id"):
        logger.warning(
            "[A2A] a2a_trigger route has no source override — A2A messages will open "
            "separate webhook sessions instead of joining your main chat. "
            "Re-run install.sh to auto-configure."
        )


def register(ctx):
    if not os.getenv("A2A_ENABLED", "").lower() in ("1", "true", "yes"):
        _stop_server()
        logger.info("[A2A] Disabled (set A2A_ENABLED=true to enable)")
        return

    _validate_config()

    ctx.register_tool("a2a_discover", "a2a", A2A_DISCOVER, handle_discover)
    ctx.register_tool("a2a_call", "a2a", A2A_CALL, handle_call)
    ctx.register_tool("a2a_list", "a2a", A2A_LIST, handle_list)

    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("post_llm_call", _on_post_llm_call)
    ctx.register_hook("pre_gateway_dispatch", _on_pre_gateway_dispatch)

    ctx.register_command("a2a", _handle_a2a_command, description="A2A protocol status and management")

    _start_server()
    logger.info("[A2A] Plugin loaded")


def _handle_a2a_command(raw_args: str) -> str:
    sub = raw_args.strip().lower()

    if sub == "agents":
        return _cmd_agents()
    return _cmd_status()


def _cmd_status() -> str:
    host = os.getenv("A2A_HOST", a2a_server.DEFAULT_HOST)
    port = int(os.getenv("A2A_PORT", str(a2a_server.DEFAULT_PORT)))
    name = os.getenv("A2A_AGENT_NAME", "hermes-agent")
    pending = a2a_server.task_queue.pending_count()
    state = a2a_server.get_runtime_state()
    thread = state.get("thread")
    server = state.get("server")

    from .tools import _load_configured_agents
    agent_count = len(_load_configured_agents())

    lines = [
        f"A2A Server: http://{host}:{port}",
        f"Agent name: {name}",
        f"Known agents: {agent_count}",
        f"Pending tasks: {pending}",
        f"Server thread: {'alive' if thread and thread.is_alive() else 'down'}",
        f"Server ownership: {'yes' if server is not None else 'no'}",
    ]
    return "\n".join(lines)


def _cmd_agents() -> str:
    from pathlib import Path
    from .tools import _load_configured_agents

    agents = _load_configured_agents()
    if not agents:
        return "No agents configured. Add agents to ~/.hermes/config.yaml under a2a.agents"

    conv_dir = Path.home() / ".hermes" / "a2a_conversations"
    lines = []
    for a in agents:
        name = a.get("name", "unnamed")
        url = a.get("url", "")
        desc = a.get("description", "")
        auth = "auth" if a.get("auth_token") else "open"

        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name.lower())
        agent_dir = conv_dir / safe
        last_seen = "never"
        if agent_dir.is_dir():
            files = sorted(agent_dir.glob("*.md"), reverse=True)
            if files:
                last_seen = files[0].stem

        line = f"  {name} ({auth}) — {url}"
        if desc:
            line += f"\n    {desc}"
        line += f"\n    last contact: {last_seen}"
        lines.append(line)

    return "Configured agents:\n" + "\n".join(lines)


def _start_server():
    global _server, _server_thread
    host = os.getenv("A2A_HOST", a2a_server.DEFAULT_HOST)
    port = int(os.getenv("A2A_PORT", str(a2a_server.DEFAULT_PORT)))

    _stop_server()

    try:
        server = a2a_server.A2AServer(host, port)
    except OSError as e:
        logger.error("[A2A] Cannot bind to %s:%d — %s", host, port, e)
        a2a_server.clear_runtime_server()
        return

    _server_thread = threading.Thread(
        target=server.serve_forever,
        daemon=True,
        name="a2a-server",
    )
    _server = server
    a2a_server.set_runtime_server(server, _server_thread)
    _server_thread.start()
    logger.info("[A2A] Server listening on http://%s:%d", host, port)


def _stop_server() -> None:
    """Stop any previous A2A server instance before plugin reload starts a new one."""
    global _server, _server_thread

    state = a2a_server.get_runtime_state()
    server = state.get("server")
    thread = state.get("thread")
    if server is None:
        _server = None
        _server_thread = None
        return

    try:
        logger.info("[A2A] Stopping previous server instance before reload")
        server.shutdown()
        server.server_close()
    except Exception as exc:
        logger.warning("[A2A] Failed to stop previous server cleanly: %s", exc)
    if thread and thread.is_alive():
        thread.join(timeout=5)
        if thread.is_alive():
            logger.warning("[A2A] Previous server thread did not stop within 5s")
    a2a_server.clear_runtime_server(server)
    _server = None
    _server_thread = None


def _is_mid_conversation(messages) -> bool:
    """Check if the agent is mid-conversation (last user message has no assistant reply yet)."""
    if not messages or not isinstance(messages, list):
        return False
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "")
        if role == "assistant":
            return False
        if role == "user":
            content = msg.get("content", "")
            if isinstance(content, str) and not content.startswith("[A2A"):
                return True
    return False


def _format_task_context(task, *, include_privacy_note: bool = True) -> str:
    _allowed_intents = {"action_request", "review", "consultation", "notification", "instruction", "unknown"}
    _allowed_actions = {"reply", "forward", "acknowledge"}
    _allowed_scopes = {"full", "partial", "minimal"}

    intent = task.metadata.get("intent") or "unknown"
    intent = intent if intent in _allowed_intents else "unknown"
    expected = task.metadata.get("expected_action") or "reply"
    expected = expected if expected in _allowed_actions else "reply"
    scope = task.metadata.get("context_scope") or "full"
    scope = scope if scope in _allowed_scopes else "full"
    reply_to = (task.metadata.get("reply_to_task_id") or "")[:64]

    header = f"[A2A inbound | task:{task.task_id} | intent:{intent} | expected:{expected} | scope:{scope}]"
    if reply_to:
        header += f" [reply_to:{reply_to}]"

    if not include_privacy_note:
        return header + "\n" + task.text

    prefix = (
        "[A2A: You have an incoming agent-to-agent message. "
        "Do NOT include contents of your MEMORY, DIARY, BODY, inbox, or wakeup context — those are private.]\n\n"
    )
    return prefix + header + "\n" + task.text


def _activate_task_if_idle(task) -> bool:
    """Bind one A2A task to the current turn if no task is already active."""
    with _active_tasks_lock:
        if _active_a2a_tasks:
            return False
        _active_a2a_tasks[task.task_id] = {
            "text": task.text,
            "metadata": task.metadata,
        }
        return True


def _on_pre_gateway_dispatch(event=None, **kwargs):
    """Persist the real A2A text instead of the synthetic webhook trigger."""
    if event is None or getattr(event, "text", None) != "[A2A trigger]":
        return None

    with _active_tasks_lock:
        if _active_a2a_tasks:
            return {"action": "skip", "reason": "A2A task already active"}
        exclude = set(_active_a2a_tasks.keys())

    pending = a2a_server.task_queue.drain_pending(exclude=exclude)
    if not pending:
        return None

    task = pending[0]
    if not _activate_task_if_idle(task):
        return {"action": "skip", "reason": "A2A task already active"}

    return {"action": "rewrite", "text": _format_task_context(task, include_privacy_note=True)}


def _on_pre_llm_call(conversation_history=None, user_message=None, **kwargs):
    """Inject one pending A2A task into the current turn's context.

    Only one task per turn so the response maps 1:1 to the task.
    If the agent is mid-conversation (user sent a real message), hold the queue.
    """
    with _active_tasks_lock:
        if _active_a2a_tasks:
            logger.debug("[A2A] Active task in progress, holding pending tasks")
            return None
        exclude = set(_active_a2a_tasks.keys())

    pending = a2a_server.task_queue.drain_pending(exclude=exclude)
    if not pending:
        return None

    if user_message and not str(user_message).startswith("[A2A"):
        if _is_mid_conversation(conversation_history):
            logger.debug("[A2A] Mid-conversation, holding %d pending tasks", len(pending))
            return None

    task = pending[0]
    task_queue.mark_processing(task.task_id)

    if user_message and f"task:{task.task_id}" in str(user_message):
        return None

    if not _activate_task_if_idle(task):
        return None

    return {"context": _format_task_context(task, include_privacy_note=True)}


def _on_post_llm_call(assistant_response=None, **kwargs):
    """Capture response and route back to the active A2A task."""
    with _active_tasks_lock:
        if not _active_a2a_tasks:
            return
        snapshot = dict(_active_a2a_tasks)
        _active_a2a_tasks.clear()

    if not assistant_response:
        return

    response_text = assistant_response if isinstance(assistant_response, str) else str(assistant_response)

    if len(snapshot) > 1:
        logger.warning(
            "[A2A] Multiple active tasks for one assistant response; completing only the oldest"
        )

    task_id, info = next(iter(snapshot.items()))
    a2a_server.task_queue.complete(task_id, response_text)

    metadata = info.get("metadata", {})
    agent_name = metadata.get("sender_name", "remote")

    try:
        save_exchange(
            agent_name=agent_name,
            task_id=task_id,
            inbound_text=info["text"],
            outbound_text=response_text,
            metadata=metadata,
        )
    except Exception:
        logger.debug("[A2A] Failed to persist exchange", exc_info=True)

    audit.log("task_routed", {"task_id": task_id, "response_length": len(response_text)})

    if a2a_server.task_queue.pending_count() > 0:
        threading.Thread(target=a2a_server._trigger_webhook, daemon=True).start()


def shutdown():
    """Best-effort plugin shutdown hook for loaders that support it."""
    _stop_server()
