"""The consoles GammaOS Pico runs on, and everything the tool needs to tell them apart.

Both reach the tool the same way: GammaOS Pico's own fastboot (hold SELECT + START + R while turning
the console on), with the same MediaTek USB ids. What differs:

  Konkr Pocket Advance (MT6785)   getvar:product "k85v1_64_kpa"
  Konkr Pocket Block (MT8786)     getvar:product "k68v1_64_block"

On both, the bootloader is written to lk_a and lk_b. From GammaOS Pico 1.2.0 a release also carries
the console's secure firmware (the "tee", with a small change that lets the bootloader use the other
CPU cores), written to tee_a and tee_b. The Pocket Block's factory leaves its _b copies empty and its
preloader starts lk_a; up to 1.1.x the tool wrote lk_a alone there, so older Pocket Block backups
hold lk_a only.

An LK image says which console it is for through its display driver string and the project at the
start of its build tag ("<project>-<commit>-<date>-<date>"). An image is only ever written to the
console it names.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class ConsoleModel:
    key: str                          # "kpa" | "block": settings, backup.json, package names
    name: str                         # "Konkr Pocket Advance"
    pico_product: str                 # what GammaOS Pico's fastboot answers to getvar:product
    stock_products: Tuple[str, ...]   # what the stock LK's fastboot answers (a stock console is refused)
    partitions: Tuple[str, ...]       # the LK copies the tool reads and writes, in that order
    tee_partitions: Tuple[str, ...]   # the secure firmware copies, written when a package has them
    panel_driver: bytes               # the display driver name every LK for this console carries
    pico_project: bytes               # build tag project of GammaOS Pico's LK
    stock_projects: Tuple[bytes, ...]  # build tag project(s) of the stock LK
    package_prefix: str               # "gammaos-pico-kpa": <prefix>-<version>-sdcard.zip, -flash.zip
    flash_gammaos: str                # GammaOS Pico's LK inside the flash package
    flash_stock: str                  # the stock LK inside the flash package
    flash_tee_gammaos: str            # GammaOS Pico's secure firmware inside the flash package (1.2.0 on)
    flash_tee_stock: str              # the stock secure firmware inside the flash package (1.2.0 on)
    scatter: str                      # SP Flash Tool's scatter file in the flash package's spflash/
    lk_size: int = 0x200000           # each LK partition, from the stock scatter
    # the copies every backup of this console holds, whichever version of the tool made it; a
    # backup may hold more (the Pocket Block's lk_b, the secure firmware), used when recorded
    backup_always: Tuple[str, ...] = ("lk_a", "lk_b")

    @property
    def partitions_text(self) -> str:
        """"lk_a and lk_b"."""
        return names_text(self.partitions)

    @property
    def tee_partitions_text(self) -> str:
        """"tee_a and tee_b"."""
        return names_text(self.tee_partitions)

    @property
    def all_partitions(self) -> Tuple[str, ...]:
        """Everything the tool may write on this console: the LK copies, then the secure firmware's."""
        return self.partitions + self.tee_partitions

    @property
    def card_package(self) -> str:
        return self.package_prefix + "-<version>-sdcard.zip"

    @property
    def flash_package(self) -> str:
        return self.package_prefix + "-<version>-flash.zip"

    @property
    def windows_tools(self) -> str:
        return self.package_prefix + "-windows-tools.zip"


KPA = ConsoleModel(
    key="kpa",
    name="Konkr Pocket Advance",
    pico_product="k85v1_64_kpa",
    stock_products=("k85v1_64",),
    partitions=("lk_a", "lk_b"),
    tee_partitions=("tee_a", "tee_b"),
    panel_driver=b"st7123p_hd640_boe_lcm_drv",
    pico_project=b"k85v1_64_kpa",
    stock_projects=(b"k85v1_64",),
    package_prefix="gammaos-pico-kpa",
    flash_gammaos="fastboot/lk_kpa_gammaos.img",
    flash_stock="fastboot/lk_kpa_stock.img",
    flash_tee_gammaos="fastboot/tee_kpa_gammaos.img",
    flash_tee_stock="fastboot/tee_kpa_stock.img",
    scatter="MT6785_Android_scatter.txt",
)

BLOCK = ConsoleModel(
    key="block",
    name="Konkr Pocket Block",
    pico_product="k68v1_64_block",
    stock_products=("m8695_bw01",),
    partitions=("lk_a", "lk_b"),
    tee_partitions=("tee_a", "tee_b"),
    panel_driver=b"jd9168s_960x720_dsi_vdo_m8695_bw01_drv",
    pico_project=b"k68v1_64_block",
    stock_projects=(b"m8695_bw01",),
    package_prefix="gammaos-pico-block",
    flash_gammaos="fastboot/lk_block_gammaos.img",
    flash_stock="fastboot/lk_block_stock.img",
    flash_tee_gammaos="fastboot/tee_block_gammaos.img",
    flash_tee_stock="fastboot/tee_block_stock.img",
    scatter="MT6768_Android_scatter.txt",
    backup_always=("lk_a",),
)

def names_text(names) -> str:
    """"lk_a", "lk_a and lk_b", "tee_a, tee_b and lk_a": a list of partition names in a sentence."""
    names = list(names)
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def lk_names(names, model: "ConsoleModel") -> list:
    """Those of `names` that are this console's LK copies, in the model's order."""
    return [p for p in model.partitions if p in names]


def tee_names(names, model: "ConsoleModel") -> list:
    """Those of `names` that are this console's secure firmware copies, in the model's order."""
    return [p for p in model.tee_partitions if p in names]


def targets_text(names, model: "ConsoleModel") -> str:
    """Where a set of images goes, for a question or a summary: "lk_a and lk_b", or
    "lk_a and lk_b, and the secure firmware (tee_a and tee_b)"."""
    text = names_text(lk_names(names, model))
    tee = tee_names(names, model)
    if tee:
        text += (", and " if text else "") + "the secure firmware (%s)" % names_text(tee)
    return text


def results_lines(results) -> list:
    """One line per partition a write touched, in the order they were written:
    "tee_b: written and checked" or "tee_b: written, not checked (...)"."""
    lines = []
    for name, state in (results or {}).items():
        if state == "verified":
            lines.append("%s: written and checked" % name)
        else:
            lines.append("%s: written, not checked (the console's bootloader cannot read it back)" % name)
    return lines


MODELS = (KPA, BLOCK)
DEFAULT = KPA
BY_KEY = {m.key: m for m in MODELS}

# "Konkr Pocket Advance or Konkr Pocket Block", for messages about an unknown device or image
ANY_NAME = " or ".join(m.name for m in MODELS)


def by_key(key: Optional[str], default: Optional[ConsoleModel] = DEFAULT) -> Optional[ConsoleModel]:
    """The model for a key ("kpa", "block"); `default` for an empty or unknown one."""
    return BY_KEY.get((key or "").strip().lower(), default)


def by_pico_product(product: str) -> Optional[ConsoleModel]:
    for m in MODELS:
        if product == m.pico_product:
            return m
    return None


def by_stock_product(product: str) -> Optional[ConsoleModel]:
    for m in MODELS:
        if product in m.stock_products:
            return m
    return None
