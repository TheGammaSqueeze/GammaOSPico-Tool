"""The Console tab: GammaOS Pico's bootloader over USB (fastboot) - back it up, install or update it,
restore a backup or the stock bootloader. The connected console says which it is (Konkr Pocket
Advance or Konkr Pocket Block); what is read and written, and which packages fit, follow from that."""

import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog

from ... import WIKI_INSTALLING
from ...console.models import MODELS, by_key
from ...release import flashpkg, github
from .. import images, theme, widgets
from ..app import Page
from ..theme import S
from .card import human

try:
    from ... import console as con
    CONSOLE_ERR = None
except Exception as e:      # the console part missing from this build
    con = None
    CONSOLE_ERR = str(e)

RISKS = ("GammaOS Pico is a custom bootloader: experimental software with no warranty of any kind. Writing "
         "a bootloader can leave the console unable to start; you do it at your own risk. Android stays, "
         "but its OTA updates may stop working while GammaOS Pico's bootloader is installed: update Android "
         "first. Keep the console charged and do not unplug it while a write runs.")


def open_folder(path):
    try:
        os.makedirs(path, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError:
        pass


class ConsolePage(Page):
    name = "Console"
    icon = "console"
    title = "The console's bootloader"

    def build(self):
        ctx, p = self.ctx, self.pal
        self.left = self.frame()
        self.right = self.frame()
        self.link = None
        self.problems = []
        self.busy = False
        self.poll_job = None
        self.cancel = None
        self.backups = []
        # ---- left: finding the console
        ls = widgets.ScrollFrame(self.left, ctx)
        ls.pack(fill="both", expand=True)
        L = ls.inner
        widgets.section(L, ctx, "Connect the console").pack(anchor="w")
        self.found = tk.Frame(L, bg=p.panel)
        self.found.pack(fill="x", pady=(S(10), S(14)))
        steps = tk.Frame(L, bg=p.panel)
        steps.pack(fill="x")
        for i, t in enumerate((
                "Turn the console off.",
                "Hold SELECT + START + R and press Power. The console shows its bootloader screen (fastboot).",
                "Connect it to this computer with a USB cable. It shows up above within a few seconds.")):
            r = tk.Frame(steps, bg=p.panel)
            r.pack(fill="x", pady=S(5))
            num = tk.Canvas(r, width=S(26), height=S(26), bg=p.panel, highlightthickness=0)
            num.pack(side="left", anchor="n")
            num._img = images.pill(S(26), S(26), None, theme.mix(p.accent, p.panel, 0.4), theme.scale())
            num.create_image(0, 0, image=num._img, anchor="nw")
            num.create_text(S(13), S(13), text=str(i + 1), fill=p.accent, font=ctx.font(12, "bold"))
            widgets.autowrap(widgets.label(r, ctx, t, 13, wraplength=S(300))).pack(side="left", padx=(S(12), 0),
                                                                                 fill="x", expand=True)
        widgets.PillButton(L, ctx, "USB setup help", self.drivers, "ghost", icon="usb", small=True).pack(anchor="w", pady=(S(10), 0))
        # the first install: the steps differ per console, and no console is known before one is
        # connected, so the person picks theirs (the one last seen is picked already)
        self.first_box = tk.Frame(L, bg=p.panel)
        self.first_box.pack(fill="x", pady=(S(18), 0))
        self.first_model = tk.StringVar(value=self.app.console_model().key)
        self._render_first()
        # ---- right: what to do
        self.prog_frame = tk.Frame(self.right, bg=p.panel)
        self.prog_frame.pack(fill="x", side="bottom")
        rs = widgets.ScrollFrame(self.right, ctx)
        rs.pack(fill="both", expand=True)
        R = rs.inner
        widgets.Notice(R, ctx, "danger", "Read this first", RISKS).pack(fill="x")
        self.actions = tk.Frame(R, bg=p.panel)
        self.actions.pack(fill="x", pady=(S(10), 0))
        bk = tk.Frame(R, bg=p.panel)
        bk.pack(fill="both", expand=True, pady=(S(16), 0))
        head = tk.Frame(bk, bg=p.panel)
        head.pack(fill="x")
        widgets.section(head, ctx, "Backups on this computer").pack(side="left")
        widgets.PillButton(head, ctx, "Open folder", lambda: open_folder(self.backup_root()), "ghost", icon="folder",
                           small=True).pack(side="right")
        widgets.PillButton(head, ctx, "Change...", self.change_root, "ghost", small=True).pack(side="right")
        self.root_label = widgets.label(bk, ctx, "", 11, color=p.dim)
        self.root_label.pack(fill="x", pady=(S(4), S(6)))
        self.bk_list = tk.Frame(bk, bg=p.panel)
        self.bk_list.pack(fill="x")
        self._render_found()
        self._render_actions()
        self.load_backups()

    def _render_first(self):
        ctx, p = self.ctx, self.pal
        for w in self.first_box.winfo_children():
            w.destroy()
        model = by_key(self.first_model.get())
        first = widgets.Notice(self.first_box, ctx, "note", "Installing for the first time?", first_install_text(model))
        first.pack(fill="x")
        pick = tk.Frame(first.body.master, bg=p.note_wash)
        pick.pack(fill="x", pady=(S(8), 0))
        for m in MODELS:
            widgets.Radio(pick, ctx, m.name, self.first_model, m.key, bg=p.note_wash,
                          command=lambda: self.app.root.after_idle(self._first_model_picked)).pack(anchor="w", pady=S(2))
        links = tk.Frame(first.body.master, bg=p.note_wash)
        links.pack(fill="x", pady=(S(8), 0))
        widgets.Link(links, ctx, "Open the Installing page", WIKI_INSTALLING, bg=p.note_wash).pack(side="left")
        widgets.PillButton(links, ctx, "Save the flash package...", self.save_flash_package, "ghost", icon="download",
                           small=True, bg=p.note_wash).pack(side="right")

    def _first_model_picked(self):
        model = by_key(self.first_model.get())
        if model is self.app.console_model():
            self._render_first()
        else:
            self.app.set_console_model(model)       # comes back through on_console_model

    def on_console_model(self):
        self.first_model.set(self.app.console_model().key)
        self._render_first()

    def panels(self, x, y, w, h):
        lw = max(S(330), min(S(430), int(w * 0.36)))
        gap = S(18)
        return [((x, y, lw, h), self.left), ((x + lw + gap, y, w - lw - gap, h), self.right)]

    # ---- finding the console ---------------------------------------------------------------------
    def on_show(self):
        if con is not None:
            self._poll()

    def on_hide(self):
        if self.poll_job:
            try:
                self.app.root.after_cancel(self.poll_job)
            except tk.TclError:
                pass
            self.poll_job = None

    def _poll(self):
        self.poll_job = None
        if self.app.page is not self or con is None:
            return
        if not self.busy:
            def look():
                # the console part's fuller report says why a device it sees cannot be used
                if hasattr(con, "scan"):
                    r = con.scan()
                    probs = list(r.problems or [])
                    if r.library_problem:
                        probs.insert(0, r.library_problem)
                    return list(r.links), probs
                return con.find_consoles(), []

            def done(res):
                links, probs = res
                link = links[0] if links else None
                if ((link and link.ident) != (self.link and self.link.ident) or probs != self.problems
                        or (link and self.link and link.label != self.link.label)):
                    self.link = link
                    self.problems = probs
                    self._render_found()
                    self._render_actions()
                if link is not None and getattr(link, "model", None) is not None:
                    self.app.set_console_model(link.model)     # its packages from now on

            def fail(e):
                pass
            self.app.run_bg(look, done, fail, name="find-console")
        self.poll_job = self.app.root.after(2000, self._poll)

    def _render_found(self):
        ctx, p = self.ctx, self.pal
        for w in self.found.winfo_children():
            w.destroy()
        f = self.found
        if con is None:
            widgets.label(f, ctx, "Console support is missing from this build (%s)." % CONSOLE_ERR, 13,
                          color=p.danger, wraplength=S(340)).pack(anchor="w")
            return
        on = self.link is not None
        row = tk.Frame(f, bg=p.panel)
        row.pack(fill="x")
        tk.Label(row, image=images.icon("console", S(40), p.ok if on else theme.mix(p.muted, p.panel, 0.35), 1.4),
                 bg=p.panel).pack(side="left")
        t = tk.Frame(row, bg=p.panel)
        t.pack(side="left", padx=(S(14), 0), fill="x", expand=True)
        if on:
            widgets.autowrap(widgets.label(t, ctx, self.link.label, 15, weight="bold", wraplength=S(260))).pack(anchor="w", fill="x")
            widgets.label(t, ctx, "Bootloader: %s" % (("GammaOS Pico " + self.link.lk_version) if self.link.lk_version
                                                      else "version not reported"), 12, color=p.ok).pack(anchor="w")
        else:
            widgets.label(t, ctx, "No console connected", 15, weight="bold").pack(anchor="w")
            widgets.label(t, ctx, "Looking for it every two seconds...", 12, color=p.dim).pack(anchor="w")
        for prob in self.problems:
            widgets.label(f, ctx, prob, 12, color=p.warn, wraplength=S(320)).pack(anchor="w", fill="x", pady=(S(8), 0))
        if self.problems:
            widgets.PillButton(f, ctx, "USB setup help", self.drivers, "ghost", icon="usb", small=True).pack(anchor="w")

    # ---- actions ---------------------------------------------------------------------------------
    def _render_actions(self):
        ctx, p = self.ctx, self.pal
        for w in self.actions.winfo_children():
            w.destroy()
        ready = con is not None and self.link is not None and not self.busy
        rel = self.app.release
        if self.link is not None:
            copies = self.link.model.partitions_text
        else:
            copies = " or ".join("%s (%s)" % (m.partitions_text, m.name.replace("Konkr ", "")) for m in MODELS)
        rows = (
            ("shield", "Back up the bootloader", "Saves %s from the console to this computer." % copies,
             "Back up", self.do_backup, "secondary"),
            ("download", "Install or update GammaOS Pico",
             "The bootloader from the latest release%s, or from a flash package or image you pick. A console "
             "with no backup here is backed up first." % ((" (" + rel.version + ")") if rel else ""),
             "Install...", self.do_install, "primary"),
            ("restore", "Restore a backup", "Writes a backup made with this tool back to the console.",
             "Restore...", self.do_restore, "secondary"),
            ("chip", "Restore the stock bootloader",
             "Puts the console's own bootloader back (from the flash package): it then starts Android as from the "
             "factory, and Android's OTA updates work again.", "Restore stock...", self.do_stock, "secondary"),
        )
        for icon, title, text, btn, fn, kind in rows:
            r = tk.Frame(self.actions, bg=p.panel)
            r.pack(fill="x", pady=S(6))
            tk.Label(r, image=images.icon(icon, S(24), p.accent if ready else p.dim), bg=p.panel).pack(side="left", anchor="n", pady=S(4))
            b = widgets.PillButton(r, ctx, btn, fn, kind, small=True, width=S(140))
            b.pack(side="right", anchor="center")
            b.enabled(ready)
            t = tk.Frame(r, bg=p.panel)
            t.pack(side="left", fill="x", expand=True, padx=(S(14), S(14)))
            widgets.label(t, ctx, title, 14, weight="bold", color=p.ink if ready else p.muted).pack(anchor="w")
            dl = widgets.label(t, ctx, text, 12, color=p.muted if ready else p.dim, wraplength=S(300))
            dl.pack(anchor="w", fill="x")
            widgets.autowrap(dl, 150)
            tk.Frame(self.actions, bg=p.line, height=1).pack(fill="x")

    def save_flash_package(self):
        """The latest flash package, downloaded and checked, saved where the user says."""
        import shutil
        d = filedialog.askdirectory(parent=self.app.root, title="Where to save the flash package")
        if not d:
            return
        self.app.status("Downloading the flash package...")
        model = by_key(self.first_model.get())

        def work():
            rel = self.app.release or github.latest()
            self.app.release = rel
            flash = rel.flash_for(model)
            sums = github.fetch_sums(rel)
            path = github.download(flash, sums, progress=lambda dn, t: self.app.post(
                self.app.status, "Downloading %s (%s of %s)" % (flash.name, human(dn), human(t))))
            dest = os.path.join(d, flash.name)
            shutil.copyfile(path, dest)
            return dest

        def done(dest):
            self.app.status("Saved %s (checked against SHA256SUMS)." % dest)
            open_folder(os.path.dirname(dest))
        self.app.run_bg(work, done, lambda e: widgets.tell(self.app, "The flash package was not saved", str(e), "danger"))

    def drivers(self):
        text = con.driver_help() if con is not None else "Console support is missing from this build."
        d = widgets.Dialog(self.app, "USB setup", width=620)
        st = widgets.ScrollText(d.body, self.ctx, height=16)
        st.pack(fill="both", expand=True)
        st.set([(text, None)])
        d.add_button("Close", d.cancel, "primary")
        d.show()

    # ---- backups -----------------------------------------------------------------------------------
    def backup_root(self):
        r = self.app.settings.get("backup_root")
        if r:
            return r
        return con.default_backup_root() if con is not None else os.path.expanduser("~")

    def change_root(self):
        d = filedialog.askdirectory(parent=self.app.root, title="Where bootloader backups go")
        if d:
            self.app.settings.set("backup_root", d)
            self.load_backups()

    def load_backups(self):
        root = self.backup_root()
        self.root_label.configure(text=root)
        if con is None:
            return

        def done(items):
            self.backups = items
            ctx, p = self.ctx, self.pal
            for w in self.bk_list.winfo_children():
                w.destroy()
            if not items:
                widgets.label(self.bk_list, ctx, "No backups yet. One is made before the first install on each "
                              "console.", 12, color=p.dim).pack(anchor="w", pady=S(4))
            for b in items:
                r = tk.Frame(self.bk_list, bg=p.panel)
                r.pack(fill="x", pady=S(3))
                tk.Label(r, image=images.icon("shield", S(18), p.muted), bg=p.panel).pack(side="left", anchor="n")
                t = tk.Frame(r, bg=p.panel)
                t.pack(side="left", padx=(S(10), 0), fill="x", expand=True)
                model = _backup_model(b)
                widgets.label(t, ctx, "%s   \u00b7   %s %s" % (b.get("date", "?"), model.name, b.get("ident", "?")),
                              12).pack(anchor="w")
                widgets.label(t, ctx, "   ".join("%s %s" % (part, _kind(b.get(part))) for part in model.partitions),
                              11, color=p.dim).pack(anchor="w")
                if b.get("ok") is False:
                    widgets.label(t, ctx, "Damaged: " + "; ".join(b.get("problems") or []), 11,
                                  color=p.danger, wraplength=S(400)).pack(anchor="w", fill="x")
                tk.Frame(self.bk_list, bg=p.line, height=1).pack(fill="x")

        def fail(e):
            for w in self.bk_list.winfo_children():
                w.destroy()
            widgets.label(self.bk_list, self.ctx, str(e), 12, color=self.pal.danger).pack(anchor="w")
        self.app.run_bg(lambda: con.list_backups(root), done, fail)

    # ---- the operations ----------------------------------------------------------------------------
    def _busy(self, on, text=""):
        self.busy = on
        for w in self.prog_frame.winfo_children():
            w.destroy()
        if on:
            ctx, p = self.ctx, self.pal
            top = tk.Frame(self.prog_frame, bg=p.panel)
            top.pack(fill="x")
            self.prog_text = widgets.label(top, ctx, text, 12, color=p.muted)
            self.prog_text.pack(side="left", fill="x", expand=True)
            self.cancel_btn = widgets.PillButton(top, ctx, "Cancel", lambda: self.cancel and self.cancel.set(), "ghost", small=True)
            self.cancel_btn.pack(side="right")
            self.prog = widgets.Progress(self.prog_frame, ctx)
            self.prog.pack(fill="x", pady=(S(6), 0))
            self.prog.set(None)
        self._render_actions()

    def _progress(self, frac, msg):
        if not self.busy:
            return
        try:
            self.prog.set(frac)
            self.prog_text.configure(text=msg)
            if frac is not None and "rit" in msg.lower():
                self.cancel_btn.enabled(False)
        except tk.TclError:
            pass
        self.app.status(msg)

    def _prog_cb(self):
        return lambda frac, msg: self.app.post(self._progress, frac, msg)

    def _fail(self, e):
        self._busy(False)
        self.app.status(str(e))
        widgets.tell(self.app, "That did not work", str(e) or e.__class__.__name__, "danger")
        self.load_backups()

    def do_backup(self):
        link = self.link
        if not link:
            return
        self.cancel = threading.Event()
        self._busy(True, "Backing up...")
        root, cancel = self.backup_root(), self.cancel

        def done(path):
            self._busy(False)
            self.app.status("Backed up to %s" % path)
            widgets.tell(self.app, "Backed up", "The console's bootloader (%s) is saved in\n%s"
                         % (link.model.partitions_text, path))
            self.load_backups()
        self.app.run_bg(lambda: con.backup_lk(link, root, cancel, self._prog_cb()), done, self._fail)

    def _confirm_write(self, what, extra=""):
        model = self.link.model
        if len(model.partitions) > 1:
            text = "Both bootloader slots (%s) of %s get %s.%s" % (model.partitions_text, self.link.label, what, extra)
        else:
            text = "The bootloader (%s) of %s gets %s.%s" % (model.partitions_text, self.link.label, what, extra)
        return widgets.ask(self.app, "Write %s to the console?" % what, text,
                           "Write it", kind="danger", notice=("danger", "A bootloader write", RISKS),
                           confirm_text="I understand the risks, the console is charged and stays connected")

    def _write(self, images_, what):
        """Back up first when this console has none here, then write and check."""
        link = self.link
        root = self.backup_root()
        self.cancel = threading.Event()
        cancel = self.cancel
        self._busy(True, "Preparing...")
        prog = self._prog_cb()

        def work():
            mine = [b for b in con.list_backups(root) if b.get("ident") == link.ident]
            made = None
            if not mine:
                self.app.post(self._progress, None, "No backup of this console yet: backing it up first...")
                made = con.backup_lk(link, root, cancel, prog)
            con.write_lk(link, images_, cancel, prog, verify=True)
            return made

        def done(made):
            self._busy(False)
            self.load_backups()
            msg = "%s is on the console and was read back to check it." % what[:1].upper() + what[1:]
            if made:
                msg += "\n\nIts previous bootloader was backed up first, to\n%s" % made
            msg += "\n\nUnplug the console and hold Power to start it."
            self.app.status("Done: %s written." % what)
            widgets.tell(self.app, "Done", msg)
        self.app.run_bg(work, done, self._fail, name="write-lk")

    def _flash_source(self, which):
        """Ask where the image comes from; returns ("latest", None), ("file", path) or None."""
        d = widgets.Dialog(self.app, "Install GammaOS Pico" if which == "gammaos" else "Restore the stock bootloader")
        ctx = self.ctx
        var = tk.StringVar(value="latest")
        rel = self.app.release
        widgets.Radio(d.body, ctx, "The latest release%s, downloaded and checked" % ((" (" + rel.version + ")") if rel else ""),
                      var, "latest").pack(anchor="w", pady=S(4))
        widgets.Radio(d.body, ctx, "A flash package%s I have" % (" or LK image" if which == "gammaos" else ""), var,
                      "file").pack(anchor="w", pady=S(4))
        d.add_button("Continue", lambda: d.done(var.get()), "primary")
        d.add_button("Cancel", d.cancel)
        r = d.show()
        if r == "file":
            prefixes = [self.link.model] if self.link is not None else list(MODELS)
            types = [("Flash package", " ".join("%s-*-flash.zip" % m.package_prefix for m in prefixes)),
                     ("Zip files", "*.zip")]
            if which == "gammaos":
                types.append(("LK image", "*.img *.bin"))
            f = filedialog.askopenfilename(parent=self.app.root, title="A GammaOS Pico flash package",
                                           filetypes=types + [("All files", "*")])
            return ("file", f) if f else None
        return ("latest", None) if r else None

    def _load_image(self, src, which, done):
        """The image bytes and a label, read and checked off the window's thread, then done(...)."""
        self._busy(True, "Getting the bootloader image...")
        cancel = threading.Event()
        self.cancel = cancel
        model = self.link.model

        def work():
            kind, path = src
            if kind == "latest":
                rel = self.app.release or github.latest()
                self.app.release = rel
                flash = rel.flash_for(model)
                sums = github.fetch_sums(rel)
                path = github.download(flash, sums, cancel=cancel, progress=lambda dn, t: self.app.post(
                    self._progress, dn / float(t) if t else None, "Downloading %s (%s)" % (flash.name, human(dn))))
            if path.lower().endswith(".zip"):
                imgs = flashpkg.read_images(path, model)
                if which not in imgs:
                    raise flashpkg.FlashPackageError("This flash package has no %s bootloader." % which)
                data = imgs[which]
            else:
                with open(path, "rb") as f:
                    data = f.read()
            info = con.check_lk_image(data, model)
            return data, info

        def ok(r):
            self._busy(False)
            data, info = r
            if info.problems:
                widgets.tell(self.app, "This image is refused", "\n".join(info.problems), "danger")
                return
            if which == "stock" and info.kind != "stock":
                widgets.tell(self.app, "Not the stock bootloader", "The image is %s, not the stock bootloader."
                             % info.kind, "danger")
                return
            if which == "gammaos" and info.kind != "gammaos-pico":
                if not widgets.ask(self.app, "Not a GammaOS Pico image",
                                   "This image is reported as %s, not GammaOS Pico. Write it anyway?" % info.kind,
                                   "Continue", kind="danger"):
                    return
            done(data, info)
        self.app.run_bg(work, ok, self._fail)

    def do_install(self):
        if not self.link:
            return
        src = self._flash_source("gammaos")
        if not src:
            return

        def go(data, info):
            what = "GammaOS Pico %s" % (info.version or "")
            if self._confirm_write(what.strip()):
                self._write({part: data for part in self.link.model.partitions}, what.strip())
        self._load_image(src, "gammaos", go)

    def do_stock(self):
        if not self.link:
            return
        src = self._flash_source("stock")
        if not src:
            return

        def go(data, info):
            if self._confirm_write("the stock bootloader", " GammaOS Pico then no longer starts, whatever is on the card."):
                self._write({part: data for part in self.link.model.partitions}, "the stock bootloader")
        self._load_image(src, "stock", go)

    def do_restore(self):
        if not self.link:
            return
        if not self.backups:
            widgets.tell(self.app, "No backups", "There is no backup in %s yet." % self.backup_root())
            return
        d = widgets.Dialog(self.app, "Restore a backup", width=620)
        ctx, p = self.ctx, self.pal
        lb = tk.Listbox(d.body, bg=p.field, fg=p.ink, bd=0, highlightthickness=0, activestyle="none",
                        selectbackground=theme.mix(p.field, p.accent, 0.35), font=ctx.font(12), height=10)
        lb.pack(fill="both", expand=True)
        for b in self.backups:
            mine = " (this console)" if b.get("ident") == self.link.ident else ""
            lb.insert("end", "  %s   %s %s%s   lk_a %s" % (b.get("date"), _backup_model(b).name, b.get("ident"), mine,
                                                         _kind(b.get("lk_a"))))
        lb.selection_set(0)
        d.add_button("Restore", lambda: d.done(lb.curselection()[0] if lb.curselection() else None), "primary")
        d.add_button("Cancel", d.cancel)
        i = d.show()
        if i is None:
            return
        b = self.backups[i]
        if b.get("ok") is False:
            widgets.tell(self.app, "This backup cannot be used", "; ".join(b.get("problems") or ["it is damaged"]), "danger")
            return
        model = self.link.model
        if _backup_model(b) is not model:
            widgets.tell(self.app, "This backup is for another console",
                         "It was made from a %s, and this console is a %s; choose a backup of this console."
                         % (_backup_model(b).name, model.name), "danger")
            return
        try:
            imgs = con.read_backup(b["path"], model)
        except (OSError, ValueError, con.ConsoleError) as e:
            widgets.tell(self.app, "This backup cannot be used", str(e), "danger")
            return
        for part in sorted(imgs):
            info = con.check_lk_image(imgs[part], model)
            if info.problems:
                widgets.tell(self.app, "This backup is refused", "%s: %s" % (part, "; ".join(info.problems)), "danger")
                return
        extra = ""
        if b.get("ident") != self.link.ident:
            extra = " This backup was made from another console (%s)." % b.get("ident")
        if self._confirm_write("the backup from %s" % b.get("date"), extra):
            self._write(imgs, "the backup from %s" % b.get("date"))


def first_install_text(model):
    """How GammaOS Pico goes on a console with its stock bootloader the first time."""
    copies = "only lk_a and lk_b" if len(model.partitions) > 1 else "only lk_a"
    return ("A %s with its stock bootloader has no fastboot this tool can use. The first time, GammaOS Pico "
            "goes on with SP Flash Tool v5 (V6 does not work), which the release has with MediaTek's USB driver "
            "in %s: load %s from the flash package's spflash/gammaos/ (%s), choose Download Only with %s "
            "ticked, then connect the powered-off console. The Installing page shows every step. From then "
            "on, this tab backs the bootloader up, updates it and puts the stock one back."
            % (model.name, model.windows_tools, model.scatter, model.flash_package, copies))


def _backup_model(entry):
    if con is not None and hasattr(con, "backup_model"):
        return con.backup_model(entry)
    return by_key(entry.get("model"))


def _kind(part):
    if not isinstance(part, dict):
        return "?"
    k = {"gammaos-pico": "GammaOS Pico", "stock": "stock", "unknown": "unknown"}.get(part.get("kind"), part.get("kind", "?"))
    return k + ((" " + part["version"]) if part.get("version") else "")
