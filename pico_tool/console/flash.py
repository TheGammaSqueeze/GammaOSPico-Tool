"""Writing the console's LK copies, and from GammaOS Pico 1.2.0 its secure firmware ("tee") copies,
over GammaOS Pico's fastboot, with a read back check.

Which copies: lk_a and lk_b, and tee_a and tee_b when the images given include the secure firmware
(flash packages from 1.2.0 on). See models.py.

Order: the secure firmware first, then the bootloader; with the secure firmware written and the
bootloader not yet, the console still starts as before. Within each, the copy the console is NOT
running from goes first (getvar:current-slot) and is read back before the running copy is touched.
If anything goes wrong with a first copy, the copy the console starts from is still the old, working
one. Whether the preloader would fall back to the other copy when the one it starts from is bad is
not known, so the messages never count on it.

Read back: each copy is read back over the image's length (an image is smaller than its partition)
and compared. A console that has no `upload` (the Pocket Block since 1.1.0) is compared by the
checksums of what it wrote instead, see device.partition_matches; the result is the same "verified". An LK older than 1.2.0 cannot read tee_a and tee_b: those are then reported as
"written", not "verified", and that is not an error.
"""

from __future__ import annotations

import threading
from typing import Callable, Dict, List, Optional

from .backup import has_backup, operation_error, default_backup_root
from .device import (ConsoleLink, open_console, partition_matches, partition_size, supports_part_read,
                     supports_tee_read)
from .errors import ConsoleCancelled, ConsoleError, ConsoleTooOld
from .fastboot import FLASH_TIMEOUT_MS, FastbootFail, FastbootSession, LinkError, ProtocolError
from .lkimage import check_lk_image, check_tee_image
from .models import ConsoleModel, names_text

Progress = Callable[[Optional[float], str], None]


def _slot_order(names: List[str], prefix: str, current: Optional[str]) -> List[str]:
    """The running slot last; _a before _b when the running slot is unknown."""
    names = sorted(names)
    running = prefix + current if current in ("a", "b") else None
    if running in names:
        names.remove(running)
        names.append(running)
    return names


def _write_order(names: List[str], current: Optional[str], model: ConsoleModel) -> List[str]:
    """The secure firmware copies, then the LK copies; within each, the running slot last."""
    tee = [n for n in names if n in model.tee_partitions]
    lk = [n for n in names if n in model.partitions]
    return _slot_order(tee, "tee_", current) + _slot_order(lk, "lk_", current)


def _what(name: str, model: ConsoleModel) -> str:
    return "secure firmware" if name in model.tee_partitions else "bootloader"


def write_lk(link: ConsoleLink, images: Dict[str, bytes], cancel: Optional[threading.Event],
             progress: Optional[Progress], verify: bool = True,
             backup_root: Optional[str] = None) -> Dict[str, str]:
    """Write {partition: image}: LK images for lk_a and/or lk_b, secure firmware images for tee_a
    and/or tee_b. Every LK image must pass check_lk_image for this console and every secure
    firmware image check_tee_image, so neither can land in the other's partitions. Cancel is
    honoured only before the first byte is written. With verify, each copy is read back and
    compared where the console's LK can read it.

    Returns {"tee_b": "verified" | "written", ...} in the order written; "written" means the LK
    could not read that copy back. A console whose LK cannot be read over USB (older than 1.0.0) is
    written only when `backup_root` (default_backup_root() when None) already holds a backup of it."""
    if not isinstance(images, dict) or not images:
        raise ConsoleError("No bootloader image was given, so nothing was written.")
    model = link.model
    bad = [k for k in images if k not in model.all_partitions]
    if bad:
        raise ConsoleError("Only %s can be written, not %s; nothing was written."
                           % (names_text(model.all_partitions), ", ".join(map(str, bad))))
    for name in sorted(images):
        data = images[name]
        if not isinstance(data, (bytes, bytearray)):
            raise ConsoleError("The image for %s is not a file's contents; nothing was written." % name)
        if name in model.tee_partitions:
            problems = check_tee_image(bytes(data)).problems
        else:
            problems = check_lk_image(bytes(data), model).problems
        if problems:
            raise ConsoleError("The image for %s cannot be written: %s Nothing was written."
                               % (name, problems[0].rstrip(".") + "."))
    if cancel is not None and cancel.is_set():
        raise ConsoleCancelled("Cancelled; nothing was written.")
    has_tee = any(n in model.tee_partitions for n in images)
    has_lk = any(n in model.partitions for n in images)

    def say(frac: Optional[float], msg: str) -> None:
        if progress:
            progress(frac, msg)

    say(0.0, "Opening the console.")
    results: Dict[str, str] = {}
    running: List[str] = []
    try:
        with open_console(link) as session:
            try:
                can_read = supports_part_read(session)
                can_read_tee = can_read and has_tee and supports_tee_read(session)
            except Exception as exc:
                raise operation_error(exc, "while checking the bootloader") from exc
            if not can_read:
                root = backup_root if backup_root is not None else default_backup_root()
                if not has_backup(root, link.ident):
                    raise ConsoleTooOld(
                        "This console's bootloader is too old to be backed up over USB and there is no backup "
                        "of it yet, so nothing was written; update it once with the flash package (see the "
                        "wiki), then the tool can back it up and update it.")
            try:
                current = session.getvar_or_none("current-slot")
                max_dl = session.getvar_or_none("max-download-size")
                sizes = {n: partition_size(session, n) for n in images}
            except Exception as exc:
                raise operation_error(exc, "while checking the bootloader") from exc
            current = (current or "").strip().lstrip("_") or None
            limit = None
            if max_dl:
                try:
                    limit = int(max_dl.strip(), 16)
                except ValueError:
                    limit = None
            for name, data in images.items():
                if sizes.get(name) is not None and len(data) > sizes[name]:
                    raise ConsoleError("The image for %s is larger than the console's %s partition; nothing was "
                                       "written." % (name, name))
                if limit is not None and len(data) > limit:
                    raise ConsoleError("The image for %s is larger than the console accepts in one transfer; "
                                       "nothing was written." % name)
            if cancel is not None and cancel.is_set():
                raise ConsoleCancelled("Cancelled; nothing was written.")

            order = _write_order(list(images), current, model)
            checked = {n: verify and (can_read_tee if n in model.tee_partitions else can_read) for n in order}
            steps = sum(2 if checked[n] else 1 for n in order)
            done_steps = [0]

            def sub(done: int, total: int, label: str) -> None:
                if total:
                    say((done_steps[0] + done / total) / steps * 0.98, label)

            if current in ("a", "b"):
                running = ["tee_" + current, "lk_" + current]
            for name in order:
                data = bytes(images[name])
                what = _what(name, model)
                if _write_one(session, name, what, data, checked[name], sub, done_steps):
                    results[name] = "verified" if checked[name] else "written"
                    continue
                # the read back did not match: write it once more, then give up and say where things are
                done_steps[0] -= 2
                if _write_one(session, name, what, data, True, sub, done_steps):
                    results[name] = "verified"
                    continue
                raise ConsoleError("The copy written to %s did not read back correctly; %s"
                                   % (name, _where_things_stand(name, list(images), results, running, model)))
    except ConsoleError:
        raise
    except _WriteStepFailed as failed:
        raise ConsoleError(_step_failed_text(failed, list(images), results, running, model)) from failed.cause
    except Exception as exc:
        raise operation_error(exc, "while writing the %s" % ("bootloader" if has_lk else "secure firmware")) from exc

    say(1.0, _done_text(results, verify, model))
    return results


def _done_text(results: Dict[str, str], verify: bool, model: ConsoleModel) -> str:
    """The last progress line: what was written and whether it was read back."""
    tee = [n for n in results if n in model.tee_partitions]
    lk = [n for n in results if n in model.partitions]
    if tee and lk:
        subject, verb = "The secure firmware and the bootloader", "were"
    elif tee:
        subject, verb = "The secure firmware", "was"
    else:
        subject, verb = "The bootloader", "was"
    unchecked = [n for n, v in results.items() if v != "verified"]
    if not verify:
        return "%s %s written." % (subject, verb)
    if not unchecked:
        return "%s %s written and checked." % (subject, verb)
    if len(unchecked) == len(results):
        return ("Written. This bootloader is too old to read the copies back, so they were not checked; "
                "the console accepted every write.")
    good = [n for n in results if n not in unchecked]
    return ("%s %s written. %s read back and checked. The console's bootloader is too old to read %s back, "
            "so %s not checked; the console accepted every write."
            % (subject, verb, _was(names_text(good), len(good)), names_text(unchecked),
               "it was" if len(unchecked) == 1 else "they were"))


def _was(names: str, count: int) -> str:
    return "%s %s" % (names, "was" if count == 1 else "were")


class _WriteStepFailed(Exception):
    def __init__(self, name: str, what: str, cause: BaseException):
        super().__init__("%s %s" % (what, name))
        self.name = name
        self.what = what
        self.cause = cause


def _write_one(session: FastbootSession, name: str, what: str, data: bytes, verify: bool, sub,
               done_steps) -> bool:
    """Download, flash and (optionally) read back one copy. Returns False on a read back mismatch."""
    try:
        sub(0, 1, "Sending the %s for %s." % (what, name))
        session.download(data, progress=lambda d, t: sub(d, t, "Sending the %s for %s." % (what, name)))
        sub(1, 1, "Writing %s." % name)
        session.flash(name, FLASH_TIMEOUT_MS)
    except Exception as exc:
        raise _WriteStepFailed(name, "writing", exc) from exc
    done_steps[0] += 1
    if not verify:
        return True
    try:
        same = partition_matches(session, name, data, on_bytes=lambda d, t: sub(d, t, "Checking %s." % name))
    except Exception as exc:
        raise _WriteStepFailed(name, "checking", exc) from exc
    done_steps[0] += 1
    return same


def _where_things_stand(failed: str, planned: List[str], results: Dict[str, str], running: List[str],
                        model: ConsoleModel) -> str:
    """The rest of the sentence after a copy failed: the state of every other copy and what to do.

    The copies reported are the console's LK copies and, when this write included the secure
    firmware, its copies too. The console is promised to start as before only when the failed copy
    is not one it runs from, the LK copy it runs from was never touched, and the secure firmware
    copy it runs from was never touched or was written and read back. Whether the preloader falls
    back to the other copy when the one it starts from is bad is not known, so a good other copy is
    reported, not relied on."""
    others = [p for p in model.tee_partitions if p in planned and p != failed]
    others += [p for p in model.partitions if p != failed]
    # in the order they were written, then those never reached
    others.sort(key=lambda p: list(results).index(p) if p in results else len(results))
    checked = [p for p in others if results.get(p) == "verified"]
    written = [p for p in others if results.get(p) == "written"]
    untouched = [p for p in others if p not in results]
    recover = ("If the console does not start, install GammaOS Pico again with the flash package (%s) and "
               "SP Flash Tool v5 (see the wiki's Installing page)." % model.flash_package)

    run_lk = [p for p in running if p in model.partitions]
    run_tee = [p for p in running if p in model.tee_partitions and p in planned]
    safe = (bool(run_lk) and failed not in running and all(p in untouched for p in run_lk)
            and all(p in untouched or p in checked for p in run_tee))
    state: List[str] = []
    if checked:
        state.append("%s written and checked." % _was(names_text(checked), len(checked)))
    if written:
        state.append("%s written." % _was(names_text(written), len(written)))
    if safe:
        keep = [p for p in untouched if p in running]
        state.append("%s not touched, so the console still starts from %s as before. Try the update again."
                     % (_was(names_text(keep), len(keep)), "it" if len(keep) == 1 else "them"))
        return " ".join(state)
    if untouched:
        state.append("%s not touched." % _was(names_text(untouched), len(untouched)))
    state.append("Do not turn the console off: try the update again, or restore a backup. %s" % recover)
    return " ".join(state)


def _short_reason(exc: BaseException) -> str:
    if isinstance(exc, LinkError):
        return {"timeout": "the console stopped answering",
                "gone": "the console was disconnected",
                "busy": "another program took the console",
                "access": "the console could not be opened",
                "driver": "the console could not be opened"}.get(exc.kind, "the USB connection failed")
    if isinstance(exc, FastbootFail):
        return "the console said: %s" % (exc.text.strip() or "no reason given")
    if isinstance(exc, ProtocolError):
        return "the console sent an unexpected answer"
    return str(exc) or exc.__class__.__name__


def _step_failed_text(failed: _WriteStepFailed, planned: List[str], results: Dict[str, str],
                      running: List[str], model: ConsoleModel) -> str:
    cause = failed.cause
    state = _where_things_stand(failed.name, planned, results, running, model)
    if isinstance(cause, FastbootFail) and "low power" in cause.text.lower():
        return ("The console's battery is too low to write safely, so %s was not written; %s Charge it first."
                % (failed.name, state))
    if failed.what == "writing":
        head = "Writing %s did not finish" % failed.name
    else:
        head = "%s was written but could not be read back to check it" % failed.name
    return "%s (%s); %s" % (head, _short_reason(cause), state)
