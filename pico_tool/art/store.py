"""Where the console finds each game's box art, and writing it there.

For a game the console reads <its folder>/boxart/<name>.ART, then GammaOS Pico's own
/roms/<system>/boxart/<name>.ART when the game is kept elsewhere. <name> is the game's name with a
ROM extension left on it dropped; files written by older versions under the name cut at its last
dot are read too. At power on a <name>.png (or <name>.<rom extension>.png) in those folders is
turned into the .ART and deleted, so a PNG waiting there counts as a cover to come.

Covers are written beside the game, under a name the console will match: the game's own file name
when it reads back the same, else the console's form of it ("_" for the letters it cannot show).
"""

import os

from ..card.fsview import CardFS
from ..card.names import NAME_MAX, base_ext, console_name, ends_with_any, fat_safe, fold
from ..card.scanner import ARC_ZIP, art_names
from ..card.systems import BY_TYPE, home_art_dir, home_dir
from . import artfile

ART_DIR = "boxart"
PNG_ROM_EXTS = tuple("." + e for e in ("gba", "gb", "gbc", "sfc", "smc", "md", "gen", "bin", "smd", "sms", "gg", "sg"))

HAVE_ART = "art"          # an .ART the console will show
HAVE_PNG = "png"          # a PNG it converts at the next power on
NO_ART = "none"
CANNOT = "cannot"         # the name cannot match any file on a FAT or exFAT card


class ArtIndex(object):
    """The box art folders of a card, listed once, to answer for thousands of games quickly."""

    def __init__(self, root, fs=None):
        self.root = os.path.abspath(root)
        self.fs = fs or CardFS(self.root)
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
        """{folded console name: (file name, size)} of <osdir>/boxart, and its path (None when
        there is no such folder)."""
        hit = self._dirs.get(osdir)
        if hit is not None:
            return hit
        folder = None
        files = {}
        for e in self.fs.listdir(osdir) or ():
            if e.is_dir and fold(e.cname) == ART_DIR:
                folder = e.path
                break
        if folder:
            for e in self.fs.listdir(folder) or ():
                if not e.is_dir:
                    files.setdefault(fold(e.cname), (e.name, e.size))
        hit = (folder, files)
        self._dirs[osdir] = hit
        return hit

    def _home(self, game):
        """The folder on the computer of /roms/<system> for the game, None when not on the card."""
        return self.fs.resolve(home_dir(BY_TYPE[game.type]))

    def candidates(self, game):
        """[(folder of the game's dir, is_home)] the console reads, in order."""
        out = [(game.dir, False)]
        if fold(game.cdir + "/boxart") != fold(home_art_dir(BY_TYPE[game.type])):
            h = self._home(game)
            if h:
                out.append((h, True))
        return out

    def find(self, game):
        """(state, path): the cover the console would show, a PNG waiting, or none."""
        if not art_possible(game):
            return CANNOT, None
        names = [fold(n) for n in art_names(game)]
        pngs = _png_names(game)
        waiting = None
        for osdir, _home in self.candidates(game):
            folder, files = self._art_folder(osdir)
            if not folder:
                continue
            for n in names:
                hit = files.get(n)
                if hit and hit[1] >= artfile.HEADER:
                    return HAVE_ART, os.path.join(folder, hit[0])
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
        (.ART under either name, waiting PNGs)."""
        out = []
        names = [fold(n) for n in art_names(game)] + _png_names(game)
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
    stem = _art_stem(game)
    game.art_stem = stem or ""
    return stem


def _art_stem(game):
    target = base_ext(game.name, "ART")[:-4]
    nice = _unicode_name(game)
    if nice is not None:
        nice = base_ext(nice, "ART")[:-4]
        if fold(console_name(nice)) == fold(target) and fat_safe(nice + ".ART"):
            return nice
    if not target.isascii() or not fat_safe(target + ".ART"):
        return None
    return target


def _unicode_name(game):
    """The game's name the way the computer shows it (the console's name keeps only ASCII), or
    None when it cannot be told."""
    exts = BY_TYPE[game.type].exts
    if game.by_file:
        base = os.path.basename(game.file)
        own = exts if not game.arc else ((".zip",) if game.arc == ARC_ZIP else (".7z",))
        el = ends_with_any(console_name(base), own)
        if not el:
            return None
        base = _cut_units(base, min(len(console_name(base)) - el, NAME_MAX))
        if game.arc:
            el = ends_with_any(console_name(base), exts)
            if el:
                base = _cut_units(base, len(console_name(base)) - el)
        return base
    if game.arc == ARC_ZIP or game.member_text is None:
        return None
    inner = _cut_units(game.member_text, NAME_MAX)
    inner = inner.replace("\\", "/").rsplit("/", 1)[-1]
    el = ends_with_any(console_name(inner), exts)
    return _cut_units(inner, len(console_name(inner)) - el) if el else inner


def _cut_units(s, n):
    """The first n UTF-16 units of s (as the console counts a name)."""
    raw = s.encode("utf-16-le", "surrogatepass")[:2 * max(0, n)]
    return raw.decode("utf-16-le", "ignore")


def art_possible(game):
    return art_stem(game) is not None


def _png_names(game):
    """The PNG names the console's import turns into this game's .ART (folded)."""
    a = base_ext(game.name, "ART")[:-4]
    out = [fold(a + ".png")]
    for e in PNG_ROM_EXTS:
        out.append(fold(a + e + ".png"))
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
    names = set(fold(n) for n in art_names(game)) | set(_png_names(game))
    for e in index.fs.listdir(folder) or ():
        if not e.is_dir and fold(e.cname) in names and os.path.normcase(e.path) != os.path.normcase(dest):
            _remove(e.path)
    atomic_write(dest, art_bytes)
    index.invalidate(game.dir)
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
