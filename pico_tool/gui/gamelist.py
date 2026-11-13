"""A list of games that stays quick with tens of thousands: only the rows in view are drawn, and the
covers come in as they are decoded. Rows look like the console's library rows: the focused one gets
the accent wash and bar."""

import tkinter as tk

from PIL import Image, ImageTk

from . import images, theme
from .theme import S
from .widgets import ThinScrollbar, wheel


def _wash(w, h, accent):
    """The selected row's wash: the accent at 16 % fading to 2.5 % at 86 % of the width, then out."""
    key = ("wash", w, h, accent)
    hit = images._cache.get(key)
    if hit:
        return hit
    c = theme.hex_rgb(accent)
    row = Image.new("RGBA", (w, 1))
    px = row.load()
    for k in range(w):
        t = k * 1000 // max(1, w)
        a = 41 - (35 * t) // 860 if t < 860 else 6 - (6 * (t - 860)) // 140
        px[k, 0] = c + (max(0, a),)
    im = ImageTk.PhotoImage(row.resize((w, h)))
    images._cache[key] = im
    return im


class GameList(tk.Frame):
    def __init__(self, parent, ctx, thumb=None, describe=None, on_select=None, on_open=None):
        p = ctx.pal
        tk.Frame.__init__(self, parent, bg=p.panel)
        self.ctx = ctx
        self.items = []
        self.sel = set()
        self.anchor = None
        self.focus = None
        self.top = 0                      # pixels scrolled
        self.rowh = S(56)
        self.thumb = thumb                # thumb(game) -> PhotoImage or None
        self.describe = describe          # describe(game) -> (title, sub, badge, badge_kind)
        self.on_select = on_select
        self.on_open = on_open
        self.hover = None
        self.empty_text = "No games"
        self.c = tk.Canvas(self, bg=p.panel, highlightthickness=0, bd=0, takefocus=1)
        self.sb = ThinScrollbar(self, ctx, self._scroll_cmd)
        self.sb.pack(side="right", fill="y", padx=(S(2), 0))
        self.c.pack(side="left", fill="both", expand=True)
        self.c.bind("<Configure>", lambda e: self.redraw())
        self.c.bind("<Button-1>", self._click)
        self.c.bind("<Shift-Button-1>", lambda e: self._click(e, shift=True))
        self.c.bind("<%s-Button-1>" % theme.MOD, lambda e: self._click(e, toggle=True))
        self.c.bind("<Double-Button-1>", self._double)
        self.c.bind("<Motion>", self._motion)
        self.c.bind("<Leave>", lambda e: self._set_hover(None))
        for key, d in (("<Up>", -1), ("<Down>", 1), ("<Prior>", "pu"), ("<Next>", "pd"), ("<Home>", "home"),
                       ("<End>", "end")):
            self.c.bind(key, lambda e, d=d: self._key(d, False))
            self.c.bind("<Shift-%s" % key[1:], lambda e, d=d: self._key(d, True))
        self.c.bind("<%s-a>" % theme.MOD, lambda e: self.select_all())
        self.c.bind("<Return>", lambda e: self.on_open and self.focus is not None and self.on_open(self.items[self.focus]))
        wheel(self.c, lambda n: self.scroll_by(n * self.rowh // 3 * 2 if abs(n) <= 3 else n))

    # ---- data --------------------------------------------------------------------------------
    def set_items(self, items, keep=True):
        old = [self.items[i] for i in sorted(self.sel)] if keep else []
        focus_item = self.items[self.focus] if keep and self.focus is not None and self.focus < len(self.items) else None
        self.items = items
        pos = {id(g): i for i, g in enumerate(items)}
        self.sel = set(pos[id(g)] for g in old if id(g) in pos)
        self.focus = pos.get(id(focus_item)) if focus_item is not None else None
        if self.focus is None and self.sel:
            self.focus = min(self.sel)
        self.anchor = self.focus
        self.top = min(self.top, self._max_top())
        self.redraw()
        self._fire()

    def selected(self):
        return [self.items[i] for i in sorted(self.sel) if i < len(self.items)]

    def select_all(self):
        self.sel = set(range(len(self.items)))
        self.redraw()
        self._fire()
        return "break"

    def select_index(self, i, see=True):
        if not self.items:
            return
        i = max(0, min(len(self.items) - 1, i))
        self.sel = {i}
        self.focus = self.anchor = i
        if see:
            self.see(i)
        self.redraw()
        self._fire()

    # ---- scrolling ---------------------------------------------------------------------------
    def _max_top(self):
        return max(0, len(self.items) * self.rowh - max(1, self.c.winfo_height()))

    def scroll_by(self, px):
        self.top = max(0, min(self._max_top(), self.top + px))
        self.redraw()

    def _scroll_cmd(self, *a):
        if a[0] == "moveto":
            total = max(1, len(self.items) * self.rowh)
            self.top = max(0, min(self._max_top(), int(float(a[1]) * total)))
            self.redraw()

    def see(self, i):
        h = self.c.winfo_height()
        y = i * self.rowh
        if y < self.top:
            self.top = y
        elif y + self.rowh > self.top + h:
            self.top = y + self.rowh - h
        self.top = max(0, min(self._max_top(), self.top))

    # ---- drawing -----------------------------------------------------------------------------
    def redraw(self):
        c = self.c
        p = self.ctx.pal
        c.delete("all")
        w, h = max(10, c.winfo_width()), max(10, c.winfo_height())
        total = len(self.items) * self.rowh
        self.sb.set(self.top / float(max(1, total)), min(1.0, (self.top + h) / float(max(1, total))))
        if not self.items:
            c.create_text(w // 2, h // 3, text=self.empty_text, fill=p.dim, font=self.ctx.font(13), width=w - S(40),
                          justify="center")
            return
        first = self.top // self.rowh
        last = min(len(self.items), (self.top + h) // self.rowh + 1)
        fT, fS, fB = self.ctx.font(13), self.ctx.font(11), self.ctx.font(10, "bold")
        tw = S(42)
        for i in range(first, last):
            g = self.items[i]
            y = i * self.rowh - self.top
            sel = i in self.sel
            if sel:
                c.create_image(0, y, image=_wash(w, self.rowh - S(2), p.accent), anchor="nw")
                c.create_rectangle(0, y + S(9), S(2.5) if S(2.5) > 2 else 2, y + self.rowh - S(11), fill=p.accent,
                                   outline="")
            elif i == self.hover:
                c.create_rectangle(0, y, w, y + self.rowh - S(2), fill=p.hover, outline="")
            c.create_line(S(14), y + self.rowh - 1, w, y + self.rowh - 1, fill=p.line)
            x = S(14)
            im = self.thumb(g) if self.thumb else None
            cy = y + self.rowh // 2
            if im is not None:
                c.create_image(x + tw // 2, cy, image=im, anchor="center")
            else:
                c.create_image(x + tw // 2, cy, image=images.icon("box", S(26), theme.mix(p.muted, p.panel, 0.55)),
                               anchor="center")
            x += tw + S(14)
            title, sub, badge, bkind = self.describe(g) if self.describe else (g.title, "", "", "")
            right = w - S(14)
            if badge:
                col = {"ok": p.ok, "warn": p.warn, "danger": p.danger, "accent": p.accent}.get(bkind, p.dim)
                bw = fB.measure(badge) + S(16)
                bh = S(20)
                bimg = images.pill(bw, bh, None, theme.mix(col, p.panel, 0.45), 1.0 * theme.scale())
                c.create_image(right, cy, image=bimg, anchor="e")
                c.create_text(right - bw // 2, cy, text=badge, fill=col, font=fB)
                right -= bw + S(12)
            avail = max(20, right - x)
            c.create_text(x, cy - S(9), text=_fit(fT, title, avail), fill=p.ink if sel else theme.mix(p.ink, p.muted, 0.35),
                          font=fT, anchor="w")
            if sub:
                c.create_text(x, cy + S(11), text=_fit(fS, sub, avail), fill=p.dim if not sel else p.muted, font=fS,
                              anchor="w")

    # ---- input -------------------------------------------------------------------------------
    def _row_at(self, y):
        i = (y + self.top) // self.rowh
        return i if 0 <= i < len(self.items) else None

    def _click(self, e, shift=False, toggle=False):
        self.c.focus_set()
        i = self._row_at(e.y)
        if i is None:
            return
        if shift and self.anchor is not None:
            a, b = sorted((self.anchor, i))
            self.sel = set(range(a, b + 1))
        elif toggle:
            self.sel ^= {i}
            self.anchor = i
        else:
            self.sel = {i}
            self.anchor = i
        self.focus = i
        self.redraw()
        self._fire()
        return "break"

    def _double(self, e):
        i = self._row_at(e.y)
        if i is not None and self.on_open:
            self.on_open(self.items[i])

    def _motion(self, e):
        self._set_hover(self._row_at(e.y))

    def _set_hover(self, i):
        if i != self.hover:
            self.hover = i
            self.redraw()

    def _key(self, d, shift):
        if not self.items:
            return "break"
        cur = self.focus if self.focus is not None else 0
        page = max(1, self.c.winfo_height() // self.rowh - 1)
        if d == "pu":
            n = cur - page
        elif d == "pd":
            n = cur + page
        elif d == "home":
            n = 0
        elif d == "end":
            n = len(self.items) - 1
        else:
            n = cur + d
        n = max(0, min(len(self.items) - 1, n))
        if shift and self.anchor is not None:
            a, b = sorted((self.anchor, n))
            self.sel = set(range(a, b + 1))
        else:
            self.sel = {n}
            self.anchor = n
        self.focus = n
        self.see(n)
        self.redraw()
        self._fire()
        return "break"

    def _fire(self):
        if self.on_select:
            self.on_select(self.selected())


def _fit(font, text, width):
    if font.measure(text) <= width:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(text[:mid] + "…") <= width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + "…"
