"""Synthetic GammaOS Pico card packages, built like the release script builds them."""

import zipfile


def make_package(path, version="2026.12.01", top=True, extra=None, menu=b"MENU-NEW", console="kpa"):
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
    pre = "gammaos-pico-%s-%s-sdcard/" % (console, version) if top else ""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        if pre:
            z.writestr(zipfile.ZipInfo(pre), b"")
        for n, d in sorted(files.items()):
            z.writestr(pre + n, d)
    return path


def make_flash_package(path, version="2026.12.01", gammaos=None, stock=None, bad_sum=False, console="kpa",
                       top=True, tee_gammaos=None, tee_stock=None):
    """console="block": the Konkr Pocket Block's package (lk_block_*.img, the MT6768 scatter).
    With tee_gammaos / tee_stock it is a 1.2.0 or later package, which also carries the secure
    firmware (tee_<console>_*.img); without them, an older one."""
    import hashlib
    if gammaos is None:
        gammaos = b"LKIMG:gammaos-pico:1.0.0:" + (b"block:" if console == "block" else b"") + b"\0" * 64
    if stock is None:
        stock = (b"STOCKBLK" if console == "block" else b"STOCKLK") * 100
    pre = "gammaos-pico-%s-%s-flash/" % (console, version) if top else "renamed/"
    scatter = "MT6768_Android_scatter.txt" if console == "block" else "MT6785_Android_scatter.txt"
    files = {"fastboot/lk_%s_gammaos.img" % console: gammaos, "fastboot/lk_%s_stock.img" % console: stock,
             "README.txt": b"flash", "spflash/gammaos/" + scatter: b"scatter", "spflash/stock/" + scatter: b"scatter"}
    if tee_gammaos is not None:
        files["fastboot/tee_%s_gammaos.img" % console] = tee_gammaos
    if tee_stock is not None:
        files["fastboot/tee_%s_stock.img" % console] = tee_stock
    sums = "".join("%s  ./%s\n" % (hashlib.sha256(d if not bad_sum else b"x").hexdigest(), n) for n, d in sorted(files.items()))
    with zipfile.ZipFile(path, "w") as z:
        for n, d in files.items():
            z.writestr(pre + n, d)
        z.writestr(pre + "SHA256SUMS", sums)
    return path
