"""The Settings tab: the look, the person's console, the scrapers and their accounts."""

import tkinter as tk

from ... import config
from ...console.models import MODELS, by_key
from ...scrape import scraper
from .. import images, theme, widgets
from ..app import Page
from ..theme import S


class SettingsPage(Page):
    name = "Settings"
    icon = "settings"
    title = "Settings"

    def build(self):
        ctx, p = self.ctx, self.pal
        st = self.app.settings
        self.left = self.frame()
        self.right = self.frame()
        # ---- the look
        ls = widgets.ScrollFrame(self.left, ctx)
        ls.pack(fill="both", expand=True)
        L = ls.inner
        widgets.section(L, ctx, "Colour").pack(anchor="w")
        widgets.label(L, ctx, "The accent and the backdrop follow GammaOS Pico's own colours.", 12,
                      color=p.muted, wraplength=S(360)).pack(anchor="w", fill="x", pady=(S(4), S(10)))
        sw = widgets.Flow(L, p.panel, gap=14)
        sw.pack(fill="x")
        for name in theme.ACCENT_NAMES:
            col = tk.Frame(sw, bg=p.panel)
            sw.add(col)
            on = name == p.name
            b = tk.Label(col, image=images.swatch(theme.accent(name), on, p.ink), bg=p.panel, cursor="hand2")
            b.pack()
            b.bind("<Button-1>", lambda e, n=name: self.app.root.after(10, self.app.set_accent, n))
            widgets.label(col, ctx, name, 11, color=p.ink if on else p.muted).pack()
        tk.Frame(L, bg=p.line, height=1).pack(fill="x", pady=S(18))
        widgets.section(L, ctx, "Cards").pack(anchor="w")
        self.auto = tk.BooleanVar(value=bool(st.get("auto_refresh")))
        widgets.Switch(L, ctx, "Watch for cards being put in and taken out", self.auto,
                       command=lambda: st.set("auto_refresh", bool(self.auto.get()))).pack(anchor="w", pady=(S(10), 0))
        self.demo = tk.BooleanVar(value=bool(st.get("include_demo_games")))
        widgets.Switch(L, ctx, "Put the free demo games on new cards", self.demo,
                       command=lambda: st.set("include_demo_games", bool(self.demo.get()))).pack(anchor="w", pady=(S(10), 0))
        tk.Frame(L, bg=p.line, height=1).pack(fill="x", pady=S(18))
        widgets.section(L, ctx, "Your console").pack(anchor="w")
        widgets.label(L, ctx, "Which console's packages the tool downloads. The Console tab sets it by itself "
                      "when a console is connected.", 12, color=p.muted, wraplength=S(360)).pack(anchor="w", fill="x",
                                                                                         pady=(S(4), S(6)))
        self.model = tk.StringVar(value=self.app.console_model().key)
        for m in MODELS:
            widgets.Radio(L, ctx, m.name, self.model, m.key,
                          command=lambda: self.app.root.after_idle(
                              lambda: self.app.set_console_model(by_key(self.model.get())))).pack(anchor="w", pady=S(4))
        tk.Frame(L, bg=p.line, height=1).pack(fill="x", pady=S(18))
        widgets.section(L, ctx, "Where the tool keeps its things").pack(anchor="w")
        widgets.label(L, ctx, "Settings: %s" % config.config_dir(), 11, color=p.dim, wraplength=S(380)).pack(anchor="w", fill="x", pady=(S(8), 0))
        widgets.label(L, ctx, "Downloads and caches: %s" % config.cache_dir(), 11, color=p.dim,
                      wraplength=S(380)).pack(anchor="w", fill="x", pady=(S(4), 0))
        widgets.label(L, ctx, "Nothing of the tool's is written to the card except what you ask for: the "
                      "card's files, covers and the BIOS.", 11, color=p.dim, wraplength=S(380)).pack(anchor="w", fill="x", pady=(S(4), 0))
        # ---- scraping
        rs = widgets.ScrollFrame(self.right, ctx)
        rs.pack(fill="both", expand=True)
        R = rs.inner
        widgets.section(R, ctx, "Box art scraping").pack(anchor="w")
        self.src = tk.StringVar(value=st.get("scrape_source"))
        row = tk.Frame(R, bg=p.panel)
        row.pack(fill="x", pady=(S(12), 0))
        for lab, val in scraper.SOURCES:
            widgets.Radio(row, ctx, lab, self.src, val, command=self.save).pack(side="left", padx=(0, S(24)))
        g = tk.Frame(R, bg=p.panel)
        g.pack(fill="x", pady=(S(14), 0))
        g.columnconfigure(1, weight=1)
        widgets.label(g, ctx, "Preferred region", 13, color=p.muted).grid(row=0, column=0, sticky="w", pady=S(6), padx=(0, S(18)))
        self.region = tk.StringVar(value=st.get("scrape_region"))
        widgets.Select(g, ctx, scraper.REGIONS, self.region, command=self.save).grid(row=0, column=1, sticky="w")
        widgets.label(g, ctx, "Covers of this region are taken first, then World, USA, Europe and Japan.", 11,
                      color=p.dim, wraplength=S(460)).grid(row=1, column=0, columnspan=2, sticky="w")
        tk.Frame(R, bg=p.line, height=1).pack(fill="x", pady=S(16))
        widgets.label(R, ctx, "ScreenScraper", 15, weight="bold").pack(anchor="w")
        widgets.label(R, ctx, "Release builds of the tool have GammaOS's own ScreenScraper access built in. Your "
                      "free screenscraper.fr account adds to the daily quota and the speed (optional).", 12,
                      color=p.muted, wraplength=S(520)).pack(anchor="w", fill="x", pady=(S(4), S(8)))
        g = tk.Frame(R, bg=p.panel)
        g.pack(fill="x")
        g.columnconfigure(1, weight=1)
        self.ss_user = tk.StringVar(value=st.get("ss_user"))
        self.ss_pass = tk.StringVar(value=st.get("ss_password"))
        widgets.label(g, ctx, "User name", 13, color=p.muted).grid(row=0, column=0, sticky="w", padx=(0, S(18)))
        widgets.Field(g, ctx, self.ss_user, width=28).grid(row=0, column=1, sticky="we", pady=S(4))
        widgets.label(g, ctx, "Password", 13, color=p.muted).grid(row=1, column=0, sticky="w", padx=(0, S(18)))
        widgets.Field(g, ctx, self.ss_pass, show="•", width=28).grid(row=1, column=1, sticky="we", pady=S(4))
        sc = scraper.ScreenScraper()
        widgets.label(R, ctx, "Built-in access: %s" % ("yes" if sc.ready else "not in this copy (run a release build, or use TheGamesDB)"),
                      11, color=p.ok if sc.ready else p.warn).pack(anchor="w", pady=(S(6), 0))
        tk.Frame(R, bg=p.line, height=1).pack(fill="x", pady=S(16))
        widgets.label(R, ctx, "TheGamesDB", 15, weight="bold").pack(anchor="w")
        widgets.label(R, ctx, "Needs your own API key (free, from thegamesdb.net). It matches by name only.", 12,
                      color=p.muted, wraplength=S(520)).pack(anchor="w", fill="x", pady=(S(4), S(8)))
        g = tk.Frame(R, bg=p.panel)
        g.pack(fill="x")
        g.columnconfigure(1, weight=1)
        self.tgdb = tk.StringVar(value=st.get("tgdb_key"))
        widgets.label(g, ctx, "API key", 13, color=p.muted).grid(row=0, column=0, sticky="w", padx=(0, S(18)))
        widgets.Field(g, ctx, self.tgdb, width=36).grid(row=0, column=1, sticky="we", pady=S(4))
        widgets.label(R, ctx, "Accounts and keys are kept in the tool's settings on this computer, readable by your "
                      "user only, never on the card.", 11, color=p.dim, wraplength=S(520)).pack(anchor="w", fill="x", pady=(S(10), 0))
        for v in (self.ss_user, self.ss_pass, self.tgdb):
            v.trace_add("write", lambda *a: self._save_soon())
        self._job = None

    def panels(self, x, y, w, h):
        lw = max(S(340), min(S(440), int(w * 0.38)))
        gap = S(18)
        return [((x, y, lw, h), self.left), ((x + lw + gap, y, w - lw - gap, h), self.right)]

    def on_console_model(self):
        key = self.app.console_model().key
        if self.model.get() != key:
            self.model.set(key)

    def _save_soon(self):
        if self._job:
            self.app.root.after_cancel(self._job)
        self._job = self.app.root.after(400, self.save)

    def save(self):
        self._job = None
        self.app.settings.update({
            "scrape_source": self.src.get(), "scrape_region": self.region.get(),
            "ss_user": self.ss_user.get().strip(), "ss_password": self.ss_pass.get(),
            "tgdb_key": self.tgdb.get().strip()})
        self.app.status("Settings saved.")
        for p in self.app.pages:
            if p.name == "Games" and p.built:
                p._bottom_idle()
