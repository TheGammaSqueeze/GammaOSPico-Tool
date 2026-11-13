"""Backing up, updating and restoring the console's bootloader (lk_a and lk_b) over GammaOS Pico's
own fastboot (hold SELECT + START + R while turning the console on).

Everything here blocks and is meant to run on a worker thread; nothing imports tkinter.
"""

from __future__ import annotations

from .backup import (backup_lk, default_backup_root, has_backup, list_backups, read_backup)
from .device import ConsoleLink, Progress, ScanReport, find_consoles, scan, wait_for_console
from .drivers import driver_help
from .errors import ConsoleCancelled, ConsoleError, ConsoleTooOld
from .flash import write_lk
from .lkimage import LK_PARTITION_SIZE, LkImageInfo, check_lk_image

__all__ = [
    "ConsoleError", "ConsoleCancelled", "ConsoleTooOld",
    "ConsoleLink", "LkImageInfo", "ScanReport", "Progress",
    "find_consoles", "scan", "wait_for_console",
    "check_lk_image", "backup_lk", "list_backups", "has_backup", "read_backup", "write_lk",
    "driver_help", "default_backup_root", "LK_PARTITION_SIZE",
]
