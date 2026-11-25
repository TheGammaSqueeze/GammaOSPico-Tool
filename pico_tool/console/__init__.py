"""Backing up, updating and restoring the console's bootloader (lk_a and lk_b) and, from GammaOS Pico
1.2.0, its secure firmware (tee_a and tee_b) over GammaOS Pico's own fastboot (hold SELECT + START +
R while turning the console on), on the Konkr Pocket Advance and the Konkr Pocket Block (models.py).

Everything here blocks and is meant to run on a worker thread; nothing imports tkinter.
"""

from __future__ import annotations

from . import models
from .backup import (backup_lk, backup_model, backup_text, default_backup_root, has_backup, list_backups,
                     read_backup)
from .device import ConsoleLink, Progress, ScanReport, find_consoles, scan, wait_for_console
from .drivers import driver_help
from .errors import ConsoleCancelled, ConsoleError, ConsoleTooOld
from .flash import write_lk
from .lkimage import LK_PARTITION_SIZE, LkImageInfo, TeeImageInfo, check_lk_image, check_tee_image
from .models import BLOCK, KPA, MODELS, ConsoleModel

__all__ = [
    "ConsoleError", "ConsoleCancelled", "ConsoleTooOld",
    "ConsoleLink", "LkImageInfo", "ScanReport", "Progress",
    "find_consoles", "scan", "wait_for_console",
    "check_lk_image", "check_tee_image", "TeeImageInfo", "backup_lk", "backup_text", "list_backups", "has_backup", "read_backup", "write_lk",
    "driver_help", "default_backup_root", "LK_PARTITION_SIZE",
    "models", "ConsoleModel", "MODELS", "KPA", "BLOCK", "backup_model",
]
