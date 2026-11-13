"""CRC32 of a game's ROM, for the scrapers: from the archive's tables when it is in one, else read
from the card (once; kept in the cache by path, size and time)."""

import os
import zlib

HASH_MAX = 64 << 20


def file_crc(path, cache=None, cancel=None):
    """CRC32 of a file (None past HASH_MAX or when it cannot be read)."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    if st.st_size > HASH_MAX:
        return None
    key = "crc|%s|%d|%d" % (path, st.st_size, int(st.st_mtime))
    if cache is not None:
        hit = cache.get(key)
        if hit is not None:
            return hit
    crc = 0
    try:
        with open(path, "rb") as f:
            while True:
                if cancel is not None and cancel.is_set():
                    return None
                b = f.read(1 << 20)
                if not b:
                    break
                crc = zlib.crc32(b, crc)
    except OSError:
        return None
    crc &= 0xFFFFFFFF
    if cache is not None:
        cache[key] = crc
    return crc


def game_crc(game, cache=None, cancel=None):
    """The game's ROM CRC32 (also kept on the game), None when it cannot be told."""
    if game.crc is not None:
        return game.crc
    if game.arc:
        return None
    game.crc = file_crc(game.file, cache, cancel)
    return game.crc
