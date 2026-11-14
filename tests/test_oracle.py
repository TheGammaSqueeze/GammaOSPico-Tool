"""The strongest check of the card scanner: the console's own scanner, built from the bootloader
sources on this computer, lists FAT32 and exFAT images of synthetic cards, and the tool's listing
of the same folders must be the same game for game, names in any script included. Its name
helpers (pico_fold.c, the old underscore form, the UTF-16 reader) are checked against the tool's
the same way. Skipped when the sources or the tools to build and format images are missing (set
GAMMAOS_PICO_LK to the lk-mt6785 checkout)."""

import os
import shutil
import subprocess

import pytest

import cardgen
from pico_tool.card import names
from pico_tool.card.names import ascii_fold, from_bytes
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
needs_src = pytest.mark.skipif(
    not (os.path.isfile(os.path.join(SRC, "pico_fold.c")) and shutil.which("gcc")),
    reason="the console sources or gcc are not available")
# mtools makes the long folder names from the locale's character set
UTF8_ENV = dict(os.environ, LC_ALL="C.UTF-8")


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
                                           "gba_sd_save.c", "gba_boxart.c", "pico_fold.c")]
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
                    subprocess.run(["mmd", "-i", path, "::" + card + "/" + de.name], check=True, env=UTF8_ENV)
                    walk(de.path, card + "/" + de.name)
        walk(tree, "")
    else:
        subprocess.run([MKFS_EXFAT, path], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run([oracle, "put", path, tree], check=True)
    return path


def oracle_list(oracle, image, max_games=None):
    args = [oracle, "list", image] + ([str(max_games)] if max_games else [])
    lines = from_bytes(subprocess.run(args, check=True, stdout=subprocess.PIPE).stdout).splitlines()
    head = lines[0].split()
    assert head[0] == "TOTAL"
    games = []
    for line in lines[1:]:
        t, d, arc, mem, size, name, uname = line.split("\t")
        assert uname == name
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
        assert (a[0], ascii_fold(a[1]), a[2], a[3], ascii_fold(a[5])) == (b[0], ascii_fold(b[1]), b[2], b[3],
                                                                         ascii_fold(b[5]))
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
    want = oracle_art(oracle, image)
    res = scan_card(str(tree))
    index = store.ArtIndex(str(tree), legacy=res.legacy)
    got = {ascii_fold(g.name): index.find(g)[0] == store.HAVE_ART for g in res.games}
    assert got == want
    assert sum(want.values()) == 9


def oracle_art(oracle, image):
    lines = from_bytes(subprocess.run([oracle, "art", image], check=True, stdout=subprocess.PIPE).stdout).splitlines()
    want = {}
    for line in lines:
        n, name = line.split("\t", 1)
        want[ascii_fold(name)] = int(n) > 0
    return want


@needs
@pytest.mark.parametrize("fs", ["exfat", "fat32"])
def test_unicode_card_matches_console(oracle, tmp_path, fs):
    tree = str(tmp_path / "tree")
    cardgen.build_unicode_card(tree, str(tmp_path / "work"))
    image = make_image(oracle, tree, str(tmp_path / "card.img"), fs)
    res = compare(oracle, tree, image)
    got = [g.name for g in res.games]
    # every name exactly as the card has it (one that fits a short 8.3 name reads back in capitals)
    assert [ascii_fold(n) for n in got] == [ascii_fold(w[5]) for w in oracle_list(oracle, image)[2]]
    assert "ポケモン" in got and "テトリス" in got and "Smile \U0001F600" in got
    assert len(res.games) > 35


@needs
def test_unicode_box_art_matches_console(oracle, tmp_path):
    import unicodedata
    tree = tmp_path / "tree"
    cardgen.build_unicode_card(str(tree), str(tmp_path / "work"))
    art = b"GART\x01\x00\x00\x00\x02\x00\x01\x00" + b"\x1f\x00\xe0\x07"
    box = tree / "roms" / "gba" / "boxart"
    box.mkdir(parents=True)
    for n in ("Ελληνικά",                                       # its own UTF-8 name
              "______ ___",                                     # the old form, this game's alone
              "____",                                           # the old form of four Japanese names
              "Caf_",                                           # a game's own name, not Café's
              unicodedata.normalize("NFD", "Émile et les Étoiles"),   # the decomposed game only
              "Pok_mon _meraude (France)",                      # two games' old form
              "Seven 東京", "Zip \U0001F600", "Old Café", "Smile __"):
        (box / (n + ".ART")).write_bytes(art)
    (tree / "roms" / "snes" / "boxart").mkdir(parents=True)
    (tree / "roms" / "snes" / "boxart" / "_ber Spiel.ART").write_bytes(art)   # at home, old form
    image = make_image(oracle, str(tree), str(tmp_path / "card.img"), "exfat")
    want = oracle_art(oracle, image)
    res = scan_card(str(tree))
    index = store.ArtIndex(str(tree), legacy=res.legacy)
    got = {ascii_fold(g.name): index.find(g)[0] == store.HAVE_ART for g in res.games}
    assert got == want
    have = sorted(k for k, v in want.items() if v)
    assert ascii_fold("Привет мир") in have and ascii_fold("Ελληνικά") in have
    assert ascii_fold("Pokémon Émeraude (France)") not in have
    assert ascii_fold(unicodedata.normalize("NFD", "Émile et les Étoiles")) in have
    assert ascii_fold("Émile et les Étoiles") not in have
    assert ascii_fold("ポケモン") not in have and ascii_fold("café") not in have and "caf_" in have
    assert ascii_fold("Über Spiel") in have and ascii_fold("Smile \U0001F600") in have


@needs_src
def test_fold_table_matches_console(oracle):
    out = subprocess.run([oracle, "foldcp"], check=True, stdout=subprocess.PIPE).stdout.decode().split()
    want = {int(a, 16): int(b, 16) for a, b in zip(out[::2], out[1::2])}
    assert len(want) > 700
    assert [c for c in range(0x30000) if names.fold_cp(c) != want.get(c, c)] == []


@needs_src
def test_fold_and_old_form_match_console(oracle):
    import unicodedata
    samples = ["Pokémon", unicodedata.normalize("NFD", "Pokémon"), "ポケモン", "Smile \U0001F600", "Plain",
               "Ελληνικά", "Привет", "한국", unicodedata.normalize("NFD", "한국"), "عربي", "ＭＡＲＩＯ", "Ǆemal",
               "Tiếng Việt", "Œuvre ﬁne", "Ⅻ", "é" * 200, "\U0001F600" * 70, "x" * 130]
    raw = [n.encode("utf-8") for n in samples] + [b"ab\xffc", b"\xc3", b"\xe3\x81", b"\xed\xa0\x80z",
                                                   b"\xf4\x90\x80\x80", b"\xc0\xaf"]
    out = subprocess.run([oracle, "names"], input=b"\n".join(raw) + b"\n", check=True,
                         stdout=subprocess.PIPE).stdout.split(b"\n")
    for line, b in zip(out, raw):
        fa, lk, d = line.split(b"\t")
        s = from_bytes(b)
        assert names.fold_ascii(s).encode("utf-8") == fa, b
        assert names.legacy_key(s) == (lk.decode(), d == b"1"), b


@needs_src
def test_long_names_match_fat_readdir(oracle):
    def units(s):
        r = s.encode("utf-16-le", "surrogatepass")
        return [r[i] | (r[i + 1] << 8) for i in range(0, len(r), 2)]
    cases = ["Short.gba", "x" * 255, "x" * 250 + ".gba", "ポ" * 100 + ".gba", "ポ" * 100, "\U0001F600" * 70 + ".gba",
             "é" * 127 + ".gba", "ポ" * 80 + ".a very long extension indeed", "ab\ud800cd.gba", "\udc00x",
             "end\ud83d", "ポ" * 90 + ".\U0001F600\U0001F600"]
    for s in cases:
        u = units(s)
        out = subprocess.run([oracle, "u16"] + ["%x" % x for x in u], check=True,
                             stdout=subprocess.PIPE).stdout.decode().strip()
        assert names.console_name(s).encode("utf-8").hex() == out, s
