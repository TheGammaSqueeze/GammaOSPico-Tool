"""The card's own files: what GammaOS Pico puts on it, and what belongs to the user.

The package (gammaos-pico-kpa-<version>-sdcard.zip, or gammaos-pico-block-<version>-sdcard.zip with
the same files) brings gammaos/ (the menu, its themes and languages, boot.cfg), cores/,
bios/README.txt, roms/<system>/ with free demo games and their box art, licences/ and README.txt
(whose first line names the release). The console writes the rest: settings, play time, the
recently played list, the archive index, saves and states.
"""

import hashlib
import os
import re

from ..art.store import atomic_write
from .systems import SYSTEMS

# never replaced or removed by an update (kept when they are there, made when they are not)
USER_FILES = ("gammaos/settings.bin", "gammaos/folders.txt", "gammaos/playtime.txt", "gammaos/recent.txt",
              "gammaos/archives.idx", "gammaos/cart_header.bin", "gammaos/blackbox.bin", "gammaos/boot.cfg",
              "gammaos/snescore.cfg")
USER_DIRS = ("saves", "states", "roms", "bios")
# the console's own data under gammaos/ (1.1): saved Wi-Fi networks, kept box art lookups, the
# RetroAchievements account, its unlocks kept offline and its caches; each core's options; the
# player's button maps for each system and game (gammaos/controls/); never replaced or removed
USER_PREFIXES = ("gammaos/net/", "gammaos/ra/", "gammaos/coreopt-", "gammaos/controls/")
# the cheats (1.1.1): the release's database is gammaos/cheats/<system>.gcd, replaced by every
# update; the folder for each system under it holds the user's own .cht files and what the console
# keeps for each game (the cheats on, the list picked), never replaced or removed
CHEATS_DIR = "gammaos/cheats/"
# inside bios/ but the release's own, replaced by every update: the Wi-Fi and Bluetooth chip's
# firmware and HTTPS's trusted roots (1.1)
PACKAGE_PREFIXES = ("bios/connsys/", "bios/certs/")

BIOS_SIZE = 16384
BIOS_MD5 = "a860e8c0b6d573d191e4ec7db1b1e4f6"

_VERSION_RE = re.compile(r"release\s+([0-9A-Za-z][0-9A-Za-z._-]*)")


def find_ci(root, rel):
    """The path of `rel` under root, matching each part without case (a FAT card on a case-sensitive
    system shows names as they were written), or None."""
    cur = root
    for part in rel.replace("\\", "/").split("/"):
        if not part:
            continue
        direct = os.path.join(cur, part)
        if os.path.exists(direct):
            cur = direct
            continue
        low = part.lower()
        try:
            names = os.listdir(cur)
        except OSError:
            return None
        for n in names:
            if n.lower() == low:
                cur = os.path.join(cur, n)
                break
        else:
            return None
    return cur


def has_pico(root):
    p = find_ci(root, "gammaos/menu.pack")
    return bool(p and os.path.isfile(p))


def card_version(root):
    """The release the card's files came from (README.txt's first line), None when unknown."""
    p = find_ci(root, "README.txt")
    if not p:
        return None
    try:
        with open(p, "rb") as f:
            head = f.read(512).decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    if head:
        m = _VERSION_RE.search(head[0])
        if m:
            return m.group(1)
    return None


def is_user_path(rel):
    """Does this card path belong to the user (an update never replaces it)?"""
    rel = rel.replace("\\", "/").strip("/")
    low = rel.lower()
    if low in USER_FILES:
        return True
    if low.startswith(PACKAGE_PREFIXES):
        return False
    if low.startswith(USER_PREFIXES):
        return True
    if low.startswith(CHEATS_DIR) and "/" in low[len(CHEATS_DIR):]:
        return True
    top = low.split("/", 1)[0]
    if top in USER_DIRS:
        return True
    if low.endswith(".art") or "/boxart/" in "/" + low + "/":
        return True
    return False


def make_rom_dirs(root):
    """roms/<system>/ for every system, as the console makes them."""
    made = []
    roms = find_ci(root, "roms") or os.path.join(root, "roms")
    for s in SYSTEMS:
        if find_ci(roms, s.key) is None:
            p = os.path.join(roms, s.key)
            os.makedirs(p, exist_ok=True)
            made.append(p)
    return made


class BiosCheck(object):
    def __init__(self, path, size, md5, ok, known, message):
        self.path, self.size, self.md5, self.ok, self.known, self.message = path, size, md5, ok, known, message


def check_bios(path):
    """Is this file a Game Boy Advance BIOS the console takes? (size must be 16384; the md5 of the
    usual dump is known, another one is allowed with a warning)."""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return BiosCheck(path, 0, None, False, False, "The file could not be read (%s)." % e.strerror)
    if size != BIOS_SIZE:
        return BiosCheck(path, size, None, False, False,
                         "This is not a Game Boy Advance BIOS: it is %d bytes, a BIOS is exactly 16384." % size)
    with open(path, "rb") as f:
        md5 = hashlib.md5(f.read()).hexdigest()
    if md5 == BIOS_MD5:
        return BiosCheck(path, size, md5, True, True, "This is the Game Boy Advance BIOS the console expects.")
    return BiosCheck(path, size, md5, True, False,
                     "The size is right but this is not the usual Game Boy Advance BIOS (md5 %s). It may be "
                     "a different revision or a replacement BIOS; games may not run the same way." % md5)


def installed_bios(root):
    """bios/gba_bios.bin (or gba_bios.bin at the root, where older cards have it), or None."""
    for rel in ("bios/gba_bios.bin", "gba_bios.bin"):
        p = find_ci(root, rel)
        if p and os.path.isfile(p):
            return p
    return None


def install_bios(root, src):
    """Copy a checked BIOS to bios/gba_bios.bin. Returns the path written."""
    chk = check_bios(src)
    if not chk.ok:
        raise ValueError(chk.message)
    with open(src, "rb") as f:
        data = f.read()
    d = find_ci(root, "bios") or os.path.join(root, "bios")
    os.makedirs(d, exist_ok=True)
    dest = find_ci(d, "gba_bios.bin") or os.path.join(d, "gba_bios.bin")
    atomic_write(dest, data)
    return dest
