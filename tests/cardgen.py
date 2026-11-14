"""Synthetic cards for the tests: ROM files, archives of every kind (made with /usr/bin/7z and zip)
and the folder layouts the console reads. Every ROM starts with its own name, so a listing can be
checked against the files it points at."""

import os
import shutil
import subprocess
import zipfile

SEVENZ = shutil.which("7z")
ZIPCLI = shutil.which("zip")


def rom(path, name=None, size=4096):
    """A ROM file whose first bytes are its name (the rest a pattern), `size` bytes."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    head = (name or os.path.splitext(os.path.basename(path))[0]).encode()
    body = head + bytes((i * 7 + len(head)) & 0xFF for i in range(max(0, size - len(head))))
    with open(path, "wb") as f:
        f.write(body[:size] if size else b"")
    return path


def seven(args, cwd):
    subprocess.run([SEVENZ] + args, cwd=cwd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def make_7z(dest, files, opts, workdir):
    """dest from files {name inside: bytes} with 7z options (["-m0=LZMA2"], ...)."""
    src = os.path.join(workdir, "src_" + os.path.basename(dest))
    shutil.rmtree(src, ignore_errors=True)
    os.makedirs(src)
    for n, data in files.items():
        p = os.path.join(src, n)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
    if os.path.exists(dest):
        os.remove(dest)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    seven(["a", "-bd", "-y"] + opts + [os.path.abspath(dest)] + sorted(os.listdir(src)), src)
    shutil.rmtree(src)
    return dest


def data(name, size=3000):
    head = name.encode()
    return (head + bytes((i * 13 + 5) & 0xFF for i in range(size)))[:max(size, len(head))]


def build_archive_card(root, work):
    """A card with every archive case the console handles (and some it refuses)."""
    os.makedirs(work, exist_ok=True)
    g = os.path.join(root, "roms", "gba")
    rom(os.path.join(g, "Plain Game (USA).gba"))
    rom(os.path.join(g, "UPPER.GBA"), "UPPER")
    rom(os.path.join(g, "empty.gba"), size=0)
    rom(os.path.join(g, "._Plain Game (USA).gba"), "._Plain")
    rom(os.path.join(g, "notes.txt"), "notes")
    # a single ROM in a zip, deflated, named after the archive; "Game.gba.zip" drops both
    make_7z(os.path.join(g, "Single Zip.zip"), {"inside name.gba": data("Single Zip")}, ["-tzip"], work)
    make_7z(os.path.join(g, "Double Ext.gba.zip"), {"x.gba": data("Double Ext")}, ["-tzip", "-mx0"], work)
    # several ROMs, folders inside, other files around them
    make_7z(os.path.join(g, "Multi Zip.zip"), {
        "Collection/Alpha (USA).gba": data("Alpha (USA)"),
        "Collection/Beta.GBA": data("Beta"),
        "readme.txt": b"hello",
        "Gamma.gba": data("Gamma")}, ["-tzip"], work)
    # the same game unpacked and in an archive: the unpacked one wins
    rom(os.path.join(g, "Alpha (USA).gba"), "Alpha (USA)")
    # 7z: LZMA2 solid, LZMA, copy, non-solid, plain header, encrypted data, encrypted header
    make_7z(os.path.join(g, "Solid Set.7z"), {"One.gba": data("One"), "Two.gba": data("Two"),
                                             "sub/Three.gba": data("Three")}, ["-m0=LZMA2", "-ms=on"], work)
    make_7z(os.path.join(g, "Lzma One.7z"), {"lz.gba": data("Lzma One")}, ["-m0=LZMA"], work)
    make_7z(os.path.join(g, "Copy Set.7z"), {"C1.gba": data("C1"), "C2.gba": data("C2")}, ["-m0=Copy"], work)
    make_7z(os.path.join(g, "Loose Set.7z"), {"L1.gba": data("L1"), "L2.gba": data("L2")},
            ["-m0=LZMA2", "-ms=off", "-mhc=off"], work)
    make_7z(os.path.join(g, "Locked Set.7z"), {"K1.gba": data("K1"), "K2.gba": data("K2")},
            ["-m0=LZMA2", "-pSECRET"], work)
    make_7z(os.path.join(g, "Hidden Names.7z"), {"H1.gba": data("H1"), "H2.gba": data("H2")},
            ["-m0=LZMA2", "-pSECRET", "-mhe=on"], work)
    make_7z(os.path.join(g, "Ppmd Set.7z"), {"P1.gba": data("P1"), "P2.gba": data("P2")}, ["-m0=PPMd"], work)
    make_7z(os.path.join(g, "Bcj One.7z"), {"b.gba": data("Bcj One")}, ["-m0=BCJ", "-m1=LZMA2"], work)
    make_7z(os.path.join(g, "No Rom.7z"), {"readme.txt": b"nothing"}, [], work)
    make_7z(os.path.join(g, "Zip Locked.zip"), {"Z1.gba": data("Z1"), "Z2.gba": data("Z2")},
            ["-tzip", "-pSECRET"], work)
    make_7z(os.path.join(g, "Zip Bzip.zip"), {"Bz1.gba": data("Bz1"), "Bz2.gba": data("Bz2")},
            ["-tzip", "-mm=BZip2"], work)
    with open(os.path.join(g, "Broken.zip"), "wb") as f:
        f.write(b"PK\x03\x04 this is not a zip at all" * 4)
    with open(os.path.join(g, "Broken.7z"), "wb") as f:
        f.write(b"7z\xbc\xaf\x27\x1c\x00\x04" + b"\x00" * 40)
    # a split 7z (Game.7z.001 is no .7z for the console) and a zip in parts
    make_7z(os.path.join(work, "split", "Split.7z"), {"S.gba": data("Split", 9000)}, ["-v2k", "-mx0"], work)
    for n in sorted(os.listdir(os.path.join(work, "split"))):
        shutil.copy(os.path.join(work, "split", n), os.path.join(g, n))
    if ZIPCLI:
        zsrc = os.path.join(work, "zsplit")
        os.makedirs(zsrc, exist_ok=True)
        with open(os.path.join(zsrc, "Parts.gba"), "wb") as f:
            f.write(os.urandom(200000))
        subprocess.run([ZIPCLI, "-q", "-0", "-s", "64k", "zparts.zip", "Parts.gba"], cwd=zsrc, check=True)
        shutil.copy(os.path.join(zsrc, "zparts.zip"), os.path.join(g, "Zip Parts.zip"))
    # ZIP64 end records (Python's writer, made to use them)
    old = zipfile.ZIP_FILECOUNT_LIMIT
    zipfile.ZIP_FILECOUNT_LIMIT = 1
    try:
        with zipfile.ZipFile(os.path.join(g, "Zip64 Set.zip"), "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("Z64a.gba", data("Z64a"))
            z.writestr("Z64b.gba", data("Z64b"))
    finally:
        zipfile.ZIP_FILECOUNT_LIMIT = old

    # other layouts and systems
    rom(os.path.join(root, "gba", "Plain Game (USA).gba"), "dupe in root")
    rom(os.path.join(root, "gba", "Root Only.gba"))
    rom(os.path.join(root, "ES-DE", "ROMs", "snes", "Super Thing (Europe).sfc"))
    rom(os.path.join(root, "ES-DE", "ROMs", "snes", "Other Thing.SMC"))
    make_7z(os.path.join(root, "ES-DE", "ROMs", "snes", "Snes Pair.7z"),
            {"S1.sfc": data("S1"), "S2.smc": data("S2"), "x.gba": data("x")}, [], work)
    rom(os.path.join(root, "Emulation", "roms", "megadrive", "Sonic Like.md"))
    rom(os.path.join(root, "Emulation", "roms", "megadrive", "Bin Game.bin"))
    rom(os.path.join(root, "Emulation", "roms", "genesis", "Gen Game.gen"))
    rom(os.path.join(root, "gameboy", "Pocket.gb"))
    rom(os.path.join(root, "roms", "gbc", "Color.gbc"))
    rom(os.path.join(root, "roms", "gbc", "Wrong.gb"), "Wrong")
    rom(os.path.join(root, "roms", "sms", "Master.sms"))
    rom(os.path.join(root, "roms", "gg", "Gear.gg"))
    rom(os.path.join(root, "roms", "sg-1000", "Sega One.sg"))
    rom(os.path.join(root, "saves", "gba", "NotAGame.gba"))
    rom(os.path.join(root, "My Games", "Advance", "Mine.gba"))
    rom(os.path.join(root, "My Games", "Advance", "deeper", "Ignored.gba"))
    rom(os.path.join(root, "Collections", "SNES Stuff", "Collected.sfc"))
    os.makedirs(os.path.join(root, "gammaos"), exist_ok=True)
    with open(os.path.join(root, "gammaos", "folders.txt"), "w") as f:
        f.write("# my folders\n"
                "gba /My Games/Advance/\n"
                "  superfamicom\t/Collections/SNES Stuff  \r\n"
                "nonsense /My Games\n"
                "gb relative/path\n"
                "gba /Not There\n"
                "gbaonly\n")
    return root


def raw_zip(dest, entries):
    """A stored zip whose entry names are exactly the bytes given: entries [(name bytes, flags,
    data)], so names in code page 437, marked UTF-8 or not, can be made as old tools wrote them."""
    import struct
    import zlib
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    local = b""
    central = b""
    for name, flags, body in entries:
        crc = zlib.crc32(body) & 0xFFFFFFFF
        off = len(local)
        local += struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, flags, 0, 0, 0, crc, len(body), len(body),
                             len(name), 0) + name + body
        central += struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, flags, 0, 0, 0, crc, len(body),
                               len(body), len(name), 0, 0, 0, 0, 0, off) + name
    end = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(entries), len(entries), len(central), len(local), 0)
    with open(dest, "wb") as f:
        f.write(local + central + end)
    return dest


def build_unicode_card(root, work):
    """A card of names in many scripts: accents, Greek, Cyrillic, CJK names that share their old
    underscore form, Hangul, Arabic, decomposed names, emoji, archives of each naming rule and
    folders outside ASCII."""
    import unicodedata
    os.makedirs(work, exist_ok=True)
    g = os.path.join(root, "roms", "gba")
    nfd = unicodedata.normalize("NFD", "Émile et les Étoiles")
    for n in ("Pokémon Émeraude (France)", "Pokemon Emerald (USA)", "Ελληνικά", "Привет мир", "ポケモン",
              "テトリス", "東京", "한국 게임", unicodedata.normalize("NFD", "한국어"), "عربي", nfd,
              "Émile et les Étoiles", "Smile \U0001F600", "Ångström", "zebra", "Ｆullwidth", "Ⅻ Roman",
              "Pok_mon Émeraude (France)", "Caf_", "Café", "★ Star", "ñandú"):
        rom(os.path.join(g, n + ".gba"), "x")
    # names past 127 bytes: cut at a whole character
    rom(os.path.join(g, "é" * 70 + ".gba"), "long")
    rom(os.path.join(g, "ポ" * 60 + ".gba"), "long2")
    # the same game twice, any case of A-Z: listed once, from the first folder
    rom(os.path.join(root, "gba", "POKéMON ÉMERAUDE (FRANCE).gba"), "dupe")
    rom(os.path.join(root, "gba", "Ελληνικά.gba"), "dupe")
    # but a composed and a decomposed name are two names for the console
    rom(os.path.join(root, "gba", unicodedata.normalize("NFD", "Ångström") + ".gba"), "nfd")
    # archives: zip names marked UTF-8, plain UTF-8, code page 437, bytes that are not UTF-8; 7z
    with zipfile.ZipFile(os.path.join(g, "Utf8 Set.zip"), "w") as z:
        z.writestr("Zip Café.gba", data("Zip Café"))
        z.writestr("Zip ポケモン.gba", data("Zip ポケモン"))
        z.writestr("Zip \U0001F600.gba", data("Zip emoji"))
    raw_zip(os.path.join(g, "Old Set.zip"), [
        (b"Old Caf\x82.gba", 0, data("Old Cafe")),
        ("Old Ñandú.gba".encode("utf-8"), 0, data("Old Nandu")),
        (b"Old \x9a\x81.gba", 0, data("Old U")),
        (b"Old Bad\xff.gba", 0x800, data("Old Bad")),
        (b"Folder\x82/Inside \xe4.gba", 0, data("Inside")),
    ])
    raw_zip(os.path.join(g, "Lonely \x82.zip".replace("\x82", "é")), [(b"x.gba", 0, data("Lonely"))])
    make_7z(os.path.join(g, "Seven Set.7z"), {"Seven Ελλάδα.gba": data("Seven 1"), "Seven 東京.gba": data("Seven 2"),
                                             "sub/Seven \U0001F600.gba": data("Seven 3"),
                                             unicodedata.normalize("NFD", "Seven Été.gba"): data("Seven 4")}, [], work)
    # folders outside ASCII: a front end's folder at the root, and one from folders.txt
    rom(os.path.join(root, "Spiele é", "roms", "snes", "Über Spiel.sfc"), "snes")
    rom(os.path.join(root, "ゲーム", "gb", "ポケット.gb"), "gb")
    rom(os.path.join(root, "Mes Jeux", "Avancé", "Mon Jeu.gba"), "mine")
    os.makedirs(os.path.join(root, "gammaos"), exist_ok=True)
    with open(os.path.join(root, "gammaos", "folders.txt"), "wb") as f:
        f.write("gba /Mes Jeux/Avancé\n".encode("utf-8"))
    return root
