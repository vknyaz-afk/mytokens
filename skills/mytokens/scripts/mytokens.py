#!/usr/bin/env python3
"""mytokens.py — MyTokens: Claude usage limits tracker: 5-hour / weekly / monthly windows, history, forecast, top-up.

Python 3.8+ standard library only. UI strings live in i18n.json (en, ru, es, zh, fr, de, pt).

Data source: local Claude Code logs (~/.claude/projects/**/*.jsonl). Every model reply has
`usage` (input/output/cache tokens) and the model id; hitting a limit leaves a message like
"You've hit your session limit · resets 1:40pm".

Unit of usage: "API-equivalent $" — tokens weighted by the model's API prices. That puts
Opus and Haiku, cache and output on one scale, roughly how subscription limits weigh models.
"""
import argparse
import bisect
import json
import math
import os
import re
import statistics
import sys
import time
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path

HOME = Path.home()
DATA_DIR = Path(os.environ.get("MYTOKENS_DIR") or os.environ.get("CLAUDE_QUOTA_DIR") or HOME / ".claude" / "mytokens")
OLD_DATA_DIR = HOME / ".claude" / "quota"  # before the plugin was renamed to MyTokens
CONFIG = DATA_DIR / "config.json"
CACHE = DATA_DIR / "cache.json"
OUT_HTML = DATA_DIR / "dashboard.html"
HERE = Path(__file__).resolve().parent
CACHE_V = 2

H, D = 3600, 86400
LENGTHS = {"session": 5 * H, "week": 7 * D}

# $ per 1M tokens: (id prefix, name, input, output, cache read).
# Cache write: 5 min = 1.25 × input, 1 hour = 2 × input. Order matters: more specific first.
PRICES = [
    ("claude-fable-5-1", "Fable 5.1", 10, 50, 0.25),
    ("claude-mythos-5-1", "Mythos 5.1", 10, 50, 0.25),
    ("claude-fable-5", "Fable 5", 10, 50, 1.0),
    ("claude-mythos", "Mythos", 10, 50, 1.0),
    ("claude-opus-5-5", "Opus 5.5", 4, 20, 0.20),
    ("claude-opus-5", "Opus 5", 5, 25, 0.50),
    ("claude-opus-4-8", "Opus 4.8", 5, 25, 0.50),
    ("claude-opus-4-7", "Opus 4.7", 5, 25, 0.50),
    ("claude-opus-4-6", "Opus 4.6", 5, 25, 0.50),
    ("claude-opus-4-5", "Opus 4.5", 5, 25, 0.50),
    ("claude-opus-4-1", "Opus 4.1", 15, 75, 1.50),
    ("claude-opus-4", "Opus 4", 15, 75, 1.50),
    ("claude-sonnet-5-5", "Sonnet 5.5", 2, 10, 0.20),
    ("claude-sonnet-5", "Sonnet 5", 2, 10, 0.20),
    ("claude-sonnet-4", "Sonnet 4.x", 3, 15, 0.30),
    ("claude-3-7-sonnet", "Sonnet 3.7", 3, 15, 0.30),
    ("claude-haiku-4-5", "Haiku 4.5", 1, 5, 0.10),
    ("claude-3-5-haiku", "Haiku 3.5", 0.8, 4, 0.08),
]
FALLBACK_PRICE = ("?", "Unknown", 3, 15, 0.30)
FAMILIES = ["opus", "sonnet", "haiku", "fable", "other"]
TOPUP_MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5", "claude-fable-5-1"]
PLAN_PRICES = {"pro": 20, "max5": 100, "max20": 200, "team": 30, "team-premium": 150, "api": 0}


# ───────────────────────── i18n ─────────────────────────

I18N = json.loads((HERE / "i18n.json").read_text(encoding="utf-8"))
LANGS = list(I18N)
LANG = "en"


def T(key, **kw):
    s = I18N.get(LANG, {}).get(key) or I18N["en"].get(key, key)
    return s.format(**kw) if kw else s


def wtitle(wid):
    return T("w_" + wid.replace(":", "_"))


def norm_lang(v):
    v = (v or "").strip().lower().replace("_", "-").split("-")[0]
    return v if v in I18N else None


def resolve_lang(cli=None):
    """Priority: --lang flag, CLAUDE_QUOTA_LANG env, config, English."""
    for v in (cli, (os.environ.get("MYTOKENS_LANG") or (os.environ.get("MYTOKENS_LANG") or os.environ.get("CLAUDE_QUOTA_LANG"))), read_json(CONFIG, {}).get("lang")):
        if norm_lang(v):
            return norm_lang(v)
    return "en"


# ───────────────────────── utilities ─────────────────────────

def price(model):
    m = (model or "").lower()
    for p in PRICES:
        if p[0] in m:
            return p
    return FALLBACK_PRICE


def family(model):
    m = (model or "").lower()
    for f in ("opus", "sonnet", "haiku", "fable", "mythos"):
        if f in m:
            return "fable" if f == "mythos" else f
    return "other"


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, data, compact=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        if compact:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def parse_ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def local(ts):
    return datetime.fromtimestamp(ts).astimezone()


def fmt_time(ts, now=None):
    if ts is None:
        return "—"
    dt, n = local(ts), local(now or time.time())
    if dt.date() == n.date():
        return T("today") + dt.strftime(" %H:%M")
    if dt.date() == (n + timedelta(days=1)).date():
        return T("tomorrow") + dt.strftime(" %H:%M")
    return dt.strftime(T("date_fmt"))


def fmt_dur(sec):
    if sec is None:
        return "—"
    sec = max(0, int(sec))
    d, r = divmod(sec, D)
    h, r = divmod(r, H)
    m = r // 60
    if d:
        return T("dur_d", d=d, h=h)
    if h:
        return T("dur_h", h=h, m=m)
    return T("dur_m", m=m)


def fmt_tok(n):
    n = float(n or 0)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= div:
            return f"{n / div:.1f}{suf}"
    return f"{n:.0f}"


def fmt_usd(x):
    if x is None:
        return "—"
    return f"${x:,.2f}" if x < 1000 else f"${x:,.0f}"


def get_tz(name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return datetime.now().astimezone().tzinfo


MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
RESET_RE = re.compile(
    r"(?:(?P<mon>[A-Za-z]{3})[a-z]*\.?\s+(?P<day>\d{1,2}),?\s*(?:at\s+)?)?"
    r"(?P<h>\d{1,2})(?::(?P<mi>\d{2}))?\s*(?P<ap>am|pm)?", re.I)


def parse_reset(text, tz_name, base_ts):
    """'1:40pm' / 'May 11, 8am' / '14:00' → the nearest such moment after base_ts."""
    m = RESET_RE.search(text or "")
    if not m:
        return None
    tz = get_tz(tz_name) if tz_name else datetime.now().astimezone().tzinfo
    h, mi = int(m["h"]), int(m["mi"] or 0)
    ap = (m["ap"] or "").lower()
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    if h > 23 or mi > 59:
        return None
    base = datetime.fromtimestamp(base_ts, tz)
    try:
        if m["mon"] and m["mon"].lower()[:3] in MONTHS:
            dt = base.replace(month=MONTHS[m["mon"].lower()[:3]], day=int(m["day"]),
                              hour=h, minute=mi, second=0, microsecond=0)
            if dt.timestamp() < base_ts - D:
                dt = dt.replace(year=dt.year + 1)
        else:
            dt = base.replace(hour=h, minute=mi, second=0, microsecond=0)
            if dt.timestamp() <= base_ts:
                dt += timedelta(days=1)
    except ValueError:
        return None
    return dt.timestamp()


HIT_RE = re.compile(r"hit your (?P<kind>[\w ]*?)\s*limit(?P<rest>.*)", re.I | re.S)
RESETS_RE = re.compile(r"resets\s+(?P<when>[^()\n]+?)\s*(?:\((?P<tz>[^)]+)\))?\s*$", re.I)


def classify_hit(text, ts):
    m = HIT_RE.search(text)
    if not m:
        return None
    kind_txt = m["kind"].lower()
    r = RESETS_RE.search(m["rest"].strip())
    reset = parse_reset(r["when"], r["tz"], ts) if r else None
    if "spend" in kind_txt or "monthly" in kind_txt:
        kind = "month"
    elif "session" in kind_txt:
        kind = "session"
    elif "opus" in kind_txt:
        kind = "week:opus"
    elif "sonnet" in kind_txt:
        kind = "week:sonnet"
    elif "week" in kind_txt:
        kind = "week"
    elif reset is not None:
        kind = "session" if reset - ts <= 5 * H + 60 else "week"
    else:
        kind = "unknown"
    return {"ts": ts, "kind": kind, "reset": reset, "text": text.strip()[:160]}


# ───────────────────────── reading logs ─────────────────────────

def project_roots():
    roots = []
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    cands = [Path(p) for p in env.split(",") if p] if env else []
    cands += [HOME / ".claude", HOME / ".config" / "claude"]
    for c in cands:
        p = c / "projects"
        if p.is_dir() and p.resolve() not in [r.resolve() for r in roots]:
            roots.append(p)
    return roots


def parse_file(path):
    entries, hits = [], []
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return entries, hits
    with f:
        for line in f:
            if '"assistant"' not in line:
                continue
            is_err = '"rate_limit"' in line or '"isApiErrorMessage":true' in line
            if '"usage"' not in line and not is_err:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") != "assistant":
                continue
            ts = parse_ts(d.get("timestamp"))
            if ts is None:
                continue
            msg = d.get("message") or {}
            if is_err and (d.get("error") == "rate_limit" or d.get("isApiErrorMessage")):
                content = msg.get("content")
                text = " ".join(c.get("text", "") for c in content if isinstance(c, dict)) \
                    if isinstance(content, list) else str(content or "")
                if "limit" in text.lower():
                    hit = classify_hit(text, ts)
                    if hit:
                        hits.append(hit)
                continue
            u, model = msg.get("usage"), msg.get("model") or ""
            if not isinstance(u, dict) or model.startswith("<"):
                continue
            cc = u.get("cache_creation") or {}
            cw1h = cc.get("ephemeral_1h_input_tokens") or 0
            cw5 = cc.get("ephemeral_5m_input_tokens")
            if cw5 is None:
                cw5 = max(0, (u.get("cache_creation_input_tokens") or 0) - cw1h)
            key = f"{msg.get('id')}:{d.get('requestId')}" if msg.get("id") else d.get("uuid")
            mult = 2 if u.get("speed") == "fast" else 1
            entries.append([round(ts, 3), model, u.get("input_tokens") or 0, u.get("output_tokens") or 0,
                            cw5, cw1h, u.get("cache_read_input_tokens") or 0, mult, key])
    return entries, hits


class Entry:
    __slots__ = ("ts", "model", "inp", "out", "cw5", "cw1h", "cr", "cost", "fam", "tokens")

    def __init__(self, r):
        self.ts, self.model, self.inp, self.out, self.cw5, self.cw1h, self.cr = r[:7]
        mult = r[7]
        _, _, pi, po, pcr = price(self.model)
        self.cost = (self.inp * pi + self.cw5 * pi * 1.25 + self.cw1h * pi * 2
                     + self.cr * pcr + self.out * po) / 1e6 * mult
        self.fam = family(self.model)
        self.tokens = self.inp + self.out + self.cw5 + self.cw1h + self.cr


def load_data():
    """Incremental log parsing. The cache keeps history even after Claude Code deletes old logs."""
    cache = read_json(CACHE, {})
    if cache.get("v") != CACHE_V:
        cache = {"v": CACHE_V, "files": {}}
    files, changed = cache["files"], False
    for root in project_roots():
        for p in root.rglob("*.jsonl"):
            try:
                st = p.stat()
            except OSError:
                continue
            k = str(p)
            c = files.get(k)
            if c and c["m"] == st.st_mtime and c["s"] == st.st_size:
                continue
            e, h = parse_file(p)
            files[k] = {"m": st.st_mtime, "s": st.st_size, "e": e, "h": h}
            changed = True
    if changed:
        write_json(CACHE, cache, compact=True)

    seen, entries, hits_by = set(), [], {}
    for c in files.values():
        for r in c["e"]:
            if r[8] in seen:
                continue
            seen.add(r[8])
            entries.append(Entry(r))
        for h in c["h"]:
            k = (h["kind"], round(h["reset"] / 60) if h["reset"] else round(h["ts"] / 3600))
            if k not in hits_by or h["ts"] < hits_by[k]["ts"]:
                hits_by[k] = h
    entries.sort(key=lambda e: e.ts)
    hits = sorted(hits_by.values(), key=lambda h: h["ts"])
    return entries, hits


# ───────────────────────── calculations ─────────────────────────

class Series:
    """Fast interval sums (prefix sums per model family)."""

    def __init__(self, entries):
        self.entries = entries
        self.ts = [e.ts for e in entries]
        self.pref = {None: [0.0]}
        for f in FAMILIES:
            self.pref[f] = [0.0]
        for e in entries:
            for f in self.pref:
                self.pref[f].append(self.pref[f][-1] + (e.cost if f is None or e.fam == f else 0))

    def cost(self, a, b, fam=None):
        i, j = bisect.bisect_left(self.ts, a), bisect.bisect_left(self.ts, b)
        p = self.pref[fam]
        return p[j] - p[i]

    def slice(self, a, b):
        return self.entries[bisect.bisect_left(self.ts, a):bisect.bisect_left(self.ts, b)]


def window_family(wid):
    return wid.split(":", 1)[1] if ":" in wid else None


def session_blocks(entries, hits):
    """5-hour windows: start at the first request (floored to the hour) or at a known reset."""
    resets = sorted(h["reset"] for h in hits if h["kind"] == "session" and h["reset"])
    blocks, cur = [], None
    for e in entries:
        if cur is None or e.ts >= cur["end"]:
            start = e.ts - e.ts % H
            i = bisect.bisect_right(resets, e.ts)
            if i < len(resets) and resets[i] - 5 * H <= e.ts:
                start = resets[i] - 5 * H
            if cur and start < cur["end"]:
                start = cur["end"]
            cur = {"start": start, "end": start + 5 * H, "cost": 0.0, "tokens": 0, "n": 0, "last": e.ts}
            blocks.append(cur)
        cur["cost"] += e.cost
        cur["tokens"] += e.tokens
        cur["n"] += 1
        cur["last"] = e.ts
    return blocks


def periodic_window(anchor, length, now):
    k = math.ceil((now - anchor) / length)
    end = anchor + k * length
    if end <= now:
        end += length
    return end - length, end


def month_window(now):
    dt = local(now)
    start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    nxt = (start + timedelta(days=32)).replace(day=1)
    return start.timestamp(), nxt.timestamp()


def token_mix(entries):
    tot = {"in": 0, "out": 0, "cw5": 0, "cw1h": 0, "cr": 0}
    for e in entries:
        tot["in"] += e.inp
        tot["out"] += e.out
        tot["cw5"] += e.cw5
        tot["cw1h"] += e.cw1h
        tot["cr"] += e.cr
    s = sum(tot.values())
    if not s:
        return {"in": 0.02, "out": 0.03, "cw5": 0.05, "cw1h": 0.0, "cr": 0.90}, tot
    return {k: v / s for k, v in tot.items()}, tot


def usd_per_mtok(model, mix):
    _, _, pi, po, pcr = price(model)
    return (mix["in"] * pi + mix["cw5"] * pi * 1.25 + mix["cw1h"] * pi * 2
            + mix["cr"] * pcr + mix["out"] * po)


def median_recent(ests, n=3):
    vals = [v for _, v in sorted(ests)[-n:]]
    return statistics.median(vals) if vals else None


def build(now=None):
    as_of = os.environ.get("MYTOKENS_AS_OF") or os.environ.get("CLAUDE_QUOTA_AS_OF")  # debugging: replay the report at a past moment (epoch seconds)
    now = now or (float(as_of) if as_of else time.time())
    cfg = read_json(CONFIG, {})
    entries, hits = load_data()
    if as_of:
        entries = [e for e in entries if e.ts <= now]
        hits = [h for h in hits if h["ts"] <= now]
    S = Series(entries)
    blocks = session_blocks(entries, hits)
    manual = cfg.get("windows", {})

    last = entries[-1] if entries else None
    cur_model = last.model if last else "claude-opus-5-5"
    recent = S.slice(now - 7 * D, now + 1) or entries
    mix, mix_tot = token_mix(recent)
    # Limits are measured in model-weighted cost; everything the user sees is shown in tokens,
    # converted at the user's usual token mix over the last 7 days.
    rc = sum(e.cost for e in recent)
    tpu = sum(e.tokens for e in recent) / rc if rc > 0 else 1e6 / usd_per_mtok(cur_model, mix)
    first_ts = entries[0].ts if entries else now

    # typical pace of the user's past 5-hour windows (what a whole window usually takes ÷ 5 h) — used at
    # the start of a new window, when a few minutes of data would give a jumpy forecast
    paces = [b["cost"] / (5 * H) for b in blocks if b["end"] <= now and b["n"] >= 5][-20:]
    typical_rate = statistics.median(paces) if paces else None

    win_ids = ["session", "week"]
    for wid in ("week:opus", "week:sonnet"):
        if wid in manual or any(h["kind"] == wid for h in hits):
            win_ids.append(wid)
    win_ids.append("month")

    windows = []
    for wid in win_ids:
        fam = window_family(wid)
        base = wid.split(":")[0]
        man = manual.get(wid, {})
        kind_hits = [h for h in hits if h["kind"] == wid]

        # ── current window bounds
        rolling = False
        if base == "session":
            start = end = None
            anchor = man.get("reset")
            if anchor and anchor - 5 * H <= now < anchor:
                start, end = anchor - 5 * H, anchor
            else:
                for h in reversed(kind_hits):
                    if h["reset"] and h["reset"] - 5 * H <= now < h["reset"]:
                        start, end = h["reset"] - 5 * H, h["reset"]
                        break
            if start is None and blocks and blocks[-1]["end"] > now:
                start, end = blocks[-1]["start"], blocks[-1]["end"]
        elif base == "week":
            anchors = [(man.get("calibrated_at", 0), man["reset"])] if man.get("reset") else []
            anchors += [(h["ts"], h["reset"]) for h in hits if h["kind"].startswith("week") and h["reset"]]
            if anchors:
                start, end = periodic_window(max(anchors)[1], 7 * D, now)
            else:
                start, end, rolling = now - 7 * D, None, True
        else:
            start, end = month_window(now)

        used = S.cost(start, now + 1, fam) if start is not None else 0.0

        # ── limit: manual calibration / limit hits in logs / historical maximum
        est_hits = []
        for h in kind_hits:
            # the window must be fully covered by logs and not older than 120 days
            if h["reset"] and base in LENGTHS and h["reset"] - LENGTHS[base] >= first_ts \
                    and h["ts"] > now - 120 * D:
                v = S.cost(h["reset"] - LENGTHS[base], h["ts"] + 1, fam)
                if v > 0:
                    est_hits.append((h["ts"], v))
        limit, source = None, None
        if base != "month":  # a subscription has no monthly limit: month is shown as API-price cost only
            last_hit_ts = max((t for t, _ in est_hits), default=0)
            if man.get("limit") and man.get("calibrated_at", 0) >= last_hit_ts:
                limit, source = man["limit"], {"k": "calibrated", "at": man.get("calibrated_at")}
            elif est_hits:
                limit, source = median_recent(est_hits), {"k": "hits", "n": len(est_hits)}
            elif man.get("limit"):
                limit, source = man["limit"], {"k": "calibrated", "at": man.get("calibrated_at")}
            else:
                if base == "session":
                    hist = [b["cost"] for b in blocks if b["end"] <= now]
                else:
                    hist = [S.cost(t - 7 * D, t, fam) for t in
                            [now - k * 7 * D for k in range(1, 9)] if t - 7 * D >= first_ts]
                if hist and max(hist) > 0:
                    limit, source = max(hist), {"k": "history"}

        # ── pace and forecast
        if base == "session" and start is not None and now - start < 1800 and typical_rate:
            rate, rate_basis = typical_rate, "history"
        elif base == "session" and start is not None:
            span = min(H, max(now - start, 600))
            rate = S.cost(now - span, now + 1, fam) / span
            rate_basis = "last_hour"
            if rate == 0 and used > 0:
                rate, rate_basis = used / max(now - start, 600), "window_avg"
        elif start is not None:
            rate = used / max(now - start, H)
            rate_basis = "window_avg"
        else:
            rate, rate_basis = 0.0, "none"
        rate24 = S.cost(now - D, now + 1, fam) / D

        remaining = (limit - used) if limit else None
        eta = None
        if limit:
            if remaining <= 0:
                eta = now
            elif rate > 0:
                eta = now + remaining / rate
        hits_before_reset = eta is not None and (end is None or eta < end)
        overflow = rate * (end - eta) if (hits_before_reset and end) else None
        pct = used / limit * 100 if limit else None
        per_tok = usd_per_mtok(cur_model, mix) / 1e6

        status = "ok"
        if pct is not None and pct >= 100:
            status = "critical"
        elif hits_before_reset or (pct is not None and pct >= 80):
            status = "warning"

        # ── cumulative curve for the chart
        def make_curve(a, b):
            out, acc = [[a, 0]], 0.0
            pts = [e for e in S.slice(a, b) if fam is None or e.fam == fam]
            step = max(1, len(pts) // 400)
            for i, e in enumerate(pts):
                acc += e.cost
                if i % step == 0 or i == len(pts) - 1:
                    out.append([e.ts, round(acc, 4)])
            return out, acc

        curve = []
        if start is not None:
            curve, _ = make_curve(start, now + 1)
            curve.append([now, round(used, 4)])

        # no active 5-hour window (the last one has ended): keep the finished window for the chart
        prev = None
        if base == "session" and start is None and blocks:
            pb = blocks[-1]
            pc, pused = make_curve(pb["start"], pb["end"])
            prev = {"start": pb["start"], "end": pb["end"], "used_tok": pused * tpu,
                    "pct": pused / limit * 100 if limit else None,
                    "curve_tok": [[t, round(v * tpu)] for t, v in pc]}

        windows.append({
            "id": wid, "start": start, "end": end,
            "rolling": rolling, "used": used, "limit": limit, "limit_source": source,
            "pct": pct, "remaining": remaining, "rate_per_h": rate * H, "rate_basis": rate_basis,
            "rate24_per_h": rate24 * H, "eta": eta, "hits_before_reset": hits_before_reset,
            "overflow_usd": overflow, "status": status, "curve": curve,
            "remaining_tokens": remaining / per_tok if remaining and remaining > 0 else 0,
            "left_seconds": remaining / rate if remaining and remaining > 0 and rate > 0 else None,
            "hit_estimates": [[t, v] for t, v in est_hits[-10:]],
            "used_tok": used * tpu, "limit_tok": limit * tpu if limit else None,
            "rate_tok_per_h": rate * H * tpu, "overflow_tok": overflow * tpu if overflow else None,
            "curve_tok": [[t, round(v * tpu)] for t, v in curve], "prev": prev,
        })

    # ── history: per day (30 days)
    days = []
    d0 = local(now).replace(hour=0, minute=0, second=0, microsecond=0)
    for k in range(29, -1, -1):
        a = (d0 - timedelta(days=k)).timestamp()
        b = (d0 - timedelta(days=k - 1)).timestamp()
        row = {"t": a, "tokens": sum(e.tokens for e in S.slice(a, b))}
        for f in FAMILIES:
            row[f] = round(S.cost(a, b, f), 4)
        days.append(row)

    session_resets = [h["reset"] for h in hits if h["kind"] == "session" and h["reset"]]
    hist_blocks = [{"start": b["start"], "cost": round(b["cost"], 4), "tok": round(b["cost"] * tpu),
                    "tokens": b["tokens"], "n": b["n"],
                    "hit": any(abs(r - b["end"]) < 1800 for r in session_resets)}
                   for b in blocks[-40:]]

    # ── top-up (extra usage at API prices)
    models, seen_names = [], set()
    for mid in [cur_model] + TOPUP_MODELS:
        p = price(mid)
        if p[1] in seen_names:
            continue
        seen_names.add(p[1])
        models.append({"id": mid, "name": p[1], "usd_per_mtok": usd_per_mtok(mid, mix),
                       "usd_per_mtok_out": p[3], "current": mid == cur_model})

    plan = cfg.get("plan")
    month_used = next(w["used"] for w in windows if w["id"] == "month")
    return {
        "generated": now, "lang": LANG, "plan": plan, "plan_prices": PLAN_PRICES, "plan_price": PLAN_PRICES.get(plan) if plan else None,
        "current_model": cur_model, "current_model_name": price(cur_model)[1],
        "windows": windows, "days": days, "blocks": hist_blocks,
        "hits": [{"ts": h["ts"], "kind": h["kind"], "reset": h["reset"], "text": h["text"]} for h in hits[-20:]],
        "mix": mix, "mix_tokens": mix_tot, "topup_models": models,
        "month_api_equiv": month_used, "entries_total": len(entries), "tok_per_usd": tpu,
        "first_ts": entries[0].ts if entries else None, "last_ts": last.ts if last else None,
        "families": FAMILIES,
    }


# ───────────────────────── output ─────────────────────────

def source_text(src, now):
    if not src:
        return ""
    if src["k"] == "calibrated":
        return T("src_calibrated", when=fmt_time(src.get("at"), now))
    if src["k"] == "hits":
        return T("src_hits", n=src["n"])
    return T("src_" + src["k"])


def text_report(r):
    now = r["generated"]
    out = [T("t_model", model=r["current_model_name"], n=r["entries_total"])
           + (T("t_plan", plan=r["plan"]) if r["plan"] else T("t_no_plan"))]
    out += ["", T("t_header"), "|---|---|---|---|---|---|---|"]
    for w in r["windows"]:
        title = wtitle(w["id"])
        if w["id"] == "month":
            out.append(f"| {title} | {fmt_tok(w['used_tok'])} | — | — | {fmt_time(w['end'], now)} | {T('t_month_note')} "
                       f"| {fmt_tok(w['rate_tok_per_h'])}{T('per_h')} |")
            continue
        if w["id"] == "session" and w["start"] is None:
            note = T("t_no_session_prev", when=fmt_time(w["prev"]["end"], now)) if w.get("prev") else T("t_no_session")
            out.append(f"| {title} | 0 | {'≈ ' + fmt_tok(w['limit_tok']) if w['limit_tok'] else '—'} | 0% | — | {note} | — |")
            continue
        pct = f"{w['pct']:.0f}%" if w["pct"] is not None else "—"
        reset = T("t_rolling") if w["rolling"] else fmt_time(w["end"], now)
        if w["limit"] is None:
            eta = T("t_no_limit")
        elif w["pct"] >= 100:
            eta = T("t_exceeded")
        elif w["eta"] is None:
            eta = T("t_no_usage")
        elif w["hits_before_reset"]:
            eta = T("t_eta", when=fmt_time(w["eta"], now), dur=fmt_dur(w["eta"] - now))
        else:
            eta = T("t_safe")
        lim = "≈ " + fmt_tok(w["limit_tok"]) if w["limit_tok"] else "—"
        out.append(f"| {title} | {fmt_tok(w['used_tok'])} | {lim} | {pct} | {reset} | {eta} "
                   f"| {fmt_tok(w['rate_tok_per_h'])}{T('per_h')} |")
    out += ["", T("t_unit")]
    for w in r["windows"]:
        if w["limit"] is not None:
            extra = ""
            if w["left_seconds"]:
                extra = T("t_left_time", dur=fmt_dur(w["left_seconds"]))
            out.append(f"- {wtitle(w['id'])}: {source_text(w['limit_source'], now)}{extra}")
        if w["overflow_tok"]:
            out.append(T("t_overflow", tok=fmt_tok(w["overflow_tok"])))
    cur = next(m for m in r["topup_models"] if m["current"])
    out.append("\n" + T("t_value_hdr"))
    out.append(T("t_topup", model=cur["name"], a=fmt_tok(10 / cur["usd_per_mtok"] * 1e6),
                        b=fmt_tok(50 / cur["usd_per_mtok"] * 1e6)))
    if r["plan_price"]:
        out.append(T("t_plan_value", api=fmt_usd(r["month_api_equiv"]), plan=fmt_usd(r["plan_price"])))
    out.append(T("t_local_only"))
    return "\n".join(out)


def write_html(r):
    tpl = (HERE / "dashboard.html").read_text(encoding="utf-8")
    safe = lambda o: json.dumps(o, ensure_ascii=False).replace("</", "<\\/")
    html = tpl.replace("/*__DATA__*/null", safe(r)).replace("/*__I18N__*/null", safe(I18N))
    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(html, encoding="utf-8")
    return OUT_HTML


# ───────────────────────── commands ─────────────────────────

def cmd_report(a):
    r = build()
    if not r["entries_total"]:
        # no local Claude Code logs (claude.ai sandbox, a cloud session, a fresh machine): the caller
        # should switch to the numbers mode (`manual`)
        print(T("t_no_logs"))
        sys.exit(3)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return
    print(text_report(r))
    if not a.no_html:
        p = write_html(r)
        print("\n" + T("t_dashboard", path=p))
        if a.open:
            webbrowser.open(p.as_uri())


DUR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([^\d\s]*)")


def parse_duration(text):
    """'3h25m' / '2d 4h' / '205' (minutes) / '3 ч 25 мин' → seconds."""
    total, found = 0.0, False
    for num, unit in DUR_RE.findall(text or ""):
        u = unit.lower()
        v = float(num)
        if u.startswith(("d", "д")):
            total += v * D
        elif u.startswith(("h", "ч")):
            total += v * H
        else:
            total += v * 60
        found = True
    return total if found else None


def cmd_manual(a):
    """Numbers mode: no local logs needed. Each window is given as NAME PERCENT RESET, where RESET is
    in:<duration until reset> or at:<reset time as shown>. Works anywhere Python runs."""
    now = datetime.strptime(a.now.strip(), "%Y-%m-%d %H:%M").timestamp() if a.now else time.time()
    if not a.win:
        sys.exit(T("e_manual_none"))
    rows = []
    for name, pct_s, spec in a.win:
        pct = float(pct_s.strip().rstrip("%").replace(",", "."))
        kind, _, val = spec.partition(":")
        if kind == "in":
            dur = parse_duration(val)
            reset = now + dur if dur is not None else None
        elif kind == "at":
            reset = parse_reset(val, a.tz, now)
        else:
            dur = parse_duration(spec)
            reset = now + dur if dur is not None else parse_reset(spec, a.tz, now)
        if reset is None:
            sys.exit(T("e_manual_spec", v=repr(spec)))
        length = LENGTHS["session"] if name.startswith("session") else LENGTHS["week"]
        elapsed = now - (reset - length)
        row = {"id": name, "pct": pct, "reset": reset, "elapsed": elapsed, "length": length,
               "ok": 0 < reset - now <= length + 60}
        rate = pct / elapsed if elapsed > 0 else 0.0
        row["projected"] = pct + rate * (reset - now) if row["ok"] else None
        row["eta"] = now + (100 - pct) / rate if row["ok"] and rate > 0 and pct < 100 else None
        row["hits_before_reset"] = bool(row["eta"] and row["eta"] < reset)
        row["early"] = row["ok"] and elapsed < 0.1 * length
        rows.append(row)

    if a.json:
        print(json.dumps({"now": now, "windows": rows}, ensure_ascii=False, indent=1))
        return
    out = [T("m_title", now=fmt_time(now, now)), "", T("m_header"), "|---|---|---|---|---|---|"]
    for r in rows:
        key = "w_" + r["id"].replace(":", "_")
        title = T(key) if key in I18N["en"] else r["id"]
        if not r["ok"]:
            fc, proj = T("m_bad"), "—"
        else:
            proj = f"{r['projected']:.0f}%"
            if r["pct"] >= 100:
                fc = T("m_over")
            elif r["hits_before_reset"]:
                fc = T("m_hit", when=fmt_time(r["eta"], now), dur=fmt_dur(r["eta"] - now),
                       x=f"{r['projected'] - 100:.0f}")
            else:
                fc = T("m_safe", x=f"{r['projected']:.0f}")
            if r["early"]:
                fc += T("m_early")
        out.append(f"| {title} | {r['pct']:g}% | {fmt_time(r['reset'], now)} | {fmt_dur(r['elapsed'])} | {proj} | {fc} |")
    prices = ", ".join(f"{price(m)[1]} ${price(m)[2]:g} / ${price(m)[3]:g}" for m in TOPUP_MODELS)
    out += ["", T("m_note"), T("m_prices", list=prices)]
    print("\n".join(out))


def cmd_calibrate(a):
    now = time.time()
    wid = a.window
    base = wid.split(":")[0]
    if base not in LENGTHS:
        sys.exit(T("e_window"))
    if not 0 < a.percent <= 100:
        sys.exit(T("e_percent"))
    cfg = read_json(CONFIG, {})
    entries, hits = load_data()
    S = Series(entries)
    length = LENGTHS[base]
    reset = None
    if a.resets_in_min is not None:
        reset = now + a.resets_in_min * 60
    elif a.resets:
        reset = parse_reset(a.resets, a.tz, now)
        if reset is None:
            sys.exit(T("e_reset_parse", v=repr(a.resets)))
    if reset is None:
        prev = cfg.get("windows", {}).get(wid, {}).get("reset")
        if base == "session":
            blocks = session_blocks(entries, hits)
            if blocks and blocks[-1]["end"] > now:
                reset = blocks[-1]["end"]
        elif prev:
            reset = periodic_window(prev, length, now)[1]
    start = reset - length if reset else now - length
    used = S.cost(start, now + 1, window_family(wid))
    if used <= 0:
        sys.exit(T("e_no_usage"))
    limit = used / (a.percent / 100)
    cfg.setdefault("windows", {})[wid] = {"limit": round(limit, 4), "reset": reset,
                                          "percent": a.percent, "used_at_calibration": round(used, 4),
                                          "calibrated_at": now}
    write_json(CONFIG, cfg)
    print(T("c_result", window=wtitle(wid), used=fmt_usd(used), pct=f"{a.percent:g}", limit=fmt_usd(limit))
          + (T("c_reset", when=fmt_time(reset, now)) if reset else ""))


def cmd_set(a):
    global LANG
    cfg = read_json(CONFIG, {})
    if a.set_lang:
        v = norm_lang(a.set_lang)
        if not v:
            sys.exit(T("e_lang", v=a.set_lang, list=", ".join(LANGS)))
        cfg["lang"] = LANG = v
        print(T("set_lang", name=I18N[v]["_name"]))
    if a.plan:
        cfg["plan"] = a.plan
    for item in a.limit or []:
        wid, _, val = item.partition("=")
        w = cfg.setdefault("windows", {}).setdefault(wid, {})
        w["limit"], w["calibrated_at"] = float(val), time.time()
    for wid in a.clear or []:
        cfg.get("windows", {}).pop(wid, None)
    write_json(CONFIG, cfg)
    print(json.dumps(cfg, ensure_ascii=False, indent=2))


def migrate_old_data():
    """Settings and the parsed-log cache used to live in ~/.claude/quota — carry them over once."""
    if DATA_DIR.exists() or not OLD_DATA_DIR.is_dir() or os.environ.get("MYTOKENS_DIR"):
        return
    import shutil
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("config.json", "cache.json"):
        if (OLD_DATA_DIR / name).is_file():
            shutil.copy2(OLD_DATA_DIR / name, DATA_DIR / name)


def main():
    global LANG
    migrate_old_data()
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="MyTokens: Claude usage limits, forecast, top-up")
    ap.add_argument("--lang", help="output language for this run: " + ", ".join(LANGS))
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("report", help="summary + HTML dashboard")
    p.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    p.add_argument("--no-html", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_report)
    p = sub.add_parser("calibrate", help="derive a limit from the percentage shown in /usage")
    p.add_argument("--window", required=True, help="session | week | week:opus | week:sonnet")
    p.add_argument("--percent", type=float, required=True)
    p.add_argument("--resets", help="reset time as shown: '1:40pm', '14:00', 'Oct 3, 10am'")
    p.add_argument("--resets-in-min", type=float, help="minutes until reset")
    p.add_argument("--tz", help="time zone, e.g. Europe/Moscow (default: local)")
    p.set_defaults(fn=cmd_calibrate)
    p = sub.add_parser("manual", help="numbers mode: forecast from the Usage page, no logs needed")
    p.add_argument("--win", nargs=3, action="append", metavar=("NAME", "PERCENT", "RESET"),
                   help="e.g. --win session 37 in:3h25m  --win week 12 at:\"Oct 3, 10am\"")
    p.add_argument("--now", help="the user's current local time, 'YYYY-MM-DD HH:MM' (needed with at: in a sandbox)")
    p.add_argument("--tz", help="time zone of at: times, e.g. Europe/Moscow")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_manual)
    p = sub.add_parser("set", help="settings")
    p.add_argument("--set-lang", metavar="LANG", help="default language: " + ", ".join(LANGS))
    p.add_argument("--plan", choices=list(PLAN_PRICES))
    p.add_argument("--limit", action="append", help="WINDOW=USD, e.g. session=35")
    p.add_argument("--clear", action="append", help="clear a window's manual calibration")
    p.set_defaults(fn=cmd_set)
    a = ap.parse_args()
    if not a.cmd:
        a = ap.parse_args((["--lang", a.lang] if a.lang else []) + ["report"])
    LANG = resolve_lang(a.lang)
    a.fn(a)


if __name__ == "__main__":
    main()
