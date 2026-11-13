"""check_lk_image on built images and, when they are on this machine, the real ones."""

import os
import struct

import pytest

from console_fake import make_lk_image
from pico_tool.console import LK_PARTITION_SIZE, check_lk_image

LK_TREE = "/work/kpa/lk-mt6785"
REAL_PICO = os.path.join(LK_TREE, "out/lk_a_kpa_signed.img")
REAL_STOCK = os.path.join(LK_TREE, "tools/konkr/stock/lk.img")
STOCK_SHA = "244b8cab3934cdb3a1b3155e6ddd7ecca6e1be42625a8b30e209bccad552ff17"


def test_pico_image():
    info = check_lk_image(make_lk_image("pico", version="1.2.3 0123abc+"))
    assert info.kind == "gammaos-pico"
    assert info.version == "1.2.3 0123abc+"
    assert info.problems == []


def test_pico_image_padded_to_partition():
    data = make_lk_image("pico", pad_to=LK_PARTITION_SIZE)
    info = check_lk_image(data)
    assert info.kind == "gammaos-pico" and info.problems == [] and info.size == LK_PARTITION_SIZE


def test_old_pico_without_version():
    info = check_lk_image(make_lk_image("pico", version=None))
    assert info.kind == "gammaos-pico" and info.version is None and info.problems == []


def test_stock_image():
    info = check_lk_image(make_lk_image("stock"))
    assert info.kind == "stock"
    assert info.version.startswith("k85v1_64-5cd8ba07fef-")
    assert info.problems == []


@pytest.mark.parametrize("kind", ["apm", "apm-pico"])
def test_other_console_refused(kind):
    info = check_lk_image(make_lk_image(kind))
    assert info.kind == "unknown"
    assert any("another console" in p for p in info.problems)


def test_unsigned_refused():
    info = check_lk_image(make_lk_image("pico", signed=False))
    assert any("not signed" in p for p in info.problems)


def test_no_dtb_refused():
    info = check_lk_image(make_lk_image("pico", dtb=False))
    assert any("lk_main_dtb" in p for p in info.problems)


def test_missing_panel_refused():
    info = check_lk_image(make_lk_image("pico", panel=False))
    assert info.problems


def test_too_large():
    data = make_lk_image("pico", pad_to=LK_PARTITION_SIZE + 512)
    info = check_lk_image(data)
    assert any("larger than" in p for p in info.problems)


def test_empty_and_garbage():
    assert check_lk_image(b"").problems == ["The file is empty."]
    assert check_lk_image(b"\x00" * 100).problems
    info = check_lk_image(b"PK\x03\x04" + b"\x00" * 4000)
    assert info.kind == "unknown" and any("not a MediaTek" in p for p in info.problems)


def test_truncated():
    data = make_lk_image("pico")
    info = check_lk_image(data[:2000])
    assert any("cut short" in p for p in info.problems)


def test_first_part_not_lk():
    data = bytearray(make_lk_image("pico"))
    data[8:40] = b"logo".ljust(32, b"\x00")
    info = check_lk_image(bytes(data))
    assert any("'logo'" in p for p in info.problems)


def test_header_magic_layout():
    data = make_lk_image("stock")
    assert struct.unpack_from("<I", data, 0)[0] == 0x58881688
    assert data[8:10] == b"lk"


def test_unknown_lk_refused():
    data = bytearray(make_lk_image("stock"))
    i = data.find(b"k85v1_64-")
    data[i:i + 8] = b"x6789v1_"
    info = check_lk_image(bytes(data))
    assert info.kind == "unknown" and info.problems


@pytest.mark.skipif(not os.path.isfile(REAL_PICO), reason="GammaOS Pico build not on this machine")
def test_real_pico_build():
    data = open(REAL_PICO, "rb").read()
    info = check_lk_image(data)
    assert info.kind == "gammaos-pico"
    assert info.problems == []
    assert info.size == LK_PARTITION_SIZE
    assert info.version and info.version.split(" ")[0].count(".") >= 1


@pytest.mark.skipif(not os.path.isfile(REAL_STOCK), reason="stock LK not on this machine")
def test_real_stock():
    import hashlib
    data = open(REAL_STOCK, "rb").read()
    assert hashlib.sha256(data).hexdigest() == STOCK_SHA
    info = check_lk_image(data)
    assert info.kind == "stock"
    assert info.version == "k85v1_64-5cd8ba07fef-20260730160432-20260730170410"
    assert info.problems == []


@pytest.mark.skipif(not (os.path.isfile(REAL_STOCK) and os.path.isfile(REAL_PICO)), reason="real images not here")
def test_real_partition_backup_with_leftovers():
    """A partition read after stock was flashed over GammaOS Pico: stock header, old bytes after it."""
    stock = open(REAL_STOCK, "rb").read()
    pico = open(REAL_PICO, "rb").read()
    info = check_lk_image(stock + pico[len(stock):])
    assert info.kind == "stock" and info.problems == []
