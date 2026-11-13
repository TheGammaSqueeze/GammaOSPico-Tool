"""Scraping covers for many games at once, writing each where the console reads it."""

from ..art import artfile, store
from ..card.hashing import game_crc
from .scraper import StopScraping, ScrapeError, with_retry


class Outcome(object):
    def __init__(self):
        self.done = []          # (game, path written)
        self.missing = []       # (game, why): no match or no cover
        self.failed = []        # (game, why): an error
        self.skipped = []       # games that have a cover already, or a name no cover can match
        self.stopped = ""       # why the batch ended early ("" it ran to the end)
        self.cancelled = False

    def summary(self):
        s = "%d covers added, %d not found, %d failed" % (len(self.done), len(self.missing), len(self.failed))
        if self.skipped:
            s += ", %d skipped" % len(self.skipped)
        return s


def wanted(games, index, overwrite=False):
    """The games a scrape should do: those without a cover (all of them with overwrite), leaving out
    names no cover file can match."""
    out = []
    for g in games:
        state, _p = index.find(g)
        if state == store.CANNOT:
            continue
        if overwrite or state == store.NO_ART:
            out.append(g)
    return out


def scrape_one(scraper, index, game, crc_cache=None, cancel=None, note=None):
    """Find and write one game's cover. Returns (path or None, message)."""
    crc = game_crc(game, crc_cache, cancel) if scraper.uses_crc else None
    res = with_retry(lambda: scraper.match(game.title, game.rom_name, game.system, crc, game.size),
                     cancel=cancel, note=note)
    if not res.matched:
        return None, "no match"
    if not res.image:
        return None, res.error or "no cover"
    art = artfile.encode_bytes(res.image)
    return store.write_art(index, game, art), res.title


def run(games, scraper, index, overwrite=False, progress=None, cancel=None, crc_cache=None):
    """Scrape the covers of `games`. progress(i, n, game, message) after each game."""
    out = Outcome()
    todo = []
    for g in games:
        state, _p = index.find(g)
        if state == store.CANNOT or (state != store.NO_ART and not overwrite):
            out.skipped.append(g)
        else:
            todo.append(g)
    n = len(todo)
    for i, g in enumerate(todo):
        if cancel is not None and cancel.is_set():
            out.cancelled = True
            break
        try:
            path, msg = scrape_one(scraper, index, g, crc_cache, cancel,
                                   note=(lambda m, g=g, i=i: progress(i, n, g, m)) if progress else None)
            if path:
                out.done.append((g, path))
                msg = "cover added" + (" (%s)" % msg if msg else "")
            else:
                out.missing.append((g, msg))
        except StopScraping as e:
            out.stopped = str(e)
            out.failed.append((g, str(e)))
            if progress:
                progress(i + 1, n, g, str(e))
            break
        except (ScrapeError, artfile.ArtError, OSError) as e:
            msg = str(e)
            out.failed.append((g, msg))
        if progress:
            progress(i + 1, n, g, msg)
    return out
