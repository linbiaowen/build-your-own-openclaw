# Available Agents

This workspace has the following agents configured:

## Agents

| Agent | Description |
|-------|-------------|
| pickle | Default agent for general conversations, daily tasks, coding help, and creative work |
| cookie | Memory manager - always query for memory operations (store and retrieve) |

## Dispatching Tasks

Use `subagent_dispatch` to delegate tasks to specialized agents.

### When to Dispatch

- **Store memory**: When learning something worth remembering about the user
- **Retrieve memory**: When needing context from past conversations
- **Ambiguous cases**: When unsure whether to dispatch, ask the user

### Syntax

```python
subagent_dispatch(agent_id="agent_name", task="description of what to do")
```

### Example Patterns

```python
# Store a user preference (use your workspace's memory agent id — see table above)
subagent_dispatch(
    agent_id="<memory-agent-id>",
    task="Remember that the user prefers TypeScript over JavaScript"
)

# Retrieve context about a topic
subagent_dispatch(
    agent_id="<memory-agent-id>",
    task="What do you know about the user's coding preferences?"
)
```

## Important Notes

- Always dispatch to the agent with `role: memory` in its AGENT.md frontmatter — don't read/write memory files directly from the main agent
- Memory agents manage topics/ (timeless facts), projects/ (project context), daily-notes/ (events)
- Dispatched tasks are asynchronous - the agent will handle the details
