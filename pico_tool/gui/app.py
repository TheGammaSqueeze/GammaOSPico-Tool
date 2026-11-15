"""The main window: the backdrop, the header and tabs, the pages, and the worker threads.

All card, network and console work runs on worker threads; their results come back to the window
through a queue the window empties every few milliseconds (Tk is used from its own thread only).
"""

import os
import queue
import sys
import threading
import traceback
import tkinter as tk
import tkinter.font as tkfont

from PIL import ImageTk

from .. import APP_NAME, __version__, config
from . import backdrop, images, theme, widgets
from .theme import S


def _dpi_awareness():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


class Page(object):
    """A tab of the window. Subclasses make their panels' frames in build() and say where the panels
    go in panels(x, y, w, h)."""
    name = ""
    icon = ""
    title = ""

    def __init__(self, app):
        self.app = app
        self.ctx = app.ctx
        self.pal = app.ctx.pal
        self.frames = []
        self._wins = {}
        self.built = False

    def build(self):
        pass

    def panels(self, x, y, w, h):
        return []

    def frame(self):
        f = tk.Frame(self.app.canvas, bg=self.pal.panel)
        self.frames.append(f)
        return f

    def on_show(self):
        pass

    def on_hide(self):
        pass

    def on_card(self):
        """The chosen card changed (or its contents)."""

    def on_console_model(self):
        """The person's console changed (picked in Settings or the Card tab, or seen on USB)."""


class App(object):
    def __init__(self, root=None, settings=None):
        _dpi_awareness()
        images.clear()          # pictures belong to one Tk interpreter
        self.root = root or tk.Tk()
        self.root.withdraw()
        self.settings = settings or config.Settings()
        dpi = self.root.winfo_fpixels("1i")
        theme.set_scale(dpi / 96.0 if dpi > 100 else 1.0)
        fams = tkfont.families(self.root)
        self.family = theme.pick_family(fams)
        self.mono = theme.pick_mono(fams)
        self.q = queue.Queue()
        self.alive = True
        self.card = None            # the chosen Volume
        self.session = None         # services.CardSession of the chosen card
        self.release = None         # the latest release, once looked up
        self._bd_job = None
        self._bd_key = None
        self._base = None
        self._base_key = None
        self._composed = {}
        self._bg_item = None
        self._render_lock = threading.Lock()
        self.root.title(APP_NAME)
        self._setup_theme()
        self.icon_photo = ImageTk.PhotoImage(images.icon_image("logo", 64, self.pal.accent, 1.8))
        try:
            self.root.iconphoto(True, self.icon_photo)
        except tk.TclError:
            pass
        geo = self.settings.get("geometry") or ""
        self.root.geometry(geo if geo else "%dx%d" % (S(1240), S(800)))
        self.root.minsize(S(940), S(640))
        self.canvas = tk.Canvas(self.root, bg="#071026", highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_resize)
        self.pages = []
        self.page = None
        self._make_pages()
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self.root.after(30, self._pump)
        self.root.deiconify()
        self.show(self.pages[0])

    # ---- theme -------------------------------------------------------------------------------
    def _setup_theme(self):
        self.pal = theme.Palette(self.settings.get("accent"))
        self.ctx = widgets.Ctx(self.root, self.pal, self.family, self.mono)
        self.root.configure(bg=self.pal.panel)
        self.root.option_add("*Menu.background", self.pal.field)
        self.root.option_add("*Menu.foreground", self.pal.ink)
        self.root.option_add("*Menu.activeBackground", theme.mix(self.pal.field, self.pal.accent, 0.35))

    def set_accent(self, name):
        if name == self.pal.name:
            return
        self.settings.set("accent", name)
        cur = self.pages.index(self.page) if self.page in self.pages else 0
        for p in self.pages:
            if p.built:
                p.on_hide()
            for f in p.frames:
                f.destroy()
        self.canvas.delete("all")
        self._bg_item = None
        images.clear()
        self._composed.clear()
        self._base = None
        self._base_key = None
        self._setup_theme()
        self.icon_photo = ImageTk.PhotoImage(images.icon_image("logo", 64, self.pal.accent, 1.8))
        self.page = None
        self._make_pages()
        self.show(self.pages[cur])

    def _make_pages(self):
        from .pages import AboutPage, CardPage, ConsolePage, GamesPage, SettingsPage
        self.pages = [CardPage(self), GamesPage(self), ConsolePage(self), SettingsPage(self), AboutPage(self)]

    # ---- threads -----------------------------------------------------------------------------
    def post(self, fn, *args):
        """Run fn(*args) on the window's thread (callable from any thread)."""
        self.q.put((fn, args))

    def _pump(self):
        try:
            for _ in range(200):
                fn, args = self.q.get_nowait()
                try:
                    fn(*args)
                except Exception:
                    traceback.print_exc()
        except queue.Empty:
            pass
        if self.alive:
            self.root.after(25, self._pump)

    def run_bg(self, work, done=None, fail=None, name="work"):
        """work() on a worker thread; done(result) or fail(exception) back on the window's thread."""
        def target():
            try:
                r = work()
            except BaseException as e:
                if fail:
                    self.post(fail, e)
                else:
                    traceback.print_exc()
                    self.post(self.status, "Something went wrong: %s" % e)
                return
            if done:
                self.post(done, r)
        t = threading.Thread(target=target, name=name, daemon=True)
        t.start()
        return t

    # ---- layout ------------------------------------------------------------------------------
    def margin(self):
        w = max(1, self.canvas.winfo_width())
        return max(S(20), min(S(46), int(w * 0.035)))

    def content_rect(self):
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        m = self.margin()
        top = S(150)
        return m, top, w - 2 * m, h - top - S(44)

    def _on_resize(self, e):
        if self._bd_job:
            self.root.after_cancel(self._bd_job)
        self._bd_job = self.root.after(140, self.relayout)
        self._draw_chrome()
        self._place_frames()

    def relayout(self):
        self._bd_job = None
        self._draw_chrome()
        self._place_frames()
        self._request_backdrop()

    def _panel_rects(self):
        if not self.page:
            return []
        x, y, w, h = self.content_rect()
        return [tuple(int(v) for v in r) for r, _f in self.page.panels(x, y, w, h)]

    def _place_frames(self):
        if not self.page:
            return
        x, y, w, h = self.content_rect()
        pad = S(14)
        seen = set()
        for (rx, ry, rw, rh), f in self.page.panels(x, y, w, h):
            wid = self.page._wins.get(f)
            if wid is None:
                wid = self.canvas.create_window(0, 0, window=f, anchor="nw")
                self.page._wins[f] = wid
            self.canvas.coords(wid, rx + pad, ry + pad)
            self.canvas.itemconfigure(wid, width=max(10, rw - 2 * pad), height=max(10, rh - 2 * pad), state="normal")
            seen.add(f)
        for f, wid in self.page._wins.items():
            if f not in seen:
                self.canvas.itemconfigure(wid, state="hidden")

    def _request_backdrop(self):
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if w < 50 or h < 50:
            return
        rects = tuple(self._panel_rects())
        accent = self.pal.name
        key = (w, h, accent, rects)
        if key == self._bd_key:
            return
        self._bd_key = key
        hit = self._composed.get(key)
        if hit is not None:
            self._set_backdrop(key, hit)
            return
        pal = self.pal

        def work():
            with self._render_lock:
                bkey = (w, h, accent)
                if self._base_key != bkey:
                    self._base = backdrop.render(w, h, accent, reading=True)
                    self._base_key = bkey
                base = self._base
            return backdrop.panels(base, list(rects), pal.panel, pal.panel_edge, pal.shadow, S(16))

        def done(img):
            if len(self._composed) > 8:
                self._composed.clear()
            self._composed[key] = img
            if self._bd_key == key:
                self._set_backdrop(key, img)
        self.run_bg(work, done, name="backdrop")

    def _set_backdrop(self, key, img):
        self._bg_photo = ImageTk.PhotoImage(img)
        if self._bg_item is None:
            self._bg_item = self.canvas.create_image(0, 0, image=self._bg_photo, anchor="nw", tags="bg")
        else:
            self.canvas.itemconfigure(self._bg_item, image=self._bg_photo)
        self.canvas.tag_lower("bg")

    # ---- header, tabs, footer ----------------------------------------------------------------
    def _draw_chrome(self):
        c = self.canvas
        c.delete("chrome")
        p = self.pal
        w, h = c.winfo_width(), c.winfo_height()
        m = self.margin()
        y = S(40)
        self._logo = images.icon("logo", S(26), p.ink, 1.9)
        c.create_image(m, y, image=self._logo, anchor="w", tags="chrome")
        fb = self.ctx.font(16, "bold")
        x = m + S(36)
        c.create_text(x, y, text="GammaOS", fill=p.ink, font=fb, anchor="w", tags="chrome")
        x += fb.measure("GammaOS") + S(6)
        fl = self.ctx.font(16)
        c.create_text(x, y, text="Pico Tool", fill=p.muted, font=fl, anchor="w", tags="chrome")
        x += fl.measure("Pico Tool") + S(16)
        c.create_line(x, y - S(10), x, y + S(10), fill=theme.mix(p.muted, "#000000", 0.45), tags="chrome")
        x += S(16)
        c.create_text(x, y, text=self.page.title if self.page else "", fill=p.ink, font=self.ctx.font(14),
                      anchor="w", tags="chrome")
        # the card at the right
        right = w - m
        card = self.card
        if card is None:
            txt, col, dot = "No card chosen", p.dim, None
        else:
            name = card.label or card.path
            bits = [name, card.fs_label]
            if card.has_pico:
                bits.append("GammaOS Pico " + (card.version or ""))
            txt = "   ·   ".join(b.strip() for b in bits if b)
            col = p.ink
            dot = p.ok if card.has_pico and card.fs_ok else (p.warn if card.fs_ok else p.danger)
        fr = self.ctx.font(13)
        c.create_text(right, y, text=txt, fill=col, font=fr, anchor="e", tags="chrome")
        if dot:
            tx = right - fr.measure(txt) - S(14)
            self._dot = images.pill(S(9), S(9), dot)
            c.create_image(tx, y, image=self._dot, anchor="center", tags="chrome")
            self._card_icon = images.icon("card", S(18), p.muted)
            c.create_image(tx - S(12), y, image=self._card_icon, anchor="e", tags="chrome")
        # tabs
        ty = S(106)
        x = m
        self._tab_imgs = []
        for pg in self.pages:
            on = pg is self.page
            f = self.ctx.font(14, "normal")
            lw = f.measure(pg.name)
            tag = "tab_%s" % pg.name
            if on:
                gl = images.glow(p.accent, S(64))
                self._tab_imgs.append(gl)
                c.create_image(x + S(12), ty, image=gl, anchor="center", tags=("chrome", tag))
            ic = images.icon(pg.icon, S(22), p.ink if on else p.muted, 1.6)
            self._tab_imgs.append(ic)
            c.create_image(x, ty, image=ic, anchor="w", tags=("chrome", tag))
            c.create_text(x + S(32), ty, text=pg.name, fill=p.ink if on else p.muted, font=f, anchor="w",
                          tags=("chrome", tag))
            full = S(32) + lw
            if on:
                c.create_rectangle(x, ty + S(21), x + full, ty + S(21) + max(2, S(2)), fill=p.accent, outline="",
                                   tags="chrome")
            c.create_rectangle(x - S(6), ty - S(20), x + full + S(6), ty + S(24), fill="", outline="",
                               tags=("chrome", tag))
            c.tag_bind(tag, "<Button-1>", lambda e, pg=pg: self.show(pg))
            c.tag_bind(tag, "<Enter>", lambda e: c.configure(cursor="hand2"))
            c.tag_bind(tag, "<Leave>", lambda e: c.configure(cursor=""))
            x += full + S(40)
        # footer
        fy = h - S(22)
        c.create_text(m, fy, text=getattr(self, "_status", "") or "", fill=p.muted, font=self.ctx.font(12),
                      anchor="w", tags=("chrome", "status"))
        c.create_text(w - m, fy, text="%s %s" % (APP_NAME, __version__), fill=p.dim, font=self.ctx.font(11),
                      anchor="e", tags="chrome")
        c.tag_raise("chrome")

    def status(self, text):
        self._status = text
        try:
            self.canvas.itemconfigure("status", text=text)
        except tk.TclError:
            pass

    # ---- pages -------------------------------------------------------------------------------
    def show(self, page):
        if self.page is page:
            return
        if self.page:
            self.page.on_hide()
            for wid in self.page._wins.values():
                self.canvas.itemconfigure(wid, state="hidden")
        self.page = page
        if not page.built:
            page.build()
            page.built = True
        self._draw_chrome()
        self._place_frames()
        self._request_backdrop()
        page.on_show()

    def show_name(self, name):
        for p in self.pages:
            if p.name == name:
                self.show(p)

    def refresh_layout(self):
        """A page changed its panels (shown or hidden ones)."""
        self._place_frames()
        self._request_backdrop()

    def set_card(self, vol):
        from ..services import CardSession
        same = self.card is not None and vol is not None and os.path.normcase(self.card.path) == os.path.normcase(vol.path)
        self.card = vol
        if vol is None:
            self.session = None
        elif not same or self.session is None:
            self.session = CardSession(vol.path, self.settings)
            self.settings.set("last_card", vol.path)
        self._draw_chrome()
        for p in self.pages:
            if p.built:
                p.on_card()

    def console_model(self):
        """The person's console (models.ConsoleModel): the one picked or last seen, the Pocket
        Advance until then. It chooses which console's packages are downloaded."""
        from ..console.models import by_key
        return by_key(self.settings.get("console_model"))

    def set_console_model(self, model):
        if model is None or self.settings.get("console_model") == model.key:
            return
        self.settings.set("console_model", model.key)
        for p in self.pages:
            if p.built:
                p.on_console_model()

    def quit(self):
        try:
            if self.root.state() == "normal":
                self.settings.set("geometry", self.root.geometry())
        except tk.TclError:
            pass
        self.alive = False
        if self.session:
            self.session.save_caches()
        for p in self.pages:
            try:
                if p.built:
                    p.on_hide()
            except Exception:
                pass
        self.root.destroy()


def main(argv=None):
    app = App()
    app.root.mainloop()
    return 0
