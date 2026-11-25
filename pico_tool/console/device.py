"""Finding the console on USB, telling it apart from other fastboot devices, and reading lk_a/lk_b
(and tee_a/tee_b where the console's LK allows it).

A device is accepted when its fastboot answers getvar:product with the project GammaOS Pico's LK is
built as for one of the consoles in models.py: "k85v1_64_kpa" (Konkr Pocket Advance) or
"k68v1_64_block" (Konkr Pocket Block). getvar:version-gammaos-pico gives "<version> <commit>"; LKs
built before that variable existed answer FAIL and are still GammaOS Pico, with an unknown version.
A stock LK ("k85v1_64", shared with other MT6785 handhelds, or "m8695_bw01") is refused: the first
install is made with the flash package.
"""

from __future__ import annotations

import re
import sys
import threading
import time
import zlib
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Set, Tuple

from .errors import ConsoleCancelled, ConsoleError, ConsoleTooOld, TOO_OLD_TO_READ
from .fastboot import FastbootFail, FastbootSession, LinkError, ProtocolError, READ_TIMEOUT_MS
from .models import ANY_NAME, DEFAULT, KPA, ConsoleModel, by_pico_product, by_stock_product
from .usb import Candidate, UsbBus

Progress = Callable[[Optional[float], str], None]

LK_PARTITIONS = ("lk_a", "lk_b")       # every LK copy that can be read; models.py says which are written
TEE_PARTITIONS = ("tee_a", "tee_b")    # the secure firmware copies; read by GammaOS Pico 1.2.0 and later
BOOTLOADER_HINT = "hold SELECT + START + R while turning it on"


@dataclass
class ConsoleLink:
    kind: str                  # "fastboot" (GammaOS Pico's bootloader, SELECT+START+R); the only kind
    ident: str                 # stable id of this console (fastboot serialno)
    label: str                 # for the UI
    lk_version: Optional[str]  # "1.0.0 abc1234" when GammaOS Pico answers getvar, else None
    model: ConsoleModel = field(default=DEFAULT)   # which console it is (from getvar:product)


# ---- the bus in use (tests swap in a fake one) ----

_bus_lock = threading.Lock()
_bus = None


def get_bus():
    global _bus
    with _bus_lock:
        if _bus is None:
            _bus = UsbBus()
        return _bus


def set_bus(bus) -> None:
    """Replace the USB bus (used by the tests' in-memory console). None goes back to real USB."""
    global _bus
    with _bus_lock, _state_lock:
        _bus = bus
        _probe_cache.clear()
        _in_use.clear()
        _ident_where.clear()


# ---- user facing wording for link failures ----

def _platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "mac"
    return "linux"


def open_problem_text(kind: str) -> str:
    """Why a console that is plugged in could not be opened, and what to do."""
    plat = _platform()
    if kind == "busy":
        return ("The console is connected but another program is using it (for example fastboot or a "
                "flashing tool); close that program and try again.")
    if kind in ("access", "driver"):
        if plat == "windows":
            if kind == "access":
                return ("The console is connected but cannot be opened; close any other program using it, "
                        "or install the WinUSB driver for it as the driver help explains.")
            return ("The console is connected but Windows has no usable driver for it; install the WinUSB "
                    "driver as the driver help explains, then reconnect it.")
        if plat == "linux":
            return ("The console is connected but this computer does not allow the tool to open it; add "
                    "the udev rule from the driver help, then unplug and reconnect it.")
        return "The console is connected but cannot be opened; unplug it, reconnect it and try again."
    if kind == "gone":
        return "The console was disconnected; reconnect it in its bootloader (%s) and try again." % BOOTLOADER_HINT
    if kind == "timeout":
        return ("The console stopped answering; unplug it, restart it into its bootloader (%s) and try again."
                % BOOTLOADER_HINT)
    return ("The USB connection to the console failed; try another cable or USB port, restart the console "
            "into its bootloader (%s) and try again." % BOOTLOADER_HINT)


def link_error_text(exc: LinkError, during: str = "") -> str:
    """One sentence for a LinkError raised in the middle of an operation."""
    if exc.kind == "gone":
        return ("The console was disconnected%s; reconnect it in its bootloader (%s) and try again."
                % (" " + during if during else "", BOOTLOADER_HINT))
    if exc.kind == "timeout":
        return ("The console stopped answering%s; unplug it, restart it into its bootloader (%s) and try again."
                % (" " + during if during else "", BOOTLOADER_HINT))
    return open_problem_text(exc.kind)


def protocol_error_text(exc: Exception, during: str = "") -> str:
    return ("The console sent an answer the tool did not expect%s (%s); restart it into its bootloader "
            "(%s) and try again." % (" " + during if during else "", exc, BOOTLOADER_HINT))


# ---- probing ----

@dataclass
class Probe:
    """What one fastboot interface on the bus turned out to be."""

    key: str
    link: Optional[ConsoleLink]   # set when it is a usable GammaOS Pico console
    problem: Optional[str]        # otherwise one sentence: why it cannot be used
    can_open: bool                # False when the problem is a driver, permission or busy one


# Polls (find_consoles, often on a timer thread) and operations (open_console) both open devices;
# this lock keeps a poll from opening a console in the instant an operation claims it.
_state_lock = threading.RLock()
_probe_cache: Dict[str, Probe] = {}
_in_use: Dict[str, ConsoleLink] = {}       # bus key -> link, while an operation holds it
_ident_where: Dict[str, str] = {}          # ident -> last bus key it was seen at


def make_label(version: Optional[str], model: ConsoleModel = DEFAULT) -> str:
    if version:
        return "%s (GammaOS Pico %s, fastboot)" % (model.name, version.split(" ", 1)[0])
    return "%s (GammaOS Pico, older than 1.0.0, fastboot)" % model.name


def stock_console_text(model: ConsoleModel) -> str:
    return ("A %s with the stock bootloader is connected; install GammaOS Pico on it the first time with "
            "the flash package %s and SP Flash Tool v5 (see the wiki's Installing page)."
            % (model.name if model is not KPA else "console", model.flash_package))


def identify(session: FastbootSession) -> Tuple[Optional[ConsoleLink], Optional[str]]:
    """Ask a fastboot device who it is. Returns (link, None) or (None, why it is refused)."""
    product = session.getvar_or_none("product")
    if product is None:
        return None, "A fastboot device is connected, but it is not a %s running GammaOS Pico." % ANY_NAME
    product = product.strip()
    stock = by_stock_product(product)
    if stock is not None:
        # "k85v1_64" is also other MT6785 handhelds' stock name, so the Pocket Advance one says "console"
        return None, stock_console_text(stock)
    model = by_pico_product(product)
    if model is None:
        return None, ("A fastboot device (%s) is connected, but it is not a %s running GammaOS Pico."
                      % (product or "unnamed", ANY_NAME))
    serial = (session.getvar_or_none("serialno") or "").strip()
    if not serial or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", serial):
        return None, "The console did not report a usable serial number; restart it into its bootloader and try again."
    version = session.getvar_or_none("version-gammaos-pico")
    if version is not None:
        version = version.strip() or None
    return ConsoleLink(kind="fastboot", ident=serial, label=make_label(version, model), lk_version=version,
                       model=model), None


def _probe(bus, cand: Candidate) -> Probe:
    try:
        transport = bus.open(cand)
    except LinkError as exc:
        return Probe(cand.key, None, open_problem_text(exc.kind), False)
    session = FastbootSession(transport)
    try:
        session.drain()
        link, why = identify(session)
    except LinkError as exc:
        return Probe(cand.key, None, open_problem_text(exc.kind), exc.kind not in ("busy", "access", "driver"))
    except ProtocolError as exc:
        return Probe(cand.key, None, protocol_error_text(exc), True)
    finally:
        session.close()
    if link:
        _ident_where[link.ident] = cand.key
    return Probe(cand.key, link, why, True)


@dataclass
class ScanReport:
    links: List[ConsoleLink]
    problems: List[str]           # one sentence per fastboot device that cannot be used
    blocked: bool                 # True when a console is there but cannot be opened (driver etc.)
    library_problem: Optional[str]


def scan() -> ScanReport:
    """Look at every fastboot interface on the bus once."""
    with _state_lock:
        return _scan_locked()


def _scan_locked() -> ScanReport:
    bus = get_bus()
    lib = bus.backend_problem() if hasattr(bus, "backend_problem") else None
    if lib:
        return ScanReport([], [], False, "The tool's USB library could not be loaded (%s); reinstall the tool." % lib)
    cands = bus.candidates()
    present = {c.key for c in cands}
    for key in list(_probe_cache):
        if key not in present:
            del _probe_cache[key]
    links: List[ConsoleLink] = []
    problems: List[str] = []
    blocked = False
    for cand in cands:
        if cand.key in _in_use:
            links.append(_in_use[cand.key])
            continue
        probe = _probe_cache.get(cand.key)
        if probe is None or probe.link is None:
            # good answers are kept while the device stays plugged in; failures are retried
            probe = _probe(bus, cand)
            if probe.link is not None:
                _probe_cache[cand.key] = probe
        if probe.link is not None:
            links.append(probe.link)
        else:
            if probe.problem and probe.problem not in problems:
                problems.append(probe.problem)
            if not probe.can_open:
                blocked = True
    return ScanReport(links, problems, blocked, None)


def find_consoles() -> List[ConsoleLink]:
    """The GammaOS Pico consoles in fastboot right now (quick poll)."""
    return scan().links


def wait_for_console(kinds: Set[str], cancel: threading.Event, progress: Progress,
                     timeout: Optional[float] = None) -> ConsoleLink:
    """Poll until a usable console appears. Progress messages say why a device that is there
    cannot be used (driver, permission, wrong bootloader); on timeout that reason is raised."""
    if "fastboot" not in set(kinds or ()):
        raise ConsoleError("The tool can only reach the console through GammaOS Pico's bootloader (fastboot).")
    start = time.monotonic()
    last_msg = None
    last_report: Optional[ScanReport] = None
    while True:
        if cancel is not None and cancel.is_set():
            raise ConsoleCancelled("Stopped waiting for the console.")
        report = scan()
        last_report = report
        if report.library_problem:
            raise ConsoleError(report.library_problem)
        if report.links:
            link = report.links[0]
            if progress:
                progress(None, "Found %s." % link.label)
            return link
        if report.problems:
            msg = report.problems[0]
        else:
            msg = "Waiting for the console: %s, then connect it over USB." % BOOTLOADER_HINT
        if progress and msg != last_msg:
            progress(None, msg)
        last_msg = msg
        if timeout is not None and time.monotonic() - start >= timeout:
            if last_report and last_report.problems:
                raise ConsoleError(last_report.problems[0])
            raise ConsoleError("No console was found; turn it on while holding SELECT + START + R, connect it "
                               "over USB and try again.")
        if cancel is not None:
            cancel.wait(0.5)
        else:
            time.sleep(0.5)


# ---- an open session on a known console ----

def _claim(link: ConsoleLink) -> Tuple[str, FastbootSession]:
    """Find and open the console with link's serial number and mark it in use."""
    bus = get_bus()
    cands = bus.candidates()
    if not cands:
        raise ConsoleError("The console is not connected; turn it on while holding SELECT + START + R and "
                           "connect it over USB.")
    preferred = _ident_where.get(link.ident)
    cands.sort(key=lambda c: 0 if c.key == preferred else 1)
    problems: List[str] = []
    for cand in cands:
        if cand.key in _in_use:
            if _in_use[cand.key].ident == link.ident:
                raise ConsoleError("The tool is already working with this console; wait for that to finish.")
            continue
        try:
            transport = bus.open(cand)
        except LinkError as exc:
            problems.append(open_problem_text(exc.kind))
            continue
        session = FastbootSession(transport)
        try:
            session.drain()
            serial = session.getvar_or_none("serialno")
            product = session.getvar_or_none("product")
        except (LinkError, ProtocolError) as exc:
            session.close()
            problems.append(link_error_text(exc) if isinstance(exc, LinkError) else protocol_error_text(exc))
            continue
        if (serial or "").strip() != link.ident or (product or "").strip() != link.model.pico_product:
            session.close()
            continue
        _ident_where[link.ident] = cand.key
        _in_use[cand.key] = link
        return cand.key, session
    if problems:
        raise ConsoleError(problems[0])
    raise ConsoleError("The console with serial number %s is not connected; turn it on while holding SELECT + "
                       "START + R and connect it over USB." % link.ident)


@contextmanager
def open_console(link: ConsoleLink) -> Iterator[FastbootSession]:
    """Open the console that `link` names and keep it reserved until the block ends."""
    if link.kind != "fastboot":
        raise ConsoleError("The tool can only reach the console through GammaOS Pico's bootloader (fastboot).")
    with _state_lock:
        key, session = _claim(link)
    try:
        yield session
    finally:
        session.close()
        with _state_lock:
            _in_use.pop(key, None)
            _probe_cache.pop(key, None)


# ---- reading lk_a / lk_b with oem part-read + upload ----

_PART_INFO = re.compile(r"part (\S+) size (0x[0-9a-fA-F]+) offset (0x[0-9a-fA-F]+) len (0x[0-9a-fA-F]+)")
MAX_PART_SIZE = 0x1000000   # nothing the console reads is larger than 16 MB


def _is_unknown_command(fail: FastbootFail) -> bool:
    return "unknown command" in fail.text.lower()


def supports_part_read(session: FastbootSession) -> bool:
    """True when the LK has `oem part-read` (GammaOS Pico 1.0.0 and later)."""
    try:
        session.run("oem part-read:lk_a", READ_TIMEOUT_MS)
        return True
    except FastbootFail as fail:
        if _is_unknown_command(fail):
            return False
        raise


def supports_tee_read(session: FastbootSession) -> bool:
    """True when the LK's `oem part-read` also reads tee_a and tee_b (GammaOS Pico 1.2.0 and later).
    An older LK refuses those names ("only lk_a and lk_b"), or has no part-read at all."""
    try:
        session.run("oem part-read:" + TEE_PARTITIONS[0], READ_TIMEOUT_MS)
        return True
    except FastbootFail as fail:
        text = fail.text.lower()
        if _is_unknown_command(fail) or "only lk_a and lk_b" in text:
            return False
        raise


def partition_size(session: FastbootSession, name: str) -> Optional[int]:
    """getvar:partition-size:<name> (hex, with or without 0x), or None when not published."""
    value = session.getvar_or_none("partition-size:" + name)
    if not value:
        return None
    try:
        return int(value.strip(), 16)
    except ValueError:
        return None


class UploadRefused(Exception):
    """The console has no `upload`, and the caller asked not to read the slow way."""


def _stage(session: FastbootSession, name: str, offset: int, size: Optional[int]) -> Tuple[int, int]:
    """`oem part-read:<name>:<offset>`: the LK stages up to 1 MB of the partition from there.
    Returns (partition size, bytes staged)."""
    command = "oem part-read:%s:0x%x" % (name, offset)
    try:
        reply = session.run(command, READ_TIMEOUT_MS)
    except FastbootFail as fail:
        if _is_unknown_command(fail):
            raise ConsoleTooOld(TOO_OLD_TO_READ) from fail
        raise
    found = None
    for line in reply.infos:
        m = _PART_INFO.fullmatch(line.strip())
        if m:
            found = m
    if not found:
        raise ProtocolError("part-read gave no size line")
    got_name = found.group(1)
    got_size, got_off, got_len = (int(found.group(i), 16) for i in (2, 3, 4))
    if got_name != name or got_off != offset:
        raise ProtocolError("part-read answered for %s at 0x%x" % (got_name, got_off))
    if got_size <= 0 or got_size > MAX_PART_SIZE or got_len <= 0 or got_off + got_len > got_size:
        raise ProtocolError("part-read sizes do not add up (size 0x%x len 0x%x)" % (got_size, got_len))
    if size is not None and got_size != size:
        raise ProtocolError("partition size changed between reads")
    return got_size, got_len


def read_partition(session: FastbootSession, name: str, limit: Optional[int] = None,
                   cancel: Optional[threading.Event] = None,
                   on_bytes: Optional[Callable[[int, int], None]] = None,
                   candidates: Sequence[bytes] = (), info: Optional[dict] = None,
                   staged_ok: bool = True) -> bytes:
    """Read lk_a or lk_b (or tee_a or tee_b, see supports_tee_read) in the steps the LK stages (1 MB
    each). With `limit`, stop once at least that many bytes from the start are in hand. Raises
    ConsoleTooOld, FastbootFail, LinkError or ProtocolError; callers turn those into sentences.

    A console that answers `upload` with "no upload on this console" (the Pocket Block since 1.1.0)
    is read from checksums instead, see _read_without_upload: `candidates` are images the partition
    may hold, which save fetching their bytes, and `info` (a dict) is filled with what that took.
    With staged_ok False such a console raises UploadRefused instead."""
    if name not in LK_PARTITIONS + TEE_PARTITIONS:
        raise ValueError("only lk_a, lk_b, tee_a and tee_b can be read")
    if session.upload_refused:
        if not staged_ok:
            raise UploadRefused()
        return _read_without_upload(session, name, limit, cancel, on_bytes, candidates, info)
    out = bytearray()
    size: Optional[int] = None
    offset = 0
    while True:
        if cancel is not None and cancel.is_set():
            raise ConsoleCancelled("Cancelled.")
        size, got_len = _stage(session, name, offset, size)
        target = size if limit is None else min(size, limit)

        def chunk_progress(done: int, _total: int, base=len(out)) -> None:
            if on_bytes:
                on_bytes(min(base + done, target), target)

        try:
            data = session.upload(READ_TIMEOUT_MS, chunk_progress)
        except FastbootFail as fail:
            if NO_UPLOAD_TEXT not in fail.text.lower():
                raise
            session.upload_refused = True
            if not staged_ok:
                raise UploadRefused() from fail
            return _read_without_upload(session, name, limit, cancel, on_bytes, candidates, info)
        if len(data) != got_len:
            raise ProtocolError("upload sent %d bytes, part-read staged %d" % (len(data), got_len))
        out += data
        offset += got_len
        if offset >= size or (limit is not None and offset >= limit):
            return bytes(out)


# ---- a console without `upload`: checksums of what part-read staged, and its bytes as text ----
#
# Since GammaOS Pico 1.1.0 the Pocket Block's LK answers `upload` with FAIL "no upload on this
# console: oem stage-hex / stage-crc" (its USB cannot carry a bulk upload). It is told by that
# answer, not by the model. What it offers on the data part-read just staged:
#
#   oem stage-crc:1000     INFO lines with the CRC-32 (zlib's) of every 4 KiB, six 8 digit hex words
#                          to a line; the last one covers what is left
#   oem stage-hex:OFF:LEN  INFO lines "%06x:" and up to 24 bytes as hex: about 24 bytes per USB
#                          packet, so it is used only where the checksums cannot settle it

NO_UPLOAD_TEXT = "no upload on this console"
STAGE_CHUNK = 0x1000
HEX_RUN = 0x4000                 # the most asked of one stage-hex command (683 INFO lines)
SLOW_READ_LIMIT = 4 << 20        # past this much by stage-hex, the progress text says it takes long
_CRC_WORD = re.compile(r"[0-9a-f]{8}")
_HEX_LINE = re.compile(r"([0-9a-f]{6,8}):((?:[0-9a-f]{2}){1,24})")


def stage_crcs(session: FastbootSession, length: int) -> List[int]:
    """The CRC-32 of every 4 KiB of the `length` bytes part-read staged."""
    reply = session.run("oem stage-crc:%x" % STAGE_CHUNK, READ_TIMEOUT_MS)
    words = " ".join(reply.infos).split()
    want = -(-length // STAGE_CHUNK)
    if len(words) != want or not all(_CRC_WORD.fullmatch(w) for w in words):
        raise ProtocolError("stage-crc gave %d checksums for %d chunks" % (len(words), want))
    return [int(w, 16) for w in words]


def stage_hex(session: FastbootSession, offset: int, length: int) -> bytes:
    """`length` bytes at `offset` of what part-read staged, sent as text."""
    out = bytearray()
    while len(out) < length:
        want = min(HEX_RUN, length - len(out))
        start = offset + len(out)
        reply = session.run("oem stage-hex:%x:%x" % (start, want), READ_TIMEOUT_MS)
        got = bytearray()
        for line in reply.infos:
            m = _HEX_LINE.fullmatch(line.strip())
            if not m or int(m.group(1), 16) != start + len(got):
                raise ProtocolError("stage-hex sent a line the tool did not expect")
            got += bytes.fromhex(m.group(2))
        if len(got) != want:
            raise ProtocolError("stage-hex sent %d bytes, %d were asked for" % (len(got), want))
        out += got
    return bytes(out)


def _steps(session: FastbootSession, name: str, limit: Optional[int], cancel,
           on_step: Optional[Callable[[int, int], None]] = None) -> Tuple[int, List[Tuple[int, int, List[int]]]]:
    """Stage the partition step by step and collect each step's checksums. Returns (partition
    size, [(offset, length, checksums), ...])."""
    steps: List[Tuple[int, int, List[int]]] = []
    size: Optional[int] = None
    offset = 0
    while True:
        if cancel is not None and cancel.is_set():
            raise ConsoleCancelled("Cancelled.")
        size, got_len = _stage(session, name, offset, size)
        steps.append((offset, got_len, stage_crcs(session, got_len)))
        offset += got_len
        if on_step:
            on_step(offset, size)
        if offset >= size or (limit is not None and offset >= limit):
            return size, steps


def partition_crcs(session: FastbootSession, name: str, limit: Optional[int] = None,
                   cancel: Optional[threading.Event] = None,
                   on_bytes: Optional[Callable[[int, int], None]] = None) -> List[int]:
    """The checksums of every 4 KiB of a partition, asked afresh (a backup's second read)."""
    def on_step(done: int, size: int) -> None:
        if on_bytes:
            target = size if limit is None else min(size, limit)
            on_bytes(min(done, target), target)
    _size, steps = _steps(session, name, limit, cancel, on_step)
    return [crc for _off, _len, crcs in steps for crc in crcs]


def _read_without_upload(session: FastbootSession, name: str, limit: Optional[int], cancel,
                         on_bytes, candidates: Sequence[bytes], info: Optional[dict]) -> bytes:
    """Build the partition's bytes from checksums. A 4 KiB chunk whose CRC-32 equals that of the
    same chunk of a candidate image (zero padded), or of a chunk of zeros or of 0xFF, is taken from
    there; every other chunk is fetched with stage-hex and checked against its CRC-32.

    `info` gets "crcs" (every chunk's checksum, for the caller's second read), "slow_total" and
    "slow_done" (bytes that had to come by stage-hex)."""
    if info is None:
        info = {}

    def scanned(done: int, size: int) -> None:
        if on_bytes:
            target = size if limit is None else min(size, limit)
            on_bytes(min(done, target) // 10, target)

    size, steps = _steps(session, name, limit, cancel, scanned)
    target = size if limit is None else min(size, limit)
    plain: Dict[Tuple[int, int], int] = {}       # (fill byte, length) -> checksum

    def known(at: int, length: int, crc: int) -> Optional[bytes]:
        for fill in (0x00, 0xFF):
            if (fill, length) not in plain:
                plain[(fill, length)] = zlib.crc32(bytes([fill]) * length)
            if plain[(fill, length)] == crc:
                return bytes([fill]) * length
        for image in candidates:
            piece = bytes(image[at:at + length]).ljust(length, b"\x00")
            if at < len(image) and zlib.crc32(piece) == crc:
                return piece
        return None

    # what each chunk is, where the checksums settle it; the rest is fetched below
    pieces: List[List[Optional[bytes]]] = []
    slow_total = 0
    for offset, length, crcs in steps:
        row: List[Optional[bytes]] = []
        for j, crc in enumerate(crcs):
            clen = min(STAGE_CHUNK, length - j * STAGE_CHUNK)
            piece = known(offset + j * STAGE_CHUNK, clen, crc)
            if piece is None:
                slow_total += clen
            row.append(piece)
        pieces.append(row)
    info.update(crcs=[crc for _o, _l, crcs in steps for crc in crcs], slow_total=slow_total, slow_done=0)

    def fetched() -> None:
        if on_bytes:
            share = info["slow_done"] / slow_total if slow_total else 1.0
            on_bytes(int(target * (0.1 + 0.9 * share)), target)

    for (offset, length, crcs), row in zip(steps, pieces):
        missing = [j for j, piece in enumerate(row) if piece is None]
        if not missing:
            continue
        _stage(session, name, offset, size)
        again = stage_crcs(session, length)
        if again != crcs:
            raise ProtocolError("the checksums of %s changed between two reads" % name)
        for j in missing:
            if cancel is not None and cancel.is_set():
                raise ConsoleCancelled("Cancelled.")
            clen = min(STAGE_CHUNK, length - j * STAGE_CHUNK)
            piece = stage_hex(session, j * STAGE_CHUNK, clen)
            if zlib.crc32(piece) != crcs[j]:
                piece = stage_hex(session, j * STAGE_CHUNK, clen)      # once more, then give up
                if zlib.crc32(piece) != crcs[j]:
                    raise ProtocolError("the bytes read from %s do not match their checksum" % name)
            row[j] = piece
            info["slow_done"] += clen
            fetched()
    if on_bytes:
        on_bytes(target, target)
    return b"".join(piece for row in pieces for piece in row)


def partition_matches(session: FastbootSession, name: str, data: bytes,
                      cancel: Optional[threading.Event] = None,
                      on_bytes: Optional[Callable[[int, int], None]] = None) -> bool:
    """True when the partition starts with `data` (the read back after a write; only the image's
    length is looked at). With `upload` the bytes are fetched and compared. Without it, each 1 MB
    step the image covers is compared by the CRC-32 of its 4 KiB chunks; the chunk the image ends
    in, where what follows on the console is not known, has the image's part fetched with stage-hex
    and compared byte for byte."""
    if not session.upload_refused:
        try:
            back = read_partition(session, name, limit=len(data), cancel=cancel, on_bytes=on_bytes,
                                  staged_ok=False)
            return len(back) >= len(data) and back[:len(data)] == data
        except UploadRefused:
            pass
    size: Optional[int] = None
    offset = 0
    while offset < len(data):
        size, length = _stage(session, name, offset, size)
        crcs = stage_crcs(session, length)
        for j, crc in enumerate(crcs):
            at = offset + j * STAGE_CHUNK
            if at >= len(data):
                break
            clen = min(STAGE_CHUNK, length - j * STAGE_CHUNK)
            if at + clen <= len(data):
                if zlib.crc32(data[at:at + clen]) != crc:
                    return False
            elif stage_hex(session, j * STAGE_CHUNK, len(data) - at) != data[at:]:
                return False
        offset += length
        if on_bytes:
            on_bytes(min(offset, len(data)), len(data))
        if offset >= size:
            break
    return offset >= len(data)
