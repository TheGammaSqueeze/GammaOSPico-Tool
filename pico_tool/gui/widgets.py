"""The window's controls, drawn in the "Current" theme: pill buttons, switches, fields, lists, notices,
progress bars and dialogs. Each one sits on a panel colour (pal.panel) unless told otherwise."""

import tkinter as tk
import webbrowser

from . import images, theme
from .theme import S


class Ctx(object):
    """What every control needs: the palette and the fonts."""

    def __init__(self, root, pal, family, mono):
        self.root = root
        self.pal = pal
        self.family = family
        self.mono = mono
        self.fonts = {}

    def font(self, size, weight="normal", mono=False):
        key = (size, weight, mono)
        f = self.fonts.get(key)
        if f is None:
            import tkinter.font as tkfont
            f = tkfont.Font(root=self.root, family=self.mono if mono else self.family,
                            size=-max(8, S(size)), weight=weight)
            self.fonts[key] = f
        return f


def spaced(text):
    """CAPITALS with hair spaces between the letters (the theme's section labels)."""
    return " ".join(text.upper())


def bind_all(widgets, seq, fn):
    for w in widgets:
        w.bind(seq, fn, add="+")


class PillButton(tk.Canvas):
    """A rounded button: "primary" (ink pill, dark text, like the console's), "accent", "secondary"
    (an outline), "danger" or "ghost" (text only)."""

    def __init__(self, parent, ctx, text, command=None, kind="secondary", icon=None, small=False, bg=None,
                 width=None, stretch=False):
        self.ctx = ctx
        self.kind = kind
        self.stretch = stretch
        self._drawn_w = 0
        self.command = command
        self.icon_name = icon
        self.small = small
        self.fixed_w = width
        self.bgc = bg or ctx.pal.panel
        self._state = "normal"
        self._hover = False
        self._down = False
        self.font_ = ctx.font(12 if small else 13, "bold" if kind in ("primary", "accent", "danger") else "normal")
        tk.Canvas.__init__(self, parent, bg=self.bgc, bd=0, highlightthickness=0, cursor="hand2")
        self.text = text
        self._draw()
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        if stretch:
            self.bind("<Configure>", lambda e: e.width != self._drawn_w and self._draw())

    def set_text(self, text):
        if text != self.text:
            self.text = text
            self._draw()

    def _colors(self):
        p = self.ctx.pal
        dis = self._state == "disabled"
        if self.kind in ("primary", "accent", "danger"):
            base = {"primary": p.ink, "accent": p.accent, "danger": p.danger}[self.kind]
            hov = p.accent if self.kind == "primary" else theme.mix(base, p.ink, 0.35)
            fill = theme.mix(base, self.bgc, 0.6) if dis else (hov if self._hover else base)
            return fill, None, p.btn_ink
        if self.kind == "ghost":
            return None, None, p.dim if dis else (p.accent if self._hover else p.muted)
        edge = theme.mix(p.muted, self.bgc, 0.78) if dis else (p.accent if self._hover else theme.mix(p.muted, self.bgc, 0.5))
        fill = theme.mix(self.bgc, p.accent, 0.10) if self._hover and not dis else None
        return fill, edge, p.dim if dis else p.ink

    def _draw(self):
        fill, edge, ink = self._colors()
        h = S(30 if self.small else 36)
        iw = S(16 if self.small else 17) if self.icon_name else 0
        gap = S(8) if self.icon_name and self.text else 0
        tw = self.font_.measure(self.text) if self.text else 0
        pad = S(6) if self.kind == "ghost" else S(14 if self.small else 18)
        w = self.fixed_w or (pad * 2 + iw + gap + tw)
        if self.stretch:
            got = self.winfo_width()
            if got > w:
                w = got
            else:
                self.configure(width=w)
        else:
            self.configure(width=w)
        self.configure(height=h)
        self._drawn_w = w
        self.delete("all")
        if fill or edge:
            self._img = images.pill(w, h, fill, edge, 1.2 * theme.scale())
            self.create_image(0, 0, image=self._img, anchor="nw")
        x = (w - (iw + gap + tw)) // 2
        if self.icon_name:
            self._ic = images.icon(self.icon_name, iw, ink)
            self.create_image(x, h // 2, image=self._ic, anchor="w")
        if self.text:
            self.create_text(x + iw + gap, h // 2, text=self.text, fill=ink, font=self.font_, anchor="w")

    def _enter(self, _e):
        if self._state != "disabled":
            self._hover = True
            self._draw()

    def _leave(self, _e):
        self._hover = False
        self._down = False
        self._draw()

    def _press(self, _e):
        if self._state != "disabled":
            self._down = True

    def _release(self, e):
        was = self._down
        self._down = False
        if was and self._state != "disabled" and 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height():
            if self.command:
                self.command()

    def set_state(self, state):
        if state != self._state:
            self._state = state
            self.configure(cursor="" if state == "disabled" else "hand2")
            self._draw()

    def enabled(self, on):
        self.set_state("normal" if on else "disabled")


class Switch(tk.Frame):
    """A switch with its label; variable is a tk.BooleanVar."""

    def __init__(self, parent, ctx, text, variable, command=None, bg=None, kind="switch"):
        self.ctx = ctx
        self.var = variable
        self.command = command
        self.kind = kind
        bgc = bg or ctx.pal.panel
        tk.Frame.__init__(self, parent, bg=bgc)
        self.img = tk.Label(self, bg=bgc, bd=0, cursor="hand2")
        self.img.pack(side="left")
        self.lab = tk.Label(self, text=text, bg=bgc, fg=ctx.pal.ink, font=ctx.font(13), cursor="hand2")
        self.lab.pack(side="left", padx=(S(8), 0))
        for w in (self.img, self.lab):
            w.bind("<Button-1>", self._toggle)
        self._trace = self.var.trace_add("write", lambda *a: self._draw())
        self._draw()

    def _draw(self):
        p = self.ctx.pal
        if self.kind == "check":
            im = images.check(bool(self.var.get()), p.accent, theme.mix(p.muted, p.panel, 0.4))
        else:
            im = images.switch(bool(self.var.get()), p.accent, theme.mix(p.muted, p.panel, 0.65))
        self.img.configure(image=im)

    def _toggle(self, _e=None):
        self.var.set(not self.var.get())
        if self.command:
            self.command()

    def destroy(self):
        try:
            self.var.trace_remove("write", self._trace)
        except Exception:
            pass
        tk.Frame.destroy(self)


class Radio(tk.Frame):
    def __init__(self, parent, ctx, text, variable, value, command=None, bg=None):
        self.ctx, self.var, self.value, self.command = ctx, variable, value, command
        bgc = bg or ctx.pal.panel
        tk.Frame.__init__(self, parent, bg=bgc)
        self.img = tk.Label(self, bg=bgc, bd=0, cursor="hand2")
        self.img.pack(side="left")
        self.lab = tk.Label(self, text=text, bg=bgc, fg=ctx.pal.ink, font=ctx.font(13), cursor="hand2")
        self.lab.pack(side="left", padx=(S(8), 0))
        for w in (self.img, self.lab):
            w.bind("<Button-1>", self._pick)
        self._trace = self.var.trace_add("write", lambda *a: self._draw())
        self._draw()

    def _draw(self):
        p = self.ctx.pal
        self.img.configure(image=images.radio(self.var.get() == self.value, p.accent, theme.mix(p.muted, p.panel, 0.4)))

    def _pick(self, _e=None):
        self.var.set(self.value)
        if self.command:
            self.command()

    def destroy(self):
        try:
            self.var.trace_remove("write", self._trace)
        except Exception:
            pass
        tk.Frame.destroy(self)


class Field(tk.Frame):
    """A one-line text field with a rounded edge that lights up in the accent when focused."""

    def __init__(self, parent, ctx, textvariable=None, show=None, width=24, placeholder="", bg=None, icon=None):
        self.ctx = ctx
        p = ctx.pal
        self.bgc = bg or p.panel
        tk.Frame.__init__(self, parent, bg=self.bgc)
        self.var = textvariable if textvariable is not None else tk.StringVar()
        self.canvas = tk.Canvas(self, bg=self.bgc, highlightthickness=0, bd=0, height=S(36), width=S(120))
        self.canvas.pack(fill="both", expand=True)
        self.entry = tk.Entry(self.canvas, textvariable=self.var, show=show or "", bd=0, relief="flat",
                              bg=p.field, fg=p.ink, insertbackground=p.ink, disabledbackground=p.field,
                              disabledforeground=p.dim, selectbackground=theme.mix(p.field, p.accent, 0.45),
                              selectforeground=p.ink, font=ctx.font(13), width=width, highlightthickness=0)
        self.ph = tk.Label(self.canvas, text=placeholder, bg=p.field, fg=p.dim, font=ctx.font(13), anchor="w",
                           cursor="xterm")
        self.icon = icon
        self.focused = False
        self.win = self.canvas.create_window(0, 0, window=self.entry, anchor="w")
        self.phwin = self.canvas.create_window(0, 0, window=self.ph, anchor="w", state="hidden")
        self.canvas.bind("<Configure>", lambda e: self._draw())
        self.entry.bind("<FocusIn>", lambda e: self._focus(True), add="+")
        self.entry.bind("<FocusOut>", lambda e: self._focus(False), add="+")
        self.canvas.bind("<Button-1>", lambda e: self.entry.focus_set())
        self.ph.bind("<Button-1>", lambda e: self.entry.focus_set())
        self.placeholder = placeholder
        self._tr = self.var.trace_add("write", lambda *a: self._show_ph())

    def _focus(self, on):
        self.focused = on
        self._draw()

    def _draw(self):
        p = self.ctx.pal
        c = self.canvas
        w, h = max(20, c.winfo_width()), max(20, c.winfo_height())
        c.delete("bg", "icon")
        edge = p.accent if self.focused else p.field_edge
        self._img = images.pill(w, h, p.field, edge, 1.2 * theme.scale(), radius=S(10))
        c.create_image(0, 0, image=self._img, anchor="nw", tags="bg")
        c.tag_lower("bg")
        left = S(12)
        if self.icon:
            self._ic = images.icon(self.icon, S(16), p.muted)
            c.create_image(left, h // 2, image=self._ic, anchor="w", tags="icon")
            left += S(24)
        c.coords(self.win, left, h // 2)
        c.itemconfigure(self.win, width=max(10, w - left - S(12)), height=max(10, h - S(12)))
        c.coords(self.phwin, left, h // 2)
        c.itemconfigure(self.phwin, width=max(10, w - left - S(12)), height=max(10, h - S(12)))
        self._show_ph()

    def _show_ph(self):
        show = bool(self.placeholder) and not self.var.get() and not self.focused
        self.canvas.itemconfigure(self.phwin, state="normal" if show else "hidden")

    def get(self):
        return self.var.get()

    def destroy(self):
        try:
            self.var.trace_remove("write", self._tr)
        except Exception:
            pass
        tk.Frame.destroy(self)


class Select(tk.Canvas):
    """A drop-down: a rounded field showing the choice, a menu of the others."""

    def __init__(self, parent, ctx, choices, variable, command=None, bg=None, width=None):
        self.ctx, self.choices, self.var, self.command = ctx, list(choices), variable, command
        self.bgc = bg or ctx.pal.panel
        self.fixed_w = width
        tk.Canvas.__init__(self, parent, bg=self.bgc, bd=0, highlightthickness=0, cursor="hand2")
        self.bind("<Button-1>", self._open)
        self._hover = False
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self._trace = self.var.trace_add("write", lambda *a: self._draw())
        self._draw()

    def _set_hover(self, on):
        self._hover = on
        self._draw()

    def _label(self):
        v = self.var.get()
        for lab, val in self.choices:
            if val == v:
                return lab
        return v

    def _draw(self):
        p = self.ctx.pal
        f = self.ctx.font(13)
        w = self.fixed_w or max(f.measure(lab) for lab, _v in self.choices) + S(56)
        h = S(36)
        self.configure(width=w, height=h)
        self.delete("all")
        self._img = images.pill(w, h, p.field, p.accent if self._hover else p.field_edge, 1.2 * theme.scale(),
                                radius=S(10))
        self.create_image(0, 0, image=self._img, anchor="nw")
        self._ch = images.icon("down", S(14), p.muted)
        self.create_image(w - S(14), h // 2, image=self._ch, anchor="e")
        self.create_text(S(12), h // 2, text=self._label(), fill=p.ink, font=f, anchor="w")

    def _open(self, _e=None):
        p = self.ctx.pal
        m = tk.Menu(self, tearoff=0, bg=p.field, fg=p.ink, activebackground=theme.mix(p.field, p.accent, 0.35),
                    activeforeground=p.ink, bd=0, font=self.ctx.font(13), relief="flat")
        for lab, val in self.choices:
            m.add_command(label=("\u2713  " if val == self.var.get() else "     ") + lab,
                          command=lambda v=val: self._choose(v))
        try:
            m.tk_popup(self.winfo_rootx(), self.winfo_rooty() + self.winfo_height())
        finally:
            m.grab_release()

    def _choose(self, v):
        self.var.set(v)
        if self.command:
            self.command()

    def destroy(self):
        try:
            self.var.trace_remove("write", self._trace)
        except Exception:
            pass
        tk.Canvas.destroy(self)


class ThinScrollbar(tk.Canvas):
    """A slim scroll bar in the theme (works as a Text's or Canvas's yscrollcommand target)."""

    def __init__(self, parent, ctx, command, bg=None):
        self.ctx = ctx
        self.cmd = command
        bgc = bg or ctx.pal.panel
        tk.Canvas.__init__(self, parent, width=S(10), bg=bgc, highlightthickness=0, bd=0)
        self.first, self.last = 0.0, 1.0
        self.drag = None
        self.hover = False
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "drag", None))
        self.bind("<Enter>", lambda e: (setattr(self, "hover", True), self._draw()))
        self.bind("<Leave>", lambda e: (setattr(self, "hover", False), self._draw()))

    def set(self, first, last):
        self.first, self.last = float(first), float(last)
        self._draw()

    def _draw(self):
        self.delete("all")
        if self.last - self.first >= 0.999:
            return
        h = self.winfo_height()
        w = self.winfo_width()
        y0 = int(self.first * h)
        y1 = max(y0 + S(24), int(self.last * h))
        if y1 > h:
            y0, y1 = h - (y1 - y0), h
        p = self.ctx.pal
        col = p.accent if (self.hover or self.drag is not None) else theme.mix(p.muted, p.panel, 0.55)
        tw = S(6)
        self._img = images.pill(tw, max(tw, y1 - y0), col)
        self.create_image(w // 2, y0, image=self._img, anchor="n")

    def _press(self, e):
        h = max(1, self.winfo_height())
        f = e.y / float(h)
        if self.first <= f <= self.last:
            self.drag = (e.y, self.first)
        else:
            self.cmd("moveto", max(0.0, f - (self.last - self.first) / 2))

    def _motion(self, e):
        if self.drag is None:
            return
        h = max(1, self.winfo_height())
        y, first = self.drag
        self.cmd("moveto", max(0.0, first + (e.y - y) / float(h)))


class Progress(tk.Canvas):
    """A rounded progress bar; fraction None makes it run back and forth."""

    def __init__(self, parent, ctx, bg=None, height=6):
        self.ctx = ctx
        bgc = bg or ctx.pal.panel
        tk.Canvas.__init__(self, parent, height=S(height), bg=bgc, highlightthickness=0, bd=0)
        self.frac = 0.0
        self._t = 0
        self._job = None
        self.bind("<Configure>", lambda e: self._draw())

    def set(self, frac):
        self.frac = frac
        if frac is None and self._job is None:
            self._tick()
        self._draw()

    def _tick(self):
        if self.frac is not None:
            self._job = None
            return
        self._t = (self._t + 1) % 100
        self._draw()
        self._job = self.after(30, self._tick)

    def _draw(self):
        self.delete("all")
        w, h = max(4, self.winfo_width()), max(2, self.winfo_height())
        p = self.ctx.pal
        self._a = images.pill(w, h, theme.mix(p.panel, p.muted, 0.18))
        self.create_image(0, 0, image=self._a, anchor="nw")
        if self.frac is None:
            seg = max(h, w // 4)
            t = self._t / 100.0
            x = int((w + seg) * t) - seg
            x0, x1 = max(0, x), min(w, x + seg)
            if x1 - x0 >= h:
                self._b = images.pill(x1 - x0, h, p.accent)
                self.create_image(x0, 0, image=self._b, anchor="nw")
        elif self.frac > 0:
            fw = max(h, int(w * min(1.0, self.frac)))
            self._b = images.pill(fw, h, p.accent)
            self.create_image(0, 0, image=self._b, anchor="nw")

    def destroy(self):
        if self._job:
            self.after_cancel(self._job)
        tk.Canvas.destroy(self)


class Notice(tk.Frame):
    """A callout: an icon, a bold first line and wrapped text, on a wash of its colour with a bar."""

    def __init__(self, parent, ctx, kind, title, text, wrap=480, bg=None):
        p = ctx.pal
        wash = {"danger": p.danger_wash, "warn": p.warn_wash}.get(kind, p.note_wash)
        bar = {"danger": p.danger, "warn": p.warn}.get(kind, p.accent)
        icon = {"danger": "warn", "warn": "warn"}.get(kind, "about")
        tk.Frame.__init__(self, parent, bg=wash)
        tk.Frame(self, bg=bar, width=S(3)).pack(side="left", fill="y")
        inner = tk.Frame(self, bg=wash)
        inner.pack(side="left", fill="both", expand=True, padx=S(12), pady=S(10))
        head = tk.Frame(inner, bg=wash)
        head.pack(fill="x")
        tk.Label(head, image=images.icon(icon, S(16), bar), bg=wash).pack(side="left")
        tk.Label(head, text=title, bg=wash, fg=bar, font=ctx.font(13, "bold")).pack(side="left", padx=(S(8), 0))
        self.body = tk.Label(inner, text=text, bg=wash, fg=p.ink, font=ctx.font(12), justify="left", anchor="w",
                             wraplength=wrap)
        self.body.pack(fill="x", pady=(S(4), 0))
        self.bind("<Configure>", lambda e: self.body.configure(wraplength=max(100, e.width - S(40))))


class Link(tk.Label):
    def __init__(self, parent, ctx, text, url, bg=None, size=12):
        self.ctx, self.url = ctx, url
        tk.Label.__init__(self, parent, text=text, bg=bg or ctx.pal.panel, fg=ctx.pal.accent,
                          font=ctx.font(size), cursor="hand2")
        self.bind("<Button-1>", lambda e: webbrowser.open(url))
        self.bind("<Enter>", lambda e: self.configure(font=self._uf()))
        self.bind("<Leave>", lambda e: self.configure(font=ctx.font(size)))
        self.size = size

    def _uf(self):
        import tkinter.font as tkfont
        f = tkfont.Font(font=self.ctx.font(self.size))
        f.configure(underline=True)
        self._f = f
        return f


def label(parent, ctx, text, size=13, color=None, weight="normal", bg=None, **kw):
    kw.setdefault("justify", "left")
    kw.setdefault("anchor", "center" if kw["justify"] == "center" else "w")
    lab = tk.Label(parent, text=text, bg=bg or ctx.pal.panel, fg=color or ctx.pal.ink,
                   font=ctx.font(size, weight), **kw)
    if kw.get("wraplength") and kw["justify"] != "center":
        autowrap(lab)
    return lab


def autowrap(lab, minimum=120):
    """Let a label wrap at the width its parent has room for, right of where it starts."""
    def update(_e=None):
        try:
            w = lab.master.winfo_width() - lab.winfo_x() - S(6)
            if w > minimum and abs(int(float(lab.cget("wraplength"))) - w) > 2:
                lab.configure(wraplength=w)
        except tk.TclError:
            pass
    lab.master.bind("<Configure>", update, add="+")
    lab.bind("<Map>", update, add="+")
    return lab


def section(parent, ctx, text, bg=None):
    return tk.Label(parent, text=spaced(text), bg=bg or ctx.pal.panel, fg=ctx.pal.muted,
                    font=ctx.font(10, "bold"), anchor="w")


def hline(parent, ctx, bg=None):
    return tk.Frame(parent, bg=ctx.pal.line, height=1)


class ScrollText(tk.Frame):
    """Read-only text with the theme's scroll bar."""

    def __init__(self, parent, ctx, height=12, bg=None, mono=False):
        p = ctx.pal
        bgc = bg or p.field
        tk.Frame.__init__(self, parent, bg=bgc)
        self.text = tk.Text(self, bg=bgc, fg=p.ink, bd=0, highlightthickness=0, wrap="word", height=height,
                            font=ctx.font(12, mono=mono), padx=S(12), pady=S(10), insertbackground=p.ink,
                            selectbackground=theme.mix(bgc, p.accent, 0.4), cursor="arrow")
        self.sb = ThinScrollbar(self, ctx, self.text.yview, bg=bgc)
        self.text.configure(yscrollcommand=self.sb.set)
        self.sb.pack(side="right", fill="y", padx=(0, S(3)), pady=S(4))
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("muted", foreground=p.muted)
        self.text.tag_configure("dim", foreground=p.dim)
        self.text.tag_configure("accent", foreground=p.accent)
        self.text.tag_configure("ok", foreground=p.ok)
        self.text.tag_configure("warn", foreground=p.warn)
        self.text.tag_configure("danger", foreground=p.danger)
        self.text.tag_configure("bold", font=ctx.font(12, "bold", mono=mono))
        self.text.tag_configure("head", font=ctx.font(13, "bold"), spacing1=S(6), spacing3=S(2))
        _wheel(self.text, lambda d: self.text.yview_scroll(d, "units"))

    def set(self, parts):
        """parts: a list of (text, tag or None)."""
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        for s, tag in parts:
            t.insert("end", s, tag or ())
        t.configure(state="disabled")


def _wheel(widget, scroll):
    """Mouse wheel for a widget on every system (scroll(+1/-1) per notch)."""
    def on_wheel(e):
        if e.num == 4:
            scroll(-3)
        elif e.num == 5:
            scroll(3)
        else:
            d = e.delta
            if theme.IS_MAC:
                scroll(-d)
            else:
                scroll(-3 * int(d / 120) if abs(d) >= 120 else (-1 if d > 0 else 1))
        return "break"
    widget.bind("<MouseWheel>", on_wheel, add="+")
    widget.bind("<Button-4>", on_wheel, add="+")
    widget.bind("<Button-5>", on_wheel, add="+")


wheel = _wheel


class Dialog(tk.Toplevel):
    """A modal window in the theme: a title, a body frame the caller fills, and buttons."""

    def __init__(self, app, title, width=560):
        ctx = app.ctx
        p = ctx.pal
        tk.Toplevel.__init__(self, app.root, bg=p.panel)
        self.app = app
        self.ctx = ctx
        self.result = None
        self.withdraw()
        self.title(title)
        self.transient(app.root)
        self.configure(highlightthickness=1, highlightbackground=p.panel_edge)
        self.resizable(True, True)
        try:
            self.iconphoto(False, app.icon_photo)
        except Exception:
            pass
        head = tk.Frame(self, bg=p.panel)
        head.pack(fill="x", padx=S(22), pady=(S(18), S(6)))
        tk.Label(head, text=title, bg=p.panel, fg=p.ink, font=ctx.font(18, "bold"), anchor="w").pack(side="left")
        self.body = tk.Frame(self, bg=p.panel)
        self.body.pack(fill="both", expand=True, padx=S(22), pady=S(6))
        self.buttons = tk.Frame(self, bg=p.panel)
        self.buttons.pack(fill="x", padx=S(22), pady=(S(8), S(18)))
        self.minsize(S(width), 1)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda e: self.cancel())

    def add_button(self, text, command, kind="secondary", icon=None):
        b = PillButton(self.buttons, self.ctx, text, command, kind, icon=icon)
        b.pack(side="right", padx=(S(10), 0))
        return b

    def show(self, wait=True):
        self.update_idletasks()
        r = self.app.root
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = r.winfo_rootx() + max(0, (r.winfo_width() - w) // 2)
        y = r.winfo_rooty() + max(0, (r.winfo_height() - h) // 3)
        self.geometry("+%d+%d" % (x, y))
        self.deiconify()
        self.lift()
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.focus_set()
        if wait:
            self.wait_window()
        return self.result

    def done(self, result):
        self.result = result
        self.close()

    def cancel(self):
        self.result = None
        self.close()

    def close(self):
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


def ask(app, title, text, ok="OK", cancel="Cancel", kind="primary", notice=None, confirm_text=None):
    """A yes/no question; with confirm_text, a box must be ticked before OK is allowed."""
    d = Dialog(app, title)
    ctx = app.ctx
    if notice:
        Notice(d.body, ctx, notice[0], notice[1], notice[2], wrap=S(520)).pack(fill="x", pady=(0, S(12)))
    label(d.body, ctx, text, 13, wraplength=S(520)).pack(fill="x")
    var = tk.BooleanVar(value=confirm_text is None)
    okb = None
    if confirm_text:
        sw = Switch(d.body, ctx, confirm_text, var, kind="check",
                    command=lambda: okb.enabled(bool(var.get())))
        sw.pack(anchor="w", pady=(S(14), 0))
    okb = d.add_button(ok, lambda: d.done(True) if var.get() else None, kind)
    okb.enabled(bool(var.get()))
    if cancel:
        d.add_button(cancel, d.cancel)
    return bool(d.show())


def tell(app, title, text, kind="note"):
    d = Dialog(app, title)
    if kind in ("danger", "warn"):
        Notice(d.body, app.ctx, kind, title, text, wrap=S(520)).pack(fill="x")
    else:
        label(d.body, app.ctx, text, 13, wraplength=S(520)).pack(fill="x")
    d.add_button("OK", lambda: d.done(True), "primary")
    d.show()


class ScrollFrame(tk.Frame):
    """A frame whose content scrolls when it is taller than the room it has (self.inner holds it)."""

    def __init__(self, parent, ctx, bg=None):
        bgc = bg or ctx.pal.panel
        tk.Frame.__init__(self, parent, bg=bgc)
        self.ctx = ctx
        self.c = tk.Canvas(self, bg=bgc, highlightthickness=0, bd=0)
        self.sb = ThinScrollbar(self, ctx, self.c.yview, bg=bgc)
        self.inner = tk.Frame(self.c, bg=bgc)
        self.win = self.c.create_window(0, 0, window=self.inner, anchor="nw")
        self.c.configure(yscrollcommand=self._sbset)
        self.c.pack(side="left", fill="both", expand=True)
        self._sb_on = False
        self.inner.bind("<Configure>", lambda e: self._update())
        self.c.bind("<Configure>", lambda e: self._update())
        self.bind("<Enter>", lambda e: self._wheel(True))
        self.bind("<Leave>", lambda e: self._wheel(False))

    def _sbset(self, a, b):
        self.sb.set(a, b)

    def _update(self):
        cw = self.c.winfo_width()
        self.c.itemconfigure(self.win, width=max(10, cw))
        ih = self.inner.winfo_reqheight()
        ch = self.c.winfo_height()
        self.c.configure(scrollregion=(0, 0, cw, max(ih, ch)))
        need = ih > ch + 1
        if need != self._sb_on:
            self._sb_on = need
            if need:
                self.sb.pack(side="right", fill="y", padx=(S(4), 0))
            else:
                self.sb.pack_forget()
                self.c.yview_moveto(0)

    def _wheel(self, on):
        root = self.c.winfo_toplevel()
        if on:
            def scroll(e):
                if not self._sb_on:
                    return
                if e.num == 4:
                    d = -2
                elif e.num == 5:
                    d = 2
                else:
                    d = -e.delta if theme.IS_MAC else -2 * int(e.delta / 120 or (1 if e.delta > 0 else -1))
                self.c.yview_scroll(d, "units")
            root.bind_all("<MouseWheel>", scroll)
            root.bind_all("<Button-4>", scroll)
            root.bind_all("<Button-5>", scroll)
        else:
            for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                root.unbind_all(seq)


class Flow(tk.Frame):
    """Children laid out left to right, wrapping onto new rows as the width needs."""

    def __init__(self, parent, bg, gap=8, vgap=8):
        tk.Frame.__init__(self, parent, bg=bg, height=1)
        self.gap, self.vgap = S(gap), S(vgap)
        self.items = []
        self.bind("<Configure>", lambda e: self._layout())

    def add(self, w):
        self.items.append(w)
        self._layout()

    def clear(self):
        for w in self.items:
            w.destroy()
        self.items = []

    def _layout(self):
        width = self.winfo_width()
        if width <= 1:
            width = 10000
        x = y = rowh = 0
        for w in self.items:
            ww, wh = w.winfo_reqwidth(), w.winfo_reqheight()
            if x and x + ww > width:
                x, y = 0, y + rowh + self.vgap
                rowh = 0
            w.place(x=x, y=y)
            x += ww + self.gap
            rowh = max(rowh, wh)
        h = y + rowh
        if int(self.cget("height")) != h:
            self.configure(height=max(1, h))
