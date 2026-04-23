"""A2A tool schemas — what the LLM sees."""

A2A_DISCOVER = {
    "name": "a2a_discover",
    "description": (
        "Discover a remote A2A agent by fetching its Agent Card. "
        "Returns the agent's name, description, capabilities, and supported skills. "
        "Use this before calling an agent to understand what it can do. "
        "Provide either 'url' or 'name' (at least one is required)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Base URL of the remote agent (e.g. http://agent:8081)",
            },
            "name": {
                "type": "string",
                "description": "Name of a configured agent from ~/.hermes/config.yaml",
            },
        },
    },
}

A2A_CALL = {
    "name": "a2a_call",
    "description": (
        "Send a message/task to a remote A2A agent and get its response. "
        "Use a2a_discover first to learn what the agent can do."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Base URL of the remote agent",
            },
            "name": {
                "type": "string",
                "description": "Name of a configured agent (alternative to url)",
            },
            "message": {
                "type": "string",
                "description": "The message or task to send to the remote agent",
            },
            "task_id": {
                "type": "string",
                "description": "Optional task ID for continuing an existing conversation",
            },
            "reply_to_task_id": {
                "type": "string",
                "description": "Task ID this message is replying to (for multi-turn threading)",
            },
            "intent": {
                "type": "string",
                "enum": ["action_request", "review", "consultation", "notification", "instruction"],
                "description": "What kind of message this is",
            },
            "expected_action": {
                "type": "string",
                "enum": ["reply", "forward", "acknowledge"],
                "description": "What you expect the remote agent to do",
            },
        },
        "required": ["message"],
    },
}

A2A_LIST = {
    "name": "a2a_list",
    "description": (
        "List all configured remote A2A agents from ~/.hermes/config.yaml. "
        "Shows agent names, URLs, and descriptions."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}
