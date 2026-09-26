# Skill Template — Agentic OS v3

Use this template when creating new skills manually or reviewing auto-generated skills.

## SKILL.md Template

```markdown
---
name: <skill-name>
description: >
  <One paragraph describing WHEN and WHY this skill should be activated.
  Include concrete trigger phrases that users might use.
  Example triggers: "fix imports", "optimize query", "review security".>
---

# <Skill Title>

## When to Use

- <Situation 1 where this skill applies>
- <Situation 2>

## Prerequisites

- <What must exist before running (files, config, etc.)>

## Instructions

### Step 1: <Action Title>

<Clear instruction using Claude Code tools>
- Use `Read` to load: <specific files>
- Use `Grep` to find: <specific patterns>

### Step 2: <Action Title>

<Next instruction>

### Step 3: <Action Title>

<Final instruction>

## Output

```
<Expected output format — keep consistent across skills>
```

## What NOT to Do

- Do not: <common mistake 1>
- Do not: <common mistake 2>

## Example

**Input:** <Concrete scenario>
**Expected behavior:** <What the skill should do>
**Output:** <Example output>
```

## Checklist for New Skills

- [ ] Name: kebab-case, 1-64 chars
- [ ] Description: Contains trigger phrases
- [ ] Steps: Use explicit Claude Code tool names (Read, Write, Edit, Bash, Glob, Grep)
- [ ] Anti-patterns: At least one "Do not" rule
- [ ] Example: At least one concrete example
- [ ] Output format: Consistent with other skills

## Layer Guide

The 5 active skills, grouped by layer. (Trigger phrases in frontmatter MUST be English — tests enforce this.)

| Layer | Purpose | Skills |
|-------|---------|--------|
| core | Memory, context, session lifecycle | session-bootstrap, wrap-up, context-keeper |
| analysis | Pattern detection, skill generation | pattern-extractor |
| knowledge | Wiki write-path | obsidian-sync |

> Note: `code-reviewer`, `test-validator`, and `tdd` were merged into `quality-gate` (v3.x), which
> was removed in v4.0.0 together with research-pipeline, wiki-query, retrospective and
> skill-generator (folded into pattern-extractor). The deprecated agents `improvement-scout` and
> `fix-reviewer` were removed (2026-04-30), `improvement-agent`/`research-agent` in 4.15.0;
> `self-improve` was archived in v5.0.0 (`_archived/`). memory-maintenance, iteration-logger and
> sync-context became commands with a script core in v5.0.0 (maintain, log, sync-context).
