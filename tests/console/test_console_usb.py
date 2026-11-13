"""The pyusb layer with stand-in device objects (no USB hardware is touched)."""

import errno

import pytest

usb_core = pytest.importorskip("usb.core")

from pico_tool.console import usb as pusb
from pico_tool.console.fastboot import LinkError


def err(code=None, eno=None, cls=None):
    cls = cls or usb_core.USBError
    return cls("boom", error_code=code, errno=eno)


@pytest.mark.parametrize("exc,kind", [
    (lambda: err(-7, errno.ETIMEDOUT, getattr(usb_core, "USBTimeoutError", usb_core.USBError)), "timeout"),
    (lambda: err(-4, errno.ENODEV), "gone"),
    (lambda: err(-6, errno.EBUSY), "busy"),
    (lambda: err(-3, errno.EACCES), "access"),
    (lambda: err(-12, errno.ENOSYS), "driver"),
    (lambda: err(-5, errno.ENOENT), "driver"),
    (lambda: err(-9, errno.EPIPE), "io"),
    (lambda: err(-1, errno.EIO), "io"),
    (lambda: OSError(errno.EACCES, "denied"), "access"),
])
def test_error_classes(exc, kind):
    assert pusb.classify_usb_error(exc()).kind == kind


class Ep:
    def __init__(self, addr, attrs=0x02, mp=512):
        self.bEndpointAddress = addr
        self.bmAttributes = attrs
        self.wMaxPacketSize = mp


class Intf:
    def __init__(self, num, cls, eps):
        self.bInterfaceNumber = num
        self.bInterfaceClass, self.bInterfaceSubClass, self.bInterfaceProtocol = cls
        self.eps = eps

    def __iter__(self):
        return iter(self.eps)


class Dev:
    def __init__(self, vid, pid, intfs, address=7, readable=True):
        self.idVendor, self.idProduct = vid, pid
        self.bus, self.address, self.port_numbers = 1, address, (2, 3)
        self.intfs = intfs
        self.readable = readable
        self.written = []
        self.reads = []

    def __iter__(self):
        if not self.readable:
            raise usb_core.USBError("descriptors not readable", error_code=-12, errno=errno.ENOSYS)
        return iter([self.intfs])

    def write(self, ep, data, timeout):
        self.written.append((ep, bytes(data)))
        return len(data)

    def read(self, ep, size, timeout):
        if not self.reads:
            raise err(-7, errno.ETIMEDOUT, getattr(usb_core, "USBTimeoutError", usb_core.USBError))
        return self.reads.pop(0)


def patch_bus(monkeypatch, devices):
    monkeypatch.setattr(pusb, "get_backend", lambda: object())
    monkeypatch.setattr(usb_core, "find", lambda **kw: iter(devices))


def test_candidates_match_the_bootloader_interface(monkeypatch):
    fastboot = Dev(0x0E8D, 0x201C, [Intf(0, (0xFF, 0x42, 0x03), [Ep(0x81), Ep(0x01)])])
    adb = Dev(0x0E8D, 0x201C, [Intf(0, (0xFF, 0x42, 0x01), [Ep(0x81), Ep(0x01)])], address=8)
    phone = Dev(0x18D1, 0x4EE0, [Intf(1, (0xFF, 0x42, 0x03), [Ep(0x82, mp=64), Ep(0x02, mp=64)])], address=9)
    mouse = Dev(0x046D, 0xC077, [Intf(0, (0x03, 0x01, 0x02), [Ep(0x81, attrs=0x03)])], address=10)
    patch_bus(monkeypatch, [fastboot, adb, phone, mouse])
    cands = pusb.UsbBus().candidates()
    assert [(c.vid, c.pid, c.interface, c.ep_in, c.ep_out, c.max_packet) for c in cands] == [
        (0x0E8D, 0x201C, 0, 0x81, 0x01, 512),
        (0x18D1, 0x4EE0, 1, 0x82, 0x02, 64),
    ]
    assert cands[0].key != cands[1].key


def test_unreadable_descriptors_with_console_ids(monkeypatch):
    hidden = Dev(0x0E8D, 0x201C, [], readable=False)
    other = Dev(0x1234, 0x5678, [], readable=False, address=9)
    patch_bus(monkeypatch, [hidden, other])
    (cand,) = pusb.UsbBus().candidates()
    assert cand.interface is None
    with pytest.raises(LinkError) as e:
        pusb.UsbBus().open(cand)
    assert e.value.kind == "driver"


def test_link_send_and_recv():
    dev = Dev(0x0E8D, 0x201C, [])
    link = pusb.UsbLink(dev, 0, 0x81, 0x01, 512)
    link.send(b"getvar:product", 1000)
    assert dev.written == [(0x01, b"getvar:product")]
    dev.reads.append(bytearray(b"OKAYk85v1_64_kpa"))
    assert link.recv(512, 1000) == b"OKAYk85v1_64_kpa"
    with pytest.raises(LinkError) as e:
        link.recv(512, 1000)
    assert e.value.kind == "timeout"


def test_backend_loads_without_touching_devices():
    pytest.importorskip("libusb_package")
    pusb._backend = None
    assert pusb.get_backend() is not None
