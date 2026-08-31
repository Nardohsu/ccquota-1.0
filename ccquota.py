#!/usr/bin/env python3
"""ccquota - a usage status line for Claude Code.

Reads Claude Code's own local files and prints a single status line:
remaining quota windows, token burn, and how many sessions are live.

No network calls, no dependencies, stdlib only.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime

__version__ = "0.1.0"

# --- tunables (all overridable by environment variables) --------------------

RETENTION_DAYS = 8          # how much history the cache keeps
CACHE_VERSION = 4           # bump to invalidate the on-disk cache
FIVE_HOUR = 5 * 3600
WEEK = 7 * 86400
DEFAULT_SEGMENTS = "ctx,5h,wk,today,agents"

# The real context limit varies by model and by --autocompact, and is not
# recorded anywhere on disk. Rather than divide by a guess, `ctx` reports raw
# tokens and only becomes a percentage once you supply the denominator.
CTX_LIMIT = int(os.environ.get("CCQUOTA_CTX_LIMIT") or 0)
SESSION_MAX_AGE = int(os.environ.get("CCQUOTA_SESSION_MAX_AGE", "86400"))
SEP = os.environ.get("CCQUOTA_SEP", "  ")

# --- ansi -------------------------------------------------------------------

_COLOR = os.environ.get("NO_COLOR") is None and os.environ.get("CCQUOTA_COLOR") != "0"

DIM, RED, YEL, GRN, CYA = "2", "31", "33", "32", "36"


def paint(code, text):
    return "\033[" + code + "m" + text + "\033[0m" if _COLOR else text


def level(frac):
    """Colour by how much of a budget is consumed."""
    if frac >= 0.90:
        return RED
    if frac >= 0.70:
        return YEL
    return GRN


# --- paths ------------------------------------------------------------------


def config_dir():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


def claude_json_path():
    """Global config file, which holds the cached quota figures.

    More than one file can be called .claude.json: Claude Code keeps a small
    bootstrap file inside the config directory and the real 70 KB config in
    $HOME, and which is which has changed between versions. Pick by content
    rather than by position, or the decoy wins and the quota data disappears.

    When CLAUDE_CONFIG_DIR is set, $HOME is not consulted at all: it would
    report quota for a different profile than the transcripts being counted.
    """
    candidates = [os.path.join(config_dir(), ".claude.json"),
                  os.path.join(config_dir(), "config.json")]
    if not os.environ.get("CLAUDE_CONFIG_DIR"):
        candidates.append(os.path.expanduser("~/.claude.json"))
    existing = [p for p in candidates if os.path.exists(p)]
    for p in existing:
        if "cachedUsageUtilization" in read_json(p):
            return p
    return existing[0] if existing else candidates[-1]


def cache_path():
    d = os.path.join(config_dir(), "ccquota")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "cache.json")


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {} if default is None else default


# --- official quota anchor --------------------------------------------------

# Claude Code caches the /usage response in the global config. It is
# authoritative, but only refreshes when Claude Code itself fetches it, so it
# is often badly stale.
PERIOD_BY_GROUP = {"session": FIVE_HOUR, "weekly": WEEK}


def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def official_limits(now):
    """Parse cachedUsageUtilization into {kind: {...}}.

    A percent only describes the window it was measured in. Once resets_at has
    passed, that window is dead and the number must not be shown as current.
    """
    cu = read_json(claude_json_path()).get("cachedUsageUtilization") or {}
    fetched = (cu.get("fetchedAtMs") or 0) / 1000.0
    out = {}
    for lim in ((cu.get("utilization") or {}).get("limits") or []):
        resets = parse_iso(lim.get("resets_at"))
        out[lim.get("kind")] = {
            "percent": lim.get("percent"),
            "resets_at": resets,
            "period": PERIOD_BY_GROUP.get(lim.get("group")),
            "fetched": fetched,
            "fresh": bool(resets and now < resets),
        }
    return out


def roll_forward(resets_at, period, now):
    """Project a stale reset time onto the current window.

    Only valid for fixed-cadence windows such as the weekly reset. The 5h
    window is anchored to your first message rather than to a fixed grid, so
    it is derived from the transcripts instead - see current_block().
    """
    if not resets_at or not period:
        return None
    end = resets_at
    if end <= now:
        end += ((now - end) // period + 1) * period
    return end - period, end


# --- transcript scanning ----------------------------------------------------


def bill(u):
    """Tokens that count against the plan.

    Cache reads are excluded: they run an order of magnitude larger than
    everything else and would swamp the figure.
    """
    return (u.get("input_tokens", 0) + u.get("output_tokens", 0)
            + u.get("cache_creation_input_tokens", 0))


def transcript_files(horizon):
    root = os.path.join(config_dir(), "projects")
    out = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            if not fn.endswith(".jsonl"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            if st.st_mtime >= horizon:
                out.append((p, st.st_size, st.st_mtime))
    return out


def parse_from(path, start, entries):
    """Parse new bytes only, returning the offset of the last complete line.

    Transcripts are append-only, so a byte offset is a safe resume point.

    One API response is often written as several lines that share a message id
    and repeat the same usage object; counting them all overstates usage by
    more than 100%. Keyed upserts collapse them, and keeping the largest value
    picks up the final figure when a streamed response grew while being written.
    """
    consumed = start
    try:
        fh = open(path, "rb")
    except OSError:
        return start
    with fh:
        fh.seek(start)
        for raw in fh:
            if not raw.endswith(b"\n"):
                break                      # torn tail, pick it up next run
            consumed += len(raw)
            if b'"usage"' not in raw:
                continue
            try:
                d = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            if d.get("type") != "assistant":
                continue
            msg = d.get("message") or {}
            usage = msg.get("usage")
            epoch = parse_iso(d.get("timestamp"))
            if not usage or epoch is None:
                continue
            key = str(msg.get("id")) + "|" + str(d.get("requestId"))
            val = [int(epoch // 60), bill(usage),
                   usage.get("cache_read_input_tokens", 0)]
            prev = entries.get(key)
            if prev is None or val[1] > prev[1]:
                entries[key] = val
    return consumed


def collect(now):
    """Return {key: [minute, billable, cache_read]} deduped across all files."""
    horizon = now - RETENTION_DAYS * 86400
    cutoff_min = int(horizon // 60)
    cache = read_json(cache_path())
    if cache.get("v") != CACHE_VERSION:
        cache = {"v": CACHE_VERSION, "files": {}}
    files = cache.setdefault("files", {})

    seen = set()
    for path, size, mtime in transcript_files(horizon):
        seen.add(path)
        rec = files.get(path)
        if rec and rec.get("size") == size and rec.get("mtime") == mtime:
            continue                       # untouched since the last run
        if rec and rec.get("size", 0) < size:
            start, entries = rec["size"], rec.get("e", {})
        else:
            # New file, truncated, or rewritten in place to the same length -
            # size alone cannot tell the last case from an untouched file.
            start, entries = 0, {}
        offset = parse_from(path, start, entries)
        entries = {k: v for k, v in entries.items() if v[0] >= cutoff_min}
        files[path] = {"size": offset, "mtime": mtime, "e": entries}

    for gone in [p for p in files if p not in seen]:
        del files[gone]

    try:
        tmp = cache_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, separators=(",", ":"))
        os.replace(tmp, cache_path())
    except OSError:
        pass                               # a read-only cache still works

    merged = {}
    for rec in files.values():
        for k, v in rec.get("e", {}).items():
            prev = merged.get(k)
            if prev is None or v[1] > prev[1]:
                merged[k] = v
    return merged


def window_sum(entries, start, end):
    a, b = int(start // 60), int(end // 60)
    tok = cr = n = 0
    for minute, billable, cache_read in entries.values():
        if a <= minute < b:
            tok += billable
            cr += cache_read
            n += 1
    return tok, cr, n


def current_block(entries, now):
    """Derive the live 5h window from activity.

    The window opens on the first message sent after the previous one lapsed,
    and runs five hours from exactly that moment. It is not aligned to a clock
    grid: the reset times Claude Code reports carry minutes and sub-seconds
    (22:40:00.448), so the start is not rounded to the hour.
    """
    mins = sorted({v[0] for v in entries.values()})
    if not mins:
        return None
    start = None
    last = None
    for m in mins:
        t = m * 60
        if start is None or t - start >= FIVE_HOUR or t - last >= FIVE_HOUR:
            start = t
        last = t
    return (start, start + FIVE_HOUR) if now < start + FIVE_HOUR else None


# --- live sessions ----------------------------------------------------------


def pid_alive(pid):
    if not pid:
        return False
    if os.name == "nt":
        try:
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def live_sessions(now):
    """Sessions whose process is still running.

    The heartbeat written into these files is far too sparse to use as a
    liveness signal, and they are not removed when a process is killed, so ask
    the OS instead. The age cap limits the damage from PID reuse.
    """
    out = []
    d = os.path.join(config_dir(), "sessions")
    try:
        names = os.listdir(d)
    except OSError:
        return out
    for fn in names:
        if not fn.endswith(".json"):
            continue
        p = os.path.join(d, fn)
        try:
            if now - os.path.getmtime(p) > SESSION_MAX_AGE:
                continue
        except OSError:
            continue
        s = read_json(p)
        if pid_alive(s.get("pid") or 0):
            out.append(s)
    return out


# --- current context --------------------------------------------------------


def context_tokens(transcript_path):
    """Context size of this session = the input side of the last response."""
    if not transcript_path or not os.path.exists(transcript_path):
        return None
    try:
        with open(transcript_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 262144))
            tail = fh.read().split(b"\n")
    except OSError:
        return None
    for raw in reversed(tail):
        if b'"usage"' not in raw:
            continue
        try:
            d = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        if d.get("type") != "assistant":
            continue
        u = (d.get("message") or {}).get("usage")
        if not u:
            continue
        return (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                + u.get("cache_creation_input_tokens", 0))
    return None


# --- formatting -------------------------------------------------------------


def payload_limit(data, key):
    """A rate limit as reported by the host on this invocation.

    Claude Code passes `rate_limits` on stdin with a live `used_percentage`
    and `resets_at`. This is the authority: it is current at the moment the
    line is drawn, unlike the on-disk cache, which only refreshes at startup.
    Everything below it in the fallback chain exists for hosts too old to send
    it.
    """
    lim = ((data.get("rate_limits") or {}).get(key)) or {}
    pct = lim.get("used_percentage")
    if pct is None:
        return None
    return {"percent": pct, "resets_at": lim.get("resets_at")}


def human(n):
    if n >= 1000000:
        return "{:.1f}M".format(n / 1000000.0)
    if n >= 1000:
        return "{:.0f}k".format(n / 1000.0)
    return str(n)


def dur(seconds):
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    if days:
        return "{}d{}h".format(days, hours)
    if hours:
        return "{}h{:02d}m".format(hours, mins)
    return "{}m".format(mins)


def build(data):
    now = time.time()
    segs = os.environ.get("CCQUOTA_SEGMENTS", DEFAULT_SEGMENTS).split(",")
    parts = []

    # Both of these are expensive - a transcript sweep and a 70 KB JSON parse -
    # and neither is needed when the host sends live figures, so pay for them
    # only if a segment actually falls through to them.
    memo = {}

    def entries():
        if "entries" not in memo:
            memo["entries"] = collect(now)
        return memo["entries"]

    def official():
        if "official" not in memo:
            memo["official"] = official_limits(now)
        return memo["official"]

    for seg in [s.strip() for s in segs if s.strip()]:
        if seg == "dir":
            cwd = (data.get("workspace") or {}).get("current_dir") or data.get("cwd") or ""
            if cwd:
                parts.append(paint(CYA, os.path.basename(cwd.rstrip("/\\"))))

        elif seg == "model":
            name = (data.get("model") or {}).get("display_name")
            if name:
                parts.append(paint(DIM, name))

        elif seg == "ctx":
            window = data.get("context_window") or {}
            pct = window.get("used_percentage")
            if pct is not None:
                # The host knows the real window size, which varies by model
                # and by --autocompact and is recorded nowhere on disk.
                parts.append("ctx " + paint(level(pct / 100.0), "{:.0f}%".format(pct)))
            else:
                ctx = context_tokens(data.get("transcript_path") or "")
                if ctx:
                    if CTX_LIMIT > 0:
                        frac = ctx / float(CTX_LIMIT)
                        body = paint(level(frac), "{:.0f}%".format(frac * 100))
                    else:
                        body = human(ctx)
                    parts.append("ctx " + body)

        elif seg == "5h":
            live = payload_limit(data, "five_hour")
            o = official().get("session")
            if live:
                body = "{}%".format(live["percent"])
                frac = live["percent"] / 100.0
                left = live["resets_at"] - now if live["resets_at"] else None
            elif o and o["fresh"] and o["percent"] is not None:
                body = "{}%".format(o["percent"])
                frac = o["percent"] / 100.0
                left = o["resets_at"] - now
            else:
                win = current_block(entries(), now)
                if not win:
                    parts.append(paint(DIM, "5h idle"))
                    continue
                body = paint(DIM, "~") + human(window_sum(entries(), win[0], win[1])[0])
                frac = (now - win[0]) / float(FIVE_HOUR)
                left = win[1] - now
            tail = paint(DIM, " " + dur(left)) if left is not None else ""
            parts.append("5h " + paint(level(frac), body) + tail)

        elif seg == "wk":
            live = payload_limit(data, "seven_day")
            o = official().get("weekly_all") or {}
            if live:
                body = "{}%".format(live["percent"])
                frac = live["percent"] / 100.0
                left = live["resets_at"] - now if live["resets_at"] else None
            elif o.get("fresh") and o.get("percent") is not None:
                body = "{}%".format(o["percent"])
                frac = o["percent"] / 100.0
                left = o["resets_at"] - now
            else:
                win = roll_forward(o.get("resets_at"), WEEK, now)
                start = win[0] if win else now - WEEK
                frac = ((now - start) / float(WEEK)) if win else 0.0
                body = paint(DIM, "~") + human(window_sum(entries(), start, now)[0])
                left = win[1] - now if win else None
            tail = paint(DIM, " " + dur(left)) if left is not None else ""
            parts.append("wk " + paint(level(frac), body) + tail)

        elif seg == "cache":
            pc = data.get("prompt_cache") or {}
            ratio = pc.get("hit_ratio")
            if ratio is not None:
                # A cold cache means the next turn re-reads the whole
                # conversation, so warmth is worth seeing before a long task.
                mark = "" if pc.get("warm") else paint(YEL, "*")
                parts.append(paint(DIM, "cache ")
                             + "{:.0f}%".format(ratio * 100) + mark)

        elif seg == "today":
            midnight = datetime.now().replace(hour=0, minute=0, second=0,
                                              microsecond=0).timestamp()
            parts.append(paint(DIM, "today ")
                         + human(window_sum(entries(), midnight, now)[0]))

        elif seg == "agents":
            n = len(live_sessions(now))
            if n:
                parts.append(paint(GRN, "●") + str(n))

        elif seg == "cost":
            usd = (data.get("cost") or {}).get("total_cost_usd")
            if usd:
                parts.append(paint(DIM, "${:.2f}".format(usd)))

    return SEP.join(parts)


def main():
    data = {}
    if "--test" not in sys.argv and not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
            if raw.strip():
                data = json.loads(raw)
        except (ValueError, OSError):
            data = {}
    try:
        print(build(data))
    except Exception as exc:                # never break the host status line
        if os.environ.get("CCQUOTA_DEBUG"):
            raise
        print(paint(DIM, "ccquota: " + type(exc).__name__))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
