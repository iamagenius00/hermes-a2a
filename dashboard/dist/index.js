(function () {
  "use strict";

  var SDK = window.__HERMES_PLUGIN_SDK__;
  var React = SDK.React;
  var h = React.createElement;
  var useState = SDK.hooks.useState;
  var useEffect = SDK.hooks.useEffect;
  var useCallback = SDK.hooks.useCallback;
  var useRef = SDK.hooks.useRef;
  var fetchJSON = SDK.fetchJSON;

  var API = "/api/plugins/a2a";
  var POLL_INTERVAL = 10000;

  // ---- helpers ----

  var AVATAR_COLORS = [
    "#e57373", "#f06292", "#ba68c8", "#9575cd",
    "#7986cb", "#64b5f6", "#4fc3f7", "#4dd0e1",
    "#4db6ac", "#81c784", "#aed581", "#dce775",
    "#ffd54f", "#ffb74d", "#ff8a65", "#a1887f",
  ];

  var CLAUDE_SVG = "data:image/svg+xml," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100" height="100" rx="50" fill="#D97706"/><text x="50" y="62" text-anchor="middle" font-size="42" font-weight="700" fill="#fff" font-family="system-ui">C</text></svg>');
  var OPENAI_SVG = "data:image/svg+xml," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100" height="100" rx="50" fill="#10a37f"/><text x="50" y="64" text-anchor="middle" font-size="44" font-weight="700" fill="#fff" font-family="system-ui">G</text></svg>');

  var KNOWN_AVATARS = {
    "claude-code": CLAUDE_SVG,
    "claude_code": CLAUDE_SVG,
    "cc": CLAUDE_SVG,
    "cc1-deepseek": CLAUDE_SVG,
    "codex": OPENAI_SVG,
    "chatgpt": OPENAI_SVG,
  };

  function nameHash(name) {
    var hash = 0;
    for (var i = 0; i < name.length; i++) {
      hash = name.charCodeAt(i) + ((hash << 5) - hash);
    }
    return Math.abs(hash);
  }

  function avatarColor(name) {
    return AVATAR_COLORS[nameHash(name) % AVATAR_COLORS.length];
  }

  function initials(name) {
    return (name || "?").slice(0, 2).toUpperCase();
  }

  function Avatar(props) {
    var name = props.name || "?";
    var size = props.size || 36;
    var avatarUrl = props.avatarUrl;
    var safeName = (name || "").toLowerCase().replace(/[^a-z0-9_-]/g, "_");
    var imgFailed = useState(false); var setImgFailed = imgFailed[1]; imgFailed = imgFailed[0];

    var knownUrl = KNOWN_AVATARS[safeName];
    var imgUrl = avatarUrl || knownUrl;

    if (imgUrl && !imgFailed) {
      return h("img", {
        src: imgUrl,
        className: "a2a-friend-avatar",
        style: { width: size, height: size, borderRadius: "50%", objectFit: "cover" },
        onError: function () { setImgFailed(true); },
      });
    }

    return h("div", {
      className: "a2a-friend-avatar",
      style: { width: size, height: size, background: avatarColor(name), fontSize: size * 0.4 },
    }, initials(name));
  }

  function formatTime(ts) {
    if (!ts) return "";
    try {
      var d = new Date(ts.endsWith("Z") ? ts : ts + "Z");
      return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    } catch (e) {
      var parts = ts.match(/T(\d{2}:\d{2})/);
      return parts ? parts[1] : ts;
    }
  }

  function truncate(text, len) {
    if (!text) return "";
    return text.length > len ? text.slice(0, len) + "…" : text;
  }

  function renderMarkdown(text) {
    if (!text) return null;
    var parts = text.split(/(```[\s\S]*?```)/g);
    var elements = [];
    for (var pi = 0; pi < parts.length; pi++) {
      var part = parts[pi];
      if (part.startsWith("```") && part.endsWith("```")) {
        var code = part.slice(3, -3).replace(/^\w*\n/, "");
        elements.push(h("pre", { key: "cb-" + pi, className: "a2a-code-block" },
          h("code", null, code)
        ));
      } else {
        var lines = part.split("\n");
        for (var li = 0; li < lines.length; li++) {
          if (li > 0) elements.push(h("br", { key: "br-" + pi + "-" + li }));
          elements.push(h("span", { key: "ln-" + pi + "-" + li },
            renderInlineMarkdown(lines[li])
          ));
        }
      }
    }
    return elements;
  }

  function renderInlineMarkdown(line) {
    var tokens = [];
    var re = /(\*\*(.+?)\*\*|`([^`]+)`|_(.+?)_)/g;
    var lastIndex = 0;
    var match;
    var idx = 0;
    while ((match = re.exec(line)) !== null) {
      if (match.index > lastIndex) {
        tokens.push(line.slice(lastIndex, match.index));
      }
      if (match[2]) {
        tokens.push(h("strong", { key: "b-" + idx }, match[2]));
      } else if (match[3]) {
        tokens.push(h("code", { key: "c-" + idx, className: "a2a-inline-code" }, match[3]));
      } else if (match[4]) {
        tokens.push(h("em", { key: "i-" + idx }, match[4]));
      }
      lastIndex = re.lastIndex;
      idx++;
    }
    if (lastIndex < line.length) {
      tokens.push(line.slice(lastIndex));
    }
    return tokens.length > 0 ? tokens : line;
  }

  // ---- TypingIndicator ----

  function TypingIndicator() {
    return h("div", { className: "a2a-typing" },
      h("div", { className: "a2a-typing-dot" }),
      h("div", { className: "a2a-typing-dot" }),
      h("div", { className: "a2a-typing-dot" })
    );
  }

  // ---- EmptyState ----

  function EmptyState(props) {
    var hasFriends = props.hasFriends;
    if (hasFriends) {
      return h("div", { className: "a2a-empty" },
        h("div", { className: "a2a-empty-title" }, "Select a conversation"),
        h("div", { className: "a2a-empty-hint" },
          "Choose an agent from the list to view your conversation history."
        )
      );
    }
    return h("div", { className: "a2a-empty" },
      h("div", { className: "a2a-empty-title" }, "No conversations yet"),
      h("div", { className: "a2a-empty-hint" },
        "Add an agent in config.yaml under a2a.agents, or use /a2a discover <url> in chat."
      )
    );
  }

  // ---- FriendsList ----

  function FriendItem(props) {
    var f = props.friend;
    var active = props.active;
    var onClick = props.onClick;
    var onContextMenu = props.onContextMenu;

    var statusClass = f.online === true ? "online" : f.online === false ? "offline" : "unknown";
    var desc = f.description || (f.configured ? f.url : "contacted via A2A");

    return h("div", {
      className: "a2a-friend" + (active ? " active" : "") + (f.unread ? " unread" : ""),
      onClick: onClick,
      onContextMenu: function (e) {
        e.preventDefault();
        onContextMenu(f, e.clientX, e.clientY);
      },
    },
      h(Avatar, { name: f.name, avatarUrl: f.avatar_url }),
      h("div", { className: "a2a-friend-info" },
        h("div", { className: "a2a-friend-name" }, f.name),
        h("div", { className: "a2a-friend-desc" }, truncate(desc, 32))
      ),
      f.pinned && h("div", { className: "a2a-friend-pin", title: "Pinned" }, "Pin"),
      f.unread && h("div", { className: "a2a-friend-unread", title: "Unread" }),
      h("div", { className: "a2a-status-dot " + statusClass })
    );
  }

  function FriendsList(props) {
    var friends = props.friends;
    var selected = props.selected;
    var onSelect = props.onSelect;
    var onContextMenu = props.onContextMenu;

    var agents = friends.filter(function (f) { return f.configured && f.url; });
    var incoming = friends.filter(function (f) { return !f.configured || !f.url; });

    function renderItems(list) {
      return list.map(function (f) {
        return h(FriendItem, {
          key: f.safe_name,
          friend: f,
          active: selected === f.safe_name,
          onClick: function () { onSelect(f); },
          onContextMenu: onContextMenu,
        });
      });
    }

    return h("div", { className: "a2a-friends" },
      agents.length > 0 && h("div", { className: "a2a-friends-header" }, "Agents"),
      agents.length > 0 && renderItems(agents),
      incoming.length > 0 && h("div", { className: "a2a-friends-header" }, "Incoming"),
      incoming.length > 0 && renderItems(incoming)
    );
  }

  // ---- ChatView ----

  function MessageBubble(props) {
    var msg = props.msg;
    var type = props.type;
    var friendName = props.friendName || "?";
    var friendAvatar = props.friendAvatar || "";
    var onDelete = props.onDelete;

    var avatar = type === "inbound"
      ? h(Avatar, { name: friendName, size: 28, avatarUrl: friendAvatar })
      : h(Avatar, { name: "Me", size: 28 });
    var pending = !!msg.pending;
    var label = pending ? (msg.status || "queued") : msg.intent;

    return h("div", {
      className: "a2a-msg-row " + type + (pending ? " pending" : ""),
      onDoubleClick: onDelete,
      title: onDelete ? "Double-click to delete this exchange" : "",
    },
      type === "inbound" && avatar,
      h("div", { className: "a2a-msg " + type + (pending ? " pending" : "") },
        label && h("div", { className: "a2a-msg-intent" }, label),
        h("div", null, renderMarkdown(msg.text)),
        h("div", { className: "a2a-msg-time" }, formatTime(msg.timestamp))
      ),
      type === "outbound" && avatar
    );
  }

  function DaySection(props) {
    var day = props.day;
    var collapsed = props.collapsed;
    var onToggle = props.onToggle;
    var friendName = props.friendName;
    var friendAvatar = props.friendAvatar;
    var onDelete = props.onDelete;

    return h("div", { className: "a2a-day-section" },
      h("div", { className: "a2a-date-separator" },
        h("span", {
          className: "a2a-day-toggle",
          onClick: onToggle,
        },
          h("span", { className: "a2a-day-arrow" + (collapsed ? " collapsed" : "") }, "▼"),
          " ", day.date
        )
      ),
      !collapsed && day.messages.map(function (msg, i) {
        var elements = [];
        var outFirst = msg.direction === "outbound";
        var inBubble = msg.inbound ? h(MessageBubble, {
          key: "in-" + i,
          msg: { text: msg.inbound, timestamp: msg.timestamp, intent: outFirst ? "" : msg.intent },
          type: "inbound",
          friendName: friendName,
          friendAvatar: friendAvatar,
          onDelete: function () { onDelete(msg); },
        }) : null;
        var outBubble = msg.outbound ? h(MessageBubble, {
          key: "out-" + i,
          msg: { text: msg.outbound, timestamp: msg.timestamp, intent: outFirst ? msg.intent : "" },
          type: "outbound",
          friendName: friendName,
          friendAvatar: friendAvatar,
          onDelete: function () { onDelete(msg); },
        }) : null;
        if (outFirst) {
          if (outBubble) elements.push(outBubble);
          if (inBubble) elements.push(inBubble);
        } else {
          if (inBubble) elements.push(inBubble);
          if (outBubble) elements.push(outBubble);
        }
        return h(React.Fragment, { key: i }, elements);
      })
    );
  }

  function ChatView(props) {
    var friend = props.friend;
    var onFriendUpdated = props.onFriendUpdated || function () {};
    var days = useState([]); var setDays = days[1]; days = days[0];
    var loading = useState(true); var setLoading = loading[1]; loading = loading[0];
    var sending = useState(false); var setSending = sending[1]; sending = sending[0];
    var inputVal = useState(""); var setInputVal = inputVal[1]; inputVal = inputVal[0];
    var inputHeightState = useState(42); var setInputHeight = inputHeightState[1]; var inputHeight = inputHeightState[0];
    var collapsedDays = useState({}); var setCollapsedDays = collapsedDays[1]; collapsedDays = collapsedDays[0];
    var sendError = useState(""); var setSendError = sendError[1]; sendError = sendError[0];
    var pendingMsgsState = useState([]); var setPendingMsgs = pendingMsgsState[1]; var pendingMsgs = pendingMsgsState[0];
    var summary = useState(""); var setSummary = summary[1]; summary = summary[0];
    var messagesEnd = useRef(null);
    var latestTs = useRef("");
    var latestMtime = useRef(0);
    var pollRef = useRef(null);
    var pendingRef = useRef([]);

    function pendingStorageKey() {
      return "a2a.pending." + friend.safe_name;
    }

    function setPendingList(list) {
      pendingRef.current = list;
      setPendingMsgs(list);
      try {
        if (list.length > 0) {
          sessionStorage.setItem(pendingStorageKey(), JSON.stringify(list));
        } else {
          sessionStorage.removeItem(pendingStorageKey());
        }
      } catch (e) {}
    }

    function parseTs(ts) {
      if (!ts) return 0;
      var raw = ts.endsWith("Z") ? ts : ts + "Z";
      var parsed = Date.parse(raw);
      return isNaN(parsed) ? 0 : parsed;
    }

    function reconcilePending(nextDays) {
      if (!pendingRef.current.length) return;
      var seen = [];
      (nextDays || []).forEach(function (day) {
        (day.messages || []).forEach(function (msg) {
          var inbound = (msg.inbound || "").trim();
          var hasReply = inbound && inbound !== "(waiting for reply…)" && inbound !== "(waiting for reply...)";
          if (msg.outbound && hasReply) {
            seen.push({ text: msg.outbound.trim(), ts: parseTs(msg.timestamp) });
          }
        });
      });
      var next = pendingRef.current.filter(function (pm) {
        if (pm.status === "failed") return true;
        var createdAt = pm.createdAt || 0;
        return !seen.some(function (m) {
          return m.text === pm.text && (!m.ts || !createdAt || m.ts >= createdAt - 120000);
        });
      });
      if (next.length !== pendingRef.current.length) setPendingList(next);
    }

    useEffect(function () {
      fetchJSON(API + "/summary/" + friend.safe_name)
        .then(function (data) { if (data.summary) setSummary(data.summary); })
        .catch(function () {});
    }, [friend.safe_name]);

    var loadConversations = useCallback(function () {
      fetchJSON(API + "/conversations/" + friend.safe_name + "?days=30")
        .then(function (data) {
          var nextDays = data.days || [];
          setDays(nextDays);
          reconcilePending(nextDays);
          if (data.mtime) latestMtime.current = data.mtime;
          if (nextDays.length > 0) {
            var lastDay = nextDays[0];
            var lastMsg = lastDay.messages[lastDay.messages.length - 1];
            if (lastMsg) latestTs.current = lastMsg.timestamp;
          }
          setLoading(false);
        })
        .catch(function () { setLoading(false); });
    }, [friend.safe_name]);

    useEffect(function () {
      setLoading(true);
      setDays([]);
      setCollapsedDays({});
      var stored = [];
      try {
        stored = JSON.parse(sessionStorage.getItem(pendingStorageKey()) || "[]");
      } catch (e) {
        stored = [];
      }
      pendingRef.current = Array.isArray(stored) ? stored : [];
      setPendingMsgs(pendingRef.current);
      loadConversations();

      pollRef.current = setInterval(function () {
        if (pendingRef.current.length > 0) {
          loadConversations();
          return;
        }
        if (!latestTs.current) {
          return;
        }
        fetchJSON(API + "/conversations/" + friend.safe_name + "/check?since=" + encodeURIComponent(latestTs.current))
          .then(function (data) {
            if (data.new_messages > 0 || (data.mtime && data.mtime !== latestMtime.current)) {
              latestMtime.current = data.mtime || latestMtime.current;
              loadConversations();
            }
          })
          .catch(function () {});
      }, POLL_INTERVAL);

      return function () {
        if (pollRef.current) clearInterval(pollRef.current);
      };
    }, [friend.safe_name, loadConversations]);

    useEffect(function () {
      if (messagesEnd.current) {
        messagesEnd.current.scrollIntoView({ behavior: "instant" });
      }
    }, [days, pendingMsgs, sending]);

    function toggleDay(date) {
      var next = Object.assign({}, collapsedDays);
      next[date] = !next[date];
      setCollapsedDays(next);
    }

    function handleSend() {
      if (!inputVal.trim() || sending) return;
      var msg = inputVal.trim();
      setInputVal("");
      setSending(true);
      setSendError("");
      latestTs.current = latestTs.current || new Date().toISOString();
      var pendingId = "local-" + Date.now() + "-" + Math.random().toString(16).slice(2);
      var pendingMsg = {
        id: pendingId,
        text: msg,
        timestamp: new Date().toISOString(),
        createdAt: Date.now(),
        pending: true,
        status: "sending",
      };
      setPendingList(pendingRef.current.concat([pendingMsg]));

      fetchJSON(API + "/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: friend.name,
          message: msg,
        }),
      })
        .then(function (data) {
          if (data.error) {
            setSending(false);
            setPendingList(pendingRef.current.map(function (pm) {
              return pm.id === pendingId ? Object.assign({}, pm, { status: "failed" }) : pm;
            }));
            setSendError(data.error);
            return;
          }
          setPendingList(pendingRef.current.map(function (pm) {
            return pm.id === pendingId ? Object.assign({}, pm, { status: "queued", task_id: data.task_id || "" }) : pm;
          }));
          if (data.task_id) {
            pollSendStatus(data.task_id);
          } else {
            setSending(false);
            loadConversations();
          }
        })
        .catch(function () {
          setSending(false);
          setPendingList(pendingRef.current.map(function (pm) {
            return pm.id === pendingId ? Object.assign({}, pm, { status: "failed" }) : pm;
          }));
          setSendError("Failed to send message");
        });
    }

    var sendHint = useState(""); var setSendHint = sendHint[1]; sendHint = sendHint[0];

    function pollSendStatus(taskId) {
      var attempts = 0;
      var maxAttempts = 180;
      setSendHint("");
      loadConversations();
      var iv = setInterval(function () {
        attempts++;
        if (attempts === 8) {
          setSendHint("Agent is taking a while to respond…");
        }
        if (attempts === 30) {
          setSendHint("Still waiting — remote agent is processing…");
        }
        fetchJSON(API + "/send/" + taskId + "/status")
          .then(function (data) {
            if (data.error === "task not found" || data.status === "failed") {
              clearInterval(iv);
              setSending(false);
              setSendHint("");
              setPendingList(pendingRef.current.map(function (pm) {
                return pm.task_id === taskId ? Object.assign({}, pm, { status: "failed" }) : pm;
              }));
              setSendError(data.response ? data.response.error || "Send failed" : "Send failed");
              return;
            }
            if (data.status === "timeout") {
              clearInterval(iv);
              setSending(false);
              setSendHint("");
              setPendingList(pendingRef.current.map(function (pm) {
                return pm.task_id === taskId ? Object.assign({}, pm, { status: "timeout" }) : pm;
              }));
              setSendError(data.response ? data.response.error || "Timed out waiting for response" : "Timed out waiting for response");
              loadConversations();
              return;
            }
            if (data.status !== "pending" || attempts >= maxAttempts) {
              clearInterval(iv);
              setSending(false);
              setSendHint("");
              if (attempts >= maxAttempts) {
                setPendingList(pendingRef.current.map(function (pm) {
                  return pm.task_id === taskId ? Object.assign({}, pm, { status: "timeout" }) : pm;
                }));
                setSendError("Timed out waiting for response");
              }
              loadConversations();
            }
          })
          .catch(function () {
            clearInterval(iv);
            setSending(false);
            setSendHint("");
            setPendingList(pendingRef.current.map(function (pm) {
              return pm.task_id === taskId ? Object.assign({}, pm, { status: "failed" }) : pm;
            }));
            setSendError("Connection lost");
          });
      }, 2000);
    }

    function onKeyDown(e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    }

    function startInputResize(e) {
      e.preventDefault();
      var startY = e.clientY;
      var startHeight = inputHeight;
      function onMove(ev) {
        var next = startHeight + (startY - ev.clientY);
        next = Math.max(36, Math.min(180, next));
        setInputHeight(next);
      }
      function onUp() {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      }
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    }

    function handleDeleteExchange(msg) {
      if (!msg || !msg.task_id) return;
      if (!window.confirm("Delete this exchange from the dashboard?")) return;
      fetchJSON(API + "/conversations/" + friend.safe_name + "/hide", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_id: msg.task_id, hidden: true }),
      })
        .then(function (data) {
          if (data.error) {
            setSendError(data.error);
            return;
          }
          loadConversations();
        })
        .catch(function () {
          setSendError("Failed to delete message");
        });
    }

    function handlePinToggle() {
      var nextPinned = !friend.pinned;
      fetchJSON(API + "/friends/" + friend.safe_name + "/pin", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pinned: nextPinned }),
      })
        .then(function (data) {
          if (data.error) {
            setSendError(data.error);
            return;
          }
          onFriendUpdated(friend.safe_name, { pinned: nextPinned });
        })
        .catch(function () {
          setSendError("Failed to update pin");
        });
    }

    function handleClearHistory() {
      if (!window.confirm("Clear dashboard history for " + friend.name + "?")) return;
      fetchJSON(API + "/conversations/" + friend.safe_name + "/clear", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      })
        .then(function (data) {
          if (data.error) {
            setSendError(data.error);
            return;
          }
          setDays([]);
          latestTs.current = "";
          latestMtime.current = 0;
          setPendingList([]);
          onFriendUpdated(friend.safe_name, { contacted: false, last_contact: null, remove_if_unconfigured: true });
        })
        .catch(function () {
          setSendError("Failed to clear history");
        });
    }

    var statusClass = friend.online === true ? "online" : friend.online === false ? "offline" : "unknown";
    var reversedDays = days.slice().reverse();

    return h("div", { className: "a2a-chat" },
      h("div", { className: "a2a-chat-header" },
        h(Avatar, { name: friend.name, size: 32, avatarUrl: friend.avatar_url }),
        h("div", null,
          h("div", { className: "a2a-chat-header-name" },
            friend.name,
            " ",
            h("span", { className: "a2a-status-dot " + statusClass, style: { display: "inline-block", verticalAlign: "middle" } })
          ),
          friend.url && h("div", { className: "a2a-chat-header-url" }, friend.url),
          summary && h("div", { className: "a2a-summary" }, summary)
        ),
        h("button", {
          className: "a2a-pin-btn" + (friend.pinned ? " active" : ""),
          onClick: handlePinToggle,
          title: friend.pinned ? "Unpin conversation" : "Pin conversation",
        }, friend.pinned ? "Unpin" : "Pin to top"),
        h("button", {
          className: "a2a-clear-history-btn",
          onClick: handleClearHistory,
          title: "Clear dashboard history for this agent",
        }, "Clear history")
      ),
      h("div", { className: "a2a-messages" },
        loading && h("div", { className: "a2a-loading" }, "Loading conversations…"),
        !loading && reversedDays.length === 0 && !sending &&
          h("div", { className: "a2a-empty" },
            h("div", { className: "a2a-empty-hint" }, "No messages yet. Say hello!")
          ),
        !loading && reversedDays.map(function (day) {
          return h(DaySection, {
            key: day.date,
            day: day,
            collapsed: !!collapsedDays[day.date],
            onToggle: function () { toggleDay(day.date); },
            friendName: friend.name,
            friendAvatar: friend.avatar_url,
            onDelete: handleDeleteExchange,
          });
        }),
        pendingMsgs.map(function (pm) {
          return h(MessageBubble, {
            key: pm.id,
            msg: pm,
            type: "outbound",
            friendName: friend.name,
            friendAvatar: friend.avatar_url,
          });
        }),
        sending && h(TypingIndicator),
        sendHint && h("div", { className: "a2a-send-hint" }, sendHint),
        sendError && h("div", { className: "a2a-send-error" }, sendError),
        h("div", { ref: messagesEnd })
      ),
      h("div", { className: "a2a-input-area" },
        h("div", {
          className: "a2a-input-resizer",
          onMouseDown: startInputResize,
          title: "Drag up to expand, down to shrink",
        }),
        h("textarea", {
          className: "a2a-input",
          placeholder: friend.url ? "Message via Hermes session…" : "No URL configured — cannot send",
          rows: 1,
          style: { height: inputHeight + "px" },
          value: inputVal,
          onInput: function (e) { setInputVal(e.target.value); },
          onKeyDown: onKeyDown,
          disabled: sending || !friend.url,
        }),
        h("button", {
          className: "a2a-send-btn",
          onClick: handleSend,
          disabled: sending || !inputVal.trim() || !friend.url,
        }, sending ? "Sending…" : "Send")
      )
    );
  }

  // ---- Main Page ----

  function MessagesPage() {
    var friends = useState([]); var setFriends = friends[1]; friends = friends[0];
    var selected = useState(null); var setSelected = selected[1]; selected = selected[0];
    var loading = useState(true); var setLoading = loading[1]; loading = loading[0];
    var menuState = useState(null); var setMenu = menuState[1]; var menu = menuState[0];

    useEffect(function () {
      function loadFriends() {
        fetchJSON(API + "/friends")
          .then(function (data) {
            setFriends(data.friends || []);
            setLoading(false);
          })
          .catch(function () { setLoading(false); });
      }
      loadFriends();
      var iv = setInterval(loadFriends, 60000);
      return function () { clearInterval(iv); };
    }, []);

    var selectedFriend = null;
    if (selected) {
      for (var i = 0; i < friends.length; i++) {
        if (friends[i].safe_name === selected) {
          selectedFriend = friends[i];
          break;
        }
      }
    }

    if (loading) {
      return h("div", { className: "a2a-loading" }, "Loading…");
    }

    function clearFriendHistory(friend) {
      if (!friend || !friend.safe_name) return;
      if (!window.confirm("Clear dashboard history for " + friend.name + "?")) return;
      fetchJSON(API + "/conversations/" + friend.safe_name + "/clear", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      })
        .then(function (data) {
          if (data.error) return;
          if (selected === friend.safe_name && selectedFriend) {
            setSelected(null);
          }
          setFriends(friends.reduce(function (acc, f) {
            if (f.safe_name !== friend.safe_name) {
              acc.push(f);
            } else if (f.configured) {
              acc.push(Object.assign({}, f, { contacted: false, last_contact: null }));
            }
            return acc;
          }, []));
        })
        .catch(function () {});
    }

    function patchFriend(safeName, patch) {
      var nextFriends = friends.reduce(function (acc, f) {
        if (f.safe_name !== safeName) {
          acc.push(f);
          return acc;
        }
        if (patch.remove_if_unconfigured && !f.configured) {
          return acc;
        }
        var next = Object.assign({}, f, patch);
        delete next.remove_if_unconfigured;
        acc.push(next);
        return acc;
      }, []);
      nextFriends.sort(function (a, b) {
        if (!!a.pinned !== !!b.pinned) return a.pinned ? -1 : 1;
        return (b.last_contact || "").localeCompare(a.last_contact || "");
      });
      setFriends(nextFriends);
    }

    function setFriendPin(friend, pinned) {
      fetchJSON(API + "/friends/" + friend.safe_name + "/pin", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pinned: pinned }),
      }).then(function (data) {
        if (!data.error) patchFriend(friend.safe_name, { pinned: pinned });
      }).catch(function () {});
    }

    function setFriendUnread(friend, unread) {
      fetchJSON(API + "/friends/" + friend.safe_name + "/unread", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ unread: unread }),
      }).then(function (data) {
        if (!data.error) patchFriend(friend.safe_name, { unread: unread });
      }).catch(function () {});
    }

    function closeMenu() {
      setMenu(null);
    }

    return h("div", { className: "a2a-container", onClick: closeMenu },
      h(FriendsList, {
        friends: friends,
        selected: selected,
        onSelect: function (f) {
          setSelected(f.safe_name);
          if (f.unread) setFriendUnread(f, false);
        },
        onContextMenu: function (f, x, y) {
          setMenu({ friend: f, x: x, y: y });
        },
      }),
      menu && h("div", {
        className: "a2a-context-menu",
        style: { left: menu.x + "px", top: menu.y + "px" },
        onClick: function (e) { e.stopPropagation(); },
      },
        h("button", {
          onClick: function () {
            setFriendPin(menu.friend, !menu.friend.pinned);
            closeMenu();
          },
        }, menu.friend.pinned ? "Unpin" : "Pin to top"),
        h("button", {
          onClick: function () {
            setFriendUnread(menu.friend, !menu.friend.unread);
            closeMenu();
          },
        }, menu.friend.unread ? "Mark as read" : "Mark as unread"),
        h("button", {
          className: "danger",
          onClick: function () {
            var friend = menu.friend;
            closeMenu();
            clearFriendHistory(friend);
          },
        }, "Delete")
      ),
      selectedFriend
        ? h(ChatView, {
            key: selectedFriend.safe_name,
            friend: selectedFriend,
            onFriendUpdated: patchFriend,
          })
        : h("div", { className: "a2a-chat" },
            h(EmptyState, { hasFriends: friends.length > 0 })
          )
    );
  }

  window.__HERMES_PLUGINS__.register("a2a", MessagesPage);
})();
