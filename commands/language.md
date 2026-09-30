---
description: Choose the plugin language — en (default), ru, es, zh, fr, de, pt
argument-hint: "ru"
---
The user wants to switch the language to: $ARGUMENTS
Map it to one of: en (English), ru (Русский), es (Español), zh (中文), fr (Français), de (Deutsch), pt (Português).
If nothing was given, list these options and ask which one.

Using the first interpreter that works (`python3`, `python`, `py -3`), run:
`<interpreter> "${CLAUDE_PLUGIN_ROOT}/skills/mytokens/scripts/mytokens.py" set --set-lang <code>`
Confirm in one line, in the chosen language.
