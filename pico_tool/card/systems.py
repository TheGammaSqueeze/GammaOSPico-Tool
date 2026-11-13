"""The systems GammaOS Pico plays, with the folder names and extensions the console knows.

The table follows the console's own (k_systems in the bootloader's card scanner): the order is the
console type number, the first folder name is GammaOS Pico's own layout (roms/<first name>).
"""

from collections import namedtuple

System = namedtuple("System", "type key label short dirs exts rom_cap ss_id tgdb_id")

SYSTEMS = (
    System(0, "gb", "Game Boy", "GB", ("gb", "gameboy"), (".gb",), 8 << 20, 9, 4),
    System(1, "gbc", "Game Boy Color", "GBC", ("gbc", "gameboycolor"), (".gbc",), 8 << 20, 10, 41),
    System(2, "gba", "Game Boy Advance", "GBA", ("gba", "gameboyadvance"), (".gba",), 32 << 20, 12, 5),
    System(3, "snes", "Super Nintendo", "SNES", ("snes", "sfc", "superfamicom", "snesna"),
           (".sfc", ".smc"), 8 << 20, 4, 6),
    System(4, "genesis", "Mega Drive / Genesis", "MD", ("genesis", "megadrive", "md", "megadrivejp"),
           (".md", ".gen", ".bin", ".smd"), 8 << 20, 1, 18),
    System(5, "sms", "Master System", "SMS", ("sms", "mastersystem"), (".sms",), 8 << 20, 2, 35),
    System(6, "gg", "Game Gear", "GG", ("gg", "gamegear"), (".gg",), 8 << 20, 21, 20),
    System(7, "sg", "SG-1000", "SG", ("sg", "sg1000", "sg-1000"), (".sg",), 8 << 20, 109, 4949),
)

BY_TYPE = {s.type: s for s in SYSTEMS}
BY_KEY = {s.key: s for s in SYSTEMS}


def system_for_folder(name):
    """The system whose folder names include `name` (any case), or None."""
    low = name.lower() if name.isascii() else None
    if low is None:
        return None
    for s in SYSTEMS:
        if low in s.dirs:
            return s
    return None


def home_dir(system):
    """GammaOS Pico's own folder of a system, as the console writes it ("/roms/gba")."""
    return "/roms/" + system.key


def home_art_dir(system):
    return home_dir(system) + "/boxart"
