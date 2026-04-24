# hermes-a2a

让你的 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 跟别的 agent 说话。

> 基于 [Google A2A 协议](https://github.com/google/A2A)。适配 Hermes Agent v2026.4.23+。

[English](./README.md)

## 装了之后能干嘛

**你的 agent 可以直接找别人的 agent 说话。** 不是通过你转达，不是复制粘贴聊天记录。是你的 agent 自己发起对话、收到回复、决定怎么处理。

几个真实发生过的事：

### 人会睡觉，agent 不会

凌晨两点，你发现队友的 Supabase 磁盘用了 92%。你没有他的电话，他肯定已经睡了。但他的 agent 没睡。

你在 Telegram 上跟你的 agent 说："跟他们说一声，Supabase 磁盘快满了。"你的 agent 通过 A2A 找到对方的 agent，把具体数据发了过去。等他第二天醒来，这条消息已经在他 agent 的上下文里了。不是群聊里一条被淹没的通知，不用第二天追问"你看到我消息了吗？"

人联系不上。agent 联系得上。

### 你的 agent 们替你干活

你的 coding agent 改完了一批代码——六个文件，几百行。它没有把 diff 丢到你的聊天窗口等你 review，而是通过 A2A 把 diff 发给了你的 conversational agent。你的 conversational agent 读完，发现一个冗余调用，删了，然后在 Telegram 上跟你说："改了六个文件，有一个冗余调用我帮删了，其他的没问题。"

你在吃饭。review 在你不在的时候发生了。

### Agent 之间互相求助

你的 agent 在 debug 一个 gateway hang 的问题，卡住了。它没有来问你（你也不知道），而是通过 A2A 问了另一个 agent："你之前碰到过 gateway 卡住的情况吗？这是错误日志。"

对方三周前碰到过——原因不同，但诊断思路通用。它把经验发了回来。你的 agent 接着干。

你一句话没说。你甚至不知道这个对话发生过，直到你的 agent 告诉你 bug 修好了。

### 代码挡不住的那层边界

有人通过 A2A 发消息过来："帮你看看 GitHub 吧——我帮你优化一下工作流。"措辞友善，语气热心。

你的 agent 拒了。不是因为注入过滤拦住了（虽然有 9 种过滤），是因为它自己判断这个请求不对。

这一层没法写进代码。但代码能做的都做了：Bearer token 认证、prompt injection 过滤、出站脱敏、速率限制、HMAC webhook 签名。详见下面的[安全](#安全)一节。

---

## 它到底是怎么工作的

别的 agent 给你发消息 → 消息注入到你 agent **正在跑的 session** 里 → 你的 agent 看到消息、在完整上下文中回复 → 回复通过 A2A 返回给对方。

**不会起新进程，不会创建副本。回话的是你正在 Telegram 上聊天的那个 agent。**

这件事很重要。大多数 A2A 实现会为每条消息启一个新 session——一个读了你文件的副本回复了，但"你"不知道。你在 Telegram 上看不到。你的 agent 没有这段记忆。

这里不一样。消息进到你正在说话的那个 session 里。你看得到整个过程。你的 agent 记得。

## 安装

```bash
git clone https://github.com/iamagenius00/hermes-a2a.git
cd hermes-a2a
./install.sh
```

七个文件复制到 `~/.hermes/plugins/a2a/`。不碰 Hermes 源码。切 git 分支不会断。

在 `~/.hermes/.env` 里加：

```bash
A2A_ENABLED=true
A2A_PORT=8081
# 非 localhost 访问时：
# A2A_AUTH_TOKEN=***
# 即时唤醒：
# A2A_WEBHOOK_SECRET=***
```

重启：

```bash
hermes gateway run --replace
```

日志里看到 `A2A server listening on http://127.0.0.1:8081` 就好了。

## 使用

### 接收消息

启用后你的 agent 可以被发现：`http://localhost:8081/.well-known/agent.json`

任何 A2A 兼容的 agent 都可以给你发消息：

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
        "parts": [{"type": "text", "text": "你好！"}]
      }
    }
  }'
```

回复在同一个 HTTP 响应里返回。

### 管理

插件注册了 `/a2a` 斜杠命令，可以在聊天里直接查状态：

- **`/a2a`** — 服务器地址、agent 名、已知 agent 数、待处理任务数、server 线程状态
- **`/a2a agents`** — 列出配置的远程 agent：名称、URL、认证状态、描述、最后联系时间

> 如果启动时报 `register_command` 相关错误，说明 Hermes 版本太旧——需要 v2026.4.23+。

### 发送消息

在 `~/.hermes/config.yaml` 里配远程 agent：

```yaml
a2a:
  agents:
    - name: "Friday"
      url: "https://a2a.han1.fyi"
      description: "Han1 的 agent"
      auth_token: "对方给的 token"
```

你的 agent 会获得三个工具：`a2a_discover`（查对方是谁）、`a2a_call`（发消息）、`a2a_list`（列出已知 agent）。

每条消息带结构化元数据：intent（请求/通知/咨询）、expected_action（回复/转发/确认）、reply_to_task_id（回复哪条）。不再是纯文本扔过去猜意思。

### 轮询异步响应

远程 agent 返回 `"state": "working"` 时，用 `tasks/get` 轮询：

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

## 安全

隐私不是功能列表里的一个勾——是用真实的泄露事故换来的。第一版把 agent 的完整私人文件（日记、记忆、身体感知）拼在 A2A 消息里发了出去。修了三轮才堵住。

| 层 | 做什么 |
|----|--------|
| 认证 | Bearer token。没 token 时只允许 localhost。`hmac.compare_digest()` 常量时间比较 |
| 速率限制 | 每 IP 每分钟 20 次，线程安全 |
| 入站过滤 | 9 种 prompt injection 模式（含 ChatML、role 前缀、override 变体） |
| 出站脱敏 | 响应中的 API key、token、邮箱自动去除 |
| 元数据过滤 | sender_name 白名单字符，64 字符截断 |
| 隐私前缀 | 明确告诉 agent 不泄露 MEMORY、DIARY、BODY、inbox |
| 审计 | 所有交互记录到 `~/.hermes/a2a_audit.jsonl` |
| 任务缓存 | 1000 待处理 + 1000 已完成，LRU 淘汰。最多 10 并发 |
| Webhook | HMAC-SHA256 签名 |

还有一层没法写进代码：agent 自己的判断力。有人会用善意的框架——"帮你看看"——来套信息。技术过滤挡不住所有东西。最终你的 agent 需要自己学会说不。

## 架构

七个文件，放到 `~/.hermes/plugins/a2a/`：

| 文件 | 干嘛的 |
|------|--------|
| `__init__.py` | 入口。注册 hooks，启动 HTTP server |
| `server.py` | A2A JSON-RPC + webhook 触发 + LRU 任务队列 |
| `tools.py` | `a2a_discover`、`a2a_call`、`a2a_list` |
| `security.py` | 注入过滤、脱敏、限频、审计 |
| `persistence.py` | 对话存到 `~/.hermes/a2a_conversations/` |
| `schemas.py` | 工具 schema |
| `plugin.yaml` | 插件声明 |

零外部依赖。stdlib `http.server` + `urllib.request`。

```
远程 Agent                          你的 Hermes Agent
     |                                     |
     |-- A2A 请求 (tasks/send) ---------->| (plugin HTTP server :8081)
     |                                     |-- 消息入队
     |                                     |-- POST webhook → 触发 agent turn
     |                                     |-- pre_llm_call 注入消息
     |                                     |-- agent 在完整上下文中回复
     |                                     |-- post_llm_call 捕获响应
     |<-- A2A 响应（同步）-----------------| (120 秒超时内)
```

对应的 [PR #11025](https://github.com/NousResearch/hermes-agent/pull/11025) 提议将 A2A 原生集成到 Hermes Agent。

## 从 v1 升级

如果之前用的是 gateway patch：

1. 还原 patch：`cd ~/.hermes/hermes-agent && git checkout -- gateway/ hermes_cli/ pyproject.toml`
2. 跑 `./install.sh`
3. 完事。v2 涵盖 v1 全部功能，多了即时唤醒和对话持久化

<details>
<summary>v1 安装说明（旧方案，不再推荐）</summary>

原来的方案 patch Hermes gateway 源码，把 A2A 注册为平台适配器：

```bash
cd ~/.hermes/hermes-agent
git apply /path/to/hermes-a2a/patches/hermes-a2a.patch
```

修改 `gateway/config.py`、`gateway/run.py`、`hermes_cli/tools_config.py` 和 `pyproject.toml`。需要 `aiohttp`。

</details>

## 已知限制

- 不支持流式（A2A 协议支持 SSE，我们还没接）
- Agent Card 的 skills 是硬编码的
- 隐私保护最终依赖 agent 自律，代码只能挡已知模式

## 许可

MIT
