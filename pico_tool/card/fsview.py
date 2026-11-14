"""The card's folders as the console walks them.

The console opens folders by path, comparing each part with the names it reads (UTF-8, the
letters A-Z without case, every other character as it is) and taking the first folder that
matches, in the folder's own order. These helpers do the same on a mounted card (or any folder), so
the listing follows the order the console sees: the directory order a FAT or exFAT driver returns.
"""

import os

from .names import FAT_NAME_MAX, ascii_fold, console_name, from_bytes, to_bytes


class Entry(object):
    __slots__ = ("name", "cname", "is_dir", "size", "path")

    def __init__(self, name, cname, is_dir, size, path):
        self.name = name        # the name on the computer
        self.cname = cname      # the name as the console reads it
        self.is_dir = is_dir
        self.size = size        # 32 bits, as a directory entry holds it
        self.path = path        # full path on the computer

    def __repr__(self):
        return "<Entry %r%s>" % (self.name, "/" if self.is_dir else "")


class CardFS(object):
    """Directory listings of a card root, cached for one scan."""

    def __init__(self, root):
        self.root = os.path.abspath(root)
        self._cache = {}

    def listdir(self, path):
        """Entries of a folder on the computer, in the order the system gives them."""
        got = self._cache.get(path)
        if got is not None:
            return got
        out = []
        try:
            with os.scandir(path) as it:
                for de in it:
                    try:
                        is_dir = de.is_dir()
                        size = 0 if is_dir else de.stat().st_size & 0xFFFFFFFF
                    except OSError:
                        continue
                    out.append(Entry(de.name, console_name(de.name), is_dir, size,
                                     os.path.join(path, de.name)))
        except OSError:
            out = None
        self._cache[path] = out
        return out

    def resolve(self, cpath):
        """The folder on the computer for a console path ("/ROMs/gba"), or None (fat_opendir)."""
        cur = self.root
        for part in _parts(cpath):
            want = ascii_fold(part)
            ents = self.listdir(cur)
            if ents is None:
                return None
            for e in ents:
                if e.is_dir and ascii_fold(e.cname) == want:
                    cur = e.path
                    break
            else:
                return None
        return cur

    def find_file(self, cdir, name):
        """A file in a console folder by its console name (fat_open), as an Entry, or None."""
        d = self.resolve(cdir)
        if d is None:
            return None
        want = ascii_fold(name)
        for e in self.listdir(d) or ():
            if not e.is_dir and ascii_fold(e.cname) == want:
                return e
        return None


def _parts(cpath):
    """The parts of a console path the way fat_opendir takes them: a part of more than 255 bytes
    goes on as the next part."""
    out = []
    for part in cpath.split("/"):
        if not part:
            continue
        if part.isascii() and len(part) <= FAT_NAME_MAX:
            out.append(part)
            continue
        b = to_bytes(part)
        for i in range(0, len(b), FAT_NAME_MAX):
            out.append(from_bytes(b[i:i + FAT_NAME_MAX]))
    return out
