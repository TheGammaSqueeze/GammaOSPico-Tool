"""A stand-in for pico_tool.console with the contract's API, for the window's and the command line's
console flows (no USB involved): one console on "fastboot", backups in a folder, writes recorded."""

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional


class ConsoleError(Exception):
    pass


@dataclass
class ConsoleLink:
    kind: str
    ident: str
    label: str
    lk_version: Optional[str]


@dataclass
class LkImageInfo:
    kind: str
    version: Optional[str]
    size: int
    problems: List[str] = field(default_factory=list)


class State(object):
    def __init__(self):
        self.present = True
        self.lk = {"lk_a": b"STOCKLK" * 100, "lk_b": b"STOCKLK" * 100}
        self.writes = []
        self.backup_root = None
        self.fail_write = None


state = State()


def make_image(kind="gammaos-pico", version="1.0.0"):
    return ("LKIMG:%s:%s:" % (kind, version)).encode() + b"\0" * 64


def check_lk_image(data):
    if data.startswith(b"LKIMG:"):
        _tag, kind, version, _rest = data.split(b":", 3)
        return LkImageInfo(kind.decode(), version.decode() or None, len(data), [])
    if data.startswith(b"STOCKLK"):
        return LkImageInfo("stock", None, len(data), [])
    return LkImageInfo("unknown", None, len(data), ["This is not a bootloader image for this console."])


def find_consoles():
    if not state.present:
        return []
    info = check_lk_image(state.lk["lk_a"])
    return [ConsoleLink("fastboot", "KPA0001", "Konkr Pocket Advance (%s, fastboot)" %
                        ("GammaOS Pico " + info.version if info.kind == "gammaos-pico" else "stock"),
                        info.version if info.kind == "gammaos-pico" else None)]


def wait_for_console(kinds, cancel, progress, timeout=None):
    t0 = time.time()
    while not cancel.is_set():
        links = [l for l in find_consoles() if l.kind in kinds]
        if links:
            return links[0]
        progress(None, "Waiting for the console")
        if timeout is not None and time.time() - t0 > timeout:
            raise ConsoleError("No console showed up.")
        time.sleep(0.05)
    raise ConsoleError("Cancelled.")


def default_backup_root():
    return state.backup_root or os.path.join(os.path.expanduser("~"), "GammaOS Pico Backups")


def backup_lk(link, dest_root, cancel, progress):
    if cancel.is_set():
        raise ConsoleError("Cancelled.")
    stamp = time.strftime("%Y-%m-%d_%H%M%S", time.gmtime())
    d = os.path.join(dest_root, link.ident, stamp)
    n = 0
    while os.path.exists(d):
        n += 1
        d = os.path.join(dest_root, link.ident, "%s_%d" % (stamp, n))
    os.makedirs(d)
    meta = {"ident": link.ident, "date": stamp, "tool": "test"}
    for part in ("lk_a", "lk_b"):
        progress(0.5 if part == "lk_a" else 1.0, "Reading %s" % part)
        data = state.lk[part]
        with open(os.path.join(d, part + ".img"), "wb") as f:
            f.write(data)
        info = check_lk_image(data)
        meta[part] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "kind": info.kind,
                      "version": info.version}
    with open(os.path.join(d, "backup.json"), "w") as f:
        json.dump(meta, f)
    return d


def list_backups(dest_root):
    out = []
    if not os.path.isdir(dest_root):
        return out
    for ident in sorted(os.listdir(dest_root)):
        base = os.path.join(dest_root, ident)
        if not os.path.isdir(base):
            continue
        for stamp in sorted(os.listdir(base), reverse=True):
            p = os.path.join(base, stamp, "backup.json")
            if os.path.isfile(p):
                with open(p) as f:
                    info = json.load(f)
                info["path"] = os.path.dirname(p)
                out.append(info)
    return out


def write_lk(link, images, cancel, progress, verify=True):
    if set(images) - {"lk_a", "lk_b"}:
        raise ConsoleError("Only lk_a and lk_b can be written.")
    for part, data in images.items():
        if check_lk_image(data).problems:
            raise ConsoleError("The image for %s is refused." % part)
    if cancel.is_set():
        raise ConsoleError("Cancelled before anything was written.")
    if state.fail_write:
        raise ConsoleError(state.fail_write)
    for part, data in images.items():
        progress(0.5, "Writing %s" % part)
        state.lk[part] = data
        state.writes.append((part, hashlib.sha256(data).hexdigest()))
    progress(1.0, "Checked")


def driver_help():
    return "Linux: add a udev rule for the console's fastboot USB id, then plug it in again."


_lock = threading.Lock()
