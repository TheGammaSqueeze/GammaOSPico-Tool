"""An in-memory GammaOS Pico console speaking fastboot, for testing without hardware.

It models USB bulk transfers at the packet level (512 byte packets, no zero length packet after a
data stream), so a host that reads past the data would swallow the OKAY, exactly like real USB.
Faults can be switched on per test: FAIL replies, silence (timeouts), unplugging, corrupt read
backs, a busy or driverless device, and an old LK without `oem part-read`.
"""

from __future__ import annotations

import re
import struct
from collections import deque
from typing import Dict, List, Optional, Set

from pico_tool.console.fastboot import LinkError, Transport
from pico_tool.console.usb import Candidate

PACKET = 512
STAGE_STEP = 0x100000


# ---- LK image builder ----

def _header(name: str, dsize: int, last: bool, itype: int = 0) -> bytes:
    h = bytearray(b"\xff" * 512)
    struct.pack_into("<II", h, 0, 0x58881688, dsize)
    h[8:40] = name.encode("ascii").ljust(32, b"\x00")
    struct.pack_into("<II", h, 40, 0xFFFFFFFF, 0xFFFFFFFF)
    struct.pack_into("<IIIIII", h, 48, 0x58891689, 512, 1, itype, 1 if last else 0, 16)
    return bytes(h)


def _align(b: bytearray, n: int = 16) -> None:
    while len(b) % n:
        b.append(0)


def make_lk_image(kind: str = "pico", version: Optional[str] = "1.0.0 abc1234", signed: bool = True,
                  dtb: bool = True, panel: bool = True, pad_to: Optional[int] = None,
                  filler: int = 4096, seed: int = 1) -> bytes:
    """A small image with the same part chain and identifying strings as a real one."""
    code = bytearray()
    code += bytes((seed * 7 + i) & 0xFF for i in range(filler))
    if kind == "pico":
        code += b"\x00version-bootloader\x00\x00k85v1_64_kpa-abc1234-20261113124810-20261113125558\x00\x00"
        if version is not None:
            code += b"version-gammaos-pico\x00\x00\x00\x00" + version.encode("ascii") + b"\x00\x00"
        code += b"product\x00k85v1_64_kpa\x00\x00# GammaOS Pico boot mode for this card.\n\x00"
    elif kind == "stock":
        code += b"\x00k85v1_64-5cd8ba07fef-20260730160432-20260730170410\x00\x00product\x00k85v1_64\x00"
    elif kind == "apm":   # same board name, another console's panel
        code += b"\x00k85v1_64-58d8ec45ef8-20251027204950-20251027210907\x00\x00st7703_hd720_lcm_drv\x00"
        panel = False
    elif kind == "apm-pico":
        code += b"\x00k85v1_64-f035f83-20260819025244-20260819135712\x00GammaOS Pico\x00"
        panel = False
    if panel:
        code += b"st7123p_hd640_boe_lcm_drv\x00"
    code += bytes(256)

    parts = [("lk", bytes(code), 0)]
    if signed:
        parts += [("cert1", b"\x11" * 0x6AD, 0x2000000), ("cert2", b"\x22" * 0x3D6, 0x2000002)]
    if dtb:
        parts += [("lk_main_dtb", b"\xd0\x0d\xfe\xed" + b"\x33" * 300, 0)]
        if signed:
            parts += [("cert1", b"\x11" * 0x6AD, 0x2000000), ("cert2", b"\x22" * 0x3D6, 0x2000002)]
    out = bytearray()
    for i, (name, body, itype) in enumerate(parts):
        out += _header(name, len(body), i == len(parts) - 1, itype)
        out += body
        if i != len(parts) - 1:
            _align(out)
    if pad_to:
        out += bytes(pad_to - len(out))
    return bytes(out)


# ---- the device ----

class FakeConsole:
    def __init__(self, serial: str = "N7KBVK6D99ZD6HPB", product: str = "k85v1_64_kpa",
                 version: Optional[str] = "1.0.0 abc1234", part_read: bool = True,
                 current_slot: str = "a", partition_size: int = 0x200000):
        self.vars: Dict[str, str] = {
            "product": product,
            "serialno": serial,
            "current-slot": current_slot,
            "max-download-size": "0x8000000",
            "partition-size:lk_a": "%x" % partition_size,
            "partition-size:lk_b": "%x" % partition_size,
            "version": "0.5",
        }
        if version is not None:
            self.vars["version-gammaos-pico"] = version
        self.part_read = part_read
        self.partitions: Dict[str, bytearray] = {
            "lk_a": bytearray(make_lk_image(seed=1, pad_to=partition_size)),
            "lk_b": bytearray(make_lk_image(seed=1, pad_to=partition_size)),
        }
        self.download: Optional[bytes] = None
        self.staged = b""
        self.out: deque = deque()
        self.receiving = 0
        self.rx = bytearray()
        self.gone = False
        self.log: List[str] = []         # every command received
        self.flashed: List[str] = []     # partitions written, in order
        # faults
        self.open_error: Optional[str] = None          # LinkError kind raised on open
        self.fail: Dict[str, str] = {}                 # command prefix -> FAIL text
        self.silent: Set[str] = set()                  # command prefixes that never get an answer
        self.unplug_on: Set[str] = set()               # command prefixes on which the cable is pulled
        self.corrupt_reads: Dict[str, int] = {}        # partition -> part-read calls left to corrupt (-1 = always)
        self.corrupt_flash: Dict[str, int] = {}        # partition -> flashes left that store damaged data
        self.short_upload = False                      # upload sends fewer bytes than announced
        self.low_battery = False
        self.flash_count: Dict[str, int] = {}

    # packets from device to host
    def _emit(self, data: bytes) -> None:
        for i in range(0, len(data), PACKET):
            self.out.append(bytes(data[i:i + PACKET]))

    def _reply(self, tag: str, text: str = "") -> None:
        self._emit((tag + text).encode("ascii")[:64])

    def _match(self, table, cmd: str):
        for prefix in table:
            if cmd.startswith(prefix):
                return prefix
        return None

    def host_sent(self, data: bytes) -> None:
        if self.gone:
            raise LinkError("gone", "no device")
        if self.receiving:
            self.rx += data
            if len(self.rx) > self.receiving:
                raise LinkError("io", "host sent more than the download asked for")
            if len(self.rx) == self.receiving:
                self.download = bytes(self.rx)
                self.receiving = 0
                self._reply("OKAY")
            return
        if len(data) > 64:
            raise LinkError("io", "command too long")
        cmd = data.decode("ascii")
        self.log.append(cmd)
        if self._match(self.unplug_on, cmd):
            self.gone = True
            return
        if self._match(self.silent, cmd):
            return
        hit = self._match(self.fail, cmd)
        if hit is not None:
            self._reply("FAIL", self.fail[hit])
            return
        self._handle(cmd)

    def _handle(self, cmd: str) -> None:
        if cmd.startswith("getvar:"):
            name = cmd[7:]
            if name in self.vars:
                self._reply("OKAY", self.vars[name])
            else:
                self._reply("FAIL", "GetVar Variable Not found")
        elif cmd.startswith("download:"):
            n = int(cmd[9:], 16)
            if self.low_battery:
                self._reply("FAIL", "low power, need battery charging.")
                return
            self.download = None
            self.rx = bytearray()
            self.receiving = n
            self._reply("DATA", "%08x" % n)
            if n == 0:
                self.receiving = 0
                self.download = b""
                self._reply("OKAY")
        elif cmd.startswith("flash:"):
            part = cmd[6:]
            if part not in self.partitions:
                self._reply("FAIL", "This partition doesn't exist")
                return
            data = self.download or b""
            if len(data) > len(self.partitions[part]):
                self._reply("FAIL", "size too large")
                return
            left = self.corrupt_flash.get(part, 0)
            if left:
                data = bytearray(data)
                data[len(data) // 2] ^= 0xFF
                self.corrupt_flash[part] = left - 1 if left > 0 else -1
            self.partitions[part][:len(data)] = data
            self.flashed.append(part)
            self.flash_count[part] = self.flash_count.get(part, 0) + 1
            self._reply("OKAY")
        elif cmd.startswith("oem part-read:") and self.part_read:
            self._part_read(cmd[len("oem part-read:"):])
        elif cmd == "upload":
            if not self.staged:
                self._reply("FAIL", "nothing staged (oem shotstage first)")
                return
            self._reply("DATA", "%08x" % len(self.staged))
            data = self.staged[:-100] if self.short_upload else self.staged
            self._emit(data)
            if not self.short_upload:
                self._reply("OKAY")
        else:
            self._reply("FAIL", "unknown command")

    def _part_read(self, arg: str) -> None:
        name, _, off_text = arg.partition(":")
        if name not in ("lk_a", "lk_b"):
            self._reply("FAIL", "part-read: only lk_a and lk_b")
            return
        off = 0
        if off_text:
            t = off_text[2:] if off_text.lower().startswith("0x") else off_text
            if not re.fullmatch(r"[0-9a-fA-F]{1,8}", t):
                self._reply("FAIL", "part-read: bad offset")
                return
            off = int(t, 16)
        size = len(self.partitions[name])
        if off >= size:
            self._reply("FAIL", "part-read: offset past the end")
            return
        n = min(STAGE_STEP, size - off)
        data = bytes(self.partitions[name][off:off + n])
        left = self.corrupt_reads.get(name, 0)
        if left:
            d = bytearray(data)
            d[17] ^= 0x5A
            data = bytes(d)
            self.corrupt_reads[name] = left - 1 if left > 0 else -1
        self.staged = data
        self._reply("INFO", "part %s size 0x%x offset 0x%x len 0x%x" % (name, size, off, n))
        self._reply("OKAY")

    def host_read(self, size: int) -> bytes:
        """One bulk IN transfer: packets until `size` bytes or a short packet. A packet that does
        not fit is an overflow, and running dry before either is a timeout, as on real USB."""
        if self.gone:
            raise LinkError("gone", "no device")
        out = bytearray()
        while True:
            if not self.out:
                raise LinkError("timeout", "no answer")
            pkt = self.out[0]
            if len(out) + len(pkt) > size:
                raise LinkError("io", "overflow: a %d byte packet with %d bytes of room" % (len(pkt), size - len(out)))
            self.out.popleft()
            out += pkt
            if len(pkt) < PACKET or len(out) == size:
                return bytes(out)


class FakeLink(Transport):
    max_packet = PACKET

    def __init__(self, console: FakeConsole):
        self.c = console
        self.closed = False

    def send(self, data: bytes, timeout_ms: int) -> None:
        self.c.host_sent(bytes(data))

    def recv(self, size: int, timeout_ms: int) -> bytes:
        return self.c.host_read(size)

    def close(self) -> None:
        self.closed = True
        self.c.out.clear()
        self.c.receiving = 0


class FakeBus:
    def __init__(self, *consoles: FakeConsole):
        self.consoles: List[FakeConsole] = list(consoles)
        self.opens = 0

    def backend_problem(self):
        return None

    def candidates(self) -> List[Candidate]:
        return [Candidate("1-%d-%d" % (i + 1, i + 5), 0x0E8D, 0x201C, 0, 0x81, 0x01, PACKET, c)
                for i, c in enumerate(self.consoles) if not c.gone]

    def open(self, cand: Candidate) -> FakeLink:
        console = cand.handle
        if console.open_error:
            raise LinkError(console.open_error, "fake open failure")
        self.opens += 1
        return FakeLink(console)
