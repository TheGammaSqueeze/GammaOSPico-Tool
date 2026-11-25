"""A stand-in for pico_tool.console with the contract's API, for the window's and the command line's
console flows (no USB involved): one console on "fastboot" (a Konkr Pocket Advance, or a Konkr
Pocket Block with state.model = BLOCK), backups in a folder, writes recorded.

Images: b"LKIMG:<kind>:<version>:" then b"block:" for a Pocket Block image (anything else is a
Pocket Advance one); b"STOCKLK" (Pocket Advance) and b"STOCKBLK" (Pocket Block) are stock.
A secure firmware image starts with b"TEEIMG:"; state.tee_read says whether this console's
bootloader can read tee_a and tee_b back (GammaOS Pico 1.2.0 and later)."""

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional

from pico_tool.console import models
from pico_tool.console.models import BLOCK, KPA, MODELS, ConsoleModel


class ConsoleError(Exception):
    pass


@dataclass
class ConsoleLink:
    kind: str
    ident: str
    label: str
    lk_version: Optional[str]
    model: ConsoleModel = KPA


@dataclass
class LkImageInfo:
    kind: str
    version: Optional[str]
    size: int
    problems: List[str] = field(default_factory=list)
    model: Optional[ConsoleModel] = None


class State(object):
    def __init__(self):
        self.present = True
        self.model = KPA
        self.lk = {"lk_a": b"STOCKLK" * 100, "lk_b": b"STOCKLK" * 100}
        self.tee = {"tee_a": tee_image("stock"), "tee_b": tee_image("stock")}
        self.tee_read = False
        self.backup_candidates = None
        self.writes = []
        self.backup_root = None
        self.fail_write = None


def tee_image(kind="gammaos"):
    return ("TEEIMG:%s:" % kind).encode() + b"\0" * 64


@dataclass
class TeeImageInfo:
    size: int
    problems: List[str] = field(default_factory=list)


def check_tee_image(data):
    if data.startswith(b"TEEIMG:"):
        return TeeImageInfo(len(data))
    return TeeImageInfo(len(data), ["The file is not a secure firmware image."])


state = State()


def make_image(kind="gammaos-pico", version="1.0.0", model=KPA):
    return ("LKIMG:%s:%s:" % (kind, version)).encode() + (b"block:" if model is BLOCK else b"") + b"\0" * 64


def stock_image(model=KPA):
    return (b"STOCKBLK" if model is BLOCK else b"STOCKLK") * 100


def check_lk_image(data, model=None):
    if data.startswith(b"LKIMG:"):
        _tag, kind, version, rest = data.split(b":", 3)
        info = LkImageInfo(kind.decode(), version.decode() or None, len(data), [],
                           BLOCK if rest.startswith(b"block:") else KPA)
    elif data.startswith(b"STOCKBLK"):
        info = LkImageInfo("stock", None, len(data), [], BLOCK)
    elif data.startswith(b"STOCKLK"):
        info = LkImageInfo("stock", None, len(data), [], KPA)
    else:
        return LkImageInfo("unknown", None, len(data), ["This is not a bootloader image for this console."])
    if model is not None and info.model is not model:
        info.problems.append("This bootloader is for the %s, not this %s." % (info.model.name, model.name))
    return info


IDENTS = {"kpa": "KPA0001", "block": "BLOCK0001"}


def find_consoles():
    if not state.present:
        return []
    info = check_lk_image(state.lk["lk_a"])
    return [ConsoleLink("fastboot", IDENTS[state.model.key], "%s (%s, fastboot)" %
                        (state.model.name, "GammaOS Pico " + info.version if info.kind == "gammaos-pico" else "stock"),
                        info.version if info.kind == "gammaos-pico" else None, state.model)]


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


def backup_lk(link, dest_root, cancel, progress, candidates=None):
    state.backup_candidates = candidates
    if cancel.is_set():
        raise ConsoleError("Cancelled.")
    stamp = time.strftime("%Y-%m-%d_%H%M%S", time.gmtime())
    d = os.path.join(dest_root, link.ident, stamp)
    n = 0
    while os.path.exists(d):
        n += 1
        d = os.path.join(dest_root, link.ident, "%s_%d" % (stamp, n))
    os.makedirs(d)
    meta = {"ident": link.ident, "date": stamp, "tool": "test", "model": link.model.key}
    for part in link.model.partitions:
        progress(0.5 if part == "lk_a" else 1.0, "Reading %s" % part)
        data = state.lk[part]
        with open(os.path.join(d, part + ".img"), "wb") as f:
            f.write(data)
        info = check_lk_image(data)
        meta[part] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "kind": info.kind,
                      "version": info.version}
    for part in link.model.tee_partitions if state.tee_read else ():
        data = state.tee[part]
        with open(os.path.join(d, part + ".img"), "wb") as f:
            f.write(data)
        meta[part] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "kind": "secure-firmware"}
    with open(os.path.join(d, "backup.json"), "w") as f:
        json.dump(meta, f)
    return d


def backup_text(path):
    with open(os.path.join(path, "backup.json")) as f:
        meta = json.load(f)
    if "tee_a" in meta:
        return "The console's bootloader and secure firmware are saved in %s" % path
    return "The console's bootloader is saved in %s\nThe secure firmware was not backed up." % path


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


def backup_model(entry):
    return models.by_key(entry.get("model"))


def read_backup(path, model=None):
    with open(os.path.join(path, "backup.json")) as f:
        meta = json.load(f)
    made_from = backup_model(meta)
    if model is not None and made_from is not model:
        raise ConsoleError("The backup was made from a %s, not this %s." % (made_from.name, model.name))
    out = {}
    for part in made_from.all_partitions:
        if part not in meta:
            continue          # an older backup, or the secure firmware could not be read
        with open(os.path.join(path, part + ".img"), "rb") as f:
            data = f.read()
        if hashlib.sha256(data).hexdigest() != meta[part]["sha256"]:
            raise ConsoleError("%s.img does not match backup.json: the backup is damaged." % part)
        if (part in made_from.partitions and part not in made_from.backup_always
                and check_lk_image(data, made_from).problems):
            continue          # the Pocket Block's lk_b as the factory left it: nothing to put back
        out[part] = data
    return out


def write_lk(link, images, cancel, progress, verify=True):
    model = link.model
    if set(images) - set(model.all_partitions):
        raise ConsoleError("Only %s can be written." % models.names_text(model.all_partitions))
    for part, data in images.items():
        if part in model.tee_partitions:
            bad = check_tee_image(data).problems
        else:
            bad = check_lk_image(data, model).problems
        if bad:
            raise ConsoleError("The image for %s is refused." % part)
    if cancel.is_set():
        raise ConsoleError("Cancelled before anything was written.")
    if state.fail_write:
        raise ConsoleError(state.fail_write)
    results = {}
    order = [p for p in model.tee_partitions if p in images] + [p for p in model.partitions if p in images]
    for part in order:
        data = images[part]
        progress(0.5, "Writing %s" % part)
        (state.tee if part in model.tee_partitions else state.lk)[part] = data
        state.writes.append((part, hashlib.sha256(data).hexdigest()))
        results[part] = "written" if part in model.tee_partitions and not state.tee_read else "verified"
    progress(1.0, "Checked")
    return results


def driver_help():
    return "Linux: add a udev rule for the console's fastboot USB id, then plug it in again."


_lock = threading.Lock()
