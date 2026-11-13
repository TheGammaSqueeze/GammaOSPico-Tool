"""write_lk against the in-memory console: order, checks, read back and every failure path."""

import threading

import pytest

from console_fake import FakeConsole, make_lk_image
from pico_tool.console import (ConsoleCancelled, ConsoleError, ConsoleTooOld, backup_lk, find_consoles,
                               write_lk)

NEW = make_lk_image("pico", version="1.0.1 fedcba9", seed=9, pad_to=0x200000)
STOCK = make_lk_image("stock", seed=4)


def plugged(bus, **kw):
    c = FakeConsole(**kw)
    bus.consoles.append(c)
    (link,) = find_consoles()
    return c, link


def writes(c):
    return [cmd for cmd in c.log if cmd.startswith(("download:", "flash:"))]


def test_update_both_copies(bus, tmp_path):
    c, link = plugged(bus)
    marks = []
    result = write_lk(link, {"lk_a": NEW, "lk_b": NEW}, threading.Event(), lambda f, m: marks.append((f, m)))
    assert result == {"lk_a": "verified", "lk_b": "verified"}
    assert bytes(c.partitions["lk_a"]) == NEW and bytes(c.partitions["lk_b"]) == NEW
    # the copy the console is not running from (current-slot a) goes first
    assert c.flashed == ["lk_b", "lk_a"]
    fracs = [f for f, _ in marks if f is not None]
    assert fracs == sorted(fracs) and fracs[-1] == 1.0
    assert all(f is not None for f, _ in marks)


def test_running_slot_b_writes_a_first(bus):
    c, link = plugged(bus, current_slot="b")
    write_lk(link, {"lk_a": NEW, "lk_b": NEW}, None, None)
    assert c.flashed == ["lk_a", "lk_b"]


def test_restore_smaller_stock_image(bus):
    c, link = plugged(bus)
    before_tail = bytes(c.partitions["lk_a"][len(STOCK):])
    result = write_lk(link, {"lk_a": STOCK, "lk_b": STOCK}, None, None)
    assert result == {"lk_a": "verified", "lk_b": "verified"}
    assert bytes(c.partitions["lk_a"][:len(STOCK)]) == STOCK
    assert bytes(c.partitions["lk_a"][len(STOCK):]) == before_tail   # only the image length is written
    # the check reads only what covers the image: one 1 MB step per copy for this small image
    assert sum(1 for cmd in c.log if cmd.startswith("oem part-read:lk_a:")) == 1


def test_one_copy_only(bus):
    c, link = plugged(bus)
    assert write_lk(link, {"lk_b": NEW}, None, None) == {"lk_b": "verified"}
    assert c.flashed == ["lk_b"]


@pytest.mark.parametrize("images", [{"boot_a": NEW}, {"lk_a": NEW, "preloader": NEW}, {"lk": NEW}, {}])
def test_only_lk_partitions(bus, images):
    c, link = plugged(bus)
    with pytest.raises(ConsoleError):
        write_lk(link, images, None, None)
    assert writes(c) == []


@pytest.mark.parametrize("bad", [
    make_lk_image("apm"),
    make_lk_image("pico", signed=False),
    make_lk_image("pico", pad_to=0x200000 + 16),
    b"",
    b"hello",
])
def test_bad_image_stops_before_writing(bus, bad):
    c, link = plugged(bus)
    with pytest.raises(ConsoleError) as e:
        write_lk(link, {"lk_a": NEW, "lk_b": bad}, None, None)
    assert "lk_b" in str(e.value) and "Nothing was written" in str(e.value)
    assert writes(c) == [] and c.log == [cmd for cmd in c.log if cmd.startswith("getvar:")]


def test_cancel_before_writing(bus):
    c, link = plugged(bus)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ConsoleCancelled):
        write_lk(link, {"lk_a": NEW, "lk_b": NEW}, cancel, None)
    assert writes(c) == []


def test_cancel_after_first_byte_is_ignored(bus):
    c, link = plugged(bus)
    cancel = threading.Event()

    def progress(frac, msg):
        if msg.startswith("Writing"):
            cancel.set()

    assert write_lk(link, {"lk_a": NEW, "lk_b": NEW}, cancel, progress) == {"lk_a": "verified", "lk_b": "verified"}


def test_bad_readback_once_is_rewritten(bus):
    c, link = plugged(bus)
    c.corrupt_flash["lk_b"] = 1
    assert write_lk(link, {"lk_a": NEW, "lk_b": NEW}, None, None) == {"lk_a": "verified", "lk_b": "verified"}
    assert c.flashed == ["lk_b", "lk_b", "lk_a"]


def test_first_copy_never_verifies_leaves_running_copy_alone(bus):
    c, link = plugged(bus)
    old_a = bytes(c.partitions["lk_a"])
    c.corrupt_flash["lk_b"] = -1
    with pytest.raises(ConsoleError) as e:
        write_lk(link, {"lk_a": NEW, "lk_b": NEW}, None, None)
    text = str(e.value)
    assert "lk_b did not read back correctly" in text
    assert "lk_a was not touched, so the console still starts from it as before" in text
    assert bytes(c.partitions["lk_a"]) == old_a
    assert "flash:lk_a" not in c.log


def test_second_copy_never_verifies_names_the_good_one(bus):
    c, link = plugged(bus)
    c.corrupt_flash["lk_a"] = -1
    with pytest.raises(ConsoleError) as e:
        write_lk(link, {"lk_a": NEW, "lk_b": NEW}, None, None)
    text = str(e.value)
    assert "lk_a did not read back correctly" in text
    assert "lk_b was written and checked." in text
    assert "Do not turn the console off" in text
    assert "SP Flash Tool" in text
    assert "still starts from lk_b" not in text


def test_corrupt_readback_transfer(bus):
    """The data on the eMMC is right but the read back is damaged every time."""
    c, link = plugged(bus)
    c.corrupt_reads["lk_b"] = -1
    with pytest.raises(ConsoleError) as e:
        write_lk(link, {"lk_a": NEW, "lk_b": NEW}, None, None)
    assert "lk_b did not read back correctly" in str(e.value)


def test_verify_off(bus):
    c, link = plugged(bus)
    assert write_lk(link, {"lk_a": NEW, "lk_b": NEW}, None, None, verify=False) == {"lk_a": "written", "lk_b": "written"}
    assert not any(cmd.startswith("upload") for cmd in c.log)


def test_flash_fail_reply(bus):
    c, link = plugged(bus)
    c.fail["flash:lk_b"] = "flash write failure"
    with pytest.raises(ConsoleError) as e:
        write_lk(link, {"lk_a": NEW, "lk_b": NEW}, None, None)
    text = str(e.value)
    assert "Writing lk_b did not finish" in text and "flash write failure" in text
    assert "lk_a was not touched" in text


def test_flash_fail_on_second_copy(bus):
    c, link = plugged(bus)
    c.fail["flash:lk_a"] = "flash write failure"
    with pytest.raises(ConsoleError) as e:
        write_lk(link, {"lk_a": NEW, "lk_b": NEW}, None, None)
    assert "lk_b was written and checked." in str(e.value)
    assert "still starts" not in str(e.value)


def test_low_battery(bus):
    c, link = plugged(bus)
    c.low_battery = True
    with pytest.raises(ConsoleError) as e:
        write_lk(link, {"lk_a": NEW, "lk_b": NEW}, None, None)
    assert "battery is too low" in str(e.value)
    assert c.flashed == []


def test_timeout_during_flash(bus):
    c, link = plugged(bus)
    c.silent.add("flash:lk_b")
    with pytest.raises(ConsoleError) as e:
        write_lk(link, {"lk_a": NEW, "lk_b": NEW}, None, None)
    text = str(e.value)
    assert "stopped answering" in text and "lk_b" in text


def test_unplugged_during_download(bus):
    c, link = plugged(bus)
    c.unplug_on.add("download:")
    with pytest.raises(ConsoleError) as e:
        write_lk(link, {"lk_a": NEW, "lk_b": NEW}, None, None)
    assert "disconnected" in str(e.value)


def test_busy_console(bus):
    c, link = plugged(bus)
    c.open_error = "busy"
    with pytest.raises(ConsoleError) as e:
        write_lk(link, {"lk_a": NEW, "lk_b": NEW}, None, None)
    assert "another program" in str(e.value)


def test_old_bootloader_without_backup_is_refused(bus, tmp_path):
    c, link = plugged(bus, version=None, part_read=False)
    with pytest.raises(ConsoleTooOld) as e:
        write_lk(link, {"lk_a": NEW, "lk_b": NEW}, None, None, backup_root=str(tmp_path))
    assert "no backup" in str(e.value)
    assert writes(c) == []


def test_old_bootloader_with_backup_is_written_unchecked(bus, tmp_path):
    c, link = plugged(bus)
    backup_lk(link, str(tmp_path), None, None)          # taken while it could still be read
    c.part_read = False                                  # now the console runs an older LK
    msgs = []
    result = write_lk(link, {"lk_a": NEW, "lk_b": NEW}, None, lambda f, m: msgs.append(m),
                      backup_root=str(tmp_path))
    assert result == {"lk_a": "written", "lk_b": "written"}
    assert bytes(c.partitions["lk_a"]) == NEW
    assert "not checked" in msgs[-1]


def test_image_larger_than_partition_reported_by_console(bus):
    c, link = plugged(bus)
    c.vars["partition-size:lk_a"] = "100000"
    with pytest.raises(ConsoleError) as e:
        write_lk(link, {"lk_a": NEW}, None, None)
    assert "larger than the console's lk_a partition" in str(e.value)
    assert writes(c) == []


def test_running_copy_alone_fails(bus):
    """Only the running copy is written and it never verifies: no claim that it still starts."""
    c, link = plugged(bus)
    c.corrupt_flash["lk_a"] = -1
    with pytest.raises(ConsoleError) as e:
        write_lk(link, {"lk_a": NEW}, None, None)
    text = str(e.value)
    assert "lk_a did not read back correctly" in text
    assert "lk_b was not touched. Do not turn the console off" in text


def test_progress_messages_name_the_copy(bus):
    c, link = plugged(bus)
    msgs = []
    write_lk(link, {"lk_a": NEW, "lk_b": NEW}, None, lambda f, m: msgs.append(m))
    for needle in ("Sending the bootloader for lk_b.", "Writing lk_b.", "Checking lk_b.", "Writing lk_a."):
        assert needle in msgs
