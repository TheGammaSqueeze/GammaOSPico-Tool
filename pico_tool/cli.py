"""The command line: everything the window does, for scripts.

  gammaos-pico-tool cards                         the cards on this computer
  gammaos-pico-tool status CARD                   what is on a card, and the latest release
  gammaos-pico-tool setup CARD [--console kpa|block] [--zip Z] [--bios F] [--no-demo] [--yes] [--dry-run]
  gammaos-pico-tool update CARD [--console kpa|block] [--zip Z] [--demo] [--yes] [--dry-run]
  gammaos-pico-tool bios CARD FILE [--force]
  gammaos-pico-tool scan CARD [--system S] [--missing] [--json]
  gammaos-pico-tool scrape CARD [--system S] [--game NAME] [--all] [--source ...] [--region R]
  gammaos-pico-tool art set CARD GAME IMAGE [--system S]
  gammaos-pico-tool art rm CARD GAME [--system S]
  gammaos-pico-tool console detect | drivers | backups
  gammaos-pico-tool console backup [--dest DIR] [--serial ID]
  gammaos-pico-tool console flash [--zip FLASH_ZIP | --image LK_IMG | --latest] [--yes] [--serial ID]
  gammaos-pico-tool console restore BACKUP_DIR [--yes] [--serial ID]
  gammaos-pico-tool console stock [--zip FLASH_ZIP | --latest] [--yes] [--serial ID]
  gammaos-pico-tool check-network                 can GitHub and ScreenScraper be reached securely

CARD is the card's folder (a drive letter such as E:\\ on Windows, /Volumes/NAME on macOS, the mount
point on Linux). The consoles are the Konkr Pocket Advance ("kpa") and the Konkr Pocket Block
("block"): the console commands find out which one is connected, and setup and update download the
card package of the console given with --console, or of the last one the tool saw (the Pocket
Advance until then). Both consoles' card packages hold the same files.
"""

import argparse
import json
import os
import sys
import threading

from . import __version__, REPO, WIKI_INSTALLING, config


class CliError(Exception):
    """A failure to report in one line (exit status 1)."""


def _out(*a):
    print(*a)
    sys.stdout.flush()


def _err(msg):
    sys.stderr.write("error: %s\n" % msg)
    sys.stderr.flush()


def _confirm(question, yes):
    if yes:
        return True
    if not sys.stdin.isatty():
        _err("%s Add --yes to go ahead without a question." % question)
        return False
    try:
        return input(question + " [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _bar(done, total, label=""):
    if not sys.stderr.isatty():
        return
    frac = done / float(total) if total else 1.0
    n = int(frac * 30)
    sys.stderr.write("\r  [%s%s] %3d%% %s" % ("#" * n, "." * (30 - n), int(frac * 100), label[:40].ljust(40)))
    if done >= total:
        sys.stderr.write("\n")
    sys.stderr.flush()


def _card(path):
    p = os.path.abspath(path)
    if not os.path.isdir(p):
        raise CliError("%s is not a folder." % path)
    return p


# ---- cards, status, setup, update, bios -----------------------------------------------------------

def cmd_cards(a):
    from .card.volumes import list_volumes
    vols = list_volumes()
    if a.json:
        _out(json.dumps([{"path": v.path, "label": v.label, "fs": v.fs_label, "fs_ok": v.fs_ok, "free": v.free,
                          "total": v.total, "gammaos_pico": v.has_pico, "version": v.version} for v in vols], indent=1))
        return 0
    if not vols:
        _out("No removable cards found. Put the card in, or give its folder to the other commands.")
        return 0
    for v in vols:
        note = "" if v.fs_ok else "   (%s: the console reads FAT32 or exFAT, see %s)" % (v.fs_label, WIKI_INSTALLING)
        pico = ("GammaOS Pico " + (v.version or "(version unknown)")) if v.has_pico else "no GammaOS Pico"
        _out("%-24s %-12s %-6s %8s free  %s%s" % (v.path, v.label[:12], v.fs_label, _size(v.free), pico, note))
    return 0


def _size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0


def cmd_status(a):
    from .card.layout import card_version, has_pico, installed_bios, check_bios
    from .card.volumes import folder_volume
    from .release import github
    root = _card(a.card)
    v = folder_volume(root)
    _out("Card:        %s (%s, %s free of %s)" % (root, v.fs_label, _size(v.free), _size(v.total)))
    if not v.fs_ok:
        _out("             The console reads FAT32 or exFAT cards: see %s" % WIKI_INSTALLING)
    if has_pico(root):
        _out("GammaOS Pico: %s" % (card_version(root) or "installed (version unknown)"))
    else:
        _out("GammaOS Pico: not on this card")
    b = installed_bios(root)
    if b:
        chk = check_bios(b)
        _out("GBA BIOS:    %s" % ("yes" if chk.known else ("present: " + chk.message)))
    else:
        _out("GBA BIOS:    none (optional: bios/gba_bios.bin)")
    try:
        rel = github.latest()
        _out("Latest:      %s" % rel.version)
    except github.ReleaseError as e:
        _out("Latest:      unknown (%s)" % e)
    return 0


def _chosen_model(a=None):
    """The console whose packages to download: --console, else the one the tool last saw."""
    from .console import models
    key = getattr(a, "console", None) or config.Settings().get("console_model")
    return models.by_key(key)


def _remember_model(link):
    model = getattr(link, "model", None)
    if model is not None:
        s = config.Settings()
        if s.get("console_model") != model.key:
            s.set("console_model", model.key)


def _get_package(a):
    """The card package to use: --zip, or the latest release's for the console, downloaded and checked."""
    from .release import github
    if a.zip:
        checked, msg = github.verify_local(a.zip)
        _out("Package: %s (%s)" % (a.zip, msg))
        return a.zip
    model = _chosen_model(a)
    _out("Looking for the latest release for the %s..." % model.name)
    rel = github.latest()
    _out("Latest release: %s" % rel.version)
    card = rel.card_for(model)
    sums = github.fetch_sums(rel)
    _out("Downloading %s..." % card.name)
    path = github.download(card, sums, progress=lambda d, t: _bar(d, t, card.name))
    _out("Checked against SHA256SUMS: %s" % path)
    return path


def _install(a, demo):
    from .card.layout import install_bios, check_bios
    from .release import install
    root = _card(a.card)
    if a.bios:
        chk = check_bios(a.bios)
        if not chk.ok:
            _err(chk.message)
            return 1
        if not chk.known and not a.force:
            _err(chk.message + " Add --force to use it anyway.")
            return 1
    zp = _get_package(a)
    p = install.plan(zp, root, demo_games=demo)
    _out("Package version %s -> %s" % (p.version or "?", root))
    _out(p.summary())
    for it in p.items:
        if it.action in (install.NEW, install.REPLACE) or a.verbose:
            _out("  %-8s %s" % (it.action, it.rel))
    if a.dry_run:
        return 0
    if not p.of(install.NEW) and not p.of(install.REPLACE):
        _out("The card is up to date.")
    elif not _confirm("Write these files to %s?" % root, a.yes):
        return 1
    else:
        install.apply(p, progress=lambda d, t, r: _bar(d, t, r))
        _out("Done.")
    if a.bios:
        _out("BIOS copied to %s" % install_bios(root, a.bios))
    return 0


def cmd_setup(a):
    from .card.layout import has_pico
    if has_pico(_card(a.card)):
        _out("This card already has GammaOS Pico: your files are kept (this is the same as update).")
    return _install(a, not a.no_demo)


def cmd_update(a):
    return _install(a, a.demo)


def cmd_bios(a):
    from .card.layout import check_bios, install_bios
    root = _card(a.card)
    chk = check_bios(a.file)
    if not chk.ok:
        _err(chk.message)
        return 1
    if not chk.known:
        if not a.force:
            _err(chk.message + " Add --force to use it anyway.")
            return 1
        _out("Warning: " + chk.message)
    _out("BIOS copied to %s" % install_bios(root, a.file))
    return 0


# ---- games and box art ----------------------------------------------------------------------------

def _session(card):
    from .services import CardSession
    s = CardSession(_card(card))
    s.scan()
    return s


def cmd_scan(a):
    from .art import store
    from .card.systems import BY_KEY, system_for_folder
    s = _session(a.card)
    res = s.result
    games = res.games
    if a.system:
        sysm = BY_KEY.get(a.system.lower()) or system_for_folder(a.system)
        if not sysm:
            _err("unknown system %r" % a.system)
            return 2
        games = [g for g in games if g.type == sysm.type]
    rows = []
    for g in games:
        state, path = s.index.find(g)
        if a.missing and state != store.NO_ART:
            continue
        rows.append((g, state, path))
    if a.json:
        _out(json.dumps([{"system": g.system.key, "name": g.display_name, "title": g.title, "folder": g.cdir,
                          "file": g.file, "archive": ["", "zip", "7z"][g.arc], "member": g.member_text,
                          "size": g.size, "crc": ("%08x" % g.crc) if g.crc is not None else None,
                          "cover": state, "cover_file": path,
                          "problem": None if not g.status else _reason(g.status)} for g, state, path in rows], indent=1))
        return 0
    for g, state, path in rows:
        mark = {store.HAVE_ART: "art", store.HAVE_PNG: "png", store.NO_ART: "-", store.CANNOT: "!"}[state]
        extra = "  [%s]" % _reason(g.status) if g.status else ""
        _out("%-5s %-4s %-60s %s%s" % (g.system.key, mark, g.title[:60], g.cdir, extra))
    with_art = sum(1 for _g, st, _p in rows if st == store.HAVE_ART)
    _out("%d games (%d with a cover)%s" % (len(rows), with_art,
                                           "; %d more past the console's limit" % (res.total - len(res.games))
                                           if res.total > len(res.games) else ""))
    return 0


def _reason(code):
    from .card.archives import REASONS
    return REASONS.get(code, "cannot start")


def cmd_scrape(a):
    from .scrape import batch, scraper
    from .services import find_games
    settings = config.Settings()
    over = {}
    if a.source:
        over["scrape_source"] = a.source
    if a.region:
        over["scrape_region"] = a.region
    conf = dict(settings.data, **over)
    sc = scraper.make_scraper(conf)
    if not sc.ready:
        _err(sc.unready_reason())
        return 1
    s = _session(a.card)
    games = s.result.games
    if a.game:
        games = []
        for q in a.game:
            found = find_games(s.result, q, a.system)
            if not found:
                _err("no game matches %r" % q)
                return 1
            games.extend(found)
    elif a.system:
        games = find_games(s.result, "", a.system)
    cancel = threading.Event()

    def prog(i, n, g, msg):
        _out("[%d/%d] %s: %s" % (i, n, g.title, msg))
    try:
        out = batch.run(games, sc, s.index, overwrite=a.all, progress=prog, cancel=cancel, crc_cache=s.crc_cache)
    except KeyboardInterrupt:
        cancel.set()
        _out("Stopped.")
        return 1
    finally:
        s.save_caches()
    _out(out.summary())
    if out.stopped:
        _err(out.stopped)
        return 1
    return 0


def _one_game(s, name, system):
    from .services import find_games
    found = find_games(s.result, name, system)
    if not found:
        raise CliError("no game matches %r" % name)
    if len(found) > 1:
        for g in found[:20]:
            _out("  %s  %s  (%s)" % (g.system.key, g.display_name, g.cdir))
        raise CliError("%r matches %d games; be more precise or add --system" % (name, len(found)))
    return found[0]


def cmd_art(a):
    from .art import artfile, store
    s = _session(a.card)
    g = _one_game(s, a.game, a.system)
    if a.art_cmd == "set":
        try:
            data = artfile.encode_file(a.image)
        except (OSError, artfile.ArtError) as e:
            _err(str(e))
            return 1
        try:
            _out("Cover written: %s" % store.write_art(s.index, g, data))
        except (OSError, artfile.ArtError) as e:
            _err(str(e))
            return 1
        return 0
    gone = store.remove_art(s.index, g)
    _out("Removed: %s" % (", ".join(gone) if gone else "nothing (the game had no cover)"))
    return 0


# ---- the console ----------------------------------------------------------------------------------

def _console():
    try:
        from . import console
    except ImportError as e:
        raise CliError("this build has no console support (%s)" % e)
    return console


def _cprog(frac, msg):
    if frac is None:
        if sys.stderr.isatty():
            sys.stderr.write("\r  %s" % msg[:70].ljust(70))
            sys.stderr.flush()
        else:
            _out("  " + msg)
    else:
        _bar(int(frac * 1000), 1000, msg)


def _backup_root(a=None):
    con = _console()
    s = config.Settings()
    return (getattr(a, "dest", None) or s.get("backup_root") or con.default_backup_root())


def _pick(links, serial):
    """The console to work with: the one with --serial, or the only one connected."""
    if serial:
        for link in links:
            if link.ident == serial:
                return link
        return None
    if len(links) > 1:
        raise CliError("%d consoles are connected (%s); say which one with --serial"
                       % (len(links), ", ".join("%s %s" % (l.model.name, l.ident) for l in links)))
    return links[0] if links else None


def _wait(con, serial=None):
    link = _pick(con.find_consoles(), serial)
    if link:
        return link
    _out("Waiting for the console%s: hold SELECT + START + R while turning it on, then connect USB "
         "(Ctrl+C to stop)." % ((" " + serial) if serial else ""))
    cancel = threading.Event()
    try:
        while True:
            found = con.wait_for_console({"fastboot"}, cancel, _cprog)
            link = _pick(con.find_consoles(), serial) if serial else found
            if link:
                return link
            cancel.wait(1.0)
    except KeyboardInterrupt:
        cancel.set()
        raise CliError("stopped")


def cmd_console(a):
    con = _console()
    try:
        if a.con_cmd == "drivers":
            _out(con.driver_help())
            return 0
        if a.con_cmd == "backups":
            root = _backup_root(a)
            items = con.list_backups(root)
            if not items:
                _out("No backups in %s" % root)
            for b in items:
                model = _backup_model(con, b)
                parts = ", ".join("%s %s" % (p, _kind(b.get(p))) for p in model.partitions)
                _out("%s  %s  %s  %s  %s" % (b.get("date"), b.get("ident"), model.name, parts, b.get("path")))
            return 0
        if a.con_cmd == "detect":
            links = con.find_consoles()
            if not links:
                _out("No console found. Hold SELECT + START + R while turning it on, then connect USB.")
                _out("First install on a console with the stock bootloader: see %s" % WIKI_INSTALLING)
                return 1
            for l in links:
                _out("%s  (id %s, bootloader %s)" % (l.label, l.ident, l.lk_version or "unknown"))
            if len(links) == 1:
                _remember_model(links[0])
            return 0
        link = _wait(con, getattr(a, "serial", None))
        _out("Found: %s (id %s)" % (link.label, link.ident))
        _remember_model(link)
        root = _backup_root(a)
        cancel = threading.Event()
        if a.con_cmd == "backup":
            path = con.backup_lk(link, root, cancel, _cprog)
            _out("Backed up to %s" % path)
            return 0
        images, what = _images_for(a, con, link)
        _out(WARNING_TEXT)
        if not _ensure_backup(con, link, root, cancel):
            return 1
        if not _confirm("Write %s to %s of this %s?" % (what, link.model.partitions_text, link.model.name), a.yes):
            return 1
        con.write_lk(link, images, cancel, _cprog, verify=True)
        _out("Done: %s is on the console. Restart it (hold Power) with the card in." % what)
        return 0
    except con.ConsoleError as e:
        _err(str(e))
        return 1


def _kind(part):
    if not isinstance(part, dict):
        return "?"
    return "%s%s" % (part.get("kind", "?"), (" " + part["version"]) if part.get("version") else "")


WARNING_TEXT = ("GammaOS Pico is a custom bootloader: experimental, with no warranty. Writing a bootloader can "
                "leave the console unable to start. Android's OTA updates may stop working while it is "
                "installed. Keep the console charged and connected until this ends.")


def _ensure_backup(con, link, root, cancel):
    """The console's bootloader is backed up before the first write (and after any write it has none)."""
    mine = [b for b in con.list_backups(root) if b.get("ident") == link.ident]
    if mine:
        return True
    _out("This console has no backup yet: backing up its bootloader first...")
    path = con.backup_lk(link, root, cancel, _cprog)
    _out("Backed up to %s" % path)
    return True


def _backup_model(con, entry):
    if hasattr(con, "backup_model"):
        return con.backup_model(entry)
    from .console import models
    return models.by_key(entry.get("model"))


def _images_for(a, con, link):
    """{partition: image} for the connected console (lk_a and lk_b, or lk_a alone on the Pocket
    Block), and a few words for what it is. Anything for the other console is refused here."""
    from .release import flashpkg
    model = link.model
    if a.con_cmd == "restore":
        return con.read_backup(a.backup, model), "the backup from %s" % a.backup
    if getattr(a, "image", None):
        with open(a.image, "rb") as f:
            data = f.read()
        info = con.check_lk_image(data, model)
        if info.problems:
            raise CliError("; ".join(info.problems))
        return {p: data for p in model.partitions}, "%s (%s)" % (os.path.basename(a.image), info.kind)
    zp = a.zip or _latest_flash(model)
    imgs = flashpkg.read_images(zp, model)
    key = "stock" if a.con_cmd == "stock" else "gammaos"
    if key not in imgs:
        raise CliError("the flash package has no %s bootloader" % key)
    info = con.check_lk_image(imgs[key], model)
    if info.problems:
        raise CliError("; ".join(info.problems))
    label = "the stock bootloader" if key == "stock" else "GammaOS Pico %s" % (info.version or imgs.get("version") or "")
    return {p: imgs[key] for p in model.partitions}, label.strip()


def _latest_flash(model):
    from .release import github
    rel = github.latest()
    flash = rel.flash_for(model)
    sums = github.fetch_sums(rel)
    _out("Downloading %s..." % flash.name)
    return github.download(flash, sums, progress=lambda d, t: _bar(d, t, flash.name))


# ---- the network check ---------------------------------------------------------------------------

NETWORK_CHECKS = (
    ("github", "GitHub (releases)", "https://api.github.com/repos/%s/releases/latest" % REPO, True),
    ("screenscraper", "ScreenScraper (box art)", "https://api.screenscraper.fr/api2/", False),
)


def cmd_check_network(a):
    """Reach GitHub and ScreenScraper once each over HTTPS. Exit status 1 when a secure connection
    fails its checks anywhere, or GitHub cannot be reached at all; a ScreenScraper that is down
    (it often is) is reported but does not fail the check. Nothing about the built-in ScreenScraper
    access is printed but whether this build has it, and no address with a query is ever sent."""
    from . import net
    from .scrape.scraper import builtin_credentials, redact
    try:
        source = net.source()
    except RuntimeError as e:
        _err(str(e))
        return 1
    _out("Certificates: %s" % net.SOURCE_TEXT.get(source, source))
    for note in net.passed_over():
        _out("              not used: %s" % redact(note))
    failed = False
    ua = "gammaos-pico-tool/" + __version__
    for key, name, url, required in NETWORK_CHECKS:
        reached, tls_problem, detail = net.probe(url, timeout=a.timeout, user_agent=ua)
        access = ""
        if key == "screenscraper":
            # whether the access is there, never what it is
            has = builtin_credentials() is not None
            access = "; this build has its built-in access" if has else "; no built-in access in this build"
            if reached:
                detail = "built-in access" if has else "no built-in access in this build"
                access = ""
        label = "%-24s" % (name + ":")
        if reached:
            _out("%s reached (%s)" % (label, detail))
        elif tls_problem:
            _out("%s secure connection failed (%s)%s" % (label, redact(detail), access))
            failed = True
        else:
            _out("%s not reached (%s)%s%s" % (label, redact(detail), "" if required else ", not counted", access))
            failed = failed or required
    return 1 if failed else 0


# ---- the parser -----------------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(prog="gammaos-pico-tool",
                                description="Set up GammaOS Pico microSD cards, manage their box art, and back "
                                            "up or update the console's bootloader. With no command, the "
                                            "window opens.")
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    sub = p.add_subparsers(dest="cmd", metavar="COMMAND")

    c = sub.add_parser("cards", help="list the removable cards on this computer")
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_cards)

    c = sub.add_parser("status", help="what is on a card, and the latest release")
    c.add_argument("card")
    c.set_defaults(func=cmd_status)

    for name, fn, helptext in (("setup", cmd_setup, "put GammaOS Pico on a new card"),
                               ("update", cmd_update, "update a card, keeping your files")):
        c = sub.add_parser(name, help=helptext)
        c.add_argument("card")
        c.add_argument("--zip", help="a card package you downloaded (default: the latest release)")
        c.add_argument("--bios", help="also copy this Game Boy Advance BIOS to bios/gba_bios.bin")
        c.add_argument("--force", action="store_true", help="accept a BIOS of the right size but unknown md5")
        c.add_argument("--console", choices=("kpa", "block"),
                       help="whose card package to download: kpa (Konkr Pocket Advance) or block (Konkr Pocket "
                            "Block); default: the console the tool last saw, else kpa. The files are the same")
        if name == "setup":
            c.add_argument("--no-demo", action="store_true", help="leave the demo games out")
        else:
            c.add_argument("--demo", action="store_true", help="add the demo games that are not on the card")
        c.add_argument("--dry-run", action="store_true", help="only show what would change")
        c.add_argument("--yes", "-y", action="store_true", help="do not ask")
        c.add_argument("--verbose", "-v", action="store_true", help="list every file")
        c.set_defaults(func=fn)

    c = sub.add_parser("bios", help="copy a Game Boy Advance BIOS to the card")
    c.add_argument("card")
    c.add_argument("file")
    c.add_argument("--force", action="store_true", help="accept a BIOS of the right size but unknown md5")
    c.set_defaults(func=cmd_bios)

    c = sub.add_parser("scan", help="list the games the console sees on a card")
    c.add_argument("card")
    c.add_argument("--system", help="gb gbc gba snes genesis sms gg sg")
    c.add_argument("--missing", action="store_true", help="only games without a cover")
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_scan)

    c = sub.add_parser("scrape", help="download covers for games that have none")
    c.add_argument("card")
    c.add_argument("--system")
    c.add_argument("--game", action="append", help="only this game (repeat for more)")
    c.add_argument("--all", action="store_true", help="replace covers that are there too")
    c.add_argument("--source", choices=("screenscraper", "thegamesdb"))
    c.add_argument("--region", help="wor us eu jp ...")
    c.set_defaults(func=cmd_scrape)

    c = sub.add_parser("art", help="set or remove one game's cover")
    asub = c.add_subparsers(dest="art_cmd", metavar="ACTION")
    asub.required = True
    s = asub.add_parser("set", help="use a picture file (PNG, JPEG, WebP...) as the cover")
    s.add_argument("card")
    s.add_argument("game")
    s.add_argument("image")
    s.add_argument("--system")
    s = asub.add_parser("rm", help="remove the cover")
    s.add_argument("card")
    s.add_argument("game")
    s.add_argument("--system")
    c.set_defaults(func=cmd_art)

    c = sub.add_parser("console", help="the console's bootloader over USB (fastboot): Konkr Pocket Advance or "
                                       "Konkr Pocket Block, found out by itself")
    csub = c.add_subparsers(dest="con_cmd", metavar="ACTION")
    csub.required = True
    csub.add_parser("detect", help="find the console")
    csub.add_parser("drivers", help="USB setup help for this system")
    s = csub.add_parser("backups", help="list the bootloader backups")
    s.add_argument("--dest", help="the backup folder")
    s = csub.add_parser("backup", help="back up the bootloader (lk_a and lk_b; lk_a on the Pocket Block)")
    s.add_argument("--dest", help="the backup folder")
    s.add_argument("--serial", help="the console's id (from detect), when more than one is connected")
    s = csub.add_parser("flash", help="install or update GammaOS Pico's bootloader")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--zip", help="a flash package for the connected console (gammaos-pico-kpa-<version>-flash.zip "
                                 "or gammaos-pico-block-<version>-flash.zip)")
    g.add_argument("--image", help="an LK image file")
    g.add_argument("--latest", action="store_true", help="the latest release (the default)")
    s.add_argument("--yes", "-y", action="store_true")
    s.add_argument("--dest", help="the backup folder")
    s.add_argument("--serial", help="the console's id (from detect), when more than one is connected")
    s = csub.add_parser("restore", help="write a backup back")
    s.add_argument("backup", help="the backup's folder")
    s.add_argument("--yes", "-y", action="store_true")
    s.add_argument("--dest", help="the backup folder")
    s.add_argument("--serial", help="the console's id (from detect), when more than one is connected")
    s = csub.add_parser("stock", help="put the stock bootloader back")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--zip", help="a flash package")
    g.add_argument("--latest", action="store_true")
    s.add_argument("--yes", "-y", action="store_true")
    s.add_argument("--dest", help="the backup folder")
    s.add_argument("--serial", help="the console's id (from detect), when more than one is connected")
    c.set_defaults(func=cmd_console)

    c = sub.add_parser("check-network",
                       help="check that GitHub and ScreenScraper can be reached over HTTPS, and say where the "
                            "certificates come from (the system's trust store, or the bundled certifi list)",
                       description="Reach GitHub's release API and ScreenScraper once each. Exit status 1 when a "
                                   "secure connection fails its certificate checks, or when GitHub cannot be "
                                   "reached; ScreenScraper being down is reported but not counted.")
    c.add_argument("--timeout", type=float, default=20.0, help="seconds to wait for each server (default 20)")
    c.set_defaults(func=cmd_check_network)
    return p


def main(argv=None):
    from .release.flashpkg import FlashPackageError
    from .release.github import ReleaseError
    from .release.install import InstallError
    p = build_parser()
    a = p.parse_args(argv)
    if not getattr(a, "func", None):
        p.print_help()
        return 0
    try:
        return a.func(a) or 0
    except (ReleaseError, InstallError, FlashPackageError, CliError) as e:
        _err(str(e))
        return 1
    except KeyboardInterrupt:
        _err("stopped")
        return 1


if __name__ == "__main__":
    sys.exit(main())
