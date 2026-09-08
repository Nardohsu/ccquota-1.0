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
import queue
import threading
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ccquota as q
from ccquota_scene import ScenePlayer

REFRESH_MS = 30000
SNAPSHOT_MAX_AGE = 900     # 超過就不再顯示 session 層級的數字
WIDTH = 430

BG = "#0d1722"
CARD = "#162432"
LINE = "#344958"
FG = "#eaf0f3"
MUTE = "#a4b3c0"
DIM = "#7e929f"
GRN = "#72d6a4"
YEL = "#e4c775"
RED = "#d1554f"
BLUE = "#8dafcb"

F = "Microsoft JhengHei UI" if sys.platform == "win32" else "Helvetica"
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
    out["guild_tokens"] = q.window_sum(entries, now - q.WEEK, now)[0]
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


class UsageDetails(tk.Toplevel):
    """The original statistics, available in a scrollable guild ledger."""

    def __init__(self, master):
        tk.Toplevel.__init__(self, master)
        self.title("ccquota · 公會用量明細")
        self.configure(bg=BG)
        self.geometry("480x{}".format(min(830, self.winfo_screenheight() - 100)))
        self.minsize(WIDTH, 420)
        self.attributes("-topmost", master.topmost.get())
        self.viewport = tk.Canvas(self, bg=BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(self, command=self.viewport.yview)
        scrollbar.pack(side="right", fill="y")
        self.viewport.pack(fill="both", expand=True)
        self.viewport.configure(yscrollcommand=scrollbar.set)
        self.content = tk.Frame(self.viewport, bg=BG)
        item = self.viewport.create_window(0, 0, anchor="nw", window=self.content)
        self.viewport.bind("<Configure>", lambda e: self.viewport.itemconfigure(item, width=e.width))
        self.content.bind("<Configure>", lambda e: self.viewport.configure(scrollregion=self.viewport.bbox("all")))
        self.bind("<MouseWheel>", lambda e: self.viewport.yview_scroll(-int(e.delta / 120), "units"))
        self.bind("<Button-4>", lambda e: self.viewport.yview_scroll(-1, "units"))
        self.bind("<Button-5>", lambda e: self.viewport.yview_scroll(1, "units"))
        self.bind("<Escape>", lambda e: self.destroy())
        self.body = None
        self.build()

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
        self.body = tk.Frame(self.content, bg=BG)
        self.body.pack(fill="both", expand=True, pady=(10, 10))
        d = self.master.data

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
                 text="面板讀取於 {}".format(datetime.fromtimestamp(d["now"]).strftime("%H:%M:%S")),
                 bg=BG, fg=DIM, font=(F, 8)).pack(anchor="w", padx=26)

    def request_rebuild(self):
        """Rebuild after the current event finishes.

        build() destroys the frame the refresh button lives in. Tk tolerates
        that here, but destroying the widget whose callback is still on the
        stack is fragile enough not to rely on, so the click is allowed to
        finish first.
        """
        self.master.request_rebuild()


def compact_tokens(value):
    for scale, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if value >= scale:
            return "{:.2f}{}".format(value / scale, suffix)
    return "{:,}".format(value)


def remaining_percent(limit):
    used = limit.get("pct")
    return None if used is None else max(0, min(100, 100 - used))


class Panel(tk.Tk):
    """Compact guild dashboard; collection stays off the Tk event loop."""

    def __init__(self):
        tk.Tk.__init__(self)
        self.title("ccquota · 冒險者公會")
        self.configure(bg=BG)
        self.geometry("430x350")
        self.minsize(430, 350)
        self.topmost = tk.BooleanVar(self, value=True)
        self.attributes("-topmost", True)
        self.data = None
        self.error = None
        self.busy = False
        self.details = None
        self.results = queue.Queue()
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.scene_player = ScenePlayer(self, os.path.join(os.path.dirname(__file__), "assets"), self.draw)
        self.refresh_button = self.button("更新", self.request_rebuild)
        self.options_button = self.button("選項", self.show_options)
        self.menu = tk.Menu(self, tearoff=False, bg=CARD, fg=FG,
                            activebackground=LINE, activeforeground=FG)
        self.menu.add_command(label="用量明細", command=self.show_details)
        self.menu.add_separator()
        self.menu.add_checkbutton(label="視窗保持置頂", variable=self.topmost, command=self.set_topmost)
        self.menu.add_command(label="更新資料   F5", command=self.request_rebuild)
        self.menu.add_separator()
        self.menu.add_command(label="隨機切換場景", command=self.scene_player.switch)
        self.menu.add_command(label="每天 09:00 / 18:00 自動換景", state="disabled")
        self.menu.add_separator()
        self.menu.add_command(label="關閉", command=self.destroy)
        self.canvas.bind("<Configure>", self.draw)
        self.canvas.bind("<Button-1>", self.on_scene_click)
        self.canvas.bind("<Motion>", self.on_motion)
        self.bind("<F5>", lambda e: self.request_rebuild())
        self.bind("<Control-d>", lambda e: self.show_details())
        self.after(50, self.poll)
        self.after(REFRESH_MS, self.tick)
        self.request_rebuild()
        self.after_idle(self.style_titlebar)

    def style_titlebar(self):
        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes
                user32 = ctypes.windll.user32
                user32.GetParent.argtypes = [wintypes.HWND]
                user32.GetParent.restype = wintypes.HWND
                hwnd = user32.GetParent(self.winfo_id())
                dwm = ctypes.windll.dwmapi.DwmSetWindowAttribute
                dwm.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
                enabled = ctypes.c_int(1)
                dwm(hwnd, 20, ctypes.byref(enabled), ctypes.sizeof(enabled))
            except (AttributeError, OSError):
                pass  # Older Windows versions keep their system title bar.

    def in_scene(self, event):
        return 15 <= event.x <= self.canvas.winfo_width()-15 and 167 <= event.y <= self.canvas.winfo_height()-35

    def on_scene_click(self, event):
        if self.in_scene(event):
            self.show_details()

    def on_motion(self, event):
        self.canvas.configure(cursor="hand2" if self.in_scene(event) else "")

    def button(self, text, command):
        return tk.Button(self, text=text, command=command, bg="#173a35", fg="#d9efdf",
                         activebackground="#26584b", activeforeground="#ffffff",
                         disabledforeground=MUTE, relief="flat", bd=0,
                         highlightthickness=1, highlightbackground="#568777",
                         highlightcolor=YEL, cursor="hand2", font=(F, -11, "bold"))

    def set_topmost(self):
        self.attributes("-topmost", self.topmost.get())
        if self.details is not None and self.details.winfo_exists():
            self.details.attributes("-topmost", self.topmost.get())

    def show_options(self):
        try:
            self.menu.tk_popup(self.options_button.winfo_rootx(), self.options_button.winfo_rooty() + 26)
        finally:
            self.menu.grab_release()

    def show_details(self):
        if self.data is None:
            return
        if self.details is not None and self.details.winfo_exists():
            self.details.lift()
            self.details.focus_set()
        else:
            self.details = UsageDetails(self)

    def request_rebuild(self):
        if self.busy:
            return
        self.busy = True
        self.refresh_button.configure(text="讀取中", state="disabled")
        self.draw()

        def collect():
            try:
                self.results.put((gather(), None))
            except Exception as exc:
                self.results.put((None, type(exc).__name__))

        threading.Thread(target=collect, daemon=True).start()

    def poll(self):
        try:
            data, error = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self.error = error
            if data is not None:
                self.data = data
            self.refresh_button.configure(text="重試" if error else "更新", state="normal")
            self.draw()
            if self.details is not None and self.details.winfo_exists():
                self.details.build()
        self.after(80, self.poll)

    def tick(self):
        self.request_rebuild()
        self.after(REFRESH_MS, self.tick)

    def rounded(self, x1, y1, x2, y2, fill=CARD, outline=LINE, radius=10):
        r = radius
        return self.canvas.create_polygon(
            x1+r, y1, x2-r, y1, x2, y1, x2, y1+r, x2, y2-r,
            x2, y2, x2-r, y2, x1+r, y2, x1, y2, x1, y2-r,
            x1, y1+r, x1, y1, smooth=True, splinesteps=16,
            fill=fill, outline=outline, width=1)

    def text(self, x, y, text, size=11, color=FG, bold=False, anchor="nw", width=None, mono=False):
        args = dict(text=text, fill=color, anchor=anchor,
                    font=(MONO if mono else F, -size, "bold" if bold else "normal"))
        if width is not None:
            args["width"] = width
        return self.canvas.create_text(x, y, **args)

    def scene(self, x, y, width, height):
        self.scene_player.render(self.canvas, x, y, width, height)

    def draw(self, event=None):
        c = self.canvas
        c.delete("all")
        w = max(430, c.winfo_width())
        h = max(350, c.winfo_height())
        self.rounded(1, 1, w-2, h-2, BG, "#53616b", 15)
        self.rounded(5, 5, w-6, h-6, BG, "#1d303e", 12)
        self.rounded(15, 17, 47, 49, "#edcc6d", "#fae7a3", 9)
        self.text(31, 33, "C", 20, "#23302c", True, "center", mono=True)
        self.text(57, 19, "CCQUOTA · 冒險者公會", 12, bold=True)
        self.text(57, 37, "你的任務正在世界裡悄悄推進", 9, MUTE)
        self.options_button.place(x=w-128, y=23, width=51, height=23)
        self.refresh_button.place(x=w-70, y=23, width=51, height=23)

        gap = 9
        cw = (w - 30 - 2*gap) / 3
        xs = [15+i*(cw+gap) for i in range(3)]
        for x in xs:
            self.rounded(x, 61, x+cw, 157, CARD, "#4a5c6c", 10)
            self.rounded(x+3, 64, x+cw-3, 154, CARD, "#203443", 8)
        for x, title in zip(xs, ("每週行動力", "公會總資產", "今日採集")):
            self.text(x+9, 70, title, 12, YEL, True)

        d = self.data
        weekly = d["seven"] if d else {}
        remain = remaining_percent(weekly)
        self.text(xs[0]+cw/2, 100, "剩餘 {}%".format("{:g}".format(remain)) if remain is not None else "剩餘 —",
                  15, tone(weekly.get("pct")), True, "center", mono=True)
        reset = weekly.get("resets")
        reset_label = datetime.fromtimestamp(reset).strftime("%m/%d %H:%M 重置") if reset else "等待官方額度"
        self.text(xs[0]+9, 118, reset_label, 9, MUTE)
        segw = (cw-18-4*4)/5
        for i in range(5):
            sx = xs[0]+9+i*(segw+4)
            self.rounded(sx, 139, sx+segw, 147, "#293d43", "", 2)
            fraction = 0 if remain is None else max(0, min(1, (remain-i*20)/20))
            if fraction > 0:
                self.rounded(sx, 139, sx+max(1, segw*fraction), 147, tone(weekly.get("pct")), "", 2)
        self.text(xs[1]+10, 101, compact_tokens(d["guild_tokens"]) if d else "—", 17, bold=True, mono=True)
        self.text(xs[1]+10, 137, "近 7 日 · TOKEN", 9, MUTE)
        self.text(xs[2]+10, 101, compact_tokens(d["today_tokens"]) if d else "—", 17, bold=True, mono=True)
        self.text(xs[2]+10, 137, "{} 次請求 · 本機".format(d["today_requests"]) if d else "正在讀取本機紀錄", 9, MUTE)

        bottom = h-35
        self.rounded(15, 167, w-15, bottom, "#111f2b", "#425764", 10)
        self.text(27, 178, "遠征隊伍", 12, YEL, True)
        sessions = d["sessions"] if d else []
        self.text(w-27, 179, "{} 名角色執行中  ›".format(len(sessions)) if d else "正在偵察…",
                  11, bold=True, anchor="ne")
        sy = 204
        self.scene(27, sy, w-54, bottom-sy-11)
        self.text(40, sy+12, "冒險者村莊" if not sessions else "冒險者出征中", 12, YEL, True)
        if sessions:
            names = "、".join((s.get("name") or "未命名") for s in sessions[:2])
            if len(names) > 23:
                names = names[:22] + "…"
            message = names + "\n正在推進任務，點此查看隊伍。"
        else:
            message = "待命角色會在村莊裡散步，\n也會在營火旁休息。"
        self.text(40, sy+34, message, 10, "#d1dce4", width=200)

        if self.error:
            status, color = "更新失敗 · {} · 請按重試".format(self.error), RED
        elif not d:
            status, color = "正在讀取公會紀錄…", MUTE
        elif weekly.get("source") == "狀態列快照":
            status, color = "額度快照 · " + ago(weekly.get("age") or 0), MUTE
        elif weekly.get("source") == "磁碟快取":
            status, color = "額度快取 · " + ago(weekly.get("age") or 0), YEL
        else:
            status, color = "無官方額度 · 採集為本機統計", YEL
        self.text(17, h-23, status, 9, color)
        stamp = datetime.fromtimestamp(d["now"]).strftime("%H:%M:%S") if d else "—"
        self.text(w-17, h-23, "讀取中…" if self.busy else "更新 " + stamp, 9, DIM, anchor="ne")


if __name__ == "__main__":
    Panel().mainloop()

