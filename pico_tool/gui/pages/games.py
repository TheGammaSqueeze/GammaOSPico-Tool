"""The Games tab: the games on the card exactly as the console lists them, and their box art."""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog

from PIL import Image, ImageTk

from ...art import artfile, store
from ...card import archives
from ...card.hashing import game_crc
from ...card.names import fold
from ...card.systems import SYSTEMS
from ...scrape import batch, scraper
from .. import images, theme, widgets
from ..app import Page
from ..gamelist import GameList
from ..theme import S
from .card import human

THUMB = 42


class Thumbs(object):
    """Cover thumbnails decoded on a worker thread, the latest asked for first."""

    def __init__(self, app, on_ready):
        self.app = app
        self.on_ready = on_ready
        self.cache = {}             # key -> PhotoImage (None: no cover)
        self.order = []
        self.pending = set()
        self.q = queue.LifoQueue()
        self.limit = 900
        self.gen = 0
        t = threading.Thread(target=self._run, daemon=True, name="thumbs")
        t.start()

    def reset(self):
        self.gen += 1
        self.cache.clear()
        self.order = []
        self.pending.clear()

    def get(self, key, path):
        if key in self.cache:
            return self.cache[key]
        if path and key not in self.pending:
            self.pending.add(key)
            self.q.put((self.gen, key, path))
        return None

    def forget(self, key):
        self.cache.pop(key, None)
        self.pending.discard(key)

    def _run(self):
        size = S(THUMB)
        while True:
            gen, key, path = self.q.get()
            if gen != self.gen:
                continue
            try:
                if path.lower().endswith(".art"):
                    im = artfile.decode_file(path)
                else:
                    im = Image.open(path).convert("RGB")
                im.thumbnail((size, size), Image.LANCZOS)
            except Exception:
                im = None
            self.app.post(self._done, gen, key, im)

    def _done(self, gen, key, im):
        if gen != self.gen:
            return
        self.pending.discard(key)
        self.cache[key] = ImageTk.PhotoImage(im) if im is not None else None
        self.order.append(key)
        if len(self.order) > self.limit:
            old = self.order.pop(0)
            self.cache.pop(old, None)
        self.on_ready()


class GamesPage(Page):
    name = "Games"
    icon = "games"
    title = "Games and box art"

    def build(self):
        ctx, p = self.ctx, self.pal
        self.left = self.frame()
        self.right = self.frame()
        self.result = None
        self.states = {}            # game index -> (state, path)
        self.system = None          # None all, else a type
        self.query = tk.StringVar()
        self.missing_only = tk.BooleanVar(value=False)
        self.scanning = False
        self.scrape_cancel = None
        self.scraping = False
        self.current = []
        self._redraw_job = None
        self.thumbs = Thumbs(self.app, self._thumb_ready)
        # ---- the list side
        self.chips = widgets.Flow(self.left, p.panel)
        self.chips.pack(fill="x")
        bar = tk.Frame(self.left, bg=p.panel)
        bar.pack(fill="x", pady=(S(12), S(8)))
        widgets.PillButton(bar, ctx, "", self.rescan, "ghost", icon="refresh", small=True).pack(side="right", padx=(S(8), 0))
        widgets.Switch(bar, ctx, "Missing covers only", self.missing_only, command=self.apply_filter).pack(side="right", padx=(S(16), 0))
        self.search = widgets.Field(bar, ctx, self.query, placeholder="Search the games", icon="search", width=12)
        self.search.pack(side="left", fill="x", expand=True)
        self.query.trace_add("write", lambda *a: self._filter_soon())
        self.search.entry.bind("<Escape>", lambda e: self.query.set(""))
        self.search.entry.bind("<Down>", lambda e: (self.list.c.focus_set(), self.list.select_index(0)))
        self.hay = {}
        self.count = widgets.label(self.left, ctx, "", 12, color=p.muted)
        self.count.pack(fill="x", pady=(0, S(6)))
        self.bottom = tk.Frame(self.left, bg=p.panel)
        self.bottom.pack(fill="x", pady=(S(10), 0), side="bottom")
        self.list = GameList(self.left, ctx, thumb=self._thumb, describe=self._describe, on_select=self._selected,
                             on_open=lambda g: self.find_cover())
        self.list.pack(fill="both", expand=True)
        self._bottom_idle()
        # ---- the detail side: its buttons pinned at the foot, the rest scrolls
        self.dbtns = tk.Frame(self.right, bg=p.panel)
        self.dbtns.pack(fill="x", side="bottom")
        self.dscroll = widgets.ScrollFrame(self.right, ctx)
        self.dscroll.pack(fill="both", expand=True)
        self.detail = self.dscroll.inner
        self._detail_empty("Pick a game to see its cover.")

    def panels(self, x, y, w, h):
        rw = max(S(300), min(S(380), int(w * 0.30)))
        gap = S(18)
        return [((x, y, w - rw - gap, h), self.left), ((x + w - rw, y, rw, h), self.right)]

    # ---- card and scanning -----------------------------------------------------------------------
    def on_show(self):
        self.app.root.bind("<%s-f>" % theme.MOD, lambda e: self.search.entry.focus_set())
        s = self.app.session
        if s is None:
            self.list.empty_text = "Choose a card on the Card tab first."
            self.list.set_items([])
            self._chips()
            self.count.configure(text="")
        elif s.result is None and not self.scanning:
            self.rescan()

    def on_hide(self):
        try:
            self.app.root.unbind("<%s-f>" % theme.MOD)
        except tk.TclError:
            pass

    def on_card(self):
        self.result = None
        self.states = {}
        self.thumbs.reset()
        if self.app.page is self:
            self.on_show()

    def rescan(self):
        s = self.app.session
        if s is None or self.scanning:
            return
        self.scanning = True
        self.list.empty_text = "Reading the card..."
        self.list.set_items([])
        self.app.status("Reading the games on the card...")

        def work():
            res = s.scan(progress=lambda m: self.app.post(self.app.status, m))
            states = {g.index: s.index.find(g) for g in res.games}
            return res, states

        def done(r):
            self.scanning = False
            self.result, self.states = r
            # the console's search folds accents and treats a decomposed name like a composed one
            self.hay = {g.index: fold(g.display_name + " " + g.title + " " + os.path.basename(g.file))
                        for g in self.result.games}
            self.thumbs.reset()
            res = self.result
            with_art = sum(1 for st in self.states.values() if st[0] == store.HAVE_ART)
            msg = "%d games on the card, %d with a cover." % (len(res.games), with_art)
            if res.total > len(res.games):
                msg += " The console lists %d of the %d found (its limit)." % (len(res.games), res.total)
            if res.skipped_folders:
                msg += " %d game folders are past the 64 it reads." % res.skipped_folders
            self.app.status(msg)
            self._chips()
            self.apply_filter()

        def fail(e):
            self.scanning = False
            self.list.empty_text = "The card could not be read: %s" % e
            self.list.set_items([])
            self.app.status(str(e))
        self.app.run_bg(work, done, fail, name="scan")

    # ---- filters ---------------------------------------------------------------------------------
    def _chips(self):
        ctx = self.ctx
        self.chips.clear()
        counts = self.result.counts() if self.result else {}
        total = len(self.result.games) if self.result else 0
        options = [(None, "All", total)] + [(s.type, s.short, counts.get(s.type, 0)) for s in SYSTEMS if counts.get(s.type)]
        for t, lab, n in options:
            on = t == self.system
            b = widgets.PillButton(self.chips, ctx, "%s  %d" % (lab, n), lambda t=t: self.set_system(t),
                                   "accent" if on else "secondary", small=True)
            self.chips.add(b)

    def set_system(self, t):
        self.system = t
        self._chips()
        self.apply_filter()

    def _filter_soon(self):
        if getattr(self, "_fjob", None):
            self.app.root.after_cancel(self._fjob)
        self._fjob = self.app.root.after(160, self.apply_filter)

    def apply_filter(self):
        self._fjob = None
        if not self.result:
            return
        words = fold(self.query.get().strip()).split()
        out = []
        for g in self.result.games:
            if self.system is not None and g.type != self.system:
                continue
            if self.missing_only.get() and self.states.get(g.index, (None,))[0] != store.NO_ART:
                continue
            if words:
                hay = self.hay.get(g.index, "")
                if not all(wd in hay for wd in words):
                    continue
            out.append(g)
        self.current = out
        if not out and self.result.games:
            self.list.empty_text = "No game matches. Try other words, another system, or turn off \"Missing covers only\"."
        elif not self.result.games:
            self.list.empty_text = ("No games on this card yet. Copy them into roms/<system>/ (see the wiki's Adding "
                                    "Games page), then press the refresh button.")
        self.list.set_items(out)
        have = sum(1 for g in out if self.states.get(g.index, (None,))[0] == store.HAVE_ART)
        self.count.configure(text="%d games  ·  %d with a cover  ·  %d without" % (len(out), have, len(out) - have))
        self._bottom_idle()

    # ---- list rows -------------------------------------------------------------------------------
    def _thumb(self, g):
        st, path = self.states.get(g.index, (store.NO_ART, None))
        if st not in (store.HAVE_ART, store.HAVE_PNG):
            return None
        return self.thumbs.get(g.index, path)

    def _thumb_ready(self):
        if self._redraw_job is None:
            self._redraw_job = self.app.root.after(40, self._redraw_now)

    def _redraw_now(self):
        self._redraw_job = None
        self.list.redraw()

    def _describe(self, g):
        bits = [g.system.short]
        if g.arc:
            bits.append(os.path.basename(g.file))
        bits.append(g.cdir)
        sub = "  ·  ".join(bits)
        st = self.states.get(g.index, (store.NO_ART, None))[0]
        if g.status != archives.OK:
            return g.title, sub, "cannot start", "danger"
        if st == store.HAVE_ART:
            return g.title, sub, "", ""
        if st == store.HAVE_PNG:
            return g.title, sub, "PNG waiting", "accent"
        if st == store.CANNOT:
            return g.title, sub, "no cover possible", "warn"
        return g.title, sub, "no cover", "dim"

    # ---- the detail panel ------------------------------------------------------------------------
    def _selected(self, games):
        if self.scraping:
            return
        if not games:
            self._detail_empty("Pick a game to see its cover.")
        elif len(games) == 1:
            self._detail_one(games[0])
        else:
            self._detail_many(games)
        self._bottom_idle()

    def _clear_detail(self):
        for w in self.detail.winfo_children():
            w.destroy()
        for w in self.dbtns.winfo_children():
            w.destroy()

    def _detail_empty(self, text):
        self._clear_detail()
        p = self.pal
        box = tk.Frame(self.detail, bg=p.panel)
        box.pack(pady=(S(110), 0))
        tk.Label(box, image=images.icon("image", S(48), theme.mix(p.muted, p.panel, 0.4), 1.3), bg=p.panel).pack()
        widgets.label(box, self.ctx, text, 13, color=p.muted, wraplength=S(260), justify="center").pack(pady=(S(10), 0))

    def _cover_box(self, parent, g):
        p = self.pal
        h = self.right.winfo_height()
        size = S(230) if h < 50 else max(S(150), min(S(250), h - S(330)))
        c = tk.Canvas(parent, width=size, height=size, bg=p.panel, highlightthickness=0, bd=0)
        self._box_bg = images.pill(size, size, p.field, None, radius=S(14))
        c.create_image(0, 0, image=self._box_bg, anchor="nw")
        st, path = self.states.get(g.index, (store.NO_ART, None))
        img = None
        if st in (store.HAVE_ART, store.HAVE_PNG) and path:
            try:
                im = artfile.decode_file(path) if path.lower().endswith(".art") else Image.open(path).convert("RGB")
                img = ImageTk.PhotoImage(images.fit(im, size - S(24), size - S(24)))
            except Exception:
                img = None
        if img is not None:
            self._cover_img = img
            c.create_image(size // 2, size // 2, image=img)
        else:
            c.create_image(size // 2, size // 2 - S(14), image=images.icon("box", S(64), theme.mix(p.muted, p.field, 0.5), 1.2))
            c.create_text(size // 2, size // 2 + S(40), text="No cover" if st != store.CANNOT else "No cover possible",
                          fill=p.dim, font=self.ctx.font(12))
        return c

    def _detail_one(self, g):
        self._clear_detail()
        ctx, p = self.ctx, self.pal
        d = self.detail
        self._cover_box(d, g).pack(anchor="center", pady=(S(4), S(14)))
        widgets.autowrap(widgets.label(d, ctx, g.title, 17, weight="bold", wraplength=S(280))).pack(fill="x")
        widgets.label(d, ctx, g.system.label, 12, color=p.accent).pack(fill="x", pady=(S(2), S(10)))
        info = tk.Frame(d, bg=p.panel)
        info.pack(fill="x")
        info.columnconfigure(1, weight=1)
        rows = [("Name", g.display_name)]
        if g.arc:
            rows.append(("Archive", os.path.basename(g.file)))
            if g.member_text and not g.by_file:
                rows.append(("Inside", g.member_text))
        else:
            rows.append(("File", os.path.basename(g.file)))
        rows.append(("Folder", g.cdir))
        rows.append(("Size", human(g.size) if g.size else "unknown"))
        self.crc_label = None
        st, path = self.states.get(g.index, (store.NO_ART, None))
        cover = {store.HAVE_ART: "on the card", store.HAVE_PNG: "a PNG the console converts at the next power on",
                 store.NO_ART: "none", store.CANNOT: "the console cannot match a cover to this name"}[st]
        for i, (k, v) in enumerate(rows + [("CRC32", ""), ("Cover", cover)]):
            widgets.label(info, ctx, k, 11, color=p.dim).grid(row=i, column=0, sticky="nw", padx=(0, S(12)), pady=S(2))
            lab = widgets.label(info, ctx, v, 11, color=p.muted, wraplength=S(200))
            lab.grid(row=i, column=1, sticky="w", pady=S(2))
            if k == "CRC32":
                self.crc_label = lab
        self._load_crc(g)
        if g.status != archives.OK:
            widgets.Notice(d, ctx, "danger", "The console will not start it", archives.REASONS.get(g.status, ""),
                           wrap=S(300)).pack(fill="x", pady=(S(10), 0))
        btns = self.dbtns
        can = st != store.CANNOT
        b = widgets.PillButton(btns, ctx, "Find a cover...", self.find_cover, "primary", icon="search", stretch=True)
        b.pack(fill="x", pady=(S(8), 0))
        b.enabled(can)
        b = widgets.PillButton(btns, ctx, "From a picture file...", self.cover_from_file, "secondary", icon="image",
                               stretch=True)
        b.pack(fill="x", pady=(S(8), 0))
        b.enabled(can)
        row = tk.Frame(btns, bg=p.panel)
        row.pack(fill="x", pady=(S(8), 0))
        b = widgets.PillButton(row, ctx, "Remove the cover", self.remove_cover, "ghost", icon="trash", small=True)
        b.pack(side="left")
        b.enabled(st in (store.HAVE_ART, store.HAVE_PNG))
        widgets.PillButton(row, ctx, "Show the file", lambda: _reveal(g.file), "ghost", icon="folder",
                           small=True).pack(side="right")

    def _load_crc(self, g):
        if g.crc is not None:
            self.crc_label.configure(text="%08X%s" % (g.crc, " (from the archive)" if g.arc else ""))
            return
        if g.arc:
            self.crc_label.configure(text="not in the archive's tables")
            return
        self.crc_label.configure(text="working it out...")
        s = self.app.session
        lab = self.crc_label

        def done(crc):
            try:
                lab.configure(text=("%08X" % crc) if crc is not None else "could not be read")
            except tk.TclError:
                pass
        self.app.run_bg(lambda: game_crc(g, s.crc_cache if s else None), done)

    def _detail_many(self, games):
        self._clear_detail()
        ctx, p = self.ctx, self.pal
        d = self.detail
        tk.Label(d, image=images.icon("games", S(48), theme.mix(p.muted, p.panel, 0.3), 1.3), bg=p.panel).pack(pady=(S(80), S(10)))
        widgets.label(d, ctx, "%d games selected" % len(games), 17, weight="bold", justify="center").pack()
        n = sum(1 for g in games if self.states.get(g.index, (None,))[0] == store.NO_ART)
        widgets.label(d, ctx, "%d of them without a cover" % n, 12, color=p.muted, justify="center").pack(pady=(S(4), 0))
        btns = self.dbtns
        widgets.PillButton(btns, ctx, "Find their missing covers", lambda: self.scrape(games, False), "primary",
                           icon="download", stretch=True).pack(fill="x", pady=(S(8), 0))
        widgets.PillButton(btns, ctx, "Find new covers for all of them", lambda: self.scrape(games, True),
                           "secondary", stretch=True).pack(fill="x", pady=(S(8), 0))
        widgets.PillButton(btns, ctx, "Remove their covers", lambda: self.remove_many(games), "ghost",
                           icon="trash", small=True).pack(anchor="w", pady=(S(8), 0))

    # ---- the bottom bar --------------------------------------------------------------------------
    def _bottom_idle(self):
        if self.scraping or not hasattr(self, "bottom"):
            return
        for w in self.bottom.winfo_children():
            w.destroy()
        ctx, p = self.ctx, self.pal
        games = self.current
        n = sum(1 for g in games if self.states.get(g.index, (None,))[0] == store.NO_ART)
        label = next((k for k, v in scraper.SOURCES if v == self.app.settings.get("scrape_source")), "ScreenScraper")
        b = widgets.PillButton(self.bottom, ctx, "Find missing covers (%d)" % n, lambda: self.scrape(games, False),
                               "primary", icon="download")
        b.pack(side="left")
        b.enabled(n > 0 and self.result is not None)
        widgets.label(self.bottom, ctx, "from %s" % label, 12, color=p.dim).pack(side="left", padx=(S(12), 0))

    def _bottom_busy(self):
        for w in self.bottom.winfo_children():
            w.destroy()
        ctx, p = self.ctx, self.pal
        top = tk.Frame(self.bottom, bg=p.panel)
        top.pack(fill="x")
        self.sc_text = widgets.label(top, ctx, "Starting...", 12, color=p.muted)
        self.sc_text.pack(side="left", fill="x", expand=True)
        widgets.PillButton(top, ctx, "Stop", lambda: self.scrape_cancel and self.scrape_cancel.set(), "secondary",
                           small=True).pack(side="right")
        self.sc_bar = widgets.Progress(self.bottom, ctx)
        self.sc_bar.pack(fill="x", pady=(S(6), 0))
        self.sc_bar.set(None)

    # ---- covers ----------------------------------------------------------------------------------
    def _one(self):
        sel = self.list.selected()
        return sel[0] if len(sel) == 1 else None

    def _refresh_game(self, g):
        s = self.app.session
        self.states[g.index] = s.index.find(g)
        self.thumbs.forget(g.index)
        self.list.redraw()
        if self._one() is g:
            self._detail_one(g)
        self._update_counts()

    def _update_counts(self):
        out = self.current
        have = sum(1 for g in out if self.states.get(g.index, (None,))[0] == store.HAVE_ART)
        self.count.configure(text="%d games  ·  %d with a cover  ·  %d without" % (len(out), have, len(out) - have))
        self._bottom_idle()

    def cover_from_file(self):
        g = self._one()
        if not g:
            return
        f = filedialog.askopenfilename(parent=self.app.root, title="A picture for %s" % g.title,
                                       filetypes=[("Pictures", "*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff"),
                                                  ("All files", "*")])
        if not f:
            return
        s = self.app.session

        def work():
            return store.write_art(s.index, g, artfile.encode_file(f))
        self.app.run_bg(work, lambda path: (self.app.status("Cover written: %s" % path), self._refresh_game(g)),
                        lambda e: widgets.tell(self.app, "The cover was not written", str(e), "danger"))

    def remove_cover(self):
        g = self._one()
        if not g:
            return
        if not widgets.ask(self.app, "Remove the cover?", "Remove the cover of %s from the card?" % g.title,
                           "Remove", kind="danger"):
            return
        s = self.app.session
        self.app.run_bg(lambda: store.remove_art(s.index, g),
                        lambda gone: (self.app.status("Removed %d file(s)." % len(gone)), self._refresh_game(g)))

    def remove_many(self, games):
        if not widgets.ask(self.app, "Remove %d covers?" % len(games),
                           "Remove the covers of the %d selected games from the card?" % len(games), "Remove",
                           kind="danger"):
            return
        s = self.app.session

        def work():
            n = 0
            for g in games:
                n += len(store.remove_art(s.index, g))
            return n

        def done(n):
            for g in games:
                self.states[g.index] = s.index.find(g)
                self.thumbs.forget(g.index)
            self.app.status("Removed %d file(s)." % n)
            self.list.redraw()
            self._selected(self.list.selected())
            self._update_counts()
        self.app.run_bg(work, done)

    def find_cover(self):
        g = self._one()
        if not g:
            return
        sc = scraper.make_scraper(self.app.settings.data)
        if not sc.ready:
            widgets.tell(self.app, "No scraper ready", sc.unready_reason())
            return
        from ..search import SearchDialog
        d = SearchDialog(self.app, g, sc)
        data = d.show()
        if not data:
            return
        s = self.app.session
        self.app.run_bg(lambda: store.write_art(s.index, g, artfile.encode_bytes(data)),
                        lambda path: (self.app.status("Cover written: %s" % path), self._refresh_game(g)),
                        lambda e: widgets.tell(self.app, "The cover was not written", str(e), "danger"))

    def scrape(self, games, overwrite):
        if self.scraping or not games:
            return
        sc = scraper.make_scraper(self.app.settings.data)
        if not sc.ready:
            widgets.tell(self.app, "No scraper ready", sc.unready_reason())
            return
        s = self.app.session
        if overwrite and not widgets.ask(self.app, "Replace their covers?",
                                         "Covers already on the card for these %d games are replaced by what %s "
                                         "finds." % (len(games), sc.label), "Replace them"):
            return
        self.scraping = True
        self.scrape_cancel = threading.Event()
        cancel = self.scrape_cancel
        self._bottom_busy()

        def prog(i, n, g, msg):
            self.app.post(self._scrape_progress, i, n, g, msg)

        def work():
            return batch.run(games, sc, s.index, overwrite=overwrite, progress=prog, cancel=cancel,
                             crc_cache=s.crc_cache)

        def done(out):
            self.scraping = False
            s.save_caches()
            for g in games:
                self.states[g.index] = s.index.find(g)
                self.thumbs.forget(g.index)
            self.list.redraw()
            self._bottom_idle()
            self._selected(self.list.selected())
            self._update_counts()
            text = out.summary() + "."
            if out.cancelled:
                text = "Stopped. " + text
            self.app.status(text)
            if out.stopped:
                widgets.tell(self.app, "Scraping stopped", out.stopped + "\n\n" + text, "warn")
            elif out.missing or out.failed:
                lines = ["%s: %s" % (g.title, why) for g, why in (out.missing + out.failed)[:12]]
                more = len(out.missing) + len(out.failed) - len(lines)
                widgets.tell(self.app, "Scraping finished", text + "\n\n" + "\n".join(lines) +
                             ("\nand %d more" % more if more > 0 else ""))

        def fail(e):
            self.scraping = False
            self._bottom_idle()
            widgets.tell(self.app, "Scraping failed", str(e), "danger")
        self.app.run_bg(work, done, fail, name="scrape")

    def _scrape_progress(self, i, n, g, msg):
        if not self.scraping:
            return
        try:
            self.sc_bar.set(i / float(n) if n else None)
            self.sc_text.configure(text="%d of %d  ·  %s: %s" % (i, n, g.title, msg))
        except tk.TclError:
            pass
        if msg.startswith("cover added"):
            s = self.app.session
            self.states[g.index] = s.index.find(g)
            self.thumbs.forget(g.index)
            self._thumb_ready()


def _reveal(path):
    """Show a file in the system's file manager."""
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
    except OSError:
        pass
