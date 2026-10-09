#!/usr/bin/env python3
"""ccquota - a usage status line for Claude Code.

Reads Claude Code's own local files and prints a single status line:
remaining quota windows, token burn, and how many sessions are live.

No network calls, no dependencies, stdlib only.
"""
from __future__ import annotations

import json
import glob
import math
import queue
import subprocess
import tempfile
import threading
import os
import sys
import time
from datetime import datetime

__version__ = "0.1.0"

# --- tunables (all overridable by environment variables) --------------------

RETENTION_DAYS = 8          # how much history the cache keeps
CACHE_VERSION = 5           # bump to invalidate the on-disk cache
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


# --- Codex quota -----------------------------------------------------------

CODEX_TIMEOUT = 8.0
CODEX_CACHE_SECONDS = 60


def codex_executable():
    """Find the native binary; never launch a shell/npm shim."""
    try:
        override = os.environ.get("CODEX_CLI_PATH")
        if override:
            return override if (override.lower().endswith(".exe")
                                and os.path.isfile(override)) else None
        root = os.environ.get("LOCALAPPDATA")
        if not root:
            return None
        paths = glob.glob(os.path.join(root, "OpenAI", "Codex", "bin", "*", "codex.exe"))
        paths = [p for p in paths if os.path.isfile(p)]
        return max(paths, key=os.path.getmtime) if paths else None
    except OSError:
        return None


def parse_codex_limits(result, now, source="app-server"):
    """Normalize only quota fields; discard all account and credit IDs."""
    try:
        by_id = result.get("rateLimitsByLimitId") or {}
        limits = by_id.get("codex")
        if limits is None:
            limits = result.get("rateLimits")
        if not isinstance(limits, dict):
            return None
        out = {"source": source, "stale": source == "logs", "five_hour": None,
               "weekly": None, "reset_credits": None, "at": int(now)}
        snake = source == "logs"
        duration_key = "window_minutes" if snake else "windowDurationMins"
        percent_key = "used_percent" if snake else "usedPercent"
        reset_key = "resets_at" if snake else "resetsAt"
        for slot, fallback in (("primary", "five_hour"), ("secondary", "weekly")):
            window = limits.get(slot)
            if window is None:
                continue
            duration = window.get(duration_key)
            key = {300: "five_hour", 10080: "weekly"}.get(duration)
            if duration is None:
                key = fallback
            if key is None:
                continue
            percent = window[percent_key]
            resets = window[reset_key]
            if (isinstance(percent, bool) or not isinstance(percent, (int, float))
                    or not math.isfinite(percent) or not 0 <= percent <= 100
                    or isinstance(resets, bool) or not isinstance(resets, (int, float))
                    or not math.isfinite(resets) or resets <= 0):
                return None
            if source == "logs" and resets <= now:
                continue
            out[key] = {"percent": int(round(percent)), "resets_at": int(resets)}
        credits = (result.get("rateLimitResetCredits") or {}).get("availableCount")
        if isinstance(credits, int) and not isinstance(credits, bool) and credits >= 0:
            out["reset_credits"] = credits
        return out
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return None


def codex_app_server(now):
    """Read one live reply with a bounded lifetime, including initialization."""
    proc = None
    reader = None
    try:
        executable = codex_executable()
        if not executable:
            return None
        deadline = time.monotonic() + CODEX_TIMEOUT
        proc = subprocess.Popen(
            [executable, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        replies = queue.Queue()

        def read_lines():
            try:
                for raw in proc.stdout:
                    try:
                        replies.put(json.loads(raw))
                    except ValueError:      # a stray log line is not a reply
                        continue
            except Exception:
                pass
            finally:
                replies.put(None)

        reader = threading.Thread(target=read_lines, daemon=True)
        reader.start()

        def send(message):
            proc.stdin.write(json.dumps(message) + "\n")
            proc.stdin.flush()

        def receive(request_id):
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                reply = replies.get(timeout=remaining)
                if reply is None:
                    return None
                if not isinstance(reply, dict):
                    return None
                if reply.get("id") == request_id:
                    if "error" in reply or not isinstance(reply.get("result"), dict):
                        return None
                    return reply["result"]

        send({"id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "ccquota", "version": __version__},
            "capabilities": {"experimentalApi": True}}})
        if receive(1) is None:
            return None
        send({"method": "initialized"})
        send({"id": 2, "method": "account/rateLimits/read"})
        return parse_codex_limits(receive(2), now)
    except Exception:
        return None
    finally:
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait()
            except OSError:
                pass
            if reader is not None:
                reader.join(timeout=1)
            for stream in (proc.stdin, proc.stdout):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass


def codex_log_limits(now):
    """Scan bounded tails of the eight newest logs, not entire transcripts."""
    try:
        home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
        paths = glob.glob(os.path.join(home, "sessions", "**", "rollout-*.jsonl"),
                          recursive=True)
        paths.sort(key=os.path.getmtime, reverse=True)
        newest = None
        newest_key = None
        for path in paths[:8]:
            try:
                with open(path, "rb") as fh:
                    size = fh.seek(0, os.SEEK_END)
                    offset = max(0, size - 256 * 1024)
                    fh.seek(offset)
                    if offset:
                        fh.readline()
                    lines = fh.read().splitlines()
                for index, raw in enumerate(lines):
                    if b'"rate_limits"' not in raw:
                        continue
                    try:
                        record = json.loads(raw)
                        limits = (record.get("payload") or {}).get("rate_limits")
                        if limits is None:
                            limits = record.get("rate_limits")
                        if not isinstance(limits, dict) or limits.get("limit_id") != "codex":
                            continue
                        parsed = parse_codex_limits({"rateLimits": limits}, now, "logs")
                        if parsed is None:
                            continue
                        stamp = parse_iso(record.get("timestamp"))
                        key = (stamp if stamp is not None else os.path.getmtime(path),
                               os.path.getmtime(path), index)
                        if newest_key is None or key > newest_key:
                            newest, newest_key = parsed, key
                    except (ValueError, TypeError, AttributeError):
                        continue
            except OSError:
                continue
        return newest
    except Exception:
        return None


def codex_limits(now):
    """Share a one-minute cache, including failures, across all consumers."""
    tmp = None
    lock = None
    try:
        path = os.path.join(os.path.dirname(cache_path()), "codex.json")
        cached = read_json(path)
        if (isinstance(cached, dict) and "codex" in cached
                and 0 <= now - cached.get("at", 0) < CODEX_CACHE_SECONDS):
            value = cached["codex"]
            # A log window can expire even during the cache's short lifetime.
            if isinstance(value, dict) and value.get("source") == "logs":
                for key in ("five_hour", "weekly"):
                    if value.get(key) and value[key]["resets_at"] <= now:
                        value[key] = None
            return value
        # Multiple panels/mods can miss the cache together. Only one may
        # probe; the next refresh of other consumers will see its cache.
        lock_path = path + ".lock"
        try:
            if time.time() - os.path.getmtime(lock_path) > CODEX_TIMEOUT + 2:
                os.unlink(lock_path)
        except FileNotFoundError:
            pass
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return None
        lock = lock_path
        os.close(fd)
        # Another process may have completed between our read and lock.
        cached = read_json(path)
        if (isinstance(cached, dict) and "codex" in cached
                and 0 <= now - cached.get("at", 0) < CODEX_CACHE_SECONDS):
            return cached["codex"]
        value = codex_app_server(now)
        if value is None:
            value = codex_log_limits(now)
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                             dir=os.path.dirname(path), delete=False) as fh:
                tmp = fh.name
                json.dump({"at": now, "codex": value}, fh)
            os.replace(tmp, path)
            tmp = None
        except OSError:
            pass
        return value
    except Exception:
        return None
    finally:
        if lock is not None:
            try:
                os.unlink(lock)
            except OSError:
                pass
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


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
            # Model and attribution ride along so the panel can break usage
            # down without a second sweep. Claude Code tags each response with
            # the skill, agent or plugin that caused it.
            val = [int(epoch // 60), bill(usage),
                   usage.get("cache_read_input_tokens", 0),
                   msg.get("model") or "",
                   d.get("attributionSkill") or "",
                   d.get("attributionAgent") or d.get("attributionPlugin") or ""]
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
    for v in entries.values():
        if a <= v[0] < b:
            tok += v[1]
            cr += v[2]
            n += 1
    return tok, cr, n


def window_breakdown(entries, start, end, index):
    """Sum billable tokens in a window, grouped by one of the tag columns.

    index 3 is the model, 4 the skill, 5 the agent or plugin.
    """
    a, b = int(start // 60), int(end // 60)
    out = {}
    for v in entries.values():
        if not (a <= v[0] < b) or len(v) <= index:
            continue
        label = v[index]
        if not label:
            continue
        out[label] = out.get(label, 0) + v[1]
    return sorted(out.items(), key=lambda kv: -kv[1])


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


def snapshot_path():
    return os.path.join(os.path.dirname(cache_path()), "live.json")


def save_snapshot(data):
    """Keep the host's live figures where a separate process can read them.

    Only the status line is handed a payload; anything else - the panel, a
    script - has no way to ask for one. Writing the interesting parts down on
    each invocation gives them a view that is as fresh as the last refresh.
    """
    if not data.get("rate_limits"):
        return
    snap = {"at": int(time.time())}
    for key in ("rate_limits", "context_window", "prompt_cache", "model", "version"):
        if key in data:
            snap[key] = data[key]
    try:
        tmp = snapshot_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(snap, fh, separators=(",", ":"))
        os.replace(tmp, snapshot_path())
    except OSError:
        pass


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

        elif seg == "cx":
            cx = codex_limits(now)
            if cx is not None:
                values = []
                for key, label in (("five_hour", "5h"), ("weekly", "wk")):
                    window = cx.get(key)
                    body = (paint(level(window["percent"] / 100.0),
                                  "{}%".format(window["percent"])) if window
                            else paint(DIM, "?"))
                    values.append(label + " " + body)
                marker = paint(DIM, "~") if cx.get("stale") else ""
                parts.append("cx " + marker + " ".join(values))

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


def local_figures():
    """The figures only this machine's files can answer, as plain data.

    The mod reads quota, context and cost from the engine itself; what it
    cannot get there - today's tokens across every session, and how many
    Claude Code processes are running - comes from here.
    """
    now = time.time()
    midnight = datetime.now().replace(hour=0, minute=0, second=0,
                                      microsecond=0).timestamp()
    tok, cache_read, count = window_sum(collect(now), midnight, now)
    return {"today_tokens": tok, "today_cache_read": cache_read,
            "today_requests": count, "sessions": len(live_sessions(now)),
            "codex": codex_limits(now)}


def main():
    if "--codex" in sys.argv:
        print(json.dumps(codex_limits(time.time())))
        return 0
    if "--local" in sys.argv:
        print(json.dumps(local_figures()))
        return 0
    data = {}
    if "--test" not in sys.argv and not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
            if raw.strip():
                data = json.loads(raw)
        except (ValueError, OSError):
            data = {}
    save_snapshot(data)
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
