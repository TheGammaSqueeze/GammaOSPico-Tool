"""Small pictures drawn with Pillow for the window: pills, rounded boxes, switches and icons.

Everything is drawn four times larger and scaled down, so the edges are smooth on every system
(Tk's own canvas shapes are not anti-aliased on Windows or X11). Pictures keep their alpha, so they
sit on any background. Cached by what they look like.
"""

import math

from PIL import Image, ImageDraw, ImageTk

from . import theme

SS = 4
_cache = {}


def _rgba(color, alpha=255):
    if isinstance(color, tuple):
        return color if len(color) == 4 else color + (alpha,)
    return theme.hex_rgb(color) + (alpha,)


def _keep(key, make):
    hit = _cache.get(key)
    if hit is None:
        hit = ImageTk.PhotoImage(make())
        _cache[key] = hit
    return hit


def clear():
    _cache.clear()


def pill_image(w, h, fill=None, border=None, bw=1.0, radius=None):
    W, H = w * SS, h * SS
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    r = (radius if radius is not None else h / 2.0) * SS
    bwp = max(1, int(round(bw * SS)))
    if border:
        d.rounded_rectangle((0, 0, W - 1, H - 1), radius=r, fill=_rgba(border))
        if fill:
            d.rounded_rectangle((bwp, bwp, W - 1 - bwp, H - 1 - bwp), radius=max(0, r - bwp), fill=_rgba(fill))
        else:
            d.rounded_rectangle((bwp, bwp, W - 1 - bwp, H - 1 - bwp), radius=max(0, r - bwp), fill=(0, 0, 0, 0))
    elif fill:
        d.rounded_rectangle((0, 0, W - 1, H - 1), radius=r, fill=_rgba(fill))
    return im.resize((w, h), Image.LANCZOS)


def pill(w, h, fill=None, border=None, bw=1.0, radius=None):
    return _keep(("pill", w, h, fill, border, bw, radius), lambda: pill_image(w, h, fill, border, bw, radius))


def switch(on, accent, track_off, knob="#F4F8FF"):
    w, h = theme.S(38), theme.S(22)

    def make():
        W, H = w * SS, h * SS
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((0, 0, W - 1, H - 1), radius=H / 2, fill=_rgba(accent if on else track_off))
        m = 3 * SS * theme.scale()
        kd = H - 2 * m
        x0 = W - m - kd if on else m
        d.ellipse((x0, m, x0 + kd, m + kd), fill=_rgba(theme.BTN_INK if on else knob))
        return im.resize((w, h), Image.LANCZOS)
    return _keep(("switch", on, accent, track_off, w), make)


def check(on, accent, edge):
    s = theme.S(18)

    def make():
        W = s * SS
        im = Image.new("RGBA", (W, W), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        if on:
            d.rounded_rectangle((0, 0, W - 1, W - 1), radius=W * 0.25, fill=_rgba(accent))
            lw = max(2, int(W * 0.12))
            d.line([(W * 0.24, W * 0.52), (W * 0.43, W * 0.70), (W * 0.77, W * 0.32)],
                   fill=_rgba(theme.BTN_INK), width=lw, joint="curve")
        else:
            bw = max(SS, int(W * 0.08))
            d.rounded_rectangle((0, 0, W - 1, W - 1), radius=W * 0.25, fill=_rgba(edge))
            d.rounded_rectangle((bw, bw, W - 1 - bw, W - 1 - bw), radius=W * 0.2, fill=(0, 0, 0, 0))
        return im.resize((s, s), Image.LANCZOS)
    return _keep(("check", on, accent, edge, s), make)


def radio(on, accent, edge):
    s = theme.S(18)

    def make():
        W = s * SS
        im = Image.new("RGBA", (W, W), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        bw = max(SS, int(W * 0.08))
        d.ellipse((0, 0, W - 1, W - 1), fill=_rgba(accent if on else edge))
        d.ellipse((bw, bw, W - 1 - bw, W - 1 - bw), fill=(0, 0, 0, 0))
        if on:
            k = W * 0.27
            d.ellipse((k, k, W - 1 - k, W - 1 - k), fill=_rgba(accent))
        return im.resize((s, s), Image.LANCZOS)
    return _keep(("radio", on, accent, edge, s), make)


def swatch(color, selected, ring):
    s = theme.S(30)

    def make():
        W = s * SS
        im = Image.new("RGBA", (W, W), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        if selected:
            d.ellipse((0, 0, W - 1, W - 1), fill=_rgba(ring))
            k = W * 0.1
            d.ellipse((k, k, W - 1 - k, W - 1 - k), fill=(0, 0, 0, 0))
        k = W * 0.18
        d.ellipse((k, k, W - 1 - k, W - 1 - k), fill=_rgba(color))
        return im.resize((s, s), Image.LANCZOS)
    return _keep(("swatch", color, selected, ring, s), make)


def glow(color, size):
    """A soft round glow (the selected tab's)."""
    def make():
        im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        px = im.load()
        c = theme.hex_rgb(color)
        r = size / 2.0
        for y in range(size):
            for x in range(size):
                d = math.hypot(x + 0.5 - r, y + 0.5 - r) / r
                a = max(0.0, 1.0 - d) ** 2 * 0.22
                px[x, y] = c + (int(a * 255),)
        return im
    return _keep(("glow", color, size), make)


# ---- icons: line drawings on a 24 unit grid ----------------------------------------------------

def _icon(name, size, color, weight=1.6):
    W = size * SS
    k = W / 24.0
    im = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = _rgba(color)
    lw = max(SS, int(round(weight * k)))

    def P(*pts):
        return [(x * k, y * k) for x, y in pts]

    def box(x0, y0, x1, y1, r, fill=False):
        if fill:
            d.rounded_rectangle((x0 * k, y0 * k, x1 * k, y1 * k), radius=r * k, fill=c)
        else:
            d.rounded_rectangle((x0 * k, y0 * k, x1 * k, y1 * k), radius=r * k, outline=c, width=lw)

    def circle(cx, cy, r, fill=False):
        bb = ((cx - r) * k, (cy - r) * k, (cx + r) * k, (cy + r) * k)
        if fill:
            d.ellipse(bb, fill=c)
        else:
            d.ellipse(bb, outline=c, width=lw)

    def line(*pts):
        d.line(P(*pts), fill=c, width=lw, joint="curve")
        for p in P(pts[0], pts[-1]):
            d.ellipse((p[0] - lw / 2.0, p[1] - lw / 2.0, p[0] + lw / 2.0, p[1] + lw / 2.0), fill=c)

    if name == "card":          # a microSD card
        line((7, 3), (16, 3), (19, 6), (19, 21), (5, 21), (5, 5), (7, 3))
        line((9, 6.5), (9, 9))
        line((12, 6.5), (12, 9))
        line((15, 6.5), (15, 9))
    elif name == "games":       # a gamepad
        box(2.5, 7, 21.5, 18, 5)
        line((7.5, 10.5), (7.5, 14.5))
        line((5.5, 12.5), (9.5, 12.5))
        circle(15.5, 11.2, 1.0, True)
        circle(17.8, 13.8, 1.0, True)
    elif name == "console":     # a handheld
        box(3, 5, 21, 19, 3)
        box(7, 8, 17, 16, 1)
        circle(5.0, 12, 0.6, True)
        circle(19.0, 12, 0.6, True)
    elif name == "settings":    # sliders
        line((3, 7), (21, 7))
        line((3, 17), (21, 17))
        box(6, 4.5, 10, 9.5, 1.5, True)
        box(14, 14.5, 18, 19.5, 1.5, True)
    elif name == "about":       # i in a circle
        circle(12, 12, 9)
        line((12, 11), (12, 16.5))
        circle(12, 7.8, 1.0, True)
    elif name == "refresh":
        d.arc((4 * k, 4 * k, 20 * k, 20 * k), 30, 320, fill=c, width=lw)
        d.polygon(P((20.5, 3.5), (21, 10), (14.5, 9)), fill=c)
    elif name == "folder":
        line((3, 6), (9, 6), (11, 8), (21, 8), (21, 19), (3, 19), (3, 6))
    elif name == "search":
        circle(10.5, 10.5, 6)
        line((15, 15), (20.5, 20.5))
    elif name == "download":
        line((12, 4), (12, 15))
        line((7.5, 10.5), (12, 15), (16.5, 10.5))
        line((4.5, 19.5), (19.5, 19.5))
    elif name == "image":
        box(3, 5, 21, 19, 2)
        line((5, 17), (10, 11.5), (14, 15.5), (16.5, 13), (20, 17))
        circle(16, 9, 1.4, True)
    elif name == "trash":
        line((4, 6.5), (20, 6.5))
        line((9, 6.5), (9, 4), (15, 4), (15, 6.5))
        line((6, 6.5), (7, 20), (17, 20), (18, 6.5))
    elif name == "chip":        # the bootloader
        box(6, 6, 18, 18, 2)
        for t in (9.5, 14.5):
            line((t, 2.5), (t, 6))
            line((t, 18), (t, 21.5))
            line((2.5, t), (6, t))
            line((18, t), (21.5, t))
    elif name == "shield":
        line((12, 3), (19.5, 6), (19.5, 11.5), (18, 16), (12, 21), (6, 16), (4.5, 11.5), (4.5, 6), (12, 3))
        line((8.5, 12), (11, 14.5), (15.5, 9.5))
    elif name == "restore":
        d.arc((4 * k, 4 * k, 20 * k, 20 * k), 200, 500, fill=c, width=lw)
        d.polygon(P((2.5, 6), (4.5, 12.5), (9.5, 8.5)), fill=c)
        line((12, 8), (12, 12.5), (15, 14))
    elif name == "warn":
        line((12, 3.5), (21.5, 20), (2.5, 20), (12, 3.5))
        line((12, 9.5), (12, 14))
        circle(12, 17, 1.0, True)
    elif name == "check":
        line((5, 12.5), (10, 17.5), (19.5, 7))
    elif name == "close":
        line((6, 6), (18, 18))
        line((18, 6), (6, 18))
    elif name == "external":
        line((13, 4), (20, 4), (20, 11))
        line((20, 4), (11, 13))
        line((17, 14), (17, 20), (4, 20), (4, 7), (10, 7))
    elif name == "usb":
        line((12, 2.5), (12, 18))
        circle(12, 19.5, 1.8, True)
        line((12, 13), (7, 9.5), (7, 7))
        line((12, 15.5), (17, 12), (17, 9))
        box(5.6, 5, 8.4, 7.4, 0.3, True)
        circle(17, 8, 1.3, True)
    elif name == "chevron":
        line((9, 5.5), (15.5, 12), (9, 18.5))
    elif name == "down":
        line((5.5, 9), (12, 15.5), (18.5, 9))
    elif name == "star":
        pts = []
        for i in range(10):
            ang = -math.pi / 2 + i * math.pi / 5
            r = 9 if i % 2 == 0 else 4
            pts.append((12 + r * math.cos(ang), 12.5 + r * math.sin(ang)))
        d.polygon(P(*pts), fill=c)
    elif name == "box":         # the console's placeholder cart
        box(5, 3, 19, 21, 2)
        box(8, 6, 16, 12, 1)
        line((8, 16), (16, 16))
    elif name == "logo":        # GammaOS's hexagon with its G
        pts = [(12 + 9.6 * math.cos(math.pi / 6 + i * math.pi / 3), 12 + 9.6 * math.sin(math.pi / 6 + i * math.pi / 3)) for i in range(6)]
        d.line(P(*(pts + [pts[0], pts[1]])), fill=c, width=lw, joint="curve")
        d.arc((7.2 * k, 7.2 * k, 16.8 * k, 16.8 * k), 30, 330, fill=c, width=lw)
        line((12.2, 12), (16.5, 12), (16.5, 14.5))
    return im.resize((size, size), Image.LANCZOS)


def icon(name, size, color, weight=1.6):
    return _keep(("icon", name, size, color, weight), lambda: _icon(name, size, color, weight))


def icon_image(name, size, color, weight=1.6):
    return _icon(name, size, color, weight)


def fit(im, w, h):
    """A picture scaled (up or down) to fit w x h, its shape kept."""
    from PIL import Image as _I
    k = min(w / float(im.width), h / float(im.height))
    size = (max(1, int(im.width * k)), max(1, int(im.height * k)))
    return im.resize(size, _I.LANCZOS) if size != im.size else im
