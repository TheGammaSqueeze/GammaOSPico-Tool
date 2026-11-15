"""Finding and identifying the console, and the wording when it cannot be used."""

import threading

import pytest

from console_fake import FakeConsole
from pico_tool.console import (ConsoleCancelled, ConsoleError, ConsoleLink, driver_help, find_consoles,
                               scan, wait_for_console)
from pico_tool.console import device


def no_progress(frac, msg):
    pass


def test_nothing_plugged_in(bus):
    assert find_consoles() == []
    with pytest.raises(ConsoleError) as e:
        wait_for_console({"fastboot"}, threading.Event(), no_progress, timeout=0)
    assert "No console was found" in str(e.value)


def test_finds_pico_console(bus):
    bus.consoles.append(FakeConsole())
    links = find_consoles()
    assert links == [ConsoleLink("fastboot", "N7KBVK6D99ZD6HPB",
                                 "Konkr Pocket Advance (GammaOS Pico 1.0.0, fastboot)", "1.0.0 abc1234")]


def test_poll_reuses_identification(bus):
    c = FakeConsole()
    bus.consoles.append(c)
    find_consoles()
    n = len(c.log)
    find_consoles()
    find_consoles()
    assert len(c.log) == n   # no new commands once the console is known


def test_old_pico_has_no_version(bus):
    bus.consoles.append(FakeConsole(version=None))
    (link,) = find_consoles()
    assert link.lk_version is None
    assert "older than 1.0.0" in link.label


def test_stock_bootloader_refused(bus):
    bus.consoles.append(FakeConsole(product="k85v1_64"))
    report = scan()
    assert report.links == []
    assert "stock bootloader" in report.problems[0]
    with pytest.raises(ConsoleError) as e:
        wait_for_console({"fastboot"}, threading.Event(), no_progress, timeout=0)
    assert "stock bootloader" in str(e.value)


def test_other_fastboot_device_refused(bus):
    bus.consoles.append(FakeConsole(product="sargo"))
    report = scan()
    assert report.links == [] and "not a Konkr Pocket Advance" in report.problems[0]


def test_device_without_product_refused(bus):
    c = FakeConsole()
    del c.vars["product"]
    bus.consoles.append(c)
    assert "not a Konkr" in scan().problems[0]


@pytest.mark.parametrize("kind,word", [("driver", "driver"), ("access", None), ("busy", "another program")])
def test_present_but_cannot_open(bus, monkeypatch, kind, word):
    c = FakeConsole()
    c.open_error = kind
    bus.consoles.append(c)
    report = scan()
    assert report.links == []
    if kind != "busy":
        assert report.blocked
    assert "connected" in report.problems[0]
    if word:
        assert word in report.problems[0]
    with pytest.raises(ConsoleError) as e:
        wait_for_console({"fastboot"}, threading.Event(), no_progress, timeout=0)
    assert "connected" in str(e.value)
    assert "No console was found" not in str(e.value)


def test_driver_text_per_os(monkeypatch):
    for plat, needle in (("win32", "WinUSB"), ("linux", "udev rule"), ("darwin", "cannot be opened")):
        monkeypatch.setattr(device.sys, "platform", plat)
        assert needle in device.open_problem_text("access")


def test_driver_help_per_os(monkeypatch):
    from pico_tool.console import drivers
    monkeypatch.setattr(drivers.sys, "platform", "win32")
    text = driver_help()
    assert "WinUSB" in text and "0E8D" in text and "201C" in text and "Zadig" in text
    monkeypatch.setattr(drivers.sys, "platform", "linux")
    text = driver_help()
    assert 'ATTR{idVendor}=="0e8d"' in text and 'ATTR{idProduct}=="201c"' in text
    assert "uaccess" in text and "plugdev" in text
    monkeypatch.setattr(drivers.sys, "platform", "darwin")
    assert "no driver" in driver_help()


def test_wait_reports_and_finds(bus):
    c = FakeConsole()
    messages = []
    cancel = threading.Event()

    def plug_in_later():
        bus.consoles.append(c)

    t = threading.Timer(0.2, plug_in_later)
    t.start()
    link = wait_for_console({"fastboot"}, cancel, lambda f, m: messages.append(m), timeout=5)
    t.join()
    assert link.ident == "N7KBVK6D99ZD6HPB"
    assert any("SELECT + START + R" in m for m in messages)
    assert messages[-1].startswith("Found")


def test_wait_cancel(bus):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ConsoleCancelled):
        wait_for_console({"fastboot"}, cancel, no_progress, timeout=5)


def test_wait_needs_fastboot_kind(bus):
    with pytest.raises(ConsoleError):
        wait_for_console({"adb"}, threading.Event(), no_progress, timeout=0)


def test_two_consoles(bus):
    bus.consoles.append(FakeConsole(serial="AAAA1111"))
    bus.consoles.append(FakeConsole(serial="BBBB2222", version=None))
    idents = sorted(lk.ident for lk in find_consoles())
    assert idents == ["AAAA1111", "BBBB2222"]


def test_open_console_picks_the_right_serial(bus):
    a = FakeConsole(serial="AAAA1111")
    b = FakeConsole(serial="BBBB2222")
    bus.consoles += [a, b]
    link = [lk for lk in find_consoles() if lk.ident == "BBBB2222"][0]
    with device.open_console(link) as s:
        assert s.getvar("serialno") == "BBBB2222"
        # while it is in use, a poll still lists it without talking to it
        before = len(b.log)
        assert "BBBB2222" in [lk.ident for lk in find_consoles()]
        assert len(b.log) == before


def test_open_console_gone(bus):
    c = FakeConsole()
    bus.consoles.append(c)
    (link,) = find_consoles()
    bus.consoles.clear()
    with pytest.raises(ConsoleError) as e:
        with device.open_console(link):
            pass
    assert "not connected" in str(e.value)


def test_library_problem(bus, monkeypatch):
    monkeypatch.setattr(bus, "backend_problem", lambda: "no libusb library could be loaded")
    with pytest.raises(ConsoleError) as e:
        wait_for_console({"fastboot"}, threading.Event(), no_progress, timeout=1)
    assert "USB library" in str(e.value)


def test_stale_reply_from_an_earlier_session_is_dropped(bus):
    """A reply an earlier program never read stays queued in the console; the next session must
    not take it as the answer to its first command (every answer after it would be one behind)."""
    c = FakeConsole()
    c.out.append(b"OKAYk85v1_64_kpa")          # left over from a getvar nobody read
    c.out.append(b"")                           # and a zero length end marker
    bus.consoles.append(c)
    (link,) = find_consoles()
    assert link.ident == "N7KBVK6D99ZD6HPB" and link.lk_version == "1.0.0 abc1234"
    c.out.append(b"OKAY")                       # again before an operation opens it
    with device.open_console(link) as s:
        assert s.getvar("serialno") == "N7KBVK6D99ZD6HPB"


def test_console_that_never_stops_sending_is_refused(bus):
    c = FakeConsole()
    for _ in range(100):
        c.out.append(b"INFOnoise")
    bus.consoles.append(c)
    report = scan()
    assert report.links == [] and "did not expect" in report.problems[0]
