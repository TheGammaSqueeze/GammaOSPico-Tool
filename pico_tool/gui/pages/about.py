"""The About tab."""

import tkinter as tk

from ... import APP_NAME, RELEASES, WIKI, __version__
from .. import images, widgets
from ..app import Page
from ..theme import S


class AboutPage(Page):
    name = "About"
    icon = "about"
    title = "About"

    def build(self):
        ctx, p = self.ctx, self.pal
        self.main = self.frame()
        ms = widgets.ScrollFrame(self.main, ctx)
        ms.pack(fill="both", expand=True)
        M = ms.inner
        head = tk.Frame(M, bg=p.panel)
        head.pack(fill="x")
        tk.Label(head, image=images.icon("logo", S(56), p.accent, 1.7), bg=p.panel).pack(side="left")
        t = tk.Frame(head, bg=p.panel)
        t.pack(side="left", padx=(S(18), 0))
        widgets.label(t, ctx, APP_NAME, 24, weight="bold").pack(anchor="w")
        widgets.label(t, ctx, "Version %s" % __version__, 13, color=p.muted).pack(anchor="w")
        widgets.label(M, ctx, "Sets up and updates the microSD card of GammaOS Pico, the offline retro handheld OS "
                      "for the Konkr Pocket Advance; lists the games the way the console does and puts box art "
                      "on the card; and backs up, updates and restores the console's bootloader over USB.", 14,
                      wraplength=S(820)).pack(anchor="w", fill="x", pady=(S(22), S(8)))
        links = tk.Frame(M, bg=p.panel)
        links.pack(anchor="w", pady=(S(6), S(14)))
        widgets.Link(links, ctx, "The GammaOS Pico wiki", WIKI, size=13).pack(side="left", padx=(0, S(24)))
        widgets.Link(links, ctx, "Releases", RELEASES, size=13).pack(side="left", padx=(0, S(24)))
        widgets.Link(links, ctx, "Box art", WIKI + "/Box-Art", size=13).pack(side="left", padx=(0, S(24)))
        widgets.Link(links, ctx, "Troubleshooting", WIKI + "/Troubleshooting-and-FAQ", size=13).pack(side="left")
        widgets.Notice(M, ctx, "note", "Offline first",
                       "Everything works without a network except downloading a release and scraping box art. A "
                       "card package or flash package you downloaded before can be used from a file, and the "
                       "tool never sends anything about your games or card anywhere but the scraper you choose "
                       "(the ROM's name, size and CRC, to find its cover).").pack(fill="x", pady=(0, S(10)))
        widgets.Notice(M, ctx, "warn", "Released as it is",
                       "GammaOS Pico and this tool are experimental software with no warranty of any kind. They "
                       "are not community projects: feature requests, issues and pull requests are not "
                       "accepted, and there is no support channel. Use them as they are, or not at all.").pack(fill="x", pady=(0, S(10)))
        widgets.label(M, ctx, "Licence: GPL-3.0-or-later. GammaOS Pico is not affiliated with Konkr, AYANEO, "
                      "Nintendo or Sega. No games or BIOS files are included.", 12, color=p.dim,
                      wraplength=S(820)).pack(anchor="w", fill="x", pady=(S(18), 0))

    def panels(self, x, y, w, h):
        pw = min(w, S(980))
        return [((x + (w - pw) // 2, y, pw, h), self.main)]
