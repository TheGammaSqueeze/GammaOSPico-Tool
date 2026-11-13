"""Finding one game's cover by hand: search a scraper, look at the candidates, pick one."""

import io
import tkinter as tk

from PIL import Image, ImageTk

from . import images, theme, widgets
from .theme import S
from ..scrape.scraper import ScrapeError


class SearchDialog(widgets.Dialog):
    def __init__(self, app, game, sc):
        widgets.Dialog.__init__(self, app, "Find a cover for %s" % game.title, width=760)
        self.game, self.sc = game, sc
        self.cands = []
        self.covers = {}            # candidate index -> picture bytes (or an error text)
        self.picked = None
        self.seq = 0
        ctx, p = self.ctx, self.ctx.pal
        bar = tk.Frame(self.body, bg=p.panel)
        bar.pack(fill="x")
        self.q = tk.StringVar(value=game.title)
        f = widgets.Field(bar, ctx, self.q, icon="search", width=40)
        f.pack(side="left", fill="x", expand=True)
        f.entry.bind("<Return>", lambda e: self.search())
        widgets.PillButton(bar, ctx, "Search", self.search, "accent").pack(side="left", padx=(S(10), 0))
        widgets.label(self.body, ctx, "%s  ·  %s" % (sc.label, game.system.label), 12, color=p.dim).pack(fill="x", pady=(S(6), S(8)))
        main = tk.Frame(self.body, bg=p.panel)
        main.pack(fill="both", expand=True)
        lf = tk.Frame(main, bg=p.field)
        lf.pack(side="left", fill="both", expand=True)
        self.lb = tk.Listbox(lf, bg=p.field, fg=p.ink, bd=0, highlightthickness=0, activestyle="none",
                             selectbackground=theme.mix(p.field, p.accent, 0.35), selectforeground=p.ink,
                             font=ctx.font(13), height=14)
        sb = widgets.ThinScrollbar(lf, ctx, self.lb.yview, bg=p.field)
        self.lb.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", pady=S(4))
        self.lb.pack(side="left", fill="both", expand=True, padx=S(8), pady=S(8))
        self.lb.bind("<<ListboxSelect>>", lambda e: self.preview())
        self.lb.bind("<Double-Button-1>", lambda e: self.use())
        size = S(260)
        self.pv = tk.Canvas(main, width=size, height=size, bg=p.panel, highlightthickness=0)
        self.pv.pack(side="left", padx=(S(16), 0), anchor="n")
        self.note = widgets.label(self.body, ctx, "", 12, color=p.muted)
        self.note.pack(fill="x", pady=(S(8), 0))
        self.ok = self.add_button("Use this cover", self.use, "primary")
        self.ok.enabled(False)
        self.add_button("Cancel", self.cancel)
        self._draw_preview(None, "")
        self.after(50, self.search)

    def _draw_preview(self, data, text):
        p = self.ctx.pal
        c = self.pv
        size = int(c.cget("width"))
        c.delete("all")
        self._bg = images.pill(size, size, p.field, None, radius=S(14))
        c.create_image(0, 0, image=self._bg, anchor="nw")
        if data:
            try:
                im = Image.open(io.BytesIO(data)).convert("RGB")
                self._img = ImageTk.PhotoImage(images.fit(im, size - S(20), size - S(20)))
                c.create_image(size // 2, size // 2, image=self._img)
                return
            except Exception:
                text = "This picture could not be read"
        c.create_text(size // 2, size // 2, text=text, fill=p.dim, font=self.ctx.font(12), width=size - S(30),
                      justify="center")

    def search(self):
        words = self.q.get().strip()
        if not words:
            return
        self.seq += 1
        seq = self.seq
        self.lb.delete(0, "end")
        self.cands = []
        self.covers = {}
        self.ok.enabled(False)
        self.note.configure(text="Searching...")
        self._draw_preview(None, "")

        def done(cands):
            if seq != self.seq or not self.winfo_exists():
                return
            self.cands = cands
            for cnd in cands:
                extra = "  (%s)" % cnd.year if cnd.year else ""
                self.lb.insert("end", "  " + cnd.title + extra)
            self.note.configure(text="%d found" % len(cands) if cands else "Nothing found: try fewer or other words.")
            if cands:
                self.lb.selection_set(0)
                self.preview()

        def fail(e):
            if seq == self.seq and self.winfo_exists():
                self.note.configure(text=str(e))
        self.app.run_bg(lambda: self.sc.search(words, self.game.system), done, fail)

    def preview(self):
        sel = self.lb.curselection()
        if not sel:
            return
        i = sel[0]
        self.ok.enabled(False)
        hit = self.covers.get(i)
        if hit is not None:
            self._show(i, hit)
            return
        self._draw_preview(None, "Loading the cover...")
        cand = self.cands[i]

        def work():
            r = self.sc.cover(cand)
            return r.image or (r.error or "no cover")

        def done(v):
            self.covers[i] = v
            if self.winfo_exists() and self.lb.curselection() and self.lb.curselection()[0] == i:
                self._show(i, v)

        def fail(e):
            self.covers[i] = str(e) if isinstance(e, ScrapeError) else "the cover could not be loaded"
            if self.winfo_exists():
                self._show(i, self.covers[i])
        self.app.run_bg(work, done, fail)

    def _show(self, i, v):
        if isinstance(v, (bytes, bytearray)):
            self._draw_preview(v, "")
            self.ok.enabled(True)
        else:
            self._draw_preview(None, "No cover: %s" % v)

    def use(self):
        sel = self.lb.curselection()
        if not sel:
            return
        v = self.covers.get(sel[0])
        if isinstance(v, (bytes, bytearray)):
            self.done(bytes(v))
