"""The games on a card, listed exactly the way the console lists them.

A port of the console's card scan (gba_sd_list_roms and its helpers in the bootloader's sd_fat.c,
with rom_archive.c for archives). Where the games may be, in this order, a game found again later
being left out:

  1. /roms/<system>                        GammaOS Pico's own layout
  2. /<system>                             at the card's root
  3. /<folder>/<system> and /<folder>/roms/<system> for every other folder at the root
                                           (ES-DE's /ROMs, EmuDeck's /Emulation/roms, ...)
  4. the folders listed in /gammaos/folders.txt

Folder and extension names are matched without case. Empty files and names starting with "." are
skipped. A .zip or .7z in a system folder is listed as the ROMs inside it (its own name when it
holds one ROM or cannot be read), and a ROM that is also on the card unpacked is listed from the
unpacked file. Names are the real ones, in UTF-8 (names.py). The list is sorted by name with the
accents folded and A-Z in either case alike, stably. The console keeps at most 64 game folders and
32768 games.
"""

import os
import threading

from . import archives
from .fsview import CardFS
from .names import (NAME_MAX, LegacyForms, ascii_fold, clean_title, cut_bytes, cut_u8, display_text,
                    ends_with_any, from_bytes, named_forms, sort_key)
from .systems import BY_TYPE, system_for_folder

GAMES_MAX = 32768
ROM_DIRS = 64
ARCHIVE_ONE = 1024        # ROMs taken from one archive
PATH_CAP = 96             # the console's path buffers (95 characters and the end)
FOLDERS_MAX = 16384
NOT_ROM_ROOTS = ("gammaos", "saves", "states", "cores", "bios", "boxart", "gba_states")

ARC_NONE, ARC_ZIP, ARC_7Z = 0, 1, 2

FOLDERS_TEMPLATE = (
    "# GammaOS Pico: more folders to look for games in.\n"
    "#\n"
    "# One folder a line: the system, a space, then the folder's path from the card's root.\n"
    "# The console already looks in roms/<system>, in <system> at the card's root, and in\n"
    "# <any folder>/<system> and <any folder>/roms/<system> (see the wiki for the folder names it\n"
    "# knows). Lines starting with # are ignored. A change counts from the next power on.\n"
    "#\n"
    "# Systems: gb gbc gba snes genesis sms gg sg\n"
    "#\n"
    "# Examples (take the # away to use one):\n"
    "# gba /My Games/Game Boy Advance\n"
    "# snes /Collections/Super Nintendo\n")


class Game(object):
    """One game of the console's list."""
    __slots__ = ("type", "name", "title", "cdir", "dir", "file", "arc", "member", "member_text",
                 "size", "crc", "status", "art_stem", "index", "by_file")

    def __init__(self, gtype, name, cdir, osdir, osfile, arc, member, size):
        self.type = gtype           # the system's type number (systems.SYSTEMS)
        self.name = name            # the console's name (UTF-8, at most 127 bytes): saves, states, box art
        self.title = ""             # the menu's title
        self.cdir = cdir            # its folder, as the console writes the path ("/ROMs/gba")
        self.dir = osdir            # that folder on the computer
        self.file = osfile          # the ROM or archive file on the computer
        self.arc = arc              # ARC_NONE, ARC_ZIP, ARC_7Z
        self.member = member        # which ROM of the archive (0 the first)
        self.member_text = None     # the ROM's name inside the archive, for the screen
        self.size = size            # the ROM's size (unpacked)
        self.crc = None             # CRC32 when known (from the archive's tables, or hashed)
        self.status = archives.OK   # why the console would not start it, OK when it would
        self.art_stem = None        # the name the box art file is written under, None when none fits
        self.index = 0
        self.by_file = True         # named after its file (a ROM, or an archive of one), not a member

    @property
    def system(self):
        return BY_TYPE[self.type]

    @property
    def rom_name(self):
        """The ROM's own file name (inside the archive for one in an archive), for scrapers."""
        if self.arc and self.member_text:
            return self.member_text.replace("\\", "/").rsplit("/", 1)[-1]
        return os.path.basename(self.file)

    @property
    def display_name(self):
        return display_text(self.name)

    def __repr__(self):
        return "<Game %s %r %s>" % (self.system.key, self.name, self.cdir)


class ScanResult(object):
    def __init__(self, root):
        self.root = root
        self.games = []             # sorted, at most GAMES_MAX
        self.total = 0              # every game found, also past the cap
        self.folders = []           # (console path, computer path) of each game folder, in order
        self.user_folders = []      # folders.txt lines that were listed
        self.folders_txt = False    # /gammaos/folders.txt is on the card
        self.skipped_folders = 0    # game folders past the 64 the console reads
        self.archive_errors = 0
        self.legacy = None          # the games' underscore forms (names.LegacyForms), None when the
                                    # list was cut short (the console then reads no old form)

    def by_type(self, gtype):
        return [g for g in self.games if g.type == gtype]

    def counts(self):
        c = {}
        for g in self.games:
            c[g.type] = c.get(g.type, 0) + 1
        return c


def _join(a, b):
    """path_join: "a/b", cut to the console's 95 bytes."""
    return cut_bytes(a + "/" + b, PATH_CAP - 1)


class ArchiveCache(object):
    """What each archive holds, kept between scans (by path, size and time), so a big card is
    looked into once. Thread safe. `store` is a dict-like or None."""

    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.lock = threading.Lock()

    FORMAT = 2              # the names kept: 2 in UTF-8 (the console's archives.idx format 2)

    def get(self, path, size, mtime, kind, exts):
        key = "%d|%s|%d|%d|%s|%s" % (self.FORMAT, path, size, int(mtime), kind, ",".join(exts))
        with self.lock:
            hit = self.store.get(key)
        if hit is not None:
            if hit[0] == "err":
                return hit[1], None
            return archives.OK, [archives.Member(*m) for m in hit[1]]
        try:
            with open(path, "rb") as f:
                members = archives.list_archive(kind, f, size, exts)
            val = ("ok", [(m.raw, m.text, m.crc, m.size, m.status) for m in members])
            rc = archives.OK
        except archives.ArchiveError as e:
            members = None
            rc = e.code
            val = ("err", e.code)
        except OSError:
            members = None
            rc = archives.ERR_READ
            val = ("err", rc)
        with self.lock:
            self.store[key] = val
        return rc, members


class Scanner(object):
    """One listing of a card (gba_sd_list_roms)."""

    def __init__(self, root, archive_cache=None, progress=None, cancel=None, max_games=GAMES_MAX):
        self.max_games = min(max_games, GAMES_MAX)
        self.fs = CardFS(root)
        self.cache = archive_cache or ArchiveCache()
        self.progress = progress        # progress(message) now and then
        self.cancel = cancel            # threading.Event
        self.res = ScanResult(self.fs.root)
        self._dirs = []                 # identities of the game folders listed so far
        self._dup = {}                  # (type, name with A-Z small) -> Game

    # one game into the list (add_game)
    def _add(self, sysm, di, osfile, nm, arc, member, size, member_info=None):
        if not nm:
            return
        res = self.res
        if len(res.games) >= self.max_games:
            res.total += 1
            return
        # the console compares names as it keeps them (cut at 127 bytes), so a game in two folders
        # is found again even when its name is longer than that
        key = cut_u8(nm, NAME_MAX)
        was = self._dup.get((sysm.type, ascii_fold(key)))
        if was is not None:
            if was.arc and not arc:     # the ROM itself beats the same game in an archive
                was.file = osfile
                was.size = size
                was.arc = ARC_NONE
                was.member = 0
                was.member_text = None
                was.by_file = True
                was.art_stem = None
                was.crc = None
                was.status = archives.OK
                was.cdir, was.dir = res.folders[di]
                _rom_status(was)
            return
        cdir, osdir = res.folders[di]
        g = Game(sysm.type, key, cdir, osdir, osfile, arc, member, size)
        if member_info is not None:
            g.member_text = member_info.text
            g.crc = member_info.crc
            g.size = member_info.size
            g.status = member_info.status
        elif arc:
            g.status = archives.OK
        _rom_status(g)
        res.games.append(g)
        self._dup[(sysm.type, ascii_fold(key))] = g
        res.total += 1

    def _folder(self, cpath, ident, sysm):
        """scan_rom_folder: the games of one system folder."""
        if ident in self._dirs:
            return
        res = self.res
        if len(self._dirs) == ROM_DIRS:
            res.skipped_folders += 1
            return
        osdir = self.fs.resolve(cpath)
        if osdir is None:
            return
        ents = self.fs.listdir(osdir)
        if ents is None:
            return
        di = len(self._dirs)
        self._dirs.append(ident)
        res.folders.append((cut_bytes(cpath, PATH_CAP - 1), osdir))
        if self.progress:
            self.progress("Reading %s" % cpath)
        exts = sysm.exts
        for e in ents:
            if self.cancel is not None and self.cancel.is_set():
                raise ScanCancelled()
            if e.is_dir or e.size == 0 or e.cname.startswith("."):
                continue
            el = ends_with_any(e.cname, exts)
            arc = ARC_NONE
            if not el:
                el = ends_with_any(e.cname, (".zip",))
                if el:
                    arc = ARC_ZIP
                else:
                    el = ends_with_any(e.cname, (".7z",))
                    if not el:
                        continue
                    arc = ARC_7Z
            nm = e.cname[:len(e.cname) - el]
            if arc:
                el2 = ends_with_any(nm, exts)
                if el2:
                    nm = nm[:len(nm) - el2]
            if not arc:
                self._add(sysm, di, e.path, nm, ARC_NONE, 0, e.size)
                continue
            rc, members = self._archive(e, arc, exts)
            if members is None:
                res.archive_errors += 1
                g_before = len(res.games)
                self._add(sysm, di, e.path, nm, arc, 0, e.size)
                if len(res.games) > g_before:
                    res.games[-1].status = rc
                    res.games[-1].size = 0
                continue
            if len(members) <= 1:
                g_before = len(res.games)
                self._add(sysm, di, e.path, nm, arc, 0, e.size, members[0] if members else None)
                if len(res.games) > g_before and not members:
                    res.games[-1].status = archives.ERR_NOROM
                    res.games[-1].size = 0
                continue
            for m, mem in enumerate(members):
                rn = cut_u8(mem.raw, NAME_MAX)          # as archives.idx keeps it
                st = max(rn.rfind("/"), rn.rfind("\\")) + 1
                rn = rn[st:]
                el2 = ends_with_any(rn, exts)
                if el2:
                    rn = rn[:len(rn) - el2]
                self._add(sysm, di, e.path, rn, arc, m, mem.size, mem)
                if res.games and res.games[-1].file == e.path and res.games[-1].member == m:
                    res.games[-1].by_file = False

    def _archive(self, e, arc, exts):
        try:
            mtime = os.stat(e.path).st_mtime
        except OSError:
            return archives.ERR_READ, None
        rc, members = self.cache.get(e.path, e.size, mtime, "zip" if arc == ARC_ZIP else "7z", exts)
        if members is not None and len(members) > ARCHIVE_ONE:
            members = members[:ARCHIVE_ONE]
        return rc, members

    def _root(self, croot):
        """scan_root: the system folders straight inside a folder ("" the card's root)."""
        osroot = self.fs.resolve(croot) if croot else self.fs.root
        if osroot is None:
            return
        for e in self.fs.listdir(osroot) or ():
            if not e.is_dir:
                continue
            sysm = system_for_folder(e.cname)
            if sysm is None:
                continue
            self._folder(_join(croot, e.cname), _ident(e.path), sysm)

    def _user_folders(self):
        """/gammaos/folders.txt: one "<system> <path>" a line."""
        res = self.res
        ent = self.fs.find_file("/gammaos", "folders.txt")
        if ent is None:
            return
        res.folders_txt = True
        try:
            with open(ent.path, "rb") as f:
                data = f.read(FOLDERS_MAX)
        except OSError:
            return
        text = from_bytes(data.replace(b"\x00", b"\n"))      # a stray NUL ends a line
        for line in text.split("\n"):
            line = line.rstrip("\r \t").lstrip(" \t")
            if not line or line[0] == "#":
                continue
            cut = len(line)
            for sep in (" ", "\t"):
                k = line.find(sep)
                if 0 <= k < cut:
                    cut = k
            if cut == len(line):
                continue
            sysname, path = line[:cut], line[cut + 1:].lstrip(" \t")
            sysm = system_for_folder(sysname)
            if sysm is None or not path.startswith("/"):
                continue
            while len(path) > 1 and path.endswith("/"):
                path = path[:-1]
            osdir = self.fs.resolve(path)
            if osdir is None:
                continue
            res.user_folders.append((sysm.key, path))
            self._folder(path, _ident(osdir), sysm)

    def run(self):
        """The listing, as a ScanResult (games sorted)."""
        res = self.res
        self._root("/roms")
        self._root("")
        for e in self.fs.listdir(self.fs.root) or ():
            if not e.is_dir or _not_a_rom_root(e.cname) or ascii_fold(e.cname) == "roms":
                continue
            if system_for_folder(e.cname) is not None:
                continue
            top = _join("", e.cname)
            self._root(top)
            self._root(_join(top, "roms"))
        self._user_folders()
        res.games.sort(key=lambda g: sort_key(g.name))
        for i, g in enumerate(res.games):
            g.index = i
            g.title = clean_title(g.name)
        if res.total <= len(res.games):
            res.legacy = LegacyForms(g.name for g in res.games)
        return res


class ScanCancelled(Exception):
    pass


def _ident(path):
    try:
        st = os.stat(path)
        if st.st_ino:
            return (st.st_dev, st.st_ino)
    except OSError:
        pass
    return os.path.normcase(os.path.realpath(path))


def _not_a_rom_root(nm):
    low = ascii_fold(nm)
    return nm.startswith(".") or low in NOT_ROM_ROOTS or low in ("system volume information", "lost.dir")


def _rom_status(g):
    """Why the console would not start a game, from what is known without unpacking it."""
    if g.status != archives.OK:
        return
    if g.size == 0 and not g.arc:
        g.status = archives.ERR_DAMAGED
    elif g.size > BY_TYPE[g.type].rom_cap:
        g.status = archives.ERR_TOOBIG


def scan_card(root, archive_cache=None, progress=None, cancel=None, max_games=GAMES_MAX):
    return Scanner(root, archive_cache, progress, cancel, max_games).run()


def write_folders_template(root):
    """gammaos/folders.txt with its instructions, as the console makes it when it is missing."""
    d = os.path.join(root, "gammaos")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "folders.txt")
    if not os.path.exists(p):
        with open(p, "w", encoding="ascii", newline="\n") as f:
            f.write(FOLDERS_TEMPLATE)
    return p


def art_names(g, legacy=None):
    """The .ART names the console tries for a game, in order (gba_sd_read_named): its own, the
    older one cut at the last dot, then its underscore form when no other game has it."""
    return named_forms(g.name, "ART", legacy)
