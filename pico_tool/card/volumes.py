"""The removable volumes of this computer that could be the console's microSD card.

Windows: the drive letters (GetLogicalDrives), their type, label and file system.
macOS:   /Volumes without the start-up disk, the file system from the mount table.
Linux:   the mount table's entries under /media, /run/media and /mnt (lsblk adds FAT32 or FAT16
         and the label when it is there).

The console reads FAT32 and exFAT cards (FAT16 too, for small ones); anything else is shown with a
warning, and the tool never formats a card.
"""

import os
import shutil
import subprocess
import sys

SUPPORTED = ("FAT32", "EXFAT", "FAT16", "FAT", "FAT12")


class Volume(object):
    __slots__ = ("path", "label", "fs", "total", "free", "removable", "has_pico", "version", "device")

    def __init__(self, path, label="", fs="", removable=True, device=""):
        self.path = path
        self.label = label
        self.fs = fs or ""
        self.total = 0
        self.free = 0
        self.removable = removable
        self.has_pico = False
        self.version = None
        self.device = device

    @property
    def fs_ok(self):
        return self.fs.upper() in SUPPORTED

    @property
    def fs_label(self):
        f = self.fs.upper()
        if f == "EXFAT":
            return "exFAT"
        if f == "VFAT":
            return "FAT"
        return self.fs or "unknown"

    def key(self):
        return (self.path, self.label, self.fs, self.has_pico, self.version, self.total)

    def __repr__(self):
        return "<Volume %s %r %s>" % (self.path, self.label, self.fs)


def fill(vol):
    """Space, and whether the folder already holds GammaOS Pico (and which version)."""
    from .layout import card_version, has_pico
    try:
        du = shutil.disk_usage(vol.path)
        vol.total, vol.free = du.total, du.free
    except OSError:
        pass
    vol.has_pico = has_pico(vol.path)
    vol.version = card_version(vol.path) if vol.has_pico else None
    return vol


def folder_volume(path):
    """A Volume for any folder the user picks (its file system guessed where possible)."""
    path = os.path.abspath(path)
    vol = Volume(path, os.path.basename(path.rstrip("\\/")) or path, "", removable=False)
    for v in list_volumes(fill_info=False):
        if os.path.normcase(path).startswith(os.path.normcase(v.path)):
            vol.fs = v.fs
    if not vol.fs:
        vol.fs = _fs_of_path(path)
    return fill(vol)


def list_volumes(fill_info=True):
    try:
        if sys.platform == "win32":
            vols = _windows()
        elif sys.platform == "darwin":
            vols = _macos()
        else:
            vols = _linux()
    except Exception:
        vols = []
    if fill_info:
        for v in vols:
            fill(v)
    return vols


# ---- Windows -------------------------------------------------------------------------------------

def _windows():
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    DRIVE_REMOVABLE, DRIVE_FIXED = 2, 3
    k32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    sysdrive = (os.environ.get("SystemDrive") or "C:").upper().rstrip("\\") + "\\"
    out = []
    old = k32.SetErrorMode(1)       # no "insert a disk" boxes for empty card readers
    try:
        mask = k32.GetLogicalDrives()
        for i in range(26):
            if not mask & (1 << i):
                continue
            root = "%s:\\" % chr(65 + i)
            kind = k32.GetDriveTypeW(root)
            if kind not in (DRIVE_REMOVABLE, DRIVE_FIXED) or root.upper() == sysdrive:
                continue
            label = ctypes.create_unicode_buffer(261)
            fsname = ctypes.create_unicode_buffer(261)
            serial = wintypes.DWORD()
            maxlen = wintypes.DWORD()
            flags = wintypes.DWORD()
            ok = k32.GetVolumeInformationW(root, label, 261, ctypes.byref(serial), ctypes.byref(maxlen),
                                           ctypes.byref(flags), fsname, 261)
            if not ok:
                continue        # an empty card reader
            fs = fsname.value
            # fixed disks only when they look like a card (some built-in readers show as fixed)
            if kind == DRIVE_FIXED and fs.upper() not in ("FAT32", "EXFAT", "FAT"):
                continue
            out.append(Volume(root, label.value, fs, removable=(kind == DRIVE_REMOVABLE)))
    finally:
        k32.SetErrorMode(old)
    return out


# ---- macOS ---------------------------------------------------------------------------------------

def _mount_table_macos():
    """{mount point: (device, fs type)} from `mount`."""
    try:
        text = subprocess.run(["mount"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=5).stdout.decode("utf-8", "replace")
    except Exception:
        return {}
    return parse_mount_output(text)


def parse_mount_output(text):
    out = {}
    for line in text.splitlines():
        # /dev/disk4s1 on /Volumes/GAMMAOS (msdos, local, nodev, nosuid, noowners)
        if " on " not in line or "(" not in line:
            continue
        dev, rest = line.split(" on ", 1)
        mnt, opts = rest.rsplit(" (", 1)
        out[mnt] = (dev, opts.split(",", 1)[0].strip(" )"))
    return out


def _macos():
    table = _mount_table_macos()
    out = []
    try:
        names = sorted(os.listdir("/Volumes"))
    except OSError:
        return out
    for n in names:
        p = os.path.join("/Volumes", n)
        if not os.path.isdir(p) or os.path.realpath(p) == "/" or n.startswith("."):
            continue
        dev, fs = table.get(p, ("", ""))
        if not dev.startswith("/dev/"):
            continue        # network shares and disk images of no interest
        fsu = {"msdos": "FAT", "exfat": "exFAT"}.get(fs.lower(), fs)
        out.append(Volume(p, n, fsu, removable=True, device=dev))
    return out


# ---- Linux ---------------------------------------------------------------------------------------

LINUX_ROOTS = ("/media/", "/run/media/", "/mnt/")
LINUX_SKIP_FS = ("9p", "drvfs", "tmpfs", "overlay", "proc", "sysfs", "squashfs", "nfs", "nfs4", "cifs",
                 "smb3", "autofs", "fuse.sshfs", "fuse.portal", "fuse.gvfsd-fuse", "virtiofs")


def _unescape(s):
    return s.replace("\\040", " ").replace("\\011", "\t").replace("\\012", "\n").replace("\\134", "\\")


def _lsblk():
    """{device path: (fs type, fs version, label)} when lsblk is there."""
    out = {}
    exe = shutil.which("lsblk")
    if not exe:
        return out
    try:
        r = subprocess.run([exe, "-P", "-p", "-o", "NAME,FSTYPE,FSVER,LABEL"], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=5)
        text = r.stdout.decode("utf-8", "replace")
        if r.returncode != 0:
            r = subprocess.run([exe, "-P", "-p", "-o", "NAME,FSTYPE,LABEL"], stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, timeout=5)
            text = r.stdout.decode("utf-8", "replace")
    except Exception:
        return out
    import shlex
    for line in text.splitlines():
        kv = {}
        for tok in shlex.split(line):
            if "=" in tok:
                k, v = tok.split("=", 1)
                kv[k] = v
        if kv.get("NAME"):
            out[kv["NAME"]] = (kv.get("FSTYPE", ""), kv.get("FSVER", ""), kv.get("LABEL", ""))
    return out


def _linux():
    try:
        with open("/proc/mounts", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    return parse_proc_mounts(text, _lsblk)


def parse_proc_mounts(text, lsblk=lambda: {}):
    """The card-like volumes of a /proc/mounts text (lsblk() gives {device: (type, version, label)})."""
    out = []
    lines = text.splitlines()
    blk = None
    seen = set()
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        dev, mnt, fs = _unescape(parts[0]), _unescape(parts[1]), parts[2]
        if not mnt.startswith(LINUX_ROOTS) or fs in LINUX_SKIP_FS or mnt in seen:
            continue
        if mnt.startswith("/mnt/wsl") or (mnt.startswith("/mnt/") and len(mnt) == 6 and fs in ("9p", "drvfs")):
            continue
        seen.add(mnt)
        label = os.path.basename(mnt)
        fsname = {"vfat": "FAT", "msdos": "FAT", "exfat": "exFAT"}.get(fs, fs)
        if dev.startswith("/dev/"):
            if blk is None:
                blk = lsblk()
            info = blk.get(dev) or blk.get(os.path.realpath(dev))
            if info:
                t, ver, lab = info
                if t == "vfat":
                    fsname = ver if ver in ("FAT32", "FAT16", "FAT12") else "FAT"
                elif t == "exfat":
                    fsname = "exFAT"
                elif t and fs == "fuseblk":
                    fsname = t
                if lab:
                    label = lab
        out.append(Volume(mnt, label, fsname, removable=True, device=dev))
    return out


def _fs_of_path(path):
    if sys.platform.startswith("linux"):
        best = ("", "")
        try:
            with open("/proc/mounts", encoding="utf-8", errors="replace") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 3:
                        mnt = _unescape(parts[1])
                        if (path == mnt or path.startswith(mnt.rstrip("/") + "/")) and len(mnt) >= len(best[0]):
                            best = (mnt, parts[2])
        except OSError:
            pass
        return {"vfat": "FAT", "msdos": "FAT", "exfat": "exFAT"}.get(best[1], best[1])
    if sys.platform == "darwin":
        best = ("", "")
        for mnt, (_dev, fs) in _mount_table_macos().items():
            if (path == mnt or path.startswith(mnt.rstrip("/") + "/")) and len(mnt) >= len(best[0]):
                best = (mnt, fs)
        return {"msdos": "FAT", "exfat": "exFAT"}.get(best[1].lower(), best[1])
    if sys.platform == "win32":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            root = ctypes.create_unicode_buffer(261)
            if not k32.GetVolumePathNameW(path, root, 261):
                return ""
            fsname = ctypes.create_unicode_buffer(261)
            if k32.GetVolumeInformationW(root.value, None, 0, None, None, None, fsname, 261):
                return fsname.value
        except Exception:
            pass
    return ""
