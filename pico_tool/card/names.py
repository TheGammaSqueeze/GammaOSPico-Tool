"""Names the way the console sees them.

The console reads every file name as UTF-16 (FAT long names, exFAT names) and keeps each unit
below 0x80 as it is; any other unit becomes "_". It compares names without case for the letters A
to Z only. A game's name is its file name without the extension, at most 127 bytes; its menu title
drops the trailing region and dump tags. Saves, states and box art are keyed by that name.

Names here are Python strings whose characters stand for the console's bytes: ASCII for names read
from the card, and one character per byte (latin-1) for raw names taken from inside a ZIP.
"""

NAME_MAX = 127          # gba_rom_entry.name
FAT_NAME_MAX = 255      # the longest name a directory entry gives
ROM_EXTS = ("gba", "gb", "gbc", "sfc", "smc", "md", "gen", "bin", "smd", "sms", "gg", "sg")

_LOWER = {c: c + 32 for c in range(ord("A"), ord("Z") + 1)}


def console_name(name):
    """A file name as the console reads it from the card: non-ASCII UTF-16 units become "_"."""
    if name.isascii():
        out = name
    else:
        raw = name.encode("utf-16-le", "surrogatepass")
        out = "".join(chr(u) if u < 0x80 else "_"
                      for u in (raw[i] | (raw[i + 1] << 8) for i in range(0, len(raw), 2)))
    return out[:FAT_NAME_MAX]


def name_from_7z(name):
    """A name inside a .7z as the console reads it: the same unit rule, at most 255."""
    return console_name(name)


def fold(s):
    """The console's case folding (A-Z only) for comparing and sorting."""
    return s.translate(_LOWER)


def same(a, b):
    return fold(a) == fold(b)


def ends_with_any(name, exts):
    """The length of the extension in `exts` that `name` ends with (any case), 0 for none.

    The name must be longer than the extension (word_in with suffix in the console)."""
    low = fold(name)
    for e in exts:
        if len(low) > len(e) and low.endswith(e):
            return len(e)
    return 0


def is_known_rom_ext(ext):
    return fold(ext) in ROM_EXTS


def base_ext(name, ext, legacy=False):
    """"<name>.<ext>": the file name the console uses for a game's box art, saves and states.

    Only a real ROM extension is cut from `name` (the listing already dropped it); the legacy form
    cuts at the last dot whatever follows it (files written by older versions)."""
    dot = name.rfind(".")
    if dot < 0 or (not legacy and not is_known_rom_ext(name[dot + 1:])):
        dot = len(name)
    return name[:dot][:288 - 6] + "." + ext


def clean_title(name):
    """The menu's title for a game: the name without a trailing ".gba" and without the trailing
    tag groups such as " (USA, Europe)" or " [!]" (gba_clean_name), the name itself when that
    leaves nothing."""
    d = name[:NAME_MAX]
    if len(d) >= 4 and d[-4] == "." and fold(d[-3:]) == "gba":
        d = d[:-4]
    while True:
        e = len(d)
        while e > 0 and d[e - 1] == " ":
            e -= 1
        if e <= 0:
            break
        c = d[e - 1]
        if c not in ")]":
            d = d[:e]
            break
        opener = "(" if c == ")" else "["
        j = e - 1
        depth = 0
        while j >= 0:
            if d[j] == c:
                depth += 1
            elif d[j] == opener:
                depth -= 1
                if depth == 0:
                    break
            j -= 1
        if j <= 0:
            break           # no opener, or the whole name is the group: kept as it is
        d = d[:j]
    return d if d else name[:NAME_MAX]


def display_text(s):
    """Text fit for the screen from a console name (bytes kept as latin-1 become UTF-8 again
    when they form valid UTF-8)."""
    if s.isascii():
        return s
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def letter(title):
    """The letter a title files under ('#' for digits and the rest), as the menu's jump list."""
    for ch in title:
        if "a" <= ch <= "z":
            return ch.upper()
        if "A" <= ch <= "Z":
            return ch
        if "0" <= ch <= "9":
            return "#"
    return "#"


FAT_FORBIDDEN = set('\\/:*?"<>|')


def fat_safe(name):
    """Can a file of this name exist on a FAT or exFAT card?"""
    if not name or name in (".", "..") or name.endswith(" ") or name.endswith("."):
        return False
    return not any(ch in FAT_FORBIDDEN or ord(ch) < 0x20 for ch in name)
