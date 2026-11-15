"""backup_lk, list_backups and read_backup against the in-memory console."""

import hashlib
import json
import os
import threading

import pytest

from console_fake import FakeConsole, make_lk_image
from pico_tool.console import (ConsoleCancelled, ConsoleError, ConsoleTooOld, backup_lk,
                               default_backup_root, find_consoles, has_backup, list_backups, read_backup)


def console_with_images(bus, **kw):
    c = FakeConsole(**kw)
    c.partitions["lk_a"] = bytearray(make_lk_image("pico", seed=2, pad_to=0x200000))
    c.partitions["lk_b"] = bytearray(make_lk_image("stock", seed=3, pad_to=0x200000))
    bus.consoles.append(c)
    (link,) = find_consoles()
    return c, link


def test_backup_round_trip(bus, tmp_path):
    c, link = console_with_images(bus)
    marks = []
    folder = backup_lk(link, str(tmp_path), threading.Event(), lambda f, m: marks.append((f, m)))
    assert os.path.dirname(folder) == os.path.join(str(tmp_path), link.ident)
    assert sorted(os.listdir(folder)) == ["backup.json", "lk_a.img", "lk_b.img"]
    record = json.load(open(os.path.join(folder, "backup.json")))
    assert set(record) == {"ident", "date", "model", "lk_a", "lk_b", "tool"}
    assert record["model"] == "kpa"
    assert record["ident"] == link.ident
    assert record["date"].endswith("Z")
    for name, kind in (("lk_a", "gammaos-pico"), ("lk_b", "stock")):
        data = open(os.path.join(folder, name + ".img"), "rb").read()
        assert data == bytes(c.partitions[name])
        assert record[name]["sha256"] == hashlib.sha256(data).hexdigest()
        assert record[name]["size"] == 0x200000
        assert record[name]["kind"] == kind
    assert record["lk_a"]["version"] == "1.0.0 abc1234"
    # every partition read twice, each in two 1 MB steps
    assert sum(1 for cmd in c.log if cmd.startswith("oem part-read:")) == 8
    assert [m for m in c.log if m.startswith(("download", "flash"))] == []
    fracs = [f for f, _ in marks if f is not None]
    assert fracs == sorted(fracs) and fracs[-1] == 1.0

    listed = list_backups(str(tmp_path))
    assert len(listed) == 1 and listed[0]["ok"] and listed[0]["path"] == folder
    assert has_backup(str(tmp_path), link.ident)
    assert not has_backup(str(tmp_path), "SOMEONEELSE")
    images = read_backup(folder)
    assert images["lk_a"] == bytes(c.partitions["lk_a"])


def test_two_backups_newest_first(bus, tmp_path):
    c, link = console_with_images(bus)
    first = backup_lk(link, str(tmp_path), None, None)
    second = backup_lk(link, str(tmp_path), None, None)
    assert first != second
    listed = list_backups(str(tmp_path))
    assert [b["path"] for b in listed][0] in (first, second)
    assert len(listed) == 2


def test_tampered_backup_is_flagged(bus, tmp_path):
    c, link = console_with_images(bus)
    folder = backup_lk(link, str(tmp_path), None, None)
    with open(os.path.join(folder, "lk_b.img"), "r+b") as f:
        f.seek(1000)
        f.write(b"\x00\x01\x02")
    (entry,) = list_backups(str(tmp_path))
    assert not entry["ok"] and "checksum" in entry["problems"][0]
    assert not has_backup(str(tmp_path), link.ident)
    with pytest.raises(ConsoleError) as e:
        read_backup(folder)
    assert "checksum" in str(e.value)


def test_missing_file_is_flagged(bus, tmp_path):
    c, link = console_with_images(bus)
    folder = backup_lk(link, str(tmp_path), None, None)
    os.remove(os.path.join(folder, "lk_a.img"))
    (entry,) = list_backups(str(tmp_path))
    assert not entry["ok"]
    with pytest.raises(ConsoleError):
        read_backup(folder)


def test_list_ignores_junk(tmp_path):
    (tmp_path / "X" / "2026-01-01_000000.partial").mkdir(parents=True)
    (tmp_path / "X" / "notes").mkdir(parents=True)
    (tmp_path / "X" / "2026-01-02_000000").mkdir(parents=True)
    (tmp_path / "X" / "2026-01-02_000000" / "backup.json").write_text("{not json")
    (tmp_path / "file.txt").write_text("hi")
    assert list_backups(str(tmp_path)) == []
    assert list_backups(str(tmp_path / "does-not-exist")) == []


def test_old_bootloader_cannot_be_backed_up(bus, tmp_path):
    c, link = console_with_images(bus, version=None, part_read=False)
    with pytest.raises(ConsoleTooOld) as e:
        backup_lk(link, str(tmp_path), None, None)
    assert "too old" in str(e.value)
    assert list_backups(str(tmp_path)) == []


def test_readback_differs_between_reads(bus, tmp_path):
    c, link = console_with_images(bus)
    c.corrupt_reads["lk_b"] = 1      # the first read of lk_b is damaged, the second is not
    with pytest.raises(ConsoleError) as e:
        backup_lk(link, str(tmp_path), None, None)
    assert "Two reads of lk_b" in str(e.value)
    assert list_backups(str(tmp_path)) == []
    assert not os.path.exists(os.path.join(str(tmp_path), link.ident))   # nothing half written left behind


def test_timeout_during_backup(bus, tmp_path):
    c, link = console_with_images(bus)
    c.silent.add("oem part-read:lk_b:0x100000")
    with pytest.raises(ConsoleError) as e:
        backup_lk(link, str(tmp_path), None, None)
    assert "stopped answering" in str(e.value)
    assert list_backups(str(tmp_path)) == []


def test_unplugged_during_backup(bus, tmp_path):
    c, link = console_with_images(bus)
    c.unplug_on.add("upload")
    with pytest.raises(ConsoleError) as e:
        backup_lk(link, str(tmp_path), None, None)
    assert "disconnected" in str(e.value)


def test_fail_reply_during_backup(bus, tmp_path):
    c, link = console_with_images(bus)
    c.fail["oem part-read:lk_a"] = "part-read: read failed"
    with pytest.raises(ConsoleError) as e:
        backup_lk(link, str(tmp_path), None, None)
    assert "part-read: read failed" in str(e.value)


def test_wrong_partition_size_reported(bus, tmp_path):
    c, link = console_with_images(bus)
    c.vars["partition-size:lk_a"] = "300000"
    with pytest.raises(ConsoleError) as e:
        backup_lk(link, str(tmp_path), None, None)
    assert "partition is" in str(e.value)


def test_cancel_backup(bus, tmp_path):
    c, link = console_with_images(bus)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ConsoleCancelled):
        backup_lk(link, str(tmp_path), cancel, None)
    assert list_backups(str(tmp_path)) == []


def test_unwritable_destination(bus, tmp_path):
    c, link = console_with_images(bus)
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(ConsoleError) as e:
        backup_lk(link, str(blocker), None, None)
    assert "could not be saved" in str(e.value)


def test_default_backup_root():
    root = default_backup_root()
    assert root.endswith("GammaOS Pico Backups")
    assert os.path.isabs(root)
