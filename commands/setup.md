---
description: Set your plan (pro / max5 / max20 / team / api) and language
argument-hint: "max5, language ru"
allowed-tools: Bash(python3:*), Bash(python:*), Bash(py:*)
---
Settings from the user: $ARGUMENTS
If the plan is not given, ask for it (pro, max5, max20, team, team-premium, api); "Max" alone is ambiguous —
ask whether it is Max 5× ($100) or Max 20× ($200). Optionally the language (en, ru, es, zh, fr, de, pt).

Using the first interpreter that works (`python3`, `python`, `py -3`), run:
`<interpreter> "${CLAUDE_PLUGIN_ROOT}/skills/mytokens/scripts/mytokens.py" set [--plan <plan>] [--set-lang <code>]`
