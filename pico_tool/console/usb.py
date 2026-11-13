"""USB access through pyusb and the libusb library bundled by the libusb-package wheel.

The bus finds every interface that announces itself as an Android bootloader interface (class
0xFF, subclass 0x42, protocol 0x03). GammaOS Pico's LK enumerates as 0E8D:201C (MediaTek's
default ids from the k85v1_64 target), product string "Android", and its fastboot serial number as
the USB serial number.
"""

from __future__ import annotations

import errno
import os
import sys
import threading
from dataclasses import dataclass
from typing import List, Optional

from .fastboot import LinkError, Transport

FASTBOOT_CLASS = (0xFF, 0x42, 0x03)
KPA_VID = 0x0E8D
KPA_PID = 0x201C

# libusb error codes (negative) as pyusb reports them in backend_error_code
_LIBUSB_IO = -1
_LIBUSB_ACCESS = -3
_LIBUSB_NO_DEVICE = -4
_LIBUSB_NOT_FOUND = -5
_LIBUSB_BUSY = -6
_LIBUSB_TIMEOUT = -7
_LIBUSB_PIPE = -9
_LIBUSB_NOT_SUPPORTED = -12


@dataclass
class Candidate:
    """A fastboot interface seen on the bus. `key` is stable while the device stays plugged in."""

    key: str
    vid: int
    pid: int
    interface: Optional[int]      # None when the descriptors could not be read (no driver yet)
    ep_in: Optional[int]
    ep_out: Optional[int]
    max_packet: int
    handle: object = None         # the pyusb device, or whatever the bus needs to open it


def classify_usb_error(exc: BaseException) -> LinkError:
    """Turn a pyusb / libusb failure into one of our LinkError kinds."""
    try:
        import usb.core   # noqa: F401  (only to know the timeout class)
        timeout_cls = getattr(usb.core, "USBTimeoutError", ())
    except Exception:   # pragma: no cover - pyusb missing
        timeout_cls = ()
    code = getattr(exc, "backend_error_code", None)
    err = getattr(exc, "errno", None)
    text = str(exc)
    if (timeout_cls and isinstance(exc, timeout_cls)) or code == _LIBUSB_TIMEOUT or err == errno.ETIMEDOUT:
        return LinkError("timeout", text)
    if code == _LIBUSB_NO_DEVICE or err == errno.ENODEV:
        return LinkError("gone", text)
    if code == _LIBUSB_BUSY or err == errno.EBUSY:
        return LinkError("busy", text)
    if code == _LIBUSB_ACCESS or err in (errno.EACCES, errno.EPERM):
        return LinkError("access", text)
    if code in (_LIBUSB_NOT_SUPPORTED, _LIBUSB_NOT_FOUND) or err in (errno.ENOSYS, errno.ENOENT):
        return LinkError("driver", text)
    if code == _LIBUSB_PIPE or err == errno.EPIPE:
        return LinkError("io", text)
    return LinkError("io", text)


# ---- the libusb backend ----

_backend_lock = threading.Lock()
_backend = None
_backend_error: Optional[str] = None


def _frozen_library_dirs() -> List[str]:
    """Where a PyInstaller build keeps the bundled libusb (its hook copies it to the root)."""
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return []
    return [base, os.path.join(base, "libusb_package")]


def get_backend():
    """The pyusb backend for the bundled libusb, falling back to a system libusb."""
    global _backend, _backend_error
    with _backend_lock:
        if _backend is not None:
            return _backend
        try:
            import usb.backend.libusb1 as libusb1
        except Exception as exc:
            _backend_error = "pyusb is not installed (%s)" % exc
            return None
        be = None
        try:
            import libusb_package
            be = libusb_package.get_libusb1_backend()
        except Exception:
            be = None
        if be is None:
            names = ("libusb-1.0.dll", "libusb-1.0.dylib", "libusb-1.0.so")
            for d in _frozen_library_dirs():
                for n in names:
                    path = os.path.join(d, n)
                    if os.path.isfile(path):
                        try:
                            be = libusb1.get_backend(find_library=lambda _name, p=path: p)
                        except Exception:
                            be = None
                        if be is not None:
                            break
                if be is not None:
                    break
        if be is None:
            try:
                be = libusb1.get_backend()
            except Exception:
                be = None
        if be is None:
            _backend_error = "no libusb library could be loaded"
        _backend = be
        return be


class UsbLink(Transport):
    """A claimed fastboot interface."""

    def __init__(self, dev, interface: int, ep_in: int, ep_out: int, max_packet: int):
        self.dev = dev
        self.interface = interface
        self.ep_in = ep_in
        self.ep_out = ep_out
        self.max_packet = max_packet or 512
        self._closed = False

    def send(self, data: bytes, timeout_ms: int) -> None:
        try:
            done = 0
            while done < len(data):
                n = self.dev.write(self.ep_out, data[done:], timeout_ms)
                if n <= 0:
                    raise LinkError("io", "the device accepted no data")
                done += n
        except LinkError:
            raise
        except Exception as exc:
            raise classify_usb_error(exc) from exc

    def recv(self, size: int, timeout_ms: int) -> bytes:
        try:
            return bytes(self.dev.read(self.ep_in, size, timeout_ms))
        except Exception as exc:
            raise classify_usb_error(exc) from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            import usb.util
            try:
                usb.util.release_interface(self.dev, self.interface)
            except Exception:
                pass
            usb.util.dispose_resources(self.dev)
        except Exception:
            pass


class UsbBus:
    """Enumerates and opens fastboot interfaces through pyusb."""

    def backend_problem(self) -> Optional[str]:
        get_backend()
        return _backend_error if _backend is None else None

    def candidates(self) -> List[Candidate]:
        be = get_backend()
        if be is None:
            return []
        import usb.core
        out: List[Candidate] = []
        try:
            devices = list(usb.core.find(find_all=True, backend=be))
        except Exception:
            return []
        for dev in devices:
            try:
                vid, pid = dev.idVendor, dev.idProduct
                port = ".".join(str(p) for p in (getattr(dev, "port_numbers", None) or ()))
                key = "%s-%s-%s" % (dev.bus, port or "?", dev.address)
            except Exception:
                continue
            found = None
            descriptors_read = False
            try:
                for cfg in dev:
                    descriptors_read = True
                    for intf in cfg:
                        if (intf.bInterfaceClass, intf.bInterfaceSubClass, intf.bInterfaceProtocol) != FASTBOOT_CLASS:
                            continue
                        ep_in = ep_out = None
                        mp = 512
                        for ep in intf:
                            if (ep.bmAttributes & 0x03) != 0x02:
                                continue   # bulk only
                            if ep.bEndpointAddress & 0x80:
                                ep_in = ep.bEndpointAddress
                                mp = ep.wMaxPacketSize & 0x7FF or mp
                            else:
                                ep_out = ep.bEndpointAddress
                        if ep_in is not None and ep_out is not None:
                            found = (intf.bInterfaceNumber, ep_in, ep_out, mp)
                            break
                    if found:
                        break
            except Exception:
                descriptors_read = False
            if found:
                out.append(Candidate(key, vid, pid, found[0], found[1], found[2], found[3], dev))
            elif not descriptors_read and (vid, pid) == (KPA_VID, KPA_PID):
                # Windows without a driver for the device can hide its descriptors; the ids
                # alone say it may be the console, so report it as one that cannot be opened.
                out.append(Candidate(key, vid, pid, None, None, None, 512, dev))
        return out

    def open(self, cand: Candidate) -> UsbLink:
        if cand.interface is None:
            raise LinkError("driver", "descriptors not readable")
        import usb.core
        import usb.util
        dev = cand.handle
        try:
            try:
                if dev.is_kernel_driver_active(cand.interface):
                    dev.detach_kernel_driver(cand.interface)
            except (NotImplementedError, usb.core.USBError):
                pass   # not supported on Windows and macOS, and no kernel driver binds 0xFF anyway
            try:
                dev.get_active_configuration()
            except usb.core.USBError as exc:
                if "Configuration not set" in str(exc):
                    dev.set_configuration()
                else:
                    raise
            usb.util.claim_interface(dev, cand.interface)
        except LinkError:
            raise
        except Exception as exc:
            try:
                usb.util.dispose_resources(dev)
            except Exception:
                pass
            raise classify_usb_error(exc) from exc
        return UsbLink(dev, cand.interface, cand.ep_in, cand.ep_out, cand.max_packet)
