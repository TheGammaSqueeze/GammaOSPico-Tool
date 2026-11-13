"""Colours, fonts and sizes: GammaOS Pico's "Current" theme.

The ink, muted and button colours and the accents are the console's own; the panels are the deep
navy of its library shade. Sizes are in design pixels and scaled to the screen (HiDPI) with S().
"""

import sys

INK = "#F4F8FF"
MUTED = "#BDCEE4"
BTN_INK = "#112030"
SHADOW = "#060A23"
SHADE = "#060D19"

PANEL = "#0B1530"          # panel fill, every widget inside a panel uses it
PANEL_EDGE = "#22365E"     # its hairline
FIELD = "#111E3D"          # text fields and list wells
FIELD_EDGE = "#2A3E66"
LINE = "#1E2F52"           # row separators
DIM = "#7F93B4"            # quieter than muted (hints, disabled)
OK_GREEN = "#A2F2C6"
WARN = "#FFD49A"
DANGER = "#FFB4C0"

# The accents and the 3x3 matrices (1024 = 1.0) that turn the blue backdrop into each colour: the
# console's thirteen, in the order its Colour setting steps through them (round the colour wheel
# from the design's blue, then the two neutrals). Blue stays first, the backdrop's own colours.
ACCENTS = (
    ("Blue", "#98E8FF", (1024, 0, 0, 0, 1024, 0, 0, 0, 1024)),
    ("Indigo", "#ADB8FF", (928, -179, 275, 51, 1053, -80, -221, 240, 1005)),
    ("Violet", "#D4BBFF", (595, -302, 731, 181, 1034, -191, -527, 786, 764)),
    ("Orchid", "#F8ADFF", (293, -209, 939, 280, 968, -224, -613, 1165, 473)),
    ("Rose", "#FFADD9", (-99, 98, 1026, 391, 838, -205, -554, 1550, 28)),
    ("Crimson", "#FFB4C0", (-450, 606, 868, 468, 665, -109, -283, 1760, -453)),
    ("Coral", "#FFBEAD", (-577, 951, 650, 481, 565, -21, -35, 1744, -685)),
    ("Amber", "#FFD49A", (-610, 1355, 279, 454, 463, 106, 322, 1559, -857)),
    ("Lime", "#D6F0A8", (-251, 1765, -490, 288, 417, 319, 909, 810, -695)),
    ("Emerald", "#A2F2C6", (637, 1250, -863, 20, 669, 335, 940, -165, 248)),
    ("Teal", "#A7F1E7", (860, 910, -746, -27, 783, 268, 752, -288, 560)),
    ("Sepia", "#F0D9B5", (389, 857, 86, 314, 692, 69, 224, 494, 50)),
    ("Graphite", "#E2E6EC", (299, 659, 66, 196, 761, 66, 196, 659, 169)),
)
ACCENT_NAMES = tuple(a[0] for a in ACCENTS)

FONT_PREFS = ("Inter", "Segoe UI", "SF Pro Text", ".AppleSystemUIFont", "Helvetica Neue", "Helvetica",
              "Cantarell", "Ubuntu", "Noto Sans", "DejaVu Sans", "Arial")

_scale = 1.0


def set_scale(s):
    global _scale
    _scale = max(1.0, min(4.0, s))


def scale():
    return _scale


def S(px):
    """Design pixels to screen pixels."""
    return int(round(px * _scale))


def accent(name):
    for n, c, _m in ACCENTS:
        if n == name:
            return c
    return ACCENTS[0][1]


def matrix(name):
    for n, _c, m in ACCENTS:
        if n == name:
            return m
    return ACCENTS[0][2]


def recolour(rgb, name):
    """An (r, g, b) of the blue design in another accent's colours."""
    m = matrix(name)
    r, g, b = rgb
    out = []
    for i in range(3):
        v = (m[3 * i] * r + m[3 * i + 1] * g + m[3 * i + 2] * b) >> 10
        out.append(0 if v < 0 else 255 if v > 255 else v)
    return tuple(out)


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def rgb_hex(rgb):
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(round(c)))) for c in rgb[:3])


def mix(a, b, t):
    """Colour a blended toward b by t (0..1), hex in and out."""
    ra, rb = hex_rgb(a), hex_rgb(b)
    return rgb_hex(tuple(ra[i] + (rb[i] - ra[i]) * t for i in range(3)))


def panel_for(name):
    """The panel colour in an accent's colours (the navy turns with the backdrop)."""
    return rgb_hex(recolour(hex_rgb(PANEL), name))


def pick_family(families):
    have = set(families)
    for f in FONT_PREFS:
        if f in have:
            return f
    return "TkDefaultFont"


MONO_PREFS = ("JetBrains Mono", "Cascadia Mono", "Consolas", "SF Mono", "Menlo", "DejaVu Sans Mono",
              "Noto Sans Mono", "Courier New")


def pick_mono(families):
    have = set(families)
    for f in MONO_PREFS:
        if f in have:
            return f
    return "TkFixedFont"


IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"
MOD = "Command" if IS_MAC else "Control"


class Palette(object):
    """Every colour the window uses, for one accent (the navy ones turn with the backdrop)."""

    def __init__(self, name):
        self.name = name if name in ACCENT_NAMES else ACCENT_NAMES[0]
        turn = lambda h: rgb_hex(recolour(hex_rgb(h), self.name))
        self.accent = accent(self.name)
        self.ink, self.muted, self.btn_ink, self.dim = INK, MUTED, BTN_INK, DIM
        self.panel = turn(PANEL)
        self.panel_edge = turn(PANEL_EDGE)
        self.field = turn(FIELD)
        self.field_edge = turn(FIELD_EDGE)
        self.line = turn(LINE)
        self.shadow = turn(SHADOW)
        self.hover = mix(self.panel, self.accent, 0.07)
        self.select = mix(self.panel, self.accent, 0.16)
        self.select_end = mix(self.panel, self.accent, 0.03)
        self.ok, self.warn, self.danger = OK_GREEN, WARN, DANGER
        self.danger_wash = mix(self.panel, DANGER, 0.12)
        self.warn_wash = mix(self.panel, WARN, 0.10)
        self.note_wash = mix(self.panel, self.accent, 0.08)
