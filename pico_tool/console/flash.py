"""Writing the console's LK copies over GammaOS Pico's fastboot, with a read back check.

Which copies: lk_a and lk_b on the Konkr Pocket Advance, lk_a alone on the Konkr Pocket Block (the
factory never writes its _b copies and the preloader starts lk_a). See models.py.

Order on the Pocket Advance: the copy the console is NOT running from goes first (getvar:current-slot), and is read
back before the running copy is touched. If anything goes wrong with the first copy, the copy the
console starts from is still the old, working one. Whether the preloader would fall back to the
other copy when the one it starts from is bad is not known, so the messages never count on it.
"""

from __future__ import annotations

import threading
from typing import Callable, Dict, List, Optional

from .backup import has_backup, operation_error, default_backup_root
from .device import ConsoleLink, open_console, partition_size, read_partition, supports_part_read
from .errors import ConsoleCancelled, ConsoleError, ConsoleTooOld
from .fastboot import FLASH_TIMEOUT_MS, FastbootFail, FastbootSession, LinkError, ProtocolError, sha256_hex
from .lkimage import check_lk_image
from .models import ConsoleModel

Progress = Callable[[Optional[float], str], None]


def _write_order(names: List[str], current: Optional[str]) -> List[str]:
    """The running slot last; lk_a before lk_b when the running slot is unknown."""
    names = sorted(names)
    running = "lk_" + current if current in ("a", "b") else None
    if running in names:
        names.remove(running)
        names.append(running)
    return names


def _other(name: str, model: ConsoleModel) -> Optional[str]:
    """The other copy this console's bootloader is written to, or None when it has only one."""
    others = [p for p in model.partitions if p != name]
    return others[0] if others else None


def write_lk(link: ConsoleLink, images: Dict[str, bytes], cancel: Optional[threading.Event],
             progress: Optional[Progress], verify: bool = True,
             backup_root: Optional[str] = None) -> Dict[str, str]:
    """Write lk_a and/or lk_b (only those the console's model writes: lk_a alone on the Pocket
    Block). Every image must pass check_lk_image for this console. Cancel is honoured only
    before the first byte is written. With verify, each copy is read back and compared by sha256.

    Returns {"lk_a": "verified" | "written", ...}; "written" means the LK could not read it back.
    A console whose LK cannot be read over USB (older than 1.0.0) is written only when
    `backup_root` (default_backup_root() when None) already holds a backup of it."""
    if not isinstance(images, dict) or not images:
        raise ConsoleError("No bootloader image was given, so nothing was written.")
    model = link.model
    bad = [k for k in images if k not in model.partitions]
    if bad:
        if len(model.partitions) == 1:
            raise ConsoleError("The %s's bootloader is written to %s only, not %s; nothing was written."
                               % (model.name, model.partitions[0], ", ".join(map(str, bad))))
        raise ConsoleError("Only %s can be written, not %s; nothing was written."
                           % (model.partitions_text, ", ".join(map(str, bad))))
    for name in sorted(images):
        data = images[name]
        if not isinstance(data, (bytes, bytearray)):
            raise ConsoleError("The image for %s is not a file's contents; nothing was written." % name)
        info = check_lk_image(bytes(data), model)
        if info.problems:
            raise ConsoleError("The image for %s cannot be written: %s Nothing was written."
                               % (name, info.problems[0].rstrip(".") + "."))
    if cancel is not None and cancel.is_set():
        raise ConsoleCancelled("Cancelled; nothing was written.")

    def say(frac: Optional[float], msg: str) -> None:
        if progress:
            progress(frac, msg)

    say(0.0, "Opening the console.")
    results: Dict[str, str] = {}
    running: Optional[str] = None
    try:
        with open_console(link) as session:
            try:
                can_read = supports_part_read(session)
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

            order = _write_order(list(images), current)
            do_verify = verify and can_read
            steps = len(order) * (2 if do_verify else 1)
            done_steps = [0]

            def sub(done: int, total: int, label: str) -> None:
                if total:
                    say((done_steps[0] + done / total) / steps * 0.98, label)

            running = "lk_" + current if current in ("a", "b") else None
            for name in order:
                data = bytes(images[name])
                if _write_one(session, name, data, do_verify, sub, done_steps):
                    results[name] = "verified" if do_verify else "written"
                    continue
                # the read back did not match: write it once more, then give up and say where things are
                done_steps[0] -= 2
                if _write_one(session, name, data, do_verify, sub, done_steps):
                    results[name] = "verified"
                    continue
                raise ConsoleError("The copy written to %s did not read back correctly; %s"
                                   % (name, _where_things_stand(name, results, running, model)))
    except ConsoleError:
        raise
    except _WriteStepFailed as failed:
        raise ConsoleError(_step_failed_text(failed, results, running, model)) from failed.cause
    except Exception as exc:
        raise operation_error(exc, "while writing the bootloader") from exc

    if verify and not all(v == "verified" for v in results.values()):
        say(1.0, "Written. This bootloader is too old to read the copies back, so they were not checked; "
                 "the console accepted every write.")
    else:
        say(1.0, "The bootloader was written%s." % (" and checked" if verify else ""))
    return results


class _WriteStepFailed(Exception):
    def __init__(self, name: str, what: str, cause: BaseException):
        super().__init__("%s %s" % (what, name))
        self.name = name
        self.what = what
        self.cause = cause


def _write_one(session: FastbootSession, name: str, data: bytes, verify: bool, sub, done_steps) -> bool:
    """Download, flash and (optionally) read back one copy. Returns False on a read back mismatch."""
    try:
        sub(0, 1, "Sending the bootloader for %s." % name)
        session.download(data, progress=lambda d, t: sub(d, t, "Sending the bootloader for %s." % name))
        sub(1, 1, "Writing %s." % name)
        session.flash(name, FLASH_TIMEOUT_MS)
    except Exception as exc:
        raise _WriteStepFailed(name, "writing", exc) from exc
    done_steps[0] += 1
    if not verify:
        return True
    try:
        back = read_partition(session, name, limit=len(data),
                              on_bytes=lambda d, t: sub(d, t, "Checking %s." % name))
    except Exception as exc:
        raise _WriteStepFailed(name, "checking", exc) from exc
    done_steps[0] += 1
    return len(back) >= len(data) and sha256_hex(back[:len(data)]) == sha256_hex(data)


def _where_things_stand(failed: str, results: Dict[str, str], running: Optional[str],
                        model: ConsoleModel) -> str:
    """The rest of the sentence after a copy failed: the state of the other copy and what to do.

    Only a copy the console starts from that was never touched is promised to work: whether the
    preloader falls back to the other copy when the one it starts from is bad is not known, so a
    good other copy is reported, not relied on."""
    other = _other(failed, model)
    recover = ("If the console does not start, install GammaOS Pico again with the flash package (%s) and "
               "SP Flash Tool v5 (see the wiki's Installing page)." % model.flash_package)
    if other is None:
        # one copy only (the Pocket Block): the console starts from the one that failed
        return "Do not turn the console off: try the update again, or restore a backup. %s" % recover
    if other == running and other not in results:
        return ("%s was not touched, so the console still starts from it as before. Try the update again."
                % other)
    if results.get(other) == "verified":
        state = "%s was written and checked." % other
    elif results.get(other) == "written":
        state = "%s was written." % other
    else:
        state = "%s was not touched." % other
    return ("%s Do not turn the console off: try the update again, or restore a backup. %s"
            % (state, recover))


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


def _step_failed_text(failed: _WriteStepFailed, results: Dict[str, str], running: Optional[str],
                      model: ConsoleModel) -> str:
    cause = failed.cause
    state = _where_things_stand(failed.name, results, running, model)
    if isinstance(cause, FastbootFail) and "low power" in cause.text.lower():
        return ("The console's battery is too low to write safely, so %s was not written; %s Charge it first."
                % (failed.name, state))
    if failed.what == "writing":
        head = "Writing %s did not finish" % failed.name
    else:
        head = "%s was written but could not be read back to check it" % failed.name
    return "%s (%s); %s" % (head, _short_reason(cause), state)
