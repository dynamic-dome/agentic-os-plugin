---
name: status
description: Show Agentic OS memory system status and health
allowed_tools: ["Read", "Glob", "Bash"]
---

# Agentic OS Status

Display the current state of the memory system.

## What to show

1. **Memory System Health:**
   - Does `.agent-memory/` exist? List missing directories/files
   - Last session summary (first 5 lines of `session-summary.md`)

2. **Statistics:**
   - Total iterations logged (count entries in `iterations/iteration-log.md`)
   - Total patterns (count in `patterns/patterns.json`)
   - Total errors (count in `iterations/errors.json`)
   - Total decisions (count in `context/decisions.json`)

3. **Format** as a compact table.
