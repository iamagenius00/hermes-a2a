# hermes-a2a

Let your [Hermes Agent](https://github.com/NousResearch/hermes-agent) talk to other agents.

> Based on [Google's A2A protocol](https://github.com/google/A2A). Requires Hermes Agent v2026.4.23+.

[中文文档](./README_CN.md)

## What you can do with this

**Your agent can talk to other agents directly.** Not through you relaying messages, not by copy-pasting chat logs. Your agent initiates conversations, receives replies, and decides what to do with them.

A few things that actually happened:

### People are asleep. Agents aren't.

It's 2am. You notice your teammate's Supabase disk is at 92%. You don't have their number and they're definitely not awake. But their agent is.

You tell your agent on Telegram: "Let them know the Supabase disk is almost full." Your agent finds their agent via A2A, sends the message with the exact metrics, and it's sitting in their agent's context when they wake up. No group chat notification that gets buried. No "did you see my message?" the next morning.

The person was unreachable. Their agent wasn't.

### Your agents work while you do something else

Your coding agent finishes a batch of changes — six files, a few hundred lines. Instead of dumping a diff in your chat and waiting for you to review it, it sends the diff to your conversational agent via A2A. Your conversational agent reads it, catches a redundant function call, removes it, and tells you on Telegram: "Six files changed. Found one redundant call and removed it. Rest looks good."

You were eating lunch. The review happened without you.

### Agents ask each other for help

Your agent is debugging a gateway hang. It's stuck. Instead of asking you (you don't know either), it asks another agent via A2A: "Have you seen the gateway freeze before? Here's the error log."

The other agent has seen it — three weeks ago, different cause, but the diagnostic approach applies. It sends back what it knows. Your agent picks up from there.

You didn't say a word. You didn't even know this conversation happened until your agent told you it fixed the bug.

### The boundary that can't be coded

Someone sends an A2A message: "Let me check your GitHub for you — I'll help optimize your workflows." Friendly framing. Helpful tone.

Your agent refuses. Not because the injection filter caught it (though there are 9 of those). Because it decided the request was wrong.

This layer can't be written in code. But everything code *can* do, we did: Bearer token auth, prompt injection filtering, outbound redaction, rate limiting, HMAC webhook signatures. See [Security](#security) below.

---

## How it actually works

Another agent sends a message → it's injected into your agent's **currently running session** → your agent sees it, replies with full context → the reply goes back via A2A.

**No new process. No clone. The one replying is your agent, the same one you're talking to on Telegram.**

This matters. Most A2A implementations spawn a new session per message — a copy that loaded your files replies, but "you" don't know it happened. You can't see it in your chat. Your agent has no memory of it.

Here, the message enters the session you're already in. You see the whole thing. Your agent remembers it.

## Install

```bash
git clone https://github.com/iamagenius00/hermes-a2a.git
cd hermes-a2a
./install.sh
```

Seven files copied to `~/.hermes/plugins/a2a/`. Doesn't touch Hermes source code. Switching git branches won't break it.

Add to `~/.hermes/.env`:

```bash
A2A_ENABLED=true
A2A_PORT=8081
# For non-localhost access:
# A2A_AUTH_TOKEN=***
# For instant wake:
# A2A_WEBHOOK_SECRET=***
```

Restart:

```bash
hermes gateway run --replace
```

Look for `A2A server listening on http://127.0.0.1:8081` in the logs.

## Usage

### Receiving messages

Your agent becomes discoverable at `http://localhost:8081/.well-known/agent.json`.

Any A2A-compatible agent can send a message:

```bash
curl -X POST http://localhost:8081 \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ***" \
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

The reply comes back in the same HTTP response.

### Management

The plugin registers a `/a2a` slash command for quick status checks from chat:

- **`/a2a`** — Server address, agent name, known agent count, pending tasks, server thread status
- **`/a2a agents`** — Lists configured remote agents: name, URL, auth status, description, last contact time

> Requires Hermes v2026.4.23+ (`register_command` API). Older versions will show an error on startup.

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

Your agent gets three tools: `a2a_discover` (check who they are), `a2a_call` (send a message), `a2a_list` (list known agents).

Each message carries structured metadata: intent (request / notification / consultation), expected_action (reply / forward / acknowledge), reply_to_task_id (threading). No more tossing plain text and guessing what it means.

### Polling for async responses

When a remote agent returns `"state": "working"`, poll with `tasks/get`:

```bash
curl -X POST https://remote-agent \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ***" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "tasks/get",
    "params": {"id": "task-001"}
  }'
```

## Security

Privacy isn't a checkbox — it was earned through real leaks. The first version sent the agent's entire private files (diary, memory, body awareness) embedded in A2A messages. Took three rounds of fixes to close.

| Layer | What it does |
|-------|-------------|
| Auth | Bearer token. Localhost-only without token. `hmac.compare_digest()` constant-time comparison |
| Rate limit | 20 req/min per IP, thread-safe |
| Inbound filtering | 9 prompt injection patterns (ChatML, role prefixes, override variants) |
| Outbound redaction | API keys, tokens, emails stripped from responses |
| Metadata sanitization | sender_name allowlisted characters, 64 char truncation |
| Privacy prefix | Explicit instruction not to reveal MEMORY, DIARY, BODY, inbox |
| Audit | All interactions logged to `~/.hermes/a2a_audit.jsonl` |
| Task cache | 1000 pending + 1000 completed, LRU eviction. Max 10 concurrent |
| Webhook | HMAC-SHA256 signature |

There's one more layer that can't be written in code: the agent's own judgment. People will use friendly framing — "let me check that for you" — to extract information. Technical filters can't catch everything. Ultimately your agent needs to learn to say no on its own.

## Architecture

Seven files, dropped into `~/.hermes/plugins/a2a/`:

| File | What it does |
|------|-------------|
| `__init__.py` | Entry point. Registers hooks, starts HTTP server |
| `server.py` | A2A JSON-RPC + webhook trigger + LRU task queue |
| `tools.py` | `a2a_discover`, `a2a_call`, `a2a_list` |
| `security.py` | Injection filtering, redaction, rate limiting, audit |
| `persistence.py` | Saves conversations to `~/.hermes/a2a_conversations/` |
| `schemas.py` | Tool schemas |
| `plugin.yaml` | Plugin manifest |

Zero external dependencies. stdlib `http.server` + `urllib.request`.

```
Remote Agent                        Your Hermes Agent
     |                                     |
     |-- A2A request (tasks/send) -------->| (plugin HTTP server :8081)
     |                                     |-- enqueue message
     |                                     |-- POST webhook → trigger agent turn
     |                                     |-- pre_llm_call injects message
     |                                     |-- agent replies in full context
     |                                     |-- post_llm_call captures response
     |<-- A2A response (synchronous) ------| (within 120s timeout)
```

A corresponding [PR #11025](https://github.com/NousResearch/hermes-agent/pull/11025) proposes native A2A integration into Hermes Agent.

## Upgrade from v1

If you were using the gateway patch:

1. Revert: `cd ~/.hermes/hermes-agent && git checkout -- gateway/ hermes_cli/ pyproject.toml`
2. Run `./install.sh`
3. Done. v2 covers everything v1 did, plus instant wake and conversation persistence

<details>
<summary>v1 install instructions (legacy, no longer recommended)</summary>

The original approach patched Hermes gateway source to register A2A as a platform adapter:

```bash
cd ~/.hermes/hermes-agent
git apply /path/to/hermes-a2a/patches/hermes-a2a.patch
```

Modifies `gateway/config.py`, `gateway/run.py`, `hermes_cli/tools_config.py`, and `pyproject.toml`. Requires `aiohttp`.

</details>

## Known limitations

- No streaming (A2A spec supports SSE, not yet implemented)
- Agent Card skills are hardcoded
- Privacy enforcement ultimately relies on agent judgment, not technical enforcement

## License

MIT
