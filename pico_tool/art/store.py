"""Where the console finds each game's box art, and writing it there.

For a game the console reads <its folder>/boxart/<name>.ART, then GammaOS Pico's own
/roms/<system>/boxart/<name>.ART when the game is kept elsewhere. <name> is the game's name (UTF-8,
as the card has it) with a ROM extension left on it dropped. In each folder it also reads the files
older versions wrote: the name cut at its last dot, and the underscore form versions before 1.1
used for names outside ASCII ("_" for each such character), the latter only when no other game of
the library has the same form. The first of those names that holds anything decides for the
folder. At power on a <name>.png (or <name>.<rom extension>.png) in those folders is turned into
<name>.ART and deleted, so a PNG waiting there counts as a cover to come.

Covers are always written beside the game under the game's own name.
"""

import os

from ..card.fsview import CardFS
from ..card.names import ascii_fold, base_ext, fat_safe
from ..card.scanner import art_names
from ..card.systems import BY_TYPE, home_art_dir, home_dir
from . import artfile

ART_DIR = "boxart"
PNG_ROM_EXTS = tuple("." + e for e in ("gba", "gb", "gbc", "sfc", "smc", "md", "gen", "bin", "smd", "sms", "gg", "sg"))

HAVE_ART = "art"          # an .ART the console will show
HAVE_PNG = "png"          # a PNG it converts at the next power on
NO_ART = "none"
CANNOT = "cannot"         # the name cannot match any file on a FAT or exFAT card


class ArtIndex(object):
    """The box art folders of a card, listed once, to answer for thousands of games quickly.

    `legacy` is the listing's ScanResult.legacy: which underscore forms are a game's own. Without
    it no file under an underscore form is read, as on a console that has no full listing."""

    def __init__(self, root, fs=None, legacy=None):
        self.root = os.path.abspath(root)
        self.fs = fs or CardFS(self.root)
        self.legacy = legacy
        self._dirs = {}

    def invalidate(self, osdir=None):
        if osdir is None:
            self._dirs.clear()
            self.fs = CardFS(self.root)
        else:
            self._dirs.pop(osdir, None)
            pre = osdir.rstrip(os.sep) + os.sep
            for k in list(self.fs._cache):
                if k == osdir or k.startswith(pre):
                    self.fs._cache.pop(k, None)

    def _art_folder(self, osdir):
        """{console name with A-Z small: (file name, size)} of <osdir>/boxart, the first file of
        each name in the folder's order, and its path (None when there is no such folder)."""
        hit = self._dirs.get(osdir)
        if hit is not None:
            return hit
        folder = None
        files = {}
        for e in self.fs.listdir(osdir) or ():
            if e.is_dir and ascii_fold(e.cname) == ART_DIR:
                folder = e.path
                break
        if folder:
            for e in self.fs.listdir(folder) or ():
                if not e.is_dir:
                    files.setdefault(ascii_fold(e.cname), (e.name, e.size))
        hit = (folder, files)
        self._dirs[osdir] = hit
        return hit

    def _home(self, game):
        """The folder on the computer of /roms/<system> for the game, None when not on the card."""
        return self.fs.resolve(home_dir(BY_TYPE[game.type]))

    def candidates(self, game):
        """[(folder of the game's dir, is_home)] the console reads, in order."""
        out = [(game.dir, False)]
        if ascii_fold(game.cdir + "/boxart") != ascii_fold(home_art_dir(BY_TYPE[game.type])):
            h = self._home(game)
            if h:
                out.append((h, True))
        return out

    def art_names(self, game):
        """The .ART names the console tries for the game in each folder, in order."""
        return art_names(game, self.legacy)

    def find(self, game):
        """(state, path): the cover the console would show, a PNG waiting, or none."""
        if not art_possible(game):
            return CANNOT, None
        names = [ascii_fold(n) for n in self.art_names(game)]
        pngs = _png_names(names)
        waiting = None
        for osdir, _home in self.candidates(game):
            folder, files = self._art_folder(osdir)
            if not folder:
                continue
            for n in names:
                hit = files.get(n)
                if hit and hit[1]:
                    # the first name holding anything decides for this folder (gba_sd_read_named)
                    if hit[1] >= artfile.HEADER:
                        return HAVE_ART, os.path.join(folder, hit[0])
                    break
            if waiting is None:
                for n in pngs:
                    hit = files.get(n)
                    if hit and hit[1]:
                        waiting = os.path.join(folder, hit[0])
                        break
        if waiting:
            return HAVE_PNG, waiting
        return NO_ART, None

    def matches(self, game):
        """Every file in the folders the console reads that counts as this game's cover
        (.ART under any of its names, waiting PNGs)."""
        out = []
        names = [ascii_fold(n) for n in self.art_names(game)]
        names += _png_names(names)
        for osdir, _home in self.candidates(game):
            folder, files = self._art_folder(osdir)
            if not folder:
                continue
            for n in names:
                hit = files.get(n)
                if hit:
                    out.append(os.path.join(folder, hit[0]))
        return out


def art_stem(game):
    """The name (without .ART) a cover for this game is written under, None when no file on a FAT or
    exFAT card can match the game's name."""
    if game.art_stem is not None:
        return game.art_stem or None
    stem = base_ext(game.name, "ART")[:-4]
    if not fat_safe(stem + ".ART"):
        stem = None
    game.art_stem = stem or ""
    return stem


def art_possible(game):
    return art_stem(game) is not None


def _png_names(art_names):
    """The PNG names (A-Z small) the console's import turns into one of these .ART names: the
    PNG's name without ".png", a ROM extension left on it dropped, becomes the .ART's."""
    out = []
    for a in art_names:
        stem = a[:-4]
        out.append(stem + ".png")
        for e in PNG_ROM_EXTS:
            out.append(stem + e + ".png")
    return out


def write_art(index, game, art_bytes):
    """Write a cover beside the game (atomically), replacing what the console would have shown from
    that folder. Returns the path written."""
    stem = art_stem(game)
    if stem is None:
        raise artfile.ArtError("The console cannot match a cover to this game's name")
    artfile.parse_header(art_bytes)
    index.invalidate(game.dir)          # what is on the card now, not what was listed before
    folder, _files = index._art_folder(game.dir)
    if not folder:
        folder = os.path.join(game.dir, ART_DIR)
        os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, stem + ".ART")
    # the old cover and waiting PNGs in this folder go (a PNG would replace the new cover at the
    # next power on); another case of the same name is one file on the card
    names = [ascii_fold(n) for n in index.art_names(game)]
    names = set(names) | set(_png_names(names))
    for e in index.fs.listdir(folder) or ():
        if not e.is_dir and ascii_fold(e.cname) in names and os.path.normcase(e.path) != os.path.normcase(dest):
            _remove(e.path)
    atomic_write(dest, art_bytes)
    index.invalidate(game.dir)
    # the console finds it only under the very characters of the game's name: a computer that
    # stores names in another Unicode form would leave a cover it never reads
    want = ascii_fold(stem + ".ART")
    if not any(not e.is_dir and ascii_fold(e.cname) == want for e in index.fs.listdir(folder) or ()):
        _remove(dest)
        index.invalidate(game.dir)
        raise artfile.ArtError("This computer changed the cover's file name, so the console would not "
                               "find it")
    return dest


def remove_art(index, game):
    """Remove every cover the console could show for the game (beside it and in /roms/<system>)."""
    gone = []
    for osdir, _home in index.candidates(game):
        index.invalidate(osdir)
    for p in index.matches(game):
        if _remove(p):
            gone.append(p)
    for osdir, _home in index.candidates(game):
        index.invalidate(osdir)
    return gone


def read_art(path):
    with open(path, "rb") as f:
        return f.read()


def _remove(path):
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False


def atomic_write(dest, data):
    """Write a file whole or not at all: a .tmp beside it, flushed to the card, then renamed."""
    tmp = dest + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, dest)
    _fsync_dir(os.path.dirname(dest))


def _fsync_dir(path):
    if os.name != "posix":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
