"""A small fastboot client, written for GammaOS Pico's bootloader.

It follows the public fastboot wire protocol: the host sends one ASCII command of at most 64 bytes
on the bulk OUT endpoint, and the device answers on the bulk IN endpoint with packets that start
with a four letter tag:

    INFO<text>     progress or detail; more packets follow
    TEXT<text>     the same, for newer devices
    OKAY<text>     the command worked; the text may carry a value (getvar)
    FAIL<text>     the command failed, the text says why
    DATA<8 hex>    the device is ready to move that many bytes (download: host to device,
                   upload: device to host), followed by OKAY or FAIL once they have moved

The transport underneath is anything with send(), recv() and close(); see UsbLink in usb.py for
the real one and the fake device in the tests for the in-memory one.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional

COMMAND_MAX = 64          # longest command the protocol allows
REPLY_MAX = 64            # longest status packet the device sends
MAX_EMPTY_PACKETS = 4     # zero length packets skipped before a reply (end markers after data)
DRAIN_TIMEOUT_MS = 50     # how long a new session waits for leftovers of an earlier one
DRAIN_MAX_PACKETS = 64
DEFAULT_TIMEOUT_MS = 5000
FLASH_TIMEOUT_MS = 60000  # writing 2 MB to the eMMC takes well under a second; leave room
READ_TIMEOUT_MS = 10000   # part-read stages 1 MB from the eMMC before it answers
SEND_CHUNK = 256 * 1024   # bytes per bulk OUT call during a download
RECV_CHUNK = 128 * 1024   # bytes per bulk IN call during an upload (a multiple of 512)


# ---- transport level failures (raised by the transport, never shown to the user as they are) ----

class LinkError(Exception):
    """The USB link failed. `kind` is one of: timeout, gone, busy, access, driver, io."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(detail or kind)
        self.kind = kind
        self.detail = detail


class Transport:
    """What FastbootSession needs from a link. Packet semantics follow USB bulk transfers: a
    recv() of n bytes returns when n bytes have arrived or the device ended a transfer with a
    short packet."""

    max_packet = 512

    def send(self, data: bytes, timeout_ms: int) -> None:
        raise NotImplementedError

    def recv(self, size: int, timeout_ms: int) -> bytes:
        raise NotImplementedError

    def close(self) -> None:
        pass


# ---- protocol level failures ----

class FastbootFail(Exception):
    """The device answered FAIL. `text` is the device's reason, `infos` any INFO lines before it."""

    def __init__(self, command: str, text: str, infos: List[str]):
        super().__init__("%s: %s" % (command, text))
        self.command = command
        self.text = text
        self.infos = infos


class ProtocolError(Exception):
    """The device sent something the protocol does not allow at that point."""


@dataclass
class Reply:
    tag: str                      # "OKAY" or "DATA"
    text: str = ""                # the text after OKAY
    size: int = 0                 # the byte count after DATA
    infos: List[str] = field(default_factory=list)


InfoSink = Optional[Callable[[str], None]]
ByteProgress = Optional[Callable[[int, int], None]]   # (bytes done, bytes total)


def _ascii(raw: bytes) -> str:
    return raw.decode("ascii", "replace")


class FastbootSession:
    """One open conversation with a fastboot device. Not thread safe: one caller at a time."""

    def __init__(self, transport: Transport):
        self.t = transport
        self.broken = False   # set once the stream may be out of step; nothing more is sent

    def close(self) -> None:
        try:
            self.t.close()
        except Exception:
            pass

    def drain(self, timeout_ms: int = DRAIN_TIMEOUT_MS) -> int:
        """Read and drop anything the device still holds from an earlier conversation: a reply that
        conversation never read (it was cut short by an error or a closed program) stays queued in
        the device and would otherwise answer this session's first command, putting every answer
        after it one behind. Call it once, before the first command. Returns the packets dropped."""
        dropped = 0
        while dropped < DRAIN_MAX_PACKETS:
            try:
                self.t.recv(max(self.t.max_packet, REPLY_MAX), timeout_ms)
            except LinkError as exc:
                if exc.kind == "timeout":
                    return dropped
                self.broken = True
                raise
            dropped += 1
        self.broken = True
        raise ProtocolError("the device keeps sending data nobody asked for")

    # -- the core exchange --

    def _send_command(self, command: str, timeout_ms: int) -> None:
        if self.broken:
            raise ProtocolError("the session ended after an earlier error")
        raw = command.encode("ascii")
        if not raw or len(raw) > COMMAND_MAX:
            raise ValueError("fastboot command must be 1 to %d bytes: %r" % (COMMAND_MAX, command))
        try:
            self.t.send(raw, timeout_ms)
        except LinkError:
            self.broken = True
            raise

    def _read_status(self, command: str, timeout_ms: int, on_info: InfoSink) -> Reply:
        """Read packets until OKAY, FAIL or DATA. INFO and TEXT lines are collected."""
        infos: List[str] = []
        empty = 0
        while True:
            try:
                pkt = self.t.recv(max(self.t.max_packet, REPLY_MAX), timeout_ms)
            except LinkError:
                self.broken = True
                raise
            if not pkt and empty < MAX_EMPTY_PACKETS:
                # a zero length packet: the end marker some LKs (the Pocket Block's) send after a
                # data stream that filled its last packet exactly. It carries no reply; the reply
                # follows it.
                empty += 1
                continue
            if len(pkt) < 4 or len(pkt) > REPLY_MAX:
                self.broken = True
                raise ProtocolError("unexpected %d byte reply to %s" % (len(pkt), command))
            tag, rest = _ascii(pkt[:4]), _ascii(pkt[4:])
            if tag in ("INFO", "TEXT"):
                infos.append(rest)
                if on_info:
                    on_info(rest)
                continue
            if tag == "OKAY":
                return Reply("OKAY", text=rest, infos=infos)
            if tag == "FAIL":
                raise FastbootFail(command, rest, infos)
            if tag == "DATA":
                if not re.fullmatch(r"[0-9a-fA-F]{8}", rest):
                    self.broken = True
                    raise ProtocolError("bad DATA length %r in reply to %s" % (rest, command))
                return Reply("DATA", size=int(rest, 16), infos=infos)
            self.broken = True
            raise ProtocolError("unknown reply %r to %s" % (_ascii(pkt[:16]), command))

    def run(self, command: str, timeout_ms: int = DEFAULT_TIMEOUT_MS, on_info: InfoSink = None) -> Reply:
        """Send a command that moves no data and return its OKAY reply."""
        self._send_command(command, timeout_ms)
        reply = self._read_status(command, timeout_ms, on_info)
        if reply.tag != "OKAY":
            self.broken = True
            raise ProtocolError("%s answered DATA where no data was expected" % command)
        return reply

    # -- the commands the tool uses --

    def getvar(self, name: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> str:
        return self.run("getvar:" + name, timeout_ms).text

    def getvar_or_none(self, name: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> Optional[str]:
        """getvar that turns a FAIL (variable not published) into None."""
        try:
            return self.getvar(name, timeout_ms)
        except FastbootFail:
            return None

    def download(self, data: bytes, timeout_ms: int = DEFAULT_TIMEOUT_MS, progress: ByteProgress = None) -> Reply:
        """Move `data` into the device's download buffer."""
        total = len(data)
        command = "download:%08x" % total
        self._send_command(command, timeout_ms)
        reply = self._read_status(command, timeout_ms, None)
        if reply.tag != "DATA":
            self.broken = True
            raise ProtocolError("download answered OKAY without asking for the data")
        if reply.size != total:
            self.broken = True
            raise ProtocolError("download asked for %d bytes, %d were offered" % (reply.size, total))
        view = memoryview(data)
        done = 0
        if progress:
            progress(0, total)
        try:
            while done < total:
                n = min(SEND_CHUNK, total - done)
                self.t.send(bytes(view[done:done + n]), timeout_ms)
                done += n
                if progress:
                    progress(done, total)
        except LinkError:
            self.broken = True
            raise
        final = self._read_status(command, timeout_ms, None)
        if final.tag != "OKAY":
            self.broken = True
            raise ProtocolError("download answered DATA twice")
        return final

    def flash(self, partition: str, timeout_ms: int = FLASH_TIMEOUT_MS, on_info: InfoSink = None) -> Reply:
        return self.run("flash:" + partition, timeout_ms, on_info)

    def upload(self, timeout_ms: int = DEFAULT_TIMEOUT_MS, progress: ByteProgress = None) -> bytes:
        """Fetch what the device has staged: DATA<len>, the bytes, then OKAY."""
        command = "upload"
        self._send_command(command, timeout_ms)
        reply = self._read_status(command, timeout_ms, None)
        if reply.tag != "DATA":
            self.broken = True
            raise ProtocolError("upload answered OKAY without sending data")
        total = reply.size
        out = bytearray()
        mp = max(1, self.t.max_packet)
        if progress:
            progress(0, total)
        try:
            while len(out) < total:
                left = total - len(out)
                # Never ask for more than what is left, rounded up to whole packets: the device
                # sends no zero length packet after the data, so a larger request could swallow
                # the OKAY that follows into the data.
                want = min(RECV_CHUNK, -(-left // mp) * mp)
                chunk = self.t.recv(want, timeout_ms)
                if not chunk:
                    raise ProtocolError("upload stopped after %d of %d bytes" % (len(out), total))
                out += chunk
                if progress:
                    progress(min(len(out), total), total)
        except (LinkError, ProtocolError):
            self.broken = True
            raise
        if len(out) != total:
            self.broken = True
            raise ProtocolError("upload sent %d bytes, %d were announced" % (len(out), total))
        final = self._read_status(command, timeout_ms, None)
        if final.tag != "OKAY":
            self.broken = True
            raise ProtocolError("upload answered DATA twice")
        return bytes(out)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
