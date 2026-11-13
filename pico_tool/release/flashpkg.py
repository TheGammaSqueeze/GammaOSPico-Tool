"""The bootloader images in a flash package (gammaos-pico-kpa-<version>-flash.zip).

The package's fastboot/ folder holds lk_kpa_gammaos.img (GammaOS Pico's LK) and lk_kpa_stock.img
(the console's stock LK), listed with their sha256 in the package's own SHA256SUMS.
"""

import hashlib
import zipfile

from .install import _package_top

GAMMAOS_IMG = "fastboot/lk_kpa_gammaos.img"
STOCK_IMG = "fastboot/lk_kpa_stock.img"


class FlashPackageError(Exception):
    pass


def read_images(zip_path):
    """{"gammaos": bytes, "stock": bytes (when present), "version": str or None}, each image checked
    against the package's SHA256SUMS when it lists it."""
    try:
        z = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as e:
        raise FlashPackageError("This is not a readable zip file (%s)." % e)
    with z:
        names = z.namelist()
        top = _package_top(names)
        sums = {}
        if top + "SHA256SUMS" in names:
            sums = {}
            for line in z.read(top + "SHA256SUMS").decode("utf-8", "replace").splitlines():
                parts = line.split(None, 1)
                if len(parts) == 2:
                    sums[parts[1].strip().lstrip("*").lstrip("./")] = parts[0].lower()
        out = {"version": None}
        m = None
        import re
        for n in names:
            m = re.search(r"gammaos-pico-kpa-(.+)-flash/?$", n.split("/")[0] + "/")
            if m:
                out["version"] = m.group(1)
                break
        for key, rel in (("gammaos", GAMMAOS_IMG), ("stock", STOCK_IMG)):
            if top + rel not in names:
                continue
            data = z.read(top + rel)
            want = sums.get(rel)
            if want and hashlib.sha256(data).hexdigest() != want:
                raise FlashPackageError("%s in the package does not match the package's SHA256SUMS: the "
                                        "package is damaged." % rel)
            out[key] = data
        if "gammaos" not in out:
            raise FlashPackageError("This is not a GammaOS Pico flash package (it has no %s). Use "
                                    "gammaos-pico-kpa-<version>-flash.zip." % GAMMAOS_IMG)
        return out
