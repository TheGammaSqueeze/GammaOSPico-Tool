"""Putting a GammaOS Pico card package on a card: a plan first, then the files.

The plan lists every file of the package and what happens to it on this card:

  new        not on the card yet: written
  replace    on the card and different: written over
  same       already the same: left alone
  keep       the user's own (settings, folders.txt, play time, saves, states, games, BIOS, box art,
             boot.cfg...): never replaced, whatever the package holds

Nothing on the card is ever deleted. Each file is written whole to "<name>.tmp", flushed to the card,
then renamed over the old one, so a card pulled out halfway holds either the old file or the new.
The card is checked to take writes before anything is written, every file written is read back
from the card and compared with the package, and at the end the whole package is checked on the
card again (see apply). A
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
                           "Use gammaos-pico-kpa-<version>-sdcard.zip (Konkr Pocket Advance) or "
                           "gammaos-pico-block-<version>-sdcard.zip (Konkr Pocket Block)." % os.path.basename(zip_path))
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
        with _uncached_reader(path) as f:          # (what the card holds, not this computer's copy)
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


def _uncached_reader(path):
    """The file at path opened for reading what is on the card, not what this computer kept of it:
    the page cache dropped for it first (Linux), or reads past the cache (macOS). Elsewhere (Windows)
    the writes were flushed to the card before and the file is read as it is."""
    f = open(path, "rb")
    fd = f.fileno()
    try:
        if hasattr(os, "posix_fadvise"):
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        elif hasattr(os, "uname") and os.uname().sysname == "Darwin":
            import fcntl
            fcntl.fcntl(fd, 48, 1)              # F_NOCACHE
    except (OSError, AttributeError, ValueError):
        pass
    return f


def _on_card(path, info):
    """Does the card hold exactly the package's file here (its size and its CRC-32, as the zip records
    them)? Read back from the card itself as far as the computer allows."""
    import zlib
    try:
        if os.path.getsize(path) != info.file_size:
            return False
        crc = 0
        with _uncached_reader(path) as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                crc = zlib.crc32(b, crc)
        return (crc & 0xFFFFFFFF) == info.CRC
    except OSError:
        return False


def _check_writable(root):
    """A small file written, flushed, read back and removed: a card locked by its switch, mounted
    read-only or failing says so here, before any file of the package is touched."""
    probe = os.path.join(root, ".gammaos-pico-write-check.tmp")
    data = b"GammaOS Pico write check\n"
    try:
        with open(probe, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        with _uncached_reader(probe) as f:
            back = f.read()
        os.remove(probe)
    except OSError as e:
        try:
            os.remove(probe)
        except OSError:
            pass
        raise InstallError("The card cannot be written (%s). Check its lock switch, and that it is not "
                           "mounted read-only." % (e.strerror or e))
    if back != data:
        raise InstallError("The card did not keep what was written to it: it may be failing, or a fake "
                           "card smaller than it says. Try another card.")


def _write_one(z, it, progress, state):
    """One file: written whole to <name>.tmp, flushed to the card, renamed over the old one."""
    d = os.path.dirname(it.dest)
    os.makedirs(d, exist_ok=True)
    tmp = it.dest + ".tmp"
    with z.open(it.info) as src, open(tmp, "wb") as out:
        while True:
            b = src.read(1 << 20)
            if not b:
                break
            out.write(b)
            state["done"] += len(b)
            if progress:
                progress(min(state["done"], state["total"]), state["total"], it.rel)
        out.flush()
        os.fsync(out.fileno())
    os.replace(tmp, it.dest)


def apply(p, progress=None, cancel=None):
    """Carry out a plan. progress(done_bytes, total_bytes, rel). Returns the list of paths written.

    Every file written is read back from the card and compared with the package (size and CRC-32);
    one that does not match is written again, once. At the end every file of the package the card
    should now hold (new, replaced and unchanged ones) is checked the same way, so a write the card
    lost or a file changed meanwhile is caught: it is written once more, and if it still does not
    match the update stops with the files named instead of saying it is done."""
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
    _check_writable(p.root)
    written = []
    state = {"done": 0, "total": total}
    z = zipfile.ZipFile(p.zip_path)
    try:
        for it in todo:
            if cancel is not None and cancel.is_set():
                raise InstallError("Stopped. The files written so far are complete; run it again to finish.")
            _write_one(z, it, progress, state)
            if not _on_card(it.dest, it.info):
                state["total"] += it.size          # (the second write shows in the bar)
                _write_one(z, it, progress, state)
            written.append(it.dest)
        make_rom_dirs(p.root)
        _sync_dirs(set(os.path.dirname(w) for w in written) | {p.root})
        # the whole package, as the card holds it now
        bad = []
        for it in p.items:
            if it.action not in (NEW, REPLACE, SAME):
                continue
            if cancel is not None and cancel.is_set():
                raise InstallError("Stopped. The files written so far are complete; run it again to finish.")
            if _on_card(it.dest, it.info):
                continue
            _write_one(z, it, progress, state)
            if not _on_card(it.dest, it.info):
                bad.append(it.rel)
            elif it.dest not in written:
                written.append(it.dest)
        if bad:
            raise InstallError("%d file%s did not end up on the card as the package has %s: %s%s. The card "
                               "may be failing or full; check it, then run the update again."
                               % (len(bad), "" if len(bad) == 1 else "s", "it" if len(bad) == 1 else "them",
                                  ", ".join(bad[:10]), "..." if len(bad) > 10 else ""))
        _sync_dirs(set(os.path.dirname(w) for w in written) | {p.root})
    finally:
        z.close()
    p.verified = sum(1 for i in p.items if i.action in (NEW, REPLACE, SAME))
    if progress:
        progress(state["total"], state["total"], "")
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
