"""Per system setup so the tool can open the console's bootloader over USB."""

from __future__ import annotations

import sys

from .usb import KPA_PID, KPA_VID

UDEV_RULE_FILE = "/etc/udev/rules.d/51-gammaos-pico.rules"
UDEV_RULE = (
    '# GammaOS Pico bootloader (fastboot) on the Konkr Pocket Advance\n'
    'SUBSYSTEM=="usb", ATTR{idVendor}=="%04x", ATTR{idProduct}=="%04x", MODE="0660", '
    'GROUP="plugdev", TAG+="uaccess"\n' % (KPA_VID, KPA_PID)
)

_IDS = "%04X:%04X" % (KPA_VID, KPA_PID)

WINDOWS_HELP = """\
Windows needs the WinUSB driver on the console's bootloader before the tool can open it.

First put the console in its bootloader: hold SELECT + START + R while turning it on, then connect
it over USB. It shows up as "Android" (USB ID {ids}).

Option 1, Zadig (quickest):
  1. Download Zadig from https://zadig.akeo.ie and run it.
  2. Options menu: tick "List All Devices".
  3. Pick "Android" in the list and check that the USB ID reads {vid} {pid}.
  4. Set the driver on the right of the arrow to "WinUSB" and click "Install Driver" (or
     "Replace Driver").
  5. Unplug the console, plug it back in, and try again in the tool.

Option 2, the Google USB Driver:
  1. Download the Google USB Driver zip from
     https://developer.android.com/studio/run/win-usb and unpack it.
  2. Open Device Manager, right click "Android" (under Other devices, with a yellow mark) and
     choose "Update driver".
  3. "Browse my computer for drivers", then "Let me pick from a list of available drivers on my
     computer", "Show All Devices", Next, "Have Disk", and point it at android_winusb.inf in the
     unpacked folder.
  4. Choose "Android Bootloader Interface" and confirm the warning about the driver.
  5. Unplug the console, plug it back in, and try again in the tool.

The MediaTek VCOM driver used by SP Flash Tool is for a different connection and does not help
here. If the tool says the console is busy, close fastboot, SP Flash Tool or any other flashing
program and try again.
""".format(ids=_IDS, vid="%04X" % KPA_VID, pid="%04X" % KPA_PID)

LINUX_HELP = """\
Linux needs a udev rule so the tool can open the console without root.

1. Create {path} with this line:

{rule}
2. Reload the rules:
     sudo udevadm control --reload-rules
     sudo udevadm trigger
3. Unplug the console and plug it back in (in its bootloader: hold SELECT + START + R while
   turning it on).

Notes:
  - TAG+="uaccess" gives the person logged in at the screen access on systemd based
    distributions (Fedora, Arch, Ubuntu and most others). Keep the file name starting with a
    number below 73, or the tag is applied too late to count.
  - GROUP="plugdev" covers distributions that use the plugdev group instead. Check that you are
    in it with "groups"; if not, run "sudo usermod -aG plugdev $USER" and log out and back in.
    Where no plugdev group exists, uaccess alone does the job.
  - If your distribution ships android-udev-rules, that package may already cover it, but this
    rule names the console's IDs ({ids}) exactly.
""".format(path=UDEV_RULE_FILE, rule=UDEV_RULE, ids=_IDS)

MAC_HELP = """\
macOS needs no driver: put the console in its bootloader (hold SELECT + START + R while turning
it on) and connect it over USB. If the tool says the console is busy, close fastboot or any other
flashing program and try again. Prefer a direct USB port or a powered hub over an unpowered one.
"""


def driver_help() -> str:
    """Setup text for the system the tool runs on."""
    if sys.platform.startswith("win"):
        return WINDOWS_HELP
    if sys.platform == "darwin":
        return MAC_HELP
    return LINUX_HELP
