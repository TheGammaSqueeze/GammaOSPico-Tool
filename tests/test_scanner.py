import os


import cardgen
from pico_tool.card import archives, scanner
from pico_tool.card.scanner import ARC_7Z, ARC_NONE, ARC_ZIP, scan_card


def names(res, t=None):
    return [g.name for g in res.games if t is None or g.type == t]


def test_layouts_and_order(tmp_path):
    cardgen.rom(str(tmp_path / "roms" / "gba" / "b game.gba"))
    cardgen.rom(str(tmp_path / "roms" / "gba" / "A Game.GBA"))
    cardgen.rom(str(tmp_path / "GameBoyAdvance" / "C game.gba"))
    cardgen.rom(str(tmp_path / "ES" / "ROMs" / "SNES" / "Super.sfc"))
    cardgen.rom(str(tmp_path / "Emulation" / "roms" / "megadrive" / "Mega.md"))
    cardgen.rom(str(tmp_path / "Emulation" / "roms" / "sg-1000" / "Sg.sg"))
    cardgen.rom(str(tmp_path / "Other" / "gb" / "Pocket.gb"))
    res = scan_card(str(tmp_path))
    assert names(res) == ["A Game", "b game", "C game", "Mega", "Pocket", "Sg", "Super"]
    t = {g.name: g.system.key for g in res.games}
    assert t == {"A Game": "gba", "b game": "gba", "C game": "gba", "Mega": "genesis", "Pocket": "gb",
                 "Sg": "sg", "Super": "snes"}
    cd = {g.name: g.cdir for g in res.games}
    assert cd["Super"] == "/ES/roms/SNES" and cd["Mega"] == "/Emulation/roms/megadrive"


def test_skipped_files_and_folders(tmp_path):
    g = tmp_path / "roms" / "gba"
    cardgen.rom(str(g / "ok.gba"))
    cardgen.rom(str(g / "empty.gba"), size=0)
    cardgen.rom(str(g / "._ok.gba"))
    cardgen.rom(str(g / ".hidden.gba"))
    cardgen.rom(str(g / "readme.txt"))
    cardgen.rom(str(g / "wrong.gb"))
    cardgen.rom(str(g / "sub" / "deeper.gba"))
    for skip in ("gammaos", "saves", "states", "cores", "bios", "boxart", ".Trash", "System Volume Information"):
        cardgen.rom(str(tmp_path / skip / "gba" / "no.gba"))
    assert names(scan_card(str(tmp_path))) == ["ok"]


def test_first_place_wins_and_unpacked_beats_archived(tmp_path):
    cardgen.rom(str(tmp_path / "roms" / "gba" / "Same.gba"), "first")
    cardgen.rom(str(tmp_path / "gba" / "same.gba"), "second")
    cardgen.make_7z(str(tmp_path / "roms" / "gb" / "Packed.zip"), {"Packed.gb": cardgen.data("p")}, ["-tzip"],
                    str(tmp_path / "w"))
    cardgen.rom(str(tmp_path / "gameboy" / "Packed.gb"), "loose")
    res = scan_card(str(tmp_path))
    by = {g.name: g for g in res.games}
    assert by["Same"].cdir == "/roms/gba"
    assert by["Packed"].arc == ARC_NONE and by["Packed"].cdir == "/gameboy"
    assert by["Packed"].file.endswith("Packed.gb")


def test_archives_single_multi_and_broken(tmp_path):
    w = str(tmp_path / "w")
    g = tmp_path / "roms" / "gba"
    cardgen.make_7z(str(g / "Kirby.zip"), {"inner name.gba": cardgen.data("k")}, ["-tzip"], w)
    cardgen.make_7z(str(g / "Double.gba.7z"), {"x.gba": cardgen.data("d")}, [], w)
    cardgen.make_7z(str(g / "Set.7z"), {"Disc/One (USA).gba": cardgen.data("1"), "Two.gba": cardgen.data("2")}, [], w)
    (g / "Broken.zip").write_bytes(b"junk" * 10)
    res = scan_card(str(tmp_path))
    by = {x.name: x for x in res.games}
    assert sorted(by) == ["Broken", "Double", "Kirby", "One (USA)", "Two"]
    assert by["Kirby"].arc == ARC_ZIP and by["Kirby"].by_file and by["Kirby"].member_text == "inner name.gba"
    assert by["Double"].arc == ARC_7Z
    assert by["One (USA)"].member == 0 and by["Two"].member == 1 and not by["Two"].by_file
    assert by["One (USA)"].title == "One"
    assert by["Broken"].status == archives.ERR_DAMAGED


def test_folders_txt(tmp_path):
    cardgen.rom(str(tmp_path / "My Games" / "Advance" / "Mine.gba"))
    cardgen.rom(str(tmp_path / "My Games" / "Advance" / "deep" / "No.gba"))
    cardgen.rom(str(tmp_path / "Col" / "S" / "Snes.sfc"))
    cardgen.rom(str(tmp_path / "Tab" / "Tabbed.gb"))
    os.makedirs(str(tmp_path / "gammaos"))
    (tmp_path / "gammaos" / "folders.txt").write_bytes(
        b"# comment\r\n"
        b"gba /My Games/Advance///\r\n"
        b"   superfamicom /Col/S   \n"
        b"gameboy\t/Tab\n"
        b"gba relative/path\n"
        b"nope /Col/S\n"
        b"gba /Missing\n"
        b"gbaonly\n")
    res = scan_card(str(tmp_path))
    # /My Games/Advance and /Col/S are also found as <folder>/<system>? No: "Advance" and "S" are no system names
    assert names(res) == ["Mine", "Snes", "Tabbed"]
    assert res.user_folders == [("gba", "/My Games/Advance"), ("snes", "/Col/S"), ("gb", "/Tab")]


def test_folders_txt_nul_byte_stops_reading(tmp_path):
    cardgen.rom(str(tmp_path / "A" / "x" / "One.gba"))
    cardgen.rom(str(tmp_path / "B" / "y" / "Two.gba"))
    os.makedirs(str(tmp_path / "gammaos"))
    (tmp_path / "gammaos" / "folders.txt").write_bytes(b"gba /A/x\n\x00gba /B/y\n")
    assert names(scan_card(str(tmp_path))) == ["One"]


def test_folder_limit_and_game_cap(tmp_path):
    for i in range(70):
        cardgen.rom(str(tmp_path / ("F%02d" % i) / "gba" / ("G%02d.gba" % i)), size=64)
    res = scan_card(str(tmp_path))
    assert len(res.folders) == 64 and res.skipped_folders == 6 and len(res.games) == 64
    capped = scan_card(str(tmp_path), max_games=10)
    assert len(capped.games) == 10 and capped.total == 64
    assert names(capped) == sorted(names(capped), key=str.lower)


def test_long_and_non_ascii_names(tmp_path):
    long = "L" * 140
    cardgen.rom(str(tmp_path / "roms" / "gba" / (long + ".gba")), "L")
    cardgen.rom(str(tmp_path / "roms" / "gba" / "Pokémon (France).gba"), "P")
    cardgen.rom(str(tmp_path / "roms" / "gba" / "Pokèmon (France).gba"), "Q")   # the same name for the console
    res = scan_card(str(tmp_path))
    ns = names(res)
    assert "L" * 127 in ns
    assert ns.count("Pok_mon (France)") == 1


def test_sort_is_case_insensitive_and_stable(tmp_path):
    for n in ("beta", "Alpha", "alpha2", "BETA2", "_under", "Zed", "1up"):
        cardgen.rom(str(tmp_path / "roms" / "gba" / (n + ".gba")), n)
    # the console folds A-Z only: "_" (0x5F) comes before the letters
    assert names(scan_card(str(tmp_path))) == ["1up", "_under", "Alpha", "alpha2", "beta", "BETA2", "Zed"]


def test_folders_template(tmp_path):
    p = scanner.write_folders_template(str(tmp_path))
    assert open(p).read().startswith("# GammaOS Pico: more folders to look for games in.")
    assert scan_card(str(tmp_path)).folders_txt
