"""The Konkr Pocket Block next to the Konkr Pocket Advance: identified by its product, its LK images
told apart from the Pocket Advance's, and only lk_a ever read for a backup or written."""

import json
import os
import threading

import pytest

from console_fake import FakeConsole, make_lk_image
from pico_tool.console import (BLOCK, KPA, ConsoleError, ConsoleLink, backup_lk, backup_model, check_lk_image,
                               find_consoles, list_backups, read_backup, scan, write_lk)

BLOCK_NEW = make_lk_image("pico", version="1.0.2 e2c4639", seed=5, pad_to=0x200000, console="block")
BLOCK_STOCK = make_lk_image("stock", seed=6, console="block")
KPA_NEW = make_lk_image("pico", version="1.0.2 abc1234", seed=7, pad_to=0x200000)
KPA_STOCK = make_lk_image("stock", seed=8)

LK_TREE = "/work/kpa/lk-mt6785"
REAL_BLOCK_PICO = os.path.join(LK_TREE, "out/lk_a_block_signed.img")
REAL_BLOCK_STOCK = os.path.join(LK_TREE, "tools/block/stock/lk.img")
REAL_KPA_PICO = os.path.join(LK_TREE, "out/lk_a_kpa_signed.img")


def block(bus, **kw):
    c = FakeConsole(serial="BW0112GA10009720", console="block", **kw)
    bus.consoles.append(c)
    (link,) = find_consoles()
    return c, link


def writes(c):
    return [cmd for cmd in c.log if cmd.startswith(("download:", "flash:"))]


# ---- telling the consoles apart ----

def test_identify_block(bus):
    c, link = block(bus, version="1.0.2 e2c4639")
    assert link == ConsoleLink("fastboot", "BW0112GA10009720", "Konkr Pocket Block (GammaOS Pico 1.0.2, fastboot)",
                               "1.0.2 e2c4639", BLOCK)
    assert link.model is BLOCK


def test_kpa_and_block_side_by_side(bus):
    bus.consoles.append(FakeConsole(serial="KPA1"))
    bus.consoles.append(FakeConsole(serial="BLOCK1", console="block"))
    models = {lk.ident: lk.model for lk in find_consoles()}
    assert models == {"KPA1": KPA, "BLOCK1": BLOCK}


def test_stock_block_refused_with_its_package(bus):
    bus.consoles.append(FakeConsole(product="m8695_bw01", console="block"))
    report = scan()
    assert report.links == []
    text = report.problems[0]
    assert "Konkr Pocket Block with the stock bootloader" in text
    assert "gammaos-pico-block-<version>-flash.zip" in text


def test_stock_kpa_names_its_package(bus):
    bus.consoles.append(FakeConsole(product="k85v1_64"))
    text = scan().problems[0]
    assert "stock bootloader" in text and "gammaos-pico-kpa-<version>-flash.zip" in text


def test_unknown_product_names_both_consoles(bus):
    bus.consoles.append(FakeConsole(product="k68v1_64"))
    text = scan().problems[0]
    assert "(k68v1_64)" in text and "Konkr Pocket Advance or Konkr Pocket Block" in text


def test_claim_checks_the_product_of_the_model(bus):
    """A link for a Pocket Advance never opens a Pocket Block with the same serial."""
    c = FakeConsole(serial="SAME", console="block")
    bus.consoles.append(c)
    fake_link = ConsoleLink("fastboot", "SAME", "x", None, KPA)
    with pytest.raises(ConsoleError) as e:
        write_lk(fake_link, {"lk_a": KPA_NEW}, None, None)
    assert "not connected" in str(e.value)
    assert writes(c) == []


# ---- images ----

def test_block_images_identified():
    info = check_lk_image(BLOCK_NEW)
    assert (info.kind, info.version, info.model, info.problems) == ("gammaos-pico", "1.0.2 e2c4639", BLOCK, [])
    info = check_lk_image(BLOCK_STOCK)
    assert info.kind == "stock" and info.model is BLOCK and info.problems == []
    assert info.version == "m8695_bw01-99c2577f4a1-20261014113306-20261014163413"


def test_kpa_images_identified():
    assert check_lk_image(KPA_NEW).model is KPA
    assert check_lk_image(KPA_STOCK).model is KPA


@pytest.mark.parametrize("data,model,other", [(KPA_NEW, BLOCK, KPA), (KPA_STOCK, BLOCK, KPA),
                                              (BLOCK_NEW, KPA, BLOCK), (BLOCK_STOCK, KPA, BLOCK)])
def test_image_for_the_other_console_refused(data, model, other):
    info = check_lk_image(data, model)
    assert info.problems == ["This bootloader is for the %s, not this %s." % (other.name, model.name)]


def test_block_project_with_kpa_panel_refused():
    """A Pocket Block build tag but the Pocket Advance's display driver: refused, never written."""
    data = bytearray(make_lk_image("pico", console="block", panel=False))
    kpa_panel = b"\x00st7123p_hd640_boe_lcm_drv\x00"
    data[600:600 + len(kpa_panel)] = kpa_panel        # inside the lk part's code, chain unchanged
    info = check_lk_image(bytes(data))
    assert info.kind == "unknown" and any("Konkr Pocket Block's display driver" in p for p in info.problems)


def test_pico_for_a_third_console_refused():
    data = bytearray(make_lk_image("pico", console="block"))
    i = data.find(b"k68v1_64_block-")
    data[i:i + 14] = b"k68v1_64_other"
    info = check_lk_image(bytes(data))
    assert info.kind == "unknown" and any("another console (k68v1_64_other)" in p for p in info.problems)


@pytest.mark.parametrize("kind", ["apm", "apm-pico"])
def test_pocket_air_mini_still_refused_on_both(kind):
    for model in (KPA, BLOCK, None):
        info = check_lk_image(make_lk_image(kind), model)
        assert info.kind == "unknown" and info.problems


# ---- backup ----

def test_block_backup_reads_lk_a_only(bus, tmp_path):
    c, link = block(bus)
    c.partitions["lk_a"] = bytearray(make_lk_image("stock", seed=3, pad_to=0x200000, console="block"))
    folder = backup_lk(link, str(tmp_path), threading.Event(), None)
    assert sorted(os.listdir(folder)) == ["backup.json", "lk_a.img"]
    assert not any(cmd.startswith("oem part-read:lk_b") for cmd in c.log)
    assert sum(1 for cmd in c.log if cmd.startswith("oem part-read:lk_a")) == 4   # twice, two 1 MB steps
    record = json.load(open(os.path.join(folder, "backup.json")))
    assert set(record) == {"ident", "date", "model", "lk_a", "tool"}
    assert record["model"] == "block" and record["lk_a"]["kind"] == "stock"
    (entry,) = list_backups(str(tmp_path))
    assert entry["ok"] and entry["model"] == "block" and backup_model(entry) is BLOCK
    assert set(read_backup(folder)) == {"lk_a"}
    assert set(read_backup(folder, BLOCK)) == {"lk_a"}
    with pytest.raises(ConsoleError) as e:
        read_backup(folder, KPA)
    assert "made from a Konkr Pocket Block, not this Konkr Pocket Advance" in str(e.value)


def test_old_backup_without_model_is_a_kpa_backup(bus, tmp_path):
    bus.consoles.append(FakeConsole())
    (link,) = find_consoles()
    folder = backup_lk(link, str(tmp_path), None, None)
    path = os.path.join(folder, "backup.json")
    record = json.load(open(path))
    del record["model"]
    json.dump(record, open(path, "w"))
    (entry,) = list_backups(str(tmp_path))
    assert entry["ok"] and entry["model"] == "kpa" and backup_model(entry) is KPA
    assert set(read_backup(folder, KPA)) == {"lk_a", "lk_b"}
    with pytest.raises(ConsoleError):
        read_backup(folder, BLOCK)


def test_backup_of_an_unknown_model_is_not_used(tmp_path):
    folder = tmp_path / "X" / "2026-01-01_000000"
    folder.mkdir(parents=True)
    (folder / "backup.json").write_text(json.dumps({"ident": "X", "date": "d", "model": "toaster"}))
    (entry,) = list_backups(str(tmp_path))
    assert not entry["ok"] and "does not know" in entry["problems"][0]
    with pytest.raises(ConsoleError):
        read_backup(str(folder))


# ---- writing ----

def test_block_update_writes_lk_a_only(bus):
    c, link = block(bus)
    old_b = bytes(c.partitions["lk_b"])
    assert write_lk(link, {"lk_a": BLOCK_NEW}, None, None) == {"lk_a": "verified"}
    assert c.flashed == ["lk_a"]
    assert bytes(c.partitions["lk_a"]) == BLOCK_NEW and bytes(c.partitions["lk_b"]) == old_b
    assert not any("lk_b" in cmd for cmd in c.log)


def test_block_stock_restore(bus):
    c, link = block(bus)
    assert write_lk(link, {"lk_a": BLOCK_STOCK}, None, None) == {"lk_a": "verified"}
    assert bytes(c.partitions["lk_a"][:len(BLOCK_STOCK)]) == BLOCK_STOCK


def test_block_never_writes_lk_b(bus):
    c, link = block(bus)
    with pytest.raises(ConsoleError) as e:
        write_lk(link, {"lk_a": BLOCK_NEW, "lk_b": BLOCK_NEW}, None, None)
    assert "written to lk_a only, not lk_b" in str(e.value)
    with pytest.raises(ConsoleError):
        write_lk(link, {"lk_b": BLOCK_NEW}, None, None)
    assert writes(c) == []


@pytest.mark.parametrize("image", [KPA_NEW, KPA_STOCK])
def test_kpa_image_refused_on_block(bus, image):
    c, link = block(bus)
    with pytest.raises(ConsoleError) as e:
        write_lk(link, {"lk_a": image}, None, None)
    assert "for the Konkr Pocket Advance, not this Konkr Pocket Block" in str(e.value)
    assert "Nothing was written" in str(e.value)
    assert writes(c) == []


@pytest.mark.parametrize("image", [BLOCK_NEW, BLOCK_STOCK])
def test_block_image_refused_on_kpa(bus, image):
    c = FakeConsole()
    bus.consoles.append(c)
    (link,) = find_consoles()
    with pytest.raises(ConsoleError) as e:
        write_lk(link, {"lk_a": image, "lk_b": image}, None, None)
    assert "for the Konkr Pocket Block, not this Konkr Pocket Advance" in str(e.value)
    assert writes(c) == []


def test_block_failed_readback_wording(bus):
    c, link = block(bus)
    c.corrupt_flash["lk_a"] = -1
    with pytest.raises(ConsoleError) as e:
        write_lk(link, {"lk_a": BLOCK_NEW}, None, None)
    text = str(e.value)
    assert "lk_a did not read back correctly" in text
    assert "lk_b" not in text
    assert "Do not turn the console off" in text and "gammaos-pico-block-<version>-flash.zip" in text


def test_block_flash_fail_wording(bus):
    c, link = block(bus)
    c.fail["flash:lk_a"] = "flash write failure"
    with pytest.raises(ConsoleError) as e:
        write_lk(link, {"lk_a": BLOCK_NEW}, None, None)
    text = str(e.value)
    assert "Writing lk_a did not finish" in text and "lk_b" not in text


# ---- the real images, when they are on this machine ----

@pytest.mark.skipif(not os.path.isfile(REAL_BLOCK_PICO), reason="GammaOS Pico Block build not on this machine")
def test_real_block_pico():
    info = check_lk_image(open(REAL_BLOCK_PICO, "rb").read(), BLOCK)
    assert info.kind == "gammaos-pico" and info.model is BLOCK and info.problems == []
    assert check_lk_image(open(REAL_BLOCK_PICO, "rb").read(), KPA).problems


@pytest.mark.skipif(not os.path.isfile(REAL_BLOCK_STOCK), reason="stock Block LK not on this machine")
def test_real_block_stock():
    info = check_lk_image(open(REAL_BLOCK_STOCK, "rb").read(), BLOCK)
    assert info.kind == "stock" and info.model is BLOCK and info.problems == []
    assert info.version == "m8695_bw01-99c2577f4a1-20261014113306-20261014163413"


@pytest.mark.skipif(not os.path.isfile(REAL_KPA_PICO), reason="GammaOS Pico KPA build not on this machine")
def test_real_kpa_pico_refused_on_block():
    info = check_lk_image(open(REAL_KPA_PICO, "rb").read(), BLOCK)
    assert info.model is KPA and info.problems
