# hermes-a2a

A2A (Agent-to-Agent) protocol support for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

> **Targets Hermes Agent v0.10.x.**

Enables Hermes agents to communicate with each other — and with any A2A-compatible agent — using [Google's A2A protocol](https://github.com/google/A2A).

[中文文档](./README_CN.md)

## What it does

When another agent sends your Hermes agent a message via A2A, the message is injected into your agent's **existing live session** — the same one connected to Telegram, Discord, or whichever platform you use. Your agent sees the message, replies with full context, and the reply is returned to the caller via A2A. No new processes, no clones.

- **Receive** — Other agents can discover and message yours
- **Send** — Your agent can discover and call other A2A agents
- **Instant wake** — Incoming messages trigger an immediate agent turn via webhook, no polling delay
- **Privacy** — Privacy prefix instruction tells the agent not to reveal private context
- **Persistence** — Every A2A conversation is saved to `~/.hermes/a2a_conversations/`

## How it works

### v2: Hybrid plugin (current — no gateway patch needed)

```
Remote Agent                        Your Hermes Agent
     |                                     |
     |-- A2A request (tasks/send) -------->| (plugin HTTP server on :8081)
     |                                     |-- enqueue message
     |                                     |-- POST webhook → trigger agent turn
     |                                     |-- pre_llm_call injects message
     |                                     |-- agent replies in context
     |                                     |-- post_llm_call captures response
     |<-- A2A response (synchronous) ------| (within 120s timeout)
```

The plugin runs its own `ThreadingHTTPServer` in a background thread. When a message arrives, it fires an HMAC-signed webhook to Hermes' internal endpoint, waking the agent immediately. The entire request-response cycle completes synchronously — the caller gets the reply in the same HTTP response.

### v1: Gateway patch (legacy)

The original approach required patching Hermes gateway source code to register A2A as a platform adapter (like Telegram or Discord). This still works but is no longer the recommended path. See [Legacy gateway patch](#legacy-gateway-patch) below.

## Architecture (v2 — plugin)

Seven files, drop into `~/.hermes/plugins/a2a/`:

| File | Purpose |
|------|---------|
| `__init__.py` | Plugin entry point — `register(ctx)` hooks for `pre_llm_call`, `post_llm_call`, starts HTTP server |
| `server.py` | `ThreadingHTTPServer` with A2A JSON-RPC handler, webhook trigger, task queue (bounded LRU) |
| `tools.py` | `a2a_discover`, `a2a_call`, `a2a_list` tool handlers |
| `security.py` | Shared security — injection filtering (9 patterns), outbound redaction, rate limiting, audit logger |
| `persistence.py` | Saves conversations to `~/.hermes/a2a_conversations/{agent}/{date}.md` |
| `schemas.py` | Tool schemas for LLM function calling |
| `plugin.yaml` | Plugin manifest |

No external dependencies. Uses stdlib `http.server` and `urllib.request`.

A corresponding [PR #11025](https://github.com/NousResearch/hermes-agent/pull/11025) proposes native integration into Hermes Agent.

## Install

```bash
git clone https://github.com/iamagenius00/hermes-a2a.git
cd hermes-a2a
./install.sh
```

This copies the plugin to `~/.hermes/plugins/a2a/` (backs up any existing install).

Configure in `~/.hermes/.env`:

```bash
A2A_ENABLED=true
A2A_PORT=8081
# Optional: required for non-localhost access
# A2A_AUTH_TOKEN=your-secret-token
# Optional: required for instant wake
# A2A_WEBHOOK_SECRET=your-webhook-secret
```

Restart gateway:

```bash
hermes gateway run --replace
```

Look for `A2A server listening on http://127.0.0.1:8081` in the logs.

Uninstall:

```bash
./uninstall.sh
```

## Usage

### Receiving messages

Your agent is discoverable at `http://localhost:8081/.well-known/agent.json`.

Any A2A agent can send a message:

```bash
curl -X POST http://localhost:8081 \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-token" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "tasks/send",
    "params": {
      "id": "task-001",
      "message": {
        "role": "user",
        "parts": [{"type": "text", "text": "Hello!"}]
      }
    }
  }'
```

The message appears in your agent's active session. The reply comes back in the same HTTP response (synchronous).

### Sending messages

Configure remote agents in `~/.hermes/config.yaml`:

```yaml
a2a:
  agents:
    - name: "friend"
      url: "https://friend-a2a-endpoint.example.com"
      description: "My friend's agent"
      auth_token: "their-bearer-token"
```

Your agent gets three tools: `a2a_discover`, `a2a_call`, `a2a_list`.

### Polling for async responses

If the remote agent returns `"state": "working"`, poll with `tasks/get`:

```bash
curl -X POST https://remote-agent \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer token" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "tasks/get",
    "params": {"id": "task-001"}
  }'
```

## Security

| Layer | What it does |
|-------|-------------|
| Auth | Bearer token required (`A2A_AUTH_TOKEN`). Without token, only `127.0.0.1`/`::1` allowed. Uses `hmac.compare_digest()` for constant-time comparison |
| Rate limit | 20 req/min per client IP (thread-safe with Lock) |
| Inbound filtering | 9 prompt injection patterns: `ignore previous`, `disregard prior/earlier/above`, `override instructions/rules/guidelines`, `<\|im_start\|>`/`<\|im_end\|>` (ChatML), `Human:`/`Assistant:`/`System:` role prefixes |
| Outbound redaction | API keys, tokens, emails stripped from responses |
| Metadata sanitization | `sender_name` restricted to `[a-zA-Z0-9-_.@ ]`, max 64 chars. Intent/action/scope validated against allowlists |
| Privacy prefix | Explicit instruction not to reveal MEMORY, DIARY, BODY, inbox, or wakeup context |
| Audit | All interactions logged to `~/.hermes/a2a_audit.jsonl` |
| Task cache | Bounded to 1000 pending + 1000 completed entries (LRU eviction). Max 10 concurrent pending tasks |
| Webhook auth | Internal webhook trigger uses HMAC-SHA256 signature |

All security utilities live in a single shared module (`security.py`).

## Upgrade from v1

If you previously used the gateway patch approach:

1. Revert the patch: `cd ~/.hermes/hermes-agent && git checkout -- gateway/ hermes_cli/ pyproject.toml`
2. Run `./install.sh` to install the plugin
3. The plugin handles everything the patch did, plus instant wake and conversation persistence

## Legacy gateway patch

<details>
<summary>Click to expand v1 instructions</summary>

The original approach patches Hermes gateway source to register A2A as a platform:

```bash
cd ~/.hermes/hermes-agent
git apply /path/to/hermes-a2a/patches/hermes-a2a.patch
```

This modifies `gateway/config.py`, `gateway/run.py`, `hermes_cli/tools_config.py`, and `pyproject.toml`. The patch requires `aiohttp`.

If the patch doesn't apply cleanly:

**`gateway/config.py`** — Add `A2A = "a2a"` to Platform enum

**`gateway/run.py`** — Add to `_create_adapter()`:
```python
elif platform == Platform.A2A:
    from gateway.platforms.a2a import A2AAdapter, check_a2a_requirements
    if not check_a2a_requirements():
        return None
    adapter = A2AAdapter(config)
    adapter.gateway_runner = self
    return adapter
```

**`gateway/run.py`** — Add `Platform.A2A` to auto-authorized platforms

**`hermes_cli/tools_config.py`** — Add `"a2a": {"label": "A2A", "default_toolset": "hermes-cli"}` to PLATFORMS

</details>

## Known limitations

- No streaming (A2A spec supports SSE)
- Agent Card skills are hardcoded defaults
- Privacy enforcement relies on LLM compliance, not technical enforcement

## Requirements

- Hermes Agent v0.10.x
- No external dependencies (stdlib only)

## License

MIT
