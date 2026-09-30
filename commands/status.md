---
description: MyTokens — short summary without opening the browser
allowed-tools: Bash(python3:*), Bash(python:*), Bash(py:*)
---
Run the MyTokens script with the first interpreter that works — `python3`, then `python`, then `py -3`
(skip a Windows `python3` that prints "Python was not found"):

`<interpreter> "${CLAUDE_PLUGIN_ROOT}/skills/mytokens/scripts/mytokens.py" report --no-html`

Show the table as is, then one line with the main takeaway (will the user hit a limit before the
reset), in the user's language. If no interpreter works or the script exits with code 3, use the
numbers mode of the mytokens skill.
