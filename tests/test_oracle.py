"""The strongest check of the card scanner: the console's own scanner, built from the bootloader
sources on this computer, lists FAT32 and exFAT images of synthetic cards, and the tool's listing
of the same folders must be the same game for game. Skipped when the sources or the tools to build
and format images are missing (set GAMMAOS_PICO_LK to the lk-mt6785 checkout)."""

import os
import shutil
import subprocess

import pytest

import cardgen
from pico_tool.card.names import fold
from pico_tool.art import store
from pico_tool.card.scanner import scan_card

LK = os.environ.get("GAMMAOS_PICO_LK", "/work/kpa/lk-mt6785")
SRC = os.path.join(LK, "emu", "gba")
HERE = os.path.dirname(os.path.abspath(__file__))
MKFS_FAT = shutil.which("mkfs.fat") or ("/usr/sbin/mkfs.fat" if os.path.exists("/usr/sbin/mkfs.fat") else None)
MKFS_EXFAT = shutil.which("mkfs.exfat") or ("/usr/sbin/mkfs.exfat" if os.path.exists("/usr/sbin/mkfs.exfat") else None)

needs = pytest.mark.skipif(
    not (os.path.isfile(os.path.join(SRC, "sd_fat.c")) and shutil.which("gcc") and MKFS_FAT and MKFS_EXFAT
         and shutil.which("mmd") and cardgen.SEVENZ),
    reason="the console sources, gcc, mkfs.fat, mkfs.exfat, mtools or 7z are not available")


@pytest.fixture(scope="session")
def oracle(tmp_path_factory):
    out = tmp_path_factory.mktemp("oracle")
    inc = out / "inc" / "lib"
    inc.mkdir(parents=True)
    (out / "inc" / "platform.h").write_text("")
    (inc / "zlib.h").write_text("#include <zlib.h>\n")
    exe = str(out / "card_oracle")
    srcs = [os.path.join(SRC, n) for n in ("sd_fat.c", "fat_ro.c", "fat_wr.c", "fat_exfat.c",
                                           "rom_archive.c", "rom_7z.c", "rom_lzma.c", "gba_boxart_sd.c",
                                           "gba_sd_save.c", "gba_boxart.c")]
    subprocess.run(["gcc", "-O1", "-w", "-I", str(out / "inc"), "-I", SRC, "-o", exe,
                    os.path.join(HERE, "oracle", "card_oracle.c")] + srcs + ["-lz"], check=True)
    return exe


def make_image(oracle, tree, path, fs):
    with open(path, "wb") as f:
        f.truncate(1 << 30 if fs == "fat32" else 256 << 20)
    if fs == "fat32":
        subprocess.run([MKFS_FAT, "-F", "32", "-s", "8", path], check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)

        # folders with long names are made with mtools (the console's writer only ever makes short
        # ones), in the folder's own order, then the files go in with the console's writer
        def walk(host, card):
            for de in os.scandir(host):
                if de.name.startswith("."):
                    continue
                if de.is_dir():
                    subprocess.run(["mmd", "-i", path, "::" + card + "/" + de.name], check=True)
                    walk(de.path, card + "/" + de.name)
        walk(tree, "")
    else:
        subprocess.run([MKFS_EXFAT, path], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run([oracle, "put", path, tree], check=True)
    return path


def oracle_list(oracle, image, max_games=None):
    args = [oracle, "list", image] + ([str(max_games)] if max_games else [])
    lines = subprocess.run(args, check=True, stdout=subprocess.PIPE).stdout.decode("latin-1").splitlines()
    head = lines[0].split()
    assert head[0] == "TOTAL"
    games = []
    for line in lines[1:]:
        t, d, arc, mem, size, name = line.split("\t")
        games.append((int(t), d, int(arc), int(mem), int(size), name))
    return int(head[1]), int(head[2]), games


def compare(oracle, tree, image, max_games=32768):
    n, total, want = oracle_list(oracle, image, max_games if max_games != 32768 else None)
    res = scan_card(tree, max_games=max_games)
    got = [(g.type, g.cdir, g.arc, g.member, g.size, g.name) for g in res.games]
    assert (len(got), res.total) == (n, total)
    for a, b in zip(want, got):
        # letter case aside (a name the console's writer kept as a short 8.3 name reads back in
        # capitals), every game must be the same, from the same folder and the same archive member
        assert (a[0], fold(a[1]), a[2], a[3], fold(a[5])) == (b[0], fold(b[1]), b[2], b[3], fold(b[5]))
        if a[2] == 0:
            assert a[4] == b[4]
    return res


@needs
@pytest.mark.parametrize("fs", ["exfat", "fat32"])
def test_archive_card_matches_console(oracle, tmp_path, fs):
    tree = str(tmp_path / "tree")
    cardgen.build_archive_card(tree, str(tmp_path / "work"))
    image = make_image(oracle, tree, str(tmp_path / "card.img"), fs)
    res = compare(oracle, tree, image)
    assert len(res.games) > 40


@needs
def test_cap_and_folder_limit_match_console(oracle, tmp_path):
    tree = tmp_path / "tree"
    for i in range(70):        # more game folders than the console reads
        cardgen.rom(str(tree / ("F%02d" % i) / "gba" / ("Game %03d.gba" % i)), size=600)
    for i in range(900):       # and names found twice
        cardgen.rom(str(tree / "roms" / "gba" / ("Title %04d.gba" % i)), size=600)
        if i % 3 == 0:
            cardgen.rom(str(tree / "gba" / ("title %04d.gba" % i)), size=700)
    image = make_image(oracle, str(tree), str(tmp_path / "card.img"), "exfat")
    res = compare(oracle, str(tree), image)
    assert res.skipped_folders > 0
    compare(oracle, str(tree), image, max_games=500)


@needs
def test_box_art_lookup_matches_console(oracle, tmp_path):
    tree = tmp_path / "tree"
    cardgen.build_archive_card(str(tree), str(tmp_path / "work"))
    cardgen.rom(str(tree / "roms" / "gba" / "Mario vs. Donkey Kong (USA).gba"))
    cardgen.rom(str(tree / "roms" / "snes" / "Home Game.sfc"))
    art = b"GART\x01\x00\x00\x00\x02\x00\x01\x00" + b"\x1f\x00\xe0\x07"

    def put(rel, data=art):
        p = tree / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    put("roms/gba/boxart/Alpha (USA).ART")
    put("roms/gba/boxart/beta.art")                       # any case
    put("roms/gba/boxart/Mario vs.ART")                   # the old cut name
    put("roms/gba/boxart/Three.ART")                      # a ROM in a 7z of several
    put("roms/gba/boxart/Single Zip.ART")                 # an archive of one ROM
    put("roms/gba/boxart/Gamma.gba.ART")                  # a ROM extension left on: not read
    put("roms/gba/boxart/C1.ART", b"GART")                # too short to count
    put("roms/gba/boxart/One.png", b"not yet")            # waiting for the power on: not read yet
    put("gba/boxart/Root Only.ART")
    put("roms/snes/boxart/Super Thing (Europe).ART")      # a game elsewhere, its art at home
    put("roms/snes/boxart/Home Game.ART")
    put("ES-DE/ROMs/snes/boxart/S1.ART")
    image = make_image(oracle, str(tree), str(tmp_path / "card.img"), "exfat")
    lines = subprocess.run([oracle, "art", image], check=True, stdout=subprocess.PIPE).stdout.decode().splitlines()
    want = {}
    for line in lines:
        n, name = line.split("\t", 1)
        want[fold(name)] = int(n) > 0
    res = scan_card(str(tree))
    index = store.ArtIndex(str(tree))
    got = {fold(g.name): index.find(g)[0] == store.HAVE_ART for g in res.games}
    assert got == want
    assert sum(want.values()) == 9
