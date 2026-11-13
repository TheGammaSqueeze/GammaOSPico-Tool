"""Errors raised by the console backend.

Every message is one plain sentence for the person using the tool: what happened and what to do.
"""


class ConsoleError(Exception):
    """Something went wrong talking to the console or with its files. str(e) is shown as is."""


class ConsoleCancelled(ConsoleError):
    """The person pressed Cancel before anything was written."""


class ConsoleTooOld(ConsoleError):
    """The console's bootloader predates `oem part-read`, so it cannot be read over USB."""


TOO_OLD_TO_READ = (
    "This console's bootloader is too old to be backed up over USB; update it first, or keep the "
    "backup the flash package method made."
)
