"""Local scene rotation and smooth fades, using only Tk and the standard library."""
import math
import os
import random
import struct
import time
import tkinter as tk
import zlib
from datetime import datetime, timedelta

FADE_SECONDS = 2.0
FRAME_MS = 33
CLOCK_MS = 1000
SCENE_BG = '#091621'
IMAGE_TYPES = {'.png', '.gif', '.ppm', '.pgm'}


def scene_slot(now):
    """Latest local 09:00/18:00 boundary (midnight is not a boundary)."""
    if now.hour >= 18:
        return now.replace(hour=18, minute=0, second=0, microsecond=0)
    if now.hour >= 9:
        return now.replace(hour=9, minute=0, second=0, microsecond=0)
    return (now - timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)


def scene_files(directory):
    try:
        with os.scandir(directory) as files:
            return sorted(entry.path for entry in files if entry.is_file()
                          and os.path.splitext(entry.name)[1].lower() in IMAGE_TYPES)
    except OSError:
        return []


def shade_png(alpha):
    """One RGBA pixel, expanded by Tk into a true translucent scene overlay."""
    def chunk(kind, data):
        return (struct.pack('!I', len(data)) + kind + data
                + struct.pack('!I', zlib.crc32(kind + data) & 0xffffffff))
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('!2I5B', 1, 1, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(bytes((0, 9, 22, 33, alpha))))
            + chunk(b'IEND', b''))


class ScenePlayer:
    def __init__(self, master, directory, redraw):
        self.master = master
        self.directory = directory
        self.redraw = redraw
        self.current_path = None
        self.art = None
        self.photo = None
        self.size = None
        self.canvas = None
        self.shade = None
        self.shade_key = None
        self.alpha = 0
        self.pending = None
        self.swapped = False
        self.started = None
        self.frame_id = None
        self.clock_id = None
        self.closed = False
        self.last_slot = scene_slot(datetime.now())
        selected = self.choose()
        if selected:
            self.current_path, self.art = selected
        self.clock_id = master.after(CLOCK_MS, self.check_clock)
        master.bind('<Destroy>', self.on_destroy, add='+')

    @property
    def transitioning(self):
        return self.started is not None

    def choose(self):
        # Rescan each time: added files work immediately, removed/broken files are skipped.
        candidates = [path for path in scene_files(self.directory) if path != self.current_path]
        random.shuffle(candidates)
        for path in candidates:
            try:
                art = tk.PhotoImage(master=self.master, file=path)
                if art.width() > 0 and art.height() > 0:
                    return path, art
            except (tk.TclError, OSError):
                continue
        return None

    def switch(self):
        if self.closed or self.transitioning:
            return False
        selected = self.choose()
        if selected is None:
            return False
        if self.art is None:
            self.current_path, self.art = selected
            self.size = None
            self.redraw()
            return True
        self.pending = selected
        self.swapped = False
        self.started = time.monotonic()
        self.animate()
        return True

    def check_clock(self, now=None):
        if self.closed:
            return
        slot = scene_slot(now or datetime.now())
        # High-water mark prevents duplicates after backward clock adjustments.
        # A jump over several boundaries causes only one transition after wake.
        if slot > self.last_slot:
            self.last_slot = slot
            self.switch()
        self.clock_id = self.master.after(CLOCK_MS, self.check_clock)

    def animate(self):
        if self.closed:
            return
        progress = min(1.0, (time.monotonic() - self.started) / FADE_SECONDS)
        amount = 2 * progress if progress < .5 else 2 * (1 - progress)
        self.alpha = round(255 * amount * amount * (3 - 2 * amount))
        if progress >= .5 and not self.swapped:
            self.current_path, self.art = self.pending
            self.pending = None
            self.swapped = True
            self.size = None
            self.redraw()
        else:
            self.update_shade()
        if progress >= 1:
            self.started = None
            self.frame_id = None
            self.alpha = 0
            self.update_shade()
            return
        self.frame_id = self.master.after(FRAME_MS, self.animate)

    def update_shade(self):
        if self.canvas is None or self.size is None:
            return
        if not self.alpha:
            self.canvas.itemconfigure('scene-shade', state='hidden')
            return
        key = self.size, self.alpha
        if key != self.shade_key:
            pixel = tk.PhotoImage(master=self.master, data=shade_png(self.alpha), format='png')
            self.shade = pixel.zoom(*self.size)
            self.shade_key = key
        self.canvas.itemconfigure('scene-shade', image=self.shade, state='normal')

    def render(self, canvas, x, y, width, height):
        self.canvas = canvas
        canvas.create_rectangle(x, y, x+width, y+height, fill=SCENE_BG, outline='#344958')
        if self.art is None:
            return
        size = (max(1, int(width)-2), max(1, int(height)-2))
        if self.size != size:
            factor = max(1, min(self.art.width() // size[0], self.art.height() // size[1]))
            scaled = self.art.subsample(factor, factor)
            zoom = max(1, math.ceil(size[0] / scaled.width()), math.ceil(size[1] / scaled.height()))
            if zoom > 1:
                scaled = scaled.zoom(zoom, zoom)
            left = max(0, (scaled.width() - size[0]) // 2)
            top = max(0, scaled.height() - size[1])
            self.photo = tk.PhotoImage(master=self.master, width=size[0], height=size[1])
            self.master.tk.call(self.photo, 'copy', scaled, '-from', left, top,
                                left+size[0], top+size[1], '-to', 0, 0)
            self.size = size
        canvas.create_image(x+1, y+1, image=self.photo, anchor='nw', tags='scene-image')
        canvas.create_image(x+1, y+1, anchor='nw', tags='scene-shade', state='hidden')
        self.update_shade()

    def on_destroy(self, event):
        if event.widget is self.master:
            self.close()

    def close(self):
        self.closed = True
        for callback in (self.clock_id, self.frame_id):
            if callback:
                self.master.after_cancel(callback)
        self.clock_id = self.frame_id = None
