---
description: MyTokens — 5-hour and weekly usage, limit forecast and a dashboard with charts
allowed-tools: Bash(python3:*), Bash(python:*), Bash(py:*)
---
Run the MyTokens script with the first interpreter that works — `python3`, then `python`, then `py -3`
(on Windows `python3` may be a Microsoft Store placeholder that prints "Python was not found"; skip it):

`<interpreter> "${CLAUDE_PLUGIN_ROOT}/skills/mytokens/scripts/mytokens.py" report --open`

It prints a summary and opens the dashboard in the browser. Summarize it for the user in at most
6 lines, in the user's language: percent used per window, reset times, whether they will hit a limit
before it resets (and when), how many tokens they will be short. If a limit is a "rough estimate",
suggest /mytokens:calibrate.

If no interpreter works, or the script exits with code 3 (no local logs), use the numbers mode of the
mytokens skill: ask for the Usage page and forecast from it.
