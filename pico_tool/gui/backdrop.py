"""The window's backdrop: the "Current" theme's deep navy field with its current of light.

Drawn with Pillow from the console's own recipe: a field of blues running across and a little down,
a mist at the upper right, a dark edge at the foot and a shade from the left, then the current, a
translucent ribbon of strata with fine threads and bright crests along it. The panels the window's
pages sit on are drawn into the same picture (rounded, with a soft shadow), so the widgets inside
them sit on a plain colour. The accent's matrix turns the whole picture to its colour.
"""

import math

from PIL import Image, ImageDraw, ImageFilter

from . import theme

AIR = ((0.0, (0x03, 0x09, 0x14)), (0.36, (0x08, 0x1D, 0x43)), (0.68, (0x0C, 0x3C, 0x7D)), (1.0, (0x08, 0x1B, 0x4C)))
MIST = ((0.0, (0x26, 0x64, 0xCC), 0.88), (0.6, (0x16, 0x55, 0xB0), 0.34), (1.0, (0x10, 0x24, 0x43), 0.0))
EDGE = ((0.0, (0x08, 0x18, 0x2E), 0.0), (0.75, (0x05, 0x13, 0x2C), 0.17), (1.0, (0x02, 0x09, 0x14), 0.80))
LEFT = ((0.0, 0.37), (0.49, 0.04), (1.0, 0.0))
LEFT_RGB = (0x05, 0x10, 0x20)
READ = ((0.0, 0.70), (0.52, 0.62), (1.0, 0.30))
READ_RGB = (0x06, 0x0D, 0x19)
CURRENT = ((0x11, 0x41, 0xA5), (0x2A, 0x65, 0xBD), (0x72, 0xC5, 0xED), (0x4E, 0x80, 0xE1), (0xBD, 0xDF, 0xFF))
CRESTS = ((-0.62, 0.39, 1.2), (0.32, 0.26, 0.7), (1.015, 0.60, 1.5), (1.03, 0.15, 3.1))
STRATA = 38
THREADS = 64


def _stops(st, p):
    if p <= st[0][0]:
        i = 0
    elif p >= st[-1][0]:
        i = len(st) - 2
    else:
        i = 0
        while i < len(st) - 2 and p > st[i + 1][0]:
            i += 1
    t = (p - st[i][0]) / (st[i + 1][0] - st[i][0])
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return i, t


def _col(st, p):
    i, t = _stops(st, p)
    a, b = st[i][1], st[i + 1][1]
    return tuple(a[k] + (b[k] - a[k]) * t for k in range(3))


def _cola(st, p):
    i, t = _stops(st, p)
    a, b = st[i][1], st[i + 1][1]
    return tuple(a[k] + (b[k] - a[k]) * t for k in range(3)), st[i][2] + (st[i + 1][2] - st[i][2]) * t


def _alpha(st, p):
    i, t = _stops([(o, (a, 0, 0)) for o, a in st], p)
    return st[i][1] + (st[i + 1][1] - st[i][1]) * t


def field(w, h):
    """The still part at a small size (it is smooth), as an RGB image w x h."""
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        v = (y + 0.5) / h
        ec, ea = _cola(EDGE, v)
        dv2 = (v - 0.42) ** 2
        for x in range(w):
            u = (x + 0.5) / w
            p = (u + (v - 0.4) * 0.3) / 1.09
            f = _col(AIR, p)
            mc, ma = _cola(MIST, ((u - 0.81) ** 2 + dv2) / 0.62)
            r = [f[k] + (mc[k] - f[k]) * ma for k in range(3)]
            r = [r[k] + (ec[k] - r[k]) * ea for k in range(3)]
            px[x, y] = (int(r[0] + 0.5), int(r[1] + 0.5), int(r[2] + 0.5))
    return img


def shade(W, H, stops, rgb):
    """A colour laid from the left with the stops' strength across (the console's column shades)."""
    row = Image.new("L", (256, 1))
    row.putdata([int(255 * _alpha(stops, (i + 0.5) / 256.0) + 0.5) for i in range(256)])
    layer = Image.new("RGBA", (W, H), tuple(rgb) + (0,))
    layer.putalpha(row.resize((W, H), Image.BILINEAR))
    return layer


def _wave_columns(W, H, ph, step):
    xs = list(range(0, W + step, step))
    A, M, Ps, Pc = [], [], [], []
    for x in xs:
        t = min(x, W) / float(W)
        A.append((0.71 - 0.30 * t + 0.086 * math.sin(2.85 * t + ph * 0.13 - 0.70) + 0.047 * math.sin(5.7 * t - ph * 0.11 + 1.6)) * H)
        M.append((0.07 + 0.15 * math.sin(3.8 * t - 0.42 + ph * 0.12)) * H)
        Ps.append(0.026 * H * math.sin(7.2 * t + ph * 0.10))
        Pc.append(0.026 * H * math.sin(7.2 * t + ph * 0.10 + math.pi / 2))
    return xs, A, M, Ps, Pc


def _curve(cols, e):
    xs, A, M, Ps, Pc = cols
    se, ce = math.sin(1.8 * e), math.cos(1.8 * e)
    return [(xs[i], A[i] + e * M[i] + Ps[i] * ce + Pc[i] * se) for i in range(len(xs))]


def current(W, H, ph=0.0, ss=2):
    """The current as an RGBA layer W x H (drawn at ss times the size, then smoothed down)."""
    SW, SH = W * ss, H * ss
    layer = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    step = max(2, SW // 360)
    cols = _wave_columns(SW, SH, ph, step)
    curves = [_curve(cols, -0.62 + n * 1.64 / STRATA) for n in range(STRATA + 1)]
    for n in range(STRATA):
        t = n / float(STRATA)
        sp = math.sin(math.pi * t)
        op = (0.15 + 0.49 * sp * sp ** 2 * (sp ** 8)) * 0.83
        ci = 0 if t < 0.35 else 1 if t < 0.57 else 2 if t < 0.81 else 3
        c = CURRENT[ci]
        poly = curves[n] + list(reversed(curves[n + 1]))
        d.polygon(poly, fill=(c[0], c[1], c[2], int(op * 255)))
    lines = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    dl = ImageDraw.Draw(lines)
    gs = SH / 720.0
    for n in range(THREADS + 4):
        if n < THREADS:
            v = n / float(THREADS - 1)
            sv = math.sin(math.pi * v)
            e = -0.58 + v * 1.56
            op = (0.05 + 0.25 * sv ** 3) * 0.88
            th = (0.48 if n % 4 else 0.85) * gs
            c = CURRENT[2] if n % 5 else CURRENT[4]
        else:
            e, op, th = CRESTS[n - THREADS]
            th *= gs
            c = CURRENT[4]
        if op * th < 0.03:
            continue
        width = max(1, int(round(th)))
        alpha = op * min(1.0, th / width)
        dl.line(_curve(cols, e), fill=(c[0], c[1], c[2], int(255 * alpha)), width=width, joint="curve")
    layer = Image.alpha_composite(layer, lines)
    if ss > 1:
        layer = layer.resize((W, H), Image.LANCZOS)
    return layer


def render(W, H, accent_name="Blue", reading=False, small=None):
    """The backdrop at W x H for an accent (an RGB image): the field, the current over it, then the
    shade from the left (and the reading shade of the console's library and settings)."""
    W, H = max(64, int(W)), max(64, int(H))
    fw = small or max(80, min(320, W // 5))
    fh = max(50, int(fw * H / float(W)))
    out = field(fw, fh).resize((W, H), Image.BICUBIC).convert("RGBA")
    out = Image.alpha_composite(out, current(W, H))
    out = Image.alpha_composite(out, shade(W, H, LEFT, LEFT_RGB))
    if reading:
        out = Image.alpha_composite(out, shade(W, H, READ, READ_RGB))
    out = out.convert("RGB")
    m = theme.matrix(accent_name)
    if m != theme.ACCENTS[0][2]:
        out = out.convert("RGB", tuple(v / 1024.0 for v in (m[0], m[1], m[2])) + (0,)
                          + tuple(v / 1024.0 for v in (m[3], m[4], m[5])) + (0,)
                          + tuple(v / 1024.0 for v in (m[6], m[7], m[8])) + (0,))
    return out


def rounded_mask(w, h, r, ss=4):
    m = Image.new("L", (w * ss, h * ss), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, w * ss - 1, h * ss - 1), radius=r * ss, fill=255)
    return m.resize((w, h), Image.LANCZOS)


def panels(base, rects, fill, edge, shadow, radius):
    """The panels drawn onto a copy of the backdrop: a soft shadow, the fill, a hairline edge."""
    img = base.copy().convert("RGBA")
    if not rects:
        return img.convert("RGB")
    W, H = img.size
    sh = Image.new("L", (W, H), 0)
    ds = ImageDraw.Draw(sh)
    off = max(2, radius // 3)
    for (x, y, w, h) in rects:
        ds.rounded_rectangle((x, y + off, x + w, y + h + off), radius=radius, fill=150)
    sh = sh.filter(ImageFilter.GaussianBlur(max(4, radius)))
    shadow_layer = Image.new("RGBA", (W, H), theme.hex_rgb(shadow) + (0,))
    shadow_layer.putalpha(sh)
    img = Image.alpha_composite(img, shadow_layer)
    fr, er = theme.hex_rgb(fill), theme.hex_rgb(edge)
    for (x, y, w, h) in rects:
        x, y, w, h = int(x), int(y), int(w), int(h)
        if w < 4 or h < 4:
            continue
        outer = rounded_mask(w, h, radius)
        inner = Image.new("L", (w, h), 0)
        inner.paste(rounded_mask(w - 2, h - 2, max(1, radius - 1)), (1, 1))
        edge_layer = Image.new("RGBA", (w, h), er + (255,))
        edge_layer.putalpha(outer)
        img.alpha_composite(edge_layer, (x, y))
        fill_layer = Image.new("RGBA", (w, h), fr + (255,))
        fill_layer.putalpha(inner)
        img.alpha_composite(fill_layer, (x, y))
    return img.convert("RGB")
