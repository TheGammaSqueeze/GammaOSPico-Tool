"""The operations the window and the command line share, kept free of any user interface."""

import os
import threading

from . import config
from .card import scanner
from .card.layout import has_pico


class CardSession(object):
    """A card being looked at: its listing, its box art index, the caches."""

    def __init__(self, root, settings=None):
        self.root = os.path.abspath(root)
        self.settings = settings
        self.archive_store = config.JsonCache("archives")
        self.crc_cache = config.JsonCache("crcs")
        self.result = None
        self.index = None
        self.lock = threading.Lock()

    @property
    def has_pico(self):
        return has_pico(self.root)

    def scan(self, progress=None, cancel=None):
        from .art.store import ArtIndex
        res = scanner.scan_card(self.root, scanner.ArchiveCache(self.archive_store), progress, cancel)
        self.archive_store.save()
        with self.lock:
            self.result = res
            self.index = ArtIndex(self.root)
        return res

    def save_caches(self):
        self.archive_store.save()
        self.crc_cache.save()


def find_games(result, query, system=None):
    """Games whose name or title is `query` (any case), else those containing it."""
    from .card.names import fold
    from .card.systems import BY_KEY, system_for_folder
    sysm = None
    if system:
        sysm = BY_KEY.get(system.lower()) or system_for_folder(system)
        if sysm is None:
            raise ValueError("Unknown system %r (gb gbc gba snes genesis sms gg sg)." % system)
    pool = [g for g in result.games if sysm is None or g.type == sysm.type]
    q = fold(query)
    exact = [g for g in pool if fold(g.name) == q or fold(g.title) == q or fold(g.display_name) == q]
    if exact:
        return exact
    return [g for g in pool if q in fold(g.name) or q in fold(g.display_name)]
