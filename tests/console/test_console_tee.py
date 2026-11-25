"""The secure firmware (tee_a, tee_b) next to the bootloader: written first, read back where the
console's LK can (GammaOS Pico 1.2.0 and later), backed up when it can be read, and never mixed up
with an LK image."""

import json
import os

import pytest

from console_fake import FakeConsole, make_lk_image, make_tee_image
from pico_tool.console import (BLOCK, KPA, ConsoleError, backup_lk, backup_text, check_lk_image,
                               check_tee_image, find_consoles, list_backups, read_backup, write_lk)
from pico_tool.console.models import results_lines

LK = {"kpa": make_lk_image("pico", version="1.2.0 fedcba9", seed=9, pad_to=0x200000),
      "block": make_lk_image("pico", version="1.2.0 e2c4639", seed=5, pad_to=0x200000, console="block")}
STOCK_LK = {"kpa": make_lk_image("stock", seed=4), "block": make_lk_image("stock", seed=6, console="block")}
TEE = make_tee_image("gammaos", seed=3)
STOCK_TEE = make_tee_image("stock", seed=2)

V11 = "1.1.2 abc1234"      # its part-read knows lk_a and lk_b only
V12 = "1.2.0 abc1234"      # its part-read also reads tee_a and tee_b


def plugged(bus, console="kpa", version=V12, **kw):
    c = FakeConsole(console=console, version=version, **kw)
    bus.consoles.append(c)
    (link,) = find_consoles()
    return c, link


def everything(console="kpa", lk=None, tee=TEE):
    lk = LK[console] if lk is None else lk
    return {"lk_a": lk, "lk_b": lk, "tee_a": tee, "tee_b": tee}


def writes(c):
    return [cmd for cmd in c.log if cmd.startswith(("download:", "flash:"))]


# ---- the image check ----

def test_tee_image_accepted():
    assert check_tee_image(TEE).problems == [] and check_tee_image(STOCK_TEE).problems == []
    assert TEE[:4] == bytes.fromhex("88168858") and TEE[8:12] == b"atf\x00"
    # a copy read back from the console: the image, then the rest of its partition
    assert check_tee_image(make_tee_image("stock", pad_to=0x500000)).problems == []


def test_lk_image_is_not_a_tee_image():
    (problem,) = check_tee_image(LK["kpa"]).problems
    assert "bootloader (LK) image, not the secure firmware" in problem


def test_tee_image_is_not_an_lk_image():
    for model in (KPA, BLOCK, None):
        info = check_lk_image(TEE, model)
        assert info.kind == "unknown"
        assert any("secure firmware (tee) image, not a bootloader" in p for p in info.problems)


@pytest.mark.parametrize("data,needle", [
    (b"", "empty"),
    (b"hello", "too small"),
    (bytes(4096), "not a secure firmware image"),
    (make_tee_image(first="tee"), "starts with a 'tee' part"),
    (TEE[:2000], "cut short"),
    (TEE + bytes(0x1000000), "larger than any secure firmware partition"),
])
def test_bad_tee_images(data, needle):
    assert any(needle in p for p in check_tee_image(data).problems)


# ---- a 1.2.0 package on both consoles ----

@pytest.mark.parametrize("console", ["kpa", "block"])
def test_tee_first_then_lk_all_verified(bus, console):
    c, link = plugged(bus, console)
    msgs = []
    result = write_lk(link, everything(console), None, lambda f, m: msgs.append((f, m)))
    assert result == {"tee_b": "verified", "tee_a": "verified", "lk_b": "verified", "lk_a": "verified"}
    # the secure firmware before the bootloader; in each, the slot the console runs from (a) last
    assert list(result) == c.flashed == ["tee_b", "tee_a", "lk_b", "lk_a"]
    for name in ("tee_a", "tee_b"):
        assert bytes(c.partitions[name][:len(TEE)]) == TEE
    for name in ("lk_a", "lk_b"):
        assert bytes(c.partitions[name]) == LK[console]
    fracs = [f for f, _ in msgs]
    assert fracs == sorted(fracs) and fracs[-1] == 1.0
    texts = [m for _, m in msgs]
    for needle in ("Sending the secure firmware for tee_b.", "Writing tee_b.", "Checking tee_b.",
                   "Sending the bootloader for lk_b.", "Writing lk_a.", "Checking lk_a."):
        assert needle in texts
    assert texts[-1] == "The secure firmware and the bootloader were written and checked."
    assert results_lines(result) == ["tee_b: written and checked", "tee_a: written and checked",
                                     "lk_b: written and checked", "lk_a: written and checked"]


def test_running_slot_b_goes_last_in_both(bus):
    c, link = plugged(bus, current_slot="b")
    write_lk(link, everything(), None, None)
    assert c.flashed == ["tee_a", "tee_b", "lk_a", "lk_b"]


def test_only_the_image_length_is_read_back(bus):
    """The image is far smaller than its partition: one 1 MB step covers it, the rest of the 5 MB
    partition is neither read nor compared."""
    c, link = plugged(bus)
    tail = bytes(c.partitions["tee_a"][len(TEE):])
    write_lk(link, everything(), None, None)
    assert bytes(c.partitions["tee_a"][len(TEE):]) == tail
    for name in ("tee_a", "tee_b"):
        assert [cmd for cmd in c.log if cmd.startswith("oem part-read:%s:" % name)] == ["oem part-read:%s:0x0" % name]


# ---- an older package: the LK only ----

@pytest.mark.parametrize("version", [V11, V12])
def test_old_package_writes_the_lk_only(bus, version):
    c, link = plugged(bus, version=version)
    tee_before = {n: bytes(c.partitions[n]) for n in ("tee_a", "tee_b")}
    msgs = []
    result = write_lk(link, {"lk_a": LK["kpa"], "lk_b": LK["kpa"]}, None, lambda f, m: msgs.append(m))
    assert result == {"lk_b": "verified", "lk_a": "verified"}
    assert c.flashed == ["lk_b", "lk_a"]
    assert {n: bytes(c.partitions[n]) for n in ("tee_a", "tee_b")} == tee_before
    # nothing asked of, or said about, the secure firmware
    assert not any("tee" in cmd for cmd in c.log)
    assert not any("tee" in m or "secure firmware" in m for m in msgs)
    assert msgs[-1] == "The bootloader was written and checked."


# ---- an LK older than 1.2.0 cannot read the secure firmware back ----

@pytest.mark.parametrize("console", ["kpa", "block"])
def test_old_lk_writes_the_tee_unchecked(bus, console):
    c, link = plugged(bus, console, version=V11)
    msgs = []
    result = write_lk(link, everything(console), None, lambda f, m: msgs.append(m))
    assert result == {"tee_b": "written", "tee_a": "written", "lk_b": "verified", "lk_a": "verified"}
    assert c.flashed == ["tee_b", "tee_a", "lk_b", "lk_a"]
    assert bytes(c.partitions["tee_a"][:len(TEE)]) == TEE and bytes(c.partitions["tee_b"][:len(TEE)]) == TEE
    # asked once whether it can read the tee, refused, and never asked to read it after a write
    assert [cmd for cmd in c.log if cmd.startswith("oem part-read:tee")] == ["oem part-read:tee_a"]
    assert not any(m.startswith("Checking tee") for m in msgs)
    assert msgs[-1] == ("The secure firmware and the bootloader were written. lk_b and lk_a were read back and "
                        "checked. The console's bootloader is too old to read tee_b and tee_a back, so they "
                        "were not checked; the console accepted every write.")
    assert results_lines(result)[0] == ("tee_b: written, not checked (the console's bootloader cannot read it "
                                        "back)")
    assert results_lines(result)[-1] == "lk_a: written and checked"


def test_another_fail_reply_to_the_tee_question_is_an_error(bus):
    """Only "only lk_a and lk_b" (or no part-read at all) means it cannot be read back."""
    c, link = plugged(bus)
    c.fail["oem part-read:tee_a"] = "part-read: read failed"
    with pytest.raises(ConsoleError) as e:
        write_lk(link, everything(), None, None)
    assert "part-read: read failed" in str(e.value)
    assert writes(c) == []


# ---- back to stock ----

@pytest.mark.parametrize("console", ["kpa", "block"])
def test_back_to_stock_writes_the_stock_tee_with_the_stock_lk(bus, console):
    c, link = plugged(bus, console)
    write_lk(link, everything(console), None, None)
    del c.flashed[:]
    stock = STOCK_LK[console]
    result = write_lk(link, everything(console, lk=stock, tee=STOCK_TEE), None, None)
    assert result == {"tee_b": "verified", "tee_a": "verified", "lk_b": "verified", "lk_a": "verified"}
    assert c.flashed == ["tee_b", "tee_a", "lk_b", "lk_a"]
    for name in ("tee_a", "tee_b"):
        assert bytes(c.partitions[name][:len(STOCK_TEE)]) == STOCK_TEE
    for name in ("lk_a", "lk_b"):
        assert bytes(c.partitions[name][:len(stock)]) == stock


# ---- a swapped or invalid image is refused before anything is written ----

@pytest.mark.parametrize("images,name,needle", [
    ({"lk_a": LK["kpa"], "lk_b": LK["kpa"], "tee_a": TEE, "tee_b": LK["kpa"]}, "tee_b",
     "bootloader (LK) image, not the secure firmware"),
    ({"lk_a": TEE, "lk_b": LK["kpa"], "tee_a": TEE, "tee_b": TEE}, "lk_a",
     "secure firmware (tee) image, not a bootloader"),
    ({"lk_a": TEE, "lk_b": TEE, "tee_a": LK["kpa"], "tee_b": LK["kpa"]}, "lk_a",
     "secure firmware (tee) image, not a bootloader"),
    ({"lk_a": LK["kpa"], "lk_b": LK["kpa"], "tee_a": b"not an image", "tee_b": TEE}, "tee_a", "too small"),
    ({"lk_a": LK["kpa"], "lk_b": LK["kpa"], "tee_a": TEE, "tee_b": bytes(8192)}, "tee_b",
     "not a secure firmware image"),
    ({"lk_a": LK["kpa"], "lk_b": LK["kpa"], "tee_a": TEE[:3000], "tee_b": TEE}, "tee_a", "cut short"),
])
def test_wrong_image_stops_before_writing(bus, images, name, needle):
    c, link = plugged(bus)
    before = {n: bytes(p) for n, p in c.partitions.items()}
    with pytest.raises(ConsoleError) as e:
        write_lk(link, images, None, None)
    text = str(e.value)
    assert "The image for %s cannot be written" % name in text and needle in text
    assert text.endswith("Nothing was written.")
    assert writes(c) == [] and c.log == [cmd for cmd in c.log if cmd.startswith("getvar:")]
    assert {n: bytes(p) for n, p in c.partitions.items()} == before


def test_tee_image_larger_than_its_partition(bus):
    c, link = plugged(bus)
    c.vars["partition-size:tee_a"] = "1000"
    with pytest.raises(ConsoleError) as e:
        write_lk(link, everything(), None, None)
    assert "larger than the console's tee_a partition" in str(e.value) and writes(c) == []


# ---- a failure in the middle: what was written is told as it is ----

def test_first_tee_copy_fails_and_nothing_the_console_runs_from_was_touched(bus):
    c, link = plugged(bus)
    before = {n: bytes(p) for n, p in c.partitions.items()}
    c.fail["flash:tee_b"] = "flash write failure"
    with pytest.raises(ConsoleError) as e:
        write_lk(link, everything(), None, None)
    text = str(e.value)
    assert text.startswith("Writing tee_b did not finish (the console said: flash write failure); ")
    assert "tee_a and lk_a were not touched, so the console still starts from them as before" in text
    assert c.flashed == []
    assert {n: bytes(p) for n, p in c.partitions.items()} == before


def test_running_tee_copy_fails(bus):
    c, link = plugged(bus)
    c.fail["flash:tee_a"] = "flash write failure"
    with pytest.raises(ConsoleError) as e:
        write_lk(link, everything(), None, None)
    text = str(e.value)
    assert "Writing tee_a did not finish" in text
    assert "tee_b was written and checked. lk_a and lk_b were not touched. Do not turn the console off" in text
    assert "still starts" not in text
    assert c.flashed == ["tee_b"]
    assert bytes(c.partitions["lk_a"]) != LK["kpa"] and bytes(c.partitions["lk_b"]) != LK["kpa"]


def test_first_lk_copy_fails_after_the_tee(bus):
    """The secure firmware is on and checked, the LK the console runs from is the old one: it
    still starts as before."""
    c, link = plugged(bus)
    old_a = bytes(c.partitions["lk_a"])
    c.corrupt_flash["lk_b"] = -1
    with pytest.raises(ConsoleError) as e:
        write_lk(link, everything(), None, None)
    text = str(e.value)
    assert "The copy written to lk_b did not read back correctly; " in text
    assert "tee_b and tee_a were written and checked. lk_a was not touched, so the console still starts from it " \
           "as before. Try the update again." in text
    assert c.flashed == ["tee_b", "tee_a", "lk_b", "lk_b"]
    assert bytes(c.partitions["lk_a"]) == old_a
    assert bytes(c.partitions["tee_a"][:len(TEE)]) == TEE and bytes(c.partitions["tee_b"][:len(TEE)]) == TEE


def test_last_copy_fails(bus):
    c, link = plugged(bus)
    c.fail["flash:lk_a"] = "flash write failure"
    with pytest.raises(ConsoleError) as e:
        write_lk(link, everything(), None, None)
    text = str(e.value)
    assert "Writing lk_a did not finish" in text
    assert "tee_b, tee_a and lk_b were written and checked. Do not turn the console off" in text
    assert "not touched" not in text and "still starts" not in text
    assert c.flashed == ["tee_b", "tee_a", "lk_b"]


def test_old_lk_fails_on_the_first_lk_copy_and_says_the_tee_was_not_checked(bus):
    """The tee could not be read back, so the console is not promised to start as before."""
    c, link = plugged(bus, version=V11)
    c.fail["flash:lk_b"] = "flash write failure"
    with pytest.raises(ConsoleError) as e:
        write_lk(link, everything(), None, None)
    text = str(e.value)
    assert "tee_b and tee_a were written. lk_a was not touched. Do not turn the console off" in text
    assert "written and checked" not in text and "still starts" not in text


def test_unplugged_while_the_tee_is_sent(bus):
    c, link = plugged(bus)
    c.unplug_on.add("download:")
    with pytest.raises(ConsoleError) as e:
        write_lk(link, everything(), None, None)
    text = str(e.value)
    assert "Writing tee_b did not finish (the console was disconnected)" in text
    assert c.flashed == []


def test_tee_read_back_never_matches(bus):
    c, link = plugged(bus)
    c.corrupt_flash["tee_b"] = -1
    with pytest.raises(ConsoleError) as e:
        write_lk(link, everything(), None, None)
    assert "The copy written to tee_b did not read back correctly" in str(e.value)
    assert c.flashed == ["tee_b", "tee_b"] and "flash:lk_a" not in c.log


def test_block_failure_in_the_middle(bus):
    c, link = plugged(bus, "block")
    c.fail["flash:lk_b"] = "flash write failure"
    with pytest.raises(ConsoleError) as e:
        write_lk(link, everything("block"), None, None)
    text = str(e.value)
    assert "Writing lk_b did not finish" in text
    assert "tee_b and tee_a were written and checked. lk_a was not touched, so the console still starts" in text
    assert c.flashed == ["tee_b", "tee_a"]


# ---- backups ----

@pytest.mark.parametrize("console", ["kpa", "block"])
def test_backup_holds_the_tee_when_the_lk_reads_it(bus, tmp_path, console):
    c, link = plugged(bus, console)
    msgs = []
    folder = backup_lk(link, str(tmp_path), None, lambda f, m: msgs.append(m))
    assert sorted(os.listdir(folder)) == ["backup.json", "lk_a.img", "lk_b.img", "tee_a.img", "tee_b.img"]
    record = json.load(open(os.path.join(folder, "backup.json")))
    assert set(record) == {"ident", "date", "model", "lk_a", "lk_b", "tee_a", "tee_b", "tool"}
    for name in ("tee_a", "tee_b"):
        assert open(os.path.join(folder, name + ".img"), "rb").read() == bytes(c.partitions[name])
        assert record[name]["kind"] == "secure-firmware" and record[name]["size"] == 0x500000
    assert writes(c) == []
    assert msgs[-1] == "Backup saved in %s." % folder
    assert backup_text(folder) == ("The console's bootloader (lk_a and lk_b) and secure firmware (tee_a and "
                                   "tee_b) are saved in %s" % folder)
    (entry,) = list_backups(str(tmp_path))
    assert entry["ok"]
    images = read_backup(folder, link.model)
    assert set(images) == {"lk_a", "lk_b", "tee_a", "tee_b"}
    # written over, then the backup put back: the secure firmware first, all of it checked
    write_lk(link, everything(console), None, None)
    del c.flashed[:]
    assert set(write_lk(link, images, None, None).values()) == {"verified"}
    assert c.flashed == ["tee_b", "tee_a", "lk_b", "lk_a"]
    for name in images:
        assert bytes(c.partitions[name]) == images[name]


def test_backup_without_the_tee_on_an_older_lk(bus, tmp_path):
    c, link = plugged(bus, version=V11)
    msgs = []
    folder = backup_lk(link, str(tmp_path), None, lambda f, m: msgs.append(m))
    assert sorted(os.listdir(folder)) == ["backup.json", "lk_a.img", "lk_b.img"]
    record = json.load(open(os.path.join(folder, "backup.json")))
    assert set(record) == {"ident", "date", "model", "lk_a", "lk_b", "tool"}
    note = ("The secure firmware was not backed up: this console's bootloader is older than GammaOS Pico 1.2.0 "
            "and cannot read it over USB. The stock secure firmware is in every flash package from 1.2.0 on.")
    assert msgs[-1] == "Backup saved in %s. %s" % (folder, note)
    assert backup_text(folder) == "The console's bootloader (lk_a and lk_b) is saved in %s\n%s" % (folder, note)
    (entry,) = list_backups(str(tmp_path))
    assert entry["ok"]
    assert set(read_backup(folder, KPA)) == {"lk_a", "lk_b"}


def test_damaged_tee_in_a_backup_is_flagged(bus, tmp_path):
    c, link = plugged(bus)
    folder = backup_lk(link, str(tmp_path), None, None)
    with open(os.path.join(folder, "tee_b.img"), "r+b") as f:
        f.seek(1000)
        f.write(b"\x00\x01\x02")
    (entry,) = list_backups(str(tmp_path))
    assert not entry["ok"] and "tee_b.img does not match its checksum" in entry["problems"][0]
    with pytest.raises(ConsoleError):
        read_backup(folder)


def test_two_reads_of_the_tee_must_agree(bus, tmp_path):
    c, link = plugged(bus)
    c.corrupt_reads["tee_a"] = 2        # the question whether it can be read, then the first read
    with pytest.raises(ConsoleError) as e:
        backup_lk(link, str(tmp_path), None, None)
    assert "Two reads of tee_a" in str(e.value)
    assert list_backups(str(tmp_path)) == []
