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
   - Total learnings (count in `learnings/learnings.json`)
   - Total decisions (count in `context/decisions.json`)
   - Pflegelauf due? (`bash "${CLAUDE_PLUGIN_ROOT}/scripts/memory-thresholds.sh" .agent-memory` prints a
     `PFLEGELAUF:` line when a Pflegelauf is due; the threshold lives only in that script)

   `iterations/errors.json` and `patterns/` are frozen data since 6.0.0 and are not counted.

3. **Format** as a compact table.
