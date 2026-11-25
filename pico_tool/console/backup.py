"""Backups of the console's LK copies (lk_a and lk_b) and, when the console's LK can read them
(GammaOS Pico 1.2.0 and later), of its secure firmware copies (tee_a and tee_b). See models.py.

Layout: <dest_root>/<ident>/<UTC yyyy-mm-dd_HHMMSS>/{lk_a.img, lk_b.img, tee_a.img, tee_b.img,
backup.json}

backup.json:
    {"ident": "<serialno>", "date": "<UTC ISO 8601>", "model": "kpa" | "block",
     "lk_a": {"sha256": "...", "size": n, "kind": "...", "version": "..." or null},
     "lk_b": {...},
     "tee_a": {"sha256": "...", "size": n, "kind": "secure-firmware" | "unknown"}, "tee_b": {...},
     "tool": "<tool version>"}

Older backups hold less and still load and restore: those made before the tool knew more than one
console have no "model" (they are Pocket Advance backups), a Pocket Block backup made before 1.2.0
holds lk_a only, and no backup made before 1.2.0, or from a console whose LK cannot read the secure
firmware, has the tee entries. A partition without an entry is simply not part of that backup.

A backup is written into "<stamp>.partial" and renamed when complete, so a half written one never
shows up in the list.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import sys
import threading
from typing import Callable, Dict, List, Optional, Sequence

from .device import (SLOW_READ_LIMIT, ConsoleLink, link_error_text, open_console, partition_crcs,
                     partition_size, protocol_error_text, read_partition, supports_tee_read)
from .errors import ConsoleCancelled, ConsoleError, ConsoleTooOld
from .fastboot import FastbootFail, LinkError, ProtocolError
from .lkimage import check_lk_image, check_tee_image
from .models import KPA, ConsoleModel, by_key, names_text

Progress = Callable[[Optional[float], str], None]

TEE_KIND = "secure-firmware"
TEE_NOT_BACKED_UP = ("The secure firmware was not backed up: this console's bootloader is older than GammaOS "
                     "Pico 1.2.0 and cannot read it over USB. The stock secure firmware is in every flash "
                     "package from 1.2.0 on.")
BACKUP_JSON = "backup.json"
BACKUP_FOLDER_NAME = "GammaOS Pico Backups"
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}_\d{6}(_\d+)?")


def _tool_version() -> str:
    try:
        from pico_tool import __version__
        return str(__version__)
    except Exception:
        return "unknown"


def _safe_ident(ident: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", ident or "")
    cleaned = cleaned.strip(".")
    return cleaned or "unknown-console"


# ---- where backups live ----

def _windows_documents() -> Optional[str]:
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                        ("Data4", ctypes.c_ubyte * 8)]

        # FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
        fid = GUID(0xFDD39AD0, 0x238F, 0x46AF, (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
        out = ctypes.c_wchar_p()
        shell32 = ctypes.windll.shell32
        ole32 = ctypes.windll.ole32
        if shell32.SHGetKnownFolderPath(ctypes.byref(fid), 0, None, ctypes.byref(out)) != 0:
            return None
        path = out.value
        ole32.CoTaskMemFree(out)
        return path
    except Exception:
        return None


def _xdg_documents() -> Optional[str]:
    home = os.path.expanduser("~")
    config = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    try:
        with open(os.path.join(config, "user-dirs.dirs"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("XDG_DOCUMENTS_DIR="):
                    value = line.split("=", 1)[1].strip().strip('"')
                    value = value.replace("$HOME", home)
                    if value and value.rstrip("/") != home.rstrip("/"):
                        return value
    except OSError:
        pass
    return None


def default_backup_root() -> str:
    """~/Documents/GammaOS Pico Backups, using the system's own Documents folder where it has one."""
    home = os.path.expanduser("~")
    docs = None
    if sys.platform.startswith("win"):
        docs = _windows_documents()
    elif sys.platform != "darwin":
        docs = _xdg_documents()
    if not docs:
        docs = os.path.join(home, "Documents")
    return os.path.join(docs, BACKUP_FOLDER_NAME)


# ---- taking a backup ----

def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _write_file(path: str, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def backup_model(record) -> ConsoleModel:
    """The console a backup (its backup.json, or a list_backups entry) was made from."""
    key = record.get("model") if isinstance(record, dict) else None
    return by_key(key, KPA) if key else KPA


def _read_both(link: ConsoleLink, cancel, progress: Optional[Progress],
               candidates: Optional[Dict[str, Sequence[bytes]]] = None) -> Dict[str, bytes]:
    """Read the console's LK copies, and its secure firmware copies when its LK can read them,
    twice each, and keep them only when both reads agree.

    A console without `upload` (device.py) has its partitions built from checksums: the second read
    is then a second asking of every checksum, which must equal the first. `candidates`
    ({partition: images it may hold}, from the flash package in hand) saves fetching bytes the tool
    already has; without them everything that is not blank comes the slow way."""
    images: Dict[str, bytes] = {}
    names = list(link.model.partitions)
    passes = len(names) * 2
    step = [0]
    info: dict = {}

    def report(done: int, total: int) -> None:
        if progress and total:
            frac = (step[0] + done / total) / passes * 0.9
            name = names[step[0] // 2]
            word = "Reading" if step[0] % 2 == 0 else "Checking"
            text = "%s %s (%d of %d KB)." % (word, name, done // 1024, total // 1024)
            slow = info.get("slow_total", 0)
            if slow and step[0] % 2 == 0:
                text += " " + slow_read_text(slow)
            progress(frac, text)

    with open_console(link) as session:
        if supports_tee_read(session):
            names += link.model.tee_partitions
            passes = len(names) * 2
        for name in names:
            expected = partition_size(session, name)
            info.clear()
            first = read_partition(session, name, cancel=cancel, on_bytes=report,
                                   candidates=(candidates or {}).get(name, ()), info=info)
            step[0] += 1
            if "crcs" in info:
                same = partition_crcs(session, name, cancel=cancel, on_bytes=report) == info["crcs"]
            else:
                same = read_partition(session, name, cancel=cancel, on_bytes=report) == first
            step[0] += 1
            if not same:
                raise ConsoleError("Two reads of %s gave different data; try another cable or USB port and back "
                                   "up again." % name)
            if expected is not None and len(first) != expected:
                raise ConsoleError("The console sent %d bytes of %s but the partition is %d bytes; restart it "
                                   "into its bootloader and back up again." % (len(first), name, expected))
            images[name] = first
    return images


def slow_read_text(slow_bytes: int) -> str:
    """The note in a backup's progress line on a console that cannot send its data in one piece."""
    if slow_bytes > SLOW_READ_LIMIT:
        return ("This console sends %d MB of it a few bytes at a time (more than 4 MB): this takes a long time."
                % (slow_bytes >> 20))
    return ("This console sends %d KB of it a few bytes at a time: this takes a few minutes."
            % max(1, slow_bytes >> 10))


def operation_error(exc: BaseException, during: str) -> ConsoleError:
    """Turn a low level failure into the one sentence the person sees."""
    if isinstance(exc, ConsoleError):
        return exc
    if isinstance(exc, LinkError):
        return ConsoleError(link_error_text(exc, during))
    if isinstance(exc, FastbootFail):
        reason = exc.text.strip() or "no reason given"
        if "low power" in reason.lower():
            return ConsoleError("The console's battery is too low to do this safely; charge it and try again.")
        return ConsoleError("The console refused %s (%s); restart it into its bootloader and try again."
                            % (during or "the request", reason))
    if isinstance(exc, ProtocolError):
        return ConsoleError(protocol_error_text(exc, during))
    return ConsoleError("Something went wrong %s (%s); restart the console into its bootloader and try again."
                        % (during or "talking to the console", exc))


def backup_lk(link: ConsoleLink, dest_root: str, cancel: Optional[threading.Event],
              progress: Optional[Progress],
              candidates: Optional[Dict[str, Sequence[bytes]]] = None) -> str:
    """Read the console's LK copies (lk_a and lk_b) and save them with their checksums, together
    with its secure firmware copies (tee_a and tee_b) when its LK can read them. When it cannot (an
    LK older than 1.2.0) the backup holds the LK alone, and backup_text() and the last progress
    line say so. Returns the backup folder.

    `candidates` ({partition: [images it may hold]}: the flash package's GammaOS Pico and stock
    images when a backup is made before a write) only matters on a console without `upload`, where
    it saves fetching those bytes a few at a time; see _read_both."""
    if progress:
        progress(0.0, "Opening the console.")
    try:
        images = _read_both(link, cancel, progress, candidates)
    except (ConsoleCancelled, ConsoleTooOld):
        raise
    except Exception as exc:
        raise operation_error(exc, "while reading the bootloader") from exc

    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    stamp = now.strftime("%Y-%m-%d_%H%M%S")
    console_dir = os.path.join(dest_root, _safe_ident(link.ident))
    final = os.path.join(console_dir, stamp)
    n = 2
    while os.path.exists(final) or os.path.exists(final + ".partial"):
        final = os.path.join(console_dir, "%s_%d" % (stamp, n))
        n += 1
    partial = final + ".partial"
    if progress:
        progress(0.92, "Saving the backup.")
    record = {"ident": link.ident, "date": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "model": link.model.key}
    try:
        os.makedirs(partial)
        for name in images:
            data = images[name]
            path = os.path.join(partial, name + ".img")
            _write_file(path, data)
            digest = _sha(data)
            if _file_sha(path) != digest or os.path.getsize(path) != len(data):
                raise ConsoleError("The backup file %s did not read back correctly; choose another folder and "
                                   "back up again." % path)
            if name in link.model.tee_partitions:
                kind = "unknown" if check_tee_image(data).problems else TEE_KIND
                record[name] = {"sha256": digest, "size": len(data), "kind": kind}
            else:
                info = check_lk_image(data, link.model)
                record[name] = {"sha256": digest, "size": len(data), "kind": info.kind, "version": info.version}
        record["tool"] = _tool_version()
        tmp_json = os.path.join(partial, BACKUP_JSON + ".tmp")
        with open(tmp_json, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_json, os.path.join(partial, BACKUP_JSON))
        os.rename(partial, final)
    except ConsoleError:
        _remove_tree(partial)
        raise
    except OSError as exc:
        _remove_tree(partial)
        raise ConsoleError("The backup could not be saved in %s (%s); choose another folder or free some space."
                           % (dest_root, exc.strerror or exc)) from exc
    if progress:
        note = "" if any(n in images for n in link.model.tee_partitions) else " " + TEE_NOT_BACKED_UP
        progress(1.0, "Backup saved in %s.%s" % (final, note))
    return final


def backup_text(path: str) -> str:
    """What the backup in `path` holds, in a sentence or two for the person: which copies were
    saved, and that the secure firmware was not when the console's LK could not read it."""
    try:
        with open(os.path.join(path, BACKUP_JSON), encoding="utf-8") as f:
            record = json.load(f)
    except (OSError, ValueError):
        record = {}
    if not isinstance(record, dict):
        record = {}
    model = backup_model(record)
    lk = [p for p in model.partitions if isinstance(record.get(p), dict)]
    tee = [p for p in model.tee_partitions if isinstance(record.get(p), dict)]
    text = "The console's bootloader (%s)" % names_text(lk) if lk else "The console's bootloader"
    if tee:
        return "%s and secure firmware (%s) are saved in %s" % (text, names_text(tee), path)
    return "%s is saved in %s\n%s" % (text, path, TEE_NOT_BACKED_UP)


def _remove_tree(path: str) -> None:
    try:
        for name in os.listdir(path):
            try:
                os.remove(os.path.join(path, name))
            except OSError:
                pass
        os.rmdir(path)
    except OSError:
        pass


# ---- reading backups back ----

def _check_backup(folder: str, record: dict) -> List[str]:
    problems: List[str] = []
    key = record.get("model")
    if key is not None and by_key(key, None) is None:
        return ["The backup is of a console this version of the tool does not know (%s)." % key]
    made_from = backup_model(record)
    for name in made_from.all_partitions:
        entry = record.get(name)
        if entry is None and name not in made_from.backup_always:
            continue      # not part of this backup (an older one, or the tee could not be read)
        if not isinstance(entry, dict) or not isinstance(entry.get("sha256"), str):
            problems.append("The backup has no record of %s." % name)
            continue
        path = os.path.join(folder, name + ".img")
        if not os.path.isfile(path):
            problems.append("The backup's %s.img is missing." % name)
            continue
        if os.path.getsize(path) != entry.get("size"):
            problems.append("The backup's %s.img has the wrong size." % name)
            continue
        if _file_sha(path) != entry["sha256"].lower():
            problems.append("The backup's %s.img does not match its checksum." % name)
    return problems


def list_backups(dest_root: str) -> List[dict]:
    """Every backup under dest_root, newest first: backup.json's fields plus "path", "ok" and
    "problems" (the files are re-hashed against the recorded sha256)."""
    found: List[dict] = []
    try:
        consoles = sorted(os.listdir(dest_root))
    except OSError:
        return found
    for console in consoles:
        cdir = os.path.join(dest_root, console)
        if not os.path.isdir(cdir):
            continue
        try:
            stamps = os.listdir(cdir)
        except OSError:
            continue
        for stamp in stamps:
            folder = os.path.join(cdir, stamp)
            if not _STAMP.fullmatch(stamp) or not os.path.isdir(folder):
                continue
            try:
                with open(os.path.join(folder, BACKUP_JSON), encoding="utf-8") as f:
                    record = json.load(f)
            except (OSError, ValueError):
                continue
            if not isinstance(record, dict) or not isinstance(record.get("ident"), str):
                continue
            entry = dict(record)
            entry["path"] = folder
            entry.setdefault("model", KPA.key)
            try:
                entry["problems"] = _check_backup(folder, record)
            except OSError as exc:
                entry["problems"] = ["The backup could not be read (%s)." % (exc.strerror or exc)]
            entry["ok"] = not entry["problems"]
            found.append(entry)
    found.sort(key=lambda e: (str(e.get("date", "")), e["path"]), reverse=True)
    return found


def has_backup(dest_root: str, ident: str) -> bool:
    """True when dest_root holds at least one intact backup of this console."""
    return any(b.get("ident") == ident and b.get("ok") for b in list_backups(dest_root))


def read_backup(path: str, model: Optional[ConsoleModel] = None) -> Dict[str, bytes]:
    """Load what a backup folder holds ({partition: image}: the LK copies and, when it has them, the
    secure firmware copies), checked against the recorded sha256, for write_lk. With `model` (the
    console it would be written to), a backup of the other console is refused.

    An LK copy that not every backup holds (the Pocket Block's lk_b, which its factory leaves empty)
    is left out when what was saved from it is not a bootloader: there is nothing to put back."""
    try:
        with open(os.path.join(path, BACKUP_JSON), encoding="utf-8") as f:
            record = json.load(f)
    except (OSError, ValueError) as exc:
        raise ConsoleError("The backup in %s cannot be read (%s); choose another backup." % (path, exc)) from exc
    if not isinstance(record, dict):
        raise ConsoleError("The backup in %s cannot be read; choose another backup." % path)
    key = record.get("model")
    if key is not None and by_key(key, None) is None:
        raise ConsoleError("The backup in %s is of a console this version of the tool does not know (%s); "
                           "choose another backup." % (path, key))
    made_from = backup_model(record)
    if model is not None and made_from is not model:
        raise ConsoleError("The backup in %s was made from a %s, not this %s; choose a backup of this console."
                           % (path, made_from.name, model.name))
    images: Dict[str, bytes] = {}
    for name in made_from.all_partitions:
        entry = record.get(name)
        if entry is None and name not in made_from.backup_always:
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("sha256"), str):
            raise ConsoleError("The backup in %s has no record of %s; choose another backup." % (path, name))
        try:
            with open(os.path.join(path, name + ".img"), "rb") as f:
                data = f.read()
        except OSError as exc:
            raise ConsoleError("The backup's %s.img cannot be read (%s); choose another backup."
                               % (name, exc.strerror or exc)) from exc
        if _sha(data) != entry["sha256"].lower() or len(data) != entry.get("size"):
            raise ConsoleError("The backup's %s.img does not match its checksum, so it was not used; choose "
                               "another backup." % name)
        if (name in made_from.partitions and name not in made_from.backup_always
                and check_lk_image(data, made_from).problems):
            continue
        images[name] = data
    return images
