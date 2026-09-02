#!/usr/bin/env python3
"""ccquota panel - a readable desktop view of the same numbers.

The status line is one dense line; this is the same data laid out for a human,
in a small always-on-top window. Run it directly:

    python ccquota_panel.py

Only the status line is handed live figures by Claude Code, so this reads the
snapshot the status line leaves behind. Keep a Claude Code CLI session running
with ccquota installed and the numbers stay current; without one, it falls back
to the cached figures on disk and says so.
"""
from __future__ import annotations

import os
import sys
import time
import tkinter as tk
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ccquota as q

REFRESH_MS = 30000
SNAPSHOT_MAX_AGE = 900     # 超過就不再顯示 session 層級的數字
WIDTH = 430

BG = "#16181d"
CARD = "#1e2127"
LINE = "#2b3038"
FG = "#e6e8eb"
MUTE = "#8b929c"
DIM = "#5d646e"
GRN = "#4ea86b"
YEL = "#c9a227"
RED = "#d1554f"
BLUE = "#4a7fd4"

F = "Segoe UI" if sys.platform == "win32" else "Helvetica"
MONO = "Consolas" if sys.platform == "win32" else "Menlo"


def tone(pct):
    if pct is None:
        return DIM
    if pct >= 90:
        return RED
    if pct >= 70:
        return YEL
    return GRN


def ago(seconds):
    seconds = int(max(0, seconds))
    if seconds < 90:
        return "{} 秒前".format(seconds)
    if seconds < 5400:
        return "{} 分鐘前".format(seconds // 60)
    return "{} 小時前".format(seconds // 3600)


def resolve_limit(snap, official, key, kind, now, entries=None):
    """Pick the freshest usable figure for one limit.

    A percentage only describes the window it was measured in. Once resets_at
    has passed, that window is gone and the number is not merely old, it is
    about something else - showing it would be worse than showing nothing. So
    each limit is judged on its own, in order of authority:

      1. the snapshot the status line leaves behind - live and official
      2. the figures cached on disk, while their window is still open
      3. tokens counted from the transcripts - no percentage, but it moves

    Step 3 matters because only the status line is ever handed a percentage.
    With no session running one, the first two go quiet within hours and the
    panel would have nothing to show at all; a token count is not the official
    figure, but it is current and it answers a refresh.
    """
    lim = ((snap.get("rate_limits") or {}).get(key)) or {}
    resets = lim.get("resets_at")
    if lim.get("used_percentage") is not None and resets and resets > now:
        return {"pct": lim["used_percentage"], "tokens": None, "resets": resets,
                "source": "狀態列快照", "age": now - snap.get("at", 0)}

    o = official.get(kind) or {}
    if o.get("fresh") and o.get("percent") is not None:
        return {"pct": o["percent"], "tokens": None, "resets": o["resets_at"],
                "source": "磁碟快取", "age": now - (o.get("fetched") or now)}

    win = q.roll_forward(o.get("resets_at"), o.get("period"), now)
    if kind == "session":
        # The 5h window is anchored to your first message rather than a clock
        # grid, so it comes from activity, not from projecting a reset time.
        win = q.current_block(entries, now) if entries else None
    elif win is None and entries:
        # No weekly anchor has ever been written, so there is no cadence to
        # follow. A rolling seven days is not the quota window, but it is the
        # same fallback the status line uses and it is labelled an estimate.
        win = (now - q.WEEK, None)

    if entries is not None and win:
        return {"pct": None, "tokens": q.window_sum(entries, win[0], now)[0],
                "resets": win[1], "source": "本機估算", "age": 0}

    return {"pct": None, "tokens": None, "resets": win[1] if win else None,
            "source": None, "age": None}


def gather():
    """Everything the panel shows, with the source of each figure recorded."""
    now = time.time()
    snap = q.read_json(q.snapshot_path())
    official = q.official_limits(now)
    out = {"now": now}

    entries = q.collect(now)
    out["five"] = resolve_limit(snap, official, "five_hour", "session", now, entries)
    out["seven"] = resolve_limit(snap, official, "seven_day", "weekly_all", now,
                                 entries)

    # Session-level figures describe whichever session last wrote the snapshot.
    # Once it is old that session is probably gone, so they stop being shown
    # rather than being attributed to the session you are looking at now.
    age = now - snap.get("at", 0) if snap.get("at") else None
    out["snapshot_age"] = age
    if age is not None and age <= SNAPSHOT_MAX_AGE:
        cw = snap.get("context_window") or {}
        out["ctx"] = cw.get("used_percentage")
        out["ctx_size"] = cw.get("context_window_size")
        out["cache"] = (snap.get("prompt_cache") or {}).get("hit_ratio")
        out["model"] = (snap.get("model") or {}).get("display_name")
    else:
        out["ctx"] = out["ctx_size"] = out["cache"] = out["model"] = None
    out["source"] = out["five"]["source"] or out["seven"]["source"]

    midnight = datetime.now().replace(hour=0, minute=0, second=0,
                                      microsecond=0).timestamp()
    tok, cread, count = q.window_sum(entries, midnight, now)
    out["today_tokens"] = tok
    out["today_cache_read"] = cread
    out["today_requests"] = count
    out["by_model"] = q.window_breakdown(entries, midnight, now, 3)[:4]

    day = now - 86400
    skills = q.window_breakdown(entries, day, now, 4)
    agents = q.window_breakdown(entries, day, now, 5)
    rows = [(n, "Skill", v) for n, v in skills] + [(n, "Agent", v) for n, v in agents]
    rows.sort(key=lambda r: -r[2])
    out["attribution"] = rows[:5]
    out["attribution_total"] = q.window_sum(entries, day, now)[0]

    out["sessions"] = q.live_sessions(now)
    return out


class Panel(tk.Tk):
    def __init__(self):
        tk.Tk.__init__(self)
        self.title("ccquota")
        self.configure(bg=BG)
        self.geometry("{}x830".format(WIDTH))
        self.minsize(WIDTH, 420)
        self.attributes("-topmost", True)
        self.body = None
        self.build()
        self.after(REFRESH_MS, self.tick)

    # --- drawing helpers ---------------------------------------------------

    def card(self, parent, pad=(14, 12)):
        f = tk.Frame(parent, bg=CARD)
        f.pack(fill="x", padx=12, pady=(0, 8), ipadx=pad[0], ipady=pad[1])
        return f

    def row(self, parent, left, right, lc=FG, rc=FG, lf=10, rf=10, bold=False):
        f = tk.Frame(parent, bg=parent["bg"])
        f.pack(fill="x", padx=14)
        tk.Label(f, text=left, bg=parent["bg"], fg=lc,
                 font=(F, lf, "bold" if bold else "normal")).pack(side="left")
        tk.Label(f, text=right, bg=parent["bg"], fg=rc,
                 font=(F, rf, "bold" if bold else "normal")).pack(side="right")
        return f

    def bar(self, parent, pct, colour):
        c = tk.Canvas(parent, height=7, bg=parent["bg"], highlightthickness=0)
        c.pack(fill="x", padx=14, pady=(6, 0))

        def draw(_event=None):
            c.delete("all")
            w = c.winfo_width() or WIDTH - 52
            c.create_rectangle(0, 0, w, 7, fill=LINE, outline="")
            if pct:
                c.create_rectangle(0, 0, max(3, w * min(pct, 100) / 100.0), 7,
                                   fill=colour, outline="")
        c.bind("<Configure>", draw)
        draw()

    def heading(self, parent, text):
        tk.Label(parent, text=text, bg=parent["bg"], fg=MUTE,
                 font=(F, 9)).pack(anchor="w", padx=14, pady=(0, 6))

    def limit_block(self, parent, title, data, reset_fmt):
        pct = data.get("pct")
        resets = data.get("resets")
        when = "—"
        if resets:
            left = resets - time.time()
            when = "{} 重置 · {}".format(
                datetime.fromtimestamp(resets).strftime(reset_fmt),
                q.dur(left) if left > 0 else "已重置")
        if data.get("source") == "本機估算":
            when += "   ·   本機估算 token，非官方百分比"
        elif data.get("age") is not None:
            when += "   ·   {} {}".format(data["source"], ago(data["age"]))
        else:
            when += "   ·   沒有可用的數字"
        if pct is not None:
            value, colour = "{}%".format(pct), tone(pct)
        elif data.get("tokens") is not None:
            value, colour = "~{:,}".format(data["tokens"]), MUTE
        else:
            value, colour = "—", DIM
        self.row(parent, title, value, lc=FG, rc=colour, lf=11, rf=13, bold=True)
        self.row(parent, when, "", lc=DIM, lf=8)
        self.bar(parent, pct, tone(pct))

    # --- content -----------------------------------------------------------

    def build(self):
        if self.body:
            self.body.destroy()
        self.body = tk.Frame(self, bg=BG)
        self.body.pack(fill="both", expand=True, pady=(10, 10))
        d = gather()

        head = tk.Frame(self.body, bg=BG)
        head.pack(fill="x", padx=26, pady=(0, 10))
        tk.Label(head, text="用量", bg=BG, fg=FG,
                 font=(F, 13, "bold")).pack(side="left")
        tk.Button(head, text="更新", command=self.request_rebuild, bg=CARD,
                  fg=MUTE, activebackground=LINE, activeforeground=FG,
                  relief="flat", font=(F, 8), padx=10,
                  cursor="hand2").pack(side="right")

        limits = self.card(self.body)
        self.limit_block(limits, "5 小時限制", d["five"], "%H:%M")
        tk.Frame(limits, bg=LINE, height=1).pack(fill="x", padx=14, pady=12)
        self.limit_block(limits, "每週 · 全模型", d["seven"], "%m/%d %H:%M")

        session = self.card(self.body)
        self.heading(session, "本次 session")
        ctx = d.get("ctx")
        size = d.get("ctx_size")
        self.row(session, "Context",
                 "{}%".format(ctx) if ctx is not None else "—",
                 lc=MUTE, rc=tone(ctx))
        if size:
            self.row(session, "視窗大小", "{:,}".format(size), lc=DIM, rc=DIM, lf=8, rf=8)
        cache = d.get("cache")
        self.row(session, "快取命中",
                 "{:.0f}%".format(cache * 100) if cache is not None else "—",
                 lc=MUTE, rc=FG)
        if d.get("model"):
            self.row(session, "模型", d["model"], lc=MUTE, rc=FG)

        today = self.card(self.body)
        self.heading(today, "今日 · 本機所有專案")
        # Exact figures, not human(): 2,283,546 and 2,287,430 both render as
        # "2.3M", which makes a working refresh look like a dead button.
        self.row(today, "Token", "{:,}".format(d["today_tokens"]),
                 lc=MUTE, rc=FG, rf=11)
        self.row(today, "請求數", "{:,}".format(d["today_requests"]), lc=MUTE, rc=FG)
        self.row(today, "快取讀取", "{:,}".format(d["today_cache_read"]),
                 lc=DIM, rc=DIM, lf=8, rf=8)
        total = float(d["today_tokens"]) or 1.0
        for name, value in d["by_model"]:
            self.row(today, "   " + name.replace("claude-", ""),
                     "{:,}  {:.0f}%".format(value, value / total * 100),
                     lc=MUTE, rc=BLUE, lf=9, rf=9)

        if d["attribution"]:
            attr = self.card(self.body)
            self.heading(attr, "近 24 小時 · 什麼在用你的額度")
            tot = float(d["attribution_total"]) or 1.0
            for name, kind, value in d["attribution"]:
                self.row(attr, "{} · {}".format(name[:26], kind),
                         "{:.0f}%".format(value / tot * 100),
                         lc=MUTE, rc=FG, lf=9, rf=9)

        sess = self.card(self.body)
        self.heading(sess, "執行中的 Claude Code · {} 個".format(len(d["sessions"])))
        for s in sorted(d["sessions"], key=lambda x: x.get("startedAt", 0)):
            started = datetime.fromtimestamp(s.get("startedAt", 0) / 1000)
            self.row(sess, "  {}".format((s.get("name") or "?")[:24]),
                     started.strftime("%H:%M"), lc=MUTE, rc=DIM, lf=9, rf=9)

        age = d["snapshot_age"]
        if age is None:
            foot = "沒有狀態列快照 · 開一個裝了 ccquota 的 CLI session 可取得即時數字"
        elif age > SNAPSHOT_MAX_AGE:
            foot = "快照已過期 {} · session 數字已隱藏".format(ago(age))
        else:
            foot = "狀態列快照 · {}".format(ago(age))
        tk.Label(self.body, text=foot, bg=BG,
                 fg=YEL if (age is None or age > SNAPSHOT_MAX_AGE) else DIM,
                 font=(F, 8)).pack(anchor="w", padx=26, pady=(2, 0))
        # Refreshing often changes nothing on screen - the snapshot only moves
        # when a session writes one - so stamp the read itself, or the button
        # looks broken when it worked.
        tk.Label(self.body,
                 text="面板讀取於 {}".format(datetime.now().strftime("%H:%M:%S")),
                 bg=BG, fg=DIM, font=(F, 8)).pack(anchor="w", padx=26)

    def request_rebuild(self):
        """Rebuild after the current event finishes.

        build() destroys the frame the refresh button lives in. Tk tolerates
        that here, but destroying the widget whose callback is still on the
        stack is fragile enough not to rely on, so the click is allowed to
        finish first.
        """
        self.after_idle(self.build)

    def tick(self):
        try:
            self.build()
        except Exception as exc:
            # A silently frozen panel still looks live, which is the one thing
            # it must never do. Say so instead.
            for w in self.body.winfo_children():
                w.destroy()
            tk.Label(self.body, text="更新失敗：{}".format(type(exc).__name__),
                     bg=BG, fg=RED, font=(F, 10)).pack(padx=26, pady=20)
            tk.Button(self.body, text="重試", command=self.build, bg=CARD,
                      fg=FG, relief="flat", font=(F, 9)).pack()
        self.after(REFRESH_MS, self.tick)


if __name__ == "__main__":
    Panel().mainloop()
