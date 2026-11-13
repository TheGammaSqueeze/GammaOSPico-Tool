"""Synthetic GammaOS Pico card packages, built like the release script builds them."""

import zipfile


def make_package(path, version="2026.12.01", top=True, extra=None, menu=b"MENU-NEW"):
    files = {
        "README.txt": ("GammaOS Pico for the Konkr Pocket Advance: the microSD card, release %s (lk-mt6785 abc1234)\n\nCopy it.\n"
                       % version).encode(),
        "gammaos/menu.pack": menu,
        "gammaos/boot.cfg": b"boot=sd\n",
        "gammaos/lang-fr-960x640.pack": b"FR",
        "cores/armgba-pico.blob": b"CORE" * 100,
        "bios/README.txt": b"Put your BIOS here.\n",
        "roms/gba/Collie Defense.gba": b"DEMO",
        "roms/gba/boxart/Collie Defense.ART": b"GART\x01\x00\x00\x00\x01\x00\x01\x00\x00\x00",
        "roms/gb/Tobu.gb": b"DEMO2",
        "licences/games/CREDITS.txt": b"credits",
    }
    if extra:
        files.update(extra)
    pre = "gammaos-pico-kpa-%s-sdcard/" % version if top else ""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        if pre:
            z.writestr(zipfile.ZipInfo(pre), b"")
        for n, d in sorted(files.items()):
            z.writestr(pre + n, d)
    return path


def make_flash_package(path, version="2026.12.01", gammaos=b"LKIMG:gammaos-pico:1.0.0:" + b"\0" * 64,
                       stock=b"STOCKLK" * 100, bad_sum=False):
    import hashlib
    pre = "gammaos-pico-kpa-%s-flash/" % version
    files = {"fastboot/lk_kpa_gammaos.img": gammaos, "fastboot/lk_kpa_stock.img": stock, "README.txt": b"flash"}
    sums = "".join("%s  ./%s\n" % (hashlib.sha256(d if not bad_sum else b"x").hexdigest(), n) for n, d in sorted(files.items()))
    with zipfile.ZipFile(path, "w") as z:
        for n, d in files.items():
            z.writestr(pre + n, d)
        z.writestr(pre + "SHA256SUMS", sums)
    return path
