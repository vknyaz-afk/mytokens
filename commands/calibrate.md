---
description: Calibrate limits using the numbers shown in /usage (percent used and reset time)
argument-hint: "session 37% resets 1:40pm, week 12% resets Oct 3 10am"
---
The user passed the readings from /usage (or Settings → Usage): $ARGUMENTS

If there are no arguments, ask the user to open /usage and send the percentage and reset time for each window.

Use the first interpreter that works — `python3`, `python`, `py -3` — and for each window run
(window: session | week | week:opus | week:sonnet):

`<interpreter> "${CLAUDE_PLUGIN_ROOT}/skills/mytokens/scripts/mytokens.py" calibrate --window <window> --percent <number> --resets "<reset time as shown>"`

If the reset is given as "in N h M min", use `--resets-in-min <minutes>` instead. Then run
`<interpreter> "${CLAUDE_PLUGIN_ROOT}/skills/mytokens/scripts/mytokens.py" report --no-html` and briefly show the result in the user's language.
If Python isn't available, there is nothing to calibrate: use the numbers mode of the mytokens skill instead.
