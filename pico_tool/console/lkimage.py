"""Checks on an LK image before it is written to a console's lk_a and lk_b, and on a secure
firmware ("tee") image before it is written to tee_a and tee_b.

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

The lk partitions of both consoles are 2 MB. The stock LKs and GammaOS Pico's carry the same chain:
lk, cert1, cert2, lk_main_dtb, cert1, cert2. They are told apart by what the lk part's code says
about itself:

  GammaOS Pico   the fastboot variable name "version-gammaos-pico" sits next to its value
                 "<VERSION> <commit>" (the makefile's PICO_VERSION and PICO_COMMIT), the build tag
                 starts with the console's project ("k85v1_64_kpa-" on the Pocket Advance,
                 "k68v1_64_block-" on the Pocket Block), and the menu's strings say "GammaOS Pico"
  stock (Konkr)  build tag "k85v1_64-<commit>-<date>-<date>" (Pocket Advance) or
                 "m8695_bw01-<commit>-<date>-<date>" (Pocket Block), and no GammaOS strings

Which console an image is for comes from that project, and the image must also carry that
console's display driver (st7123p_hd640_boe on the Pocket Advance, jd9168s_960x720 on the Pocket
Block): the AYANEO Pocket Air Mini uses the same k85v1_64 board name with another panel, and its LK
would leave a Pocket Advance dark. See models.py.

The secure firmware image uses the same part headers; its first part is named "atf" where an LK
image has "lk". That name is what keeps one from ever being written to the other's partitions. The
image is far smaller than its partition (about 160 KB in 5 to 12 MB) and says nothing the tool can
read about which console it is for: that comes from the flash package it is taken from.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .models import ANY_NAME, MODELS, ConsoleModel

PART_MAGIC = 0x58881688
EXT_MAGIC = 0x58891689
HEADER_SIZE = 512
LK_PARTITION_SIZE = max(m.lk_size for m in MODELS)   # lk_a and lk_b, from the stock scatters
TEE_MAX_SIZE = 0x1000000                              # no tee partition is larger than 16 MB
TEE_FIRST_PART = "atf"
PICO_VAR_NAME = b"version-gammaos-pico\x00"

# <project>-<commit>-<commit date>-<build date>, the LK_VER_TAG every LK of these consoles carries
_BUILD_TAG = re.compile(rb"([A-Za-z][A-Za-z0-9_]{1,40})-([0-9a-f]{6,40})-(\d{14})-(\d{14})")
_PICO_VERSION = re.compile(rb"\d+(?:\.\d+)+ [0-9a-f]{4,40}\+?")


@dataclass
class LkImageInfo:
    kind: str                 # "gammaos-pico" | "stock" | "unknown"
    version: Optional[str]    # GammaOS Pico "<version> <commit>", the stock build tag, or None
    size: int
    problems: List[str] = field(default_factory=list)   # empty = OK to write
    model: Optional[ConsoleModel] = None                 # the console it is for, once identified


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


def _model_for_project(project: Optional[bytes], pico: bool) -> Optional[ConsoleModel]:
    if project is None:
        return None
    for m in MODELS:
        if (project == m.pico_project) if pico else (project in m.stock_projects):
            return m
    return None


def check_lk_image(data: bytes, model: Optional[ConsoleModel] = None) -> LkImageInfo:
    """Say what an LK image is, which console it is for, and whether it is safe to write. With
    `model` (the console it would be written to), an image for the other console is refused too."""
    size = len(data)
    info = LkImageInfo(kind="unknown", version=None, size=size)
    if size == 0:
        info.problems.append("The file is empty.")
        return info
    limit = model.lk_size if model is not None else LK_PARTITION_SIZE
    if size > limit:
        info.problems.append(
            "The file is %d bytes, larger than the %d byte bootloader partition." % (size, limit))

    parts, problem = _walk_parts(data)
    if problem:
        info.problems.append(problem[0].upper() + problem[1:] + ".")
    if not parts:
        return info
    names = [p.name for p in parts]
    lk = parts[0]
    if lk.name == TEE_FIRST_PART:
        info.problems.append("This is a secure firmware (tee) image, not a bootloader: it is never written to "
                             "the bootloader's partitions.")
        return info
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
    target = _model_for_project(project, is_pico)

    # an LK for another console: a project no console here has, or not that console's display
    foreign = False
    if is_pico:
        info.kind = "gammaos-pico"
        info.version = _pico_version(payload)
        if project is not None and target is None:
            foreign = True
            info.problems.append(
                "This GammaOS bootloader was built for another console (%s), not the %s."
                % (project.decode("ascii", "replace"), ANY_NAME))
    elif target is not None:
        info.kind = "stock"
        info.version = tag.group(0).decode("ascii")
    else:
        info.problems.append("The image is neither GammaOS Pico nor the stock bootloader of the %s." % ANY_NAME)

    if info.kind != "unknown":
        if project is None:
            info.problems.append("The image carries no build tag, so it cannot be identified safely.")
        if target is not None and target.panel_driver not in payload:
            foreign = True
            info.problems.append(
                "This bootloader is for another console: it lacks the %s's display driver." % target.name)
        if foreign or target is None:
            info.kind, info.version = "unknown", None
        else:
            info.model = target

    if model is not None and info.model is not None and info.model is not model:
        info.problems.append("This bootloader is for the %s, not this %s." % (info.model.name, model.name))
    return info


@dataclass
class TeeImageInfo:
    size: int
    problems: List[str] = field(default_factory=list)   # empty = OK to write to tee_a and tee_b


def check_tee_image(data: bytes) -> TeeImageInfo:
    """Say whether a file is a secure firmware (tee) image: MediaTek part headers with "atf" first.
    A bootloader image, or anything else, is refused."""
    info = TeeImageInfo(size=len(data))
    if not data:
        info.problems.append("The file is empty.")
        return info
    if len(data) > TEE_MAX_SIZE:
        info.problems.append("The file is %d bytes, larger than any secure firmware partition." % len(data))
    parts, problem = _walk_parts(data)
    if not parts:
        if problem and "too small" in problem:
            info.problems.append("The file is too small to be a secure firmware image.")
        elif problem and "cut short" in problem:
            info.problems.append(problem[0].upper() + problem[1:] + ".")
        else:
            info.problems.append("The file is not a secure firmware image (no MediaTek header at the start).")
        return info
    first = parts[0].name
    if first == "lk":
        info.problems.append("This is a bootloader (LK) image, not the secure firmware: it is never written to "
                             "the secure firmware's partitions.")
        return info
    if first != TEE_FIRST_PART:
        info.problems.append("The image starts with a '%s' part, not the 'atf' part the secure firmware has."
                             % first)
        return info
    if problem:
        info.problems.append(problem[0].upper() + problem[1:] + ".")
    return info
