"""Finding the console on USB, telling it apart from other fastboot devices, and reading lk_a/lk_b.

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
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterator, List, Optional, Set, Tuple

from .errors import ConsoleCancelled, ConsoleError, ConsoleTooOld, TOO_OLD_TO_READ
from .fastboot import FastbootFail, FastbootSession, LinkError, ProtocolError, READ_TIMEOUT_MS
from .models import ANY_NAME, DEFAULT, KPA, ConsoleModel, by_pico_product, by_stock_product
from .usb import Candidate, UsbBus

Progress = Callable[[Optional[float], str], None]

LK_PARTITIONS = ("lk_a", "lk_b")       # every LK copy that can be read; models.py says which are written
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


def partition_size(session: FastbootSession, name: str) -> Optional[int]:
    """getvar:partition-size:<name> (hex, with or without 0x), or None when not published."""
    value = session.getvar_or_none("partition-size:" + name)
    if not value:
        return None
    try:
        return int(value.strip(), 16)
    except ValueError:
        return None


def read_partition(session: FastbootSession, name: str, limit: Optional[int] = None,
                   cancel: Optional[threading.Event] = None,
                   on_bytes: Optional[Callable[[int, int], None]] = None) -> bytes:
    """Read lk_a or lk_b in the steps the LK stages (1 MB each). With `limit`, stop once at least
    that many bytes from the start are in hand. Raises ConsoleTooOld, FastbootFail, LinkError or
    ProtocolError; callers turn those into sentences."""
    if name not in LK_PARTITIONS:
        raise ValueError("only lk_a and lk_b can be read")
    out = bytearray()
    size: Optional[int] = None
    offset = 0
    while True:
        if cancel is not None and cancel.is_set():
            raise ConsoleCancelled("Cancelled.")
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
        if size is None:
            size = got_size
        elif got_size != size:
            raise ProtocolError("partition size changed between reads")
        target = size if limit is None else min(size, limit)

        def chunk_progress(done: int, _total: int, base=len(out)) -> None:
            if on_bytes:
                on_bytes(min(base + done, target), target)

        data = session.upload(READ_TIMEOUT_MS, chunk_progress)
        if len(data) != got_len:
            raise ProtocolError("upload sent %d bytes, part-read staged %d" % (len(data), got_len))
        out += data
        offset += got_len
        if offset >= size or (limit is not None and offset >= limit):
            return bytes(out)
