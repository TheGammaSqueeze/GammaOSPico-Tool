"""Putting a GammaOS Pico card package on a card: a plan first, then the files.

The plan lists every file of the package and what happens to it on this card:

  new        not on the card yet: written
  replace    on the card and different: written over
  same       already the same: left alone
  keep       the user's own (settings, folders.txt, play time, saves, states, games, BIOS, box art,
             boot.cfg...): never replaced, whatever the package holds

Nothing on the card is ever deleted. Each file is written whole to "<name>.tmp", flushed to the card,
then renamed over the old one, so a card pulled out halfway holds either the old file or the new. A
name in the zip that would land outside the card (an absolute path, "..", a drive) stops everything
before anything is written.
"""

import hashlib
import os
import stat
import zipfile

from ..card.layout import find_ci, is_user_path, make_rom_dirs

NEW, REPLACE, SAME, KEEP, SKIP = "new", "replace", "same", "keep", "skip"


class InstallError(Exception):
    pass


class Item(object):
    __slots__ = ("rel", "action", "size", "info", "dest", "why")

    def __init__(self, rel, action, size, info, dest, why=""):
        self.rel, self.action, self.size, self.info, self.dest, self.why = rel, action, size, info, dest, why

    def __repr__(self):
        return "<%s %s>" % (self.action, self.rel)


class Plan(object):
    def __init__(self, zip_path, root, items, version, top):
        self.zip_path, self.root, self.items, self.version, self.top = zip_path, root, items, version, top

    def of(self, action):
        return [i for i in self.items if i.action == action]

    @property
    def write_bytes(self):
        return sum(i.size for i in self.items if i.action in (NEW, REPLACE))

    def summary(self):
        n = {a: len(self.of(a)) for a in (NEW, REPLACE, SAME, KEEP, SKIP)}
        return ("%d new, %d replaced, %d unchanged, %d of yours kept, %d demo files left out"
                % (n[NEW], n[REPLACE], n[SAME], n[KEEP], n[SKIP]))


def safe_rel(name):
    """A zip member name as a safe relative path ("a/b"), or raises InstallError."""
    n = name.replace("\\", "/")
    if n.startswith("/") or (len(n) > 1 and n[1] == ":") or n.startswith("//"):
        raise InstallError("The package holds a file with an absolute path (%s): it was not used." % name)
    parts = [p for p in n.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise InstallError("The package holds a file that would land outside the card (%s): it was not used." % name)
    for p in parts:
        if any(ord(c) < 32 for c in p) or ":" in p:
            raise InstallError("The package holds a file name the card cannot take (%s)." % name)
    return "/".join(parts)


def _package_top(names):
    """The folder every entry is in ("gammaos-pico-kpa-<v>-sdcard/"), or "" when there is none."""
    tops = set()
    for n in names:
        n = n.replace("\\", "/").lstrip("/")
        tops.add(n.split("/", 1)[0] if "/" in n else None)
    if len(tops) == 1:
        t = tops.pop()
        if t:
            return t + "/"
    return ""


def open_package(zip_path):
    try:
        z = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as e:
        raise InstallError("%s is not a readable zip file (%s)." % (os.path.basename(zip_path), e))
    names = z.namelist()
    top = _package_top(names)
    if not any(n.replace("\\", "/") == top + "gammaos/menu.pack" for n in names):
        z.close()
        raise InstallError("%s is not a GammaOS Pico microSD card package (it has no gammaos/menu.pack). "
                           "Use gammaos-pico-kpa-<version>-sdcard.zip." % os.path.basename(zip_path))
    return z, top


def _version_of(z, top):
    from ..card.layout import _VERSION_RE
    try:
        head = z.read(top + "README.txt")[:512].decode("utf-8", "replace").splitlines()
        m = _VERSION_RE.search(head[0]) if head else None
        return m.group(1) if m else None
    except KeyError:
        return None


def _same(path, info, z):
    try:
        if os.path.getsize(path) != info.file_size:
            return False
        h1 = hashlib.sha256()
        with open(path, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h1.update(b)
        h2 = hashlib.sha256()
        with z.open(info) as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h2.update(b)
        return h1.digest() == h2.digest()
    except OSError:
        return False


def plan(zip_path, root, demo_games=True):
    """What putting this package on the card at `root` would do."""
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise InstallError("The card folder %s is not there." % root)
    z, top = open_package(zip_path)
    items = []
    try:
        version = _version_of(z, top)
        for info in z.infolist():
            name = info.filename
            if not name.startswith(top):
                continue
            rel = safe_rel(name[len(top):])
            if not rel:
                continue
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise InstallError("The package holds a link (%s): it was not used." % name)
            if info.is_dir():
                continue
            existing = find_ci(root, rel)
            dest = existing or os.path.join(root, *rel.split("/"))
            if not os.path.normcase(os.path.abspath(dest)).startswith(os.path.normcase(root)):
                raise InstallError("The package holds a file that would land outside the card (%s)." % name)
            user = is_user_path(rel)
            if existing and os.path.isdir(existing):
                raise InstallError("The card has a folder where the package has the file %s." % rel)
            if user and existing:
                act, why = KEEP, "yours"
            elif user and rel.lower().startswith("roms/") and not demo_games:
                act, why = SKIP, "demo game"
            elif not existing:
                act, why = NEW, ""
            elif _same(existing, info, z):
                act, why = SAME, ""
            else:
                act, why = REPLACE, ""
            items.append(Item(rel, act, info.file_size, info, dest, why))
    finally:
        z.close()
    return Plan(zip_path, root, items, version, top)


def apply(p, progress=None, cancel=None):
    """Carry out a plan. progress(done_bytes, total_bytes, rel). Returns the list of paths written."""
    import shutil
    todo = [i for i in p.items if i.action in (NEW, REPLACE)]
    total = sum(i.size for i in todo)
    try:
        free = shutil.disk_usage(p.root).free
    except OSError:
        free = None
    replaced = sum(os.path.getsize(i.dest) for i in todo if i.action == REPLACE and os.path.exists(i.dest))
    if free is not None and total - replaced > free:
        raise InstallError("The card needs %s more free space for this package." % _mb(total - replaced - free))
    written = []
    done = 0
    z = zipfile.ZipFile(p.zip_path)
    try:
        for it in todo:
            if cancel is not None and cancel.is_set():
                raise InstallError("Stopped. The files written so far are complete; run it again to finish.")
            d = os.path.dirname(it.dest)
            os.makedirs(d, exist_ok=True)
            tmp = it.dest + ".tmp"
            with z.open(it.info) as src, open(tmp, "wb") as out:
                while True:
                    b = src.read(1 << 20)
                    if not b:
                        break
                    out.write(b)
                    done += len(b)
                    if progress:
                        progress(done, total, it.rel)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, it.dest)
            written.append(it.dest)
        make_rom_dirs(p.root)
        _sync_dirs(set(os.path.dirname(w) for w in written) | {p.root})
    finally:
        z.close()
    if progress:
        progress(total, total, "")
    return written


def _sync_dirs(dirs):
    if os.name != "posix":
        return
    for d in dirs:
        try:
            fd = os.open(d, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass


def _mb(n):
    return "%.1f MB" % (n / 1048576.0)
