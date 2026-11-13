""".ART, the console's box art file.

  off 0   "GART"
  off 4   u16 version (1)
  off 6   u16 format (0 = RGB565, little-endian)
  off 8   u16 width
  off 10  u16 height
  off 12  width * height RGB565 pixels, rows top to bottom

A picture becomes one the way the console's own PNG import makes it: transparent parts laid over
black, scaled so its larger side is at most 224 (never enlarged), the smaller side rounded to the
nearest pixel, then LANCZOS resampled and stored as RGB565.
"""

import io
import struct

MAGIC = b"GART"
MAX_SIDE = 224
HEADER = 12


class ArtError(Exception):
    pass


def _pil():
    try:
        from PIL import Image
    except ImportError:
        raise ArtError("Pillow is needed for pictures (pip install pillow)")
    return Image


def target_size(w, h, max_side=MAX_SIDE):
    """The size the console draws a w x h picture at."""
    if w <= max_side and h <= max_side:
        return w, h
    if w >= h:
        return max_side, max(1, (h * max_side * 2 + w) // (2 * w))
    return max(1, (w * max_side * 2 + h) // (2 * h)), max_side


def flatten(im):
    """An RGB picture, transparency laid over black."""
    Image = _pil()
    if im.mode == "P":
        im = im.convert("RGBA")
    if im.mode in ("RGBA", "LA", "PA") or (im.mode in ("RGB", "L") and "transparency" in im.info):
        im = im.convert("RGBA")
        black = Image.new("RGBA", im.size, (0, 0, 0, 255))
        im = Image.alpha_composite(black, im)
    if im.mode in ("I;16", "I;16B", "I;16L", "I"):
        im = im.point(lambda v: v / 256).convert("L")
    return im.convert("RGB")


def encode(im, max_side=MAX_SIDE):
    """A Pillow image as .ART bytes."""
    Image = _pil()
    try:
        im.load()
    except Exception as e:
        raise ArtError("The picture could not be read: %s" % e)
    if getattr(im, "n_frames", 1) > 1:
        im.seek(0)
    rgb = flatten(im)
    w, h = target_size(rgb.width, rgb.height, max_side)
    if (w, h) != rgb.size:
        rgb = rgb.resize((w, h), Image.LANCZOS)
    r, g, b = (band.tobytes() for band in rgb.split())
    px = bytearray(w * h * 2)
    for i in range(w * h):
        v = ((r[i] & 0xF8) << 8) | ((g[i] & 0xFC) << 3) | (b[i] >> 3)
        px[2 * i] = v & 0xFF
        px[2 * i + 1] = v >> 8
    return struct.pack("<4sHHHH", MAGIC, 1, 0, w, h) + bytes(px)


def encode_bytes(data, max_side=MAX_SIDE):
    """Picture file bytes (any format Pillow reads) as .ART bytes."""
    Image = _pil()
    try:
        im = Image.open(io.BytesIO(data))
    except Exception as e:
        raise ArtError("The picture could not be read: %s" % e)
    return encode(im, max_side)


def encode_file(path, max_side=MAX_SIDE):
    with open(path, "rb") as f:
        return encode_bytes(f.read(), max_side)


def parse_header(data):
    """(width, height) of .ART bytes, or raises ArtError (the console's checks)."""
    if len(data) < HEADER:
        raise ArtError("too short for a box art file")
    magic, _ver, fmt, w, h = struct.unpack_from("<4sHHHH", data, 0)
    if magic != MAGIC:
        raise ArtError("not a box art file")
    if fmt != 0:
        raise ArtError("an unknown pixel format")
    if not w or not h or w > 4096 or h > 4096:
        raise ArtError("an impossible size")
    if len(data) < HEADER + w * h * 2:
        raise ArtError("cut short")
    return w, h


def decode(data):
    """.ART bytes as an RGB Pillow image."""
    Image = _pil()
    w, h = parse_header(data)
    raw = bytes(data[HEADER:HEADER + w * h * 2])
    return Image.frombuffer("RGB", (w, h), raw, "raw", "BGR;16", 0, 1).copy()


def decode_file(path):
    with open(path, "rb") as f:
        return decode(f.read())


def is_valid(data):
    try:
        parse_header(data)
        return True
    except ArtError:
        return False
