"""A console whose LK has no `upload` (the Pocket Block since GammaOS Pico 1.1.0): a write is checked
by the checksums of what was written, and a backup is built from checksums, the images in hand and,
for what is left, the bytes sent as text."""

import os
import zlib

import pytest

from console_fake import NO_UPLOAD, FakeConsole, make_lk_image, make_tee_image
from pico_tool.console import BLOCK, ConsoleError, backup_lk, find_consoles, list_backups, read_backup, write_lk
from pico_tool.console import device
from pico_tool.console.backup import slow_read_text

LK_SIZE, TEE_SIZE = 0x200000, 0x500000
NEW_LK = make_lk_image("pico", version="1.2.0 e2c4639", seed=5, pad_to=LK_SIZE, console="block")
OLD_LK = make_lk_image("pico", version="1.1.2 e2c4639", seed=8, filler=300000, console="block")
STOCK_LK = make_lk_image("stock", seed=6, filler=200000, console="block")
NEW_TEE = make_tee_image("gammaos", seed=3, filler=150000)
STOCK_TEE = make_tee_image("stock", seed=2, filler=150000)
PACKAGE = {"lk_a": [NEW_LK, STOCK_LK], "lk_b": [NEW_LK, STOCK_LK],
           "tee_a": [NEW_TEE, STOCK_TEE], "tee_b": [NEW_TEE, STOCK_TEE]}
EVERYTHING = {"lk_a": NEW_LK, "lk_b": NEW_LK, "tee_a": NEW_TEE, "tee_b": NEW_TEE}


def block(bus, version="1.2.0 e2c4639", **kw):
    """A Pocket Block as it leaves the factory, then GammaOS Pico: the stock LK in lk_a, lk_b never
    written, the stock secure firmware in both tee copies."""
    c = FakeConsole(serial="BW0112GA10009720", console="block", version=version, **kw)
    c.partitions["lk_a"] = bytearray(STOCK_LK.ljust(LK_SIZE, b"\x00"))
    c.partitions["lk_b"] = bytearray(b"\xff" * LK_SIZE)
    c.partitions["tee_a"] = bytearray(STOCK_TEE.ljust(TEE_SIZE, b"\x00"))
    c.partitions["tee_b"] = bytearray(STOCK_TEE.ljust(TEE_SIZE, b"\x00"))
    bus.consoles.append(c)
    (link,) = find_consoles()
    return c, link


def count(c, prefix):
    return sum(1 for cmd in c.log if cmd.startswith(prefix))


def test_the_fake_refuses_upload_like_the_real_one(bus):
    c, link = block(bus)
    assert not c.upload and NO_UPLOAD == "no upload on this console: oem stage-hex / stage-crc"
    with device.open_console(link) as s:
        s.run("oem part-read:tee_a")
        with pytest.raises(device.FastbootFail) as e:
            s.upload()
        assert e.value.text == NO_UPLOAD
        crcs = device.stage_crcs(s, 0x100000)
        staged = bytes(c.partitions["tee_a"][:0x100000])
        assert len(crcs) == 256 and crcs[0] == zlib.crc32(staged[:0x1000])
        assert s.run("oem stage-crc:100000").infos == ["%08x " % zlib.crc32(staged)]
        assert s.run("oem stage-hex:10:1a").infos == ["000010:" + staged[16:40].hex(), "000028:" + staged[40:42].hex()]
        assert device.stage_hex(s, 0x1234, 5000) == staged[0x1234:0x1234 + 5000]


def test_block_1_2_0_flash_end_to_end(bus, tmp_path):
    """The backup before the first write, then the secure firmware and the bootloader: nothing is
    uploaded, the backup costs next to nothing by stage-hex, and all four copies are checked."""
    c, link = block(bus)
    c.partitions["lk_a"][0x180000:0x180010] = b"left by the plant"[:16]      # something no image explains
    before = {n: bytes(p) for n, p in c.partitions.items()}
    msgs = []
    folder = backup_lk(link, str(tmp_path), None, lambda f, m: msgs.append((f, m)), candidates=PACKAGE)
    assert c.hex_bytes == 0x1000                    # the one chunk that had to come as text
    assert count(c, "upload") == 1                  # asked once, refused, never again
    for name, data in before.items():
        assert open(os.path.join(folder, name + ".img"), "rb").read() == data
    (entry,) = list_backups(str(tmp_path))
    assert entry["ok"] and entry["lk_a"]["kind"] == "stock" and entry["lk_b"]["kind"] == "unknown"
    fracs = [f for f, _ in msgs]
    assert fracs == sorted(fracs) and fracs[-1] == 1.0
    assert any("This console sends 4 KB of it a few bytes at a time" in m for _, m in msgs)
    backup_commands = len(c.log)

    del c.log[:]
    c.hex_bytes = 0
    result = write_lk(link, EVERYTHING, None, None, backup_root=str(tmp_path))
    assert result == {"tee_b": "verified", "tee_a": "verified", "lk_b": "verified", "lk_a": "verified"}
    assert c.flashed == ["tee_b", "tee_a", "lk_b", "lk_a"]
    for name in ("lk_a", "lk_b"):
        assert bytes(c.partitions[name]) == NEW_LK
    for name in ("tee_a", "tee_b"):
        assert bytes(c.partitions[name][:len(NEW_TEE)]) == NEW_TEE
    assert count(c, "upload") == 1
    # the tee image ends inside a chunk: only its part of that chunk came as text, twice (two copies)
    assert c.hex_bytes == 2 * (len(NEW_TEE) % 0x1000)
    # the traffic of the whole flash stays small
    assert backup_commands < 120 and len(c.log) < 60


def test_restore_of_that_backup(bus, tmp_path):
    c, link = block(bus)
    before = {n: bytes(p) for n, p in c.partitions.items()}
    folder = backup_lk(link, str(tmp_path), None, None, candidates=PACKAGE)
    assert c.hex_bytes == 0
    write_lk(link, EVERYTHING, None, None)
    images = read_backup(folder, BLOCK)
    assert set(images) == {"lk_a", "tee_a", "tee_b"}            # lk_b held no bootloader
    assert set(write_lk(link, images, None, None).values()) == {"verified"}
    for name in images:
        assert bytes(c.partitions[name]) == before[name]


def test_mismatch_found_by_checksum_is_rewritten_once(bus):
    c, link = block(bus)
    c.corrupt_flash["lk_b"] = 1
    assert set(write_lk(link, EVERYTHING, None, None).values()) == {"verified"}
    assert c.flashed == ["tee_b", "tee_a", "lk_b", "lk_b", "lk_a"]


def test_mismatch_found_by_checksum_is_reported(bus):
    c, link = block(bus)
    old_a = bytes(c.partitions["lk_a"])
    c.corrupt_flash["lk_b"] = -1
    with pytest.raises(ConsoleError) as e:
        write_lk(link, EVERYTHING, None, None)
    text = str(e.value)
    assert "The copy written to lk_b did not read back correctly; " in text
    assert "tee_b and tee_a were written and checked. lk_a was not touched, so the console still starts" in text
    assert bytes(c.partitions["lk_a"]) == old_a and c.hex_bytes == 2 * (len(NEW_TEE) % 0x1000)


def test_image_ending_inside_a_chunk(bus):
    """What follows the image on the console is not the tool's business: the image's part of its
    last chunk is fetched and compared, the rest of that chunk is not."""
    c, link = block(bus)
    c.partitions["lk_a"] = bytearray(b"\xa5" * LK_SIZE)
    c.partitions["lk_b"] = bytearray(b"\x5a" * LK_SIZE)
    tail = len(STOCK_LK) % 0x1000
    assert tail
    assert write_lk(link, {"lk_a": STOCK_LK, "lk_b": STOCK_LK}, None, None) == {"lk_b": "verified",
                                                                                 "lk_a": "verified"}
    assert c.hex_bytes == 2 * tail
    assert bytes(c.partitions["lk_a"][len(STOCK_LK):]) == b"\xa5" * (LK_SIZE - len(STOCK_LK))
    with device.open_console(link) as s:
        assert device.partition_matches(s, "lk_a", STOCK_LK)
        c.partitions["lk_a"][len(STOCK_LK)] ^= 0xFF              # the first byte after the image
        assert device.partition_matches(s, "lk_a", STOCK_LK)
        c.partitions["lk_a"][len(STOCK_LK) - 1] ^= 0xFF          # the image's last byte
        assert not device.partition_matches(s, "lk_a", STOCK_LK)
        c.partitions["lk_a"][len(STOCK_LK) - 1] ^= 0xFF
        c.partitions["lk_a"][0x2345] ^= 0x01                     # a full chunk: found by its checksum
        sent = c.hex_bytes
        assert not device.partition_matches(s, "lk_a", STOCK_LK)
        assert c.hex_bytes == sent


def test_backup_with_no_package_comes_the_slow_way(bus, tmp_path):
    c, link = block(bus, version="1.1.2 e2c4639")           # 1.1: no upload, and no reading of the tee
    c.partitions["lk_a"] = bytearray(OLD_LK.ljust(LK_SIZE, b"\x00"))
    msgs = []
    folder = backup_lk(link, str(tmp_path), None, lambda f, m: msgs.append((f, m)))
    assert sorted(os.listdir(folder)) == ["backup.json", "lk_a.img", "lk_b.img"]
    assert open(os.path.join(folder, "lk_a.img"), "rb").read() == bytes(c.partitions["lk_a"])
    assert open(os.path.join(folder, "lk_b.img"), "rb").read() == b"\xff" * LK_SIZE
    # the image's chunks came as text; the zeros after it and the blank lk_b cost nothing
    assert c.hex_bytes == -(-len(OLD_LK) // 0x1000) * 0x1000
    assert count(c, "upload") == 1
    texts = [m for _, m in msgs]
    note = "This console sends %d KB of it a few bytes at a time: this takes a few minutes." % (c.hex_bytes >> 10)
    assert any(m.startswith("Reading lk_a (") and m.endswith(note) for m in texts)
    assert not any("a few bytes at a time" in m for m in texts if "lk_b" in m)
    fracs = [f for f, _ in msgs]
    assert fracs == sorted(fracs) and fracs[-1] == 1.0
    (entry,) = list_backups(str(tmp_path))
    assert entry["ok"] and entry["lk_a"]["version"] == "1.1.2 e2c4639"
    # the same console with the package's images in hand, none of which it holds: the same cost
    sent = c.hex_bytes
    backup_lk(link, str(tmp_path), None, None, candidates=PACKAGE)
    assert c.hex_bytes == 2 * sent


def test_slow_read_note_past_4_mb():
    assert slow_read_text(300 << 10) == ("This console sends 300 KB of it a few bytes at a time: this takes a few "
                                         "minutes.")
    assert slow_read_text(5 << 20) == ("This console sends 5 MB of it a few bytes at a time (more than 4 MB): this "
                                       "takes a long time.")


def test_second_asking_of_the_checksums_must_agree(bus, tmp_path):
    c, link = block(bus)
    # lk_a: staged once for the upload it refuses, two steps scanned, then the second asking: its
    # first step comes back damaged there
    calls = []
    real = c._part_read

    def part_read(arg):
        calls.append(arg)
        if arg.startswith("lk_a") and sum(1 for a in calls if a.startswith("lk_a")) == 4:
            c.corrupt_reads["lk_a"] = 1
        real(arg)
    c._part_read = part_read
    with pytest.raises(ConsoleError) as e:
        backup_lk(link, str(tmp_path), None, None, candidates=PACKAGE)
    assert "Two reads of lk_a gave different data" in str(e.value)
    assert list_backups(str(tmp_path)) == []


def test_text_bytes_are_checked_against_their_checksum(bus, tmp_path):
    c, link = block(bus, version="1.1.2 e2c4639")
    c.partitions["lk_a"] = bytearray(OLD_LK.ljust(LK_SIZE, b"\x00"))
    c.corrupt_hex = 1                       # one damaged answer: asked again, and then right
    folder = backup_lk(link, str(tmp_path), None, None)
    assert open(os.path.join(folder, "lk_a.img"), "rb").read() == bytes(c.partitions["lk_a"])
    c.corrupt_hex = 2                       # damaged twice: given up, nothing saved
    with pytest.raises(ConsoleError) as e:
        backup_lk(link, str(tmp_path), None, None)
    assert "do not match their checksum" in str(e.value)
    assert len(list_backups(str(tmp_path))) == 1


@pytest.mark.parametrize("console,version", [("kpa", "1.2.0 abc1234"), ("block", "1.0.2 e2c4639")])
def test_a_console_with_upload_still_uses_it(bus, tmp_path, console, version):
    c = FakeConsole(console=console, version=version, tee_read=True)
    bus.consoles.append(c)
    (link,) = find_consoles()
    assert c.upload
    lk = make_lk_image("pico", version="1.2.0 abc1234", seed=9, pad_to=LK_SIZE, console=console)
    backup_lk(link, str(tmp_path), None, None, candidates={"lk_a": [lk]})
    result = write_lk(link, {"lk_a": lk, "lk_b": lk, "tee_a": NEW_TEE, "tee_b": NEW_TEE}, None, None)
    assert set(result.values()) == {"verified"}
    assert not any(cmd.startswith("oem stage-") for cmd in c.log) and c.hex_bytes == 0
    # backup: (2 MB + 2 MB + 5 MB + 5 MB) twice in 1 MB steps; the write: two steps per 2 MB LK
    # image, one per secure firmware image
    assert count(c, "upload") == 28 + 6


def test_only_that_fail_text_switches_the_upload_off(bus, tmp_path):
    c = FakeConsole()
    bus.consoles.append(c)
    (link,) = find_consoles()
    c.fail["upload"] = "something else went wrong"
    with pytest.raises(ConsoleError) as e:
        backup_lk(link, str(tmp_path), None, None)
    assert "something else went wrong" in str(e.value)
    assert not any(cmd.startswith("oem stage-") for cmd in c.log)
