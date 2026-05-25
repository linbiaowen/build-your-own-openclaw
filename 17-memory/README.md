# Step 17: Memory

> Remember me!

## Prerequisites

Same as Step 09 - copy the config file and add your API key:

```bash
cp default_workspace/config.example.yaml default_workspace/config.user.yaml
# Edit config.user.yaml to add your API key
```

## What We Will Build

Long-term memory across all conversations. 

```
pickle: @cookie Do you know <topic> about user?
cookie: Yes, <content>.
```

## Key Components

- **Memory agent** - Specialized agent for memory management
- [default_workspace/agents/cookie/AGENT.md](../default_workspace/agents/cookie/AGENT.md)

### Agent roles (`AGENT.md` frontmatter)

| `role` | Example | Framework behavior |
|--------|---------|-------------------|
| `assistant` | pickle | User-facing agent; may get memory-delegate hints in the system prompt |
| `memory` | cookie | Receives per-user memory scope on `subagent_dispatch`; listed as delegate target |
| *(omitted)* | — | Same as a non-`memory` agent today |

Only `role: memory` is interpreted in code; other roles are for clarity and future routing.

## Try it out

```bash
cd 17-memory
uv run my-bot chat

# You: Remember that I my name is Zane
# Pickle: Got it! I've saved that preference.

uv run my-bot chat

# User: What's my name?
# Pickle: Based on your memory, you name is Zane! Hi Zane! 😸
```

## Note

This implementation uses **Specialized Agent** approach. Alternatives include:

| Approach | Description |
|----------|-------------|
| **Specialized Agent** (this) | Dedicated memory agent accessed via dispatch |
| **Direct Tools** | Memory tools in main agent |
| **Skill-Based** | Using CLI tools like grep |
| **Vector Database** | Semantic search over embeddings |

### Memory Directory Structure (Pickle Bot)

```
memories/
├── topics/
│   ├── preferences.md    # User preferences
│   └── identity.md       # User info
├── projects/
│   └── my-project.md     # Project-specific notes
└── daily-notes/
    └── 2024-01-15.md     # Daily journal
```

## What's Next

Deploy, extend, and customize!
