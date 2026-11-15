"""The bootloader images in a flash package: gammaos-pico-kpa-<version>-flash.zip for the Konkr
Pocket Advance, gammaos-pico-block-<version>-flash.zip for the Konkr Pocket Block.

The package's fastboot/ folder holds lk_<console>_gammaos.img (GammaOS Pico's LK) and
lk_<console>_stock.img (the console's stock LK), listed with their sha256 in the package's own
SHA256SUMS. Which console a package is for comes from its top folder's name, or from the image
names in it when the folder was renamed.
"""

import hashlib
import re
import zipfile

from ..console.models import MODELS
from .install import _package_top

_TOP_RE = re.compile(r"^(%s)-(.+)-flash/?$" % "|".join(re.escape(m.package_prefix) for m in MODELS))


class FlashPackageError(Exception):
    pass


def _expected_names():
    return " or ".join(m.flash_package for m in MODELS)


def read_images(zip_path, model=None):
    """{"gammaos": bytes, "stock": bytes (when present), "version": str or None, "model":
    ConsoleModel}, each image checked against the package's SHA256SUMS when it lists it. With
    `model` (the console it is meant for), a package for the other console is refused."""
    try:
        z = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as e:
        raise FlashPackageError("This is not a readable zip file (%s)." % e)
    with z:
        names = z.namelist()
        top = _package_top(names)
        sums = {}
        if top + "SHA256SUMS" in names:
            for line in z.read(top + "SHA256SUMS").decode("utf-8", "replace").splitlines():
                parts = line.split(None, 1)
                if len(parts) == 2:
                    sums[parts[1].strip().lstrip("*").lstrip("./")] = parts[0].lower()
        found = None
        version = None
        m = _TOP_RE.match(top)
        if m:
            found = next(x for x in MODELS if x.package_prefix == m.group(1))
            version = m.group(2)
        else:
            have = [x for x in MODELS if top + x.flash_gammaos in names]
            if len(have) == 1:
                found = have[0]
        if found is None or top + found.flash_gammaos not in names:
            raise FlashPackageError("This is not a GammaOS Pico flash package (it has no %s). Use %s."
                                    % (" or ".join(x.flash_gammaos for x in MODELS), _expected_names()))
        if model is not None and found is not model:
            raise FlashPackageError("This is the %s's flash package, and this console is a %s: use %s."
                                    % (found.name, model.name, model.flash_package))
        out = {"version": version, "model": found}
        for key, rel in (("gammaos", found.flash_gammaos), ("stock", found.flash_stock)):
            if top + rel not in names:
                continue
            data = z.read(top + rel)
            want = sums.get(rel)
            if want and hashlib.sha256(data).hexdigest() != want:
                raise FlashPackageError("%s in the package does not match the package's SHA256SUMS: the "
                                        "package is damaged." % rel)
            out[key] = data
        return out
