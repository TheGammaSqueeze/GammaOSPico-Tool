"""The Card tab: pick the microSD card, set it up or update it, add the Game Boy Advance BIOS."""

import os
import threading
import tkinter as tk
from tkinter import filedialog

from ... import WIKI_INSTALLING
from ...card import layout, volumes
from ...console.models import MODELS, by_key
from ...release import github, install
from .. import images, theme, widgets
from ..app import Page
from ..gamelist import _fit, _wash
from ..theme import S


def human(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0


def _middle(font, text, width):
    """text cut in its middle to fit width ("/media/user/.../card")."""
    if font.measure(text) <= width or len(text) < 8:
        return text
    lo, hi = 1, len(text) // 2
    best = text[:4] + "\u2026"
    while lo <= hi:
        k = (lo + hi) // 2
        cand = text[:k] + "\u2026" + text[-k:]
        if font.measure(cand) <= width:
            best, lo = cand, k + 1
        else:
            hi = k - 1
    return best


class CardList(tk.Canvas):
    """The cards found, one row each (a few at most)."""

    def __init__(self, parent, ctx, on_pick):
        self.ctx = ctx
        tk.Canvas.__init__(self, parent, bg=ctx.pal.panel, highlightthickness=0, bd=0)
        self.vols = []
        self.current = None
        self.on_pick = on_pick
        self.rowh = S(70)
        self.hover = None
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", lambda e: self._hover((e.y // self.rowh) if e.y // self.rowh < len(self.vols) else None))
        self.bind("<Leave>", lambda e: self._hover(None))

    def _hover(self, i):
        if i != self.hover:
            self.hover = i
            self.redraw()

    def set(self, vols, current):
        self.vols = vols
        self.current = current
        self.redraw()

    def redraw(self):
        c = self
        p = self.ctx.pal
        c.delete("all")
        w = max(10, c.winfo_width())
        if not self.vols:
            c.create_text(S(4), S(16), text="No removable card found.", fill=p.muted, font=self.ctx.font(13),
                          anchor="nw")
            c.create_text(S(4), S(42), width=w - S(10), anchor="nw", fill=p.dim, font=self.ctx.font(12),
                          text="Put the console's microSD card in this computer (a USB card reader works), "
                               "or pick its folder with \"Choose folder...\".")
            return
        fT, fS, fB = self.ctx.font(14, "bold"), self.ctx.font(12), self.ctx.font(10, "bold")
        for i, v in enumerate(self.vols):
            y = i * self.rowh
            sel = self.current is not None and os.path.normcase(v.path) == os.path.normcase(self.current)
            if sel:
                c.create_image(0, y, image=_wash(w, self.rowh - S(2), p.accent), anchor="nw")
                c.create_rectangle(0, y + S(10), max(2, S(2.5)), y + self.rowh - S(12), fill=p.accent, outline="")
            elif i == self.hover:
                c.create_rectangle(0, y, w, y + self.rowh - S(2), fill=p.hover, outline="")
            c.create_line(S(12), y + self.rowh - 1, w, y + self.rowh - 1, fill=p.line)
            cy = y + self.rowh // 2
            c.create_image(S(26), cy, image=images.icon("card", S(26), p.ink if sel else p.muted), anchor="center")
            x = S(52)
            if v.has_pico:
                badge, col = "GammaOS Pico", p.ok
            elif not v.fs_ok:
                badge, col = v.fs_label, p.danger
            else:
                badge, col = "Empty", p.dim
            bw = fB.measure(badge) + S(16)
            c.create_image(w - S(8), cy, image=images.pill(bw, S(20), None, theme.mix(col, p.panel, 0.45), theme.scale()),
                           anchor="e")
            c.create_text(w - S(8) - bw // 2, cy, text=badge, fill=col, font=fB)
            avail = w - x - bw - S(20)
            c.create_text(x, cy - S(10), text=_fit(fT, v.label or v.path, avail), fill=p.ink, font=fT, anchor="w")
            sub = "%s  ·  %s  ·  %s free" % (v.path, v.fs_label, human(v.free))
            c.create_text(x, cy + S(12), text=_fit(fS, sub, avail), fill=p.muted, font=fS, anchor="w")

    def _click(self, e):
        i = e.y // self.rowh
        if 0 <= i < len(self.vols):
            self.on_pick(self.vols[i])


class CardPage(Page):
    name = "Card"
    icon = "card"
    title = "microSD card"

    def build(self):
        ctx, p = self.ctx, self.pal
        self.left = self.frame()
        self.right = self.frame()
        self.vols = []
        self.busy = False
        self.cancel = None
        self.local_zip = None
        self.poll_job = None
        self._last_keys = None
        # ---- the cards
        head = tk.Frame(self.left, bg=p.panel)
        head.pack(fill="x")
        widgets.section(head, ctx, "microSD cards").pack(side="left")
        widgets.PillButton(head, ctx, "", self.refresh, "ghost", icon="refresh", small=True).pack(side="right")
        self.cardlist = CardList(self.left, ctx, self.pick)
        self.cardlist.pack(fill="both", expand=True, pady=(S(10), S(10)))
        foot = tk.Frame(self.left, bg=p.panel)
        foot.pack(fill="x", side="bottom")
        self.auto = tk.BooleanVar(value=bool(self.app.settings.get("auto_refresh")))
        widgets.Switch(foot, ctx, "Watch for cards", self.auto,
                       command=lambda: self.app.settings.set("auto_refresh", bool(self.auto.get()))).pack(anchor="w", pady=(0, S(12)))
        widgets.PillButton(foot, ctx, "Choose folder...", self.choose_folder, "secondary", icon="folder").pack(anchor="w")
        self.cardlist.pack_forget()
        self.cardlist.pack(fill="both", expand=True, pady=(S(10), S(10)))
        # ---- the chosen card: actions pinned at the foot, the rest scrolls
        self.act = tk.Frame(self.right, bg=p.panel)
        self.act.pack(fill="x", side="bottom")
        self.scroll = widgets.ScrollFrame(self.right, ctx)
        self.scroll.pack(fill="both", expand=True)
        self.detail = self.scroll.inner
        self.demo = tk.BooleanVar(value=bool(self.app.settings.get("include_demo_games")))
        self.model_var = tk.StringVar(value=self.app.console_model().key)
        self.render_detail()
        self.app.root.after(200, self.refresh)
        self.check_release()

    def panels(self, x, y, w, h):
        lw = max(S(320), min(S(440), int(w * 0.36)))
        gap = S(18)
        return [((x, y, lw, h), self.left), ((x + lw + gap, y, w - lw - gap, h), self.right)]

    # ---- cards ---------------------------------------------------------------------------------
    def on_show(self):
        self._schedule_poll()

    def on_hide(self):
        if self.poll_job:
            try:
                self.app.root.after_cancel(self.poll_job)
            except tk.TclError:
                pass
            self.poll_job = None

    def _schedule_poll(self):
        if self.poll_job:
            self.app.root.after_cancel(self.poll_job)
        self.poll_job = self.app.root.after(3000, self._poll)

    def _poll(self):
        self.poll_job = None
        if self.auto.get() and not self.busy:
            self.refresh(quiet=True)
        if self.app.page is self:
            self._schedule_poll()

    def refresh(self, quiet=False):
        def done(vols):
            keys = [v.key() for v in vols]
            if quiet and keys == self._last_keys:
                return
            self._last_keys = keys
            self.vols = vols
            cur = self.app.card
            # keep a folder the user picked in the list too
            if cur is not None and not any(os.path.normcase(v.path) == os.path.normcase(cur.path) for v in vols):
                if os.path.isdir(cur.path) and not cur.removable:
                    vols = vols + [volumes.fill(cur)]
                else:
                    self.app.set_card(None)
                    cur = None
            self.vols = vols
            if cur is None and len(vols) >= 1:
                last = self.app.settings.get("last_card")
                pick = next((v for v in vols if v.path == last), None) or next((v for v in vols if v.has_pico), None) \
                    or (vols[0] if len(vols) == 1 else None)
                if pick:
                    self.app.set_card(pick)
                    cur = pick
            elif cur is not None:
                same = next(v for v in vols if os.path.normcase(v.path) == os.path.normcase(cur.path))
                if same.key() != cur.key():
                    self.app.set_card(same)
            self.cardlist.set(vols, self.app.card.path if self.app.card else None)
            self.render_detail()
        self.app.run_bg(volumes.list_volumes, done, name="volumes")

    def pick(self, vol):
        if self.busy:
            return
        self.app.set_card(vol)
        self.cardlist.set(self.vols, vol.path)
        self.render_detail()

    def choose_folder(self):
        if self.busy:
            return
        d = filedialog.askdirectory(parent=self.app.root, title="The microSD card's folder")
        if not d:
            return

        def done(vol):
            if not any(os.path.normcase(v.path) == os.path.normcase(vol.path) for v in self.vols):
                self.vols.append(vol)
            self.pick(vol)
        self.app.run_bg(lambda: volumes.folder_volume(d), done)

    def on_card(self):
        if self.built:
            self.cardlist.set(self.vols, self.app.card.path if self.app.card else None)
            self.render_detail()

    def on_console_model(self):
        if self.built and not self.busy:
            self.render_detail()

    # ---- the latest release --------------------------------------------------------------------
    def check_release(self):
        self.rel_state = ("checking", None)

        def done(rel):
            self.app.release = rel
            self.rel_state = ("ok", rel)
            self.render_detail()

        def fail(e):
            self.rel_state = ("error", str(e))
            self.render_detail()
        self.app.run_bg(github.latest, done, fail, name="release")

    # ---- the detail panel ----------------------------------------------------------------------
    def render_detail(self):
        if self.busy:
            return
        ctx, p = self.ctx, self.pal
        for w in self.detail.winfo_children():
            w.destroy()
        for w in self.act.winfo_children():
            w.destroy()
        d = self.detail
        v = self.app.card
        if v is None:
            box = tk.Frame(d, bg=p.panel)
            box.pack(pady=(S(120), 0))
            tk.Label(box, image=images.icon("card", S(56), theme.mix(p.muted, p.panel, 0.3), 1.3), bg=p.panel).pack()
            widgets.label(box, ctx, "Choose the console's microSD card", 20, weight="bold").pack(pady=(S(14), S(6)))
            widgets.label(box, ctx, "Put the card in this computer. It appears on the left; pick it to set it up, "
                          "update it, or manage its games and box art.", 13, color=p.muted,
                          wraplength=S(420), justify="center").pack()
            return
        top = tk.Frame(d, bg=p.panel)
        top.pack(fill="x")
        widgets.section(top, ctx, "The card").pack(anchor="w")
        widgets.label(top, ctx, v.label or os.path.basename(v.path) or v.path, 24, weight="bold").pack(anchor="w", pady=(S(6), 0))
        pl = widgets.label(top, ctx, v.path, 12, color=p.muted)
        pl.pack(anchor="w", fill="x")
        pl.bind("<Configure>", lambda e, pl=pl, t=v.path: pl.configure(text=_middle(ctx.font(12), t, e.width)))
        grid = tk.Frame(d, bg=p.panel)
        grid.pack(fill="x", pady=(S(16), S(4)))
        grid.columnconfigure(1, weight=1)
        row = [0]

        def item(name, value, color=None, extra=None):
            r = row[0]
            widgets.label(grid, ctx, name, 13, color=p.muted).grid(row=r, column=0, sticky="w", pady=S(7), padx=(0, S(24)))
            f = tk.Frame(grid, bg=p.panel)
            f.grid(row=r, column=1, sticky="we")
            widgets.label(f, ctx, value, 13, color=color or p.ink).pack(side="left")
            if extra:
                extra(f)
            tk.Frame(grid, bg=p.line, height=1).grid(row=r, column=0, columnspan=2, sticky="sew")
            row[0] += 1

        # whose card package to download: the files are the same for both consoles, the name is not
        r = row[0]
        widgets.label(grid, ctx, "Console", 13, color=p.muted).grid(row=r, column=0, sticky="w", pady=S(7), padx=(0, S(24)))
        self.model_var.set(self.app.console_model().key)
        widgets.Select(grid, ctx, [(m.name, m.key) for m in MODELS], self.model_var,
                       command=lambda *a: self.app.root.after_idle(
                           lambda: self.app.set_console_model(by_key(self.model_var.get())))).grid(
            row=r, column=1, sticky="w", pady=S(4))
        tk.Frame(grid, bg=p.line, height=1).grid(row=r, column=0, columnspan=2, sticky="sew")
        row[0] += 1
        fs_col = p.ink if v.fs_ok else p.danger
        item("File system", v.fs_label + ("" if v.fs_ok else "  (the console cannot read it)"), fs_col)

        def space(f):
            bar = widgets.Progress(f, ctx, height=6)
            bar.pack(side="left", padx=(S(16), 0), fill="x", expand=True)
            bar.configure(width=S(160))
            bar.set((v.total - v.free) / float(v.total) if v.total else 0.0)
        item("Space", "%s free of %s" % (human(v.free), human(v.total)), extra=space)
        if v.has_pico:
            item("GammaOS Pico", v.version or "installed (version unknown)", p.ok)
        else:
            item("GammaOS Pico", "not on this card", p.muted)
        st, val = getattr(self, "rel_state", ("checking", None))
        if st == "ok":
            newer = v.has_pico and v.version and val.version != v.version
            item("Latest release", val.version + ("  (newer than the card's)" if newer else ""), p.accent if newer else p.ink)
        elif st == "checking":
            item("Latest release", "checking...", p.dim)
        else:
            item("Latest release", "unknown", p.dim,
                 extra=lambda f: widgets.PillButton(f, ctx, "Check again", self.check_release, "ghost", small=True).pack(side="left", padx=S(10)))
        bios = layout.installed_bios(v.path) if os.path.isdir(v.path) else None
        if bios:
            chk = layout.check_bios(bios)
            btext, bcol = ("installed", p.ok) if chk.known else (("installed, not the usual one", p.warn) if chk.ok else ("the wrong size", p.danger))
        else:
            btext, bcol = "none (optional)", p.muted
        item("Game Boy Advance BIOS", btext, bcol,
             extra=lambda f: widgets.PillButton(f, ctx, "Add BIOS..." if not bios else "Replace...", self.add_bios,
                                                "ghost", small=True).pack(side="left", padx=S(10)))
        if not v.fs_ok:
            n = widgets.Notice(d, ctx, "danger", "This card needs formatting",
                               "The console reads cards formatted FAT32 or exFAT. This one is %s. Formatting "
                               "erases the card, so copy anything you want to keep first; the tool never formats a "
                               "card itself. The Installing page shows how on Windows, macOS and Linux."
                               % v.fs_label)
            n.pack(fill="x", pady=(S(14), 0))
            widgets.Link(n.body.master, ctx, "Open the Installing page", WIKI_INSTALLING, bg=p.danger_wash).pack(anchor="w", pady=(S(6), 0))
        # actions
        act = self.act
        self.prog_frame = tk.Frame(act, bg=p.panel)
        self.prog_frame.pack(fill="x", pady=(S(10), S(10)))
        btns = tk.Frame(act, bg=p.panel)
        btns.pack(fill="x")
        rel = self.app.release
        if v.has_pico:
            label = "Update this card" + ((" to " + rel.version) if rel else "")
        else:
            label = "Set up this card"
        self.main_btn = widgets.PillButton(btns, ctx, label, lambda: self.start(local=False), "primary", icon="download")
        self.main_btn.pack(side="left")
        widgets.PillButton(btns, ctx, "Use a zip file...", lambda: self.start(local=True), "secondary").pack(side="left", padx=(S(12), 0))
        self.main_btn.enabled(v.fs_ok or not v.removable)
        opts = tk.Frame(act, bg=p.panel)
        opts.pack(fill="x", pady=(S(12), 0))
        widgets.Switch(opts, ctx, "Include the free demo games", self.demo,
                       command=lambda: self.app.settings.set("include_demo_games", bool(self.demo.get()))).pack(side="left")
        note = widgets.label(act, ctx, "Your games, saves, states, settings, box art, BIOS and folders.txt are never "
                             "replaced or deleted. You see every change before anything is written.", 12, color=p.dim,
                             wraplength=S(560))
        note.pack(fill="x", pady=(S(10), 0))
        note.bind("<Configure>", lambda e, n=note: n.configure(wraplength=max(200, e.width - S(4))))

    # ---- BIOS ----------------------------------------------------------------------------------
    def add_bios(self):
        v = self.app.card
        if not v:
            return
        f = filedialog.askopenfilename(parent=self.app.root, title="Your Game Boy Advance BIOS",
                                       filetypes=[("BIOS files", "*.bin *.gba *.rom"), ("All files", "*")])
        if not f:
            return
        chk = layout.check_bios(f)
        if not chk.ok:
            widgets.tell(self.app, "Not a Game Boy Advance BIOS", chk.message, "danger")
            return
        if not chk.known:
            if not widgets.ask(self.app, "An unusual BIOS", chk.message + "\n\nUse it anyway?", "Use it",
                               notice=("warn", "Not the usual BIOS", "Some games may not run the same way.")):
                return
        if layout.installed_bios(v.path) and not widgets.ask(self.app, "Replace the BIOS?",
                                                              "The card already has a Game Boy Advance BIOS. "
                                                              "Replace it with this one?", "Replace"):
            return

        def done(path):
            self.app.status("BIOS copied to %s" % path)
            self.render_detail()

        def fail(e):
            widgets.tell(self.app, "The BIOS was not copied", str(e), "danger")
        self.app.run_bg(lambda: layout.install_bios(v.path, f), done, fail)

    # ---- set up / update -------------------------------------------------------------------------
    def start(self, local):
        v = self.app.card
        if not v or self.busy:
            return
        if not v.fs_ok and v.removable:
            return
        zp = None
        if local:
            zp = filedialog.askopenfilename(parent=self.app.root, title="A GammaOS Pico card package",
                                            filetypes=[("Card package", " ".join("%s-*-sdcard.zip" % m.package_prefix
                                                                                 for m in MODELS)),
                                                       ("Zip files", "*.zip"), ("All files", "*")])
            if not zp:
                return
        self.cancel = threading.Event()
        self._busy(True, "Preparing...")
        demo = bool(self.demo.get())
        root = v.path
        cancel = self.cancel
        model = self.app.console_model()

        def work():
            if zp:
                checked, msg = github.verify_local(zp)
                self.app.post(self._progress, None, msg)
                path = zp
            else:
                self.app.post(self._progress, None, "Looking for the latest release...")
                rel = self.app.release or github.latest()
                self.app.release = rel
                card = rel.card_for(model)
                sums = github.fetch_sums(rel)
                path = github.download(card, sums, cancel=cancel,
                                       progress=lambda d, t: self.app.post(
                                           self._progress, d / float(t) if t else None,
                                           "Downloading %s  (%s of %s)" % (card.name, human(d), human(t))))
            self.app.post(self._progress, None, "Comparing the package with the card...")
            return install.plan(path, root, demo_games=demo)
        self.app.run_bg(work, self._show_plan, self._failed, name="setup")

    def _show_plan(self, plan):
        if not plan.of(install.NEW) and not plan.of(install.REPLACE):
            self._busy(False)
            widgets.tell(self.app, "Nothing to change", "The card already has every file of GammaOS Pico %s."
                         % (plan.version or ""))
            self._after_install()
            return
        d = widgets.Dialog(self.app, "Write GammaOS Pico %s to the card?" % (plan.version or ""), width=640)
        ctx, p = self.ctx, self.pal
        widgets.label(d.body, ctx, plan.summary() + ". %s to write." % human(plan.write_bytes), 13,
                      color=p.muted, wraplength=S(600)).pack(fill="x", pady=(0, S(10)))
        st = widgets.ScrollText(d.body, ctx, height=16)
        st.pack(fill="both", expand=True)
        parts = []
        groups = ((install.NEW, "New files", "ok"), (install.REPLACE, "Replaced (a newer version)", "accent"),
                  (install.KEEP, "Yours, kept as they are", "muted"), (install.SKIP, "Demo games left out", "dim"),
                  (install.SAME, "Already up to date", "dim"))
        for act, title, tag in groups:
            items = plan.of(act)
            if not items:
                continue
            parts.append(("%s (%d)\n" % (title, len(items)), "head"))
            show = items if act in (install.NEW, install.REPLACE, install.KEEP) else items[:12]
            for it in show:
                parts.append(("    " + it.rel + "\n", tag))
            if len(show) < len(items):
                parts.append(("    and %d more\n" % (len(items) - len(show)), "dim"))
        st.set(parts)
        widgets.label(d.body, ctx, "Nothing on the card is deleted. Keep the card in until it ends.", 12,
                      color=p.dim).pack(fill="x", pady=(S(10), 0))
        d.add_button("Write to the card", lambda: d.done(True), "primary")
        d.add_button("Cancel", d.cancel)
        if not d.show():
            self._busy(False)
            self.app.status("Nothing was written.")
            return
        cancel = self.cancel

        def work():
            return install.apply(plan, cancel=cancel, progress=lambda dn, t, rel: self.app.post(
                self._progress, dn / float(t) if t else 1.0, "Writing %s" % rel if rel else "Finishing..."))

        def done(written):
            self._busy(False)
            self.app.status("The card is ready: %d files written." % len(written))
            widgets.tell(self.app, "The card is ready", "GammaOS Pico %s is on the card (%d files written). Eject "
                         "the card safely before taking it out." % (plan.version or "", len(written)))
            self._after_install()
        self.app.run_bg(work, done, self._failed, name="install")

    def _after_install(self):
        v = self.app.card
        if v:
            self.app.run_bg(lambda: volumes.fill(v), lambda vol: (self.app.set_card(vol), self.refresh()))

    def _failed(self, e):
        self._busy(False)
        msg = str(e) or e.__class__.__name__
        self.app.status(msg)
        widgets.tell(self.app, "That did not work", msg, "danger")

    def _busy(self, on, text=""):
        self.busy = on
        if on:
            for w in self.prog_frame.winfo_children():
                w.destroy()
            p, ctx = self.pal, self.ctx
            top = tk.Frame(self.prog_frame, bg=p.panel)
            top.pack(fill="x")
            self.prog_text = widgets.label(top, ctx, text, 12, color=p.muted)
            self.prog_text.pack(side="left", fill="x", expand=True)
            widgets.PillButton(top, ctx, "Cancel", lambda: self.cancel and self.cancel.set(), "ghost", small=True).pack(side="right")
            self.prog = widgets.Progress(self.prog_frame, ctx)
            self.prog.pack(fill="x", pady=(S(6), 0))
            self.prog.set(None)
            self.main_btn.enabled(False)
        else:
            self.render_detail()

    def _progress(self, frac, text):
        if not self.busy:
            return
        try:
            self.prog.set(frac)
            self.prog_text.configure(text=text)
        except tk.TclError:
            pass
        self.app.status(text)
