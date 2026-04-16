# hermes-a2a

A2A (Agent-to-Agent) protocol support for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

Enables Hermes agents to communicate with each other — and with any A2A-compatible agent — using [Google's A2A protocol](https://github.com/google/A2A).

[中文文档](./README_CN.md)

## What it does

When another agent sends your Hermes agent a message via A2A, the message is injected into your agent's **existing live session** — the same one connected to Telegram, Discord, or whichever platform you use. Your agent sees the message, replies with full context, and the reply is returned to the caller via A2A. No new processes, no clones.

- **Receive** — Other agents can discover and message yours
- **Send** — Your agent can discover and call other A2A agents
- **Privacy** — Private context (memory, diary, etc.) is never leaked

## How it works

```
Remote Agent                        Your Hermes Gateway
     |                                     |
     |-- A2A request (tasks/send) -------->|
     |                                     |-- inject into live session
     |                                     |-- agent replies in context
     |<-- A2A response -------------------|
     |                                     |-- reply also shows on Telegram
```

A2A runs as a gateway platform adapter — same level as Telegram or Discord. Messages go through the standard gateway pipeline.

## Install

```bash
git clone https://github.com/iamagenius00/hermes-a2a.git
cd hermes-a2a
./install.sh
```

Then patch Hermes to register A2A as a platform:

```bash
cd ~/.hermes/hermes-agent
git apply /path/to/hermes-a2a/patches/hermes-a2a.patch
```

Enable in `~/.hermes/.env`:

```bash
A2A_ENABLED=true
A2A_PORT=8081
```

Restart gateway:

```bash
hermes gateway run --replace
```

See [detailed installation steps](#detailed-installation) below if the patch doesn't apply cleanly.

## Usage

### Receiving messages

Your agent is discoverable at `http://localhost:8081/.well-known/agent.json`.

Any A2A agent can send a message:

```bash
curl -X POST http://localhost:8081 \
  -H "Content-Type: application/json" \
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

The message appears in your agent's active session. The reply goes back via A2A AND to your messaging platform.

### Supported platforms

A2A messages are routed to whichever platform your agent is on. Set the home channel for your platform in `~/.hermes/.env`:

| Platform | Env var | How to get the ID |
|----------|---------|-------------------|
| Telegram | `TELEGRAM_HOME_CHANNEL=chat_id` | Use `/sethome` in your Telegram chat, or find your numeric chat ID |
| Discord | `DISCORD_HOME_CHANNEL=channel_id` | Right-click channel → Copy Channel ID |
| Slack | `SLACK_HOME_CHANNEL=channel_id` | Channel ID starts with `C` (find in channel details) |
| Signal | `SIGNAL_HOME_CHANNEL=phone` | Your Signal phone number |

Example for Telegram:

```bash
A2A_ENABLED=true
A2A_PORT=8081
TELEGRAM_HOME_CHANNEL=5448717161
```

Example for Discord:

```bash
A2A_ENABLED=true
A2A_PORT=8081
DISCORD_HOME_CHANNEL=1234567890
```

If multiple platforms have home channels set, priority is Telegram → Discord → Slack → Signal.

### Sending messages

Configure remote agents in `~/.hermes/config.yaml`:

```yaml
a2a:
  agents:
    - name: "friend"
      url: "http://friend-address:8081"
      description: "My friend's agent"
```

Your agent gets three tools: `a2a_discover`, `a2a_call`, `a2a_list`.

## Security

| Layer | What it does |
|-------|-------------|
| Auth | Optional Bearer token (`A2A_AUTH_TOKEN`) |
| Rate limit | 20 req/min per client IP |
| Inbound | Prompt injection patterns filtered |
| Outbound | API keys, tokens, emails redacted |
| Privacy | Agent instructed not to share memory/diary/body |
| Wakeup | A2A messages skip context injection |
| Audit | All interactions logged to `~/.hermes/a2a_audit.jsonl` |

## Wakeup plugin

If you use the [wakeup plugin](https://github.com/iamagenius00/wakeup), add this to `pre_llm_call` to prevent private context leaking via A2A:

```python
msg = user_message or ""
if "[A2A message from remote agent" in msg:
    _injected_sessions.add(sid)
    return None
```

## Detailed installation

If the patch doesn't apply cleanly, make these changes manually:

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

## Known limitations

- Response capture uses send() monkey-patch — works but should be a proper gateway hook
- No streaming (A2A spec supports SSE)
- No multi-turn tracking via task_id
- Agent Card skills are hardcoded

## Requirements

- Hermes Agent v0.8.0+
- aiohttp (likely already installed)

## License

MIT
