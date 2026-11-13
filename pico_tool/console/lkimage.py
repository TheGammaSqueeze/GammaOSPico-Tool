"""Checks on a Konkr Pocket Advance LK image before it is written to lk_a or lk_b.

A MediaTek LK image is a chain of parts. Each part starts with a 512 byte header:

    0x00  u32  magic 0x58881688
    0x04  u32  size of the part's data (after the header)
    0x08  32 b name ("lk", "cert1", "cert2", "lk_main_dtb", ...), NUL padded
    0x28  u32  load address (0xffffffff = default)
    0x2c  u32  mode
    0x30  u32  extended magic 0x58891689 (when the extended fields below are present)
    0x34  u32  header size (512)
    0x38  u32  header version
    0x3c  u32  image type
    0x40  u32  1 on the last part of the chain
    0x44  u32  alignment of the next part (16)

The Konkr's lk partitions are 2 MB. Both the stock LK and GammaOS Pico's carry the same chain:
lk, cert1, cert2, lk_main_dtb, cert1, cert2. They are told apart by what the lk part's code says
about itself:

  GammaOS Pico   the fastboot variable name "version-gammaos-pico" sits next to its value
                 "<VERSION> <commit>" (the makefile's PICO_VERSION and PICO_COMMIT), the build tag
                 starts "k85v1_64_kpa-", and the menu's strings say "GammaOS Pico"
  stock (Konkr)  build tag "k85v1_64-<commit>-<date>-<date>" and no GammaOS strings

Both must carry the Konkr's display driver (st7123p_hd640_boe): the AYANEO Pocket Air Mini uses
the same k85v1_64 board name with another panel, and its LK would leave this console dark.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

PART_MAGIC = 0x58881688
EXT_MAGIC = 0x58891689
HEADER_SIZE = 512
LK_PARTITION_SIZE = 0x200000          # lk_a and lk_b, from the stock scatter
KPA_PANEL_DRIVER = b"st7123p_hd640_boe_lcm_drv"
PICO_VAR_NAME = b"version-gammaos-pico\x00"
PICO_PROJECT = b"k85v1_64_kpa"
STOCK_PROJECT = b"k85v1_64"

# <project>-<commit>-<commit date>-<build date>, the LK_VER_TAG every build of this tree carries
_BUILD_TAG = re.compile(rb"(k85v1_64[A-Za-z0-9_]*)-([0-9a-f]{6,40})-(\d{14})-(\d{14})")
_PICO_VERSION = re.compile(rb"\d+(?:\.\d+)+ [0-9a-f]{4,40}\+?")


@dataclass
class LkImageInfo:
    kind: str                 # "gammaos-pico" | "stock" | "unknown"
    version: Optional[str]    # GammaOS Pico "<version> <commit>", the stock build tag, or None
    size: int
    problems: List[str] = field(default_factory=list)   # empty = OK to write


@dataclass
class _Part:
    name: str
    offset: int        # of the header
    data_size: int
    header_size: int


def _walk_parts(data: bytes) -> Tuple[List[_Part], Optional[str]]:
    """Follow the part chain from offset 0. Returns the parts and a problem, if any."""
    parts: List[_Part] = []
    off = 0
    while True:
        if off + HEADER_SIZE > len(data):
            if not parts:
                return parts, "the file is too small to be a bootloader image"
            return parts, None   # the chain ended without a last-part flag at the end of the data
        magic, dsize = struct.unpack_from("<II", data, off)
        if magic != PART_MAGIC:
            if not parts:
                return parts, "the file is not a MediaTek bootloader image (no LK header at the start)"
            return parts, None   # padding or old data after the chain
        name = data[off + 8:off + 40].split(b"\x00", 1)[0].decode("ascii", "replace")
        ext_magic, hsize, _hver, _itype, last, align = struct.unpack_from("<IIIIII", data, off + 48)
        if ext_magic != EXT_MAGIC:
            hsize, last, align = HEADER_SIZE, 0, 16
        if hsize < 64 or hsize > 0x10000:
            return parts, "the part '%s' has a damaged header" % name
        end = off + hsize + dsize
        if end > len(data):
            return parts, "the image is cut short: its '%s' part runs past the end of the file" % name
        parts.append(_Part(name, off, dsize, hsize))
        if len(parts) > 32:
            return parts, "the image has too many parts to be a bootloader"
        if last == 1:
            return parts, None
        if align == 0 or align & (align - 1):
            align = 16
        off = (end + align - 1) // align * align


def _pico_version(payload: bytes) -> Optional[str]:
    """The string right after the "version-gammaos-pico" name in the LK's read only data."""
    at = payload.find(PICO_VAR_NAME)
    if at < 0:
        return None
    window = payload[at + len(PICO_VAR_NAME):at + len(PICO_VAR_NAME) + 96]
    for piece in window.split(b"\x00"):
        if not piece:
            continue
        if _PICO_VERSION.fullmatch(piece):
            return piece.decode("ascii")
        break   # the first string after the name is the value; anything else means no match
    return None


def check_lk_image(data: bytes) -> LkImageInfo:
    """Say what an LK image is and whether it is safe to write to this console's lk_a / lk_b."""
    size = len(data)
    info = LkImageInfo(kind="unknown", version=None, size=size)
    if size == 0:
        info.problems.append("The file is empty.")
        return info
    if size > LK_PARTITION_SIZE:
        info.problems.append(
            "The file is %d bytes, larger than the %d byte bootloader partition." % (size, LK_PARTITION_SIZE))

    parts, problem = _walk_parts(data)
    if problem:
        info.problems.append(problem[0].upper() + problem[1:] + ".")
    if not parts:
        return info
    names = [p.name for p in parts]
    lk = parts[0]
    if lk.name != "lk":
        info.problems.append("The image starts with a '%s' part, not the 'lk' part a bootloader has." % lk.name)
        return info
    payload = data[lk.offset + lk.header_size:lk.offset + lk.header_size + lk.data_size]

    # signed: cert1 and cert2 straight after lk; and the device tree the LK starts with
    if names[1:3] != ["cert1", "cert2"]:
        info.problems.append(
            "The image is not signed (no cert1 and cert2 after the lk part), so the console would not start it.")
    if "lk_main_dtb" not in names:
        info.problems.append("The image has no lk_main_dtb part, so the console would not start it.")

    tag = _BUILD_TAG.search(payload)
    project = tag.group(1) if tag else None
    is_pico = PICO_VAR_NAME in payload or b"GammaOS Pico" in payload
    has_panel = KPA_PANEL_DRIVER in payload

    # an LK for another console: wrong project for GammaOS Pico, or not this console's display
    foreign = (not has_panel) or (is_pico and project is not None and project != PICO_PROJECT)
    if is_pico:
        info.kind = "gammaos-pico"
        info.version = _pico_version(payload)
        if project is not None and project != PICO_PROJECT:
            info.problems.append(
                "This GammaOS bootloader was built for another console (%s), not the Konkr Pocket Advance."
                % project.decode("ascii", "replace"))
    elif project == STOCK_PROJECT:
        info.kind = "stock"
        info.version = tag.group(0).decode("ascii")
    else:
        info.problems.append("The image is neither GammaOS Pico nor the Konkr Pocket Advance's stock bootloader.")

    if info.kind != "unknown":
        if not has_panel:
            info.problems.append(
                "This bootloader is for another console: it lacks the Konkr Pocket Advance's display driver.")
        if project is None:
            info.problems.append("The image carries no build tag, so it cannot be identified safely.")
        if foreign:
            info.kind, info.version = "unknown", None
    return info
