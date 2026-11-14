"""Names the way the console sees them.

The card keeps names in UTF-16 (FAT long names, exFAT names) and the console reads them as UTF-8,
any script as it is, a decomposed name (as macOS writes it) as decomposed. A name longer than 255
bytes in UTF-8 is cut at a whole character before its extension, which it keeps. Files and folders
are matched without case for the letters A to Z only (ascii_fold).

A game's name is its file name without the extension, cut at 127 bytes on a character's end; its
saves, states and box art are filed under that name. Versions before 1.1 read each UTF-16 unit
outside ASCII as "_" and filed such a name under that underscore form (legacy_key): a game still
reads a file of its old form, but only when no other game of the library has the same form
(LegacyForms).

The list is sorted with the accents folded (pico_fold.c: sort_key), the menu's title drops the
trailing region and dump tags (clean_title), and its letters and search fold accents too (letter,
fold).

Names here are Python strings of the characters the console reads. Bytes that are not UTF-8 (only
a ZIP entry's name can hold them) are kept as surrogate escapes, so a name's bytes on the console
are always to_bytes(name).
"""

import unicodedata

NAME_MAX = 127          # gba_rom_entry.name, in bytes
FAT_NAME_MAX = 255      # the longest name a directory entry gives, in bytes
ROM_EXTS = ("gba", "gb", "gbc", "sfc", "smc", "md", "gen", "bin", "smd", "sms", "gg", "sg")

_LOWER = {c: c + 32 for c in range(ord("A"), ord("Z") + 1)}


# ---- bytes and code points ---------------------------------------------------------------------

def to_bytes(s):
    """The UTF-8 bytes the console holds for a name."""
    try:
        return s.encode("utf-8", "surrogateescape")
    except UnicodeEncodeError:
        return s.encode("utf-8", "replace")


def from_bytes(b):
    return b.decode("utf-8", "surrogateescape")


def u8len(s):
    return len(s) if s.isascii() else len(to_bytes(s))


def _u8_next(b, i):
    """The code point at b[i] and the index after it (fat_u8_next): a byte that does not start a
    whole UTF-8 character reads as itself."""
    c = b[i]
    n = len(b)

    def cont(k):
        return i + k < n and (b[i + k] & 0xC0) == 0x80
    if 0xC2 <= c < 0xE0 and cont(1):
        return ((c & 31) << 6) | (b[i + 1] & 63), i + 2
    if 0xE0 <= c < 0xF0 and cont(1) and cont(2):
        return ((c & 15) << 12) | ((b[i + 1] & 63) << 6) | (b[i + 2] & 63), i + 3
    if 0xF0 <= c < 0xF5 and cont(1) and cont(2) and cont(3):
        return (((c & 7) << 18) | ((b[i + 1] & 63) << 12) | ((b[i + 2] & 63) << 6)
                | (b[i + 3] & 63)), i + 4
    return c, i + 1


def code_points(s):
    """The code points the console reads in a name, in order."""
    if s.isascii():
        return [ord(c) for c in s]
    try:
        s.encode("utf-8")
        return [ord(c) for c in s]
    except UnicodeEncodeError:
        pass
    b = to_bytes(s)
    out = []
    i = 0
    while i < len(b):
        c, i = _u8_next(b, i)
        out.append(c)
    return out


def cut_u8(s, n):
    """The name cut to at most n bytes, at the end of a whole character (u8_copy)."""
    if s.isascii():
        return s[:n]
    b = to_bytes(s)
    if len(b) <= n:
        return s
    i = 0
    while i < len(b):
        _c, j = _u8_next(b, i)
        if j > n:
            break
        i = j
    return from_bytes(b[:i])


def cut_bytes(s, n):
    """The first n bytes of a name, wherever that falls (the console's fixed path buffers)."""
    if s.isascii():
        return s[:n]
    b = to_bytes(s)
    return s if len(b) <= n else from_bytes(b[:n])


def _u16_cps(units):
    """Code points of UTF-16 units (fat_u16_next): a surrogate pair as one, a surrogate without
    its other half as U+FFFD."""
    i, n = 0, len(units)
    while i < n:
        c = units[i]
        i += 1
        if 0xD800 <= c < 0xDC00 and i < n and 0xDC00 <= units[i] < 0xE000:
            yield 0x10000 + ((c - 0xD800) << 10) + (units[i] - 0xDC00)
            i += 1
        elif 0xD800 <= c < 0xE000:
            yield 0xFFFD
        else:
            yield c


def _units(s):
    raw = s.encode("utf-16-le", "surrogatepass")
    return [raw[i] | (raw[i + 1] << 8) for i in range(0, len(raw), 2)]


def units_to_name(units, cap):
    """UTF-16 units as UTF-8 of at most cap bytes, whole characters only (fat_u16_to_u8)."""
    out = bytearray()
    for c in _u16_cps(units):
        b = chr(c).encode("utf-8")
        if len(out) + len(b) > cap:
            break
        out += b
    return out.decode("utf-8")


def console_name(name):
    """A file name as the console reads it from the card (fat_u16_name): UTF-8, a surrogate
    without its other half as U+FFFD, and one longer than 255 bytes cut before its extension,
    which it keeps."""
    if name.isascii() and len(name) <= FAT_NAME_MAX:
        return name
    units = _units(name)
    out = bytearray()
    whole = True
    for c in _u16_cps(units):
        b = chr(c).encode("utf-8")
        if len(out) + len(b) > FAT_NAME_MAX:
            whole = False
            break
        out += b
    if not whole:
        out = _keep_ext(out, units[-16:])
    return out.decode("utf-8")


def _keep_ext(out, tail):
    """fat_name_keep_ext: the extension, found in the name's last UTF-16 units, put back at the
    end in place of the whole characters before it."""
    d = -1
    for i in range(len(tail) - 1, -1, -1):
        if tail[i] == 0x2E:
            d = i
            break
    if d < 0:
        return out
    ext = bytearray()
    for c in _u16_cps(tail[d:]):
        b = chr(c).encode("utf-8")
        if len(ext) + len(b) > 71:
            return out
        ext += b
    if len(ext) > FAT_NAME_MAX:
        return out
    o = len(out)
    while o > 0 and o + len(ext) > FAT_NAME_MAX:
        o -= 1
        while o > 0 and (out[o] & 0xC0) == 0x80:
            o -= 1
    return out[:o] + ext


def name_from_7z(units):
    """A name inside a .7z as the console reads it: its first 255 UTF-16 units, as UTF-8 of at
    most 511 bytes."""
    return units_to_name(units[:255], 511)


# ---- comparing and sorting -----------------------------------------------------------------------

def ascii_fold(s):
    """The console's case folding for file and folder names (A-Z only)."""
    return s.translate(_LOWER)


def same(a, b):
    """Do two names match as the card's file names do?"""
    return ascii_fold(a) == ascii_fold(b)


# pico_fold.c's tables: the plain letter of each code point, a '*' a character kept as it is
_LATIN1 = "AAAAAAACEEEEIIII" "DNOOOOO*OUUUUYTs" "aaaaaaaceeeeiiii" "dnooooo*ouuuuyty"     # U+00C0..
_EXT_A = ("AaAaAaCcCcCcCcDd" "DdEeEeEeEeEeGgGg" "GgGgHhHhIiIiIiIi" "IiIiJjKkkLlLlLlL"
          "lLlNnNnNnnNnOoOo" "OoOoRrRrRrSsSsSs" "SsTtTtTtUuUuUuUu" "UuUuWwYyYZzZzZzs")     # U+0100..
_EXT_ADD_PAIRS = "ABBBCDDDDDEEEEEFGHHHHHIIKKKLLLLMMMNNNNOOOOPPRRRRSSSSSTTTTUUUUUVVWWWWWXXYZZZ"  # U+1E00..
_EXT_ADD_SINGLE = "htwyasssSd"                                                         # U+1E96..
_VIET = ((0x1EB7, "A"), (0x1EC7, "E"), (0x1ECB, "I"), (0x1EE3, "O"), (0x1EF1, "U"), (0x1EF9, "Y"))
_EXT_ADD_TAIL = "LlVvYy"                                                               # U+1EFA..
_EXT_B = {
    0x0180: "b", 0x0197: "I", 0x01A0: "O", 0x01A1: "o", 0x01AF: "U", 0x01B0: "u", 0x01B5: "Z",
    0x01B6: "z", 0x01C4: "D", 0x01C5: "D", 0x01C6: "d", 0x01C7: "L", 0x01C8: "L", 0x01C9: "l",
    0x01CA: "N", 0x01CB: "N", 0x01CC: "n", 0x01CD: "A", 0x01CE: "a", 0x01CF: "I", 0x01D0: "i",
    0x01D1: "O", 0x01D2: "o", 0x01D3: "U", 0x01D4: "u", 0x01D5: "U", 0x01D6: "u", 0x01D7: "U",
    0x01D8: "u", 0x01D9: "U", 0x01DA: "u", 0x01DB: "U", 0x01DC: "u", 0x01DE: "A", 0x01DF: "a",
    0x01E0: "A", 0x01E1: "a", 0x01E2: "A", 0x01E3: "a", 0x01E4: "G", 0x01E5: "g", 0x01E6: "G",
    0x01E7: "g", 0x01E8: "K", 0x01E9: "k", 0x01EA: "O", 0x01EB: "o", 0x01EC: "O", 0x01ED: "o",
    0x01F0: "j", 0x01F1: "D", 0x01F2: "D", 0x01F3: "d", 0x01F4: "G", 0x01F5: "g", 0x01F8: "N",
    0x01F9: "n", 0x01FA: "A", 0x01FB: "a", 0x01FC: "A", 0x01FD: "a", 0x01FE: "O", 0x01FF: "o",
    0x0200: "A", 0x0201: "a", 0x0202: "A", 0x0203: "a", 0x0204: "E", 0x0205: "e", 0x0206: "E",
    0x0207: "e", 0x0208: "I", 0x0209: "i", 0x020A: "I", 0x020B: "i", 0x020C: "O", 0x020D: "o",
    0x020E: "O", 0x020F: "o", 0x0210: "R", 0x0211: "r", 0x0212: "R", 0x0213: "r", 0x0214: "U",
    0x0215: "u", 0x0216: "U", 0x0217: "u", 0x0218: "S", 0x0219: "s", 0x021A: "T", 0x021B: "t",
    0x021E: "H", 0x021F: "h", 0x0226: "A", 0x0227: "a", 0x0228: "E", 0x0229: "e", 0x022A: "O",
    0x022B: "o", 0x022C: "O", 0x022D: "o", 0x022E: "O", 0x022F: "o", 0x0230: "O", 0x0231: "o",
    0x0232: "Y", 0x0233: "y",
}


def fold_cp(cp):
    """pico_fold_cp: the plain ASCII letter of a Latin letter with a diacritic (case kept), the
    first letter of a ligature or a letter like ae or sharp s, fullwidth ASCII as ASCII, 0 for a
    combining mark U+0300..U+036F (dropped), any other code point as it is."""
    if cp < 0x80:
        return cp
    if 0x0300 <= cp <= 0x036F:
        return 0
    if 0xFF01 <= cp <= 0xFF5E:
        return cp - 0xFEE0
    b = None
    if 0x00C0 <= cp <= 0x00FF:
        b = _LATIN1[cp - 0xC0]
    elif 0x0100 <= cp <= 0x017F:
        b = _EXT_A[cp - 0x100]
    elif 0x0180 <= cp <= 0x0233:
        b = _EXT_B.get(cp)
    elif 0x1E00 <= cp <= 0x1E95:
        b = _EXT_ADD_PAIRS[(cp - 0x1E00) // 2]
        if cp & 1:
            b = chr(ord(b) + 32)        # the small letter of the pair
    elif 0x1E96 <= cp <= 0x1E9F:
        b = _EXT_ADD_SINGLE[cp - 0x1E96]
    elif 0x1EA0 <= cp <= 0x1EF9:
        for last, base in _VIET:
            if cp <= last:
                b = base
                break
        if cp & 1:
            b = chr(ord(b) + 32)
    elif 0x1EFA <= cp <= 0x1EFF:
        b = _EXT_ADD_TAIL[cp - 0x1EFA]
    return ord(b) if b and b != "*" else cp


def sort_key(name):
    """The console's order of names (pico_fold_cmp): folded code points, combining marks left out,
    ASCII letters in either case alike; a character outside ASCII that does not fold comes after
    all of ASCII, by code point."""
    out = []
    for c in code_points(name):
        c = fold_cp(c)
        if c == 0:
            continue
        if 65 <= c <= 90:
            c += 32
        out.append(c)
    return tuple(out)


def fold_ascii(name):
    """pico_fold_ascii: the folded letters, every other character outside ASCII as one "_"."""
    out = []
    for c in code_points(name):
        c = fold_cp(c)
        if c:
            out.append(chr(c) if c < 0x80 else "_")
    return "".join(out)


# ---- the menu's labels: titles, letters and search ------------------------------------------------

_COMP = None


def _compositions():
    """{(letter, mark): precomposed}: the canonical pairs of the BMP NFC puts together, the table
    the card's names.pack carries for the menu to compose a decomposed name."""
    global _COMP
    if _COMP is None:
        comp = {}
        for cp in range(0x80, 0x10000):
            d = unicodedata.decomposition(chr(cp))
            if not d or d.startswith("<"):
                continue
            parts = [int(x, 16) for x in d.split()]
            if len(parts) == 2 and unicodedata.normalize("NFC", chr(parts[0]) + chr(parts[1])) == chr(cp):
                comp[(parts[0], parts[1])] = cp
        _COMP = comp
    return _COMP


def _compose(a, b):
    """A letter and the mark after it as one character, or 0 (pico_names.c compose)."""
    if 0x1100 <= a <= 0x1112 and 0x1161 <= b <= 0x1175:            # Hangul jamo
        return 0xAC00 + ((a - 0x1100) * 21 + (b - 0x1161)) * 28
    if 0xAC00 <= a <= 0xD7A3 and (a - 0xAC00) % 28 == 0 and 0x11A8 <= b <= 0x11C2:
        return a + (b - 0x11A7)
    if a > 0xFFFF or b > 0xFFFF:
        return 0
    return _compositions().get((a, b), 0)


def _arabic_mark(c):
    return 0x64B <= c <= 0x65F or c == 0x670 or 0x6D6 <= c <= 0x6ED


def label_code_points(text, cap=128):
    """A title's characters as the Current theme's menus hold them (pico_names_label, at most
    `cap`): a mark or a jamo after the character it belongs to composed with it, a combining
    accent with nothing to sit on and Arabic vowel marks left out. Arabic joining and the right to
    left drawing order only change how the name is drawn and are not done here."""
    out = []
    for c in code_points(text):
        if cap is not None and len(out) >= cap:
            break
        if c >= 0x300 and out:
            k = _compose(out[-1], c)
            if k:
                out[-1] = k
                continue
        if 0x300 <= c <= 0x36F or _arabic_mark(c):
            continue
        out.append(c)
    return out


def _fold_low(c):
    c = fold_cp(c)
    return c + 32 if 65 <= c <= 90 else c


def fold(text):
    """Text as the menu's search compares it (word_at): accents folded, small letters, a
    decomposed name the same as a composed one. Use it on both the names and the words looked
    for. The console's keyboard types A-Z only; the capitals of other scripts are made small too,
    so a word typed on a computer in Greek or Cyrillic finds its names in any case."""
    out = []
    for c in label_code_points(text, None):
        c = _fold_low(c)
        if c:
            out.append(chr(c) if c <= 0x10FFFF else "\ufffd")
    s = "".join(out)
    return s if s.isascii() else s.lower()


def _other_script(c):
    return (0x370 <= c <= 0x1FFF or 0x3040 <= c <= 0xD7A3 or 0xF900 <= c <= 0xFAFF
            or 0xFB1D <= c <= 0xFEFF or 0xFF21 <= c <= 0xFF5A or c >= 0x20000)


def letter(title):
    """The letter a title files under in the menu's jump list (name_letter): its first letter
    without accents, capital; "#" for a digit, a letter of another script or none at all."""
    for c in label_code_points(title):
        c = fold_cp(c)
        if 97 <= c <= 122:
            return chr(c - 32)
        if 65 <= c <= 90:
            return chr(c)
        if 48 <= c <= 57 or _other_script(c):
            return "#"
    return "#"


# ---- file names --------------------------------------------------------------------------------

def ends_with_any(name, exts):
    """The length of the extension in `exts` that `name` ends with (A-Z in any case), 0 for none.

    The name must be longer than the extension (word_in with suffix in the console)."""
    low = ascii_fold(name)
    for e in exts:
        if len(low) > len(e) and low.endswith(e):
            return len(e)
    return 0


def is_known_rom_ext(ext):
    return ascii_fold(ext) in ROM_EXTS


def base_ext(name, ext, legacy=False):
    """"<name>.<ext>": the file name the console uses for a game's box art, saves and states.

    Only a real ROM extension is cut from `name` (the listing already dropped it); the legacy form
    cuts at the last dot whatever follows it (files written by older versions)."""
    dot = name.rfind(".")
    if dot < 0 or (not legacy and not is_known_rom_ext(name[dot + 1:])):
        dot = len(name)
    return cut_bytes(name[:dot], 288 - 6) + "." + ext


def legacy_key(key):
    """(form, differs): a game's name as versions before 1.1 filed it (gba_sd_legacy_key): ASCII
    kept, every character outside ASCII as "_", two for one beyond U+FFFF (one per UTF-16 unit),
    cut at 127. `differs` is False when the name is all ASCII, so the form is the name itself."""
    if key.isascii():
        return key[:NAME_MAX], False
    out = []
    for c in code_points(key):
        if len(out) >= NAME_MAX:
            break
        if c < 0x80:
            out.append(chr(c))
            continue
        out.append("_")
        if c >= 0x10000 and len(out) < NAME_MAX:
            out.append("_")
    return "".join(out), True


class LegacyForms(object):
    """The underscore forms of every game listed, with how many games have each (up to two), so a
    game reads files under its old form only when it is its own (gba_sd_legacy_shared / _own)."""

    def __init__(self, keys=()):
        self._n = {}
        for k in keys:
            self.add(k)

    def add(self, key):
        form = ascii_fold(legacy_key(key)[0])
        self._n[form] = min(2, self._n.get(form, 0) + 1)

    def shared(self, form):
        return self._n.get(ascii_fold(form), 0) >= 2

    def own(self, key):
        """The game's old form when it has one and no other game shares it, else None."""
        form, differs = legacy_key(key)
        return form if differs and not self.shared(form) else None


def own_legacy(key, forms):
    """LegacyForms.own, None when no full listing is known (the console then reads no old form)."""
    return forms.own(key) if forms is not None else None


def named_forms(key, ext, forms=None):
    """The file names the console tries, in order, for a game's file with this extension
    (gba_sd_read_named): its name, the older one cut at the last dot, then its underscore form
    both ways when that is its own; each name once."""
    out = []
    names = [(key, False), (key, True)]
    old = own_legacy(key, forms)
    if old is not None:
        names += [(old, False), (old, True)]
    for k, cut in names:
        n = base_ext(k, ext, legacy=cut)
        if n not in out:
            out.append(n)
    return out


def clean_title(name):
    """The menu's title for a game: the name without a trailing ".gba" and without the trailing
    tag groups such as " (USA, Europe)" or " [!]" (gba_clean_name), the name itself when that
    leaves nothing."""
    d = cut_bytes(name, NAME_MAX)
    if len(d) >= 4 and d[-4] == "." and ascii_fold(d[-3:]) == "gba":
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
    return d if d else cut_bytes(name, NAME_MAX)


def display_text(s):
    """Text fit for the screen from a console name (bytes that are not UTF-8 shown as U+FFFD)."""
    if s.isascii():
        return s
    try:
        s.encode("utf-8")
        return s
    except UnicodeEncodeError:
        return to_bytes(s).decode("utf-8", "replace")


FAT_FORBIDDEN = set('\\/:*?"<>|')


def fat_safe(name):
    """Can a file of this name exist on a FAT or exFAT card, and read back as these characters?"""
    if not name or name in (".", "..") or name.endswith(" ") or name.endswith("."):
        return False
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return not any(ch in FAT_FORBIDDEN or ord(ch) < 0x20 for ch in name)
