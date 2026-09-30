---
name: mytokens
description: Claude usage limits — how much of the 5-hour session and weekly limits is used, when they reset, when the user will run out at the current pace, how much extra usage costs, and how much the subscription saves versus API prices. Use when the user asks about their Claude limits, quota or usage, how much is left, when they will hit the limit, when it resets, or about buying extra usage. Works in every Claude app, from local Claude Code logs when possible and otherwise from the numbers on the Usage page.
allowed-tools: Bash(python3:*), Bash(python:*), Bash(py:*)
---

# MyTokens — Claude usage limits

Reply in the user's language. The script speaks en, ru, es, zh, fr, de and pt; `--lang <code>` goes
before the subcommand.

## 1. Choose the mode

**Full mode** needs two things: you can run commands on the user's own computer (Claude Code, or a
Cowork session running locally), and Python 3 is installed there.

Try these in order and stop at the first one that prints the usage table:

```
python3 "${CLAUDE_SKILL_DIR}/scripts/mytokens.py" report --no-html
python  "${CLAUDE_SKILL_DIR}/scripts/mytokens.py" report --no-html
py -3   "${CLAUDE_SKILL_DIR}/scripts/mytokens.py" report --no-html
```

- On Windows `python3` can be a Microsoft Store placeholder that prints "Python was not found" —
  treat that as not working and try the next line.
- Exit code 3 means there are no Claude Code logs on this machine: use numbers mode.
- If none of them works, or you can't run commands at all (claude.ai, the desktop or mobile chat),
  use numbers mode. Only in Claude Code, mention once that full mode needs Python 3:
  Windows `winget install Python.Python.3.12`, macOS `xcode-select --install`,
  Linux the `python3` package.

## 2. Full mode

Summarize the table in a few lines: percent used per window, reset time, whether the user will hit
the limit before the reset and when, and how many tokens they will be short. If a limit is a "rough
estimate", suggest `/mytokens:calibrate` with the numbers from `/usage`.

- Dashboard with charts: run the same interpreter with `report --open`.
- Calibrate from `/usage`: `calibrate --window session|week --percent N --resets "<time as shown>"`
  (or `--resets-in-min N`).
- Plan and language: `set --plan pro|max5|max20|team|team-premium|api`, `set --set-lang <code>`.

## 3. Numbers mode (no local data)

1. Ask for the Usage page — a screenshot or the numbers. In Claude Code it is `/usage`; on claude.ai
   and in the apps, Settings → Usage. For each window you need the percent used and when it resets.
2. Prefer "resets in 3 h 25 min" style values; they need no clock. If you only have a clock time
   ("resets 2:00 PM"), you also need the user's current local time — take it from the screenshot's
   status bar or ask.
3. If you can run Python (Claude Code, or code execution in chat), run:

   ```
   python3 "${CLAUDE_SKILL_DIR}/scripts/mytokens.py" --lang <code> manual \
     --win session 37 in:3h25m --win week 12 in:2d18h
   ```

   With clock times use `at:"2:00pm"` / `at:"Oct 3, 10am"` and add `--now "YYYY-MM-DD HH:MM"` with
   the user's local time. Model-specific weekly limits use the names `week:opus` / `week:sonnet`.

   If you can't run Python, compute it yourself for each window (5 h for the session, 7 days for the
   week): start = reset − length; elapsed = now − start; pace = percent ÷ elapsed;
   at reset = percent + pace × (reset − now). If that is 100% or more, the limit is hit at
   now + (100 − percent) ÷ pace, and the user will be short by (at reset − 100)%.
4. Present each window: percent used, reset, the forecast. Where you can, add a simple visual — a bar
   per window with the used part and the projected part up to the reset.
5. Say plainly what numbers mode can't show: history, per-model breakdown, the value of the
   subscription versus API prices, and the dashboard. They need Claude Code with Python 3 on the
   computer where the user works.

Early in a window (the first ~10% of it) the pace is based on very little time — call the forecast
rough.
